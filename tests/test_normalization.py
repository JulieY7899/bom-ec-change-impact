from __future__ import annotations

import unittest

from bom_change_assistant.normalization import (
    coerce_date,
    normalize_identifier,
    normalize_item_no,
)
from bom_change_assistant.privacy import PrivacyKey


class NormalizationTests(unittest.TestCase):
    def test_sap_padding_is_removed(self) -> None:
        self.assertEqual(normalize_identifier("0000123"), "123")
        self.assertEqual(normalize_identifier("0000AB-12"), "AB-12")
        self.assertEqual(normalize_item_no("000010"), "10")

    def test_display_separators_join_to_same_private_key(self) -> None:
        privacy = PrivacyKey(b"x" * 32)
        self.assertEqual(
            privacy.digest("material", "00AB123XYZ"),
            privacy.digest("material", "AB123-XYZ"),
        )

    def test_date_parsing(self) -> None:
        self.assertEqual(coerce_date("2026/07/24"), "2026-07-24")
        self.assertIsNone(coerce_date("not-a-date"))


if __name__ == "__main__":
    unittest.main()
