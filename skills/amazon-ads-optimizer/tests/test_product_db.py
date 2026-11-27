from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


SKILL_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = SKILL_DIR / "scripts" / "product_db.py"
SPEC = importlib.util.spec_from_file_location("amazon_ads_product_db", MODULE_PATH)
assert SPEC and SPEC.loader
PRODUCT_DB = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PRODUCT_DB)


def upsert_args(db: Path, **overrides):
    values = {
        "db": db,
        "key": None,
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
    values.update(overrides)
    return argparse.Namespace(**values)


class ProductDatabaseTests(unittest.TestCase):
    def test_match_cli_reads_file_contents(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "products.json"
            db_path.write_text(json.dumps({"products": [{
                "key": "item-one", "asin": "B012345678", "title": "Item One",
            }]}), encoding="utf-8")
            input_path = root / "report.txt"
            input_path.write_text("advertised product: B012345678", encoding="utf-8")
            stdout = io.StringIO()
            argv = [
                "product_db", "--db", str(db_path), "match", str(input_path),
            ]
            with patch.object(sys, "argv", argv), redirect_stdout(stdout):
                exit_code = PRODUCT_DB.main()
            self.assertEqual(exit_code, 0)
            matches = json.loads(stdout.getvalue())
            self.assertEqual([item["key"] for item in matches], ["item-one"])

    def test_match_cli_rejects_unreadable_input_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "products.json"
            db_path.write_text(
                json.dumps({"products": [{"key": "item-one", "asin": "B012345678"}]}),
                encoding="utf-8",
            )
            argv = [
                "product_db", "--db", str(db_path), "match", str(root / "missing.txt"),
            ]
            with patch.object(sys, "argv", argv), self.assertRaises(SystemExit):
                PRODUCT_DB.main()

    def test_decimal_comma_rate_is_accepted(self) -> None:
        self.assertEqual(PRODUCT_DB.numeric_values("35,5%", "gross_margin_range"), [35.5])
        PRODUCT_DB.validate_range_argument("35,5%", "gross_margin_range", as_rate=True)

    def test_malformed_numeric_arguments_are_rejected(self) -> None:
        for value in ["30%junk", "12oops", "1,2,3", "1..2"]:
            with self.subTest(value=value), self.assertRaises(SystemExit):
                PRODUCT_DB.numeric_values(value, "target_acos")
        with self.assertRaises(SystemExit):
            PRODUCT_DB.numeric_values("20-30%", "target_acos")
        self.assertEqual(
            PRODUCT_DB.numeric_values("20-30%", "gross_margin_range", maximum_items=2),
            [20.0, 30.0],
        )

    def test_lowercase_asin_updates_existing_uppercase_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "products.json"
            db_path.write_text(
                json.dumps({"products": [{"key": "item-one", "asin": "B012345678", "title": "Old"}]}),
                encoding="utf-8",
            )
            with redirect_stdout(io.StringIO()):
                PRODUCT_DB.cmd_upsert(
                    upsert_args(db_path, asin="b012345678", title="Updated")
                )
            data = json.loads(db_path.read_text(encoding="utf-8"))
            self.assertEqual(len(data["products"]), 1)
            self.assertEqual(data["products"][0]["key"], "item-one")
            self.assertEqual(data["products"][0]["asin"], "B012345678")
            self.assertEqual(data["products"][0]["title"], "Updated")
            self.assertFalse(db_path.with_suffix(".yaml").exists())

    def test_json_database_preserves_multiline_strings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "products.json"
            data = {"products": [{
                "key": "item-one",
                "asin": "B012345678",
                "description": "first line\nsecond line",
            }]}
            PRODUCT_DB.save_db(db_path, data)
            self.assertEqual(
                json.loads(db_path.read_text(encoding="utf-8"))["products"][0]["description"],
                "first line\nsecond line",
            )
            self.assertFalse(db_path.with_suffix(".yaml").exists())

    def test_json_update_does_not_touch_an_unrelated_yaml_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "products.json"
            unrelated_yaml_path = root / "products.yaml"
            unrelated_yaml_path.mkdir()
            with redirect_stdout(io.StringIO()):
                PRODUCT_DB.cmd_upsert(
                    upsert_args(db_path, key="item-one", asin="B012345678")
                )
            data = json.loads(db_path.read_text(encoding="utf-8"))
            self.assertEqual(data["products"][0]["key"], "item-one")
            self.assertTrue(unrelated_yaml_path.is_dir())

    def test_failed_atomic_replace_preserves_json_and_cleans_temp_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "products.json"
            original = {"products": [{"key": "original", "asin": "B012345678"}]}
            replacement = {"products": [{"key": "replacement", "asin": "B087654321"}]}
            db_path.write_text(json.dumps(original), encoding="utf-8")
            with patch.object(Path, "replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    PRODUCT_DB.save_db(db_path, replacement)
            self.assertEqual(
                json.loads(db_path.read_text(encoding="utf-8")),
                original,
            )
            self.assertFalse(db_path.with_suffix(".json.tmp").exists())

    def test_ambiguous_identifier_collision_stops(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "products.json"
            db_path.write_text(
                json.dumps({"products": [
                    {"key": "one", "asin": "B012345678"},
                    {"key": "two", "sku": "sku-2"},
                ]}),
                encoding="utf-8",
            )
            with self.assertRaises(SystemExit):
                PRODUCT_DB.cmd_upsert(
                    upsert_args(db_path, asin="b012345678", sku="SKU-2")
                )

    def test_alias_collision_is_rejected_without_changing_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "products.json"
            original = {"products": [
                {"key": "one", "asin": "B012345678", "aliases": ["shared-alias"]},
                {"key": "two", "asin": "B087654321", "aliases": []},
            ]}
            db_path.write_text(json.dumps(original), encoding="utf-8")
            with self.assertRaises(SystemExit):
                PRODUCT_DB.cmd_upsert(
                    upsert_args(db_path, key="two", alias=["shared-alias"])
                )
            self.assertEqual(json.loads(db_path.read_text(encoding="utf-8")), original)

    def test_zero_target_acos_and_cvr_are_rejected(self) -> None:
        for field in ["target_acos", "baseline_cvr"]:
            with self.subTest(field=field), self.assertRaises(SystemExit):
                PRODUCT_DB.validate_rate_argument("0", field)

    def test_missing_identifiers_are_not_treated_as_literal_none(self) -> None:
        self.assertEqual(PRODUCT_DB.product_identifiers({"key": "one", "sku": None}), ["one"])

    def test_duplicate_identifiers_are_rejected_when_database_is_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "products.json"
            db_path.write_text(json.dumps({"products": [
                {"key": "same", "asin": "B012345678"},
                {"key": "same", "asin": "B012345678"},
            ]}), encoding="utf-8")
            with self.assertRaises(SystemExit):
                PRODUCT_DB.load_db(db_path)

    def test_non_json_database_is_rejected_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "products.yaml"
            with self.assertRaises(SystemExit):
                PRODUCT_DB.save_db(db_path, {"products": [{"key": "one"}]})
            self.assertFalse(db_path.exists())


if __name__ == "__main__":
    unittest.main()
