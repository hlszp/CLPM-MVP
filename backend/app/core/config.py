"""Application configuration via pydantic-settings.

Loads environment variables from `.env` (case-sensitive). All keys have safe
development defaults so the app can boot without an explicit `.env` file.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    JWT_SECRET_KEY: str = "clpm-dev-secret-key-change-in-production"
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
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:5174"]

    @property
    def postgres_dsn(self) -> str:
        """Async PostgreSQL DSN for SQLAlchemy 2.0."""
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)


settings = Settings()
