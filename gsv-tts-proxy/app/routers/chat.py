"""Chat Completion 路由 - OpenAI 兼容的反向代理"""

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import StreamingResponse, JSONResponse

from ..config import get_settings
from ..models.schemas import ChatCompletionRequest
from ..services.text_splitter import StreamingTextSplitter
from ..services.proxy_client import extract_content_from_sse
from ..dependencies import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["chat"])


def get_proxy_client():
    """获取代理客户端（从应用状态）"""
    from ..main import app
    return app.state.proxy_client


def get_tts_cache():
    """获取 TTS 缓存管理器（从应用状态）"""
    from ..main import app
    return app.state.tts_cache


@router.post("/chat/completions")
async def chat_completions(request: Request, _: str = Depends(verify_api_key)):
    """
    Chat Completion 接口 - OpenAI 兼容的反向代理
    
    功能：
    1. 转发请求到 NewAPI
    2. 流式接收响应
    3. 同时分段预生成 TTS
    4. 返回原始响应给客户端
    5. 注册完整文本与分段的映射关系
    """
    settings = get_settings()
    proxy_client = get_proxy_client()
    tts_cache = get_tts_cache()
    
    # 解析请求体
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON body: {e}")
    
    # 获取 TTS 相关参数
    tts_enabled = body.get("tts_enabled", True)
    tts_model = body.get("tts_model", settings.gsv_default_model)
    is_stream = body.get("stream", False)
    
    # 如果原请求是非流式的，我们仍然使用流式请求源站，但聚合后返回
    if not is_stream:
        return await _handle_non_stream_request(
            body, proxy_client, tts_cache, tts_enabled, tts_model, settings
        )
    
    # 流式请求
    return StreamingResponse(
        _stream_response(body, proxy_client, tts_cache, tts_enabled, tts_model, settings),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _handle_non_stream_request(
    body: dict,
    proxy_client,
    tts_cache,
    tts_enabled: bool,
    tts_model: str,
    settings,
) -> JSONResponse:
    """
    处理非流式请求
    
    内部使用流式请求源站，收集完整响应后返回。
    同时进行 TTS 预生成。
    """
    # 创建文本分段器
    splitter = StreamingTextSplitter(
        max_len=settings.splitter_max_len,
        min_len=settings.splitter_min_len,
    )
    
    full_content = ""
    all_segments = []  # 收集所有分段
    full_response = None
    
    try:
        async for line in proxy_client.stream_chat(body):
            if not line.startswith("data: "):
                continue
            
            data = line[6:]
            if data == "[DONE]":
                continue
            
            try:
                chunk = json.loads(data)
                
                # 保存最后一个完整的响应结构
                if full_response is None:
                    full_response = {
                        "id": chunk.get("id"),
                        "object": "chat.completion",
                        "created": chunk.get("created"),
                        "model": chunk.get("model"),
                        "choices": [{
                            "index": 0,
                            "message": {"role": "assistant", "content": ""},
                            "finish_reason": None,
                        }],
                        "usage": None,
                    }
                
                # 提取内容
                content = extract_content_from_sse(line)
                if content:
                    full_content += content
                    
                    # TTS 预生成
                    if tts_enabled and tts_cache:
                        sentences = splitter.feed(content)
                        for sentence in sentences:
                            if sentence.strip():
                                all_segments.append(sentence.strip())
                                asyncio.create_task(
                                    tts_cache.submit(sentence.strip(), tts_model)
                                )
                
                # 检查是否结束
                choices = chunk.get("choices", [])
                if choices and choices[0].get("finish_reason"):
                    full_response["choices"][0]["finish_reason"] = choices[0]["finish_reason"]
                    
            except json.JSONDecodeError:
                continue
        
        # 处理剩余文本
        if tts_enabled and tts_cache:
            remaining = splitter.flush()
            if remaining and remaining.strip():
                all_segments.append(remaining.strip())
                asyncio.create_task(tts_cache.submit(remaining.strip(), tts_model))
        
        # 注册完整文本与分段的映射关系
        if tts_enabled and tts_cache and full_content.strip() and all_segments:
            asyncio.create_task(
                tts_cache.submit_with_segments(
                    full_content.strip(),
                    all_segments,
                    tts_model
                )
            )
        
        # 更新完整内容
        if full_response:
            full_response["choices"][0]["message"]["content"] = full_content
        else:
            # 如果没有收到任何响应，返回错误
            raise HTTPException(status_code=502, detail="No response from upstream")
        
        return JSONResponse(content=full_response)
        
    except Exception as e:
        logger.error(f"Chat request failed: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {e}")


async def _stream_response(
    body: dict,
    proxy_client,
    tts_cache,
    tts_enabled: bool,
    tts_model: str,
    settings,
):
    """
    流式响应生成器
    
    1. 转发源站的流式响应
    2. 同时分段预生成 TTS
    3. 注册完整文本与分段的映射关系
    """
    # 创建文本分段器
    splitter = StreamingTextSplitter(
        max_len=settings.splitter_max_len,
        min_len=settings.splitter_min_len,
    )
    
    full_content = ""  # 收集完整文本
    all_segments = []  # 收集所有分段
    
    try:
        async for line in proxy_client.stream_chat(body):
            # 直接转发给客户端
            yield f"{line}\n\n"
            
            # 提取文本内容用于 TTS 预生成
            if tts_enabled and tts_cache:
                content = extract_content_from_sse(line)
                if content:
                    full_content += content  # 累积完整文本
                    
                    # 分段检测
                    sentences = splitter.feed(content)
                    for sentence in sentences:
                        if sentence.strip():
                            all_segments.append(sentence.strip())
                            # 异步提交 TTS 生成，不阻塞流式返回
                            asyncio.create_task(
                                tts_cache.submit(sentence.strip(), tts_model)
                            )
        
        # 处理剩余文本
        if tts_enabled and tts_cache:
            remaining = splitter.flush()
            if remaining and remaining.strip():
                all_segments.append(remaining.strip())
                asyncio.create_task(tts_cache.submit(remaining.strip(), tts_model))
            
            # 注册完整文本与分段的映射关系
            if full_content.strip() and all_segments:
                asyncio.create_task(
                    tts_cache.submit_with_segments(
                        full_content.strip(),
                        all_segments,
                        tts_model
                    )
                )
                logger.debug(
                    f"Registered segment mapping for streaming response: "
                    f"full_len={len(full_content)}, segments={len(all_segments)}"
                )
                
    except Exception as e:
        logger.error(f"Stream error: {e}", exc_info=True)
        # 发送错误事件
        error_data = {
            "error": {
                "message": str(e),
                "type": "upstream_error",
            }
        }
        yield f"data: {json.dumps(error_data)}\n\n"