from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]


def load_module(name: str, relative_path: str):
    path = SKILL_DIR / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PREPARE = load_module("prepare_amazon_analysis_policy", "scripts/prepare_amazon_analysis.py")
RENDERER = load_module("render_amazon_report_policy", "scripts/render_amazon_report.py")


def converting_row() -> dict:
    return {
        "campaign_group_id": "A1 converting",
        "keyword": "kitchen sink drain strainer",
        "recent_orders": 1,
        "recent_acos": 0.20,
        "profit_acos_cap": 0.30,
        "action_eligibility": "low_confidence_validation",
    }


def zero_row(index: int, group: str | None = None) -> dict:
    return {
        "campaign_group_id": group or f"Z{index} singleton",
        "keyword": f"zero candidate {index}",
        "recent_orders": 0,
        "zero_order_potential": True,
        "discovery_score": 100 - index,
        "action_eligibility": "observe",
    }


def required_overlay_row(keyword: str = "自动紧密") -> dict:
    return {
        "campaign_group_id": "A9 controlled overlay",
        "keyword": keyword,
        "family": "auto_learning",
        "recent_orders": 0,
        "compact_visibility_required": True,
        "controlled_test_overlay": True,
    }


class SharedPolicyTests(unittest.TestCase):
    def test_runtime_negative_rules_use_three_and_six_click_gates(self) -> None:
        rules = "\n".join(RENDERER.RUNTIME_RULES)
        self.assertIn("从3点击起", rules)
        self.assertIn("达到6点击", rules)
        self.assertNotIn("点击量不超过10时不得凭表现否定", rules)

    def test_runtime_placement_rule_distinguishes_rebuild(self) -> None:
        rules = "\n".join(RENDERER.RUNTIME_RULES)
        self.assertIn("优化现有活动模式省略广告位列", rules)
        self.assertIn("重建模式保留广告位列", rules)
        self.assertIn("全部30%", rules)
        self.assertIn("基础价除以1.3", rules)

    def test_zero_order_exact_and_phrase_share_one_cap(self) -> None:
        product = {
            "title": "kitchen sink drain strainer basket",
            "core_category": "sink drain strainer",
            "key_attributes": ["stainless steel"],
        }
        rows = [
            {
                "search_term": f"kitchen sink drain strainer {suffix}",
                "orders": 0,
                "clicks": 2,
                "impressions": 100,
                "spend": 0.40,
                "cpc": 0.20,
                "ctr_context": {"relative_ctr": 2.0},
                "bid_economics": {"max_cpc": 0.50},
                "match_types": ["词组匹配"],
            }
            for suffix in ["red", "blue", "black", "steel", "large", "small", "mesh"]
        ]
        with_conversions = PREPARE.build_zero_order_growth_candidates(
            rows, product, max_total=2, max_phrase=2
        )
        without_conversions = PREPARE.build_zero_order_growth_candidates(
            rows, product, max_total=5, max_phrase=2
        )
        self.assertEqual(
            len(with_conversions["zero_order_exact_candidates"])
            + len(with_conversions["zero_order_phrase_candidates"]),
            2,
        )
        self.assertEqual(
            len(without_conversions["zero_order_exact_candidates"])
            + len(without_conversions["zero_order_phrase_candidates"]),
            5,
        )

    def test_rebuild_without_placement_evidence_uses_neutral_preload(self) -> None:
        row = {
            "search_term": "kitchen sink drain strainer",
            "source_targeting": "sink drain",
            "source_match_type": "词组匹配",
            "campaigns": ["Phrase Campaign"],
            "ad_groups": ["Phrase Group"],
            "orders": 2,
            "clicks": 10,
            "spend": 4.0,
            "cpc": 0.40,
            "acos": 0.20,
            "action_eligibility": "controlled_scale_test",
            "max_cpc": 0.60,
            "baseline_max_cpc": 0.60,
            "suggested_bid": 0.50,
            "current_targeting_status": "来自词组",
        }
        plan = PREPARE.build_rebuild_campaign_plan(
            {"manual_exact_candidates": [row]},
            {"recommendation_mode": "stabilize_profit_only"},
            {},
            "rebuild",
            True,
            {"enabled": False},
            {"profit_acos_cap": 0.30, "estimated_gross_profit_per_order": 5.0},
            {"value": 0.10},
            0.10,
        )
        self.assertEqual(plan["status"], "ok")
        planned = plan["rows"][0]
        self.assertEqual(
            planned["target_placement"]["adjustments"],
            {"rest_of_search": 30, "top_of_search": 30, "product_pages": 30},
        )
        self.assertEqual(planned["target_placement"]["base_bid_divisor"], 1.3)
        self.assertAlmostEqual(planned["suggested_base_bid"], 0.38, places=2)
        self.assertLessEqual(planned["effective_bid_ceiling"], planned["planned_effective_bid"])

    def test_controlled_overlay_stays_bounded_in_profit_stabilization(self) -> None:
        candidates = []
        for index in range(5):
            candidates.append({
                "search_term": f"kitchen sink strainer {index}",
                "recent_orders": 1,
                "recent_spend": 2.0,
                "recent_clicks": 5,
                "recent_acos": 0.20,
                "recent_cvr": 0.20,
                "current_targeting_status": "来自自动",
                "campaigns": ["Auto Campaign"],
                "ad_groups": ["Auto Group"],
            })
        result = PREPARE.build_controlled_exploration(
            {"manual_exact_candidates": candidates},
            {"recommendation_mode": "stabilize_profit_only"},
            {"status": "ok", "all_pairs": []},
            {
                "title": "kitchen sink drain strainer",
                "core_category": "sink strainer",
                "target_acos": "30%",
                "gross_margin_range": "35%",
            },
            {"status": "single_period"},
            {"signal_window": {"recent_sufficiency": {"sufficient": False}}},
            {"zero_order_spend_thresholds": {}},
            {"target_acos_cpc": 0.60},
        )
        self.assertEqual(result["base_recommendation_mode"], "stabilize_profit_only")
        self.assertLessEqual(len(result["phrase_candidates"]), 3)
        self.assertIsNotNone(result["auto_close_test"])
        self.assertEqual(result["auto_close_test"]["search_term"], "自动紧密")
        self.assertTrue(result["auto_close_test"]["compact_visibility_required"])
        self.assertNotIn("broad_candidates", result)


