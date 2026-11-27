#!/usr/bin/env python3
"""Measure English/German competitor keyword signals.

Modes
-----
coverage:
    Discover unigram/bigram surface phrases across product blocks. This is an
    objective signal for title-differentiator analysis, not a semantic judge.

bullet:
    Count explicit candidate phrases across each competitor listing's Bullet
    Points. Report listing coverage, per-listing counts, non-zero median/mean,
    and a conservative suggested repetition range for the generated five
    bullets.

Portable command-line examples:
    python3 kwfreq.py coverage --target "target text" \
        --competitor "competitor one" --competitor "competitor two"

    py -3 kwfreq.py bullet --keyword "coffee distributor" \
        --competitor "all bullets from listing one" \
        --competitor "all bullets from listing two"

JSON stdin is also supported for long inputs:
    {
      "mode": "bullet",
      "keywords": ["coffee distributor"],
      "competitors": [
        ["listing 1 bullet 1", "listing 1 bullet 2"],
        ["listing 2 bullet 1", "listing 2 bullet 2"]
      ]
    }

Competitor entries in JSON may be strings or lists of bullet strings. In bullet
mode, pass Bullet Point text only; do not include titles or Item Highlights.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from pathlib import Path
from typing import Iterable


# Keep decimal values (and adjoining model letters) in one keyword token.
# Otherwise 1.5 mm can satisfy 5 mm, or collide with the range 1-5 mm.
# Decimal punctuation must follow a digit, not join "handle,2" into one token.
# Exclude multiplication/division signs (U+00D7, U+00F7) from the Latin range
# while retaining German uppercase sharp S (ẞ, U+1E9E).
TOKEN_CLASS = r"0-9a-zA-ZÀ-ÖØ-öø-ɏẞ"
TOKEN_RE = re.compile(rf"[{TOKEN_CLASS}]+(?:(?<=\d)[.,][0-9]+[{TOKEN_CLASS}]*)*")
JOINED_UNIT_RE = re.compile(
    r"(?<=\d)(?=(?:mm|cm|ml|kg|oz|lbs?|in|ft|m|l|g)\b)",
    re.IGNORECASE,
)
STOP = {
    # English
    "the", "and", "for", "with", "from", "this", "that", "these", "those",
    "your", "you", "our", "are", "is", "was", "were", "have", "has", "had",
    "into", "onto", "over", "under", "than", "then", "when", "while", "can",
    "will", "not", "but", "its", "use", "using", "of", "to", "in", "on",
    "at", "by", "as", "a", "an", "or",
    # German
    "und", "oder", "der", "die", "das", "den", "dem", "des", "ein", "eine",
    "einer", "einem", "einen", "für", "fur", "mit", "von", "vom", "zur",
    "zum", "zu", "im", "in", "am", "an", "auf", "aus", "ist", "sind", "war",
    "waren", "wird", "werden", "als", "bei", "durch", "über", "uber", "unter",
    "diese", "dieser", "dieses", "ihre", "ihr", "sie",
}
MIN_LEN = 3


def tokens(text: str) -> list[str]:
    normalized = JOINED_UNIT_RE.sub(" ", text or "")
    return [token.lower() for token in TOKEN_RE.findall(normalized)]


def is_content_token(token: str) -> bool:
    return (
        (len(token) >= MIN_LEN or any(char.isdigit() for char in token))
        and token not in STOP
    )


def discovery_phrases(text: str) -> set[str]:
    raw_tokens = tokens(text)
    kept = [token for token in raw_tokens if is_content_token(token)]
    result = set(kept)
    result.update(
        f"{raw_tokens[index]} {raw_tokens[index + 1]}"
        for index in range(len(raw_tokens) - 1)
        if is_content_token(raw_tokens[index])
        and is_content_token(raw_tokens[index + 1])
    )
    return result


def flatten_competitor(value: object) -> str:
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    return str(value or "")


def phrase_count(text: str, phrase: str) -> int:
    needle = tokens(phrase)
    if not needle:
        return 0
    width = len(needle)
    total = 0
    for segment in text.splitlines() or [text]:
        haystack = tokens(segment)
        total += sum(
            1
            for index in range(max(0, len(haystack) - width + 1))
            if haystack[index:index + width] == needle
        )
    return total


def alternative_phrase_count(text: str, phrases: Iterable[str]) -> int:
    """Count alternative forms without double-counting overlapping tokens."""
    seen_needles: set[tuple[str, ...]] = set()
    for phrase in phrases:
        needle = tuple(tokens(phrase))
        if needle:
            seen_needles.add(needle)
    total = 0
    for segment in text.splitlines() or [text]:
        haystack = tokens(segment)
        spans: set[tuple[int, int]] = set()
        for needle in seen_needles:
            if len(needle) > len(haystack):
                continue
            width = len(needle)
            spans.update(
                (index, index + width)
                for index in range(len(haystack) - width + 1)
                if tuple(haystack[index:index + width]) == needle
            )
        previous_end = 0
        for start, end in sorted(spans, key=lambda span: (span[1], span[0])):
            if start >= previous_end:
                total += 1
                previous_end = end
    return total


def format_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def round_half_up(value: float) -> int:
    return int(math.floor(value + 0.5))


def suggestion(counts: list[int], keyword: str = "") -> str:
    total_listings = len(counts)
    positive = [count for count in counts if count > 0]
    coverage = len(positive)

    if not positive:
        return "0"
    if total_listings <= 1:
        return "0-1"
    if total_listings == 2:
        return "1-2" if coverage == 2 else "0-1"

    broadly_supported = coverage >= 2 and coverage / total_listings >= 0.60
    if not broadly_supported:
        return "1" if coverage >= 2 else "0-1"

    cap = 6 if len(tokens(keyword)) == 1 else 3
    baseline = min(cap, max(1, round_half_up(statistics.median(positive))))
    upper = min(cap, max(baseline, round_half_up(statistics.mean(positive))))
    return str(baseline) if baseline == upper else f"{baseline}-{upper}"


def unique_in_order(values: Iterable[str]) -> list[str]:
    seen: set[tuple[str, ...]] = set()
    result: list[str] = []
    for value in values:
        key = tuple(tokens(value))
        if key and key not in seen:
            seen.add(key)
            result.append(value.strip())
    return result


def run_coverage(target: str, competitors: list[str], limit: int) -> None:
    target_set = discovery_phrases(target)
    competitor_sets = [discovery_phrases(text) for text in competitors]
    universe = set(target_set)
    for competitor_set in competitor_sets:
        universe.update(competitor_set)

    rows = []
    for phrase in universe:
        coverage = sum(phrase in block for block in competitor_sets)
        in_target = phrase in target_set
        if coverage < 2 and not in_target:
            continue
        rows.append((phrase, in_target, coverage))

    rows.sort(key=lambda row: (-row[2], not row[1], row[0]))
    print(f"MODE=coverage\tCOMPETITORS={len(competitors)}")
    for phrase, in_target, coverage in rows[:limit]:
        print(
            f"{phrase}\tTARGET={'yes' if in_target else 'no'}"
            f"\tCOVERAGE={coverage}/{len(competitors)}"
        )


def bullet_metrics(keywords: list[str], competitors: list[str]) -> list[dict[str, object]]:
    competitors = [text for text in competitors if text.strip()]
    rows: list[dict[str, object]] = []
    for keyword in unique_in_order(keywords):
        counts = [phrase_count(text, keyword) for text in competitors]
        positive = [count for count in counts if count > 0]
        rows.append({
            "keyword": keyword,
            "coverage": len(positive),
            "competitors": len(competitors),
            "counts": counts,
            "median_nonzero": statistics.median(positive) if positive else 0,
            "mean_nonzero": statistics.mean(positive) if positive else 0,
            "suggested": suggestion(counts, keyword),
        })
    return rows


def run_bullet(keywords: list[str], competitors: list[str], json_output: bool = False) -> None:
    rows = bullet_metrics(keywords, competitors)
    if json_output:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    print(f"MODE=bullet\tCOMPETITORS={sum(bool(text.strip()) for text in competitors)}")
    print("KEYWORD\tCOVERAGE\tCOUNTS\tMEDIAN_NONZERO\tMEAN_NONZERO\tSUGGESTED")

    for row in rows:
        print(
            f"{row['keyword']}\t{row['coverage']}/{row['competitors']}"
            f"\t{','.join(str(count) for count in row['counts'])}"
            f"\t{format_number(float(row['median_nonzero']))}"
            f"\t{format_number(float(row['mean_nonzero']))}"
            f"\t{row['suggested']}"
        )


def parse_cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--file",
        help="read UTF-8 JSON (with or without BOM) from a file instead of stdin",
    )
    subparsers = parser.add_subparsers(dest="mode")

    coverage = subparsers.add_parser("coverage")
    coverage.add_argument("--target", default="")
    coverage.add_argument("--competitor", action="append", default=[])
    coverage.add_argument("--limit", type=int, default=200)

    bullet = subparsers.add_parser("bullet")
    bullet.add_argument("--keyword", action="append", default=[])
    bullet.add_argument("--competitor", action="append", default=[])
    bullet.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    return parser.parse_args()


def read_json_input(path: str | None) -> dict[str, object]:
    if path:
        raw = Path(path).read_text(encoding="utf-8-sig")
    else:
        raw = sys.stdin.read()
        if raw.startswith("\ufeff"):
            raw = raw[1:]
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("top-level JSON must be an object")
    return data


def main() -> None:
    file_path: str | None = None
    if len(sys.argv) > 1:
        args = parse_cli()
        if args.mode == "coverage":
            run_coverage(args.target, args.competitor, max(1, args.limit))
            return
        if args.mode == "bullet":
            run_bullet(args.keyword, args.competitor, args.json)
            return
        if args.file:
            file_path = args.file
        else:
            raise SystemExit("Choose coverage or bullet mode.")

    data = read_json_input(file_path)
    mode = data.get("mode", "coverage")
    competitors = [
        flatten_competitor(value)
        for value in (data.get("competitors", []) or [])
    ]

    if mode == "coverage":
        run_coverage(
            str(data.get("target", "") or ""),
            competitors,
            max(1, int(data.get("limit", 200))),
        )
    elif mode == "bullet":
        run_bullet(
            [str(value) for value in (data.get("keywords", []) or [])],
            competitors,
            bool(data.get("json", False)),
        )
    else:
        raise SystemExit(f"Unknown mode: {mode}")


if __name__ == "__main__":
    main()
