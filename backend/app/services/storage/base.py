"""
Storage service interface abstraction.
Decouples invoice document handling from concrete local/cloud storage implementations.
"""

from abc import ABC, abstractmethod
from typing import BinaryIO


class StorageError(Exception):
    """Base exception for storage operations."""
    pass


class StorageFileNotFoundError(StorageError):
    """Raised when a requested storage key does not exist."""
    pass


class StorageSecurityError(StorageError):
    """Raised when an unsafe path traversal attempt is detected."""
    pass


class StorageService(ABC):
    """
    Abstract object storage service interface.
    Enables swapping local filesystem storage with S3/GCS in future phases.
    """

    @abstractmethod
    def save(self, content: bytes, key: str) -> str:
        """
        Save binary content to storage key.
        Returns the canonical storage key.
        """
        pass

    @abstractmethod
    def read(self, key: str) -> bytes:
        """Read and return binary content from storage key."""
        pass

    @abstractmethod
    def open(self, key: str) -> BinaryIO:
        """Open and return a readable binary stream from storage key."""
        pass

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Delete object at storage key. Returns True if deleted, False if not found."""
        pass

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Check whether an object exists at the specified storage key."""
        pass

    @abstractmethod
    def get_size(self, key: str) -> int:
        """Return the size in bytes of the object at the storage key."""
        pass
