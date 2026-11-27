from __future__ import annotations

import importlib.util
import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


SKILL_DIR = Path(__file__).resolve().parents[1]


def load_module(name: str, relative_path: str):
    path = SKILL_DIR / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


INPUTS = load_module("amazon_ads_input_values_regressions", "scripts/input_values.py")
PREPARE = load_module("amazon_ads_prepare_regressions", "scripts/prepare_amazon_analysis.py")
PRODUCT_DB = load_module("amazon_ads_product_db_regressions", "scripts/product_db.py")


class UnitParsingRegressions(unittest.TestCase):
    def test_explicit_small_percentages_use_percent_units(self) -> None:
        self.assertEqual(INPUTS.parse_unit_value("0,125%", kind="rate"), (0.125, True))
        self.assertEqual(INPUTS.parse_unit_value("0,001%", kind="rate"), (0.001, True))
        self.assertAlmostEqual(INPUTS.normalize_rate("0,125%"), 0.00125)
        self.assertAlmostEqual(INPUTS.normalize_rate("0,001%"), 0.00001)
        margin = PREPARE.parse_margin_range("0.5%-1%")
        self.assertEqual((margin["min"], margin["max"]), (0.005, 0.01))

    def test_wrong_units_and_duplicate_currency_are_rejected(self) -> None:
        for value, kind in [
            ("10%", "money"),
            ("$0.25", "rate"),
            ("$3", "count"),
            ("3%", "count"),
            ("$30$", "money"),
            ("USD 30 EUR", "money"),
        ]:
            with self.subTest(value=value, kind=kind), self.assertRaises(ValueError):
                INPUTS.parse_unit_value(value, kind=kind)
        with self.assertRaises(ValueError):
            PREPARE.parse_positive_midpoint("10%", "average_order_value")
        with self.assertRaises(ValueError):
            PREPARE.parse_rate("$0.25", "target_acos")

    def test_required_blank_metrics_and_wrong_report_units_hard_stop(self) -> None:
        for column, value in [
            ("Orders", ""),
            ("Spend", None),
            ("Clicks", "N/A"),
            ("Orders", "1%"),
            ("Spend", "10%"),
            ("Clicks", "$2"),
        ]:
            headers = ["Search Term", "Orders", "Spend", "Clicks"]
            row = ["term", 1, 2, 3]
            row[headers.index(column)] = value
            with self.subTest(column=column, value=value), self.assertRaises(PREPARE.ActionRequired):
                PREPARE.rows_to_tables("Report", [headers, row], "US", source_file="bad.csv")

    def test_product_db_explicit_missing_ranges_reject_without_write(self) -> None:
        for field_name, as_rate in [
            ("average_order_value", False),
            ("gross_margin_range", True),
        ]:
            for value in ["", "N/A", "null"]:
                with self.subTest(field=field_name, value=value), self.assertRaises(SystemExit):
                    PRODUCT_DB.validate_range_argument(
                        value,
                        field_name,
                        as_rate=as_rate,
                    )

        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "products.json"
            original = {"products": [{"key": "one", "asin": "B012345678"}]}
            db_path.write_text(json.dumps(original), encoding="utf-8")
            base_args = {
                "db": db_path,
                "key": "one",
                "asin": None,
                "sku": None,
                "alias": None,
                "marketplace": None,
                "target_acos": None,
                "average_order_value": None,
                "gross_margin_range": None,
                "baseline_cvr": None,
                "lifecycle_stage": None,
                "title": None,
                "core_category": None,
                "compatibility": None,
                "attributes": None,
                "target_audience": None,
                "description": None,
            }
            for field_name in ["average_order_value", "gross_margin_range"]:
                for value in ["", "N/A", "null"]:
                    args = dict(base_args)
                    args[field_name] = value
                    with self.subTest(no_write_field=field_name, value=value), self.assertRaises(SystemExit):
                        PRODUCT_DB.cmd_upsert(argparse.Namespace(**args))
                    self.assertEqual(json.loads(db_path.read_text(encoding="utf-8")), original)
                    self.assertFalse(db_path.with_suffix(".json.tmp").exists())


