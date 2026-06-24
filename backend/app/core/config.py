"""Application configuration via pydantic-settings.

Loads environment variables from `.env` (case-sensitive).

安全：
- 数据库/TDengine/Redis 密码为必填项，无硬编码默认值
- DEBUG 默认 False，生产环境不暴露 /docs
- 生产环境（DEBUG=False）启动时强制校验 JWT_SECRET_KEY 和密码安全性
"""

from __future__ import annotations

import logging

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# 开发环境已知的不安全密码（生产环境禁止使用）
_INSECURE_PG_PASSWORD = "clpm_dev_2026"
_INSECURE_TD_PASSWORD = "taosdata"


class Settings(BaseSettings):
    """Global application settings."""

    # ---- Application ----
    APP_NAME: str = "CLPM"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    ENV: str = "development"  # development / production

    # ---- PostgreSQL ----
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "clpm"
    POSTGRES_PASSWORD: str = ""  # 必填，通过 .env 设置
    POSTGRES_DB: str = "clpm"

    # ---- TDengine ----
    TDENGINE_HOST: str = "localhost"
    TDENGINE_PORT: int = 6030
    TDENGINE_USER: str = "root"
    TDENGINE_PASSWORD: str = ""  # 必填，通过 .env 设置
    TDENGINE_DB: str = "clpm_ts"

    # ---- Redis ----
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = ""  # 必填，通过 .env 设置
    REDIS_DB: int = 0

    # ---- JWT ----
    JWT_SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ---- Celery ----
    CELERY_BROKER_URL: str = ""
    CELERY_RESULT_BACKEND: str = ""

    # ---- AAS (OPC UA) ----
    AAS_ENDPOINT: str = "opc.tcp://localhost:4840"
    AAS_MOCK_MODE: bool = True
    AAS_SYNC_INTERVAL_SECONDS: int = 300  # 5 分钟
    AAS_SYNC_ENABLED: bool = True
    AAS_CONNECT_TIMEOUT_SECONDS: int = 10
    AAS_REQUEST_TIMEOUT_SECONDS: int = 30
    AAS_SECURITY_MODE: str = "SignAndEncrypt"  # None/Sign/SignAndEncrypt

    # ---- Alerting ----
    ALERT_WEBHOOK_URL: str = ""  # 告警 webhook URL，为空则仅记录日志

    # ---- CORS ----
    CORS_ORIGINS: list[str] = [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5666",
        "http://localhost:5667",
        "http://localhost:5668",
        "http://127.0.0.1:5666",
        "http://127.0.0.1:5667",
    ]

    @property
    def postgres_dsn(self) -> str:
        """Async PostgreSQL DSN for SQLAlchemy 2.0."""
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def redis_url(self) -> str:
        """Redis URL with optional password."""
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def celery_broker_url(self) -> str:
        """Celery broker URL (Redis with password)."""
        if self.CELERY_BROKER_URL:
            return self.CELERY_BROKER_URL
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/1"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/1"

    @property
    def celery_result_backend(self) -> str:
        """Celery result backend URL (Redis with password)."""
        if self.CELERY_RESULT_BACKEND:
            return self.CELERY_RESULT_BACKEND
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/2"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/2"

    def validate_security(self) -> None:
        """生产环境安全校验：ENV=production 时强制校验密钥、密码和 AAS 安全模式。"""
        if self.ENV != "production":
            return

        # JWT 密钥校验
        if not self.JWT_SECRET_KEY:
            raise RuntimeError("生产环境必须通过环境变量 JWT_SECRET_KEY 设置密钥。")
        if len(self.JWT_SECRET_KEY) < 32:
            raise RuntimeError("生产环境 JWT_SECRET_KEY 长度不得少于 32 字符。")

        # 数据库密码校验
        if not self.POSTGRES_PASSWORD:
            raise RuntimeError("生产环境必须通过环境变量 POSTGRES_PASSWORD 设置数据库密码。")
        if self.POSTGRES_PASSWORD == _INSECURE_PG_PASSWORD:
            raise RuntimeError("生产环境不得使用开发默认数据库密码。")

        # TDengine 密码校验
        if not self.TDENGINE_PASSWORD:
            raise RuntimeError("生产环境必须通过环境变量 TDENGINE_PASSWORD 设置 TDengine 密码。")
        if self.TDENGINE_PASSWORD == _INSECURE_TD_PASSWORD:
            raise RuntimeError("生产环境不得使用 TDengine 默认密码 taosdata。")

        # Redis 密码校验
        if not self.REDIS_PASSWORD:
            raise RuntimeError("生产环境必须通过环境变量 REDIS_PASSWORD 设置 Redis 密码。")

        # AAS OPC UA 安全模式校验
        if self.AAS_SECURITY_MODE == "None":
            raise RuntimeError(
                "生产环境 AAS_SECURITY_MODE 不得为 None，必须使用 Sign 或 SignAndEncrypt。"
            )

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)


settings = Settings()

# 启动时执行安全校验（仅生产环境）
settings.validate_security()

