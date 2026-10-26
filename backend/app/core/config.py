from typing import List, Union
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/taskmesh"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # API Configuration
    API_PORT: int = 8000
    API_HOST: str = "0.0.0.0"
    CORS_ORIGINS: Union[List[str], str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    # Queue & Worker Settings
    DEFAULT_JOB_LEASE_TIMEOUT_SECONDS: int = 60
    DISPATCHER_POLL_INTERVAL_SECONDS: float = 1.0
    WORKER_CONCURRENCY: int = 4

    # Google Gemini AI Diagnostics
    GEMINI_API_KEY: str | None = None
    GEMINI_MODEL: str = "gemini-3.5-flash"


settings = Settings()

