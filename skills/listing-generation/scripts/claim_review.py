#!/usr/bin/env python3
"""Classify listing claims into seller-confirmation groups.

The script accepts UTF-8 JSON on stdin or with ``--file`` and emits either a
stable Markdown confirmation block or JSON for ``validate_final.py``. It uses
only the Python standard library.

Input schema:
{
  "marketplace": "US" | "DE",
  "claims": [
    {
      "claim": "human-readable source claim",
      "claim_zh": "concise Simplified Chinese meaning for seller review",
      "claim_type": "material | compatibility | routine_care | certification | ...",
      "use_text": "exact proposed listing wording",
      "adjusted_text": "optional factual rewrite of a marketing claim",
      "high_risk_text": "optional localized high-risk wording to enable after verification",
      "match_text": "exact phrase expected in final listing",
      "block_term": "root or phrase that must stay out while held/omitted",
      "block_match": "phrase | substring",
      "locations": ["Title", "Bullet 3"],
      "location_matches": {"Title": "exact title wording",
                           "Bullet 3": "exact Bullet wording"},
      "translation_location_matches": {"Title": "准确中文标题表达",
                                       "Bullet 3": "准确中文五点表达"},
      "source_summary": "4/5 eligible sources, no conflict",
      "support_count": 4,
      "eligible_count": 5,
      "conflicting_count": 1,
      "conflict": false,
      "core_identity": false,
      "seller_confirmed": false,
      "high_risk": false,
      "evidence_needed": "optional evidence description"
    }
  ],
  "automatic_exclusions": [
    {
      "display": "masked or buyer-safe label",
      "term": "exact blocked term",
      "match": "phrase | substring",
      "reason": "why it is excluded"
    }
  ],
  "pending_facts": [
    {
      "claim": "ordinary missing or ambiguous fact",
      "claim_zh": "中文释义",
      "confirmation_needed": "what the seller should verify"
    }
  ],
  "confirmation": {
    "confirm_all_a": true,
    "delete": ["A4"],
    "enable": ["B1"],
    "disable": ["B2"],
    "modify": {
      "A2": {"use_text": "fits Auron M5", "match_text": "fits Auron M5",
               "locations": ["Bullet 1"]}
    }
  },
  "confirmation_text": "A全部确认\nA删除：A4\nA修改：A2 = fits Auron M5\nB已核实并启用：B1\nB暂不使用：B2"
}

Low/medium-risk attributes, compatibility, routine use, and care become Group
A and may be provisionally used. Certification, high-risk safety, regulated,
and test-dependent performance claims become Group B and remain held unless
``seller_confirmed`` is true. Automatic exclusions do not consume A IDs; they
are emitted separately as blocked terms. Marketing absolutes may enter Group A
only when ``adjusted_text`` removes the unsupported conclusion and states a
concrete fact. A tied non-core conflict is omitted; a core-identity conflict is
returned as blocking clarification.

Boolean fields accept JSON ``true``/``false`` only; quoted strings are invalid.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from policy_rules import (
    ALL_HIGH_RISK_SOURCE_PATTERNS as HIGH_RISK_PATTERNS,
    EXACT_MATERIAL_GRADE_PATTERNS,
    HIGH_RISK_TYPES,
    UNSUPPORTED_MARKETING_PATTERNS,
    strip_non_commercial_disclaimer,
)
from schema_validation import boolean_field

ALWAYS_OMIT_TYPES = {"competitor_brand", "wrong_product_form", "wrong_language"}

EVIDENCE_DEFAULTS = {
    "certification": "对应SKU、材料或部件的有效认证文件",
    "bpa_free": "对应塑料部件和SKU的BPA声明或检测",
    "food_contact": "对应材料和SKU的食品接触合规文件",
    "non_toxic": "对应材料和SKU的安全检测或声明",
    "medical": "适用商城要求的医疗或监管证明",
    "child_safety": "适用年龄、使用条件和安全测试",
    "pet_safety": "对应材料和使用场景的安全证明",
    "antibacterial": "抗菌测试方法、结果和适用范围",
    "electrical_safety": "对应SKU的电气安全测试或认证",
    "fire_resistance": "对应材料和SKU的阻燃或防火测试",
    "waterproof_rating": "对应SKU和测试条件的防水报告",
    "leakproof": "对应结构、方向和测试条件的防漏测试",
    "load_capacity": "对应结构和使用条件的承重测试",
    "temperature_rating": "对应材料、时长和条件的耐温测试",
    "precision": "测量方法、误差范围和精度测试",
    "filter_class": "对应SKU和滤材等级的过滤等级测试或认证文件",
    "filtration_efficiency": "对应粒径、风量、测试方法和SKU的过滤效率报告",
    "allergy_suitability": "对应声明范围、测试条件和商城要求的适用性证明",
    "lifespan": "测试条件、循环次数或寿命报告",
    "environmental": "对应声明范围的环保或可持续证明",
    "regulated_performance": "对应声明、SKU和测试条件的报告",
}

STATUS_LABELS = {
    "temporary_use": "已暂用，等待确认",
    "adjusted_use": "已改写后暂用，等待确认",
    "confirmed_use": "用户已核实，可使用",
    "omit": "已保守省略",
    "hold": "暂未写入，核实后可启用",
    "clarify_core": "核心身份冲突，必须澄清",
}


def folded(text: object) -> str:
    return str(text or "").strip().casefold()


def claim_count(claim: dict[str, object], key: str) -> int:
    value = claim.get(key, 0)
    if type(value) is not int or value < 0:
        raise ValueError(f"{key} must be a finite non-negative JSON integer")
    return value


def contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    value = folded(text)
    return any(re.search(pattern, value, re.IGNORECASE) for pattern in patterns)


def relationship_ok(marketplace: str, text: str) -> bool:
    value = folded(text)
    if marketplace == "US":
        if re.search(r"\b(?:fit|fits)\s+for\b", value):
            return False
        patterns = (
            r"\bcompatible\s+with\s+\S+",
            r"\bfor\s+\S+",
            r"\bfits\s+\S+",
        )
    else:
        patterns = (
            r"\bkompatibel\s+mit\s+\S+",
            r"\bpassend\s+für\s+\S+",
            r"\bfür\s+\S+",
        )
    return any(re.search(pattern, value, re.IGNORECASE) for pattern in patterns)


def location_matches(claim: dict[str, object], default: str) -> dict[str, str]:
    raw = claim.get("location_matches")
    if isinstance(raw, dict):
        return {
            str(location).strip(): str(match).strip()
            for location, match in raw.items()
            if str(location).strip() and str(match).strip()
        }
    locations = claim.get("locations", [])
    if isinstance(locations, list):
        return {str(location).strip(): default for location in locations if str(location).strip()}
    if str(locations or "").strip():
        return {str(locations).strip(): default}
    return {}


def translation_location_matches(claim: dict[str, object]) -> dict[str, str]:
    raw = claim.get("translation_location_matches", claim.get("translation_matches", {}))
    if not isinstance(raw, dict):
        return {}
    return {
        str(location).strip(): str(match).strip()
        for location, match in raw.items()
        if str(location).strip() and str(match).strip()
    }


def location_text(claim: dict[str, object]) -> str:
    def display(value: object) -> str:
        text = str(value).strip()
        return "Item Highlights" if text.casefold() == "product highlights" else text

    raw = claim.get("locations", [])
    if isinstance(raw, list):
        rendered = "、".join(display(value) for value in raw if str(value).strip())
        if rendered:
            return rendered
    elif str(raw or "").strip():
        return display(raw)
    matches = claim.get("location_matches", {})
    if isinstance(matches, dict):
        rendered = "、".join(display(value) for value in matches if str(value).strip())
        if rendered:
            return rendered
    return "未指定"


def normalize_automatic_exclusion(raw: dict[str, object]) -> dict[str, object]:
    term = str(raw.get("term", raw.get("block_term", ""))).strip()
    display = str(raw.get("display", raw.get("claim", ""))).strip() or "[自动排除项]"
    match = str(raw.get("match", raw.get("block_match", "substring"))).strip().lower()
    if not term:
        raise ValueError("automatic exclusions require term")
    if match not in {"phrase", "substring"}:
        raise ValueError("automatic exclusion match must be phrase or substring")
    return {
        "claim": display,
        "claim_zh": str(raw.get("claim_zh", "")).strip(),
        "claim_type": "automatic_exclusion",
        "use_text": "",
        "match_text": "",
        "block_term": term,
        "block_match": match,
        "locations": "未指定",
        "location_matches": {},
        "source_summary": str(raw.get("source_summary", "自动排除")).strip() or "自动排除",
        "seller_confirmed": False,
        "evidence_needed": "",
        "reason": str(raw.get("reason", "不进入Listing")).strip() or "不进入Listing",
        "group": "A",
        "status": "omit",
    }


def source_text(claim: dict[str, object]) -> str:
    explicit = str(claim.get("source_summary", "")).strip()
    if explicit:
        return explicit
    support = claim_count(claim, "support_count")
    eligible = claim_count(claim, "eligible_count")
    if eligible:
        return f"{support}/{eligible} 个有效来源"
    return "用户提供数据"


def evidence_text(
    claim: dict[str, object], claim_type: str, proposed: str
) -> str:
    explicit = str(claim.get("evidence_needed", "")).strip()
    if explicit:
        return explicit
    if contains_any(proposed, EXACT_MATERIAL_GRADE_PATTERNS):
        return "对应SKU和部件的材质等级规格书、材质证明或检测报告"
    return EVIDENCE_DEFAULTS.get(claim_type, "对应SKU、部件和声明范围的证明")


def classify_claim(marketplace: str, claim: dict[str, object]) -> dict[str, object]:
    original = str(claim.get("claim", "")).strip()
    claim_type = str(claim.get("claim_type", "attribute")).strip().lower()
    adjusted = str(claim.get("adjusted_text", "")).strip()
    proposed = adjusted or str(claim.get("use_text", "")).strip() or original
    seller_confirmed = boolean_field(claim, "seller_confirmed")
    explicit_high_risk = boolean_field(claim, "high_risk")
    conflict = boolean_field(claim, "conflict")
    core_identity = boolean_field(claim, "core_identity")
    support = claim_count(claim, "support_count")
    conflicting = claim_count(claim, "conflicting_count")
    original_marketing = contains_any(
        strip_non_commercial_disclaimer(original), UNSUPPORTED_MARKETING_PATTERNS
    )
    proposed_marketing = contains_any(
        strip_non_commercial_disclaimer(proposed), UNSUPPORTED_MARKETING_PATTERNS
    )
    proposed_high_risk = contains_any(proposed, HIGH_RISK_PATTERNS)
    original_high_risk = contains_any(original, HIGH_RISK_PATTERNS)

    match_text = str(claim.get("match_text", "")).strip() or proposed
    result: dict[str, object] = {
        "claim": original,
        "claim_zh": str(claim.get("claim_zh", "")).strip(),
        "claim_type": claim_type,
        "use_text": proposed,
        "match_text": match_text,
        "block_term": str(claim.get("block_term", "")).strip() or original,
        "block_match": str(claim.get("block_match", "substring")).strip().lower(),
        "locations": location_text(claim),
        "location_matches": location_matches(claim, match_text),
        "translation_location_matches": translation_location_matches(claim),
        "source": str(claim.get("source", "")).strip(),
        "source_summary": source_text(claim),
        "seller_confirmed": seller_confirmed,
        "evidence_needed": evidence_text(claim, claim_type, proposed),
        "reason": "",
    }

    if conflict and core_identity:
        result.update(group="BLOCK", status="clarify_core", reason="核心产品身份存在冲突")
        return result

    if claim_type in ALWAYS_OMIT_TYPES:
        result.update(group="A", status="omit", reason="竞品品牌、错误形态或错误语言不得进入Listing")
        return result

    high_risk = (
        explicit_high_risk
        or claim_type in HIGH_RISK_TYPES
        or proposed_high_risk
        or (original_high_risk and not adjusted)
    )
    if high_risk:
        result.update(
            group="B",
            status="confirmed_use" if seller_confirmed else "hold",
            reason="用户已核实" if seller_confirmed else "需要证据或卖家核实后启用",
        )
        return result

    if conflict:
        result.update(group="A", status="omit", reason="具体值存在冲突；请改用无冲突共同上位值或自动省略")
        return result

    if claim_type == "compatibility":
        compat_texts = list(result["location_matches"].values()) or [proposed]
        bad_compat = [text for text in compat_texts if not relationship_ok(marketplace, text)]
        if bad_compat:
            result.update(
                group="A",
                status="omit",
                block_term=bad_compat[0],
                block_match="phrase",
                reason="兼容品牌或型号未绑定指定关系介词",
            )
            return result

    if proposed_marketing:
        result.update(group="A", status="omit", reason="绝对性或主观表达未完成事实化改写")
        return result

    if claim_type == "factual_rewrite":
        status = "confirmed_use" if seller_confirmed else "adjusted_use"
        reason = "用户已确认改写事实" if seller_confirmed else "已剥离高风险或营销结论并改为具体事实"
    elif original_marketing and adjusted:
        status = "confirmed_use" if seller_confirmed else "adjusted_use"
        reason = "用户已确认改写事实" if seller_confirmed else "已删除营销结论并改为具体事实"
    else:
        status = "confirmed_use" if seller_confirmed else "temporary_use"
        reason = "用户已确认" if seller_confirmed else "普通属性、兼容性或常规使用声明"
    result.update(group="A", status=status, reason=reason)
    return result


def expand_claim(raw: dict[str, object]) -> list[dict[str, object]]:
    original = str(raw.get("claim", "")).strip()
    claim_type = str(raw.get("claim_type", "attribute")).strip().lower()
    adjusted = str(raw.get("adjusted_text", "")).strip()
    high_risk = (
        boolean_field(raw, "high_risk")
        or claim_type in HIGH_RISK_TYPES
        or contains_any(original, HIGH_RISK_PATTERNS)
    )
    adjusted_is_factual = (
        adjusted
        and not contains_any(adjusted, HIGH_RISK_PATTERNS)
        and not contains_any(
            strip_non_commercial_disclaimer(adjusted), UNSUPPORTED_MARKETING_PATTERNS
        )
    )
    if not high_risk or not adjusted_is_factual:
        return [raw]

    factual = dict(raw)
    factual.update(
        claim_type="factual_rewrite",
        use_text=adjusted,
        match_text=str(raw.get("adjusted_match_text", "")).strip() or adjusted,
        location_matches=raw.get("adjusted_location_matches", raw.get("location_matches", {})),
        translation_location_matches=raw.get(
            "adjusted_translation_matches",
            raw.get("translation_location_matches", raw.get("translation_matches", {})),
        ),
        block_term="",
        high_risk=False,
        conflict=False,
        core_identity=False,
        claim_zh=str(raw.get("adjusted_claim_zh", raw.get("claim_zh", ""))).strip(),
    )

    held = dict(raw)
    high_risk_text = str(raw.get("high_risk_text", "")).strip() or original
    held.update(
        adjusted_text="",
        use_text=high_risk_text,
        match_text=str(raw.get("high_risk_match_text", "")).strip() or high_risk_text,
        location_matches=raw.get("high_risk_location_matches", {}),
        translation_location_matches=raw.get("high_risk_translation_matches", {}),
        block_term=str(raw.get("block_term", "")).strip() or original,
        high_risk=True,
        claim_zh=str(raw.get("high_risk_claim_zh", raw.get("claim_zh", ""))).strip(),
    )
    return [factual, held]


def normalize_pending_fact(raw: dict[str, object], index: int) -> dict[str, object]:
    claim = str(raw.get("claim", "")).strip()
    if not claim:
        raise ValueError("pending facts require claim")
    return {
        "id": f"P{index}",
        "claim": claim,
        "claim_zh": str(raw.get("claim_zh", "")).strip(),
        "confirmation_needed": str(
            raw.get("confirmation_needed", "请卖家确认准确值")
        ).strip() or "请卖家确认准确值",
    }


def classify(data: dict[str, object]) -> dict[str, object]:
    marketplace = str(data.get("marketplace", "")).strip().upper()
    if marketplace not in {"US", "DE"}:
        raise ValueError("marketplace must be US or DE")
    claims = data.get("claims", [])
    if not isinstance(claims, list):
        raise ValueError("claims must be a list")

    a_claims: list[dict[str, object]] = []
    b_claims: list[dict[str, object]] = []
    automatic_exclusions: list[dict[str, object]] = []
    blocking: list[dict[str, object]] = []
    for raw in claims:
        if not isinstance(raw, dict):
            raise ValueError("every claim must be an object")
        support = claim_count(raw, "support_count")
        eligible = claim_count(raw, "eligible_count")
        conflicting = claim_count(raw, "conflicting_count")
        if eligible and support + conflicting > eligible:
            raise ValueError("claim support_count + conflicting_count exceeds eligible_count")
        for expanded in expand_claim(raw):
            result = classify_claim(marketplace, expanded)
            if result["group"] == "A":
                if result["status"] == "omit":
                    automatic_exclusions.append(result)
                else:
                    a_claims.append(result)
            elif result["group"] == "B":
                b_claims.append(result)
            else:
                blocking.append(result)

    direct_exclusions = data.get("automatic_exclusions", [])
    if not isinstance(direct_exclusions, list):
        raise ValueError("automatic_exclusions must be a list")
    for raw in direct_exclusions:
        if not isinstance(raw, dict):
            raise ValueError("every automatic exclusion must be an object")
        automatic_exclusions.append(normalize_automatic_exclusion(raw))

    for index, claim in enumerate(a_claims, start=1):
        claim["id"] = f"A{index}"
    for index, claim in enumerate(b_claims, start=1):
        claim["id"] = f"B{index}"

    pending_raw = data.get("pending_facts", [])
    if not isinstance(pending_raw, list):
        raise ValueError("pending_facts must be a list")
    if len(pending_raw) > 3:
        raise ValueError("pending_facts supports at most 3 items")
    pending_facts: list[dict[str, object]] = []
    for index, raw in enumerate(pending_raw, start=1):
        if not isinstance(raw, dict):
            raise ValueError("every pending fact must be an object")
        pending_facts.append(normalize_pending_fact(raw, index))

    result = {
        "marketplace": marketplace,
        "a_claims": a_claims,
        "b_claims": b_claims,
        "automatic_exclusions": automatic_exclusions,
        "pending_facts": pending_facts,
        "blocking_clarifications": blocking,
    }
    confirmation = data.get("confirmation")
    confirmation_text = data.get("confirmation_text")
    if confirmation is not None and str(confirmation_text or "").strip():
        raise ValueError("use either confirmation or confirmation_text, not both")
    if str(confirmation_text or "").strip():
        confirmation = parse_confirmation_text(confirmation_text)
    apply_confirmation(result, confirmation)

    audit: list[dict[str, object]] = []
    for claim in [*a_claims, *b_claims]:
        status = str(claim["status"])
        if status in {"temporary_use", "adjusted_use", "confirmed_use"}:
            phrase = str(claim["match_text"])
            match = "phrase"
        else:
            phrase = str(claim["block_term"])
            match = str(claim["block_match"])
        if phrase:
            item = {"id": claim["id"], "status": status, "phrase": phrase, "match": match}
            if status in {"temporary_use", "adjusted_use", "confirmed_use"}:
                item["locations"] = claim.get("location_matches", {})
                item["translation_locations"] = claim.get(
                    "translation_location_matches", {}
                )
            audit.append(item)

    result["validator_claim_audit"] = audit
    result["validator_blocked_terms"] = [
        {
            "term": str(claim.get("block_term", "")),
            "match": str(claim.get("block_match", "substring")),
            "reason": str(claim.get("reason", "")),
        }
        for claim in automatic_exclusions
        if str(claim.get("block_term", "")).strip()
    ]
    return result


def id_map(result: dict[str, object]) -> dict[str, dict[str, object]]:
    claims = [*result["a_claims"], *result["b_claims"]]
    return {str(claim["id"]): claim for claim in claims}


def normalize_id_list(value: object, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"confirmation.{field} must be a list")
    return [str(item).strip() for item in value if str(item).strip()]


def split_ids(value: str) -> list[str]:
    return [item.upper() for item in re.findall(r"\b[AB]\d+\b", value, re.IGNORECASE)]


def parse_confirmation_text(value: object) -> dict[str, object]:
    text = str(value or "").strip()
    if not text:
        return {}
    result: dict[str, object] = {
        "confirm_all_a": False,
        "delete": [],
        "enable": [],
        "disable": [],
        "modify": {},
    }
    recognized = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line == "A全部确认":
            result["confirm_all_a"] = True
            recognized += 1
            continue
        sep = re.match(r"^(A删除|B已核实并启用|B暂不使用|A修改)\s*[:：]\s*(.*)$", line)
        if not sep:
            raise ValueError(f"unrecognized confirmation line: {line!r}")
        action, payload = sep.group(1), sep.group(2).strip()
        if action == "A删除":
            result["delete"].extend(split_ids(payload))
            recognized += 1
        elif action == "B已核实并启用":
            result["enable"].extend(split_ids(payload))
            recognized += 1
        elif action == "B暂不使用":
            result["disable"].extend(split_ids(payload))
            recognized += 1
        elif action == "A修改":
            if not payload:
                recognized += 1
                continue
            match = re.fullmatch(r"(A\d+)\s*[=＝]\s*(.+)", payload, re.IGNORECASE)
            if not match:
                raise ValueError("A修改 must use: A修改：A2 = replacement wording")
            claim_id, wording = match.group(1).upper(), match.group(2).strip()
            result["modify"][claim_id] = {"use_text": wording, "match_text": wording}
            recognized += 1
    if not recognized:
        raise ValueError("confirmation_text contains no recognized confirmation action")
    return result


def apply_confirmation(result: dict[str, object], raw: object) -> None:
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise ValueError("confirmation must be an object")
    claims = id_map(result)

    def get_claim(claim_id: str, allowed_prefix: str | None = None) -> dict[str, object]:
        if claim_id not in claims:
            raise ValueError(f"unknown confirmation claim id: {claim_id}")
        if allowed_prefix and not claim_id.startswith(allowed_prefix):
            raise ValueError(f"{claim_id} is not a Group {allowed_prefix} claim")
        return claims[claim_id]

    if boolean_field(raw, "confirm_all_a", field="confirmation.confirm_all_a"):
        for claim in result["a_claims"]:
            if claim["status"] in {"temporary_use", "adjusted_use"}:
                claim.update(status="confirmed_use", seller_confirmed=True, reason="用户已确认")

    delete_ids = normalize_id_list(raw.get("delete"), "delete")
    enable_ids = normalize_id_list(raw.get("enable"), "enable")
    disable_ids = normalize_id_list(raw.get("disable"), "disable")
    modifications = raw.get("modify", {})
    if not isinstance(modifications, dict):
        raise ValueError("confirmation.modify must be an object")

    conflict_a = sorted(set(delete_ids) & {str(k).strip() for k in modifications})
    if conflict_a:
        raise ValueError(f"conflicting confirmation delete and modify for: {', '.join(conflict_a)}")
    conflict_b = sorted(set(enable_ids) & set(disable_ids))
    if conflict_b:
        raise ValueError(f"conflicting confirmation enable and disable for: {', '.join(conflict_b)}")

    for claim_id in delete_ids:
        claim = get_claim(claim_id, "A")
        claim.update(
            status="omit",
            seller_confirmed=False,
            block_term=str(claim.get("match_text", "")) or str(claim.get("use_text", "")),
            block_match="phrase",
            reason="用户明确删除",
        )

    for claim_id in enable_ids:
        claim = get_claim(claim_id, "B")
        claim.update(status="confirmed_use", seller_confirmed=True, reason="用户已核实")

    for claim_id in disable_ids:
        claim = get_claim(claim_id, "B")
        claim.update(status="hold", seller_confirmed=False, reason="用户暂不使用")

    marketplace = str(result["marketplace"])
    for claim_id, change in modifications.items():
        claim = get_claim(str(claim_id), "A")
        if not isinstance(change, dict):
            raise ValueError(f"confirmation.modify.{claim_id} must be an object")
        use_text = str(change.get("use_text", "")).strip()
        match_text = str(change.get("match_text", "")).strip() or use_text
        if not use_text or not match_text:
            raise ValueError(f"confirmation.modify.{claim_id} requires use_text and match_text")
        if claim["claim_type"] == "compatibility" and not relationship_ok(marketplace, use_text):
            raise ValueError(f"confirmation.modify.{claim_id} lacks an approved compatibility relationship")
        if contains_any(
            strip_non_commercial_disclaimer(use_text), UNSUPPORTED_MARKETING_PATTERNS
        ):
            raise ValueError(f"confirmation.modify.{claim_id} retains unsupported marketing wording")
        claim.update(
            use_text=use_text,
            match_text=match_text,
            status="confirmed_use",
            seller_confirmed=True,
            reason="用户修改后确认",
        )
        if "locations" in change:
            claim["locations"] = location_text({"locations": change["locations"]})
            claim["location_matches"] = location_matches(
                {"locations": change["locations"], "location_matches": change.get("location_matches")},
                match_text,
            )
        elif claim.get("location_matches"):
            claim["location_matches"] = {
                str(location): match_text for location in dict(claim["location_matches"])
            }


def md_escape(value: object) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ").strip()


def status_label(claim: dict[str, object]) -> str:
    if claim.get("status") == "temporary_use" and claim.get("source") == "target":
        return "目标资料支持，建议确认"
    return STATUS_LABELS[str(claim["status"])]


def render_markdown(result: dict[str, object]) -> str:
    lines = ["## ⚠️ 发布前确认区 Seller Confirmation", ""]
    blocking = result["blocking_clarifications"]
    if blocking:
        lines.extend(["### 核心身份冲突", ""])
        for claim in blocking:
            lines.append(f"- {md_escape(claim['claim'])}: {md_escape(claim['reason'])}")
        lines.append("")

    lines.extend([
        "### A. 已写入或已改写，请快速确认",
        "",
        "| ID | 声明或处理结果 | 中文释义 | 当前动作 | 数据依据 | Listing位置 |",
        "|---|---|---|---|---|---|",
    ])
    a_claims = result["a_claims"]
    used_a = [claim for claim in a_claims if claim["status"] != "omit"]
    omitted_a = [claim for claim in a_claims if claim["status"] == "omit"]
    automatic_exclusions = result.get("automatic_exclusions", [])
    if used_a:
        for claim in used_a:
            display = claim["use_text"]
            lines.append(
                f"| {claim['id']} | {md_escape(display)} | "
                f"{md_escape(claim.get('claim_zh', '')) or '—'} | "
                f"{md_escape(status_label(claim))} | "
                f"{md_escape(claim['source_summary'])} | {md_escape(claim['locations'])} |"
            )
    else:
        lines.append("| 无 | 无已写入A类声明 | 无 | 无 | 无 | 无 |")

    lines.extend([
        "",
        "### B. 高风险或证据类声明",
        "",
        "| ID | 候选声明 | 中文释义 | 当前动作 | 核实要求 | 核实后建议表达 | 建议位置 |",
        "|---|---|---|---|---|---|---|",
    ])
    b_claims = result["b_claims"]
    if b_claims:
        for claim in b_claims:
            lines.append(
                f"| {claim['id']} | {md_escape(claim['claim'])} | "
                f"{md_escape(claim.get('claim_zh', '')) or '—'} | "
                f"{md_escape(status_label(claim))} | "
                f"{md_escape(claim['evidence_needed'])} | {md_escape(claim['use_text'])} | "
                f"{md_escape(claim['locations'])} |"
            )
    else:
        lines.append("| 无 | 无B类声明 | 无 | 无 | 无 | 无 | 无 |")

    pending_facts = result.get("pending_facts", [])
    if pending_facts:
        lines.extend([
            "",
            "### 待确认普通信息",
            "",
            "| ID | 待确认信息 | 中文释义 | 需要确认 |",
            "|---|---|---|---|",
        ])
        for claim in pending_facts:
            lines.append(
                f"| {claim['id']} | {md_escape(claim['claim'])} | "
                f"{md_escape(claim.get('claim_zh', '')) or '—'} | "
                f"{md_escape(claim['confirmation_needed'])} |"
            )

    lines.extend([
        "",
        "### 自动省略项（无需回复）",
        "",
        "| 原始声明 | 省略原因 |",
        "|---|---|",
    ])
    excluded_rows = [*automatic_exclusions, *omitted_a]
    if excluded_rows:
        for claim in excluded_rows:
            lines.append(
                f"| {md_escape(claim['claim'])} | {md_escape(claim['reason'])} |"
            )
    else:
        lines.append("| 无 | 无自动省略项 |")

    lines.extend([
        "",
        "### 可复制确认回复",
        "",
        "```text",
        "A全部确认",
        "A删除：",
        "A修改：",
        "B已核实并启用：",
        "B暂不使用：",
        "```",
        "",
        "修改时每项单独一行，例如：`A修改：A2 = 修改后的完整表达`。",
    ])
    return "\n".join(lines)


def read_input(path: str | None) -> dict[str, object]:
    raw = Path(path).read_text(encoding="utf-8") if path else sys.stdin.read()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("top-level JSON must be an object")
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", help="read UTF-8 JSON from this file instead of stdin")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    args = parser.parse_args()
    try:
        result = classify(read_input(args.file))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"FAIL\tINPUT\t{error}")
        raise SystemExit(1)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(result))
    raise SystemExit(1 if result["blocking_clarifications"] else 0)


if __name__ == "__main__":
    main()
