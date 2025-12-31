"""TTS Speech 路由 - OpenAI 兼容的语音合成接口"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import Response, JSONResponse

from ..config import get_settings
from ..models.schemas import SpeechRequest
from ..dependencies import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["speech"])


def get_tts_cache():
    """获取 TTS 缓存管理器（从应用状态）"""
    from ..main import app
    return app.state.tts_cache


def get_tts_client():
    """获取 TTS 客户端（从应用状态）"""
    from ..main import app
    return app.state.tts_client


@router.post("/audio/speech")
async def create_speech(request: SpeechRequest, _: str = Depends(verify_api_key)):
    """
    TTS 语音合成接口 - OpenAI 兼容
    
    功能：
    1. 查询缓存中是否有预生成的 TTS（包括分段拼接）
    2. 如果有分段映射，自动拼接多个分段音频
    3. 如果有直接缓存，直接返回
    4. 如果正在生成，等待完成
    5. 如果没有，现场生成
    """
    settings = get_settings()
    tts_cache = get_tts_cache()
    
    # 验证输入
    if not request.input or not request.input.strip():
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": "Input text cannot be empty",
                    "type": "invalid_request_error",
                    "code": "invalid_input",
                }
            }
        )
    
    # 使用请求中的模型，或默认模型
    model = request.model or settings.gsv_default_model
    text = request.input.strip()
    
    logger.info(f"TTS request: model={model}, text_len={len(text)}")
    
    try:
        # 从缓存获取（如果未命中会自动生成）
        audio = await tts_cache.get(
            text=text,
            model=model,
            timeout=settings.tts_request_timeout,
            generate_if_missing=True,
        )
        
        if audio is None:
            return JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "message": "Failed to generate audio",
                        "type": "server_error",
                        "code": "generation_failed",
                    }
                }
            )
        
        # 返回音频
        return Response(
            content=audio,
            media_type="audio/wav",
            headers={
                "Content-Disposition": "attachment; filename=speech.wav",
            }
        )
        
    except Exception as e:
        logger.error(f"TTS request failed: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "message": f"TTS generation failed: {str(e)}",
                    "type": "server_error",
                    "code": "generation_failed",
                }
            }
        )


@router.get("/audio/models")
async def list_tts_models():
    """
    列出可用的 TTS 模型
    """
    settings = get_settings()
    
    return {
        "object": "list",
        "data": [
            {
                "id": settings.gsv_default_model,
                "object": "model",
                "owned_by": "gsv-tts",
            }
        ]
    }