class IdentifierRegressions(unittest.TestCase):
    def test_report_asin_and_sku_must_resolve_to_same_product(self) -> None:
        products = [
            {"key": "one", "asin": "B012345678", "sku": "SKU-1"},
            {"key": "two", "asin": "B087654321", "sku": "SKU-2"},
        ]
        mismatch = PREPARE.match_products(
            products,
            [],
            report_identifiers={
                "asins": ["B012345678"], "skus": ["sku-2"], "sources": [],
            },
        )
        self.assertEqual(mismatch["status"], "identifier_conflict")

        compatible = PREPARE.match_products(
            [{"key": "one", "asin": "B012345678", "sku": "SKU-1", "aliases": ["SKU-ONE"]}],
            [],
            report_identifiers={
                "asins": ["B012345678"], "skus": ["sku-one"], "sources": [],
            },
        )
        self.assertEqual(compatible["status"], "matched")

    def test_identifiers_are_bounded_and_parent_directories_do_not_match(self) -> None:
        product = {"key": "widget", "asin": "B012345678", "sku": "SKU-1"}
        self.assertFalse(INPUTS.bounded_identifier_in_text("SKU-1", "prefixSKU-1suffix"))
        self.assertTrue(INPUTS.bounded_identifier_in_text("SKU-1", "report SKU-1 export"))
        with tempfile.TemporaryDirectory(prefix="B012345678-parent-") as directory:
            path = Path(directory) / "report.csv"
            result = PREPARE.match_products([product], [path])
        self.assertEqual(result["status"], "none")

        matched = PREPARE.match_products([product], [Path("B012345678-report.csv")])
        self.assertEqual(matched["status"], "matched")
        embedded_context = PREPARE.match_products(
            [product], [], product_context_text="campaign prefixB012345678suffix"
        )
        self.assertEqual(embedded_context["status"], "none")
        self.assertFalse(PREPARE.product_match_has_explicit_identifier(
            {"matched_product": product}, [Path("prefixB012345678suffix.csv")], ""
        ))

    def test_product_db_match_ignores_parent_path_identifier_substrings(self) -> None:
        with tempfile.TemporaryDirectory(prefix="B012345678-parent-") as directory:
            root = Path(directory)
            path = root / "plain.txt"
            path.write_text("no product identifier here", encoding="utf-8")
            haystack = " ".join([path.name, path.read_text(encoding="utf-8")])
            db_path = root / "products.json"
            db_path.write_text(json.dumps({"products": [{
                "key": "one", "asin": "B012345678",
            }]}), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                PRODUCT_DB.cmd_match(argparse.Namespace(db=db_path, text="", files=[str(path)]))
        self.assertFalse(INPUTS.bounded_identifier_in_text("B012345678", haystack))
        self.assertEqual(json.loads(output.getvalue()), [])


class PolicyAndCompletenessRegressions(unittest.TestCase):
    def economically_relevant_term(self, **overrides) -> dict:
        term = {
            "search_term": "kitchen sink drain strainer",
            "orders": 0,
            "clicks": 6,
            "impressions": 120,
            "spend": 5.5,
            "cpc": 5.5 / 6,
            "match_types": ["Phrase"],
            "source_match_type": "Phrase",
            "ctr_context": {"relative_ctr": 0.2},
            "bid_economics": {
                "bid_cvr_used": 0.1,
                "bid_cvr_confidence": "high",
                "max_cpc": 0.5,
                "required_cvr": 0.1,
            },
        }
        term.update(overrides)
        return term

    def economically_relevant_product(self) -> dict:
        return {
            "title": "kitchen sink drain strainer",
            "average_order_value": 14,
            "gross_margin_range": "35%",
        }

    def test_direct_profit_spend_threshold_drives_negative_evidence(self) -> None:
        term = self.economically_relevant_term(clicks=1, spend=8.0, cpc=8.0)
        product = self.economically_relevant_product()
        self.assertTrue(PREPARE.negative_performance_evidence(term, product)["performance_direct_negative_exact"])
        with patch.object(PREPARE, "DIRECT_PROFIT_SPEND_MULTIPLE", 2.0):
            evidence = PREPARE.negative_performance_evidence(term, product)
        self.assertFalse(evidence["evidence_families"]["profit_spend"])
        self.assertFalse(evidence["performance_direct_negative_exact"])

    def test_relevant_query_expected_orders_threshold_is_independent(self) -> None:
        term = self.economically_relevant_term(spend=5.0, cpc=5.0 / 6)
        term["bid_economics"] = dict(term["bid_economics"], bid_cvr_used=0.30)
        product = self.economically_relevant_product()
        self.assertTrue(PREPARE.negative_performance_evidence(term, product)["performance_direct_negative_exact"])
        with patch.object(PREPARE, "RELEVANT_QUERY_EXPECTED_ORDERS", 2.0):
            evidence = PREPARE.negative_performance_evidence(term, product)
        self.assertFalse(evidence["performance_direct_negative_exact"])

    def test_relevant_query_profit_spend_threshold_is_independent(self) -> None:
        term = self.economically_relevant_term(spend=8.0, cpc=8.0 / 6)
        product = self.economically_relevant_product()
        self.assertTrue(PREPARE.negative_performance_evidence(term, product)["performance_direct_negative_exact"])
        with patch.object(PREPARE, "RELEVANT_QUERY_PROFIT_SPEND_MULTIPLE", 2.0):
            evidence = PREPARE.negative_performance_evidence(term, product)
        self.assertFalse(evidence["performance_direct_negative_exact"])

    def test_semantic_and_protection_arrays_are_not_truncated(self) -> None:
        negatives = [
            {
                "search_term": f"negative {index}",
                "source_match_family": "broad",
                "historical_orders": index,
            }
            for index in range(90)
        ]
        protected = [{"search_term": f"protected {index}"} for index in range(90)]
        roots = [{"term": f"protected root {index}"} for index in range(90)]
        compact = PREPARE.compact_candidate_actions_for_package({
            "status": "ok",
            "low_sample_semantic_mismatch_candidates": negatives,
            "protected_terms": protected,
            "root_protected_terms": roots,
        })
        self.assertEqual(len(compact["low_sample_semantic_mismatch_candidates"]), 90)
        self.assertEqual(len(compact["protected_terms"]), 90)
        self.assertEqual(len(compact["root_protected_terms"]), 90)
        self.assertEqual(compact["low_sample_semantic_mismatch_candidates"][-1]["historical_orders"], 89)
        self.assertEqual(compact["low_sample_semantic_mismatch_candidates"][-1]["source_match_family"], "broad")

        ad_data = {
            "status": "ok",
            "all_terms": [
                {"search_term": f"converting protected phrase {index}", "orders": 1, "clicks": 1}
                for index in range(90)
            ],
        }
        built_roots = PREPARE.build_root_protected_terms({}, ad_data, {})
        self.assertEqual(len(built_roots), 90)

    def test_scoped_root_and_blocked_audit_lists_exceed_eighty(self) -> None:
        rows = []
        lookup = {}
        for index in range(90):
            for suffix in ["a", "b"]:
                term = f"query {index} {suffix}"
                rows.append({
                    "search_term": term,
                    "orders": 0,
                    "clicks": 1,
                    "impressions": 10,
                    "spend": 0.1,
                    "rows_aggregated": 1,
                    "match_types": ["Phrase"],
                    "source_match_type": "Phrase",
                    "source_targeting": "seed",
                    "campaign": "Campaign",
                    "ad_group": "Group",
                })
                lookup[term] = {"search_term": term}

        def roots_for_term(term: str, _product: dict) -> list[str]:
            return [f"root {term.split()[1]}"]

        with patch.object(PREPARE, "negative_root_phrases", side_effect=roots_for_term):
            plan = PREPARE.build_scoped_negative_plan(
                {"status": "ok", "all_pairs": rows}, lookup, {}, {}, set(), set()
            )
            blocked = PREPARE.build_scoped_negative_plan(
                {"status": "ok", "all_pairs": rows},
                lookup,
                {},
                {},
                set(),
                {f"root {index}" for index in range(90)},
            )
        self.assertEqual(len(plan["root_phrase"]), 90)
        self.assertEqual(len(blocked["blocked_roots"]), 90)


if __name__ == "__main__":
    unittest.main()
