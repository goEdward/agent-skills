#!/usr/bin/env python3
"""Run the complete listing audit from one compact production payload.

Preferred schema (``schema_version: 2``). Target-anchored ordinary facts may
omit fields whose defaults are shown below:
{
  "schema_version": 2,
  "marketplace": "US" | "DE",
  "mode": "target_anchored" | "consensus_only",
  "scope": "full_listing" | "title_highlights",
  "facts": [
    {
      "field": "handle_material",
      "claim": "Handle made of wood",
      "claim_zh": "木质手柄",
      "claim_type": "material",
      "strategy": "hierarchy",
      "target": ["walnut wood", "wood"],
      "competitors": [["rosewood", "wood"], ["walnut wood", "wood"]],
      "value_renderings": {"wood": "Griff aus Holz"},
      "listing_matches": {"Item Highlights": "Griff aus Holz", "Bullet 3": "Griff aus Holz"},
      "translation_matches": {"Item Highlights": "木质手柄", "Bullet 3": "木质手柄"},
      "high_risk": false,
      "seller_confirmed": false,
      "core_identity": false,
      "target_conflict": false
    }
  ],
  "risk_candidates": [{ ... claim_review.py claim without locations when held ... }],
  "pending_facts": [{"claim": "Exact wood species", "claim_zh": "准确木种",
    "confirmation_needed": "确认手柄木种", "blocked_renderings": ["supplier term"]}],
  "exclusions": [{"display": "[竞争品牌词已排除]", "term": "Rival",
    "variants": ["Rival Brand"], "reason": "竞品卖家品牌", "kind": "competitor_brand"}],
  "package_components": [{"component": "filter_basket",
    "source_singular": ["Filtersieb"], "source_plural": ["Filtersiebe"],
    "translation_terms": ["滤篮"], "present": true, "count": null,
    "listing_matches": {"Bullet 4": "Filtersieb"},
    "translation_matches": {"Bullet 4": "滤篮"}}],
  "draft": {
    "title": "source-language title",
    "highlights": "source-language highlights",
    "bullets": ["Bullet 1", "Bullet 2", "Bullet 3", "Bullet 4", "Bullet 5"],
    "description": "source-language description",
    "translations": {
      "title": "中文标题",
      "highlights": "中文商品亮点",
      "bullets": ["中文1", "中文2", "中文3", "中文4", "中文5"],
      "description": "中文描述"
    }
  },
  "competitor_bullets": [["competitor 1 bullet 1", "competitor 1 bullet 2"]],
  "keyword_candidates": [
    "51 mm",
    {"term": "DeLonghi", "eligible_locations": ["Title", "Bullet 1"]}
  ],
  "priority_keywords": [
    {"term": "bodenloser Siebträger", "variants": ["bodenlose Siebträger"],
     "eligible_locations": ["Bullet 2"]}
  ],
  "confirmation_text": "optional direct reply against unchanged claim identities/order"
}

Boolean flags accept JSON ``true``/``false`` only; quoted strings are invalid.
An explicit ``schema_version`` must be the JSON integer ``1`` or ``2``;
compact ``facts``/``draft`` payloads use version ``2``. Listing fields,
translations, location-match values, competitor Bullets, and keyword forms
must use their documented JSON string types and are never string-coerced.
Strategy value types: ``exact`` uses one scalar value (a one-item list is
accepted); ``hierarchy`` uses a specific-to-general list; ``set_intersection``
uses component or compatibility lists; ``min_count`` uses finite non-negative
JSON integers.
Facts default to ``claim_type=attribute``, ``strategy=exact``, empty competitor
evidence, and false boolean flags. A target-anchored fact with neither
``target`` nor non-empty ``competitors`` uses its ``claim`` as target evidence;
an explicitly empty ``competitors`` list is accepted as the same default.
Every compact fact requires field-aligned ``listing_matches`` and
``translation_matches`` plus a concise Simplified Chinese ``claim_zh`` for the
seller confirmation block. A used risk candidate requires the same two maps;
held candidates may omit them. Every pending fact requires ``claim_zh``.
For regeneration, resolve seller replies against their original confirmation
table and update existing facts, confirmation flags, copy, translations, and
maps before calling the pipeline; do not replay old IDs in a rebuilt payload.
Top-level ``package_components`` is mandatory; use ``[]`` only when no package
component appears.
``scope`` defaults to ``full_listing``. ``title_highlights`` accepts and renders
only Title, Item Highlights, and their Chinese translations; omit
``keyword_candidates`` and pass at most two prefiltered ``priority_keywords``
with explicit Title/Item Highlights locations.
In ``full_listing``, a user priority keyword may use Item Highlights as its
sole placement when ``eligible_locations`` includes Item Highlights and the
field naturally contains the term within Amazon's 125-character hard limit.
``product_highlights`` is accepted as an alias for ``highlights`` in both the
draft and its translations.
Keyword variants are alternative forms: overlapping variants count once at a
given token span, and phrases never count across Bullet or field boundaries.
`required_locations` is optional and requires a selected phrase or declared
variant to occur in each listed field; it is checked with punctuation-aware
visible phrase matching.

The compact schema stores competitor Bullet text once, derives consensus,
claim IDs, display locations, blocked terms, keyword benchmarks, character
counts, and field claim maps in memory, then runs final validation. The legacy
``evidence`` + ``claim_review`` + ``final`` schema remains supported.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

import claim_review
import consensus
import kwfreq
import validate_final
from schema_validation import boolean_field, nonnegative_integer


USED_STATUSES = {"temporary_use", "adjusted_use", "confirmed_use"}
REQUIRED_FIELDS = [
    "Title", "Product Highlights", "Bullet 1", "Bullet 2", "Bullet 3",
    "Bullet 4", "Bullet 5", "Description",
]
SUPPORTED_STRATEGIES = {"hierarchy", "exact", "set_intersection", "min_count"}
SUPPORTED_SCOPES = {"full_listing", "title_highlights"}
MEASUREMENT_LIMITS = {
    "Title": 75,
    "Product Highlights": 125,
    "Bullet 1": 255,
    "Bullet 2": 255,
    "Bullet 3": 255,
    "Bullet 4": 255,
    "Bullet 5": 255,
    "Bullet 1 + 2": 320,
    "Description": validate_final.DESCRIPTION_LIMIT,
}
MOBILE_PREFERRED_LIMITS = {"Bullet 1": 145, "Bullet 2": 165}


class StructuredPipelineFailure(ValueError):
    """An expected input/policy failure that the API can return explicitly."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def structured_failure_result(
    data: dict[str, object], *, schema_version: int, code: str, message: str,
) -> dict[str, object]:
    """Return a blocked result for expected compact-input policy failures."""
    marketplace = str(data.get("marketplace", "")).strip().upper()
    scope = str(data.get("scope", "full_listing")).strip().lower() or "full_listing"
    if scope not in SUPPORTED_SCOPES:
        scope = "full_listing"
    mode = str(data.get("mode", "")).strip().lower() or "unknown"
    final = {
        "marketplace": marketplace,
        "scope": scope,
        "mode": mode,
        "keywords": [],
        "package_components": [],
        "claim_audit": [],
        "field_claim_ids": {},
    }
    validation = [{
        "level": "FAIL", "code": code, "message": message,
        "location": "", "claim_id": "",
    }]
    claim_review_result = {
        "marketplace": marketplace,
        "a_claims": [], "b_claims": [],
        "automatic_exclusions": [], "pending_facts": [],
        "blocking_clarifications": [], "validator_claim_audit": [],
        "validator_blocked_terms": [],
    }
    return {
        "schema_version": schema_version,
        "evidence": {
            "mode": mode,
            "clarifications": [],
            "clarification_required": False,
            "input_failure": {"code": code, "message": message},
        },
        "claim_review": claim_review_result,
        "confirmation_markdown": "未生成事实确认表：输入在流水线前置校验阶段被阻断。",
        "keyword_benchmark": [],
        "measurements": {},
        "final_payload": final,
        "validation": validation,
        "hard_pass": False,
        "clarification_required": False,
    }


