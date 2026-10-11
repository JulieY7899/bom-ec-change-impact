from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

_SPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[\s_\-（）()【】\[\]：:/]+")


def normalize_identifier(value: Any) -> str:
    """Normalize an ERP identifier without logging or persisting its raw form."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        if value.is_integer():
            return str(int(value))
        return format(value, ".15g")
    text = _SPACE_RE.sub(" ", str(value).strip())
    if not text:
        return ""
    try:
        number = Decimal(text)
        if number == number.to_integral_value() and "." in text:
            return str(number.quantize(Decimal("1")))
    except (InvalidOperation, ValueError):
        pass
    text = text.upper()
    # SAP exports frequently mix internal, zero-padded keys with display-format
    # keys from other tables. Treat leading zeroes as formatting so material and
    # order joins remain stable across exports.
    without_padding = text.lstrip("0")
    return without_padding or "0"


def normalize_item_no(value: Any) -> str:
    text = normalize_identifier(value)
    if text.isdigit():
        return str(int(text))
    return text


def normalize_header(value: Any) -> str:
    if value is None:
        return ""
    return _PUNCT_RE.sub("", str(value).strip().casefold())


def coerce_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        result = float(value)
        return None if math.isnan(result) else result
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def coerce_int(value: Any) -> int | None:
    number = coerce_number(value)
    return int(number) if number is not None else None


def coerce_date(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)):
        # Defensive fallback for unstyled Excel date serials.
        if 1 <= float(value) <= 2_958_465:
            return (date(1899, 12, 30) + timedelta(days=int(value))).isoformat()
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("/", "-").replace(".", "-")
    text = re.sub(r"\s+.*$", "", text)
    for pattern in ("%Y-%m-%d", "%Y%m%d", "%d-%m-%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    return None


def safe_text(value: Any, max_length: int = 240) -> str:
    if value is None:
        return ""
    text = _SPACE_RE.sub(" ", str(value).strip())
    return text[:max_length]
