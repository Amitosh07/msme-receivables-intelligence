"""
Core configuration module for MSME Receivables Intelligence Platform.
Loads configuration from environment variables and .env file.
"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


# Project root directory (where .env lives)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


class Settings(BaseSettings):
    """Application settings."""

    # Database
    DATABASE_URL: str = "postgresql+psycopg://postgres:postgres@localhost:5432/msme_receivables"

    # JWT Authentication
    JWT_SECRET_KEY: str = "replace-with-a-long-random-secret"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # Environment
    ENVIRONMENT: str = "development"

    # Storage (Local object-storage abstraction)
    STORAGE_BACKEND: str = "local"
    STORAGE_ROOT: str = "./storage"
    MAX_INVOICE_FILE_SIZE_MB: int = 10

    # Redis Queue & Worker
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_TASK_QUEUE_NAME: str = "msme_tasks"
    WORKER_POLL_INTERVAL_SECONDS: float = 1.0
    MAX_TASK_RETRIES: int = 3

    # App Info
    APP_NAME: str = "MSME Receivables Intelligence Platform"
    APP_VERSION: str = "1.0.0"

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
