from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]


class PipelineRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ads-pipeline-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.product = {
            "key": "p1", "asin": "B012345678", "sku": "SKU-1",
            "marketplace_default": "US", "target_acos": "30%",
            "average_order_value": 20, "gross_margin_range": "40%",
            "baseline_cvr": "10%", "title": "Stainless kitchen strainer",
            "core_category": "kitchen sink strainer",
        }

    def run_report(self, rows, *extra):
        source = self.root / "report.csv"
        with source.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "Search Term", "Orders", "Spend", "Clicks", "Impressions", "Sales",
                "Campaign", "Ad Group", "Match Type", "Targeting", "Bid", "ASIN", "SKU",
            ])
            writer.writerows(rows)
        database = self.root / "products.json"
        database.write_text(json.dumps({"products": [self.product]}), encoding="utf-8")
        output = self.root / "out"
        process = subprocess.run([
            sys.executable, "-B", str(SKILL_DIR / "scripts/prepare_amazon_analysis.py"),
            "--ad-file", str(source), "--product-db", str(database),
            "--output-dir", str(output), *extra,
        ], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        return json.loads(process.stdout), output

    @staticmethod
    def row(term, *, orders=0, spend=1, clicks=2, campaign="Broad", match="Broad", sku="SKU-1"):
        return [term, orders, spend, clicks, 100, orders * 20,
                campaign, "Main", match, "kitchen strainer", 0.4, "B012345678", sku]

    def test_real_cli_preserves_every_semantic_source_scope_above_eighty(self):
        rows = [self.row(f"accessory code {index}") for index in range(90)]
        rows.extend([
            self.row("shared accessory", campaign="First"),
            self.row("shared accessory", campaign="Second"),
        ])
        summary, output = self.run_report(rows)
        self.assertNotIn("action_required", summary)
        payload = json.loads((output / "report_payload.json").read_text())
        candidates = payload["semantic_review"]["candidate_terms"]
        scope_pairs = {
            (row["search_term"], row.get("source_campaign") or row.get("campaign"))
            for row in candidates if row.get("candidate_kind") == "search_term"
        }
        self.assertTrue({(row[0], row[6]) for row in rows}.issubset(scope_pairs))
        ids = [row["candidate_id"] for row in candidates]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(payload["semantic_review"]["finalization"]["status"], "pending_review")
        self.assertEqual(payload["sections"]["section_5"]["approved_negatives"], [])

    def test_real_cli_rejects_blank_required_metric_before_report_output(self):
        summary, output = self.run_report([self.row("accessory", orders="")])
        self.assertEqual(summary.get("action_required"), "fix_invalid_numeric_value")
        self.assertFalse((output / "report_draft.md").exists())
        self.assertIn("report.csv", summary["action_message"])

    def test_real_cli_corrects_explicit_small_percentage(self):
        summary, output = self.run_report([
            self.row("kitchen strainer", orders=2, spend=2, clicks=10, match="Exact"),
        ], "--baseline-cvr", "0.5%")
        self.assertNotIn("action_required", summary)
        package = json.loads((output / "analysis_package.json").read_text())
        self.assertEqual(package["analysis_context"]["cvr_baseline"]["value"], 0.005)

    def test_real_cli_rejects_wrong_money_unit(self):
        summary, output = self.run_report([self.row("accessory")], "--average-order-value", "30%")
        self.assertEqual(summary.get("action_required"), "fix_product_economics")
        self.assertFalse((output / "report_draft.md").exists())

    def test_real_cli_rejects_unknown_sku_even_with_known_asin(self):
        summary, output = self.run_report([self.row("accessory", sku="UNKNOWN-SKU")])
        self.assertEqual(summary.get("action_required"), "fix_product_identifier_conflict")
        self.assertFalse((output / "report_draft.md").exists())

    def test_real_cli_finalizes_identical_negatives_in_both_reports(self):
        summary, output = self.run_report([self.row("incompatible spare part")])
        self.assertNotIn("action_required", summary)
        payload = json.loads((output / "report_payload.json").read_text())
        review = {
            "schema_version": 1,
            "payload_fingerprint": payload["semantic_review"]["payload_fingerprint"],
            "review_json": "semantic_review.json",
            "verdicts": [
                {
                    "candidate_id": row["candidate_id"],
                    "verdict": "approve" if row.get("semantic_family") == "negative_exact" else "review",
                    "reason": "Synthetic integration test verdict, not a real advertising recommendation.",
                    "factual_evidence": ["product_facts.title"],
                    "hard_mismatch": True,
                }
                for row in payload["semantic_review"]["candidate_terms"]
            ],
        }
        review_path = output / "review.json"
        review_path.write_text(json.dumps(review), encoding="utf-8")
        files = [output / name for name in ("report_payload.json", "report_draft.md", "report_full.md")]
        before = {path: path.read_bytes() for path in files}
        command = [sys.executable, "-B", str(SKILL_DIR / "scripts/finalize_amazon_report.py"),
                   str(output), str(review_path)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
        finalized = json.loads(files[0].read_text())
        self.assertEqual(finalized["semantic_review"]["finalization"]["status"], "finalized")
        self.assertTrue(finalized["sections"]["section_5"]["approved_negatives"])
        for path in files[1:]:
            self.assertEqual(before[path].split(b"\n## 3.")[0], path.read_bytes().split(b"\n## 3.")[0])
        self.assertEqual(files[1].read_bytes().split(b"\n## 3.")[1],
                         files[2].read_bytes().split(b"\n## 3.")[1])
        first = {path: path.read_bytes() for path in files}
        repeated = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(repeated.returncode, 0, (repeated.stdout, repeated.stderr))
        self.assertEqual(first, {path: path.read_bytes() for path in files})

        review["verdicts"][0]["scope"] = "global"
        review_path.write_text(json.dumps(review), encoding="utf-8")
        rejected = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(json.loads(rejected.stdout)["status"], "review_invalid")
        self.assertEqual(first, {path: path.read_bytes() for path in files})

    def test_mixed_diagnostics_preserve_hidden_exact_source_protection(self):
        rows = [
            self.row(f"profitable product {index}", orders=3, spend=20, clicks=30,
                     campaign=f"Campaign {index}", match="Exact")
            for index in range(35)
        ]
        rows.extend([
            self.row("mystery attachment", campaign="Shared", match="Exact", spend=0.1, clicks=1),
            self.row("mystery attachment", campaign="Shared", match="Broad", spend=12, clicks=12),
        ])
        summary, output = self.run_report(rows)
        self.assertNotIn("action_required", summary)
        payload = json.loads((output / "report_payload.json").read_text())
        candidates = payload["semantic_review"]["candidate_terms"]
        self.assertEqual(sum(payload["semantic_review"]["candidate_counts_by_kind"].values()), len(candidates))
        target = next(row for row in candidates if row.get("search_term") == "mystery attachment"
                      and row.get("semantic_family") == "negative_exact")
        review = {
            "schema_version": 1,
            "payload_fingerprint": payload["semantic_review"]["payload_fingerprint"],
            "review_json": "semantic_review.json",
            "verdicts": [
                {
                    "candidate_id": row["candidate_id"],
                    "verdict": "approve" if row["candidate_id"] == target["candidate_id"] else "review",
                    "reason": "Synthetic attempt to negate a query also served by Exact in the same group.",
                    "factual_evidence": ["product_facts.title"],
                    "hard_mismatch": True,
                }
                for row in candidates
            ],
        }
        review_path = output / "review.json"
        review_path.write_text(json.dumps(review), encoding="utf-8")
        process = subprocess.run([
            sys.executable, "-B", str(SKILL_DIR / "scripts/finalize_amazon_report.py"),
            str(output), str(review_path),
        ], capture_output=True, text=True)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("protected", json.loads(process.stdout)["error"])


if __name__ == "__main__":
    unittest.main()
