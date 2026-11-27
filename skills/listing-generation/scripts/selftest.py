#!/usr/bin/env python3
"""Regression tests for the listing-generation bundled scripts.

Uses only the Python standard library and runs on macOS and Windows.
"""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent


def load(name: str):
    path = HERE / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


claim_review = load("claim_review")
consensus = load("consensus")
kwfreq = load("kwfreq")
validate_final = load("validate_final")
pipeline = load("run_listing_pipeline")


def failure_codes(data: dict[str, object]) -> set[str]:
    return {result.code for result in validate_final.validate(data) if result.level == "FAIL"}


def set_bullet_length(data: dict[str, object], index: int, length: int) -> None:
    prefix = "FEATURE: "
    if length < len(prefix):
        text = "X" * length
        match = text
    else:
        match = "x" * (length - len(prefix))
        text = prefix + match
    location = f"Bullet {index}"
    claim_id = data["field_claim_ids"][location][0]
    claim = next(item for item in data["claim_audit"] if item["id"] == claim_id)
    locations = dict(claim["locations"])
    translated_locations = dict(claim["translation_locations"])
    if "Description" in locations:
        data["description"] = data["description"].replace(locations["Description"], match)
        data["translations"]["description"] = data["translations"]["description"].replace(translated_locations["Description"], "功能：内容")
        locations["Description"] = match
        translated_locations["Description"] = "功能：内容"
    locations[location] = match
    translated_locations[location] = "功能：内容"
    claim.update(
        phrase=match,
        locations=locations,
        translation_locations=translated_locations,
    )
    data["bullets"][index - 1] = text
    data["translations"]["bullets"][index - 1] = "功能：内容"


def valid_us_data() -> dict[str, object]:
    title = "Kavora 64 oz Blender Pitcher"
    highlights = "Copolyester body, side handle"
    bullets = [
        "CHECK CAPACITY: Holds 64 oz",
        "POUR CONTROL: Side handle supports pouring",
        "MATERIAL: Copolyester body",
        "SET CONTENTS: Pitcher and lid included",
        "CARE: Hand wash the pitcher",
    ]
    description = (
        "Features\n- Copolyester body\n- Side handle for pouring\n- Care: Hand wash\n\nWarm Tips\n"
        + validate_final.US_TIP_1 + "\n" + validate_final.US_TIP_2
    )
    translations = {
        "title": "Kavora 64 oz 搅拌杯",
        "highlights": "共聚酯杯体，侧面把手",
        "bullets": [
            "检查容量：容量 64 oz", "倾倒控制：侧面把手帮助倾倒", "材料：共聚酯杯体",
            "套装内容：包含搅拌杯和杯盖", "保养：手洗搅拌杯",
        ],
        "description": (
            "产品特点\n- 共聚酯杯体\n- 侧面把手便于倾倒\n- 保养：手洗\n\n温馨提示\n"
            "1. 由于屏幕和光照条件不同，可能出现轻微色差，这是正常现象。请以实际产品为准。\n"
            "2. 所有尺寸均为手工测量，仅供参考；可能存在轻微偏差。感谢您的理解。"
        ),
    }
    claims: list[dict[str, object]] = [
        {
            "id": "A1", "status": "temporary_use", "phrase": title,
            "match": "phrase", "locations": {"Title": title},
            "translation_locations": {"Title": translations["title"]},
        },
        {
            "id": "A2", "status": "temporary_use", "phrase": highlights,
            "match": "phrase", "locations": {"Product Highlights": highlights},
            "translation_locations": {"Product Highlights": translations["highlights"]},
        },
    ]
    for index, bullet in enumerate(bullets, start=3):
        wording = bullet.split(": ", 1)[1]
        claims.append({
            "id": f"A{index}", "status": "temporary_use", "phrase": wording,
            "match": "phrase", "locations": {f"Bullet {index - 2}": wording},
            "translation_locations": {
                f"Bullet {index - 2}": translations["bullets"][index - 3]
            },
        })
    claims.append({
        "id": "A8", "status": "temporary_use", "phrase": "Copolyester body",
        "match": "phrase", "locations": {"Description": "Copolyester body"},
        "translation_locations": {"Description": "共聚酯杯体"},
    })
    claims[3]["locations"]["Description"] = "Side handle for pouring"
    claims[3]["translation_locations"]["Description"] = "侧面把手便于倾倒"
    claims[6]["locations"]["Description"] = "Hand wash"
    claims[6]["translation_locations"]["Description"] = "手洗"
    return {
        "marketplace": "US",
        "title": title,
        "highlights": highlights,
        "bullets": bullets,
        "description": description,
        "own_brand": "Kavora",
        "competitor_brands": [],
        "compatibility_targets": [],
        "keywords": [],
        "claim_audit": claims,
        "field_claim_ids": {
            "Title": ["A1"], "Product Highlights": ["A2"],
            "Bullet 1": ["A3"], "Bullet 2": ["A4"], "Bullet 3": ["A5"],
            "Bullet 4": ["A6"], "Bullet 5": ["A7"], "Description": ["A4", "A7", "A8"],
        },
        "translations": translations,
        "package_components": [
            {
                "component": "pitcher", "source_singular": ["Pitcher"],
                "source_plural": ["Pitchers"], "translation_terms": ["搅拌杯"],
                "present": True, "count": None,
                "listing_matches": {
                    "Title": "Blender Pitcher", "Bullet 4": "Pitcher",
                    "Bullet 5": "pitcher",
                },
                "translation_matches": {
                    "Title": "搅拌杯", "Bullet 4": "搅拌杯",
                    "Bullet 5": "搅拌杯",
                },
            },
            {
                "component": "lid", "source_singular": ["lid"],
                "source_plural": ["lids"], "translation_terms": ["杯盖"],
                "present": True, "count": None,
                "listing_matches": {"Bullet 4": "lid"},
                "translation_matches": {"Bullet 4": "杯盖"},
            },
        ],
        "translation_terms": [{"source": "Copolyester", "target": "共聚酯"}],
        "blocked_terms": [],
        "competitors": [],
    }


def valid_de_data() -> dict[str, object]:
    title = "Kavora 58mm Siebträger, kompatibel mit Auron P58"
    highlights = "Edelstahlkopf, Holzgriff, Doppelauslauf"
    bullets = [
        "Passform prüfen: Passend für Auron P58 mit 58-mm-Brühgruppe",
        "Zwei Tassen: Der Doppelauslauf verteilt Espresso auf zwei Ausläufe",
        "Material: Der Kopf besteht aus Edelstahl, der Griff aus Holz",
        "Lieferumfang: Siebträger und 18-g-Doppelsieb enthalten",
        "Pflege: Siebträger von Hand reinigen und sofort abtrocknen",
    ]
    description = (
        "Produktmerkmale\n- Kopf aus Edelstahl\n- Griff aus Holz\n- Zwei Ausläufe für Espresso\n\nHinweise\n"
        + validate_final.DE_TIP_1 + "\n" + validate_final.DE_TIP_2
    )
    translations = {
        "title": "Kavora 58mm 咖啡机手柄，兼容 Auron P58",
        "highlights": "不锈钢头部、木柄、双出水口",
        "bullets": [
            "检查适配：适用于 Auron P58 的58mm冲煮头", "同时两杯：双出水口把浓缩咖啡分到两个出口",
            "材料：头部采用不锈钢，握柄为木材", "包装：包含咖啡机手柄和18g双份滤篮",
            "保养：手洗咖啡机手柄并立即擦干",
        ],
        "description": (
            "产品特点\n- 头部采用不锈钢\n- 握柄为木材\n- 两个浓缩咖啡出水口\n\n温馨提示\n"
            "1. 由于屏幕和光照条件不同，可能出现轻微色差。请以实际产品为准。\n"
            "2. 所有尺寸均为手工测量，仅供参考；可能存在轻微偏差。感谢您的理解。"
        ),
    }
    phrases = [title, highlights, *(bullet.split(": ", 1)[1] for bullet in bullets), "Kopf aus Edelstahl"]
    claims = []
    locations = ["Title", "Product Highlights", "Bullet 1", "Bullet 2", "Bullet 3", "Bullet 4", "Bullet 5", "Description"]
    for index, (phrase, location) in enumerate(zip(phrases, locations), start=1):
        if location == "Title":
            translated_match = translations["title"]
        elif location == "Product Highlights":
            translated_match = translations["highlights"]
        elif location.startswith("Bullet "):
            translated_match = translations["bullets"][int(location.split()[-1]) - 1]
        else:
            translated_match = "头部采用不锈钢"
        claims.append({
            "id": f"A{index}", "status": "temporary_use", "phrase": phrase,
            "match": "phrase", "locations": {location: phrase},
            "translation_locations": {location: translated_match},
        })
    claims[3]["locations"]["Description"] = "Zwei Ausläufe für Espresso"
    claims[3]["translation_locations"]["Description"] = "两个浓缩咖啡出水口"
    claims[4]["locations"]["Description"] = "Griff aus Holz"
    claims[4]["translation_locations"]["Description"] = "握柄为木材"
    return {
        "marketplace": "DE", "title": title, "highlights": highlights,
        "bullets": bullets, "description": description, "own_brand": "Kavora",
        "competitor_brands": [], "compatibility_targets": ["Auron P58"],
        "keywords": [], "claim_audit": claims,
        "field_claim_ids": {
            **{location: [f"A{index}"] for index, location in enumerate(locations, start=1)},
            "Description": ["A4", "A5", "A8"],
        },
        "translations": translations,
        "package_components": [
            {
                "component": "portafilter", "source_singular": ["Siebträger"],
                "source_plural": [], "translation_terms": ["咖啡机手柄"],
                "present": True, "count": None,
                "listing_matches": {
                    "Title": "Siebträger", "Bullet 4": "Siebträger",
                    "Bullet 5": "Siebträger",
                },
                "translation_matches": {
                    "Title": "咖啡机手柄", "Bullet 4": "咖啡机手柄",
                    "Bullet 5": "咖啡机手柄",
                },
            },
            {
                "component": "double basket", "source_singular": ["Doppelsieb"],
                "source_plural": ["Doppelsiebe"], "translation_terms": ["双份滤篮"],
                "present": True, "count": None,
                "listing_matches": {"Bullet 4": "Doppelsieb"},
                "translation_matches": {"Bullet 4": "双份滤篮"},
            },
        ],
        "translation_terms": [{"source": "Edelstahl", "target": "不锈钢"}],
        "blocked_terms": [], "competitors": [],
    }


class ClaimReviewTests(unittest.TestCase):
    def classify_one(self, marketplace: str, text: str, claim_type: str = "material"):
        result = claim_review.classify({
            "marketplace": marketplace,
            "claims": [{"claim": text, "claim_type": claim_type, "use_text": text, "locations": ["Title"]}],
        })
        return (
            result["a_claims"] + result["b_claims"]
            + result["automatic_exclusions"] + result["blocking_clarifications"]
        )[0]

    def test_german_besteht_is_not_best(self):
        self.assertEqual(self.classify_one("DE", "besteht aus Edelstahl")["status"], "temporary_use")

    def test_german_bpa_free_is_held(self):
        claim = self.classify_one("DE", "BPA-freier Kunststoff")
        self.assertEqual((claim["group"], claim["status"]), ("B", "hold"))

    def test_filter_grade_and_allergy_terms_are_held(self):
        values = (
            ("US", "HEPA filter"), ("US", "True HEPA filter"),
            ("DE", "HEPA H13 Filter"), ("DE", "H14-Filter"),
            ("DE", "HEPA-Filterklasse"), ("DE", "für Allergiker geeignet"),
            ("DE", "allergikergeeignet"),
        )
        for marketplace, value in values:
            with self.subTest(marketplace=marketplace, value=value):
                claim = self.classify_one(marketplace, value)
                self.assertEqual((claim["group"], claim["status"]), ("B", "hold"))

    def test_hepa_fact_is_split_from_generic_product_type(self):
        result = claim_review.classify({
            "marketplace": "DE",
            "claims": [{
                "claim": "HEPA-Filter-Set", "claim_type": "product_type",
                "adjusted_text": "Filter-Set", "high_risk_text": "HEPA-Filter-Set",
                "locations": ["Title"],
                "adjusted_location_matches": {"Title": "Filter-Set"},
                "high_risk_location_matches": {"Title": "HEPA-Filter-Set"},
            }],
        })
        self.assertEqual((result["a_claims"][0]["use_text"], result["a_claims"][0]["status"]), ("Filter-Set", "adjusted_use"))
        self.assertEqual((result["b_claims"][0]["use_text"], result["b_claims"][0]["status"]), ("HEPA-Filter-Set", "hold"))

    def test_filter_claims_are_confirmed_independently(self):
        data = {
            "marketplace": "DE",
            "claims": [
                {
                    "claim": "HEPA-Filter-Set", "claim_type": "product_type",
                    "adjusted_text": "Filter-Set", "high_risk_text": "HEPA-Filter-Set",
                    "locations": ["Title"],
                    "adjusted_location_matches": {"Title": "Filter-Set"},
                    "high_risk_location_matches": {"Title": "HEPA-Filter-Set"},
                },
                {"claim": "H13-Filter", "claim_type": "filter_class", "use_text": "H13-Filter", "locations": ["Bullet 2"]},
            ],
            "confirmation_text": "B已核实并启用：B1\nB暂不使用：B2",
        }
        result = claim_review.classify(data)
        self.assertEqual(result["b_claims"][0]["status"], "confirmed_use")
        self.assertEqual(result["b_claims"][1]["status"], "hold")

    def test_compatibility_fixed_forms(self):
        allowed = {
            "US": ("compatible with Auron X7", "for Auron X7", "fits Auron X7"),
            "DE": ("kompatibel mit Auron X7", "passend für Auron X7", "für Auron X7"),
        }
        for marketplace, values in allowed.items():
            for value in values:
                with self.subTest(marketplace=marketplace, value=value):
                    self.assertEqual(self.classify_one(marketplace, value, "compatibility")["status"], "temporary_use")

    def test_fits_for_is_rejected(self):
        self.assertEqual(self.classify_one("US", "fits for Auron X7", "compatibility")["status"], "omit")

    def test_automatic_omissions_are_separate(self):
        result = claim_review.classify({
            "marketplace": "US",
            "claims": [
                {"claim": "64 oz capacity", "claim_type": "capacity", "use_text": "64 oz capacity"},
                {"claim": "best pitcher", "claim_type": "marketing_absolute", "use_text": "best pitcher"},
            ],
        })
        markdown = claim_review.render_markdown(result)
        self.assertIn("自动省略项（无需回复）", markdown)
        self.assertNotIn("| A2 | best pitcher | 已保守省略", markdown)
        self.assertEqual([claim["id"] for claim in result["a_claims"]], ["A1"])
        self.assertEqual(result["automatic_exclusions"][0]["claim"], "best pitcher")
        self.assertEqual(result["validator_blocked_terms"][0]["term"], "best pitcher")

    def test_direct_automatic_exclusion_is_lightweight_and_masked(self):
        result = claim_review.classify({
            "marketplace": "DE",
            "claims": [{
                "claim": "Siebträger", "claim_type": "product_type",
                "use_text": "Siebträger", "location_matches": {"Title": "Siebträger"},
            }],
            "automatic_exclusions": [{
                "display": "[竞争品牌词已排除]", "term": "RivalBrand",
                "match": "substring", "reason": "竞品卖家品牌",
            }],
        })
        self.assertEqual([claim["id"] for claim in result["a_claims"]], ["A1"])
        self.assertIn("[竞争品牌词已排除]", claim_review.render_markdown(result))
        self.assertEqual(result["validator_blocked_terms"][0]["term"], "RivalBrand")

    def test_display_locations_derive_from_location_matches(self):
        result = claim_review.classify({
            "marketplace": "US",
            "claims": [{
                "claim": "64 oz capacity", "use_text": "64 oz capacity",
                "location_matches": {"Title": "64 oz", "Bullet 1": "Holds 64 oz"},
            }],
        })
        self.assertEqual(result["a_claims"][0]["locations"], "Title、Bullet 1")

    def test_string_false_cannot_confirm_claims(self):
        for key in ("seller_confirmed", "conflict", "core_identity"):
            with self.subTest(key=key), self.assertRaisesRegex(
                ValueError, f"{key} must be a JSON boolean"
            ):
                claim_review.classify({
                    "marketplace": "US",
                    "claims": [{
                        "claim": "food-grade", "claim_type": "food_contact",
                        key: "false",
                    }],
                })

    def test_string_false_cannot_confirm_all_group_a(self):
        with self.assertRaisesRegex(
            ValueError, "confirmation.confirm_all_a must be a JSON boolean"
        ):
            claim_review.classify({
                "marketplace": "US",
                "claims": [{"claim": "wood", "use_text": "wood"}],
                "confirmation": {"confirm_all_a": "false"},
            })

    def test_exact_material_grade_requires_specific_evidence(self):
        for marketplace, wording in (
            ("DE", "304-Edelstahl"),
            ("DE", "304er Edelstahlkopf"),
            ("DE", "316er-Edelstahl"),
            ("DE", "Edelstahl Typ 304"),
            ("DE", "Edelstahl 316"),
            ("US", "304 stainless steel"),
            ("US", "stainless steel type 304"),
            ("US", "SUS304"),
            ("US", "stainless steel grade 316"),
        ):
            with self.subTest(wording=wording):
                result = self.classify_one(marketplace, wording, "material")
                self.assertEqual(result["group"], "B")
                self.assertEqual(result["status"], "hold")
                self.assertIn("材质等级", result["evidence_needed"])

    def test_explicit_high_risk_flag_is_enforced(self):
        result = claim_review.classify({
            "marketplace": "US",
            "claims": [{
                "claim": "Neutral test-dependent assertion",
                "claim_zh": "中性测试声明",
                "high_risk": True,
            }],
        })
        self.assertEqual(result["a_claims"], [])
        self.assertEqual(result["b_claims"][0]["status"], "hold")

    def test_certification_forms_are_group_b_without_explicit_type(self):
        for source in (
            "CE certified", "CE-certified", "ce   certified", "CE certification",
            "CE-zertifiziert", "CE marked", "CE-Kennzeichnung",
            "FDA approved", "FDA-cleared", "FDA certification", "FDA-Zulassung",
            "UL-listed", "UL certified", "ETL-listed", "ETL certified",
            "FCC certified", "FCC-zertifiziert", "LFGB compliant", "CPSIA compliant",
            "certified-food-safe",
        ):
            with self.subTest(source=source):
                result = self.classify_one("DE" if "zert" in source or "Kenn" in source else "US", source)
                self.assertEqual((result["group"], result["status"]), ("B", "hold"))
        for model in ("CE123", "XFDA", "ULTRA", "FCC123", "LFGB123", "CPSIA123"):
            with self.subTest(model=model):
                self.assertEqual(self.classify_one("US", model)["group"], "A")

    def test_claim_support_counts_require_json_integers(self):
        with self.assertRaisesRegex(ValueError, "support_count must be"):
            claim_review.classify({
                "marketplace": "US",
                "claims": [{"claim": "wood", "support_count": {"bad": 1}}],
            })


class ConsensusTests(unittest.TestCase):
    def resolve_one(self, mode: str, attribute: dict[str, object]):
        return consensus.resolve({"mode": mode, "attributes": [attribute]})

    def test_target_attribute_wins_over_competitors(self):
        result = self.resolve_one("target_anchored", {
            "field": "handle_material", "strategy": "hierarchy",
            "target": ["Walnussholz", "Holz"],
            "competitors": [["Palisander", "Holz"], ["Palisander", "Holz"]],
        })
        self.assertEqual(result["confirmed"][0]["value"], "Walnussholz")

    def test_target_silent_field_uses_competitor_consensus(self):
        result = self.resolve_one("target_anchored", {
            "field": "head_material", "strategy": "exact", "target": None,
            "competitors": ["Edelstahl", "Edelstahl", None],
        })
        self.assertEqual(result["group_a"][0]["value"], "Edelstahl")

    def test_consensus_only_uses_single_reference_as_group_a(self):
        result = self.resolve_one("consensus_only", {
            "field": "size", "strategy": "exact", "competitors": ["51 mm"],
        })
        self.assertEqual(result["group_a"][0]["source_summary"], "1/1 个竞品来源的最具体共同值")

    def test_304_and_stainless_downgrade_to_stainless(self):
        result = self.resolve_one("consensus_only", {
            "field": "head_material", "strategy": "hierarchy",
            "competitors": [["304-Edelstahl", "Edelstahl"], ["Edelstahl"]],
        })
        self.assertEqual(result["group_a"][0]["value"], "Edelstahl")

    def test_wood_species_downgrade_to_wood(self):
        result = self.resolve_one("consensus_only", {
            "field": "handle_material", "strategy": "hierarchy",
            "competitors": [["Walnussholz", "Holz"], ["Palisander", "Holz"]],
        })
        self.assertEqual(result["group_a"][0]["value"], "Holz")

    def test_package_intersection_empty_is_excluded(self):
        result = self.resolve_one("consensus_only", {
            "field": "package_includes", "strategy": "set_intersection",
            "competitors": [["Pucksieb"], ["Nadelverteiler"]],
        })
        self.assertEqual(result["excluded"][0]["field"], "package_includes")

    def test_compatibility_uses_intersection(self):
        result = self.resolve_one("consensus_only", {
            "field": "compatibility", "strategy": "set_intersection",
            "competitors": [["EC680", "EC685", "EC885"], ["EC680", "EC685"]],
        })
        self.assertEqual(result["group_a"][0]["value"], ["EC680", "EC685"])

    def test_food_contact_is_group_b(self):
        result = self.resolve_one("consensus_only", {
            "field": "food_contact", "claim_type": "food_contact",
            "strategy": "exact", "competitors": ["lebensmittelecht"],
        })
        self.assertEqual(result["group_b"][0]["status"], "hold")

    def test_single_source_304_is_group_b(self):
        result = self.resolve_one("consensus_only", {
            "field": "head_material", "claim_type": "material",
            "strategy": "hierarchy", "competitors": [["304-Edelstahl", "Edelstahl"]],
        })
        self.assertEqual(result["group_b"][0]["value"], "304-Edelstahl")

    def test_confirmed_group_b_counts_but_remains_group_b(self):
        attributes = [
            {"field": "product_type", "target": "filter"},
            {"field": "size", "target": "small"},
            {
                "field": "filter_grade", "target": "HEPA",
                "claim_type": "filter_class", "seller_confirmed": True,
            },
        ]
        result = consensus.resolve({
            "mode": "target_anchored", "attributes": attributes,
        })
        self.assertEqual(len(result["confirmed"]), 2)
        self.assertEqual(result["group_b"][0]["status"], "confirmed")
        self.assertEqual(result["content_units"], 3)
        self.assertFalse(result["clarification_required"])

        attributes[2]["seller_confirmed"] = False
        result = consensus.resolve({
            "mode": "target_anchored", "attributes": attributes,
        })
        self.assertEqual(result["group_b"][0]["status"], "hold")
        self.assertEqual(result["content_units"], 2)
        self.assertTrue(result["clarification_required"])

    def test_core_target_self_conflict_requires_clarification(self):
        result = self.resolve_one("target_anchored", {
            "field": "product_type", "strategy": "exact", "target": "Siebträger",
            "competitors": [], "target_conflict": True, "core_identity": True,
        })
        self.assertTrue(result["clarification_required"])

    def test_consensus_rejects_string_boolean_flags(self):
        for key in (
            "core_identity", "target_conflict", "high_risk", "seller_confirmed"
        ):
            with self.subTest(key=key), self.assertRaisesRegex(
                ValueError, f"{key} must be a JSON boolean"
            ):
                self.resolve_one("target_anchored", {
                    "field": "material", "target": "wood", key: "false",
                })

    def test_min_count_rejects_invalid_values_directly(self):
        for value in ("NaN", float("nan"), float("inf"), -1, 1.5, True):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "finite non-negative integers"
            ):
                self.resolve_one("consensus_only", {
                    "field": "package_count", "strategy": "min_count",
                    "competitors": [value, 2],
                })

    def test_min_count_accepts_integers_directly(self):
        result = self.resolve_one("consensus_only", {
            "field": "package_count", "strategy": "min_count",
            "competitors": [3, 2],
        })
        self.assertEqual(result["group_a"][0]["value"], 2)


class KeywordTests(unittest.TestCase):
    def test_words_before_punctuation_and_numbers_remain_searchable(self):
        for text, phrase, count in (
            ("side handle,2 lids", "side handle", 1),
            ("side handle.2 lids", "side handle", 1),
            ("Holzgriff,2 Siebe", "Holzgriff", 1),
            ("RivalBrand,64 oz", "RivalBrand", 1),
            ("1,5mm M2.5 M2,5", "1,5 mm", 1),
            ("1,5mm M2.5 M2,5", "5 mm", 0),
            ("M2.5 M2,5", "M2.5", 1),
            ("M2.5 M2,5", "M2", 0),
        ):
            with self.subTest(text=text, phrase=phrase):
                self.assertEqual(kwfreq.phrase_count(text, phrase), count)
                self.assertEqual(kwfreq.alternative_phrase_count(text, [phrase]), count)
        rows = kwfreq.bullet_metrics(["side handle"], ["side handle,2 lids"] * 2)
        self.assertEqual(rows[0]["counts"], [1, 1])
        self.assertEqual(rows[0]["suggested"], "1-2")

    def test_numeric_keyword_boundaries_and_variants(self):
        for text, phrase, count in (
            ("1.5 mm", "5 mm", 0), ("0.51mm", "51 mm", 0),
            ("1-5 mm", "1.5 mm", 0), ("510 mm", "51 mm", 0),
            ("X1.5 mm", "5 mm", 0), ("1.5X", "1.5", 0),
            ("51mm 51-mm 51 MM", "51 mm", 3),
            ("1.5mm 1.5-mm 1.5 mm", "1.5 mm", 3),
        ):
            with self.subTest(text=text, phrase=phrase):
                self.assertEqual(kwfreq.phrase_count(text, phrase), count)
                self.assertEqual(kwfreq.alternative_phrase_count(text, [phrase]), count)
        spec = {"phrase": "1.5 mm", "variants": ["1-5 mm", "1.5mm"]}
        self.assertEqual(validate_final.keyword_count("1-5 mm", spec), 1)
        self.assertEqual(validate_final.keyword_count("1.5mm", spec), 1)

    def test_blank_competitors_do_not_count_as_keyword_absence(self):
        originals = ["pitcher pitcher pitcher"] * 3
        expected = kwfreq.bullet_metrics(["pitcher"], originals)
        self.assertEqual(expected[0]["suggested"], "3")
        self.assertEqual(
            kwfreq.bullet_metrics(["pitcher"], originals + ["", " \n\t", ""]), expected
        )
        with_absence = kwfreq.bullet_metrics(["pitcher"], originals + ["side handle"] * 3)
        self.assertEqual(with_absence[0]["counts"], [3, 3, 3, 0, 0, 0])
        self.assertEqual(with_absence[0]["suggested"], "1")
        self.assertEqual(kwfreq.bullet_metrics(["pitcher"], ["", " "])[0]["competitors"], 0)

    def test_bullet_cli_header_uses_only_nonblank_competitors(self):
        proc = subprocess.run(
            [sys.executable, str(HERE / "kwfreq.py"), "bullet", "--keyword", "pitcher",
             "--competitor", "pitcher", "--competitor", " ", "--competitor", ""],
            text=True, capture_output=True, check=True,
        )
        self.assertIn("MODE=bullet\tCOMPETITORS=1", proc.stdout)
        self.assertIn("pitcher\t1/1\t1", proc.stdout)

    def test_cross_listing_frequency_rule(self):
        self.assertEqual(kwfreq.suggestion([5, 2, 1, 0, 0]), "2-3")
        self.assertEqual(kwfreq.suggestion([5, 0, 0, 0, 0]), "0-1")

    def test_batch_metrics_use_only_supplied_bullet_text(self):
        rows = kwfreq.bullet_metrics(
            ["51 mm", "Walnussholz"],
            ["Passend für EC680 mit 51 mm", "Zur Reinigung zerlegbar"],
        )
        self.assertEqual(rows[0]["counts"], [1, 0])
        self.assertEqual(rows[1]["coverage"], 0)

    def test_measurement_spacing_variants_share_one_counting_rule(self):
        text = "51mm, 51-mm und 51 mm für EC680"
        self.assertEqual(kwfreq.phrase_count(text, "51 mm"), 3)
        self.assertEqual(validate_final.phrase_count(text, "51 mm"), 3)
        self.assertEqual(kwfreq.phrase_count(text, "EC680"), 1)
        self.assertEqual(validate_final.phrase_count(text, "EC680"), 1)

    def test_alternative_keyword_forms_do_not_double_count_overlap(self):
        text = "Pitcher and lid included; wash the pitcher"
        phrases = ["pitcher", "pitcher and lid"]
        self.assertEqual(kwfreq.alternative_phrase_count(text, phrases), 2)

    def test_keyword_phrase_cannot_span_bullet_boundaries(self):
        text = "Coffee\ndistributor"
        self.assertEqual(kwfreq.phrase_count(text, "coffee distributor"), 0)
        self.assertEqual(
            kwfreq.alternative_phrase_count(text, ["coffee distributor"]), 0
        )
        self.assertEqual(validate_final.phrase_count(text, "coffee distributor"), 0)

    def test_one_token_competitor_consensus_can_recommend_more_than_three(self):
        rows = kwfreq.bullet_metrics(
            ["pitcher", "side handle"],
            [
                "pitcher pitcher pitcher pitcher side handle side handle side handle side handle",
                "pitcher pitcher pitcher pitcher side handle side handle side handle side handle",
                "pitcher pitcher pitcher side handle side handle side handle",
            ],
        )
        self.assertEqual(rows[0]["suggested"], "4")
        self.assertEqual(rows[1]["suggested"], "3")


class FinalValidationTests(unittest.TestCase):
    def test_chinese_phrase_matching_preserves_numeric_boundaries(self):
        for text, phrase, found in (
            ("直径15毫米", "5毫米", False),
            ("直径1.5毫米", "5毫米", False),
            ("直径1,5毫米", "5毫米", False),
            ("直径二十二毫米", "二毫米", False),
            ("直径零点二毫米", "二毫米", False),
            ("直径二分之一毫米", "一毫米", False),
            ("数量20", "数量2", False), ("数量2.5", "数量2", False),
            ("直径５ 毫米", "5毫米", True),
            ("直径二毫米", "二毫米", True),
            ("表面为共聚酯材料", "共聚酯", True),
        ):
            with self.subTest(text=text, phrase=phrase):
                self.assertEqual(validate_final.phrase_found(text, phrase, "phrase"), found)
                self.assertEqual(validate_final.strict_phrase_found(text, phrase), found)

    def test_valid_fixture_has_no_hard_failures(self):
        self.assertEqual(failure_codes(valid_us_data()), set())

    def test_valid_german_fixture_has_no_hard_failures(self):
        self.assertEqual(failure_codes(valid_de_data()), set())

    def test_listing_fields_and_translations_must_be_strings(self):
        data = valid_us_data()
        data["title"] = [data["title"]]
        data["translations"]["title"] = [data["translations"]["title"]]
        codes = failure_codes(data)
        self.assertIn("FIELD_SCHEMA", codes)

    def test_overlapping_keyword_variant_does_not_inflate_count(self):
        data = valid_us_data()
        data["keywords"] = [{
            "phrase": "pitcher",
            "variants": ["pitcher and lid"],
            "target": 2,
            "reported_used": 2,
            "source": "competitor",
            "eligible_locations": ["Bullet 4", "Bullet 5"],
        }]
        results = validate_final.validate(data)
        actual_rows = [result for result in results if result.code == "KEYWORD_ACTUAL"]
        self.assertEqual(len(actual_rows), 1)
        self.assertIn("count 2", actual_rows[0].message)

    def test_nonrecoverable_claim_in_unmapped_suffix_fails(self):
        data = valid_us_data()
        data["highlights"] += ", best choice"
        data["translations"]["highlights"] += "，最佳选择"
        self.assertIn("NONRECOVERABLE_CLAIM", failure_codes(data))

    def test_unconfirmed_filtration_efficiency_in_unmapped_suffix_fails(self):
        data = valid_us_data()
        data["highlights"] += ", filters 99.97% of particles"
        data["translations"]["highlights"] += "，过滤99.97%的颗粒"
        self.assertIn("RISK_CLAIM_CONFIRMATION", failure_codes(data))

    def test_translation_cannot_add_unconfirmed_food_grade_claim(self):
        data = valid_us_data()
        data["translations"]["highlights"] += "，食品级材质"
        self.assertIn(
            "TRANSLATION_RISK_CLAIM_CONFIRMATION", failure_codes(data)
        )

    def test_translation_cannot_add_nonrecoverable_claim(self):
        data = valid_us_data()
        data["translations"]["highlights"] += "，最佳选择"
        self.assertIn(
            "TRANSLATION_NONRECOVERABLE_CLAIM", failure_codes(data)
        )

    def test_confirmed_risk_claim_must_cover_the_same_field(self):
        data = valid_us_data()
        wording = "filters 99.97% of particles"
        data["highlights"] += ", " + wording
        data["translations"]["highlights"] += "，过滤99.97%的颗粒"
        data["claim_audit"].append({
            "id": "B1", "status": "confirmed_use", "phrase": wording,
            "match": "phrase", "locations": {"Bullet 1": "Holds 64 oz"},
            "translation_locations": {"Bullet 1": "容量 64 oz"},
        })
        data["field_claim_ids"]["Bullet 1"].append("B1")
        self.assertIn("RISK_CLAIM_CONFIRMATION", failure_codes(data))

    def test_confirmed_risk_claim_in_same_field_passes_risk_gate(self):
        data = valid_us_data()
        wording = "filters 99.97% of particles"
        data["highlights"] += ", " + wording
        data["translations"]["highlights"] += "，过滤99.97%的颗粒"
        data["claim_audit"].append({
            "id": "B1", "status": "confirmed_use", "phrase": wording,
            "match": "phrase", "locations": {"Product Highlights": wording},
            "translation_locations": {"Product Highlights": "过滤99.97%的颗粒"},
        })
        data["field_claim_ids"]["Product Highlights"].append("B1")
        self.assertNotIn("RISK_CLAIM_CONFIRMATION", failure_codes(data))

    def test_confirmed_risk_id_cannot_cover_unrelated_text_in_same_field(self):
        data = valid_us_data()
        wording = "filters 99.97% of particles"
        data["highlights"] += ", " + wording
        data["translations"]["highlights"] += "，过滤99.97%的颗粒"
        data["claim_audit"].append({
            "id": "B1", "status": "confirmed_use", "phrase": wording,
            "match": "phrase",
            "locations": {"Product Highlights": "Copolyester body"},
            "translation_locations": {"Product Highlights": "共聚酯杯体"},
        })
        data["field_claim_ids"]["Product Highlights"].append("B1")
        self.assertIn("RISK_CLAIM_CONFIRMATION", failure_codes(data))

    def test_confirmed_material_grade_does_not_cover_other_component(self):
        data = valid_us_data()
        data["highlights"] += ", 304 stainless steel handle, 304 stainless steel body"
        data["translations"]["highlights"] += "，304不锈钢手柄，304不锈钢主体"
        wording = "304 stainless steel handle"
        data["claim_audit"].append({
            "id": "B1", "status": "confirmed_use", "phrase": wording,
            "match": "phrase",
            "locations": {"Product Highlights": wording},
            "translation_locations": {"Product Highlights": "304不锈钢手柄"},
        })
        data["field_claim_ids"]["Product Highlights"].append("B1")
        self.assertIn("RISK_CLAIM_CONFIRMATION", failure_codes(data))

    def test_confirmed_material_grades_cover_each_complete_component(self):
        data = valid_us_data()
        data["highlights"] += ", 304 stainless steel handle, 316 stainless steel body"
        data["translations"]["highlights"] += "，304不锈钢手柄，316不锈钢主体"
        for index, (source, chinese) in enumerate((
            ("304 stainless steel handle", "304不锈钢手柄"),
            ("316 stainless steel body", "316不锈钢主体"),
        ), start=1):
            claim_id = f"B{index}"
            data["claim_audit"].append({
                "id": claim_id, "status": "confirmed_use", "phrase": source,
                "match": "phrase",
                "locations": {"Product Highlights": source},
                "translation_locations": {"Product Highlights": chinese},
            })
            data["field_claim_ids"]["Product Highlights"].append(claim_id)
        self.assertNotIn("RISK_CLAIM_CONFIRMATION", failure_codes(data))

    def test_package_content_wording_requires_component_structure(self):
        data = valid_us_data()
        data["package_components"] = []
        self.assertIn("PACKAGE_COMPONENTS_REQUIRED", failure_codes(data))

    def test_ft_cannot_be_translated_as_centimeters(self):
        data = valid_us_data()
        data["bullets"][0] = "CHECK LENGTH: Measures 2 ft"
        data["translations"]["bullets"][0] = "检查长度：长度为2厘米"
        data["claim_audit"][2].update(
            phrase="Measures 2 ft",
            locations={"Bullet 1": "Measures 2 ft"},
            translation_locations={"Bullet 1": "长度为2厘米"},
        )
        self.assertIn("TRANSLATION_UNIT", failure_codes(data))

    def test_ft_translation_to_feet_passes(self):
        data = valid_us_data()
        data["bullets"][0] = "CHECK LENGTH: Measures 2 ft"
        data["translations"]["bullets"][0] = "检查长度：长度为2英尺"
        data["claim_audit"][2].update(
            phrase="Measures 2 ft",
            locations={"Bullet 1": "Measures 2 ft"},
            translation_locations={"Bullet 1": "长度为2英尺"},
        )
        self.assertNotIn("TRANSLATION_UNIT", failure_codes(data))

    def test_translation_rejects_swapped_measurement_units(self):
        data = valid_us_data()
        source = "CHECK LENGTH: Measures 10 cm long and 5 mm wide"
        translated = "检查长度：长度为10毫米，宽度为5厘米"
        data["bullets"][0] = source
        data["translations"]["bullets"][0] = translated
        data["claim_audit"][2].update(
            phrase="Measures 10 cm long and 5 mm wide",
            locations={"Bullet 1": "Measures 10 cm long and 5 mm wide"},
            translation_locations={"Bullet 1": translated},
        )
        self.assertIn("TRANSLATION_UNIT", failure_codes(data))

    def test_keyword_one_below_preferred_warns_without_failing(self):
        data = valid_us_data()
        data["keywords"] = [{"phrase": "64 oz", "target": "2", "reported_used": 1}]
        results = validate_final.validate(data)
        self.assertNotIn("KEYWORD_TARGET", {item.code for item in results if item.level == "FAIL"})
        self.assertIn("KEYWORD_BELOW_PREFERRED", {item.code for item in results if item.level == "WARN"})

    def test_keyword_large_shortfall_still_fails(self):
        data = valid_us_data()
        data["keywords"] = [{"phrase": "64 oz", "target": "3", "reported_used": 1}]
        self.assertIn("KEYWORD_TARGET", failure_codes(data))

    def test_user_priority_keyword_requires_a_bullet(self):
        data = valid_us_data()
        data["keywords"] = [{
            "phrase": "Kavora", "source": "user_priority", "reported_used": 0,
        }]
        self.assertIn("KEYWORD_PRIORITY", failure_codes(data))

    def test_user_priority_keyword_one_bullet_use_passes(self):
        data = valid_us_data()
        data["keywords"] = [{
            "phrase": "64 oz", "source": "user_priority", "reported_used": 1,
            "eligible_locations": ["Bullet 1"],
        }]
        self.assertNotIn("KEYWORD_PRIORITY", failure_codes(data))

    def test_user_priority_declared_inflection_variant_counts(self):
        data = valid_de_data()
        data["bullets"][1] += "; der bodenlose Siebträger zeigt den Espressofluss"
        data["claim_audit"][3]["locations"] = {
            "Bullet 2": "Doppelauslauf verteilt Espresso"
        }
        data["translations"]["bullets"][1] += "；无底手柄可显示浓缩咖啡流动"
        data["keywords"] = [{
            "phrase": "bodenloser Siebträger",
            "variants": ["bodenlose Siebträger"],
            "source": "user_priority",
            "reported_used": 1,
            "eligible_locations": ["Bullet 2"],
        }]
        self.assertNotIn("KEYWORD_PRIORITY", failure_codes(data))

    def test_required_keyword_location_is_enforced(self):
        data = valid_us_data()
        data["keywords"] = [{
            "phrase": "Christmas Tree Decoration",
            "source": "user_priority",
            "reported_used": 0,
            "required_locations": ["Title"],
            "eligible_locations": ["Title", "Product Highlights"],
        }]
        self.assertIn("KEYWORD_REQUIRED_LOCATION", failure_codes(data))
        data["title"] = "Kavora Christmas Tree Decoration"
        data["keywords"][0]["reported_used"] = 0
        self.assertNotIn("KEYWORD_REQUIRED_LOCATION", failure_codes(data))

    def test_required_keyword_location_is_enforced_without_eligible_locations(self):
        data = valid_us_data()
        data["keywords"] = [{
            "phrase": "Christmas Tree Decoration",
            "source": "user_priority",
            "reported_used": 0,
            "required_locations": ["Title"],
        }]
        self.assertIn("KEYWORD_REQUIRED_LOCATION", failure_codes(data))

    def test_required_keyword_does_not_cross_punctuation(self):
        data = valid_us_data()
        data["keywords"] = [{
            "phrase": "Santa Claus",
            "source": "user_priority",
            "reported_used": 1,
            "required_locations": ["Title"],
            "eligible_locations": ["Title"],
        }]
        data["title"] = "Kavora Santa, Claus Decoration"
        self.assertIn("KEYWORD_REQUIRED_LOCATION", failure_codes(data))

    def test_required_measurement_forms_match_without_losing_numeric_precision(self):
        for number in ("51", "1.5", "1,5"):
            forms = [number + "mm", number + " mm", number + "-mm"]
            for text in forms:
                for phrase in forms:
                    with self.subTest(text=text, phrase=phrase):
                        self.assertTrue(validate_final.strict_phrase_found("Kavora " + text + " Pitcher", phrase))
        for text, phrase in (
            ("510mm", "51 mm"), ("51cm", "51 mm"), ("0.51mm", "51 mm"),
            ("1.5mm", "5 mm"), ("1-5 mm", "1.5 mm"), ("1.6mm", "1.5 mm"),
            ("51, mm", "51 mm"), ("Santa, Claus", "Santa Claus"), ("X70", "X7"),
        ):
            with self.subTest(text=text, phrase=phrase):
                self.assertFalse(validate_final.strict_phrase_found("Kavora " + text + " Pitcher", phrase))
        self.assertTrue(validate_final.strict_phrase_found("Kavora X7 Pitcher", "X7"))

    def test_required_numeric_identifiers_keep_complete_keyword_tokens(self):
        for text, phrase, expected in (
            ("M2.5", "M2.5", True), ("m2.5", "M2.5", True),
            ("X1.5", "X1.5", True), ("M2.6", "M2.5", False),
            ("M2-5", "M2.5", False), ("M2 5", "M2.5", False),
            ("M2.5X", "M2.5", False), ("XM2.5", "M2.5", False),
            ("M2.50", "M2.5", False),
        ):
            with self.subTest(text=text, phrase=phrase):
                self.assertEqual(validate_final.strict_phrase_found(text, phrase), expected)
                self.assertEqual(validate_final.keyword_count(text, {"phrase": phrase}), int(expected))

    def test_user_priority_keyword_can_use_item_highlights(self):
        data = valid_us_data()
        phrase = "Copolyester body, side handle"
        data["keywords"] = [{
            "phrase": phrase, "source": "user_priority", "reported_used": 0,
            "eligible_locations": ["Item Highlights"],
        }]
        self.assertNotIn("KEYWORD_PRIORITY", failure_codes(data))

    def test_user_priority_item_highlights_allows_full_125_characters(self):
        data = valid_us_data()
        phrase = "Copolyester body, side handle"
        data["highlights"] = phrase + ", " + ("x" * (125 - len(phrase) - 2))
        data["claim_audit"][1].update(phrase=data["highlights"], locations={"Item Highlights": data["highlights"]})
        data["translations"]["highlights"] = "共聚酯杯体，侧面把手，" + ("扩展" * 20)
        data["keywords"] = [{
            "phrase": phrase, "source": "user_priority", "reported_used": 0,
            "eligible_locations": ["Item Highlights"],
        }]
        self.assertEqual(len(data["highlights"]), 125)
        self.assertNotIn("KEYWORD_PRIORITY", failure_codes(data))
        self.assertNotIn("CHAR_LIMIT", failure_codes(data))

    def test_encoded_html_entity_in_listing_field_fails(self):
        data = valid_us_data()
        data["title"] += " &#x20;"
        self.assertIn("ENCODED_ENTITY", failure_codes(data))

    def test_item_highlights_over_125_characters_fails(self):
        data = valid_us_data()
        phrase = "Copolyester body, side handle"
        data["highlights"] = phrase + ", " + ("x" * (126 - len(phrase) - 2))
        data["claim_audit"][1].update(
            phrase=data["highlights"],
            locations={"Item Highlights": data["highlights"]},
        )
        self.assertEqual(len(data["highlights"]), 126)
        self.assertIn("CHAR_LIMIT", failure_codes(data))

    def test_user_priority_highlights_requires_planned_location(self):
        data = valid_us_data()
        phrase = "Copolyester body, side handle"
        data["keywords"] = [{
            "phrase": phrase, "source": "user_priority", "reported_used": 0,
        }]
        self.assertIn("KEYWORD_PRIORITY", failure_codes(data))

    def test_lowercase_item_highlights_warns_without_failing(self):
        data = valid_us_data()
        data["highlights"] = "copolyester body, side handle"
        data["claim_audit"][1].update(
            phrase=data["highlights"],
            locations={"Item Highlights": data["highlights"]},
        )
        results = validate_final.validate(data)
        self.assertIn(
            "HIGHLIGHTS_CAPITALIZATION",
            {item.code for item in results if item.level == "WARN"},
        )
        self.assertNotIn("HIGHLIGHTS_CAPITALIZATION", failure_codes(data))

    def test_item_highlights_period_separates_short_clusters(self):
        data = valid_us_data()
        data["highlights"] = "Climbs on a Christmas tree. Music on or off"
        data["claim_audit"][1].update(
            phrase=data["highlights"],
            locations={"Product Highlights": data["highlights"]},
        )
        results = validate_final.validate(data)
        self.assertNotIn("HIGHLIGHTS_LONG_SENTENCE", {item.code for item in results if item.level == "WARN"})

    def test_item_highlights_long_sentence_is_reviewed(self):
        data = valid_us_data()
        data["highlights"] = "This moving Christmas tree ornament climbs up and down the rope for a highly visible animated holiday display. Music on or off"
        data["claim_audit"][1].update(
            phrase=data["highlights"],
            locations={"Product Highlights": data["highlights"]},
        )
        results = validate_final.validate(data)
        self.assertIn(
            "HIGHLIGHTS_LONG_SENTENCE",
            {item.code for item in results if item.level == "WARN"},
        )

    def test_mobile_total_allows_flexible_bullet_distribution(self):
        data = valid_us_data()
        set_bullet_length(data, 1, 155)
        set_bullet_length(data, 2, 150)
        results = validate_final.validate(data)
        self.assertEqual({item.code for item in results if item.level == "FAIL"}, set())
        self.assertIn("MOBILE_DISTRIBUTION", {item.code for item in results if item.level == "WARN"})

    def test_mobile_total_of_320_passes(self):
        data = valid_us_data()
        set_bullet_length(data, 1, 160)
        set_bullet_length(data, 2, 160)
        self.assertEqual(failure_codes(data), set())

    def test_mobile_total_over_320_fails(self):
        data = valid_us_data()
        set_bullet_length(data, 1, 161)
        set_bullet_length(data, 2, 160)
        self.assertIn("MOBILE_FIRST_TWO", failure_codes(data))

    def test_amazon_bullet_limit_is_255(self):
        data = valid_us_data()
        set_bullet_length(data, 3, 256)
        self.assertIn("CHAR_LIMIT", failure_codes(data))

    def test_bullet_minimum_is_ten(self):
        data = valid_us_data()
        set_bullet_length(data, 3, 9)
        self.assertIn("BULLET_MIN_LENGTH", failure_codes(data))

    def test_bullet_only_prohibited_content_is_field_scoped(self):
        data = valid_us_data()
        data["bullets"][2] = "MATERIAL: Bamboo body"
        data["claim_audit"][4].update(
            phrase="Bamboo body", locations={"Bullet 3": "Bamboo body"}
        )
        data["translations"]["bullets"][2] = "材料：竹制杯体"
        self.assertIn("BULLET_PROHIBITED_CONTENT", failure_codes(data))

        title_data = valid_us_data()
        title_data["title"] = "Kavora Bamboo Blender Pitcher"
        title_data["claim_audit"][0].update(
            phrase=title_data["title"], locations={"Title": title_data["title"]}
        )
        title_data["translations"]["title"] = "Kavora 竹制搅拌杯"
        self.assertNotIn("BULLET_PROHIBITED_CONTENT", failure_codes(title_data))

    def test_german_bullet_prohibited_content_fails(self):
        data = valid_de_data()
        data["bullets"][2] = "Material: Griff aus Bambus"
        data["claim_audit"][4].update(
            phrase="Griff aus Bambus", locations={"Bullet 3": "Griff aus Bambus"}
        )
        data["translations"]["bullets"][2] = "材料：竹制手柄"
        self.assertIn("BULLET_PROHIBITED_CONTENT", failure_codes(data))

    def test_bullet_prohibited_symbol_fails(self):
        data = valid_us_data()
        data["bullets"][2] = "MATERIAL: Copolyester body™"
        data["claim_audit"][4].update(
            phrase="Copolyester body", locations={"Bullet 3": "Copolyester body"}
        )
        self.assertIn("BULLET_PROHIBITED_CHAR", failure_codes(data))

    def test_bullet_number_and_unit_style_are_warnings(self):
        data = valid_us_data()
        data["bullets"][3] = "PACKAGE: Includes 2 filters with 51mm diameter"
        data["claim_audit"][5].update(
            phrase="Includes 2 filters with 51mm diameter",
            locations={"Bullet 4": "Includes 2 filters with 51mm diameter"},
        )
        data["translations"]["bullets"][3] = "包装：包含 2 个直径为 51 mm 的滤网"
        warnings = {item.code for item in validate_final.validate(data) if item.level == "WARN"}
        self.assertIn("BULLET_NUMBER_STYLE", warnings)
        self.assertIn("BULLET_UNIT_SPACING", warnings)

    def test_keyword_outside_planned_bullet_fails(self):
        data = valid_us_data()
        data["keywords"] = [{
            "phrase": "pitcher", "source": "user_priority", "reported_used": 2,
            "eligible_locations": ["Bullet 1"],
        }]
        self.assertIn("KEYWORD_PLACEMENT", failure_codes(data))

    def test_pending_wording_leak_fails(self):
        data = valid_us_data()
        data["pending_blocked_terms"] = [{"term": "lid", "match": "phrase"}]
        self.assertIn("PENDING_LEAK", failure_codes(data))

    def test_chinese_pending_phrase_does_not_match_unrelated_numbered_tip(self):
        data = valid_us_data()
        data["pending_blocked_terms"] = [{"term": "2个滤篮", "match": "phrase"}]
        self.assertNotIn("PENDING_LEAK", failure_codes(data))

    def test_chinese_pending_phrase_still_blocks_real_match(self):
        data = valid_us_data()
        data["pending_blocked_terms"] = [{"term": "2个滤篮", "match": "phrase"}]
        data["description"] += "\n包装内含2个滤篮。"
        self.assertIn("PENDING_LEAK", failure_codes(data))

    def test_single_token_keyword_six_uses_warns(self):
        data = valid_us_data()
        for index in range(4):
            data["bullets"][index] += " pitcher"
        data["keywords"] = [{"phrase": "pitcher", "target": "1", "reported_used": 6}]
        results = validate_final.validate(data)
        self.assertFalse(any(item.level == "FAIL" and item.code == "KEYWORD_TARGET" for item in results))
        self.assertTrue(any(item.level == "WARN" and item.code == "KEYWORD_UPPER" for item in results))

    def test_single_token_keyword_seven_uses_fails(self):
        data = valid_us_data()
        for index in range(5):
            data["bullets"][index] += " pitcher"
        data["keywords"] = [{"phrase": "pitcher", "target": "1", "reported_used": 7}]
        self.assertIn("KEYWORD_TARGET", failure_codes(data))

    def test_multi_token_keyword_four_uses_fails(self):
        data = valid_us_data()
        for index in (0, 2, 3):
            data["bullets"][index] += " side handle"
        data["keywords"] = [{"phrase": "side handle", "target": "1", "reported_used": 4}]
        self.assertIn("KEYWORD_TARGET", failure_codes(data))

    def test_explicit_keyword_override_converts_cap_failure_to_warning(self):
        data = valid_us_data()
        for index in range(5):
            data["bullets"][index] += " pitcher"
        data["keywords"] = [{
            "phrase": "pitcher",
            "target": "1",
            "reported_used": 7,
            "override_reason": (
                "Bullets 1-5 use pitcher as the product subject for distinct capacity, "
                "pouring, material, package, and care questions; removal makes the subject ambiguous"
            ),
        }]
        results = validate_final.validate(data)
        self.assertFalse(any(item.level == "FAIL" and item.code == "KEYWORD_TARGET" for item in results))
        self.assertTrue(any(item.level == "WARN" and item.code == "KEYWORD_OVERRIDE" for item in results))

    def test_vague_seo_override_does_not_bypass_keyword_cap(self):
        data = valid_us_data()
        for index in range(5):
            data["bullets"][index] += " pitcher"
        data["keywords"] = [{
            "phrase": "pitcher",
            "target": "1",
            "reported_used": 7,
            "override_reason": "Important SEO keyword for ranking",
        }]
        self.assertIn("KEYWORD_OVERRIDE", failure_codes(data))

    def test_empty_fields_fail(self):
        data = valid_us_data()
        data.update(title="", highlights="", bullets=["", "", "", "", ""])
        self.assertIn("REQUIRED_FIELD", failure_codes(data))

    def test_claim_audit_must_not_be_empty(self):
        data = valid_us_data()
        data["claim_audit"] = []
        self.assertIn("CLAIM_SCHEMA", failure_codes(data))

    def test_claim_location_is_enforced(self):
        data = valid_us_data()
        data["claim_audit"][0]["locations"] = {"Title": "absent wording"}
        self.assertIn("CLAIM_LOCATION", failure_codes(data))

    def test_field_claim_map_must_match_verified_locations(self):
        data = valid_us_data()
        data["field_claim_ids"]["Bullet 1"] = ["A1"]
        self.assertIn("CLAIM_COVERAGE", failure_codes(data))

    def test_competitor_brand_leak_fails(self):
        data = valid_us_data()
        data["competitor_brands"] = ["RivalBrand"]
        data["title"] = "RivalBrand " + data["title"]
        data["claim_audit"][0].update(phrase=data["title"], locations={"Title": data["title"]})
        data["translations"]["title"] = "RivalBrand Kavora 64 oz 搅拌杯"
        self.assertIn("COMPETITOR_BRAND", failure_codes(data))

    def test_competitor_brand_in_translation_fails(self):
        data = valid_us_data()
        data["competitor_brands"] = ["RivalBrand"]
        data["translations"]["title"] = "RivalBrand " + data["translations"]["title"]
        self.assertIn("COMPETITOR_BRAND", failure_codes(data))

    def test_competitor_brand_in_audit_storage_does_not_fail(self):
        data = valid_us_data()
        data["competitor_brands"] = ["RivalBrand"]
        data["competitor_brand_audit_reason"] = "RivalBrand was identified and locked"
        self.assertNotIn("COMPETITOR_BRAND", failure_codes(data))

    def test_blocked_terms_must_be_a_list(self):
        data = valid_us_data()
        data["blocked_terms"] = "Kavora"
        self.assertIn("BLOCKED_SCHEMA", failure_codes(data))

    def test_competitors_must_be_a_list_of_strings(self):
        for value in ("copied competitor text", [123]):
            with self.subTest(value=value):
                data = valid_us_data()
                data["competitors"] = value
                self.assertIn("COMPETITOR_SCHEMA", failure_codes(data))

    def test_competitor_brands_must_contain_strings(self):
        data = valid_us_data()
        data["competitor_brands"] = [123]
        self.assertIn("BRAND_SCHEMA", failure_codes(data))

    def test_compatibility_targets_must_contain_strings(self):
        data = valid_us_data()
        data["compatibility_targets"] = [123]
        self.assertIn("COMPATIBILITY_SCHEMA", failure_codes(data))

    def test_unknown_package_quantity_allows_count_neutral_singular(self):
        data = valid_us_data()
        data["package_components"] = [{
            "component": "lid", "source_singular": ["lid"],
            "source_plural": ["lids"], "translation_terms": ["杯盖"],
            "present": True, "count": None,
            "listing_matches": {"Bullet 4": "lid"},
            "translation_matches": {"Bullet 4": "杯盖"},
        }]
        self.assertNotIn("PACKAGE_COUNT_UNKNOWN", failure_codes(data))

    def test_unknown_package_quantity_ignores_measurement_before_component(self):
        data = valid_de_data()
        data["package_components"] = [{
            "component": "double basket", "source_singular": ["Doppelsieb"],
            "source_plural": ["Doppelsiebe"], "translation_terms": ["滤篮"],
            "present": True, "count": None,
            "listing_matches": {"Bullet 4": "Doppelsieb"},
            "translation_matches": {"Bullet 4": "双份滤篮"},
        }]
        self.assertNotIn("PACKAGE_COUNT_UNKNOWN", failure_codes(data))

    def test_unknown_package_quantity_rejects_number_and_plural(self):
        data = valid_us_data()
        data["bullets"][3] = "SET CONTENTS: Pitcher and two lids included"
        data["claim_audit"][5].update(
            phrase="Pitcher and two lids included",
            locations={"Bullet 4": "Pitcher and two lids included"},
            translation_locations={"Bullet 4": "套装内容：包含杯体和两个杯盖"},
        )
        data["translations"]["bullets"][3] = "套装内容：包含杯体和两个杯盖"
        data["package_components"] = [{
            "component": "lid", "source_singular": ["lid"],
            "source_plural": ["lids"], "translation_terms": ["杯盖"],
            "present": True, "count": None,
            "listing_matches": {"Bullet 4": "two lids"},
            "translation_matches": {"Bullet 4": "两个杯盖"},
        }]
        self.assertIn("PACKAGE_COUNT_UNKNOWN", failure_codes(data))

    def test_unknown_package_quantity_cannot_hide_in_unmapped_context(self):
        data = valid_us_data()
        data["bullets"][3] = "SET CONTENTS: Pitcher and a lid included"
        data["claim_audit"][5].update(
            phrase="Pitcher and a lid included",
            locations={"Bullet 4": "Pitcher and a lid included"},
            translation_locations={"Bullet 4": "套装内容：包含杯体和一个杯盖"},
        )
        data["translations"]["bullets"][3] = "套装内容：包含杯体和一个杯盖"
        data["package_components"][1]["listing_matches"] = {"Bullet 4": "lid"}
        data["package_components"][1]["translation_matches"] = {"Bullet 4": "杯盖"}
        self.assertIn("PACKAGE_COUNT_UNKNOWN", failure_codes(data))

    def test_package_component_in_another_field_requires_another_location_match(self):
        data = valid_us_data()
        data["highlights"] += ", lid included"
        data["translations"]["highlights"] += "，包含杯盖"
        data["claim_audit"][1].update(
            phrase=data["highlights"],
            locations={"Product Highlights": data["highlights"]},
            translation_locations={"Product Highlights": data["translations"]["highlights"]},
        )
        self.assertIn("PACKAGE_LOCATION", failure_codes(data))

    def test_confirmed_package_count_requires_source_and_translation_count(self):
        data = valid_us_data()
        data["bullets"][3] = "SET CONTENTS: Pitcher and two lids included"
        data["claim_audit"][5].update(
            phrase="Pitcher and two lids included",
            locations={"Bullet 4": "Pitcher and two lids included"},
            translation_locations={"Bullet 4": "套装内容：包含杯体和两个杯盖"},
        )
        data["translations"]["bullets"][3] = "套装内容：包含杯体和两个杯盖"
        data["package_components"] = [{
            "component": "lid", "source_singular": ["lid"],
            "source_plural": ["lids"], "translation_terms": ["杯盖"],
            "present": True, "count": 2,
            "listing_matches": {"Bullet 4": "two lids"},
            "translation_matches": {"Bullet 4": "两个杯盖"},
        }]
        codes = failure_codes(data)
        self.assertNotIn("PACKAGE_COUNT", codes)
        self.assertNotIn("PACKAGE_COUNT_TRANSLATION", codes)
        data["translations"]["bullets"][3] = "套装内容：包含杯体和杯盖"
        data["claim_audit"][5]["translation_locations"] = {
            "Bullet 4": "套装内容：包含杯体和杯盖"
        }
        data["package_components"][0]["translation_matches"] = {
            "Bullet 4": "杯盖"
        }
        self.assertIn("PACKAGE_COUNT_TRANSLATION", failure_codes(data))

    def test_translation_allows_reordered_measurement_pairs(self):
        data = valid_us_data()
        source = "CHECK LENGTH: Measures 10 cm long and 5 mm wide"
        translated = "检查长度：宽度为5毫米，长度为10厘米"
        data["bullets"][0] = source
        data["translations"]["bullets"][0] = translated
        data["claim_audit"][2].update(
            phrase="Measures 10 cm long and 5 mm wide",
            locations={"Bullet 1": "Measures 10 cm long and 5 mm wide"},
            translation_locations={"Bullet 1": translated},
        )
        self.assertNotIn("TRANSLATION_UNIT", failure_codes(data))

    def test_phone_and_promise_fail(self):
        data = valid_us_data()
        data["highlights"] = "Call 123-456-7890 for a lifetime refund promise"
        data["claim_audit"][1].update(phrase=data["highlights"], locations={"Product Highlights": data["highlights"]})
        data["translations"]["highlights"] = "请拨打 123-456-7890 获取终身退款承诺"
        codes = failure_codes(data)
        self.assertIn("PHONE", codes)
        self.assertIn("PROMISE_OR_CONTACT", codes)

    def test_compatibility_relation_is_enforced(self):
        data = valid_us_data()
        data["title"] = "Kavora Blender Pitcher fits for Auron X7"
        data["compatibility_targets"] = ["Auron X7"]
        data["claim_audit"][0].update(phrase=data["title"], locations={"Title": data["title"]})
        data["translations"]["title"] = "Kavora 搅拌杯，适用于 Auron X7"
        self.assertIn("COMPATIBILITY_RELATION", failure_codes(data))

    def test_hepa_registered_as_group_a_still_fails(self):
        data = valid_us_data()
        data["title"] = "Kavora HEPA Filter 64 oz"
        data["claim_audit"][0].update(phrase=data["title"], locations={"Title": data["title"]})
        data["translations"]["title"] = "Kavora HEPA 滤芯 64 oz"
        self.assertIn("FILTER_CLAIM_CONFIRMATION", failure_codes(data))

    def test_confirmed_hepa_b_claim_passes(self):
        data = valid_us_data()
        data["title"] = "Kavora HEPA Filter 64 oz"
        data["claim_audit"][0].update(phrase="Kavora", locations={"Title": "Kavora"})
        data["claim_audit"].append({
            "id": "B1", "status": "confirmed_use", "phrase": "HEPA",
            "match": "phrase", "locations": {"Title": "HEPA"},
        })
        data["field_claim_ids"]["Title"] = ["A1", "B1"]
        data["translations"]["title"] = "Kavora HEPA 滤芯 64 oz"
        self.assertNotIn("FILTER_CLAIM_CONFIRMATION", failure_codes(data))

    def test_exact_material_grade_requires_confirmed_group_b(self):
        data = valid_de_data()
        source = "304er Edelstahlkopf, Holzgriff, Doppelauslauf"
        translated = "304号不锈钢头部、木柄、双出水口"
        data["highlights"] = source
        data["translations"]["highlights"] = translated
        data["claim_audit"][1].update(
            phrase=source,
            locations={"Product Highlights": source},
            translation_locations={"Product Highlights": translated},
        )
        codes = failure_codes(data)
        self.assertIn("RISK_CLAIM_CONFIRMATION", codes)
        self.assertIn("TRANSLATION_RISK_CLAIM_CONFIRMATION", codes)

        data["claim_audit"].append({
            "id": "B1", "status": "confirmed_use", "phrase": "304er Edelstahlkopf",
            "match": "phrase",
            "locations": {"Product Highlights": "304er Edelstahlkopf"},
            "translation_locations": {"Product Highlights": "304号不锈钢头部"},
        })
        data["field_claim_ids"]["Product Highlights"].append("B1")
        codes = failure_codes(data)
        self.assertNotIn("RISK_CLAIM_CONFIRMATION", codes)
        self.assertNotIn("TRANSLATION_RISK_CLAIM_CONFIRMATION", codes)

    def test_hepa_confirmation_does_not_enable_h13(self):
        data = valid_us_data()
        data["title"] = "Kavora HEPA H13 Filter 64 oz"
        data["claim_audit"][0].update(phrase="Kavora", locations={"Title": "Kavora"})
        data["claim_audit"].append({
            "id": "B1", "status": "confirmed_use", "phrase": "HEPA",
            "match": "phrase", "locations": {"Title": "HEPA"},
        })
        data["field_claim_ids"]["Title"] = ["A1", "B1"]
        data["translations"]["title"] = "Kavora HEPA H13 滤芯 64 oz"
        messages = [
            result.message for result in validate_final.validate(data)
            if result.code == "FILTER_CLAIM_CONFIRMATION"
        ]
        self.assertTrue(any("H13/H14" in message for message in messages))

    def test_confirmed_grade_after_joined_measurement_is_covered(self):
        for marketplace, source, translated, grade, grade_zh in (
            ("US", "51mm, 304 stainless steel", "51mm，304不锈钢", "304 stainless steel", "304不锈钢"),
            ("DE", "51mm, 304er Edelstahl", "51mm，304不锈钢", "304er Edelstahl", "304不锈钢"),
        ):
            with self.subTest(marketplace=marketplace):
                data = valid_us_data() if marketplace == "US" else valid_de_data()
                data["highlights"] = source
                data["translations"]["highlights"] = translated
                data["claim_audit"][1].update(
                    phrase=source, locations={"Product Highlights": source},
                    translation_locations={"Product Highlights": translated},
                )
                data["claim_audit"].append({
                    "id": "B1", "status": "confirmed_use", "phrase": grade, "match": "phrase",
                    "locations": {"Product Highlights": grade},
                    "translation_locations": {"Product Highlights": grade_zh},
                })
                data["field_claim_ids"]["Product Highlights"].append("B1")
                self.assertNotIn("RISK_CLAIM_CONFIRMATION", failure_codes(data))

    def test_joined_measurement_confirmation_does_not_cover_adjacent_h13(self):
        data = valid_us_data()
        source = "Kavora 51mm HEPA H13 Filter"
        translated = "Kavora 51mm HEPA H13滤芯"
        data["title"] = source
        data["translations"]["title"] = translated
        data["claim_audit"][0].update(
            phrase=source, locations={"Title": source},
            translation_locations={"Title": translated},
        )
        data["claim_audit"].append({
            "id": "B1", "status": "confirmed_use", "phrase": "51mm HEPA", "match": "phrase",
            "locations": {"Title": "51mm HEPA"},
            "translation_locations": {"Title": "51mm HEPA"},
        })
        data["field_claim_ids"]["Title"].append("B1")
        messages = [item.message for item in validate_final.validate(data)
                    if item.code == "FILTER_CLAIM_CONFIRMATION"]
        self.assertTrue(any("H13/H14" in message for message in messages))

    def test_confirmed_hepa_inside_chinese_mapping_is_covered(self):
        data = valid_us_data()
        source = "Kavora HEPA Filter 64 oz"
        translated = "Kavora 滤芯采用HEPA滤材 64 oz"
        data["title"] = source
        data["translations"]["title"] = translated
        data["claim_audit"][0].update(
            phrase=source, locations={"Title": source},
            translation_locations={"Title": translated},
        )
        data["claim_audit"].append({
            "id": "B1", "status": "confirmed_use", "phrase": "HEPA Filter", "match": "phrase",
            "locations": {"Title": "HEPA Filter"},
            "translation_locations": {"Title": "HEPA滤材"},
        })
        data["field_claim_ids"]["Title"].append("B1")
        self.assertNotIn("TRANSLATION_RISK_CLAIM_CONFIRMATION", failure_codes(data))

    def test_latin_confirmation_keeps_full_field_unit_boundaries_in_chinese(self):
        data = valid_us_data()
        source = "Kavora 51mm HEPA Filter 64 oz"
        translated = "Kavora 滤芯51mm尺寸HEPA滤材 64 oz"
        data["title"] = source
        data["translations"]["title"] = translated
        data["claim_audit"][0].update(
            phrase=source, locations={"Title": source},
            translation_locations={"Title": translated},
        )
        data["claim_audit"].append({
            "id": "B1", "status": "confirmed_use", "phrase": "HEPA Filter", "match": "phrase",
            "locations": {"Title": "HEPA Filter"}, "translation_locations": {"Title": "HEPA"},
        })
        data["field_claim_ids"]["Title"].append("B1")
        self.assertNotIn("TRANSLATION_RISK_CLAIM_CONFIRMATION", failure_codes(data))

    def test_translation_number_and_polarity_are_enforced(self):
        data = valid_us_data()
        data["translations"]["title"] = "Kavora 搅拌杯"
        data["bullets"][4] = "CARE: Do not machine wash the pitcher"
        data["claim_audit"][6].update(
            phrase="Do not machine wash the pitcher",
            locations={"Bullet 5": "Do not machine wash the pitcher"},
        )
        data["translations"]["bullets"][4] = "保养：清洗搅拌杯"
        codes = failure_codes(data)
        self.assertIn("TRANSLATION_NUMBER", codes)
        self.assertIn("TRANSLATION_POLARITY", codes)

    def test_chinese_adjacent_numbers_and_units_are_accepted(self):
        results = []
        validate_final.translation_consistency(
            results,
            "Bullet",
            "0.14 in, 1.9 L, 9.8 in, 4-fin, not for 3-fin",
            "0.14英寸、1.9升、9.8英寸、4翼、不适用于3翼",
            [],
        )
        self.assertEqual([result for result in results if result.level == "FAIL"], [])

    def test_single_digit_chinese_numeral_is_accepted(self):
        results = []
        validate_final.translation_consistency(
            results, "Title", "Portafilter with 3 ears", "三耳咖啡机手柄", [],
        )
        self.assertNotIn("TRANSLATION_NUMBER", {result.code for result in results if result.level == "FAIL"})

    def test_location_matches_replace_redundant_global_phrase_check(self):
        data = valid_us_data()
        data["claim_audit"][0]["phrase"] = "canonical identity wording"
        self.assertNotIn("CLAIM_REQUIRED", failure_codes(data))

    def test_internal_audit_wording_fails(self):
        data = valid_de_data()
        data["bullets"][2] = "Material: Die Angaben beziehen sich jeweils auf diese Bauteile"
        data["claim_audit"][4].update(
            phrase="Die Angaben beziehen sich jeweils auf diese Bauteile",
            locations={"Bullet 3": "Die Angaben beziehen sich jeweils auf diese Bauteile"},
        )
        data["translations"]["bullets"][2] = "材料：这些说明分别对应各个部件"
        self.assertIn("META_LANGUAGE", failure_codes(data))

    def test_bullet_one_five_duplication_warns(self):
        data = valid_us_data()
        data["bullets"][4] = data["bullets"][0]
        data["claim_audit"][6].update(
            phrase="Holds 64 oz", locations={"Bullet 5": "Holds 64 oz"},
        )
        data["translations"]["bullets"][4] = data["translations"]["bullets"][0]
        warnings = {result.code for result in validate_final.validate(data) if result.level == "WARN"}
        self.assertIn("READABILITY_DUPLICATE", warnings)

    def test_bullet_five_claim_subset_warns(self):
        data = valid_us_data()
        shared = "Holds 64 oz; side handle supports pouring"
        data["bullets"][0] = "CHECK CAPACITY: " + shared
        data["bullets"][4] = "CHECK AGAIN: " + shared
        data["claim_audit"][2]["locations"] = {"Bullet 1": "Holds 64 oz", "Bullet 5": "Holds 64 oz"}
        data["claim_audit"][3]["locations"] = {
            "Bullet 1": "side handle supports pouring",
            "Bullet 2": "Side handle supports pouring",
            "Bullet 5": "side handle supports pouring",
        }
        data["field_claim_ids"]["Bullet 1"] = ["A3", "A4"]
        data["field_claim_ids"]["Bullet 5"] = ["A3", "A4"]
        data["translations"]["bullets"][0] = "检查容量：容量 64 oz；侧面把手帮助倾倒"
        data["translations"]["bullets"][4] = "再次检查：容量 64 oz；侧面把手帮助倾倒"
        warnings = {result.code for result in validate_final.validate(data) if result.level == "WARN"}
        self.assertIn("READABILITY_DUPLICATE_CLAIMS", warnings)


