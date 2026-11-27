from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "prepare_amazon_analysis.py"
SPEC = importlib.util.spec_from_file_location("prepare_amazon_analysis_negative", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


PRODUCT = {
    "title": "3-in-1 Kitchen Sink Drain Strainer for 3-1/2 inch Drain",
    "core_category": "kitchen sink drain strainer stopper",
    "description": "304 stainless steel pop up stopper for standard kitchen sinks",
    "compatibility": ["2.95-3.42 inch width"],
    "key_attributes": ["3-in-1", "anti-clogging basket"],
    "target_acos": "40%",
    "average_order_value": 14,
    "gross_margin_range": "35%",
}


def query_row(**overrides) -> dict:
    row = {
        "search_term": "wrong accessory",
        "orders": 0,
        "clicks": 3,
        "impressions": 120,
        "spend": 1.0,
        "cpc": 1 / 3,
        "match_types": ["词组匹配"],
        "targeting_expressions": ["sink drain strainer"],
        "source_match_type": "词组匹配",
        "source_targeting": "sink drain strainer",
        "campaign": "Phrase Campaign",
        "ad_group": "Phrase Group",
        "ctr_context": {"relative_ctr": 0.20, "ctr_signal": "strong_ctr_weakness"},
        "bid_economics": {
            "bid_cvr_used": 0.10,
            "bid_cvr_confidence": "high",
            "max_cpc": 0.50,
            "required_cvr": 0.10,
        },
    }
    row.update(overrides)
    return row


def scope_pair(term: str, *, clicks: int = 3, spend: float = 3.0, orders: int = 0, date: str = "2026-07-20") -> dict:
    return {
        "search_term": term,
        "orders": orders,
        "clicks": clicks,
        "impressions": 100,
        "spend": spend,
        "cpc": spend / clicks if clicks else None,
        "rows_aggregated": 1,
        "date_values_seen": [date],
        "match_types": ["词组匹配"],
        "targeting_expressions": ["sink drain strainer"],
        "source_match_type": "词组匹配",
        "source_targeting": "sink drain strainer",
        "campaign": "Phrase Campaign",
        "ad_group": "Phrase Group",
    }


def lookup_for(rows: list[dict]) -> dict[str, dict]:
    return {
        row["search_term"]: {
            "search_term": row["search_term"],
            "ctr_context": {"relative_ctr": 0.25, "ctr_signal": "strong_ctr_weakness"},
            "bid_economics": {
                "bid_cvr_used": 0.10,
                "bid_cvr_confidence": "high",
                "max_cpc": 0.50,
                "required_cvr": 0.10,
            },
        }
        for row in rows
    }


class MatchAwareNegativeTests(unittest.TestCase):
    def test_scope_aggregation_keeps_target_and_match_family_separate(self) -> None:
        tables = MODULE.rows_to_tables(
            "Report",
            [
                (
                    "Search Term", "Orders", "Spend", "Clicks", "Campaign", "Ad Group",
                    "Targeting", "Match Type",
                ),
                ("same term", 1, 2.0, 4, "C", "G", "target one", "Exact"),
                ("same term", 0, 3.0, 3, "C", "G", "target one", "Broad"),
                ("same term", 0, 5.0, 6, "C", "G", "target two", "Broad"),
            ],
            "US",
        )
        result = MODULE.aggregate_terms_by_scope(tables)
        self.assertEqual(result["unique_pairs"], 3)
        rows = {
            (row["source_match_family"], row["source_targeting"]): row
            for row in result["all_pairs"]
        }
        self.assertEqual(rows[("exact", "target one")]["orders"], 1)
        self.assertEqual(rows[("exact", "target one")]["spend"], 2.0)
        self.assertEqual(rows[("broad", "target one")]["spend"], 3.0)
        self.assertEqual(rows[("broad", "target two")]["orders"], 0)
        self.assertEqual(rows[("broad", "target two")]["spend"], 5.0)

    def test_low_ctr_alone_does_not_trigger_early_review(self) -> None:
        evidence = MODULE.negative_performance_evidence(
            query_row(
                search_term="kitchen sink drain strainer",
                spend=0.20,
                cpc=0.066,
            ),
            PRODUCT,
        )
        self.assertTrue(evidence["evidence_families"]["low_relative_ctr"])
        self.assertFalse(evidence["early_negative_review"])
        self.assertFalse(evidence["performance_direct_negative_exact"])

    def test_three_click_weak_query_enters_review_with_second_signal(self) -> None:
        evidence = MODULE.negative_performance_evidence(
            query_row(spend=2.50, cpc=0.833),
            PRODUCT,
        )
        self.assertTrue(evidence["early_negative_review"])
        self.assertEqual(evidence["negative_decision_tier"], "negative_candidate_review")
        self.assertFalse(evidence["performance_direct_negative_exact"])

    def test_six_click_discovery_query_can_qualify_direct_exact_negative(self) -> None:
        evidence = MODULE.negative_performance_evidence(
            query_row(clicks=6, spend=5.50, cpc=0.917),
            PRODUCT,
        )
        self.assertTrue(evidence["performance_direct_negative_exact"])
        self.assertEqual(
            evidence["negative_decision_tier"],
            "direct_negative_exact_after_semantic_safety_check",
        )

    def test_exact_source_never_creates_performance_negative(self) -> None:
        evidence = MODULE.negative_performance_evidence(
            query_row(
                clicks=12,
                spend=12.0,
                cpc=1.0,
                match_types=["精确匹配"],
                source_match_type="精确匹配",
            ),
            PRODUCT,
        )
        self.assertFalse(evidence["negative_source_eligible"])
        self.assertFalse(evidence["performance_direct_negative_exact"])

    def test_size_root_keeps_discriminator_not_generic_unit(self) -> None:
        roots = MODULE.negative_root_phrases("2 inch kitchen sink drain strainer", PRODUCT)
        self.assertIn("2 inch", roots)
        self.assertNotIn("inch", roots)

    def test_measurement_query_is_prioritized_for_semantic_review(self) -> None:
        evidence = MODULE.negative_performance_evidence(
            query_row(search_term="2 inch kitchen sink drain strainer", clicks=1, impressions=3),
            PRODUCT,
        )
        self.assertIn("measurement", evidence["semantic_modifier_tokens"])

    def test_mixed_fraction_does_not_create_false_two_inch_root(self) -> None:
        roots = MODULE.negative_root_phrases("standard 3-1/2 inch sink drain strainer", PRODUCT)
        self.assertIn("3.5 inch", roots)
        self.assertNotIn("2 inch", roots)

    def test_repeated_root_is_aggregated_inside_source_scope(self) -> None:
        rows = [
            scope_pair("cactus kitchen sink strainer", date="2026-07-19"),
            scope_pair("cactus sink strainer basket", date="2026-07-20"),
        ]
        plan = MODULE.build_scoped_negative_plan(
            {"status": "ok", "all_pairs": rows},
            lookup_for(rows),
            {},
            PRODUCT,
            set(),
            set(),
        )
        cactus = next(row for row in plan["root_phrase"] if row["root"] == "cactus")
        self.assertEqual(cactus["supporting_term_count"], 2)
        self.assertFalse(cactus["performance_direct_negative_phrase"])

    def test_root_evidence_does_not_merge_across_source_scopes(self) -> None:
        first = scope_pair("cactus kitchen sink strainer", date="2026-07-19")
        second = scope_pair("cactus sink strainer basket", date="2026-07-20")
        second["campaign"] = "Different Phrase Campaign"
        rows = [first, second]
        plan = MODULE.build_scoped_negative_plan(
            {"status": "ok", "all_pairs": rows},
            lookup_for(rows),
            {},
            PRODUCT,
            set(),
            set(),
        )
        self.assertFalse(any(row["root"] == "cactus" for row in plan["root_phrase"]))

    def test_single_token_root_uses_stronger_performance_gate(self) -> None:
        rows = [
            scope_pair("cactus kitchen sink strainer", date="2026-07-18"),
            scope_pair("cactus sink strainer basket", date="2026-07-19"),
            scope_pair("cactus drain basket", date="2026-07-20"),
            scope_pair("cactus sink stopper", date="2026-07-20"),
        ]
        plan = MODULE.build_scoped_negative_plan(
            {"status": "ok", "all_pairs": rows},
            lookup_for(rows),
            {},
            PRODUCT,
            set(),
            set(),
        )
        cactus = next(row for row in plan["root_phrase"] if row["root"] == "cactus")
        self.assertTrue(cactus["performance_direct_negative_phrase"])

    def test_converting_variant_blocks_root_negative(self) -> None:
        zero_rows = [
            scope_pair("cactus kitchen sink strainer", date="2026-07-19"),
            scope_pair("cactus sink strainer basket", date="2026-07-20"),
        ]
        converting = scope_pair(
            "cactus kitchen sink stopper",
            clicks=2,
            spend=2.0,
            orders=1,
            date="2026-07-20",
        )
        rows = [*zero_rows, converting]
        protected = {"cactus kitchen sink stopper"}
        plan = MODULE.build_scoped_negative_plan(
            {"status": "ok", "all_pairs": rows},
            lookup_for(rows),
            {},
            PRODUCT,
            protected,
            set(),
        )
        self.assertFalse(any(row["root"] == "cactus" for row in plan["root_phrase"]))
        self.assertTrue(any(row["root"] == "cactus" for row in plan["blocked_roots"]))


if __name__ == "__main__":
    unittest.main()
