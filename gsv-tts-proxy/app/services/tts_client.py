"""GSV TTS 客户端 - 使用固定请求格式"""

import asyncio
import time
import logging
from typing import Optional, Dict
import httpx

from .token_rotator import TokenRotator

logger = logging.getLogger(__name__)


class GSVTTSClient:
    """
    GSV TTS 客户端
    
    功能：
    - 使用固定的请求格式
    - 只动态修改 input 字段
    - 集成 Token 轮询器
    - 支持重试机制
    """
    
    def __init__(
        self,
        api_url: str,
        token_rotator: TokenRotator,
        default_voice: str,
        default_model: str,
        timeout: int = 60,
        retry_count: int = 2,
    ):
        """
        初始化 GSV TTS 客户端。
        
        :param api_url: GSV TTS API 基础 URL
        :param token_rotator: Token 轮询器
        :param default_voice: 默认语音角色
        :param default_model: 默认 TTS 模型
        :param timeout: 请求超时时间（秒）
        :param retry_count: 失败重试次数
        """
        self.api_url = api_url.rstrip('/')
        self.token_rotator = token_rotator
        self.default_voice = default_voice
        self.default_model = default_model
        self.timeout = timeout
        self.retry_count = retry_count
        
        # HTTP 客户端
        self.client: Optional[httpx.AsyncClient] = None
        
        # 统计信息
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.total_response_time = 0.0
    
    async def initialize(self):
        """初始化 HTTP 客户端"""
        if self.client is None:
            self.client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                follow_redirects=True,
            )
    
    async def close(self):
        """关闭 HTTP 客户端"""
        if self.client:
            await self.client.aclose()
            self.client = None
    
    def _build_request_body(self, text: str) -> dict:
        """
        构造请求体 - 固定格式，只动态修改 input。
        
        :param text: 要合成的文本
        :return: 请求体字典
        """
        return {
            "model": self.default_model,
            "input": text,
            "voice": self.default_voice,
            "response_format": "wav",
            "speed": 1,
            "instructions": "默认",
            "other_params": {
                "text_lang": "中英混合",
                "prompt_lang": "中文",
                "emotion": "默认",
                "top_k": 10,
                "top_p": 1,
                "temperature": 1,
                "text_split_method": "按标点符号切",
                "batch_size": 1,
                "batch_threshold": 0.75,
                "split_bucket": True,
                "fragment_interval": 0.3,
                "parallel_infer": True,
                "repetition_penalty": 1.35,
                "sample_steps": 16,
                "if_sr": False,
                "seed": -1
            }
        }
    
    async def synthesize(self, text: str) -> bytes:
        """
        合成语音。
        
        1. 从轮询器获取 Token
        2. 构造固定格式的请求
        3. 发送请求并处理响应
        4. 重试失败的请求（使用不同 Token）
        
        :param text: 要合成的文本
        :return: WAV 音频数据
        :raises Exception: 如果所有重试都失败
        """
        await self.initialize()
        
        self.total_requests += 1
        last_error = None
        
        for attempt in range(self.retry_count + 1):
            # 获取下一个 Token
            token = await self.token_rotator.get_next_token()
            
            try:
                result = await self._do_request(text, token)
                self.successful_requests += 1
                self.token_rotator.report_success(token)
                return result
            except Exception as e:
                last_error = e
                self.token_rotator.report_failure(token, str(e))
                
                logger.warning(
                    f"TTS request failed (attempt {attempt + 1}/{self.retry_count + 1}): "
                    f"error={e}"
                )
                
                if attempt < self.retry_count:
                    # 指数退避
                    await asyncio.sleep(0.5 * (2 ** attempt))
        
        self.failed_requests += 1
        raise Exception(f"All TTS request attempts failed: {last_error}")
    
    async def _do_request(self, text: str, token: str) -> bytes:
        """
        执行单个 TTS 请求。
        
        :param text: 要合成的文本
        :param token: API Token
        :return: WAV 音频数据
        """
        start_time = time.time()
        
        try:
            # 构造请求
            url = f"{self.api_url}/v1/audio/speech"
            body = self._build_request_body(text)
            headers = {
                "accept": "application/json",
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
            
            logger.debug(f"TTS request: url={url}, text_len={len(text)}")
            
            response = await self.client.post(
                url,
                json=body,
                headers=headers,
            )
            
            response.raise_for_status()
            
            # 记录响应时间
            response_time = time.time() - start_time
            self.total_response_time += response_time
            
            logger.debug(
                f"TTS request successful: text_len={len(text)}, "
                f"response_time={response_time:.2f}s, "
                f"audio_size={len(response.content)}"
            )
            
            return response.content
            
        except httpx.HTTPStatusError as e:
            raise Exception(f"HTTP error {e.response.status_code}: {e.response.text[:200]}")
        except httpx.TimeoutException:
            raise Exception("Request timeout")
        except Exception as e:
            raise Exception(f"Request failed: {e}")
    
    async def health_check(self) -> bool:
        """
        检查 TTS 服务是否可用。
        
        尝试使用一个简单的文本进行合成测试。
        
        :return: 是否健康
        """
        try:
            await self.synthesize("测试")
            return True
        except Exception as e:
            logger.warning(f"Health check failed: {e}")
            return False
    
    def get_stats(self) -> Dict:
        """获取统计信息"""
        avg_response_time = (
            self.total_response_time / self.successful_requests
            if self.successful_requests > 0 else 0
        )
        
        return {
            "api_url": self.api_url,
            "default_voice": self.default_voice,
            "default_model": self.default_model,
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "success_rate": (
                self.successful_requests / self.total_requests
                if self.total_requests > 0 else 0
            ),
            "avg_response_time": avg_response_time,
            "token_stats": self.token_rotator.get_stats(),
        }