class CompactVisibilityTests(unittest.TestCase):
    def test_converting_pool_displays_at_most_two_zero_order_rows(self) -> None:
        rows = [converting_row(), *[zero_row(index) for index in range(5)]]
        selected = RENDERER._compact_new_campaign_rows(rows)
        self.assertEqual(sum(bool(row.get("zero_order_potential")) for row in selected), 2)

    def test_no_converting_terms_display_at_most_five_zero_order_rows(self) -> None:
        selected = RENDERER._compact_new_campaign_rows([zero_row(index) for index in range(7)])
        self.assertEqual(sum(bool(row.get("zero_order_potential")) for row in selected), 5)

    def test_sibling_expansion_cannot_break_zero_order_cap(self) -> None:
        rows = [
            converting_row(),
            zero_row(1, "Z1 oversized"),
            zero_row(2, "Z1 oversized"),
            zero_row(3, "Z1 oversized"),
            zero_row(4),
            zero_row(5),
        ]
        selected = RENDERER._compact_new_campaign_rows(rows)
        zero_selected = [row for row in selected if row.get("zero_order_potential")]
        self.assertEqual(len(zero_selected), 2)
        self.assertNotIn("Z1 oversized", {row.get("campaign_group_id") for row in zero_selected})

    def test_required_overlay_is_visible_without_consuming_zero_order_cap(self) -> None:
        overlay = required_overlay_row()
        rows = [converting_row(), *[zero_row(index) for index in range(5)], overlay]
        selected = RENDERER._compact_new_campaign_rows(rows)
        self.assertIn(overlay, selected)
        self.assertEqual(sum(bool(row.get("zero_order_potential")) for row in selected), 2)

    def test_rebuild_preserves_controlled_auto_close_visibility(self) -> None:
        exact = {
            "search_term": "kitchen sink drain strainer",
            "orders": 2,
            "clicks": 10,
            "spend": 4.0,
            "cpc": 0.40,
            "acos": 0.20,
            "action_eligibility": "controlled_scale_test",
            "max_cpc": 0.60,
        }
        controlled = {
            "auto_close_test": {
                "search_term": "自动紧密",
                "suggested_bid": 0.20,
                "suggested_daily_budget": 2.0,
                "controlled_test_overlay": True,
                "compact_visibility_required": True,
            },
            "phrase_candidates": [],
        }
        plan = PREPARE.build_rebuild_campaign_plan(
            {"manual_exact_candidates": [exact]},
            {"recommendation_mode": "stabilize_profit_only"},
            controlled,
            "rebuild",
            False,
            None,
            {"profit_acos_cap": 0.30, "estimated_gross_profit_per_order": 5.0},
            {"value": 0.10},
            0.10,
        )
        auto = next(row for row in plan["rows"] if row.get("family") == "auto_learning")
        self.assertTrue(auto["compact_visibility_required"])
        self.assertIn(auto, RENDERER._compact_new_campaign_rows(plan["rows"]))

    def test_mature_micro_phrase_enters_rebuild_execution_table(self) -> None:
        micro = {
            "search_term": "kitchen sink strainer",
            "source_match_type": "manual_exact",
            "source_orders": 2,
            "source_spend": 4.0,
            "source_clicks": 10,
            "source_cpc": 0.40,
            "source_acos": 0.20,
            "suggested_bid_min": 0.20,
            "suggested_bid_max": 0.30,
            "direct_test_eligible": True,
        }
        plan = PREPARE.build_rebuild_campaign_plan(
            {},
            {
                "recommendation_mode": "stabilize_with_micro_phrase",
                "micro_phrase_candidates": [micro],
            },
            {},
            "rebuild",
            False,
            None,
            {"profit_acos_cap": 0.30, "estimated_gross_profit_per_order": 5.0},
            {"value": 0.10},
            0.10,
        )
        phrase = next(row for row in plan["rows"] if row.get("family") == "manual_phrase")
        self.assertTrue(phrase["compact_visibility_required"])
        self.assertEqual(phrase["keyword"], "kitchen sink strainer")


