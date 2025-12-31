"""Token 轮询器 - Round Robin 策略"""

import asyncio
import time
import logging
from typing import List, Dict, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class TokenStats:
    """Token 统计信息"""
    token: str                        # Token（脱敏显示）
    total_requests: int = 0           # 总请求数
    successful_requests: int = 0      # 成功请求数
    failed_requests: int = 0          # 失败请求数
    consecutive_failures: int = 0     # 连续失败次数
    last_used_at: Optional[float] = None  # 最后使用时间
    last_failure_at: Optional[float] = None  # 最后失败时间
    is_available: bool = True         # 是否可用
    
    @property
    def success_rate(self) -> float:
        """成功率"""
        if self.total_requests == 0:
            return 0.0
        return self.successful_requests / self.total_requests
    
    @property
    def masked_token(self) -> str:
        """脱敏的 Token（只显示前4位和后4位）"""
        if len(self.token) <= 8:
            return "****"
        return f"{self.token[:4]}...{self.token[-4:]}"


class TokenRotator:
    """
    Token 轮询器 - Round Robin 策略
    
    功能：
    - 维护 Token 列表
    - 轮询分发 Token
    - 标记失败的 Token（可选：临时跳过）
    - 统计 Token 使用情况
    """
    
    # 连续失败多少次后临时禁用 Token
    MAX_CONSECUTIVE_FAILURES = 5
    # 禁用 Token 后多久重新启用（秒）
    RECOVERY_INTERVAL = 300  # 5 分钟
    
    def __init__(self, tokens: List[str]):
        """
        初始化 Token 轮询器。
        
        :param tokens: Token 列表
        :raises ValueError: 如果 Token 列表为空
        """
        if not tokens:
            raise ValueError("Token list cannot be empty")
        
        self.tokens = tokens
        self.current_index = 0
        self._lock = asyncio.Lock()
        
        # 为每个 Token 创建统计信息
        self._stats: Dict[str, TokenStats] = {
            token: TokenStats(token=token) for token in tokens
        }
        
        logger.info(f"TokenRotator initialized with {len(tokens)} tokens")
    
    async def get_next_token(self) -> str:
        """
        获取下一个可用的 Token（线程安全）。
        
        使用 Round-Robin 策略，跳过临时不可用的 Token。
        如果所有 Token 都不可用，则重置所有 Token 状态并返回第一个。
        
        :return: 下一个可用的 Token
        """
        async with self._lock:
            # 尝试找到一个可用的 Token
            attempts = 0
            while attempts < len(self.tokens):
                token = self.tokens[self.current_index]
                stats = self._stats[token]
                
                # 移动到下一个索引
                self.current_index = (self.current_index + 1) % len(self.tokens)
                
                # 检查是否可用
                if stats.is_available:
                    stats.last_used_at = time.time()
                    stats.total_requests += 1
                    return token
                
                # 检查是否到了恢复时间
                if stats.last_failure_at:
                    elapsed = time.time() - stats.last_failure_at
                    if elapsed >= self.RECOVERY_INTERVAL:
                        logger.info(
                            f"Token {stats.masked_token} recovered after "
                            f"{elapsed:.0f}s, re-enabling"
                        )
                        stats.is_available = True
                        stats.consecutive_failures = 0
                        stats.last_used_at = time.time()
                        stats.total_requests += 1
                        return token
                
                attempts += 1
            
            # 所有 Token 都不可用，强制重置并使用第一个
            logger.warning("All tokens unavailable, forcing reset")
            self._reset_all_tokens()
            token = self.tokens[0]
            self.current_index = 1 % len(self.tokens)
            stats = self._stats[token]
            stats.last_used_at = time.time()
            stats.total_requests += 1
            return token
    
    def _reset_all_tokens(self):
        """重置所有 Token 状态"""
        for stats in self._stats.values():
            stats.is_available = True
            stats.consecutive_failures = 0
    
    def report_success(self, token: str):
        """
        报告 Token 使用成功。
        
        :param token: 成功使用的 Token
        """
        if token not in self._stats:
            return
        
        stats = self._stats[token]
        stats.successful_requests += 1
        stats.consecutive_failures = 0
        stats.is_available = True
        
        logger.debug(f"Token {stats.masked_token} success, total: {stats.successful_requests}")
    
    def report_failure(self, token: str, error: Optional[str] = None):
        """
        报告 Token 使用失败。
        
        :param token: 失败的 Token
        :param error: 错误信息（可选）
        """
        if token not in self._stats:
            return
        
        stats = self._stats[token]
        stats.failed_requests += 1
        stats.consecutive_failures += 1
        stats.last_failure_at = time.time()
        
        # 连续失败过多，临时禁用
        if stats.consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
            stats.is_available = False
            logger.warning(
                f"Token {stats.masked_token} disabled after "
                f"{stats.consecutive_failures} consecutive failures"
                + (f": {error}" if error else "")
            )
        else:
            logger.debug(
                f"Token {stats.masked_token} failure ({stats.consecutive_failures}/"
                f"{self.MAX_CONSECUTIVE_FAILURES})"
                + (f": {error}" if error else "")
            )
    
    def get_stats(self) -> Dict:
        """
        获取统计信息。
        
        :return: 统计信息字典
        """
        total_requests = sum(s.total_requests for s in self._stats.values())
        successful_requests = sum(s.successful_requests for s in self._stats.values())
        failed_requests = sum(s.failed_requests for s in self._stats.values())
        available_tokens = sum(1 for s in self._stats.values() if s.is_available)
        
        return {
            "total_tokens": len(self.tokens),
            "available_tokens": available_tokens,
            "total_requests": total_requests,
            "successful_requests": successful_requests,
            "failed_requests": failed_requests,
            "success_rate": successful_requests / total_requests if total_requests > 0 else 0,
            "tokens": [
                {
                    "token": stats.masked_token,
                    "is_available": stats.is_available,
                    "total_requests": stats.total_requests,
                    "successful_requests": stats.successful_requests,
                    "failed_requests": stats.failed_requests,
                    "consecutive_failures": stats.consecutive_failures,
                    "success_rate": stats.success_rate,
                }
                for stats in self._stats.values()
            ],
        }