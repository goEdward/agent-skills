from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "prepare_amazon_analysis.py"
SPEC = importlib.util.spec_from_file_location("prepare_amazon_analysis", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def all_placement_preload() -> dict:
    return {
        "mode": "all_placement_preload",
        "adjustments": {
            "rest_of_search": 30,
            "top_of_search": 30,
            "product_pages": 30,
        },
        "campaign_bucket": "All-Placement-Preload",
        "dynamic_bidding": "down_only",
        "placement_preload_control": True,
        "base_bid_backsolve_required": True,
        "base_bid_divisor": 1.30,
    }


def campaign_row(**overrides) -> dict:
    row = {
        "search_term": "3 in 1 kitchen sink drain strainer",
        "source_targeting": "3 in 1 kitchen sink drain strainer",
        "source_match_type": "精确匹配",
        "source_bid": 0.92,
        "cpc": 1.3166666667,
        "orders": 2,
        "clicks": 3,
        "acos": 0.282546495,
        "profit_acos_cap": 0.35,
        "max_cpc": 1.501850272,
        "action_eligibility": "low_confidence_validation",
        "abnormal_attribution_sample": False,
        "placement_plan": all_placement_preload(),
    }
    row.update(overrides)
    return row


class BidPlanningTests(unittest.TestCase):
    def build(self, row: dict) -> dict:
        return MODULE._campaign_plan_row(
            row,
            family="manual_exact",
            mode="rebuild",
            date_stamp="20260720",
            placement_enabled=True,
            profit_context={"estimated_gross_profit_per_order": 4.63},
            cvr_baseline={"value": 0.283582},
        )

    def test_profitable_low_confidence_term_gets_traffic_floor(self) -> None:
        result = self.build(campaign_row())
        self.assertEqual(result["traffic_cpc_floor"], 1.19)
        self.assertTrue(result["traffic_floor_applied"])
        self.assertEqual(result["traffic_feasibility_status"], "applied")
        self.assertEqual(result["planned_effective_bid"], 1.19)
        self.assertEqual(result["suggested_base_bid"], 0.92)
        self.assertEqual(result["effective_bid_ceiling"], 1.20)

    def test_explicit_profit_repair_target_is_not_raised(self) -> None:
        result = self.build(campaign_row(
            target_bid=0.72,
            source_bid=0.90,
            cpc=1.77,
            orders=4,
            clicks=9,
            acos=0.5425,
            action_eligibility="profit_repair",
        ))
        self.assertIsNone(result["traffic_cpc_floor"])
        self.assertEqual(result["planned_effective_bid"], 0.72)
        self.assertEqual(result["suggested_base_bid"], 0.55)
        self.assertEqual(result["effective_bid_ceiling"], 0.72)

    def test_zero_order_term_never_gets_traffic_floor(self) -> None:
        result = self.build(campaign_row(orders=0, clicks=8, acos=None))
        self.assertIsNone(result["traffic_cpc_floor"])
        self.assertFalse(result["traffic_floor_applied"])

    def test_profitable_term_below_three_clicks_stays_low_sample(self) -> None:
        result = self.build(campaign_row(orders=1, clicks=2, cpc=1.40, acos=0.25))
        self.assertIsNone(result["traffic_cpc_floor"])
        self.assertFalse(result["traffic_floor_applied"])
        self.assertEqual(result["planned_effective_bid"], 0.92)

    def test_traffic_floor_is_capped_by_profit_cpc(self) -> None:
        result = self.build(campaign_row(
            source_bid=0.60,
            cpc=2.00,
            max_cpc=1.50,
        ))
        self.assertEqual(result["traffic_cpc_floor"], 1.80)
        self.assertEqual(result["traffic_feasibility_status"], "profit_cap_conflict")
        self.assertEqual(result["planned_effective_bid"], 1.50)
        self.assertLessEqual(result["effective_bid_ceiling"], 1.50)

    def test_missing_profit_does_not_invent_monetary_stop_lines(self) -> None:
        result = MODULE._campaign_plan_row(
            campaign_row(),
            family="manual_exact",
            mode="rebuild",
            date_stamp="20260720",
            placement_enabled=False,
            profit_context={},
            cvr_baseline={"value": 0.10},
        )
        self.assertIsNone(result["isolation_stop_rule"])
        self.assertIsNone(result["zero_order_stop_tiers"]["watch_spend"])
        serialized = str(result)
        self.assertNotIn("6.50", serialized)
        self.assertNotIn("13.00", serialized)
        self.assertNotIn("19.50", serialized)


class ExistingCampaignBidTests(unittest.TestCase):
    def test_ratio_inference_matches_cpc_to_base_relationship(self) -> None:
        result = MODULE.existing_campaign_bid_plan(
            source_bid=1.00,
            suggested_change="-10%",
            confidence="high",
            target_cpc=1.20,
            actual_cpc=1.30,
            clicks=20,
            source_bid_stable=True,
        )
        self.assertEqual(result["target_bid"], 0.92)
        self.assertEqual(result["actual_bid_change_pct"], -8.00)
        self.assertEqual(result["bid_plan_method"], "cpc_ratio_step_limited")

    def test_long_term_cpc_target_cannot_force_an_oversized_cut(self) -> None:
        result = MODULE.existing_campaign_bid_plan(
            source_bid=1.26,
            suggested_change="-10%到-20%",
            confidence="high",
            target_cpc=0.5296,
            actual_cpc=1.3891666667,
            clicks=12,
            source_bid_stable=True,
        )
        self.assertEqual(result["theoretical_base_bid"], 0.48)
        self.assertEqual(result["target_bid"], 1.01)
        self.assertGreaterEqual(result["actual_bid_change_pct"], -20.00)

    def test_unstable_source_bid_falls_back_to_percentage_step(self) -> None:
        result = MODULE.existing_campaign_bid_plan(
            source_bid=1.26,
            suggested_change="-20%",
            confidence="high",
            target_cpc=0.53,
            actual_cpc=1.39,
            clicks=12,
            source_bid_stable=False,
        )
        self.assertEqual(result["target_bid"], 1.01)
        self.assertEqual(result["bid_plan_method"], "percentage_step")

    def test_persistent_extreme_zero_order_scope_pauses(self) -> None:
        reason = MODULE.existing_campaign_pause_reason(
            orders=0,
            clicks=8,
            acos=None,
            profit_acos_cap=0.35,
            required_bid_change_pct=-60.00,
            daily_signal="persistent_zero_order",
            daily_spend_multiple=2.32,
            daily_longer_window_consistent=True,
        )
        self.assertEqual(reason, "persistent_zero_order_extreme_repair_pause_scope")

    def test_small_converting_scope_steps_down_instead_of_pausing(self) -> None:
        reason = MODULE.existing_campaign_pause_reason(
            orders=2,
            clicks=12,
            acos=0.9277,
            profit_acos_cap=0.35,
            required_bid_change_pct=-62.00,
            daily_signal="persistent_zero_order",
            daily_spend_multiple=2.32,
            daily_longer_window_consistent=True,
        )
        self.assertIsNone(reason)


class HistoricalRecoveryTests(unittest.TestCase):
    def test_history_can_raise_bids_only_when_independent_and_economically_sound(self) -> None:
        row = {"baseline": {"orders": 3, "acos": 0.20}}
        overlapping = MODULE.historical_recovery_assessment(
            row, target_acos=0.30, history_independent=False,
        )
        self.assertEqual(overlapping["action"], "validate_without_bid_increase")
        self.assertEqual(overlapping["bid_change"], "保持")

        weak = MODULE.historical_recovery_assessment(
            {"baseline": {"orders": 3, "acos": 0.50}},
            target_acos=0.30,
            history_independent=True,
        )
        self.assertEqual(weak["action"], "diagnose_historical_economics_before_recovery")
        self.assertNotIn("+", weak["bid_change"])

        proven = MODULE.historical_recovery_assessment(
            row, target_acos=0.30, history_independent=True,
        )
        self.assertEqual(proven["action"], "prioritized_restore_or_bid_test")
        self.assertEqual(proven["bid_change"], "+5%到+10%")


if __name__ == "__main__":
    unittest.main()
