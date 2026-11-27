#!/usr/bin/env python3
"""Fit Amazon backend Search Terms to an exclusive UTF-8 byte limit.

The script preserves input order, normalizes spacing/case, removes punctuation
and underscores, deduplicates words globally, and keeps only words that fit
within the requested Amazon-counted UTF-8 byte limit. Separator spaces are not
included in the Amazon-counted length.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Iterable


DEFAULT_EXCLUSIVE_LIMIT = 250


def byte_len(text: str) -> int:
    return len(text.encode("utf-8"))


def normalize_term(raw: str) -> list[str]:
    text = raw.strip().lower()
    text = re.sub(r"_+", " ", text)
    text = re.sub(r"[^\w\s-]", " ", text, flags=re.UNICODE)
    text = text.replace("-", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    return [word for word in text.split(" ") if word]


def fit_terms(raw_terms: Iterable[str], limit: int) -> dict[str, object]:
    seen: set[str] = set()
    kept: list[str] = []
    removed: list[str] = []
    counted_bytes = 0

    for raw in raw_terms:
        for word in normalize_term(raw):
            if word in seen:
                continue
            seen.add(word)

            word_bytes = byte_len(word)
            if counted_bytes + word_bytes < limit:
                kept.append(word)
                counted_bytes += word_bytes
            else:
                removed.append(word)

    final = " ".join(kept)
    return {
        "search_terms": final,
        "amazon_counted_bytes": counted_bytes,
        "raw_utf8_bytes": byte_len(final),
        "limit_exclusive": limit,
        "remaining_counted_bytes": limit - 1 - counted_bytes,
        "kept_terms": kept,
        "removed_terms": removed,
    }


def read_terms(args: argparse.Namespace) -> list[str]:
    terms: list[str] = []
    if args.stdin:
        terms.extend(line.rstrip("\n") for line in sys.stdin if line.strip())
    if args.terms:
        terms.extend(args.terms)
    if not terms:
        raise SystemExit("No terms provided. Use --terms or --stdin.")
    return terms


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fit Amazon Search Terms to an exclusive UTF-8 byte limit."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_EXCLUSIVE_LIMIT,
        help=(
            "Exclusive Amazon-counted UTF-8 byte limit. "
            f"Defaults to {DEFAULT_EXCLUSIVE_LIMIT}."
        ),
    )
    parser.add_argument(
        "--terms",
        nargs=argparse.REMAINDER,
        help=(
            "Candidate terms in priority order. This must be the final option; "
            "all following arguments are treated literally, including values that "
            "begin with '-'. Multi-word phrases are split into words."
        ),
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read one candidate term or phrase per line from stdin.",
    )
    args = parser.parse_args()

    if args.terms:
        for token in args.terms:
            if token in {"--stdin", "--terms", "-h", "--help"} or token.startswith(
                "--limit="
            ) or token == "--limit":
                parser.error(f"{token} must appear before --terms.")

    if args.limit < 1:
        raise SystemExit("--limit must be greater than 0.")

    result = fit_terms(read_terms(args), args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
