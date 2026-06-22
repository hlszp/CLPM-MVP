"""Application configuration via pydantic-settings.

Loads environment variables from `.env` (case-sensitive). All keys have safe
development defaults so the app can boot without an explicit `.env` file.

安全：生产环境（DEBUG=False）启动时强制校验 JWT_SECRET_KEY 不得为开发默认值。
"""

from __future__ import annotations

import logging
import os

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# 开发环境默认密钥（仅用于本地开发，生产环境必须通过 .env 覆盖）
_DEV_JWT_SECRET = "clpm-dev-secret-key-change-in-production"


class Settings(BaseSettings):
    """Global application settings."""

    # ---- Application ----
    APP_NAME: str = "CLPM"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True

    # ---- PostgreSQL ----
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "clpm"
    POSTGRES_PASSWORD: str = "clpm_dev_2026"
    POSTGRES_DB: str = "clpm"

    # ---- TDengine ----
    TDENGINE_HOST: str = "localhost"
    TDENGINE_PORT: int = 6030
    TDENGINE_USER: str = "root"
    TDENGINE_PASSWORD: str = "taosdata"
    TDENGINE_DB: str = "clpm_ts"

    # ---- Redis ----
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0

    # ---- JWT ----
    JWT_SECRET_KEY: str = _DEV_JWT_SECRET
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ---- Celery ----
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # ---- AAS (OPC UA) ----
    AAS_ENDPOINT: str = "opc.tcp://localhost:4840"
    AAS_MOCK_MODE: bool = True
    AAS_SYNC_INTERVAL_SECONDS: int = 300  # 5 分钟
    AAS_SYNC_ENABLED: bool = True
    AAS_CONNECT_TIMEOUT_SECONDS: int = 10
    AAS_REQUEST_TIMEOUT_SECONDS: int = 30
    AAS_SECURITY_MODE: str = "None"  # None/Sign/SignAndEncrypt

    # ---- CORS ----
    CORS_ORIGINS: list[str] = [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5666",
        "http://127.0.0.1:5666",
    ]

    @property
    def postgres_dsn(self) -> str:
        """Async PostgreSQL DSN for SQLAlchemy 2.0."""
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    def validate_security(self) -> None:
        """生产环境安全校验：DEBUG=False 时强制 JWT_SECRET_KEY 不得为开发默认值。"""
        if not self.DEBUG and self.JWT_SECRET_KEY == _DEV_JWT_SECRET:
            raise RuntimeError(
                "生产环境（DEBUG=False）必须通过环境变量 JWT_SECRET_KEY 设置独立密钥，"
                "不得使用开发默认值。"
            )
        if not self.DEBUG and len(self.JWT_SECRET_KEY) < 32:
            raise RuntimeError("生产环境 JWT_SECRET_KEY 长度不得少于 32 字符。")

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)


settings = Settings()

# 启动时执行安全校验（测试环境跳过，通过 PYTEST_CURRENT_TEST 环境变量识别）
if os.environ.get("CLPM_ENV") != "test" and not os.environ.get("PYTEST_CURRENT_TEST"):
    settings.validate_security()

