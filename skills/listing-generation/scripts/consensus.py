#!/usr/bin/env python3
"""Resolve normalized target and competitor evidence into listing decisions.

This helper deliberately does not parse free-form listing text. The model first
normalizes each attribute, then supplies one evidence object per field. Values
for ``hierarchy`` strategy are ordered from most specific to most general, for
example ``["304 stainless steel", "stainless steel", "steel"]``.

Input schema:
{
  "mode": "target_anchored" | "consensus_only",
  "attributes": [
    {
      "field": "handle_material",
      "claim_type": "material",
      "strategy": "hierarchy" | "exact" | "set_intersection" | "min_count",
      "target": ["walnut wood", "wood"],
      "competitors": [["rosewood", "wood"], ["walnut wood", "wood"]],
      "renderings": {"wood": "Griff aus Holz"},
      "high_risk": false,
      "seller_confirmed": false,
      "core_identity": false,
      "target_conflict": false
    }
  ]
}

Target attributes win in target-anchored mode. When the target is silent, the
most specific non-conflicting common value becomes Group A. High-risk evidence
always remains Group B unless the seller explicitly confirms it.
Boolean fields accept JSON ``true``/``false`` only, and ``min_count`` accepts
finite non-negative JSON integers only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from policy_rules import ALL_HIGH_RISK_SOURCE_PATTERNS, HIGH_RISK_TYPES
from schema_validation import boolean_field, nonnegative_integer


def present(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return bool(str(value).strip())


def folded(value: object) -> str:
    return str(value or "").strip().casefold()


def as_chain(value: object) -> list[str]:
    if not present(value):
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def render_value(attribute: dict[str, object], value: object) -> str:
    renderings = attribute.get("renderings", {})
    if isinstance(renderings, dict):
        rendered = renderings.get(str(value))
        if rendered is not None and str(rendered).strip():
            return str(rendered).strip()
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def hierarchy_common(values: list[object]) -> str | None:
    chains = [as_chain(value) for value in values if present(value)]
    if not chains:
        return None
    common = {folded(item) for item in chains[0]}
    for chain in chains[1:]:
        common &= {folded(item) for item in chain}
    for item in chains[0]:
        if folded(item) in common:
            return item
    return None


def exact_common(values: list[object]) -> str | None:
    explicit = [str(value).strip() for value in values if present(value)]
    unique = {folded(value) for value in explicit}
    return explicit[0] if len(unique) == 1 else None


def set_intersection(values: list[object]) -> list[str]:
    sets: list[dict[str, str]] = []
    for value in values:
        if not present(value):
            continue
        items = as_chain(value)
        sets.append({folded(item): item for item in items})
    if not sets:
        return []
    common = set(sets[0])
    for item_map in sets[1:]:
        common &= set(item_map)
    return [item for key, item in sets[0].items() if key in common]


def minimum_count(values: list[object]) -> int | None:
    numbers: list[int] = []
    for index, value in enumerate(values):
        if not present(value):
            continue
        numbers.append(nonnegative_integer(value, f"min_count.values[{index}]"))
    return min(numbers) if numbers else None


def choose_consensus(attribute: dict[str, object], values: list[object]) -> object:
    strategy = str(attribute.get("strategy", "exact")).strip().lower()
    if strategy == "hierarchy":
        return hierarchy_common(values)
    if strategy == "exact":
        return exact_common(values)
    if strategy == "set_intersection":
        return set_intersection(values)
    if strategy == "min_count":
        return minimum_count(values)
    raise ValueError(f"unsupported consensus strategy: {strategy}")


def choose_target(attribute: dict[str, object], value: object) -> object:
    strategy = str(attribute.get("strategy", "exact")).strip().lower()
    if strategy == "hierarchy":
        chain = as_chain(value)
        return chain[0] if chain else None
    if strategy == "set_intersection":
        return as_chain(value)
    if strategy == "min_count":
        return minimum_count([value])
    if strategy == "exact":
        return str(value).strip() if present(value) else None
    raise ValueError(f"unsupported consensus strategy: {strategy}")


def richness_label(units: int) -> str:
    if units < 3:
        return "insufficient"
    if units <= 4:
        return "limited"
    if units <= 7:
        return "basic"
    if units <= 12:
        return "full"
    return "dense"


def resolve(data: dict[str, object]) -> dict[str, object]:
    mode = str(data.get("mode", "")).strip().lower()
    if mode not in {"target_anchored", "consensus_only"}:
        raise ValueError("mode must be target_anchored or consensus_only")
    attributes = data.get("attributes", [])
    if not isinstance(attributes, list):
        raise ValueError("attributes must be a list")

    confirmed: list[dict[str, object]] = []
    group_a: list[dict[str, object]] = []
    group_b: list[dict[str, object]] = []
    excluded: list[dict[str, object]] = []
    clarifications: list[dict[str, object]] = []

    for raw in attributes:
        if not isinstance(raw, dict):
            raise ValueError("every attribute must be an object")
        field = str(raw.get("field", "")).strip()
        if not field:
            raise ValueError("every attribute requires field")
        claim_type = str(raw.get("claim_type", "attribute")).strip().lower()
        core = boolean_field(raw, "core_identity", field=f"{field}.core_identity")
        if boolean_field(raw, "target_conflict", field=f"{field}.target_conflict"):
            item = {"field": field, "reason": "target attribute conflicts with itself"}
            (clarifications if core else excluded).append(item)
            continue

        target = raw.get("target")
        competitors = raw.get("competitors", [])
        if not isinstance(competitors, list):
            raise ValueError(f"{field}.competitors must be a list")
        explicit_competitors = [value for value in competitors if present(value)]

        from_target = mode == "target_anchored" and present(target)
        value = choose_target(raw, target) if from_target else choose_consensus(raw, explicit_competitors)
        if not present(value):
            item = {
                "field": field,
                "reason": "no non-conflicting common value",
                "sources": len(explicit_competitors),
                "eligible": len(competitors),
            }
            (clarifications if core else excluded).append(item)
            continue

        use_text = render_value(raw, value)
        high_risk = (
            boolean_field(raw, "high_risk", field=f"{field}.high_risk")
            or claim_type in HIGH_RISK_TYPES
            or any(
                re.search(pattern, use_text, re.IGNORECASE)
                for pattern in ALL_HIGH_RISK_SOURCE_PATTERNS
            )
        )
        seller_confirmed = boolean_field(
            raw, "seller_confirmed", field=f"{field}.seller_confirmed"
        )
        support = 1 if from_target else len(explicit_competitors)
        eligible = 1 if from_target else len(competitors)
        source_summary = (
            "目标产品明确属性" if from_target
            else f"{support}/{eligible} 个竞品来源的最具体共同值"
        )
        item = {
            "field": field,
            "value": value,
            "use_text": use_text,
            "claim_type": claim_type,
            "source": "target" if from_target else "competitor_consensus",
            "source_summary": source_summary,
            "support_count": support,
            "eligible_count": eligible,
            "core_identity": core,
        }

        if high_risk:
            item["status"] = "confirmed" if seller_confirmed else "hold"
            group_b.append(item)
        elif from_target:
            item["status"] = "confirmed"
            confirmed.append(item)
        else:
            item["status"] = "temporary_use"
            group_a.append(item)

    units = len(confirmed) + len(group_a) + sum(
        1 for item in group_b if item.get("status") == "confirmed"
    )
    return {
        "mode": mode,
        "confirmed": confirmed,
        "group_a": group_a,
        "group_b": group_b,
        "excluded": excluded,
        "clarifications": clarifications,
        "content_units": units,
        "content_richness": richness_label(units),
        "clarification_required": bool(clarifications) or units < 3,
    }


def read_input(path: str | None) -> dict[str, object]:
    raw = Path(path).read_text(encoding="utf-8") if path else sys.stdin.read()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("top-level JSON must be an object")
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", help="read UTF-8 JSON from a file")
    args = parser.parse_args()
    try:
        result = resolve(read_input(args.file))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"FAIL\tINPUT\t{error}")
        raise SystemExit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(1 if result["clarification_required"] else 0)


if __name__ == "__main__":
    main()
