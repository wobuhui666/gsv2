"""NewAPI 代理客户端 - 支持流式响应转发"""

import json
import logging
from typing import AsyncIterator, Optional, Dict, Any
import httpx

logger = logging.getLogger(__name__)


class ProxyClient:
    """
    NewAPI 代理客户端
    
    负责将 Chat Completion 请求转发到 NewAPI，
    并支持流式响应。
    """
    
    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: int = 120,
    ):
        """
        初始化代理客户端。
        
        :param base_url: NewAPI 基础 URL
        :param api_key: API Key
        :param timeout: 请求超时时间（秒）
        """
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout
        self.client: Optional[httpx.AsyncClient] = None
    
    async def initialize(self):
        """初始化 HTTP 客户端"""
        if self.client is None:
            self.client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=10),
                follow_redirects=True,
            )
    
    async def close(self):
        """关闭 HTTP 客户端"""
        if self.client:
            await self.client.aclose()
            self.client = None
    
    def _get_headers(self, extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """构造请求头"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)
        return headers
    
    async def stream_chat(
        self,
        request_data: Dict[str, Any],
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> AsyncIterator[str]:
        """
        流式请求 Chat Completion。
        
        :param request_data: 请求数据（将被强制设置为 stream=True）
        :param extra_headers: 额外的请求头
        :yields: SSE 数据行
        """
        await self.initialize()
        
        # 强制使用流式模式
        request_data = {**request_data, "stream": True}
        
        # 移除 TTS 加速器特有的参数
        request_data.pop("tts_enabled", None)
        request_data.pop("tts_model", None)
        
        url = f"{self.base_url}/v1/chat/completions"
        headers = self._get_headers(extra_headers)
        
        logger.debug(f"Streaming chat request to {url}")
        
        async with self.client.stream(
            "POST",
            url,
            json=request_data,
            headers=headers,
        ) as response:
            response.raise_for_status()
            
            async for line in response.aiter_lines():
                if line:
                    yield line
    
    async def chat(
        self,
        request_data: Dict[str, Any],
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        非流式请求 Chat Completion。
        
        :param request_data: 请求数据
        :param extra_headers: 额外的请求头
        :return: 响应数据
        """
        await self.initialize()
        
        # 移除 TTS 加速器特有的参数
        request_data = {**request_data}
        request_data.pop("tts_enabled", None)
        request_data.pop("tts_model", None)
        
        url = f"{self.base_url}/v1/chat/completions"
        headers = self._get_headers(extra_headers)
        
        logger.debug(f"Chat request to {url}")
        
        response = await self.client.post(
            url,
            json=request_data,
            headers=headers,
        )
        response.raise_for_status()
        
        return response.json()
    
    async def forward_request(
        self,
        path: str,
        method: str = "POST",
        data: Optional[Dict[str, Any]] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> httpx.Response:
        """
        转发任意请求到 NewAPI。
        
        :param path: 请求路径（不包含 base_url）
        :param method: HTTP 方法
        :param data: 请求数据
        :param extra_headers: 额外的请求头
        :return: 响应对象
        """
        await self.initialize()
        
        url = f"{self.base_url}{path}"
        headers = self._get_headers(extra_headers)
        
        response = await self.client.request(
            method=method,
            url=url,
            json=data,
            headers=headers,
        )
        
        return response


def extract_content_from_sse(line: str) -> Optional[str]:
    """
    从 SSE 行中提取文本内容。
    
    :param line: SSE 数据行
    :return: 提取的文本内容，如果没有则返回 None
    """
    if not line.startswith("data: "):
        return None
    
    data = line[6:]  # 移除 "data: " 前缀
    
    if data == "[DONE]":
        return None
    
    try:
        parsed = json.loads(data)
        choices = parsed.get("choices", [])
        if choices:
            delta = choices[0].get("delta", {})
            content = delta.get("content")
            return content
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse SSE data: {data}")
    
    return None