class ThreeSectionContractTests(unittest.TestCase):
    def test_section_one_keeps_all_scope_rows_and_source_actions(self) -> None:
        scope_rows = []
        source_actions = []
        for index in range(40):
            source = {
                "search_term": f"scope term {index}",
                "orders": 1,
                "clicks": 2,
                "spend": 1.0,
                "cpc": 0.5,
                "acos": 0.5,
                "campaign": f"Campaign {index}",
                "ad_group": f"Group {index}",
                "source_campaign": f"Campaign {index}",
                "source_ad_group": f"Group {index}",
                "source_targeting": f"target {index}",
                "source_match_type": "phrase",
                "source_match_family": "phrase",
                "source_bid": 0.4,
            }
            scope_rows.append(source)
            source_actions.append({
                **source,
                "recommended_action": "bid_down",
                "actual_bid_change_pct": -10,
                "reason": "scope action",
            })

        package = {
            "scope_analysis": {"all_pairs": scope_rows, "top_pairs": scope_rows[:30]},
            "ad_data": {"top_converting_terms": scope_rows, "top_click_only_terms": []},
            "candidate_actions": PREPARE.compact_candidate_actions_for_package({
                "status": "ok",
                "campaign_bid_merge_sources": source_actions,
            }),
        }
        payload = RENDERER.build_report_payload(package)
        rows = payload["sections"]["section_1"]["rows"]
        self.assertEqual(len(rows), 40)
        last = next(row for row in rows if row["search_term"] == "scope term 39")
        self.assertEqual(last["recommended_action"], "bid_down")

    def test_runtime_instructions_and_quick_copy_keep_section_three_executable_only(self) -> None:
        macro = PREPARE.build_account_macro_summary({}, None, None, None, {}, {}, {})
        macro_instruction = macro["model_instruction"]
        self.assertNotIn("macro diagnosis in Section 3", macro_instruction)
        self.assertIn("quick-copy negatives", macro_instruction)

        runtime_payload = RENDERER.build_report_payload({})
        semantic_instruction = runtime_payload["semantic_review"]["instruction"]
        self.assertNotIn("普通候选留在Section 2/3", semantic_instruction)
        self.assertIn("普通否定候选只留在Section 1审核表", semantic_instruction)
        self.assertIn("可立即执行的否定词才能进入Section 3", semantic_instruction)

        review_only_term = "review-only-term"
        approved_term = "approved-negative-term"
        report = RENDERER.render_report({
            "placement_enabled": False,
            "sections": {
                "section_1": {"rows": [], "total_converting_terms": 0},
                "section_2": {
                    "rows": [{
                        "search_term": review_only_term,
                        "orders": 0,
                        "clicks": 6,
                        "spend": 3.0,
                        "cpc": 0.5,
                        "negative_decision_tier": "negative_candidate_review",
                    }],
                    "all_rows": [],
                },
                "section_3": {"new_campaign_plan": {"rows": []}},
                "section_5": {"approved_negatives": [{
                    "negative_group": "N1",
                    "scope": "Campaign A/Ad Group A",
                    "negative_type": "否定精准",
                    "search_term": approved_term,
                }]},
            },
        })
        section_three = report.split("## 3.", 1)[1]
        self.assertNotIn(review_only_term, section_three)
        self.assertIn(approved_term, section_three)

    def test_draft_has_exactly_three_numbered_sections(self) -> None:
        payload = {
            "placement_enabled": False,
            "sections": {
                "section_1": {"rows": [], "total_converting_terms": 0},
                "section_2": {"rows": [], "all_rows": []},
                "section_3": {"new_campaign_plan": {"rows": []}},
                "section_5": {"approved_negatives": []},
            },
        }
        report = RENDERER.render_report(payload)
        headings = re.findall(r"^## (\d+)\. (.+)$", report, flags=re.MULTILINE)
        self.assertEqual(
            headings,
            [
                ("1", "搜索词表现、来源与具体动作"),
                ("2", "新广告创建建议"),
                ("3", "快捷复制——否定关键词"),
            ],
        )

    def test_development_contract_contains_no_legacy_sections(self) -> None:
        contract = (SKILL_DIR / "references" / "report_contract.md").read_text(encoding="utf-8")
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        for legacy in ["five-section", "Section 4", "Section 5", "N1/N2"]:
            self.assertNotIn(legacy, contract)
        self.assertNotIn("four-section renderer", skill)

    def test_optimize_existing_draft_omits_placement_columns(self) -> None:
        payload = {
            "placement_enabled": False,
            "campaign_plan_mode": "optimize-existing",
            "sections": {
                "section_1": {"rows": [], "total_converting_terms": 0},
                "section_2": {"rows": [], "all_rows": []},
                "section_3": {"new_campaign_plan": {"rows": []}},
                "section_5": {"approved_negatives": []},
            },
        }
        report = RENDERER.render_report(payload)
        self.assertNotIn("广告位溢价", report)
        self.assertNotIn("广告位/建组建议", report)

    def test_product_targeting_renders_with_rebuild_rows_and_full_retains_all(self) -> None:
        product_targets = [
            {
                "search_term": f"B0000000{index:02d}",
                "orders": 1,
                "spend": 2.0,
                "clicks": 4,
                "acos": 0.20,
                "recommended_product_targeting_action": "keep_or_scale_product_target",
                "reason": "analyze_as_product_targeting_not_keyword_targeting",
            }
            for index in range(12)
        ]
        compact_actions = PREPARE.compact_candidate_actions_for_package({
            "status": "ok",
            "product_targeting_candidates": product_targets,
        })
        package = {
            "placement_used_for_new_campaigns": False,
            "candidate_actions": compact_actions,
            "rebuild_campaign_plan": {
                "rows": [
                    {
                        "campaign_group_id": "A1 Exact",
                        "keyword": "kitchen sink drain strainer",
                        "match_type": "精准",
                        "recent_orders": 1,
                        "recent_acos": 0.20,
                        "profit_acos_cap": 0.30,
                        "action_eligibility": "low_confidence_validation",
                        "suggested_base_bid": 0.40,
                        "target_cpc": 0.50,
                        "suggested_daily_budget": 5,
                        "dynamic_bidding": "只降低",
                    }
                ]
            },
        }
        payload = RENDERER.build_report_payload(package)
        compact = RENDERER.render_report(payload)
        full = RENDERER.render_full_report(payload)
        self.assertEqual(len(payload["sections"]["section_2"]["product_targeting_rows"]), 10)
        self.assertEqual(len(payload["sections"]["section_2"]["all_product_targeting_rows"]), 12)
        self.assertIn("B000000000", compact)
        self.assertNotIn("B000000011", compact)
        self.assertIn("B000000011", full)
        self.assertEqual(compact.count("### 手动商品投放建议"), 1)
        self.assertEqual(full.count("### 手动商品投放建议"), 1)

    def test_product_targets_never_enter_keyword_campaign_groups(self) -> None:
        plan = PREPARE.build_rebuild_campaign_plan(
            {
                "manual_exact_candidates": [{
                    "search_term": "kitchen sink drain strainer",
                    "orders": 1,
                    "clicks": 5,
                    "spend": 2.0,
                    "cpc": 0.40,
                    "acos": 0.20,
                    "max_cpc": 0.50,
                    "action_eligibility": "low_confidence_validation",
                }],
                "product_targeting_candidates": [{
                    "search_term": "B012345678",
                    "orders": 2,
                    "spend": 3.0,
                    "clicks": 5,
                    "acos": 0.20,
                }],
            },
            {"recommendation_mode": "stabilize_profit_only"},
            {},
            "rebuild",
            False,
            None,
            {"profit_acos_cap": 0.30, "estimated_gross_profit_per_order": 5.0},
            {"value": 0.10},
            0.10,
        )
        self.assertNotIn("B012345678", {row.get("keyword") for row in plan["rows"]})


if __name__ == "__main__":
    unittest.main()
