#!/usr/bin/env python3
"""Strict shared validators for listing-generation input schemas."""

from __future__ import annotations


def boolean_field(
    data: dict[str, object],
    key: str,
    *,
    field: str | None = None,
    default: bool = False,
) -> bool:
    """Return an optional JSON boolean without truthiness coercion."""
    if key not in data:
        return default
    value = data[key]
    if type(value) is not bool:
        raise ValueError(f"{field or key} must be a JSON boolean")
    return value


def nonnegative_integer(value: object, field: str) -> int:
    """Require a JSON integer suitable for a count-based strategy."""
    if type(value) is not int or value < 0:
        raise ValueError(
            f"{field}: min_count requires numeric scalars that are finite "
            "non-negative integers; use set_intersection for component lists"
        )
    return value
