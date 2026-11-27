#!/usr/bin/env python3
"""Validate a semantic review and finalize the three-section Amazon report."""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("render_amazon_report", SCRIPT_DIR / "render_amazon_report.py")
assert SPEC and SPEC.loader
RENDERER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RENDERER)


class ReviewError(ValueError):
    pass


REVIEW_KEYS = {"schema_version", "payload_fingerprint", "review_json", "verdicts"}
VERDICT_KEYS = {
    "candidate_id", "verdict", "reason", "factual_evidence", "hard_mismatch",
    "supporting_terms_checked",
}
VERDICTS = {"approve", "reject", "review"}
NEGATIVE_FAMILIES = {"negative_exact", "negative_phrase"}
ALLOWED_NEGATIVE_TIERS = {
    "negative_exact": {
        "direct_negative_exact_after_semantic_safety_check",
        "negative_candidate_review",
        "semantic_mismatch_only_low_sample",
    },
    "negative_phrase": {
        "root_negative_phrase_after_semantic_safety_check",
        "root_negative_phrase_review",
    },
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalized(value: Any) -> str:
    return " ".join(_text(value).casefold().split())


def _is_asin_token(value: Any) -> bool:
    text = _text(value)
    return bool(re.fullmatch(r"(?i)[a-z0-9]{10}", text) and re.search(r"\d", text))


def _looks_like_product_target(row: dict[str, Any]) -> bool:
    identity = RENDERER._semantic_identity(row)
    query = identity["query"]
    target = identity["target"]
    source_family = _normalized(row.get("source_family"))
    return bool(
        _is_asin_token(query)
        or source_family in {"product", "product_targeting", "auto_product_targeting"}
        or re.search(r"(?i)\b(?:asin|product)\s*[=:]", target)
        or _is_asin_token(target)
    )


def _scope(row: dict[str, Any]) -> tuple[str, str, str, str]:
    identity = RENDERER._semantic_identity(row)
    return tuple(identity[key] for key in ("campaign", "ad_group", "target", "match_family"))


def _resolve_product_fact(product_facts: dict[str, Any], reference: str) -> Any:
    if not reference.startswith("product_facts."):
        raise ReviewError(f"factual evidence must reference product_facts fields: {reference}")
    current: Any = product_facts
    path = reference[len("product_facts."):]
    tokens = re.findall(r"([^.\[\]]+)|\[(\d+)\]", path)
    if not tokens:
        raise ReviewError(f"unknown product_facts reference: {reference}")
    for name, index in tokens:
        if name:
            if not isinstance(current, dict) or name not in current:
                raise ReviewError(f"unknown product_facts reference: {reference}")
            current = current[name]
        else:
            position = int(index)
            if not isinstance(current, list) or position >= len(current):
                raise ReviewError(f"unknown product_facts reference: {reference}")
            current = current[position]
    if current is None or current == "" or current == [] or current == {}:
        raise ReviewError(f"empty product_facts reference: {reference}")
    return current


def _assert_review_shape(review: Any) -> dict[str, Any]:
    if not isinstance(review, dict):
        raise ReviewError("review JSON must be an object")
    unknown = set(review) - REVIEW_KEYS
    if unknown:
        raise ReviewError(f"unknown review fields: {sorted(unknown)}")
    missing = REVIEW_KEYS - set(review)
    if missing:
        raise ReviewError(f"missing review fields: {sorted(missing)}")
    if review.get("schema_version") != 1:
        raise ReviewError("schema_version must be 1")
    if not _text(review.get("payload_fingerprint")):
        raise ReviewError("payload_fingerprint is required")
    if not _text(review.get("review_json")):
        raise ReviewError("review_json must be a nonempty string")
    if not isinstance(review.get("verdicts"), list):
        raise ReviewError("review JSON must contain a verdicts array")
    return review


def _assert_verdict_shape(item: Any, product_facts: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ReviewError("each verdict must be an object")
    unknown = set(item) - VERDICT_KEYS
    if unknown:
        raise ReviewError(f"unknown verdict fields: {sorted(unknown)}")
    required = {"candidate_id", "verdict", "reason", "factual_evidence"}
    missing = required - set(item)
    if missing:
        raise ReviewError(f"missing verdict fields: {sorted(missing)}")
    candidate_id = _text(item.get("candidate_id"))
    verdict = _text(item.get("verdict"))
    reason = _text(item.get("reason"))
    evidence = item.get("factual_evidence")
    if not candidate_id or verdict not in VERDICTS or not reason:
        raise ReviewError("verdict requires candidate_id, approve/reject/review, and a nonempty reason")
    if not isinstance(evidence, list) or not evidence or any(not isinstance(value, str) or not value.strip() for value in evidence):
        raise ReviewError("factual_evidence must be a nonempty list of nonempty strings")
    for reference in evidence:
        _resolve_product_fact(product_facts, reference.strip())
    if "hard_mismatch" in item and not isinstance(item["hard_mismatch"], bool):
        raise ReviewError("hard_mismatch must be boolean")
    checked = item.get("supporting_terms_checked")
    if checked is not None and (
        not isinstance(checked, list)
        or any(not isinstance(value, str) or not value.strip() for value in checked)
    ):
        raise ReviewError("supporting_terms_checked must be a list of nonempty strings")
    return item


def _candidate_is_protected(payload: dict[str, Any], row: dict[str, Any]) -> str | None:
    identity = RENDERER._semantic_identity(row)
    query = _normalized(identity["query"])
    is_root = identity["candidate_kind"] == "root_cluster"
    campaign = _normalized(identity["campaign"])
    ad_group = _normalized(identity["ad_group"])
    for record in (payload.get("semantic_review") or {}).get("protection_records", []) or []:
        if not isinstance(record, dict):
            continue
        protected_term = _normalized(record.get("term"))
        if not protected_term:
            continue
        record_campaign = _normalized(record.get("campaign"))
        record_group = _normalized(record.get("ad_group"))
        if record_campaign and record_campaign != campaign:
            continue
        if record_group and record_group != ad_group:
            continue
        if protected_term == query or (is_root and query and query in protected_term):
            return _text(record.get("reason")) or "protected term"
    return None


def _validate_candidate_scope(candidate_id: str, row: dict[str, Any]) -> None:
    campaign, ad_group, target, family = _scope(row)
    if not all(_text(value) for value in (campaign, ad_group, target, family)):
        raise ReviewError(f"{candidate_id}: executable scope must include campaign, ad group, source target, and match family")
    if family not in {"broad", "phrase"}:
        raise ReviewError(f"{candidate_id}: executable negatives require original Broad/Phrase source scope")


def _validate_verdict(payload: dict[str, Any], row: dict[str, Any], verdict: dict[str, Any]) -> None:
    if verdict["verdict"] != "approve":
        return
    candidate_id = verdict["candidate_id"]
    semantic_family = _text(row.get("semantic_family"))
    if semantic_family not in NEGATIVE_FAMILIES:
        raise ReviewError(f"{candidate_id}: {semantic_family or 'unclassified'} candidates cannot become negatives")
    _validate_candidate_scope(candidate_id, row)
    identity = RENDERER._semantic_identity(row)
    if semantic_family == "negative_exact" and identity["candidate_kind"] != "search_term":
        raise ReviewError(f"{candidate_id}: Negative Exact requires a search-term candidate")
    if semantic_family == "negative_phrase" and identity["candidate_kind"] != "root_cluster":
        raise ReviewError(f"{candidate_id}: Negative Phrase requires a root candidate")
    tier = _text(row.get("negative_decision_tier"))
    if tier not in ALLOWED_NEGATIVE_TIERS[semantic_family]:
        raise ReviewError(f"{candidate_id}: unexpected negative_decision_tier {tier or '<missing>'}")
    if semantic_family == "negative_exact" and row.get("negative_source_eligible") is not True:
        raise ReviewError(f"{candidate_id}: Negative Exact requires negative_source_eligible=true")
    if row.get("negative_source_eligible") is False:
        raise ReviewError(f"{candidate_id}: explicitly ineligible negative source cannot be approved")
    if row.get("protected") or row.get("root_protected"):
        raise ReviewError(f"{candidate_id}: protected candidate cannot be approved")
    if _text(row.get("negative_decision_tier")) in {"protected_review_only", "protected"}:
        raise ReviewError(f"{candidate_id}: protected decision tier cannot be approved")
    if any(float(row.get(key) or 0) > 0 for key in ("orders", "recent_orders", "historical_orders", "supporting_orders")):
        raise ReviewError(f"{candidate_id}: converting or historical candidate cannot be approved")
    if row.get("is_asin") or row.get("product_targeting") or _looks_like_product_target(row):
        raise ReviewError(f"{candidate_id}: ASIN/product target cannot become a keyword negative")
    protected = _candidate_is_protected(payload, row)
    if protected:
        raise ReviewError(f"{candidate_id}: cannot approve protected/conflicting term ({protected})")
    if row.get("global_negative_blocked"):
        raise ReviewError(f"{candidate_id}: global-negative conflict is not executable")

    requires_hard_mismatch = (
        tier in {"negative_candidate_review", "semantic_mismatch_only_low_sample", "root_negative_phrase_review"}
        or identity["candidate_kind"] == "root_cluster"
    )
    if requires_hard_mismatch and verdict.get("hard_mismatch") is not True:
        raise ReviewError(f"{candidate_id}: approval requires hard_mismatch=true")

    if identity["candidate_kind"] == "root_cluster":
        expected = row.get("supporting_terms")
        checked = verdict.get("supporting_terms_checked")
        if not isinstance(expected, list) or not expected:
            raise ReviewError(f"{candidate_id}: root candidate is missing complete supporting terms")
        if checked != expected:
            raise ReviewError(f"{candidate_id}: supporting_terms_checked must exactly match the complete supporting_terms list")
        if row.get("root_protected") or row.get("protected_source_terms"):
            raise ReviewError(f"{candidate_id}: protected root cannot be approved")
    elif "supporting_terms_checked" in verdict:
        raise ReviewError(f"{candidate_id}: supporting_terms_checked is only valid for root candidates")


def _validated_engine_negatives(payload: dict[str, Any]) -> list[dict[str, Any]]:
    approved: list[dict[str, Any]] = []
    rows = ((payload.get("sections") or {}).get("section_5") or {}).get("engine_approved_negatives") or []
    for row in rows:
        if not isinstance(row, dict):
            raise ReviewError("engine-approved negative must be an object")
        gate = row.get("engine_gate")
        if not isinstance(gate, dict):
            raise ReviewError("conditional isolation negative lacks complete engine gate data")
        required = {
            "status", "keyword", "exact_group", "minimum_days", "minimum_orders",
            "requires_profit_economics", "requires_exact_coverage", "scope", "actual_days",
            "actual_orders", "profit_economics_met", "exact_coverage_confirmed", "ready",
        }
        if required - set(gate):
            raise ReviewError("conditional isolation negative lacks complete engine gate data")
        try:
            minimum_days = float(gate.get("minimum_days"))
            actual_days = float(gate.get("actual_days"))
            minimum_orders = float(gate.get("minimum_orders"))
            actual_orders = float(gate.get("actual_orders"))
            gate_ready = (
                gate.get("status") == "ready"
                and gate.get("ready") is True
                and _text(gate.get("exact_group"))
                and minimum_days > 0
                and actual_days >= minimum_days
                and minimum_orders > 0
                and actual_orders >= minimum_orders
                and gate.get("requires_profit_economics") is True
                and gate.get("profit_economics_met") is True
                and gate.get("requires_exact_coverage") is True
                and gate.get("exact_coverage_confirmed") is True
                and _text(gate.get("scope"))
                and _text(row.get("search_term"))
            )
        except (TypeError, ValueError):
            gate_ready = False
        if not gate_ready:
            raise ReviewError("conditional isolation negative is not ready")
        term = _normalized(row.get("search_term"))
        for record in (payload.get("semantic_review") or {}).get("protection_records", []) or []:
            if isinstance(record, dict) and _normalized(record.get("term")) == term:
                raise ReviewError("conditional isolation negative conflicts with a protected term")
        approved.append(copy.deepcopy(row))
    return approved


def validate_review(payload: dict[str, Any], review: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    review = _assert_review_shape(review)
    semantic = payload.get("semantic_review")
    if not isinstance(semantic, dict):
        raise ReviewError("payload is missing semantic_review")
    stored = _text(semantic.get("payload_fingerprint"))
    recomputed = RENDERER.compute_payload_fingerprint(payload)
    if not stored or stored != recomputed or review.get("payload_fingerprint") != recomputed:
        raise ReviewError("payload fingerprint mismatch")
    candidates = semantic.get("candidate_terms")
    if not isinstance(candidates, list) or any(not isinstance(row, dict) for row in candidates):
        raise ReviewError("semantic candidates must be an array of objects")
    by_id: dict[str, dict[str, Any]] = {}
    for row in candidates:
        candidate_id = _text(row.get("candidate_id"))
        if not candidate_id or candidate_id in by_id:
            raise ReviewError("semantic candidates must have unique candidate_id values")
        if candidate_id != RENDERER._semantic_candidate_id(row):
            raise ReviewError(f"candidate identity mismatch: {candidate_id}")
        by_id[candidate_id] = row

    verdicts: dict[str, dict[str, Any]] = {}
    product_facts = payload.get("product_facts")
    if not isinstance(product_facts, dict) or not product_facts:
        raise ReviewError("payload requires nonempty product_facts")
    for raw in review["verdicts"]:
        item = _assert_verdict_shape(raw, product_facts)
        candidate_id = item["candidate_id"]
        if candidate_id in verdicts:
            raise ReviewError(f"duplicate verdict: {candidate_id}")
        if candidate_id not in by_id:
            raise ReviewError(f"unknown candidate_id: {candidate_id}")
        verdicts[candidate_id] = item
    missing = sorted(set(by_id) - set(verdicts))
    if missing:
        raise ReviewError(f"missing verdicts: {', '.join(missing)}")
    for candidate_id, item in verdicts.items():
        _validate_verdict(payload, by_id[candidate_id], item)

    approved = _validated_engine_negatives(payload)
    for candidate_id in [row["candidate_id"] for row in candidates]:
        item = verdicts[candidate_id]
        if item["verdict"] != "approve":
            continue
        row = by_id[candidate_id]
        identity = RENDERER._semantic_identity(row)
        approved.append({
            "negative_group": row.get("negative_group") or f"SEM-{candidate_id[4:12]}",
            "scope": "/".join((identity["campaign"], identity["ad_group"])),
            "scope_level": "ad_group",
            "source_match_family": identity["match_family"],
            "negative_type": "否定词组" if identity["candidate_kind"] == "root_cluster" else "否定精准",
            "search_term": row.get("root") if identity["candidate_kind"] == "root_cluster" else identity["query"],
            "candidate_id": candidate_id,
            "reason": item["reason"],
            "factual_evidence": item["factual_evidence"],
        })

    output = copy.deepcopy(payload)
    output["sections"]["section_5"]["approved_negatives"] = approved
    output["semantic_review"]["finalization"] = {
        "status": "finalized",
        "review_json": review.get("review_json") or "semantic_review.json",
        "approved_negative_count": len(approved),
    }
    output["audit_decision"] = {"schema_version": 1, "verdicts": verdicts}
    output["semantic_review"]["payload_fingerprint"] = recomputed
    return output, approved


def _stage_text(path: Path, text: str) -> Path:
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        return Path(temp_name)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _write_synchronized(files: dict[Path, str]) -> None:
    originals = {path: path.read_bytes() if path.exists() else None for path in files}
    staged: dict[Path, Path] = {}
    replaced: list[Path] = []
    try:
        for path, text in files.items():
            staged[path] = _stage_text(path, text)
        for path in files:
            os.replace(staged[path], path)
            replaced.append(path)
    except Exception:
        for path in replaced:
            original = originals[path]
            if original is None:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            else:
                path.write_bytes(original)
        raise
    finally:
        for temp_path in staged.values():
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def _before_section_three(text: str) -> str:
    return text.split("\n## 3. ", 1)[0]


def finalize(output_dir: Path, review_path: Path) -> dict[str, str]:
    payload_path = output_dir / "report_payload.json"
    draft_path = output_dir / "report_draft.md"
    full_path = output_dir / "report_full.md"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    review = json.loads(review_path.read_text(encoding="utf-8"))
    finalized, _ = validate_review(payload, review)
    payload_text = json.dumps(finalized, ensure_ascii=False, separators=(",", ":"))
    draft_text = RENDERER.render_report(finalized)
    full_text = RENDERER.render_full_report(finalized)
    if finalized.get("sections", {}).get("section_1") != payload.get("sections", {}).get("section_1"):
        raise ReviewError("finalization changed Section 1 data")
    if finalized.get("sections", {}).get("section_2") != payload.get("sections", {}).get("section_2"):
        raise ReviewError("finalization changed Section 2 data")
    if _before_section_three(draft_text) != _before_section_three(RENDERER.render_report(payload)):
        raise ReviewError("finalization changed rendered Section 1/2")
    if _before_section_three(full_text) != _before_section_three(RENDERER.render_full_report(payload)):
        raise ReviewError("finalization changed full-report Section 1/2")
    _write_synchronized({payload_path: payload_text, draft_path: draft_text, full_path: full_text})
    return {
        "report_payload": str(payload_path),
        "report_draft": str(draft_path),
        "report_full": str(full_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("review_json", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(finalize(args.output_dir, args.review_json), ensure_ascii=False, indent=2))
    except (OSError, ValueError, json.JSONDecodeError, ReviewError) as exc:
        print(json.dumps({"status": "review_invalid", "error": str(exc)}, ensure_ascii=False))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
