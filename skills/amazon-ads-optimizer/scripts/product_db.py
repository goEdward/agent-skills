#!/usr/bin/env python3
"""List, match, and upsert products in the Amazon Ads optimizer database."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from input_values import (
    bounded_identifier_in_text,
    normalize_positive_range,
    normalize_rate,
    parse_economic_range,
    parse_unit_value,
)


SKILL_DIR = Path(__file__).resolve().parents[1]
USER_SECRETS_DB = Path.home() / ".config" / "agent-secrets" / "amazon-ads-optimizer" / "products.json"
LOCAL_DB = SKILL_DIR / "references" / "products.json"


def configure_utf8_output() -> None:
    """Keep Chinese JSON output readable in Windows and POSIX terminals."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8")
            except (OSError, ValueError):
                pass


def default_db() -> Path:
    override = os.environ.get("AMAZON_ADS_PRODUCT_DB")
    if override:
        return Path(override).expanduser()
    if USER_SECRETS_DB.exists() or not LOCAL_DB.exists():
        return USER_SECRETS_DB
    return LOCAL_DB


def validate_db_path(path: Path) -> None:
    if path.suffix.lower() != ".json":
        raise SystemExit(
            f"Invalid product database path {str(path)!r}: the product database must be JSON."
        )


def load_db(path: Path) -> dict[str, Any]:
    validate_db_path(path)
    if not path.exists():
        return {"products": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Invalid product database {path}: expected valid JSON.") from exc
    validate_product_database(data, path)
    return data


def save_db(path: Path, data: dict[str, Any]) -> None:
    validate_db_path(path)
    validate_product_database(data, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        validate_product_database(
            json.loads(tmp_path.read_text(encoding="utf-8")),
            tmp_path,
        )
        tmp_path.replace(path)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_strict_numeric_token(value: str) -> float:
    return parse_unit_value(value, kind="number")[0]


def numeric_values(value: str | None, field_name: str, maximum_items: int = 1) -> list[float]:
    if value is None:
        return []
    text = value.strip()
    if not text:
        raise SystemExit(f"Invalid {field_name}: value cannot be empty.")
    if re.match(r"^\s*-", text):
        raise SystemExit(f"Invalid {field_name}: values cannot be negative.")
    kind = "rate" if field_name in {"target_acos", "baseline_cvr", "gross_margin_range"} else "money"
    try:
        values, _units = parse_economic_range(text, kind=kind)
    except ValueError:
        values = []
    if not values or len(values) > maximum_items:
        expected = "one number" if maximum_items == 1 else "one number or a two-value range"
        raise SystemExit(f"Invalid {field_name} {value!r}: expected {expected}.")
    return values


def validate_rate_argument(value: str | None, field_name: str) -> None:
    if value is None:
        return
    try:
        normalized = normalize_rate(value, field_name=field_name)
    except ValueError as exc:
        raise SystemExit(f"Invalid {field_name} {value!r}: rate must be greater than 0 and at most 100%.") from exc
    if normalized is None or normalized <= 0:
        raise SystemExit(f"Invalid {field_name} {value!r}: rate must be greater than 0 and at most 100%.")


def validate_range_argument(value: str | None, field_name: str, *, as_rate: bool = False) -> None:
    if value is None:
        return
    try:
        normalized = normalize_positive_range(
            value,
            field_name=field_name,
            kind="rate" if as_rate else "money",
            as_rate=as_rate,
        )
    except ValueError as exc:
        raise SystemExit(f"Invalid {field_name} {value!r}: expected positive values in the correct units.") from exc
    if normalized is None:
        raise SystemExit(f"Invalid {field_name} {value!r}: expected positive values in the correct units.")


def product_identifiers(product: dict[str, Any]) -> list[str]:
    values = [
        product.get("key"),
        product.get("asin"),
        product.get("sku"),
        *product.get("aliases", []),
    ]
    return [
        str(value).strip().casefold()
        for value in values
        if value is not None and str(value).strip()
    ]


def validate_unique_product_identifiers(products: list[dict[str, Any]]) -> None:
    owners: dict[str, list[str]] = {}
    for index, product in enumerate(products):
        owner = str(product.get("key") or product.get("asin") or f"product-{index + 1}")
        for identifier in set(product_identifiers(product)):
            owners.setdefault(identifier, []).append(owner)
    collisions = {
        identifier: product_owners
        for identifier, product_owners in owners.items()
        if len(product_owners) > 1
    }
    if collisions:
        raise SystemExit(f"Identifiers must be unique across products: {collisions}")


def validate_product_database(data: Any, path: Path | None = None) -> None:
    location = f" {path}" if path else ""
    if not isinstance(data, dict) or not isinstance(data.get("products"), list):
        raise SystemExit(f"Invalid product database{location}: root object must contain a products array.")
    products = data["products"]
    for index, product in enumerate(products):
        if not isinstance(product, dict):
            raise SystemExit(f"Invalid product database{location}: product {index + 1} must be an object.")
        aliases = product.get("aliases", [])
        if not isinstance(aliases, list):
            raise SystemExit(f"Invalid product database{location}: product {index + 1} aliases must be an array.")
        asin = str(product.get("asin") or "").strip().upper()
        if asin and not re.fullmatch(r"[A-Z0-9]{10}", asin):
            raise SystemExit(
                f"Invalid product database{location}: product {index + 1} has invalid ASIN {product.get('asin')!r}."
            )
        if not product_identifiers(product):
            raise SystemExit(
                f"Invalid product database{location}: product {index + 1} needs a key, ASIN, SKU, or alias."
            )
    validate_unique_product_identifiers(products)


def cmd_list(args: argparse.Namespace) -> int:
    data = load_db(args.db)
    rows = [
        {
            "key": product.get("key"),
            "asin": product.get("asin"),
            "marketplace_default": product.get("marketplace_default"),
            "target_acos": product.get("target_acos"),
            "average_order_value": product.get("average_order_value"),
            "gross_margin_range": product.get("gross_margin_range"),
            "baseline_cvr": product.get("baseline_cvr"),
            "lifecycle_stage": product.get("lifecycle_stage"),
            "title": product.get("title"),
        }
        for product in data.get("products", [])
    ]
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def cmd_match(args: argparse.Namespace) -> int:
    data = load_db(args.db)
    sources = [args.text]
    for file_name in args.files or []:
        path = Path(file_name).expanduser()
        try:
            sources.extend([path.name, path.read_text(encoding="utf-8-sig", errors="ignore")])
        except OSError as exc:
            raise SystemExit(f"Unable to read match input file {path}.") from exc
    haystack = " ".join(filter(None, sources)).lower()
    asins = set(
        re.findall(r"(?<![a-z0-9])b0[a-z0-9]{8}(?![a-z0-9])", haystack, flags=re.I)
    )
    matches = []
    for product in data.get("products", []):
        identifiers = product_identifiers(product)
        asin = str(product.get("asin", "")).lower()
        if any(bounded_identifier_in_text(identifier, haystack) for identifier in identifiers) or (asin and asin in asins):
            matches.append(product)
    print(json.dumps(matches, ensure_ascii=False, indent=2))
    return 0


def cmd_upsert(args: argparse.Namespace) -> int:
    validate_rate_argument(args.target_acos, "target_acos")
    validate_rate_argument(args.baseline_cvr, "baseline_cvr")
    validate_range_argument(args.average_order_value, "average_order_value")
    validate_range_argument(args.gross_margin_range, "gross_margin_range", as_rate=True)

    data = load_db(args.db)
    products = data.setdefault("products", [])
    normalized_asin = args.asin.strip().upper() if args.asin else None
    if normalized_asin and not re.fullmatch(r"[A-Z0-9]{10}", normalized_asin):
        raise SystemExit(f"Invalid ASIN {args.asin!r}: expected exactly 10 letters or digits.")
    normalized_sku = args.sku.strip() if args.sku else None
    normalized_marketplace = args.marketplace.strip().upper() if args.marketplace else None
    if normalized_marketplace and normalized_marketplace not in {"US", "DE"}:
        raise SystemExit(f"Invalid marketplace {args.marketplace!r}: expected US or DE.")
    key = args.key.strip().casefold() if args.key else (normalized_asin.casefold() if normalized_asin else None)
    if not key:
        raise SystemExit("--key or --asin is required for upsert")

    incoming_identifiers = {
        value.casefold()
        for value in [key, normalized_asin, normalized_sku]
        if value
    }
    matches = []
    for product in products:
        if incoming_identifiers & set(product_identifiers(product)):
            matches.append(product)
    if len(matches) > 1:
        raise SystemExit(
            f"Identifiers match multiple existing products: {[product.get('key') for product in matches]}"
        )
    existing = matches[0] if matches else None
    if existing is None:
        existing = {"key": key}
        products.append(existing)
    elif args.key:
        existing["key"] = key

    assignments = {
        "asin": normalized_asin,
        "sku": normalized_sku,
        "marketplace_default": normalized_marketplace,
        "target_acos": args.target_acos,
        "average_order_value": args.average_order_value,
        "gross_margin_range": args.gross_margin_range,
        "baseline_cvr": args.baseline_cvr,
        "lifecycle_stage": args.lifecycle_stage,
        "title": args.title,
        "core_category": args.core_category,
        "target_audience": args.target_audience,
        "description": args.description,
    }
    for field, value in assignments.items():
        if value is not None:
            existing[field] = value
    if args.alias:
        aliases = existing.setdefault("aliases", [])
        normalized_aliases = {str(alias).strip().casefold() for alias in aliases}
        for alias in args.alias:
            normalized_alias = alias.strip().casefold()
            if normalized_alias and normalized_alias not in normalized_aliases:
                aliases.append(alias.strip())
                normalized_aliases.add(normalized_alias)
    if args.compatibility is not None:
        existing["compatibility"] = split_csv(args.compatibility)
    if args.attributes is not None:
        existing["key_attributes"] = split_csv(args.attributes)

    validate_unique_product_identifiers(products)
    save_db(args.db, data)
    print(json.dumps(existing, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    configure_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=default_db())
    subparsers = parser.add_subparsers(required=True)

    list_parser = subparsers.add_parser("list")
    list_parser.set_defaults(func=cmd_list)

    match_parser = subparsers.add_parser("match")
    match_parser.add_argument("--text", default="")
    match_parser.add_argument("files", nargs="*")
    match_parser.set_defaults(func=cmd_match)

    upsert_parser = subparsers.add_parser("upsert")
    upsert_parser.add_argument("--key")
    upsert_parser.add_argument("--asin")
    upsert_parser.add_argument("--sku")
    upsert_parser.add_argument("--alias", action="append")
    upsert_parser.add_argument("--marketplace")
    upsert_parser.add_argument("--target-acos")
    upsert_parser.add_argument("--average-order-value")
    upsert_parser.add_argument("--gross-margin-range", help="Single percentage like 30%% or range like 20-30%%")
    upsert_parser.add_argument("--baseline-cvr", help="Baseline conversion rate from dashboard or historical data")
    upsert_parser.add_argument("--lifecycle-stage", help="Product lifecycle stage, such as launch, growth, mature, or stable")
    upsert_parser.add_argument("--title")
    upsert_parser.add_argument("--core-category")
    upsert_parser.add_argument("--compatibility", help="Comma-separated compatibility values")
    upsert_parser.add_argument("--attributes", help="Comma-separated attribute values")
    upsert_parser.add_argument("--target-audience")
    upsert_parser.add_argument("--description")
    upsert_parser.set_defaults(func=cmd_upsert)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
