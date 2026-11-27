from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_DIR = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RENDERER = load("semantic_renderer", SKILL_DIR / "scripts" / "render_amazon_report.py")
FINALIZER = load("semantic_finalizer", SKILL_DIR / "scripts" / "finalize_amazon_report.py")


def candidate(term: str = "cactus sink strainer", **overrides) -> dict:
    row = {
        "search_term": term,
        "candidate_kind": "search_term",
        "negative_type": "negative_exact",
        "semantic_family": "negative_exact",
        "negative_decision_tier": "direct_negative_exact_after_semantic_safety_check",
        "source_campaign": "Phrase Campaign",
        "source_ad_group": "Phrase Group",
        "source_targeting": "sink strainer",
        "source_match_type": "phrase",
        "source_family": "phrase",
        "negative_source_eligible": True,
        "orders": 0,
    }
    row.update(overrides)
    row["candidate_id"] = RENDERER._semantic_candidate_id(row)
    return row


def payload_with(rows: list[dict], *, protections: list[dict] | None = None) -> dict:
    payload = {
        "payload_version": 2,
        "product_facts": {
            "title": "Kitchen sink drain strainer",
            "compatibility": ["standard kitchen sink drains"],
        },
        "placement_enabled": False,
        "sections": {
            "section_1": {"rows": [{"search_term": "kept section one"}], "total_converting_terms": 1},
            "section_2": {"rows": [{"search_term": "kept section two"}], "all_rows": []},
            "section_3": {"new_campaign_plan": {"rows": []}},
            "section_5": {"approved_negatives": [], "engine_approved_negatives": []},
        },
        "semantic_review": {
            "candidate_terms": rows,
            "protection_records": protections or [],
            "finalization": {"status": "pending_review", "review_json": "semantic_review.json", "approved_negative_count": 0},
        },
    }
    payload["semantic_review"]["payload_fingerprint"] = RENDERER.compute_payload_fingerprint(payload)
    return payload


def review_for(payload: dict, *, approve: set[str] | None = None) -> dict:
    approve = approve or set()
    verdicts = []
    for row in payload["semantic_review"]["candidate_terms"]:
        item = {
            "candidate_id": row["candidate_id"],
            "verdict": "approve" if row["candidate_id"] in approve else "reject",
            "reason": "Product facts establish the semantic decision.",
            "factual_evidence": ["product_facts.title"],
        }
        if item["verdict"] == "approve" and (
            row.get("candidate_kind") == "root_cluster"
            or row.get("negative_decision_tier") in {
                "negative_candidate_review", "semantic_mismatch_only_low_sample", "root_negative_phrase_review",
            }
        ):
            item["hard_mismatch"] = True
        if row.get("candidate_kind") == "root_cluster" and item["verdict"] == "approve":
            item["supporting_terms_checked"] = row["supporting_terms"]
        verdicts.append(item)
    return {
        "schema_version": 1,
        "payload_fingerprint": payload["semantic_review"]["payload_fingerprint"],
        "review_json": "semantic_review.json",
        "verdicts": verdicts,
    }


class CandidateCoverageTests(unittest.TestCase):
    def test_renderer_keeps_more_than_eighty_candidates_and_scoped_identities(self) -> None:
        diagnostics = [
            {
                "search_term": f"diagnostic term {index}",
                "candidate_kind": "search_term",
                "campaign": "C",
                "ad_group": "G",
                "source_targeting": "T",
                "source_match_type": "phrase",
            }
            for index in range(81)
        ]
        same_word = [
            {
                "search_term": "same word",
                "candidate_kind": "search_term",
                "negative_type": "negative_exact",
                "campaign": "C",
                "ad_group": "G",
                "source_targeting": target,
                "source_match_type": family,
            }
            for target, family in [("T1", "phrase"), ("T2", "phrase"), ("T1", "broad")]
        ]
        payload = RENDERER.build_report_payload({
            "candidate_actions": {
                "diagnostic_candidates": diagnostics,
                "negative_exact_review_candidates": same_word,
            },
        })
        rows = payload["semantic_review"]["candidate_terms"]
        self.assertEqual(len(rows), 84)
        self.assertEqual(len({row["candidate_id"] for row in rows}), 84)
        self.assertEqual(sum(row["search_term"] == "same word" for row in rows), 3)

    def test_pending_report_never_renders_engine_or_legacy_negatives(self) -> None:
        payload = payload_with([])
        payload["sections"]["section_5"]["approved_negatives"] = [{"search_term": "legacy unsafe"}]
        payload["sections"]["section_5"]["engine_approved_negatives"] = [{"search_term": "engine unsafe"}]
        report = RENDERER.render_report(payload).split("## 3.", 1)[1]
        self.assertNotIn("legacy unsafe", report)
        self.assertNotIn("engine unsafe", report)
        self.assertIn("当前不执行否定词", report)


