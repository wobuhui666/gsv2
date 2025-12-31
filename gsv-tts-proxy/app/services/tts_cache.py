"""TTS 缓存管理器 - 支持预生成和异步等待"""

import asyncio
import hashlib
import time
import logging
from typing import Optional, Dict, List
from dataclasses import dataclass, field
from enum import Enum

from .tts_client import GSVTTSClient
from .audio_utils import concatenate_wav

logger = logging.getLogger(__name__)


class CacheStatus(Enum):
    """缓存条目状态"""
    PENDING = "pending"        # 待生成
    GENERATING = "generating"  # 生成中
    COMPLETED = "completed"    # 已完成
    FAILED = "failed"          # 生成失败


@dataclass
class TTSCacheEntry:
    """TTS 缓存条目"""
    text: str                           # 原文本
    model: str                          # TTS 模型
    audio: Optional[bytes] = None       # 音频数据
    status: CacheStatus = CacheStatus.PENDING
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    error: Optional[str] = None
    # 用于等待生成完成的事件
    _event: asyncio.Event = field(default_factory=asyncio.Event)
    
    @property
    def generation_time(self) -> Optional[float]:
        """生成耗时"""
        if self.completed_at and self.created_at:
            return self.completed_at - self.created_at
        return None


@dataclass
class SegmentMapping:
    """分段映射信息"""
    full_text: str                    # 完整文本（用于日志和调试）
    segment_keys: List[str]           # 分段的缓存 key 列表
    created_at: float = field(default_factory=time.time)


