"""
Encryption-at-rest wrapper for any StorageService.

Each object is sealed with AES-256-GCM under a random 96-bit nonce. The
storage key is bound as associated data, so a ciphertext copied to another
tenant's path fails authentication instead of decrypting. A short key id in
the header lets older keys keep decrypting during rotation.

Layout: MAGIC (8) | key id (8) | nonce (12) | ciphertext + GCM tag (16)
"""

from __future__ import annotations

import hashlib
import io
import os
from typing import BinaryIO, Dict, Iterable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backend.app.services.storage.base import StorageError, StorageService

MAGIC = b"MSMEENC1"
KEY_ID_BYTES = 8
NONCE_BYTES = 12
HEADER_BYTES = len(MAGIC) + KEY_ID_BYTES + NONCE_BYTES


def key_id(key: bytes) -> bytes:
    return hashlib.sha256(b"msme-storage-key-id" + key).digest()[:KEY_ID_BYTES]


def canonical_key(key: str) -> str:
    return key.replace("\\", "/").lstrip("/")


def is_encrypted(blob: bytes) -> bool:
    return blob.startswith(MAGIC)


class EncryptedStorage(StorageService):
    def __init__(
        self,
        inner: StorageService,
        primary_key: bytes,
        previous_keys: Iterable[bytes] = (),
        allow_plaintext_reads: bool = True,
    ) -> None:
        self.inner = inner
        self._primary_id = key_id(primary_key)
        self._primary = AESGCM(primary_key)
        self._keys: Dict[bytes, AESGCM] = {self._primary_id: self._primary}
        for old in previous_keys:
            self._keys.setdefault(key_id(old), AESGCM(old))
        self.allow_plaintext_reads = allow_plaintext_reads

    def encrypt(self, content: bytes, key: str) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        header = MAGIC + self._primary_id
        sealed = self._primary.encrypt(nonce, content, header + canonical_key(key).encode("utf-8"))
        return header + nonce + sealed

    def decrypt(self, blob: bytes, key: str) -> bytes:
        if not is_encrypted(blob):
            if self.allow_plaintext_reads:
                return blob
            raise StorageError("Stored object is not encrypted and plaintext reads are disabled.")
        if len(blob) < HEADER_BYTES + 16:
            raise StorageError("Stored object is truncated and cannot be decrypted.")
        kid = blob[len(MAGIC) : len(MAGIC) + KEY_ID_BYTES]
        cipher = self._keys.get(kid)
        if cipher is None:
            raise StorageError("Stored object was encrypted with a key that is not configured.")
        nonce = blob[len(MAGIC) + KEY_ID_BYTES : HEADER_BYTES]
        aad = MAGIC + kid + canonical_key(key).encode("utf-8")
        try:
            return cipher.decrypt(nonce, blob[HEADER_BYTES:], aad)
        except InvalidTag as exc:
            raise StorageError("Stored object failed integrity verification.") from exc

    def needs_rewrite(self, blob: bytes) -> bool:
        """True for plaintext objects and objects sealed with a non-primary key."""
        return not is_encrypted(blob) or blob[len(MAGIC) : len(MAGIC) + KEY_ID_BYTES] != self._primary_id

    def save(self, content: bytes, key: str) -> str:
        return self.inner.save(self.encrypt(content, key), key)

    def read(self, key: str) -> bytes:
        return self.decrypt(self.inner.read(key), key)

    def open(self, key: str) -> BinaryIO:
        return io.BytesIO(self.read(key))

    def delete(self, key: str) -> bool:
        return self.inner.delete(key)

    def exists(self, key: str) -> bool:
        return self.inner.exists(key)

    def get_size(self, key: str) -> int:
        return len(self.read(key))