class ReviewSchemaTests(unittest.TestCase):
    def test_review_json_is_required(self) -> None:
        row = candidate()
        payload = payload_with([row])
        review = review_for(payload)
        del review["review_json"]
        with self.assertRaises(FINALIZER.ReviewError):
            FINALIZER.validate_review(payload, review)

    def test_rejects_unknown_duplicate_missing_ids_fields_and_stale_payloads(self) -> None:
        row = candidate()
        payload = payload_with([row])
        base = review_for(payload)
        invalid = []
        unknown_field = copy.deepcopy(base)
        unknown_field["unexpected"] = True
        invalid.append(unknown_field)
        unknown_id = copy.deepcopy(base)
        unknown_id["verdicts"][0]["candidate_id"] = "sem_unknown"
        invalid.append(unknown_id)
        duplicate = copy.deepcopy(base)
        duplicate["verdicts"].append(copy.deepcopy(duplicate["verdicts"][0]))
        invalid.append(duplicate)
        missing = copy.deepcopy(base)
        missing["verdicts"] = []
        invalid.append(missing)
        missing_verdict = copy.deepcopy(base)
        del missing_verdict["verdicts"][0]["verdict"]
        invalid.append(missing_verdict)
        fake_evidence = copy.deepcopy(base)
        fake_evidence["verdicts"][0]["factual_evidence"] = ["the product is unrelated"]
        invalid.append(fake_evidence)
        stale = copy.deepcopy(base)
        stale["payload_fingerprint"] = "sha256:stale"
        invalid.append(stale)
        tampered_payload = copy.deepcopy(payload)
        tampered_payload["sections"]["section_1"]["rows"][0]["search_term"] = "tampered"
        with self.assertRaises(FINALIZER.ReviewError):
            FINALIZER.validate_review(tampered_payload, base)
        for review in invalid:
            with self.subTest(review=review):
                with self.assertRaises(FINALIZER.ReviewError):
                    FINALIZER.validate_review(payload, review)

    def test_illegal_approvals_fail_closed(self) -> None:
        cases = [
            {"protected": True},
            {"root_protected": True},
            {"orders": 1},
            {"historical_orders": 1},
            {"source_match_type": "exact", "source_family": "exact"},
            {"source_targeting": ""},
            {"is_asin": True},
            {
                "search_term": "B012345678",
                "negative_decision_tier": "semantic_mismatch_only_low_sample",
            },
            {"source_targeting": "asin=B012345678"},
            {"negative_source_eligible": False},
            {"negative_decision_tier": "unexpected_tier"},
            {"semantic_family": "diagnostic"},
            {"semantic_family": "controlled_exploration"},
        ]
        for overrides in cases:
            row = candidate(**overrides)
            payload = payload_with([row])
            review = review_for(payload, approve={row["candidate_id"]})
            with self.subTest(overrides=overrides):
                with self.assertRaises(FINALIZER.ReviewError):
                    FINALIZER.validate_review(payload, review)

    def test_review_tier_and_root_require_hard_mismatch_and_complete_terms(self) -> None:
        review_row = candidate(negative_decision_tier="negative_candidate_review")
        payload = payload_with([review_row])
        review = review_for(payload, approve={review_row["candidate_id"]})
        del review["verdicts"][0]["hard_mismatch"]
        with self.assertRaises(FINALIZER.ReviewError):
            FINALIZER.validate_review(payload, review)

        ineligible_root = candidate(
            term="unsafe root",
            root="unsafe root",
            candidate_kind="root_cluster",
            negative_type="negative_phrase",
            semantic_family="negative_phrase",
            negative_decision_tier="root_negative_phrase_review",
            negative_source_eligible=False,
            supporting_terms=["unsafe root one", "unsafe root two"],
        )
        payload = payload_with([ineligible_root])
        review = review_for(payload, approve={ineligible_root["candidate_id"]})
        with self.assertRaises(FINALIZER.ReviewError):
            FINALIZER.validate_review(payload, review)

        root = candidate(
            term="cactus",
            root="cactus",
            candidate_kind="root_cluster",
            negative_type="negative_phrase",
            semantic_family="negative_phrase",
            negative_decision_tier="root_negative_phrase_review",
            supporting_terms=["cactus sink basket", "cactus sink stopper"],
        )
        payload = payload_with([root])
        review = review_for(payload, approve={root["candidate_id"]})
        review["verdicts"][0]["supporting_terms_checked"] = ["cactus sink basket"]
        with self.assertRaises(FINALIZER.ReviewError):
            FINALIZER.validate_review(payload, review)

    def test_same_group_protection_blocks_broader_keyword_negative(self) -> None:
        row = candidate(term="cactus")
        payload = payload_with([row], protections=[{
            "term": "cactus",
            "reason": "exact_candidate",
            "campaign": "Phrase Campaign",
            "ad_group": "Phrase Group",
            "match_family": "exact",
        }])
        with self.assertRaises(FINALIZER.ReviewError):
            FINALIZER.validate_review(payload, review_for(payload, approve={row["candidate_id"]}))


