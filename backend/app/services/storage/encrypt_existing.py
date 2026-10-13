"""
Encrypt plaintext objects (and re-seal objects under retired keys) in local storage.

Dry run by default; pass --apply to rewrite files in place:

    python -m backend.app.services.storage.encrypt_existing [--apply]

After a successful run, set STORAGE_ALLOW_PLAINTEXT_READS=false and remove
retired keys from STORAGE_ENCRYPTION_PREVIOUS_KEYS.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from typing import Optional, Sequence

from backend.app.core.config import Settings, settings
from backend.app.services.storage import build_storage
from backend.app.services.storage.encrypted import EncryptedStorage
from backend.app.services.storage.local import LocalFileStorage

logger = logging.getLogger(__name__)


@dataclass
class MigrationReport:
    scanned: int = 0
    rewritten: int = 0
    already_current: int = 0
    failed: int = 0


def migrate(storage: EncryptedStorage, apply: bool) -> MigrationReport:
    if not isinstance(storage.inner, LocalFileStorage):
        raise RuntimeError("Only local storage can be migrated in place.")
    root = storage.inner.root_path
    report = MigrationReport()
    for path in sorted(p for p in root.rglob("*") if p.is_file() and ".tmp_" not in p.name):
        key = path.relative_to(root).as_posix()
        report.scanned += 1
        try:
            blob = storage.inner.read(key)
            if not storage.needs_rewrite(blob):
                report.already_current += 1
                continue
            plaintext = storage.decrypt(blob, key)
            if apply:
                storage.save(plaintext, key)
                if storage.read(key) != plaintext:
                    raise RuntimeError("verification after rewrite failed")
            report.rewritten += 1
        except Exception as exc:
            report.failed += 1
            logger.error("Could not migrate %s: %s", key, exc)
    return report


def main(argv: Optional[Sequence[str]] = None, config: Settings = settings) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="rewrite files (default: report only)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    storage = build_storage(config)
    if not isinstance(storage, EncryptedStorage):
        logger.error("STORAGE_ENCRYPTION_KEY is not set; nothing to encrypt with.")
        return 2
    storage.allow_plaintext_reads = True
    report = migrate(storage, apply=args.apply)
    verb = "Encrypted" if args.apply else "Would encrypt"
    logger.info(
        "%s %d of %d files (%d already current, %d failed).",
        verb, report.rewritten, report.scanned, report.already_current, report.failed,
    )
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
