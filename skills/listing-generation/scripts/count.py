#!/usr/bin/env python3
"""Count characters by Unicode code point (matches Amazon's length rule).

Why this exists: `wc -m` degrades to counting BYTES when the shell locale is
not UTF-8 (e.g. LANG unset), so accented text gets over-counted. Amazon
counts code points, so this script is the single source of truth for the
Title (<=75), Item Highlights (<=125), Description (<=2000), each Bullet (10-255), and combined
Bullet 1 + 2 mobile budget (<=320) checks. The individual 145/165 values are
preferred mobile allocations, not hard limits.

Usage:
  - Pass fields as args (portable across macOS and Windows):
      python3 count.py "TITLE" "HIGHLIGHTS"
      py -3 count.py "TITLE" "HIGHLIGHTS"
  - Sum multiple fields without a separator:
      python3 count.py --sum "BULLET1" "BULLET2"
  - Stdin remains supported for callers that need it.

Output: one line per input field -> "<length>\t<text>". Blank lines are skipped.
Length is len() over the string = Unicode code points (spaces and punctuation
included; surrounding whitespace stripped, trailing newline never counted).
"""
import sys


def emit(text: str) -> None:
    s = text.strip()
    if s:
        print(f"{len(s)}\t{s}")


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "--sum":
        fields = [a.strip() for a in args[1:]]
        print(f"{sum(len(field) for field in fields)}\tSUM")
        return
    if args:
        for a in args:
            emit(a)
    else:
        for line in sys.stdin.read().splitlines():
            emit(line)


if __name__ == "__main__":
    main()