class FinalizationTests(unittest.TestCase):
    def _write_report(self, root: Path, payload: dict) -> Path:
        (root / "report_payload.json").write_text(json.dumps(payload), encoding="utf-8")
        (root / "report_draft.md").write_text(RENDERER.render_report(payload), encoding="utf-8")
        (root / "report_full.md").write_text(RENDERER.render_full_report(payload), encoding="utf-8")
        review_path = root / "review.json"
        review_path.write_text(json.dumps(review_for(payload)), encoding="utf-8")
        return review_path

    def test_finalization_is_idempotent_and_preserves_section_one_and_two(self) -> None:
        row = candidate()
        payload = payload_with([row])
        review = review_for(payload, approve={row["candidate_id"]})
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            review_path = self._write_report(root, payload)
            review_path.write_text(json.dumps(review), encoding="utf-8")
            before_1 = copy.deepcopy(payload["sections"]["section_1"])
            before_2 = copy.deepcopy(payload["sections"]["section_2"])
            FINALIZER.finalize(root, review_path)
            first = {path.name: path.read_bytes() for path in root.glob("report_*")}
            FINALIZER.finalize(root, review_path)
            second = {path.name: path.read_bytes() for path in root.glob("report_*")}
            finalized = json.loads((root / "report_payload.json").read_text(encoding="utf-8"))
            self.assertEqual(first, second)
            self.assertEqual(finalized["sections"]["section_1"], before_1)
            self.assertEqual(finalized["sections"]["section_2"], before_2)
            self.assertIn("cactus sink strainer", (root / "report_draft.md").read_text(encoding="utf-8"))

    def test_validation_and_caught_write_failure_leave_all_reports_unchanged(self) -> None:
        row = candidate()
        payload = payload_with([row])
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            review_path = self._write_report(root, payload)
            originals = {path.name: path.read_bytes() for path in root.glob("report_*")}
            invalid = review_for(payload)
            invalid["payload_fingerprint"] = "sha256:bad"
            review_path.write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaises(FINALIZER.ReviewError):
                FINALIZER.finalize(root, review_path)
            self.assertEqual(originals, {path.name: path.read_bytes() for path in root.glob("report_*")})

            review_path.write_text(json.dumps(review_for(payload)), encoding="utf-8")
            real_replace = FINALIZER.os.replace
            calls = 0

            def fail_second(source, destination):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("simulated replace failure")
                return real_replace(source, destination)

            with patch.object(FINALIZER.os, "replace", side_effect=fail_second):
                with self.assertRaises(OSError):
                    FINALIZER.finalize(root, review_path)
            self.assertEqual(originals, {path.name: path.read_bytes() for path in root.glob("report_*")})

    def test_incomplete_conditional_isolation_gate_never_autoapproves(self) -> None:
        payload = payload_with([])
        payload["sections"]["section_5"]["engine_approved_negatives"] = [{
            "search_term": "isolation term",
            "engine_gate": {"ready": True},
        }]
        payload["semantic_review"]["payload_fingerprint"] = RENDERER.compute_payload_fingerprint(payload)
        with self.assertRaises(FINALIZER.ReviewError):
            FINALIZER.validate_review(payload, review_for(payload))

    def test_isolation_gate_uses_engine_emitted_thresholds(self) -> None:
        payload = payload_with([])
        payload["sections"]["section_5"]["engine_approved_negatives"] = [{
            "search_term": "isolation term",
            "engine_gate": {
                "status": "ready",
                "keyword": "isolation term",
                "exact_group": "Exact Group",
                "minimum_days": 3,
                "minimum_orders": 1,
                "requires_profit_economics": True,
                "requires_exact_coverage": True,
                "scope": "Phrase Group",
                "actual_days": 3,
                "actual_orders": 1,
                "profit_economics_met": True,
                "exact_coverage_confirmed": True,
                "ready": True,
            },
        }]
        payload["semantic_review"]["payload_fingerprint"] = RENDERER.compute_payload_fingerprint(payload)
        finalized, approved = FINALIZER.validate_review(payload, review_for(payload))
        self.assertEqual(len(approved), 1)
        self.assertEqual(finalized["semantic_review"]["finalization"]["status"], "finalized")


class DocumentationTests(unittest.TestCase):
    def test_skill_and_contract_describe_same_finalizer_workflow(self) -> None:
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        contract = (SKILL_DIR / "references" / "report_contract.md").read_text(encoding="utf-8")
        command = "scripts/finalize_amazon_report.py"
        self.assertIn(command, skill)
        self.assertIn(command, contract)
        self.assertIn("supporting_terms_checked", contract)
        self.assertIn("candidate_id", contract)
        self.assertIn("pending", skill.casefold())
        self.assertIn("required decision metric cell is empty", skill)


if __name__ == "__main__":
    unittest.main()
