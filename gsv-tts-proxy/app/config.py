"""配置管理模块"""

from typing import List, Optional
from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """应用配置"""
    
    # 服务器配置
    host: str = Field(default="0.0.0.0", description="服务监听地址")
    port: int = Field(default=8000, description="服务监听端口")
    
    # GSV TTS API 配置
    gsv_api_url: str = Field(
        default="https://gsv2p.acgnai.top",
        description="GSV TTS API 基础 URL"
    )
    gsv_api_tokens: str = Field(
        ...,
        description="GSV API Token 列表，逗号分隔"
    )
    gsv_default_voice: str = Field(
        default="原神-中文-胡桃_ZH",
        description="默认语音角色"
    )
    gsv_default_model: str = Field(
        default="GSVI-v4",
        description="默认 TTS 模型"
    )
    
    # NewAPI 配置（用于 LLM 代理）
    newapi_base_url: str = Field(..., description="NewAPI 基础 URL")
    newapi_api_key: str = Field(..., description="NewAPI API Key")
    newapi_timeout: int = Field(default=120, description="NewAPI 请求超时时间（秒）")
    
    # TTS 配置
    tts_request_timeout: int = Field(default=60, description="TTS 请求超时时间（秒）")
    tts_retry_count: int = Field(default=2, description="TTS 请求重试次数")
    
    # 缓存配置
    cache_max_size: int = Field(default=1000, description="缓存最大条目数")
    cache_ttl: int = Field(default=3600, description="缓存过期时间（秒）")
    cache_cleanup_interval: int = Field(default=300, description="缓存清理间隔（秒）")
    
    # 文本分段配置
    splitter_max_len: int = Field(default=40, description="分段最大长度")
    splitter_min_len: int = Field(default=5, description="分段最小长度")
    
    # 日志配置
    log_level: str = Field(default="INFO", description="日志级别")
    
    @property
    def gsv_token_list(self) -> List[str]:
        """解析 GSV API Token 列表"""
        return [token.strip() for token in self.gsv_api_tokens.split(",") if token.strip()]
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


# 全局配置实例
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """获取配置实例（单例）"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reload_settings() -> Settings:
    """重新加载配置"""
    global _settings
    _settings = Settings()
    return _settings