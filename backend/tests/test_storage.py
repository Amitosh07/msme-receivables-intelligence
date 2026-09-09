"""
Unit tests for object storage abstraction and LocalFileStorage implementation.
Verifies file operations, boundary enforcement, and path traversal security.
"""

import tempfile
import unittest
from pathlib import Path

from backend.app.services.storage.base import (
    StorageFileNotFoundError,
    StorageSecurityError,
)
from backend.app.services.storage.local import LocalFileStorage


class TestLocalStorageService(unittest.TestCase):
    """Test suite for LocalFileStorage implementation."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = LocalFileStorage(root_dir=self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_save_and_read(self):
        """Binary content can be saved and read back accurately."""
        content = b"%PDF-1.4 test document content 12345"
        key = "tenants/test-tenant/invoices/doc-1.pdf"

        saved_key = self.storage.save(content, key)
        self.assertEqual(saved_key, key)
        self.assertTrue(self.storage.exists(key))

        read_content = self.storage.read(key)
        self.assertEqual(read_content, content)
        self.assertEqual(self.storage.get_size(key), len(content))

    def test_open_stream(self):
        """Open returns a readable binary stream."""
        content = b"streamable content"
        key = "documents/test.pdf"
        self.storage.save(content, key)

        with self.storage.open(key) as stream:
            self.assertEqual(stream.read(), content)

    def test_delete_existing_file(self):
        """Deleting an existing file returns True and removes the file."""
        content = b"content to delete"
        key = "temp/delete_me.pdf"
        self.storage.save(content, key)
        self.assertTrue(self.storage.exists(key))

        deleted = self.storage.delete(key)
        self.assertTrue(deleted)
        self.assertFalse(self.storage.exists(key))

    def test_delete_non_existent_file(self):
        """Deleting a non-existent file returns False."""
        self.assertFalse(self.storage.delete("non_existent.pdf"))

    def test_read_non_existent_raises_not_found(self):
        """Reading a non-existent file raises StorageFileNotFoundError."""
        with self.assertRaises(StorageFileNotFoundError):
            self.storage.read("does_not_exist.pdf")

    def test_path_traversal_double_dot_rejected(self):
        """Keys containing '..' path traversal tokens must be rejected."""
        with self.assertRaises(StorageSecurityError):
            self.storage.save(b"data", "../../etc/passwd")

        with self.assertRaises(StorageSecurityError):
            self.storage.read("../outside.txt")

        with self.assertRaises(StorageSecurityError):
            self.storage.save(b"data", "tenants/../../../secret.txt")

    def test_path_traversal_backslash_normalized(self):
        """Backslashes in keys are normalized and cannot escape root."""
        with self.assertRaises(StorageSecurityError):
            self.storage.save(b"data", "..\\..\\windows\\system32")

    def test_empty_key_rejected(self):
        """Empty or whitespace-only keys must be rejected."""
        with self.assertRaises(StorageSecurityError):
            self.storage.save(b"data", "")

        with self.assertRaises(StorageSecurityError):
            self.storage.save(b"data", "   ")


if __name__ == "__main__":
    unittest.main()