class PipelineTests(unittest.TestCase):
    def test_brand_and_pending_guards_survive_punctuation_before_numbers(self):
        for marketplace in ("US", "DE"):
            for guard in ("brand", "pending"):
                for separator in (",", ".", ", "):
                    with self.subTest(marketplace=marketplace, guard=guard, separator=separator):
                        data = self.certification_lamp_input(marketplace)
                        suffix = "RivalBrand" + separator + "64 oz"
                        data["draft"]["highlights"] += ", " + suffix
                        data["draft"]["translations"]["highlights"] += "，" + suffix
                        self.assertTrue(pipeline.run(data)["hard_pass"])
                        if guard == "brand":
                            data["draft"]["competitor_brands"] = ["RivalBrand"]
                            expected = "COMPETITOR_BRAND"
                        else:
                            data["pending_facts"] = [{
                                "claim": "Unverified name", "claim_zh": "名称待确认",
                                "blocked_renderings": ["RivalBrand"],
                            }]
                            expected = "PENDING_LEAK"
                        result = pipeline.run(data)
                        self.assertFalse(result["hard_pass"])
                        self.assertIn(expected, {
                            row["code"] for row in result["validation"] if row["level"] == "FAIL"
                        })
                        self.assertNotIn("## Final Listing", pipeline.render_listing_markdown(result))

    def test_duplicate_confirmed_records_cannot_supply_a_third_fact(self):
        for marketplace, source, chinese in (
            ("US", "UL listed", "UL列名"),
            ("DE", "CE-Kennzeichnung", "CE标志"),
        ):
            for mode in ("target_anchored", "consensus_only"):
                for across_containers in (False, True):
                    with self.subTest(marketplace=marketplace, mode=mode, across=across_containers):
                        data = self.certification_lamp_input(marketplace)
                        data["facts"] = data["facts"][:1]
                        data["mode"] = mode
                        data["facts"][0]["competitors"] = [data["facts"][0]["claim"]]
                        data["draft"]["highlights"] = source
                        data["draft"]["translations"]["highlights"] = chinese
                        claim = {
                            "claim": source, "claim_zh": chinese, "seller_confirmed": True,
                            "listing_matches": {"Item Highlights": source},
                            "translation_matches": {"Item Highlights": chinese},
                        }
                        if across_containers:
                            data["facts"].append(dict(claim, field="certification", competitors=[source]))
                        else:
                            data["risk_candidates"].append(copy.deepcopy(claim))
                        duplicate = copy.deepcopy(claim)
                        duplicate["claim"] = "  " + source.upper() + "  "
                        data["risk_candidates"].append(duplicate)
                        result = pipeline.run(data)
                        self.assertTrue(result["hard_pass"])
                        self.assertTrue(result["clarification_required"])
                        self.assertEqual(result["evidence"]["content_units"], 2)
                        self.assertEqual(result["evidence"]["publication_counts"]["confirmed_b"], 1)
                        self.assertEqual([row["id"] for row in result["claim_review"]["b_claims"]], ["B1", "B2"])
                        self.assertNotIn("## Final Listing", pipeline.render_listing_markdown(result))
                        duplicate["translation_matches"] = {"Item Highlights": "不存在的译文"}
                        self.assertFalse(pipeline.run(data)["hard_pass"])

    def test_duplicate_risk_in_different_fields_counts_once(self):
        data = self.certification_lamp_input(ordinary_count=2)
        for field in ("title", "highlights"):
            data["draft"][field] += ", UL listed"
            data["draft"]["translations"][field] += "，UL列名"
        data["risk_candidates"] = [{
            "claim": "UL listed", "claim_zh": "UL列名",
            "listing_matches": {field: "UL listed"},
            "translation_matches": {field: "UL列名"},
        } for field in ("Title", "Item Highlights")]
        data["confirmation_text"] = "B已核实并启用：B1、B2"
        result = pipeline.run(data)
        self.assertTrue(result["hard_pass"])
        self.assertFalse(result["clarification_required"])
        self.assertEqual(result["evidence"]["content_units"], 3)
        self.assertEqual(len(result["claim_review"]["b_claims"]), 2)

    def test_publication_dedup_preserves_component_and_claim_scope(self):
        for pairs in (
            (("UL listed base", "UL列名底座"), ("UL listed adapter", "UL列名适配器")),
            (("FDA approved", "FDA批准"), ("FDA cleared", "FDA许可")),
            (("304 stainless steel body", "304不锈钢杯体"), ("316 stainless steel body", "316不锈钢杯体")),
        ):
            with self.subTest(pairs=pairs):
                data = self.certification_lamp_input()
                data["facts"] = data["facts"][:1]
                data["draft"]["highlights"] = ", ".join(source for source, _ in pairs)
                data["draft"]["translations"]["highlights"] = "，".join(chinese for _, chinese in pairs)
                data["risk_candidates"] = [{
                    # Even a shared generic label must retain scoped renderings.
                    "claim": "Verified component specification", "claim_zh": "已核实部件规格",
                    "high_risk": True, "seller_confirmed": True,
                    "listing_matches": {"Item Highlights": source},
                    "translation_matches": {"Item Highlights": chinese},
                } for source, chinese in pairs]
                result = pipeline.run(data)
                self.assertTrue(result["hard_pass"], result["validation"])
                self.assertFalse(result["clarification_required"])
                self.assertEqual(result["evidence"]["content_units"], 3)

    def numeric_model_input(self, marketplace="US", scope="title_highlights", wording=None):
        source = wording or (("Model " if marketplace == "US" else "Modell ") + "12345678")
        chinese = "编号12345678"
        if scope == "title_highlights":
            data = self.certification_lamp_input(marketplace)
        else:
            data = self.compact_input() if marketplace == "US" else self.pipeline_input("DE")
        draft = data.get("draft", data.get("final"))
        draft["highlights"] += ", " + source
        draft["translations"]["highlights"] += "，" + chinese
        if "facts" in data:
            claim = {
                "field": "model", "claim": source, "claim_zh": chinese,
                "listing_matches": {"Item Highlights": source},
                "translation_matches": {"Item Highlights": chinese},
            }
            data["facts"].append(claim)
        else:
            claim = {
                "claim": source, "claim_zh": chinese, "locations": ["Item Highlights"],
                "location_matches": {"Item Highlights": source},
                "translation_location_matches": {"Item Highlights": chinese},
            }
            data["claim_review"]["claims"].append(claim)
        return data

    def test_mapped_numeric_models_pass_in_both_markets_and_scopes(self):
        for marketplace, labels in (
            ("US", ("Model ", "model no.: ", "Part No. ", "MODEL\tNUMBER: ")),
            ("DE", ("Modell ", "Modellnummer: ", "Teilenummer ", "MODELLNUMMER:")),
        ):
            for scope in ("full_listing", "title_highlights"):
                for label in labels:
                    with self.subTest(marketplace=marketplace, scope=scope, label=label):
                        data = self.numeric_model_input(marketplace, scope, label + "12345678")
                        result = pipeline.run(data)
                        self.assertTrue(result["hard_pass"], [
                            row for row in result["validation"] if row["level"] == "FAIL"
                        ])
                        self.assertIn("## Final Listing", pipeline.render_listing_markdown(result))
                        self.assertNotIn("PHONE", failure_codes(result["final_payload"]))

    def test_numeric_model_exception_requires_complete_same_field_mapping(self):
        for change in ("missing", "digits_only", "other_field", "invalid_translation", "held"):
            with self.subTest(change=change):
                data = self.numeric_model_input()
                claim = data["facts"][-1]
                if change == "missing":
                    data["facts"].pop()
                elif change == "digits_only":
                    claim["listing_matches"] = {"Item Highlights": "12345678"}
                elif change == "other_field":
                    claim["listing_matches"] = {"Title": claim["claim"]}
                    claim["translation_matches"] = {"Title": claim["claim_zh"]}
                elif change == "invalid_translation":
                    claim["translation_matches"] = {"Item Highlights": "编号99999999"}
                else:
                    data["facts"].pop()
                    data["risk_candidates"] = [{
                        "claim": claim["claim"], "claim_zh": claim["claim_zh"],
                        "high_risk": True,
                    }]
                result = pipeline.run(data)
                self.assertFalse(result["hard_pass"])
                self.assertIn("PHONE", {
                    row["code"] for row in result["validation"] if row["level"] == "FAIL"
                })

    def test_numeric_model_exception_does_not_hide_contact_numbers(self):
        for marketplace, wording in (
            ("US", "12345678"), ("US", "Call Model 12345678"),
            ("DE", "Telefon Modell 12345678"),
            ("US", "Model +12345678"), ("US", "Model 1234-5678"),
            ("US", "Model 1234 5678"), ("US", "Model 12345678 contact"),
            ("US", "Model 12345678; call 12345678"),
            ("DE", "Modell 12345678; Telefon 12345678"),
        ):
            with self.subTest(marketplace=marketplace, wording=wording):
                data = self.numeric_model_input(marketplace, wording=wording)
                result = pipeline.run(data)
                self.assertFalse(result["hard_pass"])
                self.assertTrue(any(row["code"] == "PHONE" for row in result["validation"]))
        data = self.numeric_model_input(wording="Model 12345678; call 212-555-0187")
        data["draft"]["translations"]["highlights"] += "，致电212-555-0187"
        result = pipeline.run(data)
        phones = [row["message"] for row in result["validation"] if row["code"] == "PHONE"]
        self.assertEqual(len(phones), 1)
        self.assertIn("212-555-0187", phones[0])

    def test_model_number_abbreviation_preserves_real_negation(self):
        for source, chinese, should_fail in (
            ("Model No. 12345678", "编号12345678", False),
            ("No Model 12345678", "编号12345678", True),
            ("Model No. 12345678; no lid", "编号12345678，含杯盖", True),
            ("Model No. 12345678; no lid", "编号12345678，无杯盖", False),
            ("Modellnummer: 12345678 ohne Deckel", "编号12345678，含杯盖", True),
        ):
            with self.subTest(source=source, chinese=chinese):
                results = []
                validate_final.translation_consistency(results, "Title", source, chinese, [])
                self.assertEqual(
                    "TRANSLATION_POLARITY" in {row.code for row in results}, should_fail,
                )

    def test_translated_measurements_accept_unit_joining_without_changing_value(self):
        for source_value, chinese_value, passes in (
            ("51 mm", "51mm", True), ("51-mm", "51 mm", True),
            ("51 mm", "51-mm", True), ("1.5 mm", "1.5-mm", True),
            ("1,5 mm", "1,5-mm", True),
            ("51 mm", "51-cm", False), ("51 mm", "510-mm", False),
            ("1.5 mm", "1-5 mm", False),
        ):
            with self.subTest(source=source_value, translated=chinese_value):
                data = self.compact_input()
                source, chinese = f"Measures {source_value}", f"尺寸为{chinese_value}"
                data["draft"]["bullets"][0] = "SIZE: " + source
                data["draft"]["translations"]["bullets"][0] = chinese
                data["facts"][2].update(
                    claim=source, claim_zh=chinese, target=source,
                    listing_matches={"Bullet 1": source},
                    translation_matches={"Bullet 1": chinese},
                )
                data["competitor_bullets"] = []
                data["keyword_candidates"] = []
                result = pipeline.run(data)
                self.assertEqual(result["hard_pass"], passes)
                if not passes:
                    self.assertIn("TRANSLATION_UNIT", {
                        row["code"] for row in result["validation"] if row["level"] == "FAIL"
                    })

    def test_package_quantities_survive_intervening_measurements(self):
        for source, chinese, pitcher_count, lid_count, passes in (
            ("Pitcher and two 5 mm lids included", "搅拌杯和两个5毫米杯盖", None, 2, True),
            ("Pitcher and two 5 mm lids included", "搅拌杯和三个5毫米杯盖", None, 2, False),
            ("Pitcher and 5 mm lid included", "搅拌杯和5毫米杯盖", None, None, True),
            ("Pitcher and a 5 mm lid included", "搅拌杯和一个5毫米杯盖", None, None, False),
            ("Two pitchers, 5 mm lid included", "两个搅拌杯和5毫米杯盖", 2, None, True),
            ("Two pitchers and 5 mm lid included", "两个搅拌杯和5毫米杯盖", 2, None, True),
        ):
            with self.subTest(source=source, chinese=chinese):
                data = self.compact_input()
                data["draft"]["bullets"][3] = "SET CONTENTS: " + source
                data["draft"]["translations"]["bullets"][3] = chinese
                data["facts"][5].update(
                    claim=source, claim_zh=chinese, target=source,
                    listing_matches={"Bullet 4": source},
                    translation_matches={"Bullet 4": chinese},
                )
                data["package_components"][0]["count"] = pitcher_count
                data["package_components"][0]["listing_matches"]["Bullet 4"] = (
                    "pitchers" if pitcher_count else "pitcher"
                )
                data["package_components"][1]["count"] = lid_count
                data["package_components"][1]["listing_matches"]["Bullet 4"] = (
                    "lids" if "lids" in source else "lid"
                )
                result = pipeline.run(data)
                self.assertEqual(result["hard_pass"], passes)
                if not passes:
                    self.assertIn("PACKAGE_COUNT_UNKNOWN" if lid_count is None else "PACKAGE_COUNT_TRANSLATION", {
                        row["code"] for row in result["validation"] if row["level"] == "FAIL"
                    })
        self.assertEqual(validate_final.latin_counts_near_terms(
            "zwei 18-g-Doppelsiebe", ["Doppelsiebe"]
        ), [2])
        self.assertEqual(validate_final.latin_counts_near_terms(
            "two 5 mm lids", ["5 mm lids"]
        ), [2])
        self.assertEqual(validate_final.chinese_counts_near_terms(
            "两个5毫米杯盖", ["5毫米杯盖"]
        ), [2])

    def test_package_counts_do_not_cross_semicolon_boundaries(self):
        for source, chinese, lid_count, passes in (
            ("Two pitchers; lid included", "两个搅拌杯；包含杯盖", None, True),
            ("Lid included; two pitchers", "包含杯盖；两个搅拌杯", None, True),
            ("Two pitchers; two lids included", "两个搅拌杯；包含两个杯盖", None, False),
            ("Two pitchers; two lids included", "两个搅拌杯；包含两个杯盖", 2, True),
        ):
            with self.subTest(source=source, lid_count=lid_count):
                data = self.compact_input()
                data["draft"]["bullets"][3] = "SET CONTENTS: " + source
                data["draft"]["translations"]["bullets"][3] = chinese
                data["facts"][5].update(
                    claim=source, claim_zh=chinese, target=source,
                    listing_matches={"Bullet 4": source},
                    translation_matches={"Bullet 4": chinese},
                )
                data["package_components"][0]["count"] = 2
                data["package_components"][0]["listing_matches"]["Bullet 4"] = "pitchers"
                data["package_components"][1]["count"] = lid_count
                data["package_components"][1]["listing_matches"]["Bullet 4"] = (
                    "lids" if "lids" in source else "lid"
                )
                result = pipeline.run(data)
                self.assertEqual(result["hard_pass"], passes)
                if not passes:
                    self.assertIn("PACKAGE_COUNT_UNKNOWN", {
                        row["code"] for row in result["validation"] if row["level"] == "FAIL"
                    })

    def test_chinese_pending_size_only_blocks_the_complete_value(self):
        for number, passes in (("15", True), ("1.5", True), ("5", False)):
            data = self.compact_input()
            source, chinese = f"Measures {number} mm", f"尺寸为{number}毫米"
            data["draft"]["bullets"][0] = "SIZE: " + source
            data["draft"]["translations"]["bullets"][0] = chinese
            data["facts"][2].update(
                claim=source, claim_zh=chinese, target=source,
                listing_matches={"Bullet 1": source},
                translation_matches={"Bullet 1": chinese},
            )
            data["competitor_bullets"] = []
            data["keyword_candidates"] = []
            data["pending_facts"] = [{
                "claim": "Optional spool hole size", "claim_zh": "可选线轴孔径",
                "blocked_renderings": ["5毫米"],
            }]
            result = pipeline.run(data)
            self.assertEqual(result["hard_pass"], passes)
            self.assertEqual("## Final Listing" in pipeline.render_listing_markdown(result), passes)

    def test_numeric_keyword_counts_and_pending_guards_use_complete_values(self):
        for wording, passes in (("1.5 mm", False), ("5 mm", True)):
            data = self.compact_input()
            source, chinese = f"Measures {wording}", f"尺寸为{wording}"
            data["draft"]["bullets"][0] = "SIZE: " + source
            data["draft"]["translations"]["bullets"][0] = chinese
            data["facts"][2].update(
                claim=source, claim_zh=chinese, target=source,
                listing_matches={"Bullet 1": source},
                translation_matches={"Bullet 1": chinese},
            )
            data["competitor_bullets"] = [["Measures 1.5 mm"], ["Diameter 1.5 mm"]]
            data["keyword_candidates"] = ["5 mm"]
            data["priority_keywords"] = [{"term": "5 mm", "eligible_locations": ["Bullet 1"]}]
            result = pipeline.run(data)
            self.assertEqual(result["hard_pass"], passes)
            self.assertEqual(result["keyword_benchmark"][0]["counts"], [0, 0])
            self.assertEqual(result["final_payload"]["keywords"][0]["reported_used"], int(passes))
            self.assertEqual("## Final Listing" in pipeline.render_listing_markdown(result), passes)
            data.pop("priority_keywords")
            data["keyword_candidates"] = []
            data["competitor_bullets"] = []
            data["pending_facts"] = [{
                "claim": "Optional spool hole size", "claim_zh": "可选线轴孔径",
                "blocked_renderings": ["5 mm"],
            }]
            self.assertEqual(pipeline.run(data)["hard_pass"], not passes)

    def test_compact_requires_complete_chinese_translations(self):
        draft = {
            "title": "Title", "highlights": "Highlights",
            "bullets": ["b1", "b2", "b3", "b4", "b5"],
            "description": "Description",
            "translations": {
                "title": "标题", "highlights": "亮点",
                "bullets": ["一", "二", "三", "四", ""],
                "description": "描述",
            },
        }
        with self.assertRaisesRegex(ValueError, "five non-empty Chinese translations"):
            pipeline.validate_required_translations(draft, "full_listing")

    def pipeline_input(self, marketplace: str = "US") -> dict[str, object]:
        final = valid_de_data() if marketplace == "DE" else valid_us_data()
        audit = final.pop("claim_audit")
        final.pop("field_claim_ids")
        claims = []
        for item in audit:
            claims.append({
                "claim": item["phrase"], "use_text": item["phrase"],
                "match_text": item["phrase"],
                "locations": list(item["locations"]),
                "location_matches": copy.deepcopy(item["locations"]),
                "translation_location_matches": copy.deepcopy(
                    item["translation_locations"]
                ),
            })
        return {
            "claim_review": {"marketplace": marketplace, "claims": claims},
            "final": final,
            "auto_field_claim_ids": True,
        }

    def test_pipeline_derives_claim_map_and_passes(self):
        result = pipeline.run(self.pipeline_input())
        self.assertTrue(result["hard_pass"])
        self.assertEqual(result["final_payload"]["field_claim_ids"]["Bullet 5"], ["A7"])

    def test_legacy_pipeline_normalizes_scope_and_mode_metadata(self):
        result = pipeline.run(self.pipeline_input("DE"))
        self.assertTrue(result["hard_pass"])
        self.assertEqual(result["final_payload"]["scope"], "full_listing")
        self.assertEqual(result["final_payload"]["mode"], "legacy")

    def test_modified_field_is_revalidated(self):
        data = self.pipeline_input()
        data["final"]["title"] = "Changed title"
        result = pipeline.run(data)
        codes = {item["code"] for item in result["validation"] if item["level"] == "FAIL"}
        self.assertIn("CLAIM_LOCATION", codes)

    def test_pipeline_derives_keyword_targets_from_bullets(self):
        data = self.pipeline_input()
        data["keyword_benchmark"] = {
            "keywords": ["64 oz"],
            "competitors": [["Holds 64 oz"], ["Capacity is 64 oz"]],
        }
        result = pipeline.run(data)
        self.assertEqual(result["final_payload"]["keywords"][0]["reported_used"], 1)

    def test_pipeline_rejects_whole_competitor_blocks_for_bullet_benchmark(self):
        data = self.pipeline_input()
        data["keyword_benchmark"] = {
            "keywords": ["64 oz"],
            "competitors": ["TITLE 64 oz\nBULLET Holds 64 oz"],
        }
        with self.assertRaisesRegex(ValueError, "lists of Bullet strings"):
            pipeline.run(data)

    def test_pipeline_rejects_nonstring_competitor_bullets(self):
        data = self.pipeline_input()
        data["keyword_benchmark"] = {
            "keywords": ["64 oz"],
            "competitors": [[{"text": "Holds 64 oz"}]],
        }
        with self.assertRaisesRegex(ValueError, "only Bullet strings"):
            pipeline.run(data)

    def compact_input(self) -> dict[str, object]:
        final = valid_us_data()
        audit = final.pop("claim_audit")
        final.pop("field_claim_ids")
        final.pop("keywords")
        final.pop("competitors")
        package_components = final.pop("package_components")
        final["competitor_brand_audit_reason"] = "No seller brand is present in the test data"
        facts = []
        for index, item in enumerate(audit, start=1):
            location, wording = next(iter(item["locations"].items()))
            if location == "Title":
                claim_zh = final["translations"]["title"]
            elif location == "Product Highlights":
                claim_zh = final["translations"]["highlights"]
            elif location.startswith("Bullet "):
                claim_zh = final["translations"]["bullets"][int(location.split()[-1]) - 1]
            else:
                claim_zh = item["translation_locations"][location]
            facts.append({
                "field": f"fact_{index}",
                "claim": item["phrase"],
                "claim_zh": claim_zh,
                "claim_type": "attribute",
                "strategy": "exact",
                "target": item["phrase"],
                "competitors": [],
                "listing_matches": copy.deepcopy(item["locations"]),
                "translation_matches": copy.deepcopy(
                    item["translation_locations"]
                ),
            })
        return {
            "schema_version": 2,
            "marketplace": "US",
            "mode": "target_anchored",
            "facts": facts,
            "risk_candidates": [],
            "pending_facts": [],
            "package_components": package_components,
            "exclusions": [{
                "display": "[竞争品牌词已排除]", "term": "RivalBrand",
                "reason": "竞品卖家品牌", "kind": "competitor_brand",
            }],
            "draft": final,
            "competitor_bullets": [["Holds 64 oz"], ["Capacity is 64 oz"]],
            "keyword_candidates": ["64 oz"],
        }

    def title_highlights_input(self) -> dict[str, object]:
        return {
            "schema_version": 2,
            "marketplace": "US",
            "mode": "target_anchored",
            "scope": "title_highlights",
            "facts": [
                {
                    "field": "brand",
                    "claim": "Kavora",
                    "claim_zh": "Kavora 品牌",
                    "listing_matches": {"Title": "Kavora"},
                    "translation_matches": {"Title": "Kavora"},
                },
                {
                    "field": "product_type",
                    "claim": "Pipe cleaners",
                    "claim_zh": "扭扭棒",
                    "listing_matches": {"Title": "Pipe Cleaners"},
                    "translation_matches": {"Title": "扭扭棒"},
                },
                {
                    "field": "craft_use",
                    "claim": "For crafts",
                    "claim_zh": "用于手工制作",
                    "listing_matches": {"Title": "for Crafts"},
                    "translation_matches": {"Title": "手工"},
                },
                {
                    "field": "color",
                    "claim": "Pink",
                    "claim_zh": "粉色",
                    "listing_matches": {"Item Highlights": "Pink"},
                    "translation_matches": {"Item Highlights": "粉色"},
                },
                {
                    "field": "form",
                    "claim": "Chenille stems",
                    "claim_zh": "雪尼尔棒",
                    "listing_matches": {"Item Highlights": "chenille stems"},
                    "translation_matches": {"Item Highlights": "雪尼尔棒"},
                },
                {
                    "field": "kit_use",
                    "claim": "Flower-making kit",
                    "claim_zh": "花朵制作套装",
                    "listing_matches": {"Item Highlights": "flower-making kit"},
                    "translation_matches": {"Item Highlights": "花朵制作套装"},
                },
            ],
            "risk_candidates": [],
            "pending_facts": [],
            "package_components": [],
            "exclusions": [],
            "draft": {
                "title": "Kavora Pipe Cleaners for Crafts",
                "highlights": "Pink chenille stems, flower-making kit",
                "translations": {
                    "title": "Kavora 手工扭扭棒",
                    "highlights": "粉色雪尼尔棒，花朵制作套装",
                },
            },
            "priority_keywords": [{
                "term": "pipe cleaners",
                "variants": ["pipe cleaner"],
                "eligible_locations": ["Title"],
            }],
        }

    def test_number_parser_does_not_split_joined_decimal_unit(self):
        self.assertEqual(validate_final.normalized_numbers("8.66in animated Santa"), ["8.66"])
        self.assertEqual(validate_final.normalized_numbers("8.66 in animated Santa"), ["8.66"])

    def test_title_highlights_scope_runs_only_selected_fields(self):
        data = self.title_highlights_input()
        result = pipeline.run(data)
        self.assertTrue(result["hard_pass"])
        self.assertEqual(result["measurements"], {
            "Title": len(data["draft"]["title"]),
            "Product Highlights": len(data["draft"]["highlights"]),
        })
        self.assertEqual(
            set(result["final_payload"]["field_claim_ids"]),
            {"Title", "Product Highlights"},
        )
        markdown = pipeline.render_listing_markdown(result)
        self.assertIn("### Title", markdown)
        self.assertIn("### Item Highlights", markdown)
        self.assertNotIn("Product Highlights", markdown)
        self.assertNotIn("### Bullet Points", markdown)
        self.assertNotIn("### Product Description", markdown)

    def test_title_highlights_german_scope_skips_description_rules(self):
        data = self.title_highlights_input()
        data["marketplace"] = "DE"
        data["facts"] = [
            {
                "field": "brand", "claim": "Kavora", "claim_zh": "Kavora 品牌",
                "listing_matches": {"Title": "Kavora"},
                "translation_matches": {"Title": "Kavora"},
            },
            {
                "field": "product_type", "claim": "Pfeifenreiniger", "claim_zh": "扭扭棒",
                "listing_matches": {"Title": "Pfeifenreiniger"},
                "translation_matches": {"Title": "扭扭棒"},
            },
            {
                "field": "craft_use", "claim": "Zum Basteln", "claim_zh": "用于手工制作",
                "listing_matches": {"Title": "zum Basteln"},
                "translation_matches": {"Title": "手工"},
            },
            {
                "field": "color", "claim": "Rosa", "claim_zh": "粉色",
                "listing_matches": {"Product Highlights": "Rosa"},
                "translation_matches": {"Product Highlights": "粉色"},
            },
            {
                "field": "form", "claim": "Chenilledraht", "claim_zh": "雪尼尔棒",
                "listing_matches": {"Product Highlights": "Chenilledraht"},
                "translation_matches": {"Product Highlights": "雪尼尔棒"},
            },
            {
                "field": "kit_use", "claim": "Set zum Basteln von Blumen", "claim_zh": "花朵制作套装",
                "listing_matches": {"Product Highlights": "Set zum Basteln von Blumen"},
                "translation_matches": {"Product Highlights": "花朵制作套装"},
            },
        ]
        data["draft"] = {
            "title": "Kavora Pfeifenreiniger zum Basteln",
            "highlights": "Rosa Chenilledraht, Set zum Basteln von Blumen",
            "translations": {
                "title": "Kavora 手工扭扭棒",
                "highlights": "粉色雪尼尔棒，花朵制作套装",
            },
        }
        data["priority_keywords"] = [{
            "term": "Pfeifenreiniger",
            "eligible_locations": ["Title"],
        }]
        result = pipeline.run(data)
        self.assertTrue(result["hard_pass"])
        self.assertNotIn(
            "WARM_TIPS",
            {item["code"] for item in result["validation"] if item["level"] == "FAIL"},
        )

    def test_title_highlights_rejects_unknown_scope(self):
        data = self.title_highlights_input()
        data["scope"] = "title_only"
        with self.assertRaisesRegex(ValueError, "scope must be"):
            pipeline.run(data)

    def test_title_highlights_rejects_unselected_draft_fields(self):
        data = self.title_highlights_input()
        data["draft"]["bullets"] = ["Legacy Bullet"]
        with self.assertRaisesRegex(ValueError, "must omit Bullets"):
            pipeline.run(data)

    def test_title_highlights_rejects_empty_out_of_scope_fields(self):
        data = self.title_highlights_input()
        data["draft"]["bullets"] = []
        data["draft"]["description"] = ""
        data["draft"]["translations"]["bullets"] = []
        data["draft"]["translations"]["description"] = ""
        with self.assertRaisesRegex(ValueError, "must omit Bullets"):
            pipeline.run(data)

    def test_validate_final_rejects_empty_out_of_scope_fields(self):
        data = valid_us_data()
        data["scope"] = "title_highlights"
        data["bullets"] = []
        data["description"] = ""
        self.assertIn("SCOPE_FIELD", failure_codes(data))

    def test_title_highlights_rejects_out_of_scope_fact_mapping(self):
        data = self.title_highlights_input()
        data["facts"][0]["listing_matches"] = {"Bullet 1": "Kavora"}
        with self.assertRaisesRegex(ValueError, "outside scope"):
            pipeline.run(data)

    def test_title_highlights_requires_locked_keyword_placement(self):
        data = self.title_highlights_input()
        data["priority_keywords"].append({
            "term": "craft supplies",
            "eligible_locations": ["Product Highlights"],
        })
        result = pipeline.run(data)
        self.assertFalse(result["hard_pass"])
        self.assertIn(
            "KEYWORD_PRIORITY",
            {item["code"] for item in result["validation"] if item["level"] == "FAIL"},
        )

    def test_title_highlights_limits_locked_keywords(self):
        data = self.title_highlights_input()
        data["priority_keywords"].extend([
            {"term": "chenille stems", "eligible_locations": ["Product Highlights"]},
            {"term": "flower-making kit", "eligible_locations": ["Product Highlights"]},
        ])
        with self.assertRaisesRegex(ValueError, "at most two"):
            pipeline.run(data)

    def test_title_highlights_keyword_bearing_highlights_can_use_hard_limit(self):
        data = self.title_highlights_input()
        data["priority_keywords"].append({
            "term": "flower-making kit",
            "eligible_locations": ["Item Highlights"],
        })
        highlights = data["draft"]["highlights"]
        data["draft"]["highlights"] = highlights + ", " + (
            "x" * (125 - len(highlights) - 2)
        )
        result = pipeline.run(data)
        self.assertTrue(result["hard_pass"])
        self.assertEqual(result["measurements"]["Product Highlights"], 125)

    def test_title_highlights_pending_wording_still_blocks_output(self):
        data = self.title_highlights_input()
        data["pending_facts"] = [{
            "claim": "Premium grade",
            "claim_zh": "高级等级",
            "confirmation_needed": "确认等级依据",
            "blocked_renderings": ["premium"],
        }]
        data["draft"]["highlights"] += ", premium"
        result = pipeline.run(data)
        self.assertFalse(result["hard_pass"])
        self.assertIn(
            "PENDING_LEAK",
            {item["code"] for item in result["validation"] if item["level"] == "FAIL"},
        )

    def test_title_highlights_omits_keyword_candidates(self):
        data = self.title_highlights_input()
        data["keyword_candidates"] = ["pipe cleaners"]
        with self.assertRaisesRegex(ValueError, "must omit keyword_candidates"):
            pipeline.run(data)

    def test_compact_pipeline_runs_every_stage_from_one_payload(self):
        result = pipeline.run(self.compact_input())
        self.assertTrue(result["hard_pass"])
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["keyword_benchmark"][0]["counts"], [1, 1])
        self.assertIn("RivalBrand", result["final_payload"]["competitor_brands"])
        self.assertIn("Title", result["claim_review"]["a_claims"][0]["locations"])
        self.assertEqual(
            result["claim_review"]["a_claims"][0]["status"],
            "temporary_use",
        )
        self.assertIn("目标资料支持 8 项", pipeline.render_optimization_summary(result))
        self.assertIn("目标资料支持，建议确认", result["confirmation_markdown"])

    def test_compact_rejects_unsupported_schema_versions(self):
        for version in (3, -1, 1.5, "3", {"bad": 1}):
            with self.subTest(version=version):
                data = self.compact_input()
                data["schema_version"] = version
                with self.assertRaisesRegex(ValueError, "schema_version must be"):
                    pipeline.run(data)

    def test_compact_rejects_nonstring_draft_fields(self):
        data = self.compact_input()
        data["draft"]["title"] = [data["draft"]["title"]]
        with self.assertRaisesRegex(ValueError, "draft.title must be a string"):
            pipeline.run(data)

    def test_compact_rejects_null_bullets_without_typeerror(self):
        data = self.compact_input()
        data["draft"]["bullets"] = None
        with self.assertRaisesRegex(ValueError, "draft.bullets must be a list"):
            pipeline.run(data)

    def test_risk_candidate_explicit_high_risk_flag_is_held(self):
        data = self.compact_input()
        data["risk_candidates"] = [{
            "claim": "Neutral test-dependent assertion",
            "claim_zh": "中性测试声明",
            "high_risk": True,
        }]
        result = pipeline.run(data)
        self.assertTrue(result["hard_pass"])
        self.assertEqual(result["claim_review"]["b_claims"][0]["status"], "hold")

    def test_risk_candidate_support_count_type_is_preflighted(self):
        data = self.compact_input()
        data["risk_candidates"] = [{
            "claim": "Neutral assertion",
            "claim_zh": "中性声明",
            "support_count": {"bad": 1},
        }]
        with self.assertRaisesRegex(ValueError, "support_count must be"):
            pipeline.run(data)

    def test_target_evidence_does_not_auto_confirm_high_risk_fact(self):
        data = self.compact_input()
        wording = "filters 99.97% of particles"
        data["draft"]["highlights"] += ", " + wording
        data["draft"]["translations"]["highlights"] += "，过滤99.97%的颗粒"
        data["facts"].append({
            "field": "filtration_efficiency",
            "claim": wording,
            "claim_zh": "过滤99.97%的颗粒",
            "listing_matches": {"Product Highlights": wording},
            "translation_matches": {"Product Highlights": "过滤99.97%的颗粒"},
        })
        result = pipeline.run(data)
        self.assertFalse(result["hard_pass"])
        self.assertFalse(result["clarification_required"])
        self.assertEqual(
            [item["code"] for item in result["validation"]], ["RISK_MAPPING"]
        )
        self.assertIn("held Group B fact", result["validation"][0]["message"])
        self.assertNotIn("## Final Listing", pipeline.render_listing_markdown(result))
        self.assertEqual(result["final_payload"]["claim_audit"], [])

        with tempfile.TemporaryDirectory() as directory:
            payload = Path(directory) / "held-risk.json"
            payload.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            process = subprocess.run(
                [sys.executable, "-B", str(HERE / "run_listing_pipeline.py"),
                 "--file", str(payload), "--format", "json"],
                capture_output=True, text=True, encoding="utf-8",
            )
            self.assertEqual(process.returncode, 1)
            cli_result = json.loads(process.stdout)
            self.assertFalse(cli_result["hard_pass"])
            self.assertEqual(cli_result["validation"][0]["code"], "RISK_MAPPING")

    def test_target_exact_material_grade_requires_confirmation(self):
        data = self.compact_input()
        wording = "304 stainless steel body, side handle"
        translated = "304不锈钢杯体，侧面把手"
        data["draft"]["highlights"] = wording
        data["draft"]["translations"]["highlights"] = translated
        data["facts"][1].update(
            claim=wording,
            claim_zh=translated,
            target=wording,
            listing_matches={"Product Highlights": wording},
            translation_matches={"Product Highlights": translated},
        )
        result = pipeline.run(data)
        self.assertFalse(result["hard_pass"])
        self.assertEqual(
            [item["code"] for item in result["validation"]], ["RISK_MAPPING"]
        )
        self.assertNotIn("## Final Listing", pipeline.render_listing_markdown(result))

        data["facts"][1]["seller_confirmed"] = True
        result = pipeline.run(data)
        self.assertTrue(result["hard_pass"])
        self.assertEqual(result["claim_review"]["b_claims"][0]["status"], "confirmed_use")

    def test_explicitly_confirmed_high_risk_fact_passes_same_field_gate(self):
        data = self.compact_input()
        wording = "filters 99.97% of particles"
        data["draft"]["highlights"] += ", " + wording
        data["draft"]["translations"]["highlights"] += "，过滤99.97%的颗粒"
        data["facts"].append({
            "field": "filtration_efficiency",
            "claim": wording,
            "claim_zh": "过滤99.97%的颗粒",
            "seller_confirmed": True,
            "listing_matches": {"Product Highlights": wording},
            "translation_matches": {"Product Highlights": "过滤99.97%的颗粒"},
        })
        result = pipeline.run(data)
        self.assertTrue(result["hard_pass"])
        self.assertEqual(result["claim_review"]["b_claims"][0]["status"], "confirmed_use")

    def test_compact_requires_explicit_package_components(self):
        data = self.compact_input()
        data.pop("package_components")
        with self.assertRaisesRegex(ValueError, "requires explicit package_components"):
            pipeline.run(data)

    def test_regenerated_seller_modification_updates_both_languages_before_reordering(self):
        data = self.title_highlights_input()
        original = pipeline.run(data)
        self.assertTrue(original["hard_pass"])
        reply_id = next(item["id"] for item in original["claim_review"]["a_claims"] if item["claim"] == "Pink")
        original_claim = next(item["claim"] for item in original["claim_review"]["a_claims"] if item["id"] == reply_id)
        fact = next(item for item in data["facts"] if item["claim"] == original_claim)
        fact.update(
            claim="Blue", claim_zh="蓝色", seller_confirmed=True,
            listing_matches={"Item Highlights": "Blue"},
            translation_matches={"Item Highlights": "蓝色"},
        )
        data["draft"]["highlights"] = data["draft"]["highlights"].replace("Pink", "Blue")
        data["draft"]["translations"]["highlights"] = data["draft"]["translations"]["highlights"].replace("粉色", "蓝色")
        data["facts"].reverse()
        result = pipeline.run(data)
        self.assertTrue(result["hard_pass"])
        confirmed = [item for item in result["claim_review"]["a_claims"] if item["seller_confirmed"]]
        self.assertEqual([(item["claim"], item["claim_zh"]) for item in confirmed], [("Blue", "蓝色")])
        self.assertNotIn("粉色", result["confirmation_markdown"])

    def test_regenerated_seller_deletion_removes_fact_and_bilingual_copy(self):
        data = self.title_highlights_input()
        original = pipeline.run(data)
        reply_id = next(item["id"] for item in original["claim_review"]["a_claims"] if item["claim"] == "Pink")
        original_claim = next(item["claim"] for item in original["claim_review"]["a_claims"] if item["id"] == reply_id)
        data["facts"] = [item for item in data["facts"] if item["claim"] != original_claim]
        data["draft"]["highlights"] = "Chenille stems, flower-making kit"
        data["draft"]["translations"]["highlights"] = "雪尼尔棒，花朵制作套装"
        result = pipeline.run(data)
        self.assertTrue(result["hard_pass"])
        self.assertNotIn("Pink", pipeline.render_listing_markdown(result))
        self.assertNotIn("粉色", pipeline.render_listing_markdown(result))

    def test_regenerated_single_b_confirmation_stays_with_fact_after_reordering(self):
        data = self.title_highlights_input()
        data["facts"] = [data["facts"][0], {
            "field": "product_type", "claim": "Air Filter", "claim_zh": "空气滤芯",
            "listing_matches": {"Title": "Air Filter"}, "translation_matches": {"Title": "空气滤芯"},
        }, {
            "field": "frame_material", "claim": "Plastic frame", "claim_zh": "塑料框架",
            "listing_matches": {"Item Highlights": "Plastic frame"},
            "translation_matches": {"Item Highlights": "塑料框架"},
        }]
        data["draft"] = {
            "title": "Kavora Air Filter", "highlights": "Plastic frame",
            "translations": {"title": "Kavora 空气滤芯", "highlights": "塑料框架"},
        }
        data["priority_keywords"] = []
        data["risk_candidates"] = [
            {"claim": "HEPA", "claim_zh": "HEPA过滤"}, {"claim": "H13", "claim_zh": "H13等级"},
        ]
        original = pipeline.run(data)
        self.assertTrue(original["hard_pass"])
        original_claim = next(item["claim"] for item in original["claim_review"]["b_claims"] if item["id"] == "B1")
        fact = next(item for item in data["risk_candidates"] if item["claim"] == original_claim)
        fact.update(
            seller_confirmed=True, listing_matches={"Item Highlights": "HEPA"},
            translation_matches={"Item Highlights": "HEPA过滤"},
        )
        data["draft"]["highlights"] += ", HEPA filtration"
        data["draft"]["translations"]["highlights"] += "，HEPA过滤"
        data["risk_candidates"].reverse()
        result = pipeline.run(data)
        self.assertTrue(result["hard_pass"])
        claims = {item["claim"]: item for item in result["claim_review"]["b_claims"]}
        self.assertEqual(claims["HEPA"]["status"], "confirmed_use")
        self.assertEqual(claims["H13"]["status"], "hold")
        self.assertNotIn("H13", result["final_payload"]["highlights"])

    def test_changed_translation_must_update_exact_fact_mapping(self):
        data = self.compact_input()
        data["draft"]["translations"]["highlights"] = "不锈钢杯体，侧面把手"
        result = pipeline.run(data)
        self.assertFalse(result["hard_pass"])
        self.assertIn(
            "CLAIM_TRANSLATION_LOCATION",
            {item["code"] for item in result["validation"] if item["level"] == "FAIL"},
        )

    def test_pipeline_reads_utf8_bom_file(self):
        data = self.compact_input()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.json"
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8-sig")
            loaded = pipeline.read_input(str(path))
        self.assertEqual(loaded, data)
        self.assertTrue(pipeline.run(loaded)["hard_pass"])

    def test_pipeline_reads_utf8_bom_stdin(self):
        data = self.compact_input()
        raw = "\ufeff" + json.dumps(data, ensure_ascii=False)
        original_stdin = sys.stdin
        try:
            sys.stdin = io.StringIO(raw)
            loaded = pipeline.read_input(None)
        finally:
            sys.stdin = original_stdin
        self.assertEqual(loaded, data)

    def test_compact_target_fact_uses_concise_defaults(self):
        data = self.compact_input()
        fact = data["facts"][0]
        fact.pop("claim_type")
        fact.pop("strategy")
        fact.pop("target")
        fact.pop("competitors")
        result = pipeline.run(data)
        self.assertTrue(result["hard_pass"])
        self.assertEqual(result["evidence"]["confirmed"][0]["value"], fact["claim"])

    def test_compact_target_fact_accepts_empty_competitor_default(self):
        data = self.compact_input()
        fact = data["facts"][0]
        fact.pop("target")
        fact["competitors"] = []
        result = pipeline.run(data)
        self.assertTrue(result["hard_pass"])
        self.assertEqual(result["evidence"]["confirmed"][0]["value"], fact["claim"])

    def test_compact_fact_requires_chinese_meaning(self):
        data = self.compact_input()
        data["facts"][0].pop("claim_zh")
        with self.assertRaisesRegex(ValueError, "claim_zh must not be blank"):
            pipeline.run(data)

    def test_risk_candidate_requires_chinese_meaning(self):
        data = self.compact_input()
        data["risk_candidates"] = [{
            "claim": "Food-grade copolyester",
            "claim_type": "food_contact",
            "high_risk": True,
        }]
        with self.assertRaisesRegex(ValueError, "risk candidate requires claim_zh"):
            pipeline.run(data)

    def test_pending_fact_requires_chinese_meaning(self):
        data = self.compact_input()
        data["pending_facts"] = [{"claim": "Exact lid quantity"}]
        with self.assertRaisesRegex(ValueError, "claim_zh must not be blank"):
            pipeline.run(data)

    def test_pipeline_infers_roomy_highlights_keyword_substitute(self):
        data = self.compact_input()
        data["priority_keywords"] = [{
            "term": "Copolyester body, side handle",
            "eligible_locations": ["Product Highlights"],
        }]
        result = pipeline.run(data)
        self.assertTrue(result["hard_pass"])
        self.assertNotIn(
            "allow_highlights_substitute",
            result["final_payload"]["keywords"][-1],
        )

    def test_product_highlights_aliases_are_normalized(self):
        data = self.compact_input()
        draft = data["draft"]
        draft["product_highlights"] = draft.pop("highlights")
        translations = draft["translations"]
        translations["product_highlights"] = translations.pop("highlights")
        result = pipeline.run(data)
        self.assertTrue(result["hard_pass"])
        self.assertEqual(
            result["measurements"]["Product Highlights"],
            len("Copolyester body, side handle"),
        )
        self.assertIn("highlights", result["final_payload"])
        self.assertNotIn("product_highlights", result["final_payload"])
        self.assertIn("highlights", result["final_payload"]["translations"])

    def test_conflicting_product_highlights_alias_is_rejected(self):
        data = self.compact_input()
        data["draft"]["product_highlights"] = "Different highlights"
        with self.assertRaisesRegex(ValueError, "highlights conflicts"):
            pipeline.run(data)

    def test_min_count_rejects_component_lists_with_readable_error(self):
        data = self.compact_input()
        data["facts"][0].update(strategy="min_count", target=["filter basket"])
        with self.assertRaisesRegex(
            ValueError, "min_count requires numeric scalars.*set_intersection"
        ):
            pipeline.run(data)

    def test_compact_rejects_string_boolean_flags(self):
        for key in (
            "high_risk", "seller_confirmed", "core_identity", "target_conflict"
        ):
            with self.subTest(key=key):
                data = self.compact_input()
                data["facts"][0][key] = "false"
                with self.assertRaisesRegex(
                    ValueError, f"{key} must be a JSON boolean"
                ):
                    pipeline.run(data)

    def test_min_count_rejects_invalid_numeric_values(self):
        invalid = ("NaN", float("nan"), float("inf"), -1, 1.5, True)
        for value in invalid:
            with self.subTest(value=value):
                data = self.compact_input()
                data["facts"][0].update(strategy="min_count", target=value)
                with self.assertRaisesRegex(
                    ValueError, "finite non-negative integers"
                ):
                    pipeline.run(data)

    def test_min_count_accepts_nonnegative_integer(self):
        data = self.compact_input()
        data["facts"][0].update(strategy="min_count", target=2)
        result = pipeline.run(data)
        self.assertTrue(result["hard_pass"])
        self.assertEqual(result["evidence"]["confirmed"][0]["value"], 2)

    def test_compact_rejects_malformed_security_lists(self):
        for field, value in (
            ("blocked_terms", "Kavora"),
            ("competitor_brands", "RivalBrand"),
        ):
            with self.subTest(field=field):
                data = self.compact_input()
                data["draft"][field] = value
                with self.assertRaisesRegex(ValueError, f"{field} must be a list"):
                    pipeline.run(data)

    def test_compact_rejects_malformed_value_renderings(self):
        data = self.compact_input()
        data["facts"][0]["value_renderings"] = "wood"
        with self.assertRaisesRegex(ValueError, "value_renderings must be an object"):
            pipeline.run(data)

    def test_set_intersection_accepts_component_lists(self):
        data = self.compact_input()
        data["facts"][0].update(
            strategy="set_intersection",
            target=[data["facts"][0]["claim"]],
            competitors=[[data["facts"][0]["claim"]]],
        )
        result = pipeline.run(data)
        self.assertTrue(result["hard_pass"])

    def test_zero_keyword_benchmark_is_informational(self):
        data = self.compact_input()
        data["competitor_bullets"] = [["Side handle"], ["Pitcher and lid"]]
        data["keyword_candidates"] = ["64 oz"]
        result = pipeline.run(data)
        self.assertTrue(result["hard_pass"])
        self.assertEqual(result["keyword_benchmark"][0]["suggested"], "0")
        self.assertEqual(result["final_payload"]["keywords"], [])
        self.assertIn("信息性 benchmark", result["final_payload"]["keyword_audit_reason"])

    def test_priority_inherits_declared_variant_without_a_nonzero_benchmark(self):
        for competitors in ([], [["Holds 64 oz"]], [["Side handles support pouring"]]):
            for declared in (False, True):
                with self.subTest(competitors=competitors, declared=declared):
                    data = self.compact_input()
                    data["competitor_bullets"] = competitors
                    data["keyword_candidates"] = [{
                        "term": "side handles", "variants": ["side handle"] if declared else [],
                    }]
                    data["priority_keywords"] = ["side handles"]
                    result = pipeline.run(data)
                    keyword = result["final_payload"]["keywords"][0]
                    self.assertEqual(result["hard_pass"], declared)
                    self.assertEqual(keyword["reported_used"], int(declared))
                    self.assertNotIn("target", keyword)
                    if declared:
                        self.assertIn("side handles：Bullet 2、Item Highlights，五点共1次", pipeline.render_listing_markdown(result))
                    else:
                        self.assertIn("KEYWORD_PRIORITY", {
                            row["code"] for row in result["validation"] if row["level"] == "FAIL"
                        })

    def test_priority_inherits_candidate_placement_even_without_a_benchmark(self):
        for competitors in ([], [["Holds 64 oz"]]):
            for location, passes in (("Bullet 2", True), ("Bullet 3", False)):
                with self.subTest(competitors=competitors, location=location):
                    data = self.compact_input()
                    data["competitor_bullets"] = competitors
                    data["keyword_candidates"] = [{
                        "term": "side handles", "variants": ["side handle"],
                        "eligible_locations": [location],
                    }]
                    data["priority_keywords"] = ["SIDE HANDLES"]
                    result = pipeline.run(data)
                    self.assertEqual(result["hard_pass"], passes)
                    self.assertEqual(result["final_payload"]["keywords"][0]["eligible_locations"], [location])

    def test_priority_does_not_inherit_variants_from_a_different_keyword(self):
        data = self.compact_input()
        data["competitor_bullets"] = []
        data["keyword_candidates"] = [{"term": "side handles", "variants": ["side handle"]}]
        data["priority_keywords"] = ["pouring handles"]
        result = pipeline.run(data)
        self.assertFalse(result["hard_pass"])
        self.assertEqual(result["final_payload"]["keywords"][0]["variants"], [])
        self.assertEqual(result["final_payload"]["keywords"][0]["reported_used"], 0)

    def test_listing_markdown_renders_final_copy_and_translations(self):
        markdown = pipeline.render_listing_markdown(pipeline.run(self.compact_input()))
        self.assertIn("## ⚠️ 发布前确认区 Seller Confirmation", markdown)
        self.assertIn("### 可复制确认回复", markdown)
        self.assertIn("## Final Listing", markdown)
        self.assertIn("### Item Highlights", markdown)
        self.assertIn("Kavora 64 oz Blender Pitcher", markdown)
        self.assertIn("Kavora 64 oz 搅拌杯", markdown)
        translation_heading = markdown.index("### 五点中文翻译")
        self.assertLess(markdown.index("CHECK CAPACITY: Holds 64 oz"), translation_heading)
        self.assertGreater(markdown.find("检查容量：容量 64 oz", translation_heading), translation_heading)
        self.assertIn("**中文：**", markdown)
        self.assertIn("共聚酯杯体", markdown)

    def test_priority_keyword_is_validated_and_rendered_separately(self):
        data = self.compact_input()
        data["priority_keywords"] = [{
            "term": "pitcher",
            "eligible_locations": ["Bullet 4", "Bullet 5"],
        }]
        result = pipeline.run(data)
        self.assertTrue(result["hard_pass"])
        priority = [
            item for item in result["final_payload"]["keywords"]
            if item.get("source") == "user_priority"
        ]
        self.assertEqual(priority[0]["reported_used"], 2)
        markdown = pipeline.render_markdown(result)
        self.assertIn("### 用户指定关键词", markdown)
        self.assertIn("pitcher：Bullet 4、Bullet 5，五点共2次", markdown)

    def test_pipeline_counts_declared_priority_variant(self):
        data = self.compact_input()
        data["priority_keywords"] = [{
            "term": "pitchers",
            "variants": ["pitcher"],
            "eligible_locations": ["Bullet 4", "Bullet 5"],
        }]
        result = pipeline.run(data)
        self.assertTrue(result["hard_pass"])
        priority = [
            item for item in result["final_payload"]["keywords"]
            if item.get("source") == "user_priority"
        ][0]
        self.assertEqual(priority["reported_used"], 2)

    def test_pipeline_combines_priority_variant_with_one_below_competitor_warning(self):
        data = self.compact_input()
        data["competitor_bullets"] = [
            ["64 oz capacity, 64 oz pitcher"],
            ["Holds 64 oz; designed around 64 oz capacity"],
            ["64 oz jar with a clearly marked 64 oz capacity"],
        ]
        data["priority_keywords"] = [{
            "term": "pitchers",
            "variants": ["pitcher"],
            "eligible_locations": ["Bullet 4", "Bullet 5"],
        }]
        result = pipeline.run(data)
        self.assertTrue(result["hard_pass"])
        warnings = {
            item["code"] for item in result["validation"] if item["level"] == "WARN"
        }
        self.assertIn("KEYWORD_BELOW_PREFERRED", warnings)
        priority = [
            item for item in result["final_payload"]["keywords"]
            if item.get("source") == "user_priority"
        ][0]
        self.assertEqual(priority["reported_used"], 2)

    def test_pending_fact_blocked_rendering_cannot_leak(self):
        data = self.compact_input()
        data["pending_facts"] = [{
            "claim": "Exact lid wording",
            "claim_zh": "杯盖准确名称",
            "confirmation_needed": "确认杯盖名称",
            "blocked_renderings": ["lid"],
        }]
        result = pipeline.run(data)
        self.assertFalse(result["hard_pass"])
        self.assertIn(
            "PENDING_LEAK",
            {item["code"] for item in result["validation"] if item["level"] == "FAIL"},
        )

    def test_pending_fact_blocked_chinese_rendering_cannot_leak(self):
        data = self.compact_input()
        data["pending_facts"] = [{
            "claim": "Exact wood species", "claim_zh": "准确木种",
            "confirmation_needed": "确认木种",
            "blocked_renderings": ["红酸枝木"],
        }]
        data["draft"]["translations"]["title"] += " 红酸枝木"
        result = pipeline.run(data)
        self.assertFalse(result["hard_pass"])
        self.assertIn(
            "PENDING_LEAK",
            {item["code"] for item in result["validation"] if item["level"] == "FAIL"},
        )

    def test_competitor_brand_variants_are_locked(self):
        data = self.compact_input()
        data["exclusions"][0]["variants"] = ["Rival Brand"]
        data["draft"]["translations"]["title"] += " Rival Brand"
        result = pipeline.run(data)
        self.assertFalse(result["hard_pass"])
        self.assertIn("Rival Brand", result["final_payload"]["competitor_brands"])
        self.assertIn(
            "COMPETITOR_BRAND",
            {item["code"] for item in result["validation"] if item["level"] == "FAIL"},
        )

    def test_confirmation_renders_chinese_meanings_and_pending_facts(self):
        data = self.compact_input()
        data["risk_candidates"] = [{
            "claim": "Food-grade copolyester",
            "claim_zh": "食品接触级共聚酯",
            "claim_type": "food_contact",
            "high_risk": True,
            "seller_confirmed": False,
        }]
        data["pending_facts"] = [{
            "claim": "Exact lid quantity",
            "claim_zh": "杯盖准确数量",
            "confirmation_needed": "确认包装中的杯盖数量",
        }]
        markdown = pipeline.render_markdown(pipeline.run(data))
        self.assertIn("| 中文释义 |", markdown)
        self.assertIn("食品接触级共聚酯", markdown)
        self.assertIn("| P1 | Exact lid quantity | 杯盖准确数量 |", markdown)

    def test_pending_facts_are_limited_and_not_group_b(self):
        data = self.compact_input()
        data["pending_facts"] = [
            {"claim": f"Question {index}", "claim_zh": f"问题{index}"}
            for index in range(1, 4)
        ]
        result = pipeline.run(data)
        self.assertEqual(len(result["claim_review"]["pending_facts"]), 3)
        self.assertEqual(result["claim_review"]["b_claims"], [])
        data["pending_facts"].append({"claim": "Question 4", "claim_zh": "问题4"})
        with self.assertRaisesRegex(ValueError, "at most 3"):
            pipeline.run(data)

    def test_listing_markdown_omits_unvalidated_final_copy(self):
        data = self.compact_input()
        data["draft"]["title"] = ""
        markdown = pipeline.render_listing_markdown(pipeline.run(data))
        self.assertIn("FAIL", markdown)
        self.assertNotIn("## Final Listing", markdown)

    def test_repair_report_groups_stale_matches_and_preserves_full_audit(self):
        data = self.compact_input()
        data["draft"]["bullets"][4] = "CARE: Rinse after use"
        data["draft"]["translations"]["bullets"][4] = "保养：使用后冲洗"
        result = pipeline.run(data)
        before = copy.deepcopy(result)
        markdown = pipeline.render_listing_markdown(result)
        self.assertFalse(result["hard_pass"])
        self.assertEqual(result, before)
        self.assertIn("### Bullet 5", markdown)
        self.assertIn("CLAIM_LOCATION / CLAIM_COVERAGE", markdown)
        self.assertIn("'Hand wash the pitcher'", markdown)
        self.assertIn("'保养：手洗搅拌杯'", markdown)
        self.assertIn("A7 的源文、中文及匹配片段", markdown)
        self.assertIn("包装映射 pitcher", markdown)
        first_bullet_section = markdown.split("### Bullet 1", 1)[1].split("### Bullet 2", 1)[0]
        self.assertNotIn("同步检查", first_bullet_section)
        self.assertNotIn("has no verified match in the field", markdown)
        full = pipeline.render_markdown(result)
        self.assertIn("has no verified match in the field", full)
        self.assertIn("Seller Confirmation", full)
        self.assertNotIn("Seller Confirmation", markdown)
        self.assertNotIn("## Final Listing", markdown)
        self.assertLess(len(markdown), len(full))

    def test_repair_report_keeps_translation_only_and_other_field_failures(self):
        data = self.compact_input()
        data["draft"]["translations"]["bullets"][4] = "保养：使用后冲洗"
        data["draft"]["title"] = "Replacement pitcher"
        result = pipeline.run(data)
        markdown = pipeline.render_listing_markdown(result)
        self.assertIn("### Title", markdown)
        self.assertIn("### Bullet 5", markdown)
        self.assertIn("CLAIM_TRANSLATION_LOCATION", markdown)
        self.assertIn("CLAIM_TRANSLATION_COVERAGE", markdown)
        self.assertIn("A7: source and translation claim locations differ", markdown)
        self.assertIn("'保养：手洗搅拌杯'", markdown)

    def test_repair_report_does_not_merge_unrelated_coverage_failure(self):
        data = self.compact_input()
        data["draft"]["title"] = "Replacement pitcher"
        result = pipeline.run(data)
        result["validation"].append({
            "level": "FAIL", "code": "CLAIM_COVERAGE",
            "message": "Title references unknown claim ID A99",
            "location": "Title", "claim_id": "A99",
        })
        markdown = pipeline.render_listing_markdown(result)
        self.assertIn("Title references unknown claim ID A99", markdown)

    def test_repair_report_handles_invalid_package_records_without_crashing(self):
        data = self.compact_input()
        data["draft"]["title"] = "Changed title"
        data["package_components"] = ["lid", {"listing_matches": {"Title": "Changed"}}]
        result = pipeline.run(data)
        markdown = pipeline.render_listing_markdown(result)
        self.assertFalse(result["hard_pass"])
        self.assertIn("PACKAGE_SCHEMA", markdown)
        self.assertNotIn("## Final Listing", markdown)

    def test_repair_report_handles_invalid_legacy_package_collection(self):
        data = self.pipeline_input()
        data["final"]["title"] = "Changed title"
        data["final"]["package_components"] = None
        result = pipeline.run(data)
        markdown = pipeline.render_listing_markdown(result)
        self.assertFalse(result["hard_pass"])
        self.assertIn("PACKAGE_SCHEMA", markdown)

    def test_repair_report_shows_highlights_limit_and_mapping_dependencies(self):
        data = self.compact_input()
        original = data["draft"]["highlights"]
        data["draft"]["highlights"] += "x" * (128 - len(original))
        result = pipeline.run(data)
        markdown = pipeline.render_listing_markdown(result)
        self.assertIn("### Item Highlights", markdown)
        self.assertIn("128/125", markdown)
        self.assertIn("A2 的源文、中文及匹配片段", markdown)
        self.assertNotIn("## Final Listing", markdown)

    def test_german_full_listing_repair_and_success_preserve_language(self):
        data = self.pipeline_input("DE")
        original = copy.deepcopy(data)
        good = pipeline.run(data)
        self.assertTrue(good["hard_pass"])
        self.assertIn(original["final"]["title"], pipeline.render_listing_markdown(good))
        data["final"]["bullets"][4] = "Pflege: Nach Gebrauch abspülen"
        data["final"]["translations"]["bullets"][4] = "保养：使用后冲洗"
        result = pipeline.run(data)
        markdown = pipeline.render_listing_markdown(result)
        old_source = original["claim_review"]["claims"][6]["match_text"]
        self.assertFalse(result["hard_pass"])
        self.assertIn("### Bullet 5", markdown)
        self.assertIn(old_source, markdown)
        self.assertIn("CLAIM_TRANSLATION_LOCATION", markdown)
        self.assertNotIn("## Final Listing", markdown)

    def test_title_highlights_repair_excludes_full_listing_dependencies(self):
        for marketplace in ("US", "DE"):
            with self.subTest(marketplace=marketplace):
                data = self.title_highlights_input()
                data["marketplace"] = marketplace
                data["draft"]["title"] = "Kavora" if marketplace == "US" else "Kavora Bastelmaterial"
                markdown = pipeline.render_listing_markdown(pipeline.run(data))
                self.assertIn("### Title", markdown)
                self.assertNotIn("Bullet", markdown)
                self.assertNotIn("Description", markdown)
                self.assertNotIn("## Final Listing", markdown)

    def test_repair_report_preserves_insufficient_evidence_reason(self):
        data = self.title_highlights_input()
        data["facts"] = [data["facts"][0], data["facts"][3]]
        result = pipeline.run(data)
        self.assertTrue(result["clarification_required"])
        markdown = pipeline.render_listing_markdown(result)
        self.assertIn("可用信息 2/3 项", markdown)
        self.assertNotIn("## Final Listing", markdown)

    def test_repair_report_preserves_core_conflict_reason(self):
        data = self.pipeline_input()
        data["evidence"] = {"mode": "target_anchored", "attributes": [{
            "field": "product_type", "target": "Pitcher", "strategy": "exact",
            "core_identity": True, "target_conflict": True,
        }]}
        result = pipeline.run(data)
        self.assertTrue(result["clarification_required"])
        markdown = pipeline.render_listing_markdown(result)
        self.assertIn("product_type: target attribute conflicts with itself", markdown)
        self.assertNotIn("## Final Listing", markdown)

    def test_repair_report_preserves_risk_and_pending_blockers(self):
        for marketplace, claim in (("US", "food-grade"), ("DE", "lebensmittelecht")):
            with self.subTest(marketplace=marketplace):
                data = self.pipeline_input(marketplace)
                data["final"]["highlights"] += ", " + claim
                data["final"]["blocked_terms"] = [{"term": "待核实木种", "match": "phrase"}]
                data["final"]["translations"]["title"] += " 待核实木种"
                result = pipeline.run(data)
                markdown = pipeline.render_listing_markdown(result)
                self.assertFalse(result["hard_pass"])
                self.assertIn("RISK_CLAIM_CONFIRMATION", markdown)
                self.assertIn("BLOCKED_TERM", markdown)
                self.assertNotIn("## Final Listing", markdown)

    def test_repair_report_preserves_required_keyword_location(self):
        data = self.compact_input()
        data["priority_keywords"] = [{
            "term": "side handle", "eligible_locations": ["Item Highlights"],
            "required_locations": ["Title"],
        }]
        result = pipeline.run(data)
        markdown = pipeline.render_listing_markdown(result)
        self.assertFalse(result["hard_pass"])
        self.assertIn("### Title", markdown)
        self.assertIn("'side handle' is missing from required Title", markdown)

    def test_candidate_required_locations_survive_conversion_and_zero_benchmark(self):
        for competitors in ([["Side handle"], ["Side handle"]], [["Holds 64 oz"]]):
            with self.subTest(competitors=competitors):
                data = self.compact_input()
                data["competitor_bullets"] = competitors
                data["keyword_candidates"] = [{"term": "side handle", "required_locations": ["Title"]}]
                result = pipeline.run(data)
                self.assertIn("KEYWORD_REQUIRED_LOCATION", {
                    item["code"] for item in result["validation"] if item["level"] == "FAIL"
                })
                self.assertEqual(result["final_payload"]["keywords"][0]["required_locations"], ["Title"])
                if competitors == [["Holds 64 oz"]]:
                    self.assertNotIn("target", result["final_payload"]["keywords"][0])

    def test_required_locations_survive_priority_keyword_replacement(self):
        for custom_keywords in (False, True):
            with self.subTest(custom_keywords=custom_keywords):
                data = self.compact_input()
                data["competitor_bullets"] = [["Side handle"]]
                data["keyword_candidates"] = [{"term": "side handle", "required_locations": ["Title"]}]
                if custom_keywords:
                    data["draft"]["keywords"] = [{
                        "phrase": "side handle", "source": "competitor", "required_locations": ["Description"],
                    }]
                data["priority_keywords"] = [{"term": "side handle", "required_locations": ["Bullet 2"]}]
                result = pipeline.run(data)
                required = result["final_payload"]["keywords"][0]["required_locations"]
                self.assertIn("Title", required)
                self.assertIn("Bullet 2", required)
                if custom_keywords:
                    self.assertIn("Description", required)
                self.assertFalse(result["hard_pass"])

    def test_required_description_uses_actual_description_without_relaxing_priority_gate(self):
        data = self.compact_input()
        data["priority_keywords"] = [{"term": "Copolyester body", "required_locations": ["Description"]}]
        self.assertTrue(pipeline.run(data)["hard_pass"])
        data["draft"]["description"] = data["draft"]["description"].replace("Copolyester body", "Body")
        codes = {item["code"] for item in pipeline.run(data)["validation"] if item["level"] == "FAIL"}
        self.assertIn("KEYWORD_REQUIRED_LOCATION", codes)
        data = self.compact_input()
        data["priority_keywords"] = [{"term": "Features", "required_locations": ["Description"]}]
        codes = {item["code"] for item in pipeline.run(data)["validation"] if item["level"] == "FAIL"}
        self.assertIn("KEYWORD_PRIORITY", codes)
        self.assertNotIn("KEYWORD_REQUIRED_LOCATION", codes)

    def test_required_numeric_identifier_allows_correct_bilingual_delivery(self):
        for marketplace in ("US", "DE"):
            product, material, drive = (
                ("Screws", "Steel", "Phillips drive") if marketplace == "US"
                else ("Schrauben", "Stahl", "Kreuzschlitz")
            )
            for term, passes in (("M2.5", True), ("m2.5", True), ("M2.6", False)):
                with self.subTest(marketplace=marketplace, term=term):
                    data = {
                        "schema_version": 2, "marketplace": marketplace,
                        "mode": "target_anchored", "scope": "title_highlights",
                        "package_components": [], "facts": [],
                        "draft": {
                            "title": f"M2.5 {product}", "highlights": f"{material}, {drive}",
                            "translations": {"title": "M2.5 螺丝", "highlights": "钢制，十字槽"},
                        },
                        "priority_keywords": [{
                            "term": term, "eligible_locations": ["Title"],
                            "required_locations": ["Title"],
                        }],
                    }
                    for field, source, chinese, location in (
                        ("type", product, "螺丝", "Title"), ("size", "M2.5", "M2.5", "Title"),
                        ("material", material, "钢制", "Item Highlights"),
                        ("drive", drive, "十字槽", "Item Highlights"),
                    ):
                        data["facts"].append({
                            "field": field, "claim": source, "claim_zh": chinese,
                            "listing_matches": {location: source},
                            "translation_matches": {location: chinese},
                        })
                    result = pipeline.run(data)
                    self.assertEqual(result["hard_pass"], passes)
                    proc = subprocess.run(
                        [sys.executable, "-B", str(HERE / "run_listing_pipeline.py"), "--format", "listing-markdown"],
                        input=json.dumps(data, ensure_ascii=False), text=True, encoding="utf-8", capture_output=True,
                    )
                    self.assertEqual(proc.returncode, 0 if passes else 1)
                    self.assertEqual("## Final Listing" in proc.stdout, passes)
                    if passes:
                        self.assertIn("**中文：** M2.5 螺丝", proc.stdout)
                    else:
                        self.assertIn("KEYWORD_REQUIRED_LOCATION", proc.stdout)

    def test_duplicate_keyword_specs_keep_requirements_and_accepted_forms(self):
        data = self.title_highlights_input()
        data["priority_keywords"] = [
            {"term": "pipe cleaner", "eligible_locations": ["Title"], "required_locations": ["Title"]},
            {"term": "Pipe Cleaner", "variants": ["pipe cleaners"], "required_locations": ["Item Highlights"]},
        ]
        result = pipeline.run(data)
        keyword = result["final_payload"]["keywords"][0]
        self.assertEqual(keyword["variants"], ["pipe cleaners"])
        self.assertEqual(keyword["required_locations"], ["Title", "Item Highlights"])
        failures = [item for item in result["validation"] if item["code"] == "KEYWORD_REQUIRED_LOCATION"]
        self.assertEqual([item["location"] for item in failures], ["Product Highlights"])

    def test_duplicate_keywords_preserve_single_eligible_plan_in_either_order(self):
        for location, passes in (("Bullet 2", True), ("Bullet 3", False)):
            for reverse in (False, True):
                with self.subTest(location=location, reverse=reverse):
                    data = self.compact_input()
                    specs = ["side handle", {
                        "term": " SIDE HANDLE ", "eligible_locations": [location],
                    }]
                    data["priority_keywords"] = list(reversed(specs)) if reverse else specs
                    result = pipeline.run(data)
                    self.assertEqual(result["hard_pass"], passes)
                    self.assertEqual(
                        result["final_payload"]["keywords"][-1]["eligible_locations"], [location]
                    )
                    self.assertEqual("## Final Listing" in pipeline.render_listing_markdown(result), passes)

    def test_duplicate_eligible_plans_intersect_normalized_field_aliases(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                data = self.title_highlights_input()
                specs = [
                    {"term": "pipe cleaners", "eligible_locations": ["Title", "Item Highlights"]},
                    {"term": "PIPE CLEANERS", "eligible_locations": [" title "]},
                ]
                data["priority_keywords"] = list(reversed(specs)) if reverse else specs
                result = pipeline.run(data)
                self.assertTrue(result["hard_pass"])
                self.assertEqual(result["final_payload"]["keywords"][0]["eligible_locations"], ["Title"])
        destination = {"eligible_locations": ["Item Highlights"]}
        pipeline.merge_keyword_constraints(destination, {"eligible_locations": [" product highlights "]})
        self.assertEqual(destination["eligible_locations"], ["Product Highlights"])

    def test_priority_replacement_keeps_candidate_eligible_plan(self):
        for priority_locations in (None, ["Bullet 2", "Bullet 3"]):
            with self.subTest(priority_locations=priority_locations):
                data = self.compact_input()
                data["competitor_bullets"] = [["Side handle"], ["Side handle"]]
                data["keyword_candidates"] = [{
                    "term": "side handle", "eligible_locations": ["Bullet 3"],
                }]
                priority = {"term": "side handle"}
                if priority_locations is not None:
                    priority["eligible_locations"] = priority_locations
                data["priority_keywords"] = [priority]
                result = pipeline.run(data)
                self.assertFalse(result["hard_pass"])
                self.assertEqual(result["final_payload"]["keywords"][0]["eligible_locations"], ["Bullet 3"])
                self.assertIn("KEYWORD_PLACEMENT", {
                    row["code"] for row in result["validation"] if row["level"] == "FAIL"
                })

    def test_disjoint_eligible_plans_fail_instead_of_becoming_unrestricted(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                data = self.compact_input()
                specs = [
                    {"term": "side handle", "eligible_locations": ["Bullet 2"]},
                    {"term": "SIDE HANDLE", "eligible_locations": ["Bullet 3"]},
                ]
                data["priority_keywords"] = list(reversed(specs)) if reverse else specs
                with self.assertRaisesRegex(ValueError, "conflicting eligible_locations"):
                    pipeline.run(data)

    def test_predraft_keyword_cli_matches_pipeline_on_original_bullets(self):
        for marketplace, term, bullets in (
            ("US", "Christmas garland", [
                ["Christmas garland with lights. Christmas garland for a door", "Christmas", "garland"],
                ["Full Christmas garland; pre-lit Christmas garland"],
                ["Christmas garland with pinecones"],
            ]),
            ("DE", "Siebträger", [
                ["Siebträger aus Edelstahl; Siebträger mit Holzgriff"],
                ["Siebträger für Espresso"], ["Siebträger mit Doppelauslauf"],
            ]),
        ):
            with self.subTest(marketplace=marketplace):
                request = {"mode": "bullet", "keywords": [term], "competitors": bullets, "json": True}
                proc = subprocess.run(
                    [sys.executable, "-X", "utf8", str(HERE / "kwfreq.py")],
                    input=json.dumps(request, ensure_ascii=False), text=True,
                    encoding="utf-8", capture_output=True, check=True,
                )
                planned = json.loads(proc.stdout)
                data = self.pipeline_input(marketplace)
                data["keyword_benchmark"] = {"keywords": [term], "competitors": bullets}
                result = pipeline.run(data)
                self.assertEqual(result["keyword_benchmark"], planned)
                self.assertEqual(planned[0]["counts"], [2, 2, 1] if marketplace == "US" else [2, 1, 1])
                self.assertEqual(
                    result["final_payload"]["competitors"],
                    ["\n".join(record) for record in bullets],
                )

    def test_standalone_candidate_keeps_eligible_plan_without_nonzero_benchmark(self):
        for competitors in ([], [["Pouring grip"], ["Pouring grip"]], [["Side handle"], ["Side handle"]]):
            for location, passes in (("Bullet 2", True), ("Bullet 3", False)):
                with self.subTest(competitors=competitors, location=location):
                    data = self.compact_input()
                    data["competitor_bullets"] = competitors
                    data["keyword_candidates"] = [{
                        "term": " SIDE HANDLE ", "eligible_locations": [location],
                    }]
                    result = pipeline.run(data)
                    self.assertEqual(result["hard_pass"], passes)
                    keyword = result["final_payload"]["keywords"][0]
                    self.assertEqual(keyword["eligible_locations"], [location])
                    self.assertEqual(keyword["reported_used"], 1)
                    if not competitors or competitors[0] == ["Pouring grip"]:
                        self.assertNotIn("target", keyword)
                    if not passes:
                        self.assertIn("KEYWORD_PLACEMENT", {
                            row["code"] for row in result["validation"] if row["level"] == "FAIL"
                        })
                    self.assertEqual("## Final Listing" in pipeline.render_listing_markdown(result), passes)

    def test_zero_candidate_plan_preserves_variants_without_requiring_use(self):
        for term, variants, actual in (
            ("side handles", ["side handle"], 1), ("hand grip", [], 0),
        ):
            with self.subTest(term=term):
                data = self.compact_input()
                data["competitor_bullets"] = [["Pouring grip"], ["Pouring grip"]]
                data["keyword_candidates"] = [{
                    "term": term, "variants": variants, "eligible_locations": [" bullet 2 "],
                }]
                result = pipeline.run(data)
                self.assertTrue(result["hard_pass"])
                keyword = result["final_payload"]["keywords"][0]
                self.assertEqual(keyword["eligible_locations"], ["Bullet 2"])
                self.assertEqual(keyword.get("variants", []), variants)
                self.assertEqual(keyword["reported_used"], actual)
                self.assertNotIn("target", keyword)

    def test_german_standalone_zero_candidate_plan_is_enforced(self):
        for location, passes in (("Bullet 2", True), ("Bullet 3", False)):
            with self.subTest(location=location):
                data = self.pipeline_input("DE")
                data["final"]["competitor_brand_audit_reason"] = "No seller brand in these Bullet records"
                data["keyword_benchmark"] = {
                    "keywords": [{"term": "Doppelauslauf", "eligible_locations": [location]}],
                    "competitors": [["Zwei Ausläufe"], ["Zwei Ausläufe"]],
                }
                result = pipeline.run(data)
                self.assertEqual(result["hard_pass"], passes)
                self.assertEqual(result["final_payload"]["keywords"][0]["eligible_locations"], [location])

    def test_production_markdown_omits_low_level_pass_traces(self):
        markdown = pipeline.render_markdown(pipeline.run(self.compact_input()))
        self.assertIn("## 竞品与优化结论", markdown)
        self.assertIn("事实模式：target_anchored", markdown)
        self.assertIn("PASS FINAL", markdown)
        self.assertNotIn("PASS CHAR_LIMIT", markdown)
        self.assertIn("Title 28/75", markdown)
        self.assertIn("B1 27/255（移动端建议≤145）", markdown)
        self.assertIn("### 竞品提取关键词", markdown)
        self.assertNotIn("coverage 2/2", markdown)

    def test_description_length_boundaries_include_tips_and_block_delivery(self):
        for marketplace in ("US", "DE"):
            for length in (1999, 2000, 2001):
                with self.subTest(marketplace=marketplace, length=length):
                    data = self.compact_input() if marketplace == "US" else self.pipeline_input("DE")
                    draft = data["draft"] if marketplace == "US" else data["final"]
                    models = ", ".join(f"Auron P{number}" for number in range(1000, 1120))
                    source = ("Compatible with " if marketplace == "US" else "Passend für ") + models
                    chinese = "兼容 " + models
                    heading = "\n\nWarm Tips" if marketplace == "US" else "\n\nHinweise"
                    extra = length - len(draft["description"]) - len(source) - 1
                    self.assertTrue(0 <= extra <= source.count(","))
                    # Vary permitted list spacing without changing a model or fact.
                    source = source.replace(",", ", ", extra)
                    draft["description"] = draft["description"].replace(heading, "\n" + source + heading)
                    draft["translations"]["description"] = draft["translations"]["description"].replace(
                        "\n\n温馨提示", "\n" + chinese + "\n\n温馨提示"
                    )
                    if marketplace == "US":
                        data["facts"].append({
                            "field": "model_fit", "claim": source, "claim_zh": chinese,
                            "listing_matches": {"Description": source},
                            "translation_matches": {"Description": chinese},
                        })
                    else:
                        data["claim_review"]["claims"].append({
                            "claim": source, "claim_zh": chinese, "locations": ["Description"],
                            "location_matches": {"Description": source},
                            "translation_location_matches": {"Description": chinese},
                        })
                    self.assertEqual(len(draft["description"]), length)
                    result = pipeline.run(data)
                    self.assertEqual(result["hard_pass"], length <= 2000)
                    self.assertEqual(result["measurements"]["Description"], length)
                    self.assertEqual("## Final Listing" in pipeline.render_listing_markdown(result), length <= 2000)
                    if length > 2000:
                        self.assertTrue(any(
                            row["code"] == "CHAR_LIMIT" and row["location"] == "Description"
                            for row in result["validation"] if row["level"] == "FAIL"
                        ))

    def test_description_budget_does_not_apply_to_translation_or_partial_scope(self):
        data = self.compact_input()
        models = [f"P{number}" for number in range(1000, 1150)]
        source = "Compatible with Auron models " + ", ".join(models)
        chinese = "兼容型号：" + "、".join(f"Auron {model} 型号" for model in models)
        data["draft"]["description"] = data["draft"]["description"].replace(
            "\n\nWarm Tips", "\n" + source + "\n\nWarm Tips"
        )
        data["draft"]["translations"]["description"] = data["draft"]["translations"]["description"].replace(
            "\n\n温馨提示", "\n" + chinese + "\n\n温馨提示"
        )
        data["facts"].append({
            "field": "model_fit", "claim": source, "claim_zh": chinese,
            "listing_matches": {"Description": source},
            "translation_matches": {"Description": chinese},
        })
        self.assertGreater(len(data["draft"]["translations"]["description"]), 2000)
        self.assertTrue(pipeline.run(data)["hard_pass"])
        for marketplace in ("US", "DE"):
            result = pipeline.run(self.certification_lamp_input(marketplace))
            self.assertTrue(result["hard_pass"])
            self.assertNotIn("Description", result["measurements"])
            self.assertFalse(any(
                row["code"] == "CHAR_LIMIT" and row["location"] == "Description"
                for row in result["validation"]
            ))

    def test_full_listing_acceptance_fixtures_deliver_complete_descriptions(self):
        for marketplace in ("US", "DE"):
            with self.subTest(marketplace=marketplace):
                data = self.pipeline_input(marketplace)
                draft = data["final"]
                source, chinese = draft["description"], draft["translations"]["description"]
                features = [line for line in source.splitlines() if line.startswith("- ")]
                translated_features = [line for line in chinese.splitlines() if line.startswith("- ")]
                self.assertGreaterEqual(len(features), 3)
                self.assertLessEqual(len(features), 5)
                self.assertEqual(len(features), len(translated_features))
                for meaning in ("屏幕", "光照", "以实际产品为准", "仅供参考", "轻微偏差", "感谢您的理解"):
                    self.assertIn(meaning, chinese)
                if marketplace == "US":
                    self.assertIn("正常现象", chinese)
                result = pipeline.run(data)
                self.assertTrue(result["hard_pass"])
                markdown = pipeline.render_listing_markdown(result)
                self.assertIn(source, markdown)
                self.assertIn(chinese, markdown)

    def test_compound_chinese_numbers_cannot_replace_single_digit_measurements(self):
        for chinese, passes in (
            ("二毫米", True), ("两毫米", True), ("2毫米", True), ("十二毫米", False),
            ("二十二毫米", False), ("零点二毫米", False),
            ("二分之一毫米", False), ("两百毫米", False), ("两分之一毫米", False),
        ):
            with self.subTest(chinese=chinese):
                data = self.compact_input()
                data["draft"]["bullets"][0] = "CHECK LENGTH: Measures 2 mm"
                data["draft"]["translations"]["bullets"][0] = f"检查尺寸：{chinese}"
                data["facts"][2].update(
                    claim="Measures 2 mm", claim_zh=chinese, target="Measures 2 mm",
                    listing_matches={"Bullet 1": "Measures 2 mm"},
                    translation_matches={"Bullet 1": chinese},
                )
                data["competitor_bullets"] = []
                data["keyword_candidates"] = []
                result = pipeline.run(data)
                self.assertEqual(result["hard_pass"], passes)
                if not passes:
                    self.assertIn("TRANSLATION_UNIT", {
                        row["code"] for row in result["validation"] if row["level"] == "FAIL"
                    })

    def test_compound_chinese_package_counts_remain_explicit_quantities(self):
        for count, chinese, passes in (
            (2, "两个", True), (2, "２个", True), (10, "十个", True),
            (2, "十二个", False), (2, "二十二个", False),
            (2, "零点二个", False), (2, "二分之一个", False),
            (None, "十二个", False), (None, "零点二个", False),
        ):
            with self.subTest(count=count, chinese=chinese):
                data = self.compact_input()
                source = "Pitcher and lid included" if count is None else (
                    f"Pitcher and {'two' if count == 2 else 'ten'} lids included"
                )
                translated = f"套装内容：包含搅拌杯和{chinese}杯盖"
                data["draft"]["bullets"][3] = f"SET CONTENTS: {source}"
                data["draft"]["translations"]["bullets"][3] = translated
                data["facts"][5].update(
                    claim=source, claim_zh=translated, target=source,
                    listing_matches={"Bullet 4": source},
                    translation_matches={"Bullet 4": translated},
                )
                data["package_components"][1].update(
                    count=count, listing_matches={"Bullet 4": "lid" if count is None else "lids"},
                    translation_matches={"Bullet 4": f"{chinese}杯盖"},
                )
                result = pipeline.run(data)
                self.assertEqual(result["hard_pass"], passes)
                if not passes:
                    self.assertIn(
                        "PACKAGE_COUNT_UNKNOWN" if count is None else "PACKAGE_COUNT_TRANSLATION",
                        {row["code"] for row in result["validation"] if row["level"] == "FAIL"},
                    )

    def test_single_digit_translation_does_not_borrow_fraction_digits(self):
        for source, translated, passes in (
            ("3 ears", "三耳", True), ("1 item", "其中之一", True),
            ("2 items", "两件", True), ("3 ears", "十三耳", False),
            ("2 items", "二分之一", False), ("2 items", "两百件", False),
        ):
            with self.subTest(translated=translated):
                rows = []
                validate_final.translation_consistency(rows, "Title", source, translated, [])
                self.assertEqual(not any(row.level == "FAIL" for row in rows), passes)

    def test_certifications_require_matching_confirmed_bilingual_claims(self):
        cases = (
            ("US", "CE certified", "CE认证"), ("DE", "CE-zertifiziert", "CE认证"),
            ("US", "CE marked", "CE标志"), ("DE", "CE-Kennzeichnung", "CE标志"),
            ("US", "FDA approved", "FDA批准"), ("US", "FDA cleared", "FDA许可"),
            ("US", "FDA certified", "FDA认证"),
            ("US", "UL listed", "UL列名"), ("US", "ETL listed", "ETL列名"),
            ("US", "UL certified", "UL认证"), ("US", "ETL certified", "ETL认证"),
            ("US", "FCC certified", "FCC认证"), ("DE", "FCC-zertifiziert", "FCC认证"),
            ("US", "LFGB compliant", "符合LFGB"), ("US", "CPSIA compliant", "符合CPSIA"),
            ("US", "certified food-safe", "食品安全认证"),
        )
        for marketplace, source, translated in cases:
            for confirmed in (False, True):
                with self.subTest(marketplace=marketplace, source=source, confirmed=confirmed):
                    data = self.pipeline_input(marketplace)
                    data["final"]["translations"]["highlights"] += f"，{translated}"
                    claim = {"claim": source, "claim_zh": translated, "claim_type": "certification"}
                    if confirmed:
                        data["final"]["highlights"] += f", {source}"
                        claim.update(
                            seller_confirmed=True, locations=["Item Highlights"],
                            location_matches={"Item Highlights": source},
                            translation_location_matches={"Item Highlights": translated},
                        )
                    data["claim_review"]["claims"].append(claim)
                    result = pipeline.run(data)
                    self.assertEqual(result["hard_pass"], confirmed)
                    if not confirmed:
                        self.assertIn("TRANSLATION_RISK_CLAIM_CONFIRMATION", {
                            row["code"] for row in result["validation"] if row["level"] == "FAIL"
                        })

    def test_certification_cannot_borrow_another_claim_or_status(self):
        for source, translated in (
            ("BPA-free", "不含BPA，CE认证"), ("FDA approved", "CE认证"),
            ("CE marked", "CE认证"), ("CE certified", "CE标志"),
            ("FDA cleared", "FDA批准"), ("FDA approved", "FDA许可"),
            ("FDA approved", "FDA认证"), ("UL listed", "UL认证"),
            ("ETL listed", "ETL认证"),
        ):
            with self.subTest(source=source, translated=translated):
                data = self.compact_input()
                data["draft"]["highlights"] += f", {source}"
                data["draft"]["translations"]["highlights"] += f"，{translated}"
                data["risk_candidates"] = [{
                    "claim": source, "claim_zh": translated,
                    "claim_type": "bpa_free" if source == "BPA-free" else "certification",
                    "seller_confirmed": True,
                    "listing_matches": {"Item Highlights": source},
                    "translation_matches": {"Item Highlights": translated},
                }]
                result = pipeline.run(data)
                self.assertIn("TRANSLATION_RISK_CLAIM_CONFIRMATION", {
                    row["code"] for row in result["validation"] if row["level"] == "FAIL"
                })

    def test_certification_confirmation_covers_only_its_field_and_mapping(self):
        for location in ("Title", "Item Highlights"):
            with self.subTest(location=location):
                data = self.compact_input()
                key = "title" if location == "Title" else "highlights"
                data["draft"][key] += ", CE certified"
                data["draft"]["translations"][key] += "，CE认证产品"
                data["draft"]["translations"]["highlights"] += "，另有CE认证"
                data["risk_candidates"] = [{
                    "claim": "CE certified", "claim_zh": "CE认证产品",
                    "claim_type": "certification", "seller_confirmed": True,
                    "listing_matches": {location: "CE certified"},
                    "translation_matches": {location: "CE认证产品"},
                }]
                result = pipeline.run(data)
                self.assertIn("TRANSLATION_RISK_CLAIM_CONFIRMATION", {
                    row["code"] for row in result["validation"] if row["level"] == "FAIL"
                })

    def test_certification_patterns_do_not_capture_model_names(self):
        data = self.compact_input()
        data["draft"]["translations"]["highlights"] += "，型号CE123、XFDA、FCC123、LFGB123"
        self.assertTrue(pipeline.run(data)["hard_pass"])

    def test_blank_competitor_benchmark_matches_cli_and_pipeline(self):
        for marketplace, term in (("US", "64 oz"), ("DE", "58mm")):
            with self.subTest(marketplace=marketplace):
                competitors = [[f"Capacity {term}"], [], [" \t"], [f"Size {term}"]]
                proc = subprocess.run(
                    [sys.executable, "-X", "utf8", str(HERE / "kwfreq.py")],
                    input=json.dumps({"mode": "bullet", "keywords": [term], "competitors": competitors, "json": True}),
                    text=True, encoding="utf-8", capture_output=True, check=True,
                )
                data = self.pipeline_input(marketplace)
                data["keyword_benchmark"] = {"keywords": [term], "competitors": competitors}
                data["final"]["competitor_brand_audit_reason"] = "No seller brand appears in the supplied Bullet records"
                result = pipeline.run(data)
                self.assertTrue(result["hard_pass"])
                self.assertEqual(result["keyword_benchmark"], json.loads(proc.stdout))
                self.assertEqual(result["keyword_benchmark"][0]["counts"], [1, 1])

    def test_partial_keyword_override_keeps_other_frequency_requirements(self):
        data = self.compact_input()
        data["competitor_bullets"] = [[
            "Side handle for pouring", "Side handle on body", "Side handle grip",
            "Copolyester body construction", "Copolyester body material",
            "Copolyester body shell",
        ]] * 3
        data["keyword_candidates"] = ["side handle", "Copolyester body"]
        data["draft"]["keywords"] = [{
            "phrase": "SIDE HANDLE", "source": "competitor", "target": "3",
            "override_reason": (
                "Bullet 2 answers pouring control; repeating the handle in material, "
                "package or care adds no new buyer information"
            ),
        }]
        result = pipeline.run(data)
        self.assertFalse(result["hard_pass"])
        self.assertTrue(any(
            row["code"] == "KEYWORD_TARGET" and "Copolyester body" in row["message"]
            for row in result["validation"]
        ))
        self.assertTrue(any(
            row["code"] == "KEYWORD_OVERRIDE" and row["level"] == "WARN"
            for row in result["validation"]
        ))
        self.assertNotIn("## Final Listing", pipeline.render_listing_markdown(result))
        data["draft"]["keywords"].append({
            "phrase": "Copolyester body", "source": "competitor", "target": "3",
            "override_reason": (
                "Bullet 3 states the body material; repeating it in fit, "
                "package or care would add no distinct buyer information"
            ),
        })
        result = pipeline.run(data)
        self.assertTrue(result["hard_pass"])
        self.assertEqual(sum(
            row["code"] == "KEYWORD_OVERRIDE" and row["level"] == "WARN"
            for row in result["validation"]
        ), 2)

    def test_custom_priority_keeps_other_benchmarks_and_zero_placement(self):
        data = self.compact_input()
        data["competitor_bullets"] = [[
            "64 oz capacity", "Holds 64 oz", "64 oz vessel",
            "Copolyester body construction", "Copolyester body material",
            "Copolyester body shell",
        ]] * 3
        data["keyword_candidates"] = [
            "64 oz", "Copolyester body",
            {"term": "side handles", "variants": ["side handle"],
             "eligible_locations": ["Bullet 2"]},
        ]
        data["draft"]["keywords"] = [{"phrase": "64 OZ", "source": "user_priority"}]
        result = pipeline.run(data)
        self.assertFalse(result["hard_pass"])
        keywords = {item["phrase"].casefold(): item for item in result["final_payload"]["keywords"]}
        self.assertNotIn("target", keywords["64 oz"])
        self.assertEqual(keywords["copolyester body"]["target"], "3")
        self.assertNotIn("target", keywords["side handles"])
        self.assertEqual(keywords["side handles"]["reported_used"], 1)
        self.assertEqual(keywords["side handles"]["eligible_locations"], ["Bullet 2"])
        data["priority_keywords"] = ["Copolyester body"]
        result = pipeline.run(data)
        self.assertTrue(result["hard_pass"])
        self.assertTrue(all("target" not in item for item in result["final_payload"]["keywords"]))

    def test_rendered_keyword_counts_use_final_copy_and_accepted_variants(self):
        for case in ("case", "missing_count", "variant", "missing_spec"):
            with self.subTest(case=case):
                data = self.compact_input()
                term = "64 oz"
                if case == "case":
                    data["priority_keywords"] = [{"term": "64 OZ"}]
                elif case == "missing_count":
                    data["draft"]["keywords"] = [{"phrase": term, "target": "1-2"}]
                elif case == "variant":
                    term = "64 ounces"
                    data["keyword_candidates"] = [{"term": term, "variants": ["64 oz"]}]
                    data["competitor_bullets"] = [["Holds 64 ounces"], ["Capacity is 64 ounces"]]
                else:
                    data["draft"]["keywords"] = [{"phrase": "pitcher", "source": "user_priority"}]
                result = pipeline.run(data)
                self.assertTrue(result["hard_pass"])
                self.assertIn(
                    f"{term}（建议1-2，实际1）", pipeline.render_listing_markdown(result)
                )

    def certification_lamp_input(self, marketplace="US", ordinary_count=3):
        title = "Desk Lamp" if marketplace == "US" else "Schreibtischlampe"
        material = "Metal body" if marketplace == "US" else "Metallgehäuse"
        color = "Black" if marketplace == "US" else "Schwarz"
        facts = [{
            "field": "product_type", "claim": title, "claim_zh": "台灯",
            "listing_matches": {"Title": title},
            "translation_matches": {"Title": "台灯"},
        }, {
            "field": "material", "claim": material, "claim_zh": "金属灯身",
            "listing_matches": {"Item Highlights": material},
            "translation_matches": {"Item Highlights": "金属灯身"},
        }]
        if ordinary_count == 3:
            facts.append({
                "field": "color", "claim": color, "claim_zh": "黑色",
                "listing_matches": {"Item Highlights": color},
                "translation_matches": {"Item Highlights": "黑色"},
            })
        return {
            "schema_version": 2, "marketplace": marketplace,
            "mode": "target_anchored", "scope": "title_highlights",
            "facts": facts, "risk_candidates": [], "package_components": [],
            "draft": {
                "title": title,
                "highlights": material + (", " + color if ordinary_count == 3 else ""),
                "translations": {
                    "title": "台灯",
                    "highlights": "金属灯身" + ("，黑色" if ordinary_count == 3 else ""),
                },
            },
        }

    def package_absence_input(self, marketplace="US", scope="title_highlights"):
        absence = "Battery not included" if marketplace == "US" else "Batterie nicht enthalten"
        component = {
            "component": "battery",
            "source_singular": ["battery" if marketplace == "US" else "Batterie"],
            "source_plural": ["batteries" if marketplace == "US" else "Batterien"],
            "translation_terms": ["电池"], "present": False, "count": None,
        }
        if scope == "title_highlights":
            data = self.certification_lamp_input(marketplace)
            field = "Item Highlights"
            data["draft"]["highlights"] += ". " + absence
            data["draft"]["translations"]["highlights"] += "。不含电池"
        elif marketplace == "US":
            data = self.compact_input()
            field = "Bullet 4"
            data["draft"]["bullets"][3] += "; " + absence
            data["draft"]["translations"]["bullets"][3] += "；不含电池"
        else:
            data = self.pipeline_input(marketplace)
            field = "Bullet 4"
            data["final"]["bullets"][3] += "; " + absence
            data["final"]["translations"]["bullets"][3] += "；不含电池"
        source_map, chinese_map = {field: absence}, {field: "不含电池"}
        component.update(listing_matches=source_map.copy(), translation_matches=chinese_map.copy())
        if "facts" in data:
            data["facts"].append({
                "field": "battery_absence", "claim": absence, "claim_zh": "不含电池",
                "listing_matches": source_map, "translation_matches": chinese_map,
            })
            data["package_components"].append(component)
        else:
            data["claim_review"]["claims"].append({
                "claim": absence, "claim_zh": "不含电池", "locations": [field],
                "location_matches": source_map, "translation_location_matches": chinese_map,
            })
            data["final"]["package_components"].append(component)
        return data

    def test_package_absence_is_delivered_in_both_markets_and_scopes(self):
        for marketplace in ("US", "DE"):
            for scope in ("title_highlights", "full_listing"):
                with self.subTest(marketplace=marketplace, scope=scope):
                    data = self.package_absence_input(marketplace, scope)
                    result = pipeline.run(data)
                    self.assertTrue(result["hard_pass"])
                    self.assertFalse(result["clarification_required"])
                    markdown = pipeline.render_listing_markdown(result)
                    self.assertIn("Battery not included" if marketplace == "US" else "Batterie nicht enthalten", markdown)
                    self.assertIn("不含电池", markdown)
                    self.assertIn("## Final Listing", markdown)

    def test_package_absence_accepts_direct_grammar_and_formatting_variants(self):
        for marketplace, source, chinese in (
            ("US", "The battery is not included", "不包含电池"),
            ("US", "Batteries are not included", "不含电池"),
            ("US", "BATTERY  NOT INCLUDED", "不 含 电 池"),
            ("DE", "Die Batterie ist nicht enthalten", "不含电池"),
            ("DE", "Die Batterien sind nicht enthalten", "未包含电池"),
        ):
            with self.subTest(source=source):
                data = self.package_absence_input(marketplace)
                fact = data["facts"][-1]
                old = fact["claim"]
                data["draft"]["highlights"] = data["draft"]["highlights"].replace(old, source)
                data["draft"]["translations"]["highlights"] = data["draft"]["translations"]["highlights"].replace("不含电池", chinese)
                fact.update(claim=source, claim_zh=chinese, listing_matches={"Item Highlights": source}, translation_matches={"Item Highlights": chinese})
                data["package_components"][-1].update(listing_matches={"Item Highlights": source}, translation_matches={"Item Highlights": chinese})
                self.assertTrue(pipeline.run(data)["hard_pass"])

    def test_absent_component_rejects_positive_and_unrelated_negation(self):
        for marketplace, source, chinese in (
            ("US", "Battery included", "包含电池"),
            ("US", "Adapter not included; battery included", "不含适配器；包含电池"),
            ("DE", "Batterie enthalten", "包含电池"),
            ("DE", "Adapter nicht enthalten; Batterie enthalten", "不含适配器；包含电池"),
        ):
            with self.subTest(source=source):
                data = self.package_absence_input(marketplace)
                fact = data["facts"][-1]
                data["draft"]["highlights"] = data["draft"]["highlights"].replace(fact["claim"], source)
                data["draft"]["translations"]["highlights"] = data["draft"]["translations"]["highlights"].replace("不含电池", chinese)
                fact.update(claim=source, claim_zh=chinese, listing_matches={"Item Highlights": source}, translation_matches={"Item Highlights": chinese})
                data["package_components"][-1].update(listing_matches={"Item Highlights": source}, translation_matches={"Item Highlights": chinese})
                result = pipeline.run(data)
                self.assertFalse(result["hard_pass"])
                self.assertIn("PACKAGE_PRESENCE", {row["code"] for row in result["validation"] if row["level"] == "FAIL"})

    def test_package_absence_cannot_cover_another_mention_or_field(self):
        for marketplace in ("US", "DE"):
            for side in ("source", "chinese"):
                for field in ("highlights", "title"):
                    with self.subTest(marketplace=marketplace, side=side, field=field):
                        data = self.package_absence_input(marketplace)
                        if side == "source":
                            data["draft"][field] += "; " + ("battery included" if marketplace == "US" else "Batterie enthalten")
                        else:
                            data["draft"]["translations"][field] += "；包含电池"
                        result = pipeline.run(data)
                        self.assertFalse(result["hard_pass"])
                        self.assertIn("PACKAGE_PRESENCE", {row["code"] for row in result["validation"] if row["level"] == "FAIL"})

    def test_package_absence_requires_complete_bilingual_maps(self):
        for change in ("missing", "noun_only", "positive_chinese", "positive_count"):
            with self.subTest(change=change):
                data = self.package_absence_input()
                component = data["package_components"][-1]
                if change == "missing":
                    component.update(listing_matches={}, translation_matches={})
                elif change == "noun_only":
                    component.update(listing_matches={"Item Highlights": "Battery"}, translation_matches={"Item Highlights": "电池"})
                elif change == "positive_chinese":
                    data["draft"]["translations"]["highlights"] = "金属灯身，黑色。包含电池"
                    data["facts"][-1]["translation_matches"] = {"Item Highlights": "包含电池"}
                    component["translation_matches"] = {"Item Highlights": "包含电池"}
                else:
                    component["count"] = 1
                result = pipeline.run(data)
                self.assertFalse(result["hard_pass"])
                self.assertIn("PACKAGE_PRESENCE", {row["code"] for row in result["validation"] if row["level"] == "FAIL"})

    def test_package_absence_preserves_decimal_component_names(self):
        for marketplace, name, source, chinese in (
            ("US", "M2.5 screws", "M2.5 screws not included", "不含M2.5螺丝"),
            ("DE", "M2,5-Schrauben", "M2,5-Schrauben nicht enthalten", "不含M2,5螺丝"),
        ):
            with self.subTest(marketplace=marketplace):
                data = self.package_absence_input(marketplace)
                fact = data["facts"][-1]
                data["draft"]["highlights"] = data["draft"]["highlights"].replace(fact["claim"], source)
                data["draft"]["translations"]["highlights"] = data["draft"]["translations"]["highlights"].replace("不含电池", chinese)
                fact.update(claim=source, claim_zh=chinese, listing_matches={"Item Highlights": source}, translation_matches={"Item Highlights": chinese})
                data["package_components"][-1].update(
                    component="screws", source_singular=[name], source_plural=[],
                    translation_terms=[chinese[2:]], listing_matches={"Item Highlights": source},
                    translation_matches={"Item Highlights": chinese},
                )
                self.assertTrue(pipeline.run(data)["hard_pass"])

    def test_confirmed_candidate_counts_like_the_same_used_fact(self):
        for mode in ("target_anchored", "consensus_only"):
            for marketplace, source, chinese in (
                ("US", "UL listed", "UL列名"),
                ("DE", "CE-Kennzeichnung", "CE标志"),
            ):
                for container in ("facts", "risk_candidates"):
                    with self.subTest(mode=mode, marketplace=marketplace, container=container):
                        data = self.certification_lamp_input(marketplace, 2)
                        data["mode"] = mode
                        for fact in data["facts"]:
                            fact["competitors"] = [fact["claim"]]
                        claim = {
                            "claim": source, "claim_zh": chinese,
                            "claim_type": "certification", "seller_confirmed": True,
                            "listing_matches": {"Item Highlights": source},
                            "translation_matches": {"Item Highlights": chinese},
                        }
                        if container == "facts":
                            claim.update(field="certification", competitors=[source])
                        data[container].append(claim)
                        data["draft"]["highlights"] += ", " + source
                        data["draft"]["translations"]["highlights"] += "，" + chinese
                        result = pipeline.run(data)
                        self.assertTrue(result["hard_pass"])
                        self.assertFalse(result["clarification_required"])
                        self.assertEqual(result["evidence"]["content_units"], 3)
                        rendered = pipeline.render_listing_markdown(result)
                        self.assertIn("已确认 Group B 1 项，共 3 个可用信息单元", rendered)
                        self.assertIn("## Final Listing", rendered)

    def test_held_and_disabled_candidates_do_not_count(self):
        for ordinary_count in (2, 3):
            for disabled in (False, True):
                with self.subTest(ordinary_count=ordinary_count, disabled=disabled):
                    data = self.certification_lamp_input(ordinary_count=ordinary_count)
                    data["risk_candidates"] = [{
                        "claim": "UL listed", "claim_zh": "UL列名",
                        "claim_type": "certification", "seller_confirmed": disabled,
                    }]
                    if disabled:
                        data["confirmation_text"] = "B暂不使用：B1"
                    result = pipeline.run(data)
                    self.assertTrue(result["hard_pass"])
                    self.assertEqual(result["evidence"]["content_units"], ordinary_count)
                    self.assertEqual(result["clarification_required"], ordinary_count < 3)
                    self.assertEqual(result["claim_review"]["b_claims"][0]["status"], "hold")

    def test_direct_confirmation_and_multiple_fields_count_one_candidate(self):
        data = self.certification_lamp_input(ordinary_count=2)
        data["draft"]["title"] += ", UL listed"
        data["draft"]["highlights"] += ", UL listed"
        data["draft"]["translations"]["title"] += "，UL列名"
        data["draft"]["translations"]["highlights"] += "，UL列名"
        data["risk_candidates"] = [{
            "claim": "UL listed", "claim_zh": "UL列名", "claim_type": "certification",
            "listing_matches": {"Title": "UL listed", "Item Highlights": "UL listed"},
            "translation_matches": {"Title": "UL列名", "Item Highlights": "UL列名"},
        }]
        data["confirmation_text"] = "B已核实并启用：B1"
        result = pipeline.run(data)
        self.assertTrue(result["hard_pass"])
        self.assertFalse(result["clarification_required"])
        self.assertEqual(result["evidence"]["content_units"], 3)
        data["risk_candidates"][0]["translation_matches"]["Title"] = "不存在的认证翻译"
        self.assertFalse(pipeline.run(data)["hard_pass"])

    def test_publication_count_still_blocks_core_conflicts(self):
        data = self.certification_lamp_input()
        data["facts"][0].update(core_identity=True, target_conflict=True)
        result = pipeline.run(data)
        self.assertFalse(result["hard_pass"])
        self.assertTrue(result["clarification_required"])
        markdown = pipeline.render_listing_markdown(result)
        self.assertIn("product_type: target attribute conflicts with itself", markdown)
        self.assertNotIn("## Final Listing", markdown)

    def test_compact_consensus_without_decision_returns_structured_clarification(self):
        data = self.compact_input()
        data["mode"] = "consensus_only"
        result = pipeline.run(data)
        self.assertFalse(result["hard_pass"])
        self.assertTrue(result["clarification_required"])
        self.assertTrue(any(
            item["reason"] == "compact fact has no eligible consensus decision"
            for item in result["evidence"]["clarifications"]
        ))
        failure_codes = {
            item["code"] for item in result["validation"] if item["level"] == "FAIL"
        }
        self.assertEqual(failure_codes, {"CONSENSUS_DECISION"})
        markdown = pipeline.render_listing_markdown(result)
        self.assertIn("no eligible consensus decision", markdown)
        self.assertNotIn("## Final Listing", markdown)

    def test_consensus_clarification_keeps_independent_validation_failures(self):
        data = self.compact_input()
        data["mode"] = "consensus_only"
        data["draft"]["highlights"] += " ★"
        result = pipeline.run(data)
        failures = {
            item["code"] for item in result["validation"] if item["level"] == "FAIL"
        }
        self.assertEqual(failures, {"CONSENSUS_DECISION", "EMOJI"})
        self.assertTrue(result["clarification_required"])
        self.assertNotIn("## Final Listing", pipeline.render_listing_markdown(result))

    def test_full_listing_summary_counts_confirmed_candidate(self):
        data = self.compact_input()
        data["risk_candidates"] = [{
            "claim": "LFGB compliant", "claim_zh": "符合LFGB", "seller_confirmed": True,
            "listing_matches": {"Item Highlights": "LFGB compliant"},
            "translation_matches": {"Item Highlights": "符合LFGB"},
        }]
        data["draft"]["highlights"] += ", LFGB compliant"
        data["draft"]["translations"]["highlights"] += "，符合LFGB"
        result = pipeline.run(data)
        self.assertTrue(result["hard_pass"])
        self.assertEqual(result["evidence"]["content_units"], 9)
        self.assertIn("已确认 Group B 1 项，共 9 个可用信息单元", pipeline.render_listing_markdown(result))

    def test_explicit_risk_flag_survives_fact_conversion(self):
        for source, chinese in (("GS-geprüft", "GS认证"), ("CE-Kennzeichnung", "CE标志")):
            for container in ("facts", "risk_candidates"):
                with self.subTest(source=source, container=container):
                    data = self.certification_lamp_input("DE")
                    claim = {
                        "claim": source, "claim_zh": chinese,
                        "high_risk": True, "seller_confirmed": True,
                        "listing_matches": {"Item Highlights": source},
                        "translation_matches": {"Item Highlights": chinese},
                    }
                    if container == "facts":
                        claim["field"] = "certification"
                    data[container].append(claim)
                    data["draft"]["highlights"] += ", " + source
                    data["draft"]["translations"]["highlights"] += "，" + chinese
                    result = pipeline.run(data)
                    self.assertTrue(result["hard_pass"])
                    self.assertEqual(len(result["claim_review"]["b_claims"]), 1)
                    self.assertEqual(result["claim_review"]["b_claims"][0]["status"], "confirmed_use")
                    self.assertEqual(result["evidence"]["publication_counts"]["confirmed_b"], 1)
                    rendered = pipeline.render_listing_markdown(result)
                    self.assertIn("| B1 | " + source, rendered)
                    self.assertNotIn("| A4 | " + source, rendered)
                    claim["seller_confirmed"] = False
                    if container == "facts":
                        result = pipeline.run(data)
                        self.assertFalse(result["hard_pass"])
                        self.assertEqual(
                            [item["code"] for item in result["validation"]],
                            ["RISK_MAPPING"],
                        )
                        self.assertNotIn(
                            "## Final Listing", pipeline.render_listing_markdown(result)
                        )
                    else:
                        self.assertFalse(pipeline.run(data)["hard_pass"])

    def test_untyped_certification_candidates_follow_the_complete_flow(self):
        for marketplace, source, chinese in (
            ("US", "UL-listed", "UL列名"),
            ("DE", "CE-Kennzeichnung", "CE标志"),
            ("DE", "CE-zertifiziert", "CE认证"),
        ):
            for confirmed in (False, True):
                with self.subTest(source=source, confirmed=confirmed):
                    data = self.certification_lamp_input(marketplace)
                    claim = {"claim": source, "claim_zh": chinese, "seller_confirmed": confirmed}
                    if confirmed:
                        claim.update(
                            listing_matches={"Item Highlights": source},
                            translation_matches={"Item Highlights": chinese},
                        )
                        data["draft"]["highlights"] += ", " + source
                        data["draft"]["translations"]["highlights"] += "，" + chinese
                    data["risk_candidates"] = [claim]
                    result = pipeline.run(data)
                    self.assertTrue(result["hard_pass"])
                    self.assertFalse(result["clarification_required"])
                    self.assertEqual(result["claim_review"]["b_claims"][0]["status"], "confirmed_use" if confirmed else "hold")
                    self.assertEqual(result["evidence"]["content_units"], 4 if confirmed else 3)
                    self.assertIn("## Final Listing", pipeline.render_listing_markdown(result))

    def test_unconfirmed_certification_forms_are_scanned_in_source_copy(self):
        for marketplace, source in (("US", "UL-listed"), ("DE", "CE-Kennzeichnung"), ("DE", "CE-zertifiziert")):
            with self.subTest(source=source):
                data = self.certification_lamp_input(marketplace)
                data["draft"]["highlights"] += ", " + source
                result = pipeline.run(data)
                failures = [row for row in result["validation"] if row["level"] == "FAIL"]
                self.assertEqual([row["code"] for row in failures], ["RISK_CLAIM_CONFIRMATION"])
                self.assertNotIn("## Final Listing", pipeline.render_listing_markdown(result))

    def test_source_model_names_do_not_become_certifications(self):
        data = self.certification_lamp_input()
        data["draft"]["title"] += " CE123"
        data["draft"]["translations"]["title"] += " CE123"
        result = pipeline.run(data)
        self.assertTrue(result["hard_pass"])
        self.assertEqual(result["claim_review"]["b_claims"], [])

    def test_free_form_optimization_claim_is_not_rendered(self):
        data = self.compact_input()
        data["draft"]["optimization_conclusion"] = "Gift positioning was added"
        markdown = pipeline.render_markdown(pipeline.run(data))
        self.assertNotIn("Gift positioning was added", markdown)


class PlanBRegressionTests(unittest.TestCase):
    def compact_cup_input(self) -> dict[str, object]:
        return {
            "schema_version": 2,
            "marketplace": "US",
            "mode": "target_anchored",
            "facts": [
                {
                    "field": "product_type",
                    "claim": "Coffee cup",
                    "claim_zh": "咖啡杯",
                    "listing_matches": {
                        "Title": "Coffee Cup",
                        "Item Highlights": "Coffee cup",
                        "Bullet 1": "Coffee cup",
                        "Bullet 2": "Coffee cup",
                        "Bullet 3": "Coffee cup",
                        "Bullet 4": "Coffee cup",
                        "Bullet 5": "Coffee cup",
                        "Description": "Coffee cup",
                    },
                    "translation_matches": {
                        "Title": "咖啡杯",
                        "Item Highlights": "咖啡杯",
                        "Bullet 1": "咖啡杯",
                        "Bullet 2": "咖啡杯",
                        "Bullet 3": "咖啡杯",
                        "Bullet 4": "咖啡杯",
                        "Bullet 5": "咖啡杯",
                        "Description": "咖啡杯",
                    },
                },
                {
                    "field": "material",
                    "claim": "Glass",
                    "claim_zh": "玻璃",
                    "listing_matches": {"Bullet 3": "Glass"},
                    "translation_matches": {"Bullet 3": "玻璃"},
                },
                {
                    "field": "capacity",
                    "claim": "250 ml",
                    "claim_zh": "250毫升",
                    "listing_matches": {"Bullet 2": "250 ml"},
                    "translation_matches": {"Bullet 2": "250毫升"},
                },
            ],
            "package_components": [],
            "draft": {
                "title": "Coffee Cup",
                "highlights": "Coffee cup for daily use",
                "bullets": [
                    "Design: Coffee cup for everyday serving",
                    "Capacity: Coffee cup holds 250 ml",
                    "Material: Glass coffee cup body",
                    "Care: Coffee cup hand wash recommended",
                    "Use: Coffee cup for home drinks",
                ],
                "description": (
                    "Coffee cup summary\n\n"
                    + validate_final.US_TIP_1
                    + "\n"
                    + validate_final.US_TIP_2
                ),
                "translations": {
                    "title": "咖啡杯",
                    "highlights": "日常使用的咖啡杯",
                    "bullets": [
                        "设计：日常使用的咖啡杯",
                        "容量：咖啡杯容量250毫升",
                        "材质：玻璃咖啡杯身",
                        "护理：咖啡杯建议手洗",
                        "用途：家用饮品咖啡杯",
                    ],
                    "description": (
                        "咖啡杯摘要\n\n"
                        "1. 屏幕和光线差异可能导致轻微色差，请以实物为准。\n"
                        "2. 尺寸为手工测量仅供参考，可能存在微小偏差。"
                    ),
                },
            },
        }

    def test_token_re_splits_multiplication_and_keeps_uppercase_sharp_s(self):
        self.assertEqual(kwfreq.tokens("10×20 cm"), ["10", "20", "cm"])
        self.assertEqual(validate_final.tokens("10×20 cm"), ["10", "20", "cm"])
        self.assertEqual(kwfreq.tokens("GROẞ"), ["groß"])
        self.assertEqual(validate_final.tokens("M2.5"), ["m2.5"])

    def test_kwfreq_accepts_utf8_bom_on_stdin_and_file(self):
        payload = json.dumps({
            "mode": "bullet",
            "keywords": ["coffee cup"],
            "competitors": [["coffee cup"], ["coffee cup"]],
        })
        proc_stdin = subprocess.run(
            [sys.executable, str(HERE / "kwfreq.py")],
            input="\ufeff" + payload,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc_stdin.returncode, 0, proc_stdin.stderr)
        self.assertIn("coffee cup", proc_stdin.stdout)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kw.json"
            path.write_text(payload, encoding="utf-8-sig")
            proc_file = subprocess.run(
                [sys.executable, str(HERE / "kwfreq.py"), "--file", str(path)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(proc_file.returncode, 0, proc_file.stderr)
            self.assertIn("coffee cup", proc_file.stdout)

    def test_decimal_model_keyword_shares_single_token_cap(self):
        data = self.compact_cup_input()
        data["competitor_bullets"] = [["M2.5 M2.5"], ["M2.5 M2.5"], ["M2.5 M2.5"]]
        data["keyword_candidates"] = ["M2.5"]
        data["draft"]["bullets"][0] = "Design: M2.5 coffee cup M2.5"
        data["draft"]["bullets"][1] = "Capacity: M2.5 coffee cup holds 250 ml"
        data["draft"]["bullets"][2] = "Material: Glass M2.5 coffee cup body"
        data["draft"]["translations"]["bullets"][0] = "设计：M2.5咖啡杯M2.5"
        data["draft"]["translations"]["bullets"][1] = "容量：M2.5咖啡杯容量250毫升"
        data["draft"]["translations"]["bullets"][2] = "材质：玻璃M2.5咖啡杯身"
        result = pipeline.run(data)
        self.assertTrue(result["hard_pass"])
        codes = {(r["level"], r["code"]) for r in result["validation"]}
        self.assertIn(("WARN", "KEYWORD_UPPER"), codes)

    def test_unit_re_ignores_in_prepositions_and_5g_while_checking_real_inches(self):
        for src, zh in (
            ("3-in-1 Coffee Tool", "3合1咖啡工具"),
            ("5 in 1 Kit", "5合1套装"),
            ("Released 2026 in Germany", "2026年在德国发布"),
            ("5G WiFi Module", "5G无线模块"),
            # Literal forms kept in Chinese must not become inch/gram pairs.
            ("5G WiFi module, 2 m cable", "5G无线模块，2米线"),
            ("3-in-1 tool, 250 ml cup", "3-in-1工具，250毫升杯"),
        ):
            res: list[validate_final.Result] = []
            validate_final.translation_consistency(res, "Bullet 1", src, zh, [], "US")
            self.assertEqual([r.code for r in res], [], f"unexpected failure for {src!r}")
        bad: list[validate_final.Result] = []
        validate_final.translation_consistency(bad, "Bullet 1", "Width 8.66 in", "宽8.66厘米", [], "US")
        self.assertIn("TRANSLATION_UNIT", [r.code for r in bad])

    def test_temperature_symbols_us_thousands_and_chinese_ten_pass(self):
        for src, zh, mkt in (
            ("Rated to 200 °C", "耐温200°C", "US"),
            ("Rated to 200 °C", "耐温200℃", "US"),
            ("Rated to 380 °F", "耐温380°F", "US"),
            ("Rated to 380 °F", "耐温380℉", "US"),
            ("Holds 1,000 ml", "容量1000毫升", "US"),
            ("Holds 1,000 ml", "容量1,000毫升", "US"),
            ("Stärke 1,5 mm", "厚度1.5毫米", "DE"),
            ("Cable 10 m", "线长十米", "US"),
        ):
            res: list[validate_final.Result] = []
            validate_final.translation_consistency(res, "Bullet 1", src, zh, [], mkt)
            self.assertEqual([r.code for r in res], [], f"unexpected failure for {src!r}/{zh!r}")

    def test_polarity_accepts_idiomatic_fang_mian_and_catches_stainless_bypass(self):
        for src, zh in (
            ("Non-slip base for stability", "防滑底座更稳固"),
            ("No-drill wall mount", "免打孔壁挂安装"),
            ("Ohne Werkzeug montierbar", "免工具安装"),
            ("Stainless steel cup, not dishwasher safe", "不锈钢杯，不可放洗碗机"),
            ("Do not microwave", "禁止微波加热"),
        ):
            res: list[validate_final.Result] = []
            validate_final.translation_consistency(res, "Bullet 1", src, zh, [], "US")
            self.assertEqual([r.code for r in res], [], f"unexpected failure for {src!r}/{zh!r}")
        # Paired idiomatic 防/免 terms must not satisfy an unrelated or
        # separate negation that the translation dropped.
        for src, zh in (
            ("Stainless steel cup, not dishwasher safe", "不锈钢杯，可放洗碗机"),
            ("Does not include lid", "防滑杯，含杯盖"),
            ("Not dust-proof", "防尘"),
            ("No lid included", "免费赠送杯盖"),
            ("Sold without lid", "免费赠送杯盖"),
            ("Shipped without power cord", "免费附赠电源线"),
            ("Ohne Deckel geliefert", "免工具安装，含杯盖"),
            ("Non-slip base, does not include lid", "防滑底座，含杯盖"),
            ("Non-pressurized basket, non-slip base", "防滑底座，加压滤篮"),
            ("Non-toxic coating", "防刮涂层"),
            ("No-spill lid, without straw", "防漏杯盖，附带吸管"),
        ):
            bypass: list[validate_final.Result] = []
            validate_final.translation_consistency(bypass, "Bullet 1", src, zh, [], "US")
            self.assertEqual([r.code for r in bypass], ["TRANSLATION_POLARITY"], f"{src!r}/{zh!r}")

    def test_encoded_entity_checked_in_bullets_and_description_with_locations(self):
        data = self.compact_cup_input()
        data["draft"]["bullets"][0] = "Design: Coffee cup &amp; saucer styling"
        data["draft"]["description"] = (
            "Coffee cup &amp; mug\n\n" + validate_final.US_TIP_1 + "\n" + validate_final.US_TIP_2
        )
        result = pipeline.run(data)
        self.assertFalse(result["hard_pass"])
        entity_locs = {
            r["location"] for r in result["validation"] if r["code"] == "ENCODED_ENTITY"
        }
        self.assertEqual(entity_locs, {"Bullet 1", "Description"})

    def test_phone_re_exempts_dates_and_slash_dimensions_while_blocking_phone_formats(self):
        data = self.compact_cup_input()
        data["draft"]["bullets"][0] = "Design: Coffee cup sizes 120/150/180 mm, 51/53/54/58 mm, 500/1000 ml, 0.5/1.0/1.5/2.0 mm"
        data["draft"]["bullets"][3] = "Care: Coffee cup batch updated 2026-07-27"
        data["draft"]["translations"]["bullets"][0] = "设计：咖啡杯尺寸120/150/180毫米、51/53/54/58毫米、500/1000毫升、0.5/1.0/1.5/2.0毫米"
        data["draft"]["translations"]["bullets"][3] = "护理：咖啡杯批次更新2026-07-27"
        result = pipeline.run(data)
        self.assertFalse(any(r["code"] == "PHONE" for r in result["validation"]))

        for bad_bullet, bad_zh in (
            ("Care: Coffee cup call 030/1234567", "护理：咖啡杯致电030/1234567"),
            ("Care: Coffee cup call 030/123/4567", "护理：咖啡杯致电030/123/4567"),
            ("Care: Coffee cup contact 030/1234567 in Berlin", "护理：咖啡杯柏林联系030/1234567"),
            ("Care: Coffee cup hotline 030/1234567 in Deutschland", "护理：咖啡杯德国热线030/1234567"),
            ("Care: Coffee cup call 030/1234567 m office", "护理：咖啡杯致电030/1234567"),
            ("Care: Coffee cup call 030/123/4567 mm", "护理：咖啡杯致电030/123/4567毫米"),
            ("Care: Coffee cup sizes 120/150/180", "护理：咖啡杯尺寸120/150/180"),
            ("Care: Coffee cup Part No. 1234-5678", "护理：咖啡杯零件号1234-5678"),
        ):
            bad = self.compact_cup_input()
            bad["draft"]["bullets"][3] = bad_bullet
            bad["draft"]["translations"]["bullets"][3] = bad_zh
            bad_result = pipeline.run(bad)
            self.assertTrue(any(r["code"] == "PHONE" for r in bad_result["validation"]), bad_bullet)

    def test_package_counts_ignore_list_numbering_and_parse_multiplier_x(self):
        self.assertEqual(
            validate_final.latin_counts_near_terms("1. Stainless filter basket", ["filter basket"]),
            [],
        )
        self.assertEqual(
            validate_final.latin_counts_near_terms(
                "Includes 2x filter baskets", ["filter basket", "filter baskets"]
            ),
            [2],
        )
        data = self.compact_cup_input()
        data["package_components"] = [{
            "component": "tamper",
            "source_singular": ["tamper"],
            "source_plural": ["tampers"],
            "translation_terms": ["压粉锤"],
            "present": False,
            "count": 2,
            "listing_matches": {},
            "translation_matches": {},
        }]
        result = pipeline.run(data)
        self.assertTrue(any(r["code"] == "PACKAGE_PRESENCE" for r in result["validation"]))

    def test_title_repetition_exempts_prepositions_not_units_and_keeps_location(self):
        data = self.compact_cup_input()
        data["draft"]["title"] = "Coffee Cup for Tea, Mug for Cocoa, Glass for Juice"
        data["draft"]["translations"]["title"] = "咖啡杯，可装茶、可可和果汁"
        result = pipeline.run(data)
        self.assertFalse(any(r["code"] == "TITLE_REPETITION" for r in result["validation"]))

        # Amazon exempts only prepositions, articles and conjunctions; a unit
        # repeated three times still violates the title rule.
        for title, zh in (
            ("Coffee Cup Set 10 cm x 20 cm x 30 cm", "咖啡杯套装 10厘米x20厘米x30厘米"),
            ("Coffee Cup Coffee Mug Coffee Glass", "咖啡杯咖啡马克杯咖啡玻璃杯"),
        ):
            stuffed = self.compact_cup_input()
            stuffed["draft"]["title"] = title
            stuffed["draft"]["translations"]["title"] = zh
            rep = [
                r for r in pipeline.run(stuffed)["validation"]
                if r["code"] == "TITLE_REPETITION"
            ]
            self.assertEqual(len(rep), 1, title)
            self.assertEqual(rep[0]["location"], "Title")

    def test_nonrecoverable_does_not_mask_other_marketing_and_de_disclaimer_passes(self):
        data = self.compact_cup_input()
        data["draft"]["highlights"] = "Best premium coffee cup"
        data["draft"]["translations"]["highlights"] = "最佳高端咖啡杯"
        result = pipeline.run(data)
        codes = {r["code"] for r in result["validation"] if r["level"] == "FAIL"}
        self.assertIn("NONRECOVERABLE_CLAIM", codes)
        self.assertIn("UNSUPPORTED_MARKETING_CLAIM", codes)

        # Full pipeline (both claim_review and validate_final) must accept DE
        # non-commercial disclaimers while still blocking positive commercial claims.
        de_pipeline = {
            "schema_version": 2,
            "marketplace": "DE",
            "mode": "target_anchored",
            "scope": "title_highlights",
            "facts": [
                {
                    "field": "type",
                    "claim": "Kaffeetasse",
                    "claim_zh": "咖啡杯",
                    "listing_matches": {"Title": "Kaffeetasse"},
                    "translation_matches": {"Title": "咖啡杯"},
                },
                {
                    "field": "material",
                    "claim": "Glas",
                    "claim_zh": "玻璃",
                    "listing_matches": {"Title": "Glas"},
                    "translation_matches": {"Title": "玻璃"},
                },
                {
                    "field": "scope_note",
                    "claim": "Nicht für den gewerblichen Gebrauch",
                    "claim_zh": "不适用于商业用途",
                    "listing_matches": {"Item Highlights": "Nicht für den gewerblichen Gebrauch"},
                    "translation_matches": {"Item Highlights": "不适用于商业用途"},
                },
            ],
            "package_components": [],
            "draft": {
                "title": "Kaffeetasse aus Glas",
                "highlights": "Nicht für den gewerblichen Gebrauch",
                "translations": {"title": "玻璃咖啡杯", "highlights": "不适用于商业用途"},
            },
        }
        de_out = pipeline.run(de_pipeline)
        self.assertTrue(de_out["hard_pass"], de_out["validation"])
        self.assertEqual(de_out["claim_review"]["automatic_exclusions"], [])

        de_commercial = copy.deepcopy(de_pipeline)
        de_commercial["facts"][2].update(
            claim="Gewerbliche Qualität",
            claim_zh="商业品质",
            listing_matches={"Item Highlights": "Gewerbliche Qualität"},
            translation_matches={"Item Highlights": "商业品质"},
        )
        de_commercial["draft"]["highlights"] = "Gewerbliche Qualität"
        de_commercial["draft"]["translations"]["highlights"] = "商业品质"
        de_bad_out = pipeline.run(de_commercial)
        self.assertFalse(de_bad_out["hard_pass"])

    def test_repair_markdown_groups_field_failures_with_dependency_hints(self):
        data = self.compact_cup_input()
        data["draft"]["title"] = "Coffee Cup!"
        data["draft"]["bullets"][0] = "Design: Coffee cup for everyday serving."
        report = pipeline.render_repair_markdown(pipeline.run(data))
        self.assertIn("### Title", report)
        self.assertIn("FAIL TITLE_CHAR", report)
        self.assertIn("### Bullet 1", report)
        self.assertIn("FAIL BULLET_END", report)
        self.assertIn("同步检查：", report)
        self.assertNotIn("### 其他检查", report)

    def test_compatibility_checks_all_location_matches(self):
        data = self.compact_cup_input()
        data["facts"].append({
            "field": "compat",
            "claim": "Fits Auron P58",
            "claim_zh": "适配Auron P58",
            "claim_type": "compatibility",
            "listing_matches": {"Title": "for Auron P58", "Bullet 1": "Auron P58 replacement"},
            "translation_matches": {"Title": "适配Auron P58", "Bullet 1": "Auron P58替代"},
        })
        data["draft"]["title"] = "Coffee Cup for Auron P58"
        data["draft"]["bullets"][0] = "Design: Coffee cup Auron P58 replacement"
        data["draft"]["translations"]["title"] = "适配Auron P58咖啡杯"
        data["draft"]["translations"]["bullets"][0] = "设计：咖啡杯Auron P58替代"
        result = pipeline.run(data)
        self.assertFalse(result["hard_pass"])

    def test_confirmation_preserves_ascii_colon_and_rejects_conflicts(self):
        parsed = claim_review.parse_confirmation_text("A修改：A2 = Capacity: 64 oz")
        self.assertEqual(parsed["modify"]["A2"]["use_text"], "Capacity: 64 oz")
        with self.assertRaises(ValueError):
            claim_review.classify({
                "marketplace": "US",
                "claims": [{"claim": "Glass body", "claim_zh": "玻璃杯身", "locations": ["Bullet 1"]}],
                "confirmation_text": "A删除：A1\nA修改：A1 = Borosilicate glass",
            })
        with self.assertRaises(ValueError):
            claim_review.classify({
                "marketplace": "US",
                "claims": [{"claim": "BPA-free", "claim_zh": "不含BPA", "locations": ["Bullet 1"]}],
                "confirmation_text": "B已核实并启用：B1\nB暂不使用：B1",
            })

    def test_draft_keywords_override_preserves_candidate_variants(self):
        data = self.compact_cup_input()
        data["competitor_bullets"] = [
            ["side handle side handle side handle"],
            ["side handle side handle side handle"],
            ["side handle side handle side handle"],
        ]
        data["keyword_candidates"] = [{"term": "side handle", "variants": ["side handles"]}]
        data["draft"]["bullets"][0] = "Design: Coffee cup with side handles for pouring"
        data["draft"]["keywords"] = [{
            "phrase": "side handle",
            "target": "3",
            "source": "competitor",
            "override_reason": "Bullet 1 covers pouring grip; repeating side handle in material or care adds no buyer value",
        }]
        result = pipeline.run(data)
        self.assertTrue(result["hard_pass"])
        actual_msgs = [
            r["message"] for r in result["validation"] if r["code"] == "KEYWORD_ACTUAL"
        ]
        self.assertTrue(any("count 1" in msg for msg in actual_msgs), actual_msgs)

    def test_title_highlights_rejects_out_of_scope_required_and_competitor_bullets(self):
        base = {
            "schema_version": 2,
            "marketplace": "US",
            "mode": "target_anchored",
            "scope": "title_highlights",
            "facts": [
                {"field": "t1", "claim": "Pipe cleaners", "claim_zh": "扭扭棒", "listing_matches": {"Title": "Pipe Cleaners"}, "translation_matches": {"Title": "扭扭棒"}},
                {"field": "t2", "claim": "For crafts", "claim_zh": "用于手工", "listing_matches": {"Title": "for Crafts"}, "translation_matches": {"Title": "手工"}},
                {"field": "t3", "claim": "Pink", "claim_zh": "粉色", "listing_matches": {"Item Highlights": "Pink"}, "translation_matches": {"Item Highlights": "粉色"}},
            ],
            "package_components": [],
            "draft": {
                "title": "Pipe Cleaners for Crafts",
                "highlights": "Pink",
                "translations": {"title": "手工扭扭棒", "highlights": "粉色"},
            },
        }
        bad_req = copy.deepcopy(base)
        bad_req["priority_keywords"] = [{
            "term": "pipe cleaners",
            "eligible_locations": ["Title"],
            "required_locations": ["Bullet 1"],
        }]
        with self.assertRaises(ValueError):
            pipeline.run(bad_req)
        bad_comp = copy.deepcopy(base)
        bad_comp["competitor_bullets"] = [["Some competitor bullet"]]
        with self.assertRaises(ValueError):
            pipeline.run(bad_comp)

    def test_plan_b_bullet_redline_remains_blocking(self):
        data = self.compact_cup_input()
        data["facts"].append({
            "field": "eco",
            "claim": "Eco-friendly glass",
            "claim_zh": "环保玻璃",
            "seller_confirmed": True,
            "listing_matches": {"Bullet 3": "Eco-friendly glass"},
            "translation_matches": {"Bullet 3": "环保玻璃"},
        })
        data["draft"]["bullets"][2] = "Material: Eco-friendly glass coffee cup 200 °C"
        data["draft"]["translations"]["bullets"][2] = "材质：环保玻璃咖啡杯200°C"
        result = pipeline.run(data)
        self.assertFalse(result["hard_pass"])
        fails = {(r["code"], r["location"]) for r in result["validation"] if r["level"] == "FAIL"}
        self.assertIn(("BULLET_PROHIBITED_CONTENT", "Bullet 3"), fails)
        self.assertIn(("BULLET_PROHIBITED_CHAR", "Bullet 3"), fails)


if __name__ == "__main__":
    unittest.main(verbosity=2)
