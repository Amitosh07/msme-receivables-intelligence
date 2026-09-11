"""Focused Phase F trust, evidence, timezone, and worker-boundary regressions."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import unittest
import uuid
from unittest.mock import patch

from sqlalchemy import select

from backend.app.models.payment import Payment
from backend.app.services.manual_payment_service import _validate_payment_date
from backend.app.services.payment_proof_parser import ExtractedProof
from backend.app.services.payment_proof_service import PROOF_VERIFICATION_CONFIDENCE_THRESHOLD


class TestPhaseFPolicy(unittest.TestCase):
    def test_india_business_date_is_used_for_timezone_aware_payment(self):
        # 19:00 UTC is 00:30 of the following day in Asia/Kolkata.
        with patch("backend.app.services.manual_payment_service.business_today", return_value=date(2026, 9, 12)):
            normalized = _validate_payment_date(datetime(2026, 9, 11, 19, 0, tzinfo=timezone.utc))
        self.assertEqual(normalized, datetime(2026, 9, 12, tzinfo=timezone.utc))

    def test_future_business_date_is_rejected_for_all_payment_paths(self):
        with patch("backend.app.services.manual_payment_service.business_today", return_value=date(2026, 9, 11)):
            with self.assertRaisesRegex(ValueError, "cannot be in the future"):
                _validate_payment_date(date(2026, 9, 12))

    def test_operational_confidence_threshold_is_locked(self):
        self.assertEqual(PROOF_VERIFICATION_CONFIDENCE_THRESHOLD, 0.85)
        self.assertEqual(ExtractedProof(confidence=0.8).candidate_count, 1)


if __name__ == "__main__":
    unittest.main()
