"""GSV TTS Proxy 服务 - 主入口"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .routers import chat_router, speech_router
from .services.proxy_client import ProxyClient
from .services.token_rotator import TokenRotator
from .services.tts_client import GSVTTSClient
from .services.tts_cache import TTSCacheManager

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    settings = get_settings()
    
    # 配置日志级别
    logging.getLogger().setLevel(getattr(logging, settings.log_level.upper()))
    
    logger.info("Starting GSV TTS Proxy...")
    logger.info(f"GSV API URL: {settings.gsv_api_url}")
    logger.info(f"GSV Tokens: {len(settings.gsv_token_list)} configured")
    logger.info(f"Default Voice: {settings.gsv_default_voice}")
    logger.info(f"NewAPI URL: {settings.newapi_base_url}")
    
    # 初始化代理客户端（用于 LLM）
    proxy_client = ProxyClient(
        base_url=settings.newapi_base_url,
        api_key=settings.newapi_api_key,
        timeout=settings.newapi_timeout,
    )
    await proxy_client.initialize()
    app.state.proxy_client = proxy_client
    
    # 初始化 Token 轮询器
    token_rotator = TokenRotator(tokens=settings.gsv_token_list)
    app.state.token_rotator = token_rotator
    
    # 初始化 GSV TTS 客户端
    tts_client = GSVTTSClient(
        api_url=settings.gsv_api_url,
        token_rotator=token_rotator,
        default_voice=settings.gsv_default_voice,
        default_model=settings.gsv_default_model,
        timeout=settings.tts_request_timeout,
        retry_count=settings.tts_retry_count,
    )
    await tts_client.initialize()
    app.state.tts_client = tts_client
    
    # 初始化 TTS 缓存管理器
    tts_cache = TTSCacheManager(
        tts_client=tts_client,
        max_size=settings.cache_max_size,
        ttl=settings.cache_ttl,
        cleanup_interval=settings.cache_cleanup_interval,
    )
    await tts_cache.start()
    app.state.tts_cache = tts_cache
    
    logger.info("GSV TTS Proxy started successfully!")
    
    yield
    
    # 清理资源
    logger.info("Shutting down GSV TTS Proxy...")
    await tts_cache.stop()
    await tts_client.close()
    await proxy_client.close()
    logger.info("GSV TTS Proxy stopped.")


# 创建 FastAPI 应用
app = FastAPI(
    title="GSV TTS Proxy",
    description="智能 TTS 代理服务 - 支持 Token 轮询和 LLM Chat 代理",
    version="1.0.0",
    lifespan=lifespan,
)

# 添加 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(chat_router)
app.include_router(speech_router)


@app.get("/")
async def root():
    """根路径 - 服务信息"""
    return {
        "service": "GSV TTS Proxy",
        "version": "1.0.0",
        "description": "智能 TTS 代理服务 - 支持 Token 轮询和 LLM Chat 代理",
    }


@app.get("/health")
async def health():
    """健康检查"""
    settings = get_settings()
    tts_cache = app.state.tts_cache
    tts_client = app.state.tts_client
    
    return {
        "status": "healthy",
        "version": "1.0.0",
        "cache_stats": tts_cache.get_stats(),
        "tts_client_stats": tts_client.get_stats(),
    }


@app.get("/cache/stats")
async def cache_stats():
    """缓存统计"""
    tts_cache = app.state.tts_cache
    return tts_cache.get_stats()


@app.post("/cache/clear")
async def clear_cache():
    """清空缓存"""
    tts_cache = app.state.tts_cache
    await tts_cache.clear()
    return {"status": "success", "message": "Cache cleared"}


@app.get("/tokens/stats")
async def token_stats():
    """Token 轮询统计"""
    token_rotator = app.state.token_rotator
    return token_rotator.get_stats()


@app.get("/v1/models")
async def list_models():
    """列出可用模型 - OpenAI 兼容"""
    import time
    settings = get_settings()
    
    return {
        "object": "list",
        "data": [
            {
                "id": settings.gsv_default_model,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "gsv-tts",
            }
        ]
    }


# 错误处理
@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return JSONResponse(
        status_code=404,
        content={
            "error": {
                "message": "Not found",
                "type": "invalid_request_error",
                "code": "not_found",
            }
        }
    )


@app.exception_handler(500)
async def internal_error_handler(request: Request, exc):
    logger.error(f"Internal error: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "message": "Internal server error",
                "type": "server_error",
                "code": "internal_error",
            }
        }
    )


if __name__ == "__main__":
    import uvicorn
    settings = get_settings()
    uvicorn.run(app, host=settings.host, port=settings.port)