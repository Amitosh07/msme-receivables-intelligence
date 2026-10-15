"""
Core configuration module for MSME Receivables Intelligence Platform.
Loads configuration from environment variables and .env file.
"""

import base64
import binascii
import logging
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict


# Project root directory (where .env lives)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

logger = logging.getLogger(__name__)

PRODUCTION_ENVIRONMENTS = frozenset({"production", "staging"})
PLACEHOLDER_SECRETS = frozenset(
    {
        "",
        "replace-with-a-long-random-secret",
        "changeme",
        "change-me",
        "secret",
        "placeholder",
        "your_password",
        "password",
        "postgres",
    }
)
MIN_JWT_SECRET_LENGTH = 32
ALLOWED_JWT_ALGORITHMS = frozenset({"HS256", "HS384", "HS512"})
ENCRYPTION_KEY_BYTES = 32


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

    # HTTP surface
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"
    API_DOCS_ENABLED: Optional[bool] = None

    # Rate limiting (fixed one-minute or one-hour windows, shared through Redis)
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_PER_MINUTE: int = 300
    RATE_LIMIT_LOGIN_PER_MINUTE: int = 10
    RATE_LIMIT_LOGIN_PER_ACCOUNT_PER_HOUR: int = 30
    RATE_LIMIT_REGISTER_PER_HOUR: int = 10
    RATE_LIMIT_UPLOADS_PER_MINUTE: int = 30
    RATE_LIMIT_DEMO_PER_MINUTE: int = 5

    # Storage (Local object-storage abstraction)
    STORAGE_BACKEND: str = "local"
    STORAGE_ROOT: str = "./storage"
    MAX_INVOICE_FILE_SIZE_MB: int = 10
    STORAGE_ENCRYPTION_KEY: str = ""
    STORAGE_ENCRYPTION_PREVIOUS_KEYS: str = ""
    STORAGE_ALLOW_PLAINTEXT_READS: bool = True

    # Redis Queue & Worker
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_TASK_QUEUE_NAME: str = "msme_tasks"
    WORKER_POLL_INTERVAL_SECONDS: float = 1.0
    MAX_TASK_RETRIES: int = 3
    PROCESSING_TASK_STALE_MINUTES: int = 5

    # App Info
    APP_NAME: str = "MSME Receivables Intelligence Platform"
    APP_VERSION: str = "1.0.0"

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.strip().lower() in PRODUCTION_ENVIRONMENTS

    @property
    def cors_origins(self) -> List[str]:
        return [origin.strip().rstrip("/") for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def api_docs_enabled(self) -> bool:
        if self.API_DOCS_ENABLED is not None:
            return self.API_DOCS_ENABLED
        return self.ENVIRONMENT.strip().lower() == "development"


class InsecureConfigurationError(RuntimeError):
    """Raised at startup when required secrets are missing or unsafe."""


def decode_encryption_key(value: str) -> bytes:
    """Decode a base64 (standard or URL-safe) 256-bit key; never echoes the value."""
    raw = value.strip()
    padded = raw + "=" * (-len(raw) % 4)
    try:
        # Normalize URL-safe input to standard base64 so validation rejects
        # otherwise silently ignored characters.
        standard = padded.replace("-", "+").replace("_", "/")
        key = base64.b64decode(standard, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InsecureConfigurationError("A storage encryption key is not valid base64.") from exc
    if len(key) != ENCRYPTION_KEY_BYTES:
        raise InsecureConfigurationError(
            f"A storage encryption key must decode to {ENCRYPTION_KEY_BYTES} bytes."
        )
    return key


def redact_url(url: str) -> str:
    """URL safe for logs: any password is replaced with ***."""
    parsed = urlparse(url)
    if "@" not in parsed.netloc:
        return url
    userinfo, host = parsed.netloc.rsplit("@", 1)
    user = userinfo.split(":", 1)[0]
    return parsed._replace(netloc=f"{user}:***@{host}").geturl()


def _database_password(url: str) -> Optional[str]:
    try:
        from sqlalchemy.engine import make_url

        return make_url(url).password
    except Exception:
        return None


def configuration_problems(config: Settings) -> List[str]:
    """Return human-readable problems that make this configuration unsafe to deploy.

    Messages name the offending setting but never include its value.
    """
    problems: List[str] = []

    secret = config.JWT_SECRET_KEY.strip()
    if secret.lower() in PLACEHOLDER_SECRETS or len(secret) < MIN_JWT_SECRET_LENGTH:
        problems.append(
            f"JWT_SECRET_KEY must be a random value of at least {MIN_JWT_SECRET_LENGTH} characters."
        )
    if config.JWT_ALGORITHM.upper() not in ALLOWED_JWT_ALGORITHMS:
        problems.append("JWT_ALGORITHM must be one of HS256, HS384 or HS512.")

    db_password = _database_password(config.DATABASE_URL)
    if db_password is None or db_password.strip().lower() in PLACEHOLDER_SECRETS:
        problems.append("DATABASE_URL must include a non-default database password.")

    if not urlparse(config.REDIS_URL).password:
        problems.append("REDIS_URL must include a Redis password.")

    if not config.STORAGE_ENCRYPTION_KEY.strip():
        problems.append("STORAGE_ENCRYPTION_KEY must be set so stored documents are encrypted.")

    origins = config.cors_origins
    if not origins:
        problems.append("CORS_ORIGINS must list the web origins allowed to call the API.")
    for origin in origins:
        parsed = urlparse(origin)
        if origin == "*" or "*" in origin:
            problems.append("CORS_ORIGINS must not contain wildcards.")
            break
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            problems.append("CORS_ORIGINS entries must be full origins such as https://app.example.com.")
            break

    if not config.RATE_LIMIT_ENABLED:
        problems.append("RATE_LIMIT_ENABLED must stay on outside development.")

    return problems


def enforce_secure_configuration(config: Settings) -> None:
    """Fail fast on unsafe production configuration; warn in development.

    Malformed encryption keys are rejected in every environment because they
    would make stored documents unreadable.
    """
    for value in [config.STORAGE_ENCRYPTION_KEY, *config.STORAGE_ENCRYPTION_PREVIOUS_KEYS.split(",")]:
        if value.strip():
            decode_encryption_key(value)

    problems = configuration_problems(config)
    if not problems:
        return
    if config.is_production:
        raise InsecureConfigurationError(
            "Refusing to start with an insecure configuration:\n- " + "\n- ".join(problems)
        )
    for problem in problems:
        logger.warning("Insecure setting (allowed in %s only): %s", config.ENVIRONMENT, problem)


settings = Settings()