def required_fields(scope: str) -> list[str]:
    return (
        ["Title", "Product Highlights"]
        if scope == "title_highlights"
        else list(REQUIRED_FIELDS)
    )


def derive_field_claim_ids(
    audit: list[dict[str, object]], scope: str = "full_listing"
) -> dict[str, list[str]]:
    result = {field: [] for field in required_fields(scope)}
    for item in audit:
        if str(item.get("status", "")) not in USED_STATUSES:
            continue
        claim_id = str(item.get("id", "")).strip()
        locations = item.get("locations", {})
        if not isinstance(locations, dict):
            continue
        for raw_location in locations:
            location = validate_final.resolve_location(str(raw_location))
            if location in result and claim_id and claim_id not in result[location]:
                result[location].append(claim_id)
    return result


def dedupe_blocked(items: list[object]) -> list[object]:
    seen: set[tuple[str, str]] = set()
    result: list[object] = []
    for item in items:
        if isinstance(item, str):
            key = (item.casefold(), "phrase")
        elif isinstance(item, dict):
            key = (
                str(item.get("term", "")).strip().casefold(),
                str(item.get("match", "phrase")).strip().lower(),
            )
        else:
            continue
        if key[0] and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def measurements(final: dict[str, object]) -> dict[str, int]:
    raw_bullets = final.get("bullets", [])
    bullets = (
        [value.strip() for value in raw_bullets]
        if isinstance(raw_bullets, list)
        and all(isinstance(value, str) for value in raw_bullets)
        else []
    )
    result = {
        "Title": len(str(final.get("title", "")).strip()),
        "Product Highlights": len(str(final.get("highlights", "")).strip()),
    }
    if str(final.get("scope", "full_listing")) == "title_highlights":
        return result
    for index in range(5):
        result[f"Bullet {index + 1}"] = len(bullets[index]) if index < len(bullets) else 0
    result["Bullet 1 + 2"] = sum(result[f"Bullet {index}"] for index in (1, 2))
    result["Description"] = len(str(final.get("description", "")).strip())
    return result