class TTSCacheManager:
    """
    TTS 缓存管理器
    
    支持：
    - 预提交文本到生成队列
    - 异步等待正在生成的 TTS
    - LRU + TTL 缓存淘汰
    - 缓存统计
    - 完整文本 → 分段映射，支持音频拼接
    """
    
    def __init__(
        self,
        tts_client: GSVTTSClient,
        max_size: int = 1000,
        ttl: int = 3600,
        cleanup_interval: int = 300,
    ):
        """
        初始化缓存管理器。
        
        :param tts_client: GSV TTS 客户端
        :param max_size: 最大缓存条目数
        :param ttl: 缓存过期时间（秒）
        :param cleanup_interval: 缓存清理间隔（秒）
        """
        self.tts_client = tts_client
        self.max_size = max_size
        self.ttl = ttl
        self.cleanup_interval = cleanup_interval
        
        # 缓存存储
        self._cache: Dict[str, TTSCacheEntry] = {}
        self._lock = asyncio.Lock()
        
        # 分段映射：完整文本的 cache_key → SegmentMapping
        self._segment_map: Dict[str, SegmentMapping] = {}
        self._segment_map_lock = asyncio.Lock()
        
        # 统计信息
        self.hit_count = 0
        self.miss_count = 0
        self.concat_hit_count = 0  # 拼接命中次数
        
        # 清理任务
        self._cleanup_task: Optional[asyncio.Task] = None
    
    def _generate_cache_key(self, text: str, model: str) -> str:
        """
        生成缓存 key。
        
        :param text: 文本内容
        :param model: TTS 模型名称
        :return: SHA256 哈希值
        """
        content = f"{model}:{text}"
        return hashlib.sha256(content.encode()).hexdigest()
    
    async def start(self):
        """启动缓存管理器（包括定期清理任务）"""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.info("TTS cache manager started")
    
    async def stop(self):
        """停止缓存管理器"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
        logger.info("TTS cache manager stopped")
    
    async def _cleanup_loop(self):
        """定期清理过期缓存"""
        while True:
            try:
                await asyncio.sleep(self.cleanup_interval)
                await self._cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cache cleanup error: {e}")
    
    async def _cleanup_expired(self):
        """清理过期的缓存条目"""
        async with self._lock:
            now = time.time()
            expired_keys = [
                key for key, entry in self._cache.items()
                if now - entry.created_at > self.ttl
            ]
            
            for key in expired_keys:
                del self._cache[key]
            
            if expired_keys:
                logger.info(f"Cleaned up {len(expired_keys)} expired cache entries")
        
        # 清理过期的分段映射
        async with self._segment_map_lock:
            now = time.time()
            expired_mappings = [
                key for key, mapping in self._segment_map.items()
                if now - mapping.created_at > self.ttl
            ]
            
            for key in expired_mappings:
                del self._segment_map[key]
            
            if expired_mappings:
                logger.info(f"Cleaned up {len(expired_mappings)} expired segment mappings")
    
    async def _evict_if_needed(self):
        """如果缓存满了，淘汰最旧的条目"""
        if len(self._cache) >= self.max_size:
            # 按创建时间排序，删除最旧的 10%
            entries = sorted(
                self._cache.items(),
                key=lambda x: x[1].created_at
            )
            to_remove = max(1, len(entries) // 10)
            
            for key, _ in entries[:to_remove]:
                del self._cache[key]
            
            logger.info(f"Evicted {to_remove} cache entries due to size limit")
    
    async def submit(self, text: str, model: str) -> str:
        """
        提交文本到预生成队列。
        
        :param text: 要合成的文本
        :param model: TTS 模型名称
        :return: 缓存 key
        """
        cache_key = self._generate_cache_key(text, model)
        
        async with self._lock:
            # 检查是否已存在
            if cache_key in self._cache:
                logger.debug(f"Cache entry already exists: {cache_key[:16]}...")
                return cache_key
            
            # 淘汰旧条目
            await self._evict_if_needed()
            
            # 创建新条目
            entry = TTSCacheEntry(text=text, model=model)
            self._cache[cache_key] = entry
        
        # 启动异步生成任务
        asyncio.create_task(self._generate(cache_key))
        
        logger.debug(f"Submitted TTS generation: {cache_key[:16]}..., text_len={len(text)}")
        return cache_key
    
    async def submit_with_segments(
        self,
        full_text: str,
        segments: List[str],
        model: str,
    ) -> str:
        """
        提交完整文本及其分段到生成队列，并记录映射关系。
        
        :param full_text: 完整文本
        :param segments: 分段列表
        :param model: TTS 模型名称
        :return: 完整文本的缓存 key
        """
        full_key = self._generate_cache_key(full_text, model)
        
        # 提交每个分段（如果还没提交的话）
        segment_keys = []
        for seg in segments:
            if seg and seg.strip():
                seg_key = await self.submit(seg.strip(), model)
                segment_keys.append(seg_key)
        
        if not segment_keys:
            logger.warning(f"No valid segments for full text: {full_key[:16]}...")
            return full_key
        
        # 记录映射关系
        async with self._segment_map_lock:
            self._segment_map[full_key] = SegmentMapping(
                full_text=full_text[:100] + ("..." if len(full_text) > 100 else ""),
                segment_keys=segment_keys,
            )
        
        logger.info(
            f"Registered segment mapping: {full_key[:16]}... → "
            f"{len(segment_keys)} segments"
        )
        
        return full_key
    
    async def _generate(self, cache_key: str):
        """
        执行 TTS 生成。
        
        :param cache_key: 缓存 key
        """
        async with self._lock:
            entry = self._cache.get(cache_key)
            if entry is None:
                return
            entry.status = CacheStatus.GENERATING
        
        try:
            # 调用 TTS 客户端生成
            audio = await self.tts_client.synthesize(entry.text)
            
            async with self._lock:
                entry = self._cache.get(cache_key)
                if entry:
                    entry.audio = audio
                    entry.status = CacheStatus.COMPLETED
                    entry.completed_at = time.time()
                    entry._event.set()
            
            logger.debug(
                f"TTS generation completed: {cache_key[:16]}..., "
                f"audio_size={len(audio)}, time={entry.generation_time:.2f}s"
            )
            
        except Exception as e:
            async with self._lock:
                entry = self._cache.get(cache_key)
                if entry:
                    entry.status = CacheStatus.FAILED
                    entry.error = str(e)
                    entry._event.set()
            
            logger.error(f"TTS generation failed: {cache_key[:16]}..., error={e}")
    
    async def get(
        self,
        text: str,
        model: str,
        timeout: float = 60,
        generate_if_missing: bool = True,
    ) -> Optional[bytes]:
        """
        获取 TTS 音频。
        
        :param text: 文本内容
        :param model: TTS 模型名称
        :param timeout: 等待超时时间（秒）
        :param generate_if_missing: 如果缓存未命中是否现场生成
        :return: WAV 音频数据，如果失败则返回 None
        """
        cache_key = self._generate_cache_key(text, model)
        
        # 首先检查是否有分段映射
        async with self._segment_map_lock:
            segment_mapping = self._segment_map.get(cache_key)
        
        if segment_mapping:
            # 有分段映射，尝试拼接
            logger.info(
                f"Found segment mapping for {cache_key[:16]}..., "
                f"concatenating {len(segment_mapping.segment_keys)} segments"
            )
            result = await self._get_concatenated(segment_mapping.segment_keys, timeout)
            if result:
                self.concat_hit_count += 1
                return result
            # 拼接失败，继续尝试其他方式
            logger.warning(f"Segment concatenation failed for {cache_key[:16]}...")
        
        # 检查直接缓存
        async with self._lock:
            entry = self._cache.get(cache_key)
        
        if entry is None:
            self.miss_count += 1
            
            if not generate_if_missing:
                return None
            
            # 现场生成
            logger.debug(f"Cache miss, generating on-demand: {cache_key[:16]}...")
            await self.submit(text, model)
            
            async with self._lock:
                entry = self._cache.get(cache_key)
        else:
            self.hit_count += 1
        
        if entry is None:
            return None
        
        # 如果已完成，直接返回
        if entry.status == CacheStatus.COMPLETED:
            return entry.audio
        
        # 如果失败，返回 None
        if entry.status == CacheStatus.FAILED:
            logger.warning(f"Returning None for failed entry: {cache_key[:16]}...")
            return None
        
        # 等待生成完成
        try:
            await asyncio.wait_for(entry._event.wait(), timeout=timeout)
            
            if entry.status == CacheStatus.COMPLETED:
                return entry.audio
            else:
                return None
                
        except asyncio.TimeoutError:
            logger.warning(f"Timeout waiting for TTS generation: {cache_key[:16]}...")
            return None
    
    async def _get_concatenated(
        self,
        segment_keys: List[str],
        timeout: float,
    ) -> Optional[bytes]:
        """
        获取并拼接多个分段的音频。
        
        :param segment_keys: 分段的缓存 key 列表
        :param timeout: 总超时时间
        :return: 拼接后的 WAV 音频数据
        """
        wav_parts = []
        start_time = time.time()
        
        for i, seg_key in enumerate(segment_keys):
            # 计算剩余超时时间
            elapsed = time.time() - start_time
            remaining_timeout = max(1, timeout - elapsed)
            
            audio = await self.get_by_key(seg_key, timeout=remaining_timeout)
            if audio is None:
                logger.warning(
                    f"Failed to get segment {i+1}/{len(segment_keys)}: {seg_key[:16]}..."
                )
                return None
            wav_parts.append(audio)
        
        # 拼接音频
        try:
            result = concatenate_wav(wav_parts)
            logger.info(
                f"Concatenated {len(wav_parts)} segments, "
                f"total size: {len(result)} bytes"
            )
            return result
        except Exception as e:
            logger.error(f"Failed to concatenate audio: {e}")
            return None
    
    async def get_by_key(self, cache_key: str, timeout: float = 60) -> Optional[bytes]:
        """
        通过缓存 key 获取 TTS 音频。
        
        :param cache_key: 缓存 key
        :param timeout: 等待超时时间（秒）
        :return: WAV 音频数据，如果失败则返回 None
        """
        async with self._lock:
            entry = self._cache.get(cache_key)
        
        if entry is None:
            return None
        
        if entry.status == CacheStatus.COMPLETED:
            return entry.audio
        
        if entry.status == CacheStatus.FAILED:
            return None
        
        try:
            await asyncio.wait_for(entry._event.wait(), timeout=timeout)
            return entry.audio if entry.status == CacheStatus.COMPLETED else None
        except asyncio.TimeoutError:
            return None
    
    def get_stats(self) -> Dict:
        """获取缓存统计信息"""
        status_counts = {status: 0 for status in CacheStatus}
        for entry in self._cache.values():
            status_counts[entry.status] += 1
        
        total_requests = self.hit_count + self.miss_count
        hit_rate = self.hit_count / total_requests if total_requests > 0 else 0
        
        return {
            "total_entries": len(self._cache),
            "completed_entries": status_counts[CacheStatus.COMPLETED],
            "pending_entries": status_counts[CacheStatus.PENDING],
            "generating_entries": status_counts[CacheStatus.GENERATING],
            "failed_entries": status_counts[CacheStatus.FAILED],
            "segment_mappings": len(self._segment_map),
            "hit_count": self.hit_count,
            "miss_count": self.miss_count,
            "concat_hit_count": self.concat_hit_count,
            "hit_rate": hit_rate,
        }
    
    async def clear(self):
        """清空缓存"""
        async with self._lock:
            self._cache.clear()
        async with self._segment_map_lock:
            self._segment_map.clear()
        logger.info("Cache cleared")