from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from typing import Any

from .normalization import normalize_identifier


@dataclass(frozen=True)
class PrivacyKey:
    secret: bytes

    @classmethod
    def create(cls) -> "PrivacyKey":
        return cls(secrets.token_bytes(32))

    @classmethod
    def from_text(cls, value: str) -> "PrivacyKey":
        return cls(base64.urlsafe_b64decode(value.encode("ascii")))

    def to_text(self) -> str:
        return base64.urlsafe_b64encode(self.secret).decode("ascii")

    def digest(self, kind: str, raw_value: Any) -> str:
        normalized = normalize_identifier(raw_value)
        if not normalized:
            return ""
        if kind in {
            "material",
            "sales_order",
            "sales_item",
            "sales_order_item",
            "production_order",
            "change",
            "change_object",
            "configuration",
            "plant",
        }:
            # Different SAP exports may add/remove display separators (for example,
            # a hyphen in a material number). They are formatting, not identity.
            normalized = re.sub(r"[\s\-_/\.]+", "", normalized)
        payload = f"{kind}:{normalized}".encode("utf-8")
        return hmac.new(self.secret, payload, hashlib.sha256).hexdigest()

    def safety_identifier(self) -> str:
        return hmac.new(
            self.secret, b"openai-safety:local-user", hashlib.sha256
        ).hexdigest()[:32]


def file_fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