def normalize_competitor_bullets(value: object, field: str) -> list[list[str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    result: list[list[str]] = []
    for listing_index, listing in enumerate(value):
        if not isinstance(listing, list):
            raise ValueError(f"{field} entries must be lists of Bullet strings; titles are not accepted")
        if any(not isinstance(bullet, str) for bullet in listing):
            raise ValueError(
                f"{field}[{listing_index}] must contain only Bullet strings"
            )
        result.append(list(listing))
    return result


def benchmark(keywords: object, competitors: list[list[str]]) -> list[dict[str, object]]:
    if keywords is None:
        return []
    if not isinstance(keywords, list):
        raise ValueError("keyword candidates must be a list")
    flattened = [kwfreq.flatten_competitor(value) for value in competitors]
    return kwfreq.bullet_metrics([str(value) for value in keywords], flattened)


def first_listing_match(matches: dict[str, str], fallback: str) -> str:
    return next((value for value in matches.values() if value), fallback)


def normalize_highlights_alias(container: dict[str, object], field: str) -> None:
    alias = "product_highlights"
    if alias not in container:
        return
    if "highlights" in container and container["highlights"] != container[alias]:
        raise ValueError(f"{field}.highlights conflicts with {field}.{alias}")
    container.setdefault("highlights", container[alias])
    container.pop(alias, None)


def normalize_draft(draft: dict[str, object]) -> dict[str, object]:
    final = copy.deepcopy(draft)
    normalize_highlights_alias(final, "draft")
    translations = final.get("translations")
    if isinstance(translations, dict):
        normalize_highlights_alias(translations, "draft.translations")
    return final


def validate_final_field_types(final: dict[str, object]) -> None:
    for key in ("title", "highlights", "description"):
        if key in final and not isinstance(final[key], str):
            raise ValueError(f"draft.{key} must be a string")
    if "bullets" in final:
        bullets = final["bullets"]
        if not isinstance(bullets, list) or any(
            not isinstance(value, str) for value in bullets
        ):
            raise ValueError("draft.bullets must be a list of strings")
    if "translations" not in final:
        return
    translations = final["translations"]
    if not isinstance(translations, dict):
        raise ValueError("draft.translations must be an object")
    for key in ("title", "highlights", "description"):
        if key in translations and not isinstance(translations[key], str):
            raise ValueError(f"draft.translations.{key} must be a string")
    if "bullets" in translations:
        bullets = translations["bullets"]
        if not isinstance(bullets, list) or any(
            not isinstance(value, str) for value in bullets
        ):
            raise ValueError("draft.translations.bullets must be a list of strings")


def validate_required_translations(final: dict[str, object], scope: str) -> None:
    """Require non-empty Chinese copy for every selected listing field."""
    translations = final.get("translations")
    if not isinstance(translations, dict):
        raise ValueError("draft.translations must be an object")
    required = ["title", "highlights"]
    if scope == "full_listing":
        required.extend(["bullets", "description"])
    for key in required:
        value = translations.get(key)
        if key == "bullets":
            if not isinstance(value, list) or len(value) != 5 or any(
                not isinstance(item, str) or not item.strip() for item in value
            ):
                raise ValueError(
                    "draft.translations.bullets must contain five non-empty Chinese translations"
                )
        elif not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"draft.translations.{key} must be a non-empty Chinese translation"
            )


def normalize_location_match_map(
    value: object,
    field: str,
    active_fields: set[str],
    *,
    required: bool,
) -> dict[str, str]:
    if not isinstance(value, dict):
        if required:
            raise ValueError(f"{field} must be a non-empty object")
        return {}
    result: dict[str, str] = {}
    for raw_location, raw_text in value.items():
        if not isinstance(raw_location, str) or not isinstance(raw_text, str):
            raise ValueError(f"{field} keys and values must be strings")
        text = raw_text.strip()
        if not raw_location.strip() or not text:
            continue
        location = validate_final.resolve_location(raw_location)
        if not location:
            raise ValueError(f"{field} has unknown location {raw_location!r}")
        if location not in active_fields:
            raise ValueError(f"{field} location {location!r} is outside scope")
        if location in result and result[location] != text:
            raise ValueError(f"{field} has conflicting aliases for {location}")
        result[location] = text
    if required and not result:
        raise ValueError(f"{field} has no usable entries")
    return result


def numeric_value(value: object, field: str) -> object:
    return nonnegative_integer(value, field)


def exact_value(value: object, field: str) -> object:
    if isinstance(value, list):
        usable = [item for item in value if consensus.present(item)]
        if len(usable) <= 1:
            return usable[0] if usable else None
        raise ValueError(
            f"{field}: exact requires one scalar value; use hierarchy or "
            "set_intersection for multi-value lists"
        )
    if isinstance(value, (tuple, set, dict)):
        raise ValueError(f"{field}: exact requires one scalar value")
    return value


def normalize_strategy_values(
    field: str,
    strategy: str,
    target: object,
    competitors: object,
) -> tuple[object, list[object]]:
    if strategy not in SUPPORTED_STRATEGIES:
        raise ValueError(f"{field}.strategy is unsupported: {strategy}")
    if not isinstance(competitors, list):
        raise ValueError(f"{field}.competitors must be a list")

    normalized_competitors: list[object] = list(competitors)
    if strategy == "exact":
        target = exact_value(target, f"{field}.target")
        normalized_competitors = [
            exact_value(value, f"{field}.competitors[{index}]")
            for index, value in enumerate(competitors)
        ]
    elif strategy == "set_intersection":
        if consensus.present(target) and not isinstance(target, list):
            target = [target]
        normalized_competitors = [
            value if isinstance(value, list) or not consensus.present(value) else [value]
            for value in competitors
        ]
    elif strategy == "min_count":
        if consensus.present(target):
            target = numeric_value(target, f"{field}.target")
        normalized_competitors = [
            numeric_value(value, f"{field}.competitors[{index}]")
            if consensus.present(value) else value
            for index, value in enumerate(competitors)
        ]
    return target, normalized_competitors


def keyword_target_is_nonzero(value: object) -> bool:
    text = str(value).strip()
    if not text:
        return False
    upper = text.split("-", 1)[-1]
    try:
        return int(upper) > 0
    except ValueError:
        return True


def keyword_phrases(item: dict[str, object]) -> list[str]:
    values = [str(item.get("phrase", item.get("term", ""))).strip()]
    variants = item.get("variants", [])
    if isinstance(variants, list):
        values.extend(str(value).strip() for value in variants)
    result: list[str] = []
    seen: set[tuple[str, ...]] = set()
    for value in values:
        key = tuple(kwfreq.tokens(value))
        if value and key and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def keyword_count(text: str, item: dict[str, object]) -> int:
    return kwfreq.alternative_phrase_count(text, keyword_phrases(item))


def merge_keyword_constraints(destination: dict[str, object], source: dict[str, object]) -> None:
    """Keep accepted forms and explicit placements when the same term is merged."""
    plans: list[list[str]] = []
    for raw in (destination.get("eligible_locations"), source.get("eligible_locations")):
        if raw is None:
            continue
        if not isinstance(raw, list) or not raw:
            raise ValueError("keyword eligible_locations must be a non-empty list")
        if any(not isinstance(value, str) for value in raw):
            raise ValueError("keyword eligible_locations must contain strings")
        locations = [validate_final.resolve_location(value) for value in raw]
        if None in locations:
            raise ValueError("keyword eligible_locations contains an unknown location")
        plans.append(list(dict.fromkeys(locations)))
    if plans:
        eligible = plans[0]
        if len(plans) == 2:
            allowed = set(plans[1])
            eligible = [location for location in eligible if location in allowed]
        if not eligible:
            raise ValueError("keyword has conflicting eligible_locations")
        destination["eligible_locations"] = eligible
    for field in ("variants", "required_locations"):
        left, right = destination.get(field, []), source.get(field, [])
        if not isinstance(left, list) or not isinstance(right, list):
            raise ValueError(f"keyword {field} must be a list")
        if any(not isinstance(value, str) for value in [*left, *right]):
            raise ValueError(f"keyword {field} must contain strings")
        merged = list(dict.fromkeys([*left, *right]))
        if merged:
            destination[field] = merged


def normalize_keyword_specs(value: object, field: str) -> list[dict[str, object]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    result: list[dict[str, object]] = []
    seen: dict[tuple[str, ...], dict[str, object]] = {}
    for index, raw in enumerate(value):
        if isinstance(raw, str):
            spec: dict[str, object] = {"term": raw.strip()}
        elif isinstance(raw, dict):
            spec = copy.deepcopy(raw)
            raw_term = raw.get("term", raw.get("phrase", ""))
            if not isinstance(raw_term, str):
                raise ValueError(f"{field}[{index}].term must be a string")
            spec["term"] = raw_term.strip()
            spec.pop("phrase", None)
        else:
            raise ValueError(f"{field}[{index}] must be a string or object")
        term = str(spec.get("term", "")).strip()
        if not term:
            raise ValueError(f"{field}[{index}].term must not be blank")
        key = tuple(kwfreq.tokens(term))
        if not key:
            continue
        locations = spec.get("eligible_locations")
        if locations is not None:
            if not isinstance(locations, list) or not locations:
                raise ValueError(f"{field}[{index}].eligible_locations must be a non-empty list")
            if any(not isinstance(item, str) for item in locations):
                raise ValueError(f"{field}[{index}].eligible_locations must contain strings")
            spec["eligible_locations"] = [item.strip() for item in locations if item.strip()]
        variants = spec.get("variants", [])
        if isinstance(variants, str):
            variants = [variants]
        if not isinstance(variants, list):
            raise ValueError(f"{field}[{index}].variants must be a list or string")
        if any(not isinstance(item, str) for item in variants):
            raise ValueError(f"{field}[{index}].variants must contain strings")
        spec["variants"] = [
            item.strip() for item in variants
            if item.strip() and tuple(kwfreq.tokens(item)) != key
        ]
        required = spec.get("required_locations")
        if required is not None:
            if not isinstance(required, list) or not required:
                raise ValueError(f"{field}[{index}].required_locations must be a non-empty list")
            if any(not isinstance(item, str) or not item.strip() for item in required):
                raise ValueError(f"{field}[{index}].required_locations must contain non-empty strings")
            spec["required_locations"] = [item.strip() for item in required]
        spec["term"] = term
        if key in seen:
            merge_keyword_constraints(seen[key], spec)
            continue
        seen[key] = spec
        result.append(spec)
    return result


def compact_parts(data: dict[str, object]) -> tuple[
    dict[str, object], dict[str, object], dict[str, object], list[list[str]],
    list[dict[str, object]], list[dict[str, object]],
]:
    if not isinstance(data.get("marketplace"), str):
        raise ValueError("compact payload marketplace must be a string")
    if not isinstance(data.get("mode"), str):
        raise ValueError("compact payload mode must be a string")
    if "scope" in data and not isinstance(data["scope"], str):
        raise ValueError("compact payload scope must be a string")
    marketplace = data["marketplace"].strip().upper()
    mode = data["mode"].strip().lower()
    scope = data.get("scope", "full_listing").strip().lower() or "full_listing"
    if marketplace not in {"US", "DE"}:
        raise ValueError("compact payload marketplace must be US or DE")
    if mode not in {"target_anchored", "consensus_only"}:
        raise ValueError("compact payload mode must be target_anchored or consensus_only")
    if scope not in SUPPORTED_SCOPES:
        raise ValueError("compact payload scope must be full_listing or title_highlights")
    active_fields = set(required_fields(scope))

    facts = data.get("facts", [])
    if not isinstance(facts, list) or not facts:
        raise ValueError("compact payload facts must be a non-empty list")
    attributes: list[dict[str, object]] = []
    fact_by_field: dict[str, dict[str, object]] = {}
    for raw in facts:
        if not isinstance(raw, dict):
            raise ValueError("every compact fact must be an object")
        if not isinstance(raw.get("field"), str):
            raise ValueError("every compact fact field must be a string")
        field = raw["field"].strip()
        if not field:
            raise ValueError("every compact fact requires field")
        if not isinstance(raw.get("claim"), str) or not raw["claim"].strip():
            raise ValueError(f"{field}.claim must not be blank")
        if not isinstance(raw.get("claim_zh"), str) or not raw["claim_zh"].strip():
            raise ValueError(f"{field}.claim_zh must not be blank")
        if field in fact_by_field:
            raise ValueError(f"duplicate compact fact field: {field}")
        matches = normalize_location_match_map(
            raw.get("listing_matches"),
            f"{field}.listing_matches",
            active_fields,
            required=True,
        )
        translation_matches = normalize_location_match_map(
            raw.get("translation_matches"),
            f"{field}.translation_matches",
            active_fields,
            required=True,
        )
        if set(translation_matches) != set(matches):
            raise ValueError(
                f"{field}.translation_matches must cover exactly the listing_matches locations"
            )
        renderings = raw.get("value_renderings", {})
        if not isinstance(renderings, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in renderings.items()
        ):
            raise ValueError(
                f"{field}.value_renderings must be an object with string keys and values"
            )
        if "strategy" in raw and not isinstance(raw["strategy"], str):
            raise ValueError(f"{field}.strategy must be a string")
        if "claim_type" in raw and not isinstance(raw["claim_type"], str):
            raise ValueError(f"{field}.claim_type must be a string")
        strategy = raw.get("strategy", "exact").strip().lower() or "exact"
        target = raw.get("target")
        # An explicitly empty competitor list is equivalent to omitted
        # competitor evidence for a target-anchored fact. Keep compact
        # payload defaults useful without requiring callers to omit the key.
        if "target" not in raw and mode == "target_anchored" and not raw.get("competitors"):
            target = raw.get("claim")
        target, competitors = normalize_strategy_values(
            field, strategy, target, raw.get("competitors", [])
        )
        flags = {
            name: boolean_field(raw, name, field=f"{field}.{name}")
            for name in (
                "high_risk", "seller_confirmed", "core_identity", "target_conflict"
            )
        }
        fact = copy.deepcopy(raw)
        fact["listing_matches"] = matches
        fact["translation_matches"] = translation_matches
        fact.update(flags)
        fact_by_field[field] = fact
        attributes.append({
            "field": field,
            "claim_type": str(raw.get("claim_type", "attribute")),
            "strategy": strategy,
            "target": copy.deepcopy(target),
            "competitors": copy.deepcopy(competitors),
            "renderings": copy.deepcopy(renderings),
            "high_risk": flags["high_risk"],
            "seller_confirmed": flags["seller_confirmed"],
            "core_identity": flags["core_identity"],
            "target_conflict": flags["target_conflict"],
        })

    evidence = {"mode": mode, "attributes": attributes}
    evidence_result = consensus.resolve(evidence)
    decisions: dict[str, tuple[str, dict[str, object]]] = {}
    for bucket in ("confirmed", "group_a", "group_b"):
        for item in evidence_result[bucket]:
            decisions[str(item["field"])] = (bucket, item)

    claims: list[dict[str, object]] = []
    unresolved_fact_fields: list[str] = []
    unresolved_locations: set[str] = set()
    for field, raw in fact_by_field.items():
        decision_entry = decisions.get(field)
        if decision_entry is None:
            core_target_conflict = (
                boolean_field(raw, "core_identity", field=f"{field}.core_identity")
                and boolean_field(raw, "target_conflict", field=f"{field}.target_conflict")
            )
            if mode == "consensus_only" or core_target_conflict:
                if mode == "consensus_only":
                    unresolved_fact_fields.append(field)
                    unresolved_locations.update(
                        resolved
                        for location in raw["listing_matches"]
                        for resolved in [validate_final.resolve_location(str(location))]
                        if resolved
                    )
                if not any(
                    isinstance(item, dict) and str(item.get("field", "")) == field
                    for item in evidence_result.get("clarifications", [])
                ):
                    evidence_result.setdefault("clarifications", []).append({
                        "field": field,
                        "reason": (
                            "target attribute conflicts with itself"
                            if core_target_conflict
                            else "compact fact has no eligible consensus decision"
                        ),
                    })
                continue
            raise ValueError(f"used compact fact {field!r} has no eligible consensus decision")
        bucket, decision = decision_entry
        matches = dict(raw["listing_matches"])
        if bucket == "group_b" and str(decision.get("status", "")) != "confirmed":
            raise StructuredPipelineFailure(
                "RISK_MAPPING",
                f"held Group B fact {field!r} cannot have listing_matches; "
                "confirm the claim before mapping it into final copy",
            )
        fallback = str(decision.get("use_text", raw.get("claim", field))).strip()
        use_text = first_listing_match(matches, fallback)
        claims.append({
            "claim": str(raw.get("claim", fallback)).strip() or fallback,
            "claim_zh": str(raw.get("claim_zh", "")).strip(),
            "claim_type": str(raw.get("claim_type", decision.get("claim_type", "attribute"))),
            "high_risk": raw["high_risk"] or bucket == "group_b",
            "use_text": use_text,
            "match_text": use_text,
            "locations": list(matches),
            "location_matches": matches,
            "translation_location_matches": dict(raw["translation_matches"]),
            "source": str(decision.get("source", "")),
            "source_summary": str(decision.get("source_summary", "")),
            "support_count": int(decision.get("support_count", 0) or 0),
            "eligible_count": int(decision.get("eligible_count", 0) or 0),
            "seller_confirmed": boolean_field(
                raw, "seller_confirmed", field=f"{field}.seller_confirmed"
            ),
            "core_identity": boolean_field(
                raw, "core_identity", field=f"{field}.core_identity"
            ),
            "block_term": str(raw.get("block_term", "")).strip(),
            "block_match": str(raw.get("block_match", "substring")).strip(),
            "evidence_needed": str(raw.get("evidence_needed", "")).strip(),
        })

    risk_candidates = data.get("risk_candidates", [])
    if not isinstance(risk_candidates, list):
        raise ValueError("risk_candidates must be a list")
    for raw in risk_candidates:
        if not isinstance(raw, dict):
            raise ValueError("every risk candidate must be an object")
        if not isinstance(raw.get("claim"), str) or not raw["claim"].strip():
            raise ValueError("every risk candidate requires claim")
        if not isinstance(raw.get("claim_zh"), str) or not raw["claim_zh"].strip():
            raise ValueError("every risk candidate requires claim_zh")
        claim = copy.deepcopy(raw)
        matches_raw = claim.pop("listing_matches", claim.get("location_matches", {}))
        matches = normalize_location_match_map(
            matches_raw,
            "risk_candidate.listing_matches",
            active_fields,
            required=False,
        )
        translation_matches_raw = claim.pop(
            "translation_matches",
            claim.get("translation_location_matches", {}),
        )
        translation_matches = normalize_location_match_map(
            translation_matches_raw,
            "risk_candidate.translation_matches",
            active_fields,
            required=bool(matches),
        )
        if set(translation_matches) != set(matches):
            raise ValueError(
                "risk_candidate.translation_matches must cover exactly the listing_matches locations"
            )
        fallback = str(claim.get("use_text", claim.get("claim", ""))).strip()
        claim["use_text"] = first_listing_match(matches, fallback)
        claim["match_text"] = str(claim.get("match_text", "")).strip() or claim["use_text"]
        claim["locations"] = list(matches)
        claim["location_matches"] = matches
        claim["translation_location_matches"] = translation_matches
        claims.append(claim)

    exclusions = data.get("exclusions", [])
    if not isinstance(exclusions, list):
        raise ValueError("exclusions must be a list")
    automatic_exclusions: list[dict[str, object]] = []
    derived_competitor_brands: list[str] = []
    for raw in exclusions:
        if not isinstance(raw, dict):
            raise ValueError("every exclusion must be an object")
        automatic_exclusions.append(copy.deepcopy(raw))
        if str(raw.get("kind", "")).strip() == "competitor_brand":
            variants = raw.get("variants", [])
            if isinstance(variants, str):
                variants = [variants]
            if not isinstance(variants, list):
                raise ValueError("competitor-brand exclusion variants must be a list or string")
            for value in [raw.get("term", ""), *variants]:
                term = str(value).strip()
                if term and term not in derived_competitor_brands:
                    derived_competitor_brands.append(term)

    pending_facts = data.get("pending_facts", [])
    if not isinstance(pending_facts, list):
        raise ValueError("pending_facts must be a list")
    for index, raw in enumerate(pending_facts, start=1):
        if not isinstance(raw, dict):
            raise ValueError("every pending fact must be an object")
        if not isinstance(raw.get("claim"), str) or not raw["claim"].strip():
            raise ValueError(f"pending_facts[{index}].claim must not be blank")
        if not isinstance(raw.get("claim_zh"), str) or not raw["claim_zh"].strip():
            raise ValueError(f"pending_facts[{index}].claim_zh must not be blank")

    claims_input: dict[str, object] = {
        "marketplace": marketplace,
        "claims": claims,
        "automatic_exclusions": automatic_exclusions,
        "pending_facts": copy.deepcopy(pending_facts),
    }
    if data.get("confirmation") is not None:
        claims_input["confirmation"] = copy.deepcopy(data["confirmation"])
    if str(data.get("confirmation_text", "")).strip():
        claims_input["confirmation_text"] = str(data["confirmation_text"])

    draft = data.get("draft")
    if not isinstance(draft, dict):
        raise ValueError("compact payload draft must be an object")
    final = normalize_draft(draft)
    validate_final_field_types(final)
    validate_required_translations(final, scope)
    if scope == "title_highlights":
        if "bullets" in final:
            raise ValueError("title_highlights draft must omit Bullets")
        if "description" in final:
            raise ValueError("title_highlights draft must omit Description")
        translations = final.get("translations", {})
        if isinstance(translations, dict):
            if "bullets" in translations:
                raise ValueError("title_highlights draft translations must omit Bullets")
            if "description" in translations:
                raise ValueError("title_highlights draft translations must omit Description")
    if "package_components" not in data:
        raise ValueError("compact payload requires explicit package_components, use [] when none")
    if not isinstance(data["package_components"], list):
        raise ValueError("package_components must be a list")
    if "package_components" in final and final["package_components"] != data["package_components"]:
        raise ValueError("draft.package_components conflicts with top-level package_components")
    final["package_components"] = copy.deepcopy(data["package_components"])
    final["marketplace"] = marketplace
    final["scope"] = scope
    competitor_bullets = normalize_competitor_bullets(
        data.get("competitor_bullets", draft.get("competitor_bullets")),
        "competitor_bullets",
    )
    final.pop("competitor_bullets", None)
    final["competitors"] = [kwfreq.flatten_competitor(value) for value in competitor_bullets]
    raw_brands = final.get("competitor_brands", [])
    if not isinstance(raw_brands, list):
        raise ValueError("draft.competitor_brands must be a list")
    brands: list[str] = []
    for index, value in enumerate(raw_brands):
        if not isinstance(value, str):
            raise ValueError(f"draft.competitor_brands[{index}] must be a string")
        if value.strip():
            brands.append(value.strip())
    for brand in derived_competitor_brands:
        if brand not in brands:
            brands.append(brand)
    final["competitor_brands"] = brands
    if competitor_bullets and not brands and not str(final.get("competitor_brand_audit_reason", "")).strip():
        final["competitor_brand_audit_reason"] = "未识别到明确竞品卖家品牌"

    keyword_candidates = normalize_keyword_specs(
        data.get("keyword_candidates", draft.get("keyword_candidates", [])),
        "keyword_candidates",
    )
    priority_keywords = normalize_keyword_specs(
        data.get("priority_keywords", draft.get("priority_keywords", [])),
        "priority_keywords",
    )
    if scope == "title_highlights":
        if competitor_bullets:
            raise ValueError(
                "title_highlights must omit competitor_bullets; do not benchmark legacy or competitor Bullets in this scope"
            )
        if keyword_candidates:
            raise ValueError(
                "title_highlights must omit keyword_candidates; prefilter and use priority_keywords"
            )
        if len(priority_keywords) > 2:
            raise ValueError("title_highlights accepts at most two priority_keywords")
        for spec in priority_keywords:
            locations = spec.get("eligible_locations", [])
            if not locations:
                raise ValueError(
                    "title_highlights priority_keywords require explicit eligible_locations"
                )
            resolved = {
                validate_final.resolve_location(str(value)) for value in locations
            }
            if None in resolved or not resolved <= active_fields:
                raise ValueError(
                    "title_highlights priority keyword locations must be Title and/or Item Highlights"
                )
            required_locations = spec.get("required_locations", [])
            if required_locations:
                resolved_required = {
                    validate_final.resolve_location(str(value))
                    for value in required_locations
                }
                if None in resolved_required or not resolved_required <= active_fields:
                    raise ValueError(
                        "title_highlights priority keyword required_locations must be Title and/or Item Highlights"
                    )
    final.pop("keyword_candidates", None)
    final.pop("priority_keywords", None)
    if competitor_bullets and not keyword_candidates and not str(final.get("keyword_audit_reason", "")).strip():
        final["keyword_audit_reason"] = "没有通过事实与语言校验的高价值Bullet关键词"
    pending_blocked: list[dict[str, str]] = []
    for pending in data.get("pending_facts", []):
        if not isinstance(pending, dict):
            continue
        renderings = pending.get("blocked_renderings", [])
        if isinstance(renderings, str):
            renderings = [renderings]
        if not isinstance(renderings, list):
            raise ValueError("pending_facts.blocked_renderings must be a list or string")
        for rendering in renderings:
            term = str(rendering).strip()
            if term:
                pending_blocked.append({
                    "term": term,
                    "match": "phrase",
                    "reason": "待确认普通信息不得进入最终Listing",
                })
    final["pending_blocked_terms"] = pending_blocked
    if unresolved_fact_fields:
        evidence_result["unresolved_fact_fields"] = unresolved_fact_fields
        evidence_result["unresolved_locations"] = sorted(unresolved_locations)
    return (
        evidence_result, claims_input, final, competitor_bullets,
        keyword_candidates, priority_keywords,
    )


def legacy_parts(data: dict[str, object]) -> tuple[
    dict[str, object] | None, dict[str, object], dict[str, object], list[list[str]],
    list[dict[str, object]], list[dict[str, object]],
]:
    evidence = data.get("evidence")
    if evidence is not None and not isinstance(evidence, dict):
        raise ValueError("evidence must be an object")
    claims_input = data.get("claim_review")
    if not isinstance(claims_input, dict):
        raise ValueError("claim_review must be an object")
    final_input = data.get("final")
    if not isinstance(final_input, dict):
        raise ValueError("final must be an object")
    final = copy.deepcopy(final_input)
    scope = str(final.get("scope", "full_listing")).strip().lower() or "full_listing"
    if scope not in SUPPORTED_SCOPES:
        raise ValueError("legacy final scope must be full_listing or title_highlights")
    final["scope"] = scope
    final["mode"] = (
        str(evidence.get("mode", "legacy")).strip().lower()
        if isinstance(evidence, dict)
        else "legacy"
    )
    validate_final_field_types(final)
    validate_required_translations(final, scope)
    benchmark_input = data.get("keyword_benchmark")
    if benchmark_input is None:
        competitor_bullets = normalize_competitor_bullets(final.get("competitors", []), "final.competitors") if final.get("competitors") else []
        keywords: list[dict[str, object]] = []
    else:
        if not isinstance(benchmark_input, dict):
            raise ValueError("keyword_benchmark must be an object")
        competitor_bullets = normalize_competitor_bullets(benchmark_input.get("competitors", []), "keyword_benchmark.competitors")
        keywords = normalize_keyword_specs(benchmark_input.get("keywords", []), "keyword_benchmark.keywords")
    priority_keywords = normalize_keyword_specs(data.get("priority_keywords", []), "priority_keywords")
    return evidence, copy.deepcopy(claims_input), final, competitor_bullets, keywords, priority_keywords


def suppress_consensus_cascades(
    results: list[dict[str, object]],
    evidence_result: dict[str, object],
    claim_result: dict[str, object],
) -> list[dict[str, object]]:
    """Keep the consensus root failure while removing dependent audit noise."""
    unresolved = evidence_result.get("unresolved_fact_fields", [])
    if not isinstance(unresolved, list) or not unresolved:
        return results
    locations = evidence_result.get("unresolved_locations", [])
    unresolved_locations = set(locations) if isinstance(locations, list) else set()
    filtered: list[dict[str, object]] = []
    for item in results:
        code = str(item.get("code", ""))
        message = str(item.get("message", ""))
        location = str(item.get("location", ""))
        if (
            code == "CLAIM_SCHEMA"
            and message == "claim_audit must be a non-empty list"
            and not claim_result.get("validator_claim_audit")
        ):
            continue
        if (
            code == "CLAIM_COVERAGE"
            and location in unresolved_locations
            and "has no mapped claim IDs" in message
        ):
            continue
        filtered.append(item)
    if not any(item.get("code") == "CONSENSUS_DECISION" for item in filtered):
        fields = ", ".join(str(value) for value in unresolved)
        filtered.append({
            "level": "FAIL",
            "code": "CONSENSUS_DECISION",
            "message": (
                "consensus_only has no eligible consensus decision for: "
                f"{fields}"
            ),
            "location": "",
            "claim_id": "",
        })
    return filtered


def unique_publication_claims(claims: list[dict[str, object]]) -> list[dict[str, object]]:
    """Count repeated scoped facts once without merging their audit records.

    Ignore field placement and formatting, but retain bilingual claim wording
    and complete rendering values so different components/scopes stay distinct.
    This is exact-record deduplication, not semantic paraphrase matching.
    """
    def normalized(value: object) -> str:
        return " ".join(str(value).split()).casefold()

    seen: set[tuple[object, ...]] = set()
    unique: list[dict[str, object]] = []
    # When an ordinary fact has both sources, retain its target attribution.
    for claim in sorted(claims, key=lambda item: item.get("source") != "target"):
        key = (
            normalized(claim.get("claim", "")),
            normalized(claim.get("claim_zh", "")),
            frozenset(normalized(value) for value in claim.get("location_matches", {}).values()),
            frozenset(normalized(value) for value in claim.get("translation_location_matches", {}).values()),
        )
        if key not in seen:
            seen.add(key)
            unique.append(claim)
    return unique


def run(data: dict[str, object]) -> dict[str, object]:
    has_compact_fields = "facts" in data or "draft" in data
    if "schema_version" in data:
        version = data["schema_version"]
        if type(version) is not int or version not in {1, 2}:
            raise ValueError("schema_version must be the JSON integer 1 or 2")
        if version == 1 and has_compact_fields:
            raise ValueError("schema_version 1 conflicts with compact facts/draft fields")
        compact = version == 2
    else:
        compact = "facts" in data and "draft" in data
    if compact:
        try:
            evidence_result, claims_input, final, competitor_bullets, keyword_specs, priority_specs = compact_parts(data)
        except StructuredPipelineFailure as error:
            return structured_failure_result(
                data,
                schema_version=2,
                code=error.code,
                message=str(error),
            )
    else:
        evidence, claims_input, final, competitor_bullets, keyword_specs, priority_specs = legacy_parts(data)
        evidence_result = consensus.resolve(evidence) if evidence is not None else None

    claim_result = claim_review.classify(claims_input)
    if compact:
        # Deduplicate publication counts only; every claim keeps its stable ID,
        # confirmation status and independent mapping/risk validation below.
        used_a = unique_publication_claims([
            item for item in claim_result["a_claims"]
            if item["status"] in USED_STATUSES
        ])
        target_supported = sum(item.get("source") == "target" for item in used_a)
        confirmed_b = len(unique_publication_claims([
            item for item in claim_result["b_claims"]
            if item["status"] == "confirmed_use"
        ]))
        evidence_result["publication_counts"] = {
            "target_supported": target_supported,
            "group_a": len(used_a) - target_supported,
            "confirmed_b": confirmed_b,
        }
        units = len(used_a) + confirmed_b
        evidence_result["content_units"] = units
        evidence_result["content_richness"] = consensus.richness_label(units)
        evidence_result["clarification_required"] = bool(
            evidence_result["clarifications"]
        ) or units < 3
    scope = str(final.get("scope", "full_listing"))
    final["claim_audit"] = copy.deepcopy(claim_result["validator_claim_audit"])
    raw_blocked = final.get("blocked_terms", [])
    if not isinstance(raw_blocked, list):
        raise ValueError("draft.blocked_terms must be a list")
    blocked = list(raw_blocked)
    blocked.extend(copy.deepcopy(claim_result.get("validator_blocked_terms", [])))
    final["blocked_terms"] = dedupe_blocked(blocked)
    final["field_claim_ids"] = derive_field_claim_ids(final["claim_audit"], scope)

    keyword_metrics = (
        []
        if scope == "title_highlights"
        else benchmark([str(item["term"]) for item in keyword_specs], competitor_bullets)
    )
    if not isinstance(final.get("keywords", []), list):
        raise ValueError("draft.keywords must be a list")
    existing_terms = {
        tuple(kwfreq.tokens(str(item.get("phrase", ""))))
        for item in final.get("keywords", []) if isinstance(item, dict)
    }
    if keyword_metrics:
        bullet_text = "\n".join(str(value) for value in final.get("bullets", []))
        final.setdefault("keywords", [])
        spec_by_key = {
            tuple(kwfreq.tokens(str(spec["term"]))): spec for spec in keyword_specs
        }
        # A per-term override must not disable unrelated benchmark requirements
        # or lose candidate variants/placement constraints declared alongside it.
        for item in keyword_metrics:
            key = tuple(kwfreq.tokens(str(item["keyword"])))
            spec = spec_by_key.get(key, {})
            if key in existing_terms:
                if spec:
                    for existing_item in final["keywords"]:
                        if (
                            isinstance(existing_item, dict)
                            and tuple(kwfreq.tokens(str(existing_item.get("phrase", "")))) == key
                        ):
                            merge_keyword_constraints(existing_item, spec)
                continue
            if not keyword_target_is_nonzero(item["suggested"]):
                continue
            final["keywords"].append({
                "phrase": str(item["keyword"]),
                "target": str(item["suggested"]),
                "variants": copy.deepcopy(spec.get("variants", [])),
                "reported_used": keyword_count(bullet_text, {
                    "phrase": str(item["keyword"]),
                    "variants": spec.get("variants", []),
                }),
                "source": "competitor",
                **({
                    "eligible_locations": spec["eligible_locations"],
                } if spec.get("eligible_locations") else {}),
            })
            existing_terms.add(key)
        if not final["keywords"] and not str(final.get("keyword_audit_reason", "")).strip():
            final["keyword_audit_reason"] = (
                "候选词在竞品 Bullet 中没有非零频次基准，仅保留信息性 benchmark"
            )
    bullet_text = "\n".join(str(value) for value in final.get("bullets", []))
    priority_text = (
        "\n".join([str(final.get("title", "")), str(final.get("highlights", ""))])
        if scope == "title_highlights"
        else bullet_text
    )
    candidate_specs = {
        tuple(kwfreq.tokens(str(spec["term"]))): spec for spec in keyword_specs
    } if priority_specs else {}
    for spec in priority_specs:
        term = str(spec["term"])
        key = tuple(kwfreq.tokens(term))
        priority = {
            "phrase": term,
            "variants": copy.deepcopy(spec.get("variants", [])),
            "source": "user_priority",
        }
        if spec.get("eligible_locations"):
            priority["eligible_locations"] = copy.deepcopy(spec["eligible_locations"])
        if spec.get("required_locations"):
            priority["required_locations"] = copy.deepcopy(spec["required_locations"])
        # Accepted forms and placement constraints survive zero-frequency filtering.
        if key in candidate_specs:
            merge_keyword_constraints(priority, candidate_specs[key])
        if key in existing_terms:
            for item in final["keywords"]:
                if isinstance(item, dict) and tuple(kwfreq.tokens(str(item.get("phrase", "")))) == key:
                    merge_keyword_constraints(priority, item)
            final["keywords"] = [
                priority if isinstance(item, dict) and tuple(kwfreq.tokens(str(item.get("phrase", "")))) == key else item
                for item in final.get("keywords", [])
            ]
        else:
            final.setdefault("keywords", []).append(priority)
        priority["reported_used"] = keyword_count(priority_text, priority)
        existing_terms.add(key)

    # Apply explicit candidate placements after filtering and priority replacement.
    # A zero benchmark retains the placement alone, without a new frequency target.
    placement_specs = [
        spec for spec in keyword_specs
        if spec.get("eligible_locations") or spec.get("required_locations")
    ]
    if placement_specs:
        keyword_by_key = {
            tuple(kwfreq.tokens(str(item.get("phrase", "")))): item
            for item in final.get("keywords", []) if isinstance(item, dict)
        }
        for spec in placement_specs:
            key = tuple(kwfreq.tokens(str(spec["term"])))
            if key not in keyword_by_key:
                item = {"phrase": str(spec["term"]), "source": "competitor"}
                final.setdefault("keywords", []).append(item)
                keyword_by_key[key] = item
            item = keyword_by_key[key]
            merge_keyword_constraints(item, spec)
            item["reported_used"] = keyword_count(bullet_text, item)
    if competitor_bullets:
        final["competitors"] = [kwfreq.flatten_competitor(value) for value in competitor_bullets]
    elif isinstance(final.get("competitors"), list):
        final["competitors"] = [kwfreq.flatten_competitor(value) for value in final["competitors"]]

    results = validate_final.validate(final)
    result_dicts = [result.__dict__ for result in results]
    if compact and evidence_result is not None:
        result_dicts = suppress_consensus_cascades(
            result_dicts, evidence_result, claim_result
        )
    hard_failures = [result for result in result_dicts if result["level"] == "FAIL"]
    clarification = bool(claim_result["blocking_clarifications"])
    if evidence_result is not None:
        clarification = clarification or bool(evidence_result["clarification_required"])
    return {
        "schema_version": 2 if compact else 1,
        "evidence": evidence_result,
        "claim_review": claim_result,
        "confirmation_markdown": claim_review.render_markdown(claim_result),
        "keyword_benchmark": keyword_metrics,
        "measurements": measurements(final),
        "final_payload": final,
        "validation": result_dicts,
        "hard_pass": not hard_failures,
        "clarification_required": clarification,
    }


def render_optimization_summary(result: dict[str, object]) -> str:
    evidence = result.get("evidence")
    claim_result = result.get("claim_review", {})
    final = result.get("final_payload", {})
    if not isinstance(claim_result, dict):
        claim_result = {}
    if not isinstance(final, dict):
        final = {}
    if isinstance(evidence, dict):
        mode = str(evidence.get("mode", "unknown"))
        units = int(evidence.get("content_units", 0) or 0)
        richness = {
            "insufficient": "信息不足", "limited": "信息有限",
            "basic": "基础完整", "full": "内容充实", "dense": "信息密集",
        }.get(str(evidence.get("content_richness", "")), "未分类")
        target_supported = len(evidence.get("confirmed", [])) if isinstance(evidence.get("confirmed"), list) else 0
        group_a = len(evidence.get("group_a", [])) if isinstance(evidence.get("group_a"), list) else 0
        raw_group_b = evidence.get("group_b", [])
        confirmed_b = sum(
            1 for item in raw_group_b
            if isinstance(item, dict) and item.get("status") == "confirmed"
        ) if isinstance(raw_group_b, list) else 0
        publication_counts = evidence.get("publication_counts")
        if isinstance(publication_counts, dict):
            target_supported = publication_counts["target_supported"]
            group_a = publication_counts["group_a"]
            confirmed_b = publication_counts["confirmed_b"]
    else:
        mode, units, richness = "legacy", 0, "未分类"
        target_supported, group_a, confirmed_b = 0, 0, 0
    pending = claim_result.get("pending_facts", [])
    exclusions = claim_result.get("automatic_exclusions", [])
    keywords = final.get("keywords", [])
    pending_count = len(pending) if isinstance(pending, list) else 0
    exclusion_count = len(exclusions) if isinstance(exclusions, list) else 0
    keyword_count_value = len(keywords) if isinstance(keywords, list) else 0
    scope = str(final.get("scope", "full_listing"))
    deliverable = "Title / Item Highlights" if scope == "title_highlights" else "Final Listing"
    status = f"可输出 {deliverable}" if result.get("hard_pass") and not result.get("clarification_required") else f"已阻止 {deliverable}"
    return "\n".join([
        "## 竞品与优化结论",
        "",
        f"- 事实模式：{mode}",
        f"- 事实基础：目标资料支持 {target_supported} 项，Group A {group_a} 项，"
        f"已确认 Group B {confirmed_b} 项，共 {units} 个可用信息单元（{richness}）",
        f"- 待确认普通信息：{pending_count} 项；自动省略：{exclusion_count} 项",
        f"- 关键词：{keyword_count_value} 个有效候选或用户指定词已按最终文案复核",
        f"- 流水线状态：{status}",
    ])


def render_markdown(result: dict[str, object]) -> str:
    lines = [
        render_optimization_summary(result), "",
        str(result["confirmation_markdown"]), "", "## 自动审计摘要", "",
    ]
    labels = {
        "Product Highlights": "Highlights",
        "Bullet 1": "B1",
        "Bullet 2": "B2",
        "Bullet 3": "B3",
        "Bullet 4": "B4",
        "Bullet 5": "B5",
        "Bullet 1 + 2": "B1+B2",
    }
    measurement_parts = []
    for field, length in dict(result["measurements"]).items():
        rendered = f"{labels.get(field, field)} {length}/{MEASUREMENT_LIMITS[field]}"
        if field in MOBILE_PREFERRED_LIMITS:
            rendered += f"（移动端建议≤{MOBILE_PREFERRED_LIMITS[field]}）"
        measurement_parts.append(rendered)
    lines.append("**字符统计：** " + "｜".join(measurement_parts))
    final_keywords = [
        item for item in dict(result["final_payload"]).get("keywords", [])
        if isinstance(item, dict)
    ]
    priority_rows = [item for item in final_keywords if item.get("source") == "user_priority"]
    if priority_rows:
        lines.extend(["", "### 用户指定关键词", ""])
        bullets = [str(value) for value in dict(result["final_payload"]).get("bullets", [])]
        title = str(dict(result["final_payload"]).get("title", ""))
        highlights = str(dict(result["final_payload"]).get("highlights", ""))
        scope = str(dict(result["final_payload"]).get("scope", "full_listing"))
        for item in priority_rows:
            phrase = str(item.get("phrase", ""))
            placements = []
            if scope == "title_highlights" and keyword_count(title, item):
                placements.append("Title")
            placements.extend(
                f"Bullet {index}" for index, bullet in enumerate(bullets, start=1)
                if keyword_count(bullet, item)
            )
            if keyword_count(highlights, item):
                placements.append("Item Highlights")
            if scope == "title_highlights":
                count = keyword_count("\n".join([title, highlights]), item)
                lines.append(
                    f"- {phrase}：{('、'.join(placements) or '未找到有效位置')}，"
                    f"Title/Highlights共{count}次"
                )
            else:
                lines.append(
                    f"- {phrase}：{('、'.join(placements) or '未找到有效位置')}，"
                    f"五点共{sum(keyword_count(value, item) for value in bullets)}次"
                )
    metrics = result.get("keyword_benchmark", [])
    if metrics:
        lines.extend(["", "### 竞品提取关键词", ""])
        active = [item for item in metrics if keyword_target_is_nonzero(item["suggested"])]
        if active:
            specs = {
                tuple(kwfreq.tokens(str(item.get("phrase", "")))): item
                for item in final_keywords
            }
            bullet_text = "\n".join(
                str(value) for value in result["final_payload"].get("bullets", [])
            )
            summary = []
            for item in active:
                phrase = str(item["keyword"])
                spec = specs.get(tuple(kwfreq.tokens(phrase)), {"phrase": phrase})
                actual = keyword_count(bullet_text, spec)
                summary.append(f"{phrase}（建议{item['suggested']}，实际{actual}）")
            lines.append("- " + "，".join(summary))
        else:
            lines.append("- 无非零竞品Bullet频次基准；完整数据保留在JSON结果中")
    lines.extend(["", "### 最终验证", ""])
    actionable = [
        item for item in result["validation"]
        if item["level"] in {"FAIL", "WARN"} or item["code"] == "FINAL"
    ]
    for item in actionable:
        lines.append(f"- {item['level']} {item['code']}: {item['message']}")
    return "\n".join(lines)


def render_repair_markdown(result: dict[str, object]) -> str:
    """Group known dependencies without changing validation or guessing matches."""
    final = result["final_payload"]
    package_components = final.get("package_components", [])
    if not isinstance(package_components, list):
        package_components = []
    issues = [item for item in result["validation"] if item["level"] in {"FAIL", "WARN"}]
    lines = ["## 校验修复", "", "未输出成品；修复后须重新完整校验。", ""]
    evidence = result.get("evidence") or {}
    clarifications = [
        f"{item['field']}: {item['reason']}"
        for item in evidence.get("clarifications", [])
    ]
    clarifications.extend(
        f"{item.get('claim_zh') or item['claim']}: {item['reason']}"
        for item in result["claim_review"].get("blocking_clarifications", [])
    )
    if evidence.get("clarification_required") and evidence.get("content_units", 0) < 3:
        clarifications.append(f"可用信息 {evidence.get('content_units', 0)}/3 项，请补充产品事实")
    if result.get("clarification_required"):
        lines.extend(["### 需要确认", ""])
        lines.extend(f"- {text}" for text in dict.fromkeys(clarifications))
        lines.append("")

    # Only a same-claim, same-field missing source match explains this cascade.
    missing_source = {
        (item.get("location"), item.get("claim_id"))
        for item in issues
        if item["code"] == "CLAIM_LOCATION" and item.get("location") and item.get("claim_id")
    }
    grouped: dict[str, list[dict[str, object]]] = {}
    cascades: dict[tuple[str, str], list[str]] = {}
    for item in issues:
        location = str(item.get("location", ""))
        claim_id = str(item.get("claim_id", ""))
        key = (location, claim_id)
        if item["code"] == "CLAIM_COVERAGE" and key in missing_source:
            cascades.setdefault(key, []).append(str(item["code"]))
        else:
            grouped.setdefault(location, []).append(item)

    ordered = [*required_fields(str(final.get("scope", "full_listing"))), "Bullet 1 + 2", ""]
    ordered.extend(location for location in grouped if location not in ordered)
    for location in ordered:
        if location not in grouped:
            continue
        label = "Item Highlights" if location == "Product Highlights" else location or "其他检查"
        lines.extend([f"### {label}", ""])
        for item in sorted(grouped[location], key=lambda item: item["level"] != "FAIL"):
            extra = cascades.get((location, str(item.get("claim_id", ""))), []) if item["code"] == "CLAIM_LOCATION" else []
            codes = " / ".join(dict.fromkeys([str(item["code"]), *extra]))
            lines.append(f"- {item['level']} {codes}: {item['message']}")

        fields = ["Bullet 1", "Bullet 2"] if location == "Bullet 1 + 2" else [location]
        related = [
            item for item in final.get("claim_audit", [])
            if item.get("status") in USED_STATUSES
            and any(field in item.get("locations", {}) or field in item.get("translation_locations", {}) for field in fields)
        ]
        if location and any(item["level"] == "FAIL" for item in grouped[location]):
            ids = ", ".join(item["id"] for item in related)
            components = [
                str(item.get("component", "未命名组件")) for item in package_components
                if isinstance(item, dict) and any(
                    validate_final.resolve_location(str(raw_field)) in fields
                    for key in ("listing_matches", "translation_matches")
                    if isinstance(item.get(key), dict)
                    for raw_field in item.get(key, {})
                )
            ]
            hint = f"同步检查：{ids or '该字段事实映射'} 的源文、中文及匹配片段"
            if components:
                hint += "; 包装映射 " + ", ".join(components)
            lines.extend(["", hint + "；保留关键词计划。"])
        lines.append("")
    lines.append("完整检查结果保留在审计输出；WARN 需审查，不必为凑长度或词频改写。")
    return "\n".join(lines)


def render_listing_markdown(result: dict[str, object]) -> str:
    if not bool(result.get("hard_pass")) or bool(result.get("clarification_required")):
        return render_repair_markdown(result)
    audit = render_markdown(result)
    final = dict(result["final_payload"])
    translations = final.get("translations", {})
    if not isinstance(translations, dict):
        translations = {}
    bullets = [str(value) for value in final.get("bullets", [])]
    translated_bullets = [str(value) for value in translations.get("bullets", [])]
    lines = [audit, "", "## Final Listing", "", "### Title", ""]
    lines.extend([
        str(final.get("title", "")), "", f"**中文：** {translations.get('title', '')}", "",
        "### Item Highlights", "", str(final.get("highlights", "")), "",
        f"**中文：** {translations.get('highlights', '')}", "",
    ])
    if str(final.get("scope", "full_listing")) == "title_highlights":
        return "\n".join(lines)
    lines.extend(["### Bullet Points", ""])
    for index, bullet in enumerate(bullets, start=1):
        lines.extend([f"{index}. {bullet}", ""])
    lines.extend(["### 五点中文翻译", ""])
    for index, translated in enumerate(translated_bullets, start=1):
        lines.extend([f"{index}. {translated}", ""])
    lines.extend([
        "### Product Description", "", "```text", str(final.get("description", "")), "```", "",
        "**中文：**", "", "```text", str(translations.get("description", "")), "```",
    ])
    return "\n".join(lines)


def read_input(path: str | None) -> dict[str, object]:
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", help="read UTF-8 JSON, with or without a BOM, from a file")
    parser.add_argument(
        "--format", choices=("markdown", "listing-markdown", "json"),
        default="listing-markdown",
    )
    args = parser.parse_args()
    try:
        result = run(read_input(args.file))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"FAIL\tINPUT\t{error}")
        raise SystemExit(1)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.format == "listing-markdown":
        print(render_listing_markdown(result))
    else:
        print(render_markdown(result))
    raise SystemExit(1 if result["clarification_required"] or not result["hard_pass"] else 0)


if __name__ == "__main__":
    main()
