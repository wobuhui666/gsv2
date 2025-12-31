"""依赖项模块 - API 鉴权等"""

import logging
from typing import Optional

from fastapi import Header, HTTPException, status

from .config import get_settings

logger = logging.getLogger(__name__)


async def verify_api_key(
    authorization: Optional[str] = Header(None, description="Bearer token for authentication")
) -> str:
    """
    验证 API Key 的依赖项
    
    验证请求头中的 Authorization: Bearer <token> 是否与配置的 NEWAPI_API_KEY 匹配。
    
    Args:
        authorization: HTTP Authorization 头的值
        
    Returns:
        验证通过的 API Key
        
    Raises:
        HTTPException: 401 如果验证失败
    """
    settings = get_settings()
    expected_key = settings.newapi_api_key
    
    if not authorization:
        logger.warning("Missing Authorization header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "message": "Missing Authorization header",
                    "type": "authentication_error",
                    "code": "missing_authorization",
                }
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # 解析 Bearer token
    parts = authorization.split()
    
    if len(parts) != 2 or parts[0].lower() != "bearer":
        logger.warning("Invalid Authorization header format")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "message": "Invalid Authorization header format. Expected: Bearer <token>",
                    "type": "authentication_error",
                    "code": "invalid_authorization_format",
                }
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    token = parts[1]
    
    # 验证 token
    if token != expected_key:
        logger.warning("Invalid API key provided")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "message": "Invalid API key",
                    "type": "authentication_error",
                    "code": "invalid_api_key",
                }
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return token