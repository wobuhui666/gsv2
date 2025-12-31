"""路由模块"""

from .chat import router as chat_router
from .speech import router as speech_router

__all__ = ["chat_router", "speech_router"]