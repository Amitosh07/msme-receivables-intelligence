"""
Storage abstraction package.
Provides factory function to retrieve the configured StorageService instance.
"""

from functools import lru_cache
from pathlib import Path

from backend.app.core.config import decode_encryption_key, settings
from backend.app.services.storage.base import (
    StorageError,
    StorageFileNotFoundError,
    StorageSecurityError,
    StorageService,
)
from backend.app.services.storage.encrypted import EncryptedStorage
from backend.app.services.storage.local import LocalFileStorage


def build_storage(config=settings) -> StorageService:
    if config.STORAGE_BACKEND != "local":
        raise NotImplementedError(f"Unsupported storage backend: {config.STORAGE_BACKEND}")
    storage: StorageService = LocalFileStorage(root_dir=Path(config.STORAGE_ROOT))
    if config.STORAGE_ENCRYPTION_KEY.strip():
        storage = EncryptedStorage(
            storage,
            primary_key=decode_encryption_key(config.STORAGE_ENCRYPTION_KEY),
            previous_keys=[
                decode_encryption_key(k) for k in config.STORAGE_ENCRYPTION_PREVIOUS_KEYS.split(",") if k.strip()
            ],
            allow_plaintext_reads=config.STORAGE_ALLOW_PLAINTEXT_READS,
        )
    return storage


@lru_cache(maxsize=1)
def get_storage() -> StorageService:
    """
    Returns the singleton StorageService instance configured for the application:
    local files under settings.STORAGE_ROOT, encrypted when STORAGE_ENCRYPTION_KEY is set.
    """
    return build_storage()


__all__ = [
    "StorageService",
    "LocalFileStorage",
    "EncryptedStorage",
    "StorageError",
    "StorageFileNotFoundError",
    "StorageSecurityError",
    "build_storage",
    "get_storage",
]
