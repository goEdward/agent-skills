"""Shared strict parsers and bounded identifier helpers for input validation."""

from __future__ import annotations

import math
import re
from typing import Any


MISSING_TEXT = {"", "-", "--", "n/a", "na", "none", "null"}
CURRENCY_RE = r"(?:US\$|USD|EUR|€|\$|￥|¥)"


def is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip().casefold() in MISSING_TEXT)


def parse_localized_token(value: Any, marketplace: Any = None) -> float:
    if isinstance(value, bool) or value is None:
        raise ValueError
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError
        return number
    token = str(value).strip().replace("\u00a0", "").replace(" ", "").replace("'", "")
    if not re.fullmatch(r"[+-]?\d[\d.,]*", token):
        raise ValueError
    negative = token.startswith("-")
    token = token.lstrip("+-")
    commas, dots = token.count(","), token.count(".")
    is_de = str(marketplace or "").casefold() in {"de", "eu-de"} or str(marketplace or "").casefold().endswith("-de")
    if commas and dots:
        decimal = "," if token.rfind(",") > token.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        integer, fraction = token.rsplit(decimal, 1)
        if not fraction.isdigit() or decimal in integer:
            raise ValueError
        if thousands in integer and not re.fullmatch(rf"\d{{1,3}}(?:{re.escape(thousands)}\d{{3}})+", integer):
            raise ValueError
        token = integer.replace(thousands, "") + "." + fraction
    elif commas:
        comma_thousands = bool(re.fullmatch(r"\d{1,3}(?:,\d{3})+", token))
        if commas > 1 and not comma_thousands:
            raise ValueError
        token = token.replace(",", "") if comma_thousands and not is_de else token.replace(",", ".")
    elif dots > 1:
        if not re.fullmatch(r"\d{1,3}(?:\.\d{3})+", token):
            raise ValueError
        token = token.replace(".", "")
    elif dots == 1 and is_de and re.fullmatch(r"\d{1,3}\.\d{3}", token):
        token = token.replace(".", "")
    number = float(token)
    if not math.isfinite(number):
        raise ValueError
    return -number if negative else number


def parse_unit_value(value: Any, *, kind: str, marketplace: Any = None) -> tuple[float, bool]:
    """Return numeric value and whether an explicit percent unit was supplied."""
    if is_missing(value):
        raise ValueError
    text = str(value).strip() if not isinstance(value, (int, float)) else ""
    has_percent = "%" in text
    currency_matches = re.findall(CURRENCY_RE, text, flags=re.I)
    has_currency = bool(currency_matches)
    if len(currency_matches) > 1:
        raise ValueError
    if text and (text.count("%") > 1 or ("%" in text and not re.fullmatch(r".*%\s*", text))):
        raise ValueError
    if kind in {"money", "count"} and has_percent:
        raise ValueError
    if kind in {"rate", "count"} and has_currency:
        raise ValueError
    if has_currency:
        text = re.sub(rf"^\s*{CURRENCY_RE}\s*|\s*{CURRENCY_RE}\s*$", "", text, flags=re.I)
        if re.search(CURRENCY_RE, text, flags=re.I):
            raise ValueError
    if has_percent:
        text = text.rsplit("%", 1)[0].strip()
        if re.fullmatch(r"[+-]?0,\d+", text.replace("\u00a0", "").replace(" ", "")):
            text = text.replace(",", ".")
    return parse_localized_token(text if text else value, marketplace), has_percent


def parse_economic_range(value: Any, *, kind: str, marketplace: Any = None) -> tuple[list[float], list[bool]]:
    if is_missing(value):
        raise ValueError
    if not isinstance(value, str):
        number, explicit = parse_unit_value(value, kind=kind, marketplace=marketplace)
        return [number], [explicit]
    text = value.strip()
    try:
        number, explicit = parse_unit_value(text, kind=kind, marketplace=marketplace)
        return [number], [explicit]
    except ValueError:
        pass
    parts = re.split(r"\s*(?:-|–|—|~|至|to)\s*", text, flags=re.I)
    if len(parts) != 2 or not all(parts):
        raise ValueError
    if kind == "rate" and text.rstrip().endswith("%") and "%" not in parts[0]:
        parts[0] = f"{parts[0]}%"
    parsed = [parse_unit_value(part, kind=kind, marketplace=marketplace) for part in parts]
    values = [item[0] for item in parsed]
    units = [item[1] for item in parsed]
    if units[0] != units[1]:
        raise ValueError
    return values, units


def normalize_rate(value: Any, *, field_name: str = "rate", marketplace: Any = None) -> float | None:
    if is_missing(value):
        return None
    values, units = parse_economic_range(value, kind="rate", marketplace=marketplace)
    if len(values) != 1:
        raise ValueError(f"Invalid {field_name} value {value!r}.")
    number = values[0] / 100 if units[0] or values[0] > 1 else values[0]
    if number < 0 or number > 1:
        raise ValueError(f"Invalid {field_name} value {value!r}.")
    return number


def normalize_positive_range(value: Any, *, field_name: str, kind: str, as_rate: bool = False, marketplace: Any = None) -> list[float] | None:
    if is_missing(value):
        return None
    values, units = parse_economic_range(value, kind=kind, marketplace=marketplace)
    normalized = [number / 100 if (as_rate and (explicit or number > 1)) else number for number, explicit in zip(values, units)]
    if any(number <= 0 for number in normalized) or (len(normalized) == 2 and normalized[0] > normalized[1]):
        raise ValueError(f"Invalid {field_name} value {value!r}.")
    if as_rate and any(number > 1 for number in normalized):
        raise ValueError(f"Invalid {field_name} value {value!r}.")
    return normalized


def bounded_identifier_in_text(identifier: Any, text: str) -> bool:
    value = str(identifier or "").strip().casefold()
    haystack = str(text or "").casefold()
    if not value:
        return False
    escaped = re.escape(value)
    return bool(re.search(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", haystack))
