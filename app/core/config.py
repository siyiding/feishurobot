"""Configuration management."""
import os
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Application
    app_name: str = "整车测试助手飞书机器人"
    debug: bool = False
    log_level: str = "INFO"

    # Feishu
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_bot_name: str = "整车测试助手"

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""

    # Feishu Project API
    feishu_project_api_base: str = "https://project.feishu.cn/open-apis"
    
    # Intent recognition confidence threshold
    intent_confidence_threshold: float = 0.6

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
