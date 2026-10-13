"""
Local filesystem implementation of StorageService.
Provides safe local object storage with path-traversal prevention.
"""

import os
import shutil
from pathlib import Path
from typing import BinaryIO

from backend.app.services.storage.base import (
    StorageError,
    StorageFileNotFoundError,
    StorageSecurityError,
    StorageService,
)


class LocalFileStorage(StorageService):
    """
    Local filesystem storage provider.
    Stores files under a configured root directory and prevents path traversal attacks.
    """

    def __init__(self, root_dir: str | Path) -> None:
        self.root_path = Path(root_dir).resolve()
        self.root_path.mkdir(parents=True, exist_ok=True)

    def _resolve_safe_path(self, key: str) -> Path:
        """
        Safely resolve a storage key to an absolute filesystem Path.
        Guarantees the target path cannot escape self.root_path.
        """
        if not key or not key.strip():
            raise StorageSecurityError("Storage key cannot be empty.")

        # Normalize key: strip leading slashes and convert backslashes
        clean_key = key.replace("\\", "/").lstrip("/")

        # Check for obvious traversal tokens before resolution
        parts = clean_key.split("/")
        if ".." in parts or "." in parts:
            raise StorageSecurityError(f"Path traversal detected in key: {key}")

        target = (self.root_path / clean_key).resolve()

        # Enforce boundary check
        try:
            target.relative_to(self.root_path)
        except ValueError as e:
            raise StorageSecurityError(f"Storage path escapes root directory: {key}") from e

        return target

    def save(self, content: bytes, key: str) -> str:
        """
        Save binary content to storage key using atomic file replacement.
        Creates intermediate directories as needed.
        """
        target = self._resolve_safe_path(key)
        target.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write via temporary file
        temp_target = target.with_suffix(f".tmp_{os.getpid()}_{target.name}")
        try:
            with open(temp_target, "wb") as f:
                f.write(content)
            temp_target.replace(target)
        except Exception as e:
            if temp_target.exists():
                temp_target.unlink()
            raise StorageError(f"Failed to write file to storage: {e}") from e

        # Return canonical key normalized with forward slashes
        return key.replace("\\", "/").lstrip("/")

    def read(self, key: str) -> bytes:
        """Read binary content from storage key."""
        target = self._resolve_safe_path(key)
        if not target.is_file():
            raise StorageFileNotFoundError(f"File not found in storage: {key}")
        try:
            with open(target, "rb") as f:
                return f.read()
        except Exception as e:
            raise StorageError(f"Failed to read file from storage: {e}") from e

    def open(self, key: str) -> BinaryIO:
        """Open a readable binary file stream."""
        target = self._resolve_safe_path(key)
        if not target.is_file():
            raise StorageFileNotFoundError(f"File not found in storage: {key}")
        return open(target, "rb")

    def delete(self, key: str) -> bool:
        """Delete file at key if it exists."""
        target = self._resolve_safe_path(key)
        if target.is_file():
            target.unlink()
            return True
        return False

    def exists(self, key: str) -> bool:
        """Check if file exists at key."""
        target = self._resolve_safe_path(key)
        return target.is_file()

    def get_size(self, key: str) -> int:
        """Return size in bytes of the stored file."""
        target = self._resolve_safe_path(key)
        if not target.is_file():
            raise StorageFileNotFoundError(f"File not found in storage: {key}")
        return target.stat().st_size
