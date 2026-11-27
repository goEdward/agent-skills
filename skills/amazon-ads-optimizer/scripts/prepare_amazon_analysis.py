#!/usr/bin/env python3
"""Prepare Amazon Ads Excel data for Codex/GPT optimization review."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import os
import re
import sys
import tempfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from input_values import (
    bounded_identifier_in_text,
    is_missing,
    normalize_positive_range,
    normalize_rate,
    parse_unit_value,
)

try:
    from openpyxl import load_workbook
    from openpyxl.utils.exceptions import InvalidFileException
except ModuleNotFoundError:
    load_workbook = None

    class InvalidFileException(Exception):
        """Fallback used only when openpyxl is unavailable."""


class ActionRequired(Exception):
    """A user-correctable input problem that must stop report generation."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class ActionArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ActionRequired("fix_invalid_arguments", message)


SKILL_DIR = Path(__file__).resolve().parents[1]
USER_SECRETS_PRODUCT_DB = Path.home() / ".config" / "agent-secrets" / "amazon-ads-optimizer" / "products.json"
LOCAL_PRODUCT_DB = SKILL_DIR / "references" / "products.json"
DECISION_POLICY_PATH = SKILL_DIR / "references" / "decision_policy.json"
REPORT_RENDERER = SKILL_DIR / "scripts" / "render_amazon_report.py"
DECISION_POLICY = json.loads(DECISION_POLICY_PATH.read_text(encoding="utf-8"))
NEGATIVE_POLICY = DECISION_POLICY["negative"]
ZERO_ORDER_DISCOVERY_POLICY = DECISION_POLICY["zero_order_discovery"]
PLACEMENT_POLICY = DECISION_POLICY["placement"]

REQUIRED_AD_COLUMNS = ["用户搜索词", "广告订单量", "广告花费", "广告点击量"]
CANONICAL_COLUMN_ALIASES = {
    "用户搜索词": [
        "用户搜索词", "Search Term", "Customer Search Term", "Customer search term",
        "Kundensuchbegriff", "Suchbegriff des Kunden",
    ],
    "广告订单量": [
        "广告订单量", "Orders", "7 Day Total Orders (#)", "7 Day Total Orders",
        "14 Day Total Orders (#)", "14 Day Total Orders",
        "Bestellungen", "Bestellungen in 7 Tagen insgesamt (#)",
        "Bestellungen insgesamt in 7 Tagen (#)", "7-Tage-Bestellungen insgesamt (#)",
        "Gesamtzahl der Bestellungen innerhalb von 7 Tagen (#)",
    ],
    "广告花费": ["广告花费", "Spend", "Ad Spend", "Ausgaben", "Werbeausgaben"],
    "广告点击量": ["广告点击量", "Clicks", "Klicks"],
    "广告曝光量": ["广告曝光量", "Impressions", "Impressionen"],
    "广告销售额": [
        "广告销售额", "Sales", "7 Day Total Sales", "14 Day Total Sales",
        "Verkäufe", "Umsatz", "Verkäufe in 7 Tagen insgesamt",
        "Umsatz innerhalb von 7 Tagen",
    ],
    "广告笔单价": ["广告笔单价", "Average Order Value", "Average order value", "Durchschnittlicher Bestellwert"],
    "源竞价": ["源竞价", "Bid", "Keyword Bid", "Targeting Bid", "Gebot", "Keyword-Gebot"],
    "所在广告活动": [
        "所在广告活动", "Campaign", "Campaign Name", "Campaign name",
        "Kampagne", "Kampagnenname",
    ],
    "广告组": [
        "广告组", "广告组名称", "Ad Group", "Ad group", "Ad Group Name",
        "Anzeigengruppe", "Anzeigengruppenname",
    ],
    "匹配类型": ["匹配类型", "Match Type", "Match type", "Übereinstimmungstyp", "Match-Typ"],
    "活动类型": ["活动类型", "Campaign Type", "Campaign type", "Kampagnentyp"],
    "投放": [
        "投放", "投放目标", "Targeting", "Target", "Keyword or Product Targeting",
        "Ausrichtung", "Keyword- oder Produktausrichtung",
    ],
    "日期": ["日期", "Date", "Datum"],
    "广告位": ["广告位", "Placement", "Platzierung"],
    "竞价策略": ["竞价策略", "Bidding Strategy", "Gebotsstrategie"],
    "默认调整": ["默认调整", "Placement Adjustment", "Platzierungsanpassung"],
    "店铺名称": ["店铺名称", "Store", "Shop", "Shopname"],
}
NUMERIC_CANONICAL_COLUMNS = {
    "广告订单量", "广告花费", "广告点击量", "广告曝光量", "广告销售额", "广告笔单价", "源竞价", "默认调整",
}
NUMERIC_COLUMN_KINDS = {
    "广告订单量": "count",
    "广告花费": "money",
    "广告点击量": "count",
    "广告曝光量": "count",
    "广告销售额": "money",
    "广告笔单价": "money",
    "源竞价": "money",
    "默认调整": "rate",
}
_CANONICAL_HEADER_BY_ALIAS = {
    re.sub(r"\s+", " ", str(alias).strip()).casefold(): canonical
    for canonical, aliases in CANONICAL_COLUMN_ALIASES.items()
    for alias in aliases
}
CLICK_ONLY_OUTPUT_CLICK_THRESHOLD = 10
DISCOVERY_EARLY_REVIEW_CLICKS = int(NEGATIVE_POLICY["early_review_clicks"])
DISCOVERY_DIRECT_NEGATIVE_CLICKS = int(NEGATIVE_POLICY["direct_clicks"])
DIRECT_PROFIT_SPEND_MULTIPLE = float(NEGATIVE_POLICY["direct_profit_spend_multiple"])
RELEVANT_QUERY_EXPECTED_ORDERS = float(NEGATIVE_POLICY["relevant_query_expected_orders"])
RELEVANT_QUERY_PROFIT_SPEND_MULTIPLE = float(NEGATIVE_POLICY["relevant_query_profit_spend_multiple"])
ZERO_ORDER_MAX_WITH_CONVERSIONS = int(ZERO_ORDER_DISCOVERY_POLICY["max_with_conversions"])
ZERO_ORDER_MAX_WITHOUT_CONVERSIONS = int(ZERO_ORDER_DISCOVERY_POLICY["max_without_conversions"])
PLACEMENT_DEFAULT_UPLIFT_PCT = int(PLACEMENT_POLICY["default_uplift_pct"])
PLACEMENT_FALLBACK_DIVISOR = float(PLACEMENT_POLICY["fallback_divisor"])
ROOT_NEGATIVE_MIN_TERMS = 3
ROOT_NEGATIVE_MIN_CLICKS = 8
ROOT_NEGATIVE_SINGLE_TOKEN_MIN_TERMS = 4
ROOT_NEGATIVE_SINGLE_TOKEN_MIN_CLICKS = 12
LOCAL_CVR_MIN_CLICKS_FOR_BID = 10
LOCAL_CVR_MIN_ORDERS_FOR_BID = 2
LOW_TRAFFIC_CLICK_THRESHOLD = 2
LOW_TRAFFIC_IMPRESSION_THRESHOLD = 100
HIGH_EXPOSURE_IMPRESSION_THRESHOLD = 500
ACCOUNT_HISTORY_MIN_CLICKS_FOR_STRICT_CPC = 100
ACCOUNT_HISTORY_MIN_ORDERS_FOR_STRICT_CPC = 5
CTR_MIN_IMPRESSIONS_WEAK = 100
CTR_MIN_IMPRESSIONS_USABLE = 500
CTR_BASELINE_MIN_PEER_IMPRESSIONS = 1000
CTR_BASELINE_MIN_PEER_TERMS = 5
ABA_BIG_RANK_THRESHOLD = 10_000
ABA_MID_RANK_THRESHOLD = 100_000
CONFIRMATION_ACOS_LADDER = [0.30, 0.20, 0.15, 0.10]
PERIOD_DIFFERENCE_CURRENCY_TOLERANCE = 0.02
PERIOD_DIFFERENCE_COUNT_TOLERANCE = 1e-6
DAILY_WINDOW_MIN_CLICKS = 50
DAILY_WINDOW_MIN_ORDERS = 3
DAILY_WINDOW_HIGH_CLICK_OVERRIDE = 80
DAILY_WINDOW_MIN_BASELINE_DAYS = 14
DAILY_DATE_COLUMNS = ["日期", "Date", "Datum"]
INTERNAL_DAILY_DATE_KEY = "__amazon_ads_daily_date"
PLACEMENT_DATE_COLUMNS = ["日期", "Date", "Datum"]
PLACEMENT_SHOP_COLUMNS = ["店铺名称", "店铺", "Store", "Shop", "Shopname"]
PLACEMENT_CAMPAIGN_COLUMNS = ["所属广告活动", "所在广告活动", "广告活动", "广告活动名称", "Campaign Name", "Kampagnenname"]
PLACEMENT_NAME_COLUMNS = ["广告位", "广告位置", "投放位置", "Placement", "Platzierung"]
PLACEMENT_STRATEGY_COLUMNS = ["竞价策略", "Bidding Strategy", "Gebotsstrategie"]
PLACEMENT_ADJUSTMENT_COLUMNS = ["默认调整", "广告位溢价", "广告位调整", "Placement Adjustment", "Platzierungsanpassung"]
PLACEMENT_IMPRESSIONS_COLUMNS = ["广告曝光量", "曝光量", "Impressions", "Impressionen"]
PLACEMENT_CLICKS_COLUMNS = ["广告点击量", "点击量", "Clicks", "Klicks"]
PLACEMENT_SPEND_COLUMNS = ["广告花费", "花费", "Spend", "Ausgaben", "Werbeausgaben"]
PLACEMENT_ORDERS_COLUMNS = ["广告订单量", "订单量", "Orders", "Bestellungen"]
PLACEMENT_SALES_COLUMNS = ["广告销售额", "销售额", "Sales", "Verkäufe", "Umsatz"]
AD_GROUP_COLUMNS = ["广告组", "广告组名称", "广告组名", "所在广告组", "Ad Group", "Ad group", "Ad Group Name", "Anzeigengruppe", "Anzeigengruppenname"]
CAMPAIGN_COLUMNS = ["所在广告活动", "Campaign", "Campaign Name", "Campaign name", "Kampagne", "Kampagnenname"]
MATCH_TYPE_COLUMNS = ["匹配类型", "Match Type", "Match type", "Übereinstimmungstyp", "Match-Typ"]
CAMPAIGN_TYPE_COLUMNS = ["活动类型", "Campaign Type", "Campaign type", "Kampagnentyp"]
TARGETING_COLUMNS = [
    "投放",
    "投放目标",
    "关键词或商品投放",
    "Targeting",
    "Target",
    "Keyword or Product Targeting",
    "Ausrichtung",
    "Keyword- oder Produktausrichtung",
]
DATE_COLUMNS = ["日期", "开始日期", "结束日期", "日期/合计", "Date", "Start Date", "End Date", "Datum", "Startdatum", "Enddatum"]
ABA_SEARCH_TERM_COLUMNS = [
    "用户搜索词",
    "Search Term",
    "Customer Search Term",
    "搜索词",
    "关键词",
    "Keyword",
    "Search Query",
    "搜索查询",
]
ABA_RANK_COLUMNS = [
    "ABA排名",
    "ABA搜索词排名",
    "ABA Rank",
    "ABA rank",
    "Search Frequency Rank",
    "Search frequency rank",
    "搜索频率排名",
    "搜索查询排名",
    "搜索排名",
    "排名",
]
PRODUCT_IDENTIFIER_COLUMN_HINTS = [
    "asin",
    "sku",
    "fnsku",
    "msku",
    "product key",
    "产品key",
    "商品编码",
    "商品asin",
    "广告商品asin",
    "父asin",
    "子asin",
]
PRODUCT_CONTEXT_COLUMNS = [
    *CAMPAIGN_COLUMNS,
    *AD_GROUP_COLUMNS,
    "用户搜索词",
    "Search Term",
    "Customer Search Term",
    "搜索词",
    "关键词",
    "Keyword",
]
KNOWN_AD_COLUMNS = {
    "用户搜索词",
    "广告订单量",
    "广告花费",
    "广告点击量",
    "广告转化率",
    "ACoS",
    "CPC",
    *MATCH_TYPE_COLUMNS,
    *CAMPAIGN_COLUMNS,
    *CAMPAIGN_TYPE_COLUMNS,
    *TARGETING_COLUMNS,
    "广告组",
    "广告组名称",
    "广告组名",
    "日期",
    "广告曝光量",
    "广告销售额",
    "广告笔单价",
    "ROAS",
    "用户搜索词翻译",
    *ABA_RANK_COLUMNS,
}

PRODUCT_IMPORTANT_COLUMNS = [
    "日期/合计",
    "销量",
    "销售额",
    "净销售额",
    "订单量",
    "退款量",
    "退款率",
    "退货量",
    "退货率",
    "价格",
    "平均售价",
    "Sessions",
    "PV",
    "BuyBox赢得率",
    "转化率",
    "大类目排名",
    "小类目排名",
    "评分数",
    "星级评分",
    "广告点击量",
    "广告销售额",
    "广告花费",
    "广告订单量",
    "ACoS",
    "广告曝光量",
    "CPC",
    "广告点击率",
    "ROAS",
    "ACoAS",
    "ASoAS",
    "广告订单量占比",
    "广告转化率",
    "本产品广告销售额",
    "本产品广告订单量",
    "总收入",
    "总支出",
    "毛利润",
    "毛利率",
    "ROI",
    "FBA可售",
]

PRODUCT_ADDITIVE_COLUMNS = {
    "销量",
    "销售额",
    "净销售额",
    "订单量",
    "销量-B2B",
    "订单量-B2B",
    "销售额-B2B",
    "退款量",
    "退货量",
    "换货量",
    "测评订单量",
    "测评销售额",
    "促销销量",
    "促销订单量",
    "促销销售额",
    "Sessions",
    "PV",
    "Sessions-B2B",
    "PV-B2B",
    "UnitOrdered-B2B",
    "广告点击量",
    "广告销售额",
    "广告花费",
    "广告订单量",
    "广告曝光量",
    "本产品广告销售额",
    "本产品广告订单量",
    "总收入",
    "总支出",
    "毛利润",
    "累计好评数",
    "累计中查评数",
    "新增好评数",
    "新增中查评数",
}


def configure_utf8_output() -> None:
    """Keep Chinese JSON output readable in Windows and POSIX terminals."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8")
            except (OSError, ValueError):
                pass


def normalize_header(value: Any) -> str:
    return "" if value is None else str(value).strip()


def canonical_header(value: Any) -> str:
    header = normalize_header(value)
    key = re.sub(r"\s+", " ", header).casefold()
    return _CANONICAL_HEADER_BY_ALIAS.get(key, header)


def marketplace_is_de(value: Any) -> bool:
    text = clean_text(value).casefold()
    return text == "de" or text.endswith("-de") or "germany" in text or "deutsch" in text


def currency_code_for_marketplace(value: Any) -> str:
    return "EUR" if marketplace_is_de(value) else "USD"


def currency_symbol_for_marketplace(value: Any) -> str:
    return "€" if currency_code_for_marketplace(value) == "EUR" else "$"


def default_product_db() -> Path:
    override = os.environ.get("AMAZON_ADS_PRODUCT_DB")
    if override:
        return Path(override).expanduser()
    if USER_SECRETS_PRODUCT_DB.exists() or not LOCAL_PRODUCT_DB.exists():
        return USER_SECRETS_PRODUCT_DB
    return LOCAL_PRODUCT_DB


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value).strip()


def first_text(record: dict[str, Any], columns: list[str]) -> str:
    for column in columns:
        value = clean_text(record.get(column))
        if value:
            return value
    return ""


def columns_present(headers: list[str], candidates: list[str]) -> list[str]:
    header_set = set(headers)
    return [column for column in candidates if column in header_set]


def extract_filename_date_range(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    date_pattern = r"20\d{2}[-_]\d{2}[-_]\d{2}"
    match = re.search(rf"({date_pattern}).*?({date_pattern})", path.name)
    if not match:
        return None
    start, end = (item.replace("_", "-") for item in match.groups())
    return {"start": start, "end": end, "source": "filename", "file": path.name}


def parse_date_candidates(value: Any, marketplace: str | None = None) -> list[date]:
    if isinstance(value, datetime):
        return [value.date()]
    if isinstance(value, date):
        return [value]
    text = clean_text(value)
    if not text:
        return []

    parsed: list[date] = []
    seen: set[date] = set()

    def add(candidate: date) -> None:
        if candidate not in seen:
            seen.add(candidate)
            parsed.append(candidate)

    for match in re.findall(r"20\d{2}[-_/]\d{1,2}[-_/]\d{1,2}", text):
        try:
            add(date.fromisoformat(match.replace("_", "-").replace("/", "-")))
        except ValueError:
            pass

    marketplace_text = clean_text(marketplace).lower()
    regional_formats = ["%d.%m.%Y"]
    if marketplace_text == "de" or marketplace_text.endswith("-de"):
        regional_formats.append("%d/%m/%Y")
    else:
        regional_formats.append("%m/%d/%Y")
    for date_format in regional_formats:
        try:
            add(datetime.strptime(text, date_format).date())
        except ValueError:
            pass
    return parsed


def normalized_date_sort_key(value: Any, marketplace: str | None = None) -> str:
    candidates = parse_date_candidates(value, marketplace)
    return max(candidates).isoformat() if candidates else ""


def extract_table_date_range(
    tables: list[dict[str, Any]],
    marketplace: str | None = None,
) -> dict[str, Any] | None:
    observed: list[date] = []
    source_columns: set[str] = set()
    source_sheets: set[str] = set()
    for table in tables:
        present_columns = columns_present(table.get("headers", []), DATE_COLUMNS)
        if not present_columns:
            continue
        for record in table.get("records", []):
            for column in present_columns:
                candidates = parse_date_candidates(record.get(column), marketplace)
                if candidates:
                    observed.extend(candidates)
                    source_columns.add(column)
                    source_sheets.add(clean_text(table.get("sheet")))
    if not observed:
        return None
    return {
        "start": min(observed).isoformat(),
        "end": max(observed).isoformat(),
        "source": "data_columns",
        "columns": sorted(source_columns),
        "sheets": sorted(sheet for sheet in source_sheets if sheet),
    }


def resolve_date_range(
    path: Path | None,
    tables: list[dict[str, Any]],
    marketplace: str | None = None,
) -> dict[str, Any] | None:
    table_range = extract_table_date_range(tables, marketplace)
    if table_range:
        table_range["file"] = path.name if path else None
        return table_range
    return extract_filename_date_range(path)


def period_overlap_context(
    baseline_range: dict[str, Any] | None,
    recent_range: dict[str, Any] | None,
) -> dict[str, Any]:
    """Detect overlap for reporting only; never rewrite or subtract source data."""
    if not baseline_range or not recent_range:
        return {
            "status": "unknown",
            "warning": None,
            "note": "Date overlap could not be checked because one or both filename date ranges are unavailable.",
        }
    try:
        baseline_start = date.fromisoformat(str(baseline_range["start"]))
        baseline_end = date.fromisoformat(str(baseline_range["end"]))
        recent_start = date.fromisoformat(str(recent_range["start"]))
        recent_end = date.fromisoformat(str(recent_range["end"]))
    except (KeyError, TypeError, ValueError):
        return {
            "status": "unknown",
            "warning": None,
            "note": "Date overlap could not be checked because a filename date range is invalid.",
        }
    if baseline_start > baseline_end or recent_start > recent_end:
        return {
            "status": "unknown",
            "warning": None,
            "note": "Date overlap could not be checked because a filename date range is reversed.",
        }

    overlap_start = max(baseline_start, recent_start)
    overlap_end = min(baseline_end, recent_end)
    if overlap_start <= overlap_end:
        overlap_days = (overlap_end - overlap_start).days + 1
        return {
            "status": "overlap",
            "overlap_start": overlap_start.isoformat(),
            "overlap_end": overlap_end.isoformat(),
            "overlap_days": overlap_days,
            "warning": (
                "Baseline and recent periods overlap. The script has not altered or subtracted source data; "
                "treat period comparisons as contextual only and do not use them as the sole basis for "
                "negative or aggressive bid actions."
            ),
        }
    return {
        "status": "non_overlapping",
        "warning": None,
        "note": "Baseline and recent filename date ranges do not overlap.",
    }


def attach_period_overlap_warning(
    period_comparison: dict[str, Any],
    baseline_range: dict[str, Any] | None,
    recent_range: dict[str, Any] | None,
) -> dict[str, Any]:
    check = period_overlap_context(baseline_range, recent_range)
    period_comparison["date_overlap_check"] = check
    if check.get("status") == "overlap":
        period_comparison["warning"] = check.get("warning")
        period_comparison["decision_rule"] = (
            f"{period_comparison.get('decision_rule', '')} "
            "Because the supplied periods overlap, use this comparison as contextual evidence only; "
            "do not make negatives or aggressive bid changes from period labels alone."
        ).strip()
    return period_comparison


def daily_report_context(
    path: Path,
    tables: list[dict[str, Any]],
    marketplace: str | None = None,
) -> dict[str, Any]:
    """Validate row-level daily granularity and cache each parsed date on the in-memory record."""
    ad_tables = [
        table
        for table in tables
        if all(column in table.get("headers", []) for column in REQUIRED_AD_COLUMNS)
    ]
    if not ad_tables:
        return {"status": "not_daily", "reason": "required_ad_columns_missing"}

    filename_hint = "每日" in path.name or "daily" in path.name.lower()
    observed_dates: list[date] = []
    rows_checked = 0
    for table in ad_tables:
        date_columns = columns_present(table.get("headers", []), DAILY_DATE_COLUMNS)
        if not date_columns:
            return {"status": "not_daily", "reason": "row_level_daily_date_column_missing"}
        for record in table.get("records", []):
            if not clean_text(record.get("用户搜索词")):
                continue
            candidates: list[date] = []
            for column in date_columns:
                parsed = parse_date_candidates(record.get(column), marketplace)
                if parsed:
                    candidates = parsed
                    break
            if len(candidates) != 1:
                return {
                    "status": "not_daily",
                    "reason": "daily_row_date_missing_or_ambiguous",
                    "sheet": table.get("sheet"),
                    "row_date_value": first_text(record, date_columns),
                }
            record[INTERNAL_DAILY_DATE_KEY] = candidates[0].isoformat()
            observed_dates.append(candidates[0])
            rows_checked += 1

    distinct_dates = sorted(set(observed_dates))
    if rows_checked == 0 or len(distinct_dates) < 2:
        return {"status": "not_daily", "reason": "insufficient_distinct_daily_dates"}
    if not filename_hint and len(distinct_dates) < 7:
        return {
            "status": "not_daily",
            "reason": "daily_granularity_not_confident",
            "distinct_dates": len(distinct_dates),
        }

    observed_start = distinct_dates[0]
    observed_end = distinct_dates[-1]
    filename_range = extract_filename_date_range(path)
    coverage_start = observed_start
    coverage_end = observed_end
    coverage_source = "observed_daily_rows"
    if filename_range:
        try:
            filename_start = date.fromisoformat(str(filename_range["start"]))
            filename_end = date.fromisoformat(str(filename_range["end"]))
        except (KeyError, TypeError, ValueError):
            filename_start = observed_start
            filename_end = observed_end
        if filename_start <= observed_start and filename_end >= observed_end:
            coverage_start = filename_start
            coverage_end = filename_end
            coverage_source = "filename_confirmed_daily_coverage"

    source_coverage_end = coverage_end
    excluded_partial_date = None
    if coverage_end >= date.today():
        excluded_partial_date = coverage_end.isoformat()
        coverage_end = date.today() - timedelta(days=1)
    if coverage_end < coverage_start:
        return {"status": "not_daily", "reason": "no_completed_daily_period_available"}
    total_days = (coverage_end - coverage_start).days + 1
    return {
        "status": "daily",
        "rows_checked": rows_checked,
        "distinct_active_dates": len(distinct_dates),
        "observed_start": observed_start.isoformat(),
        "observed_end": observed_end.isoformat(),
        "coverage_start": coverage_start.isoformat(),
        "coverage_end": coverage_end.isoformat(),
        "source_coverage_end": source_coverage_end.isoformat(),
        "excluded_partial_date": excluded_partial_date,
        "coverage_days": total_days,
        "coverage_source": coverage_source,
        "calendar_days_without_rows": max(0, total_days - len(distinct_dates)),
        "filename_daily_hint": filename_hint,
    }


def filter_daily_tables(
    tables: list[dict[str, Any]],
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for table in tables:
        records = []
        for record in table.get("records", []):
            raw_date = record.get(INTERNAL_DAILY_DATE_KEY)
            if not raw_date:
                continue
            try:
                record_date = date.fromisoformat(str(raw_date))
            except ValueError:
                continue
            if start <= record_date <= end:
                records.append(record)
        output.append(
            {
                **table,
                "records": records,
            }
        )
    return output


def daily_window_sufficiency(ad_data: dict[str, Any]) -> dict[str, Any]:
    metrics = account_metrics(ad_data)
    clicks = metric_float(metrics.get("clicks"))
    orders = metric_float(metrics.get("orders"))
    sufficient = clicks >= DAILY_WINDOW_HIGH_CLICK_OVERRIDE or (
        clicks >= DAILY_WINDOW_MIN_CLICKS and orders >= DAILY_WINDOW_MIN_ORDERS
    )
    reason = (
        "high_click_override"
        if clicks >= DAILY_WINDOW_HIGH_CLICK_OVERRIDE
        else "click_and_order_threshold_met"
        if sufficient
        else "insufficient_recent_signal"
    )
    return {
        "sufficient": sufficient,
        "reason": reason,
        "clicks": maybe_int(clicks),
        "orders": maybe_int(orders),
        "spend": metrics.get("spend"),
        "sales": metrics.get("ad_sales"),
        "thresholds": {
            "clicks": DAILY_WINDOW_MIN_CLICKS,
            "orders": DAILY_WINDOW_MIN_ORDERS,
            "high_click_override": DAILY_WINDOW_HIGH_CLICK_OVERRIDE,
        },
    }


def _daily_range(start: date, end: date, role: str) -> dict[str, Any]:
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "source": "single_daily_report_window",
        "role": role,
        "days": (end - start).days + 1,
    }


def select_daily_analysis_windows(
    tables: list[dict[str, Any]],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Choose a stable decision window plus the shortest sufficient signal/validation windows."""
    if context.get("status") != "daily":
        return {"status": "not_applicable", "daily_report_context": context}
    coverage_start = date.fromisoformat(str(context["coverage_start"]))
    coverage_end = date.fromisoformat(str(context["coverage_end"]))
    coverage_days = (coverage_end - coverage_start).days + 1
    aggregate_cache: dict[tuple[date, date], tuple[list[dict[str, Any]], dict[str, Any]]] = {}

    def aggregate_window(start: date, end: date) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        key = (start, end)
        if key not in aggregate_cache:
            window_tables = filter_daily_tables(tables, start, end)
            aggregate_cache[key] = (window_tables, aggregate_ad_data(window_tables))
        return aggregate_cache[key]

    signal_evaluations: list[dict[str, Any]] = []
    signal_window: dict[str, Any] | None = None
    validation_window: dict[str, Any] | None = None
    signal_pairs = [(1, 1), (3, 3), (7, 7), (7, 14), (7, 30), (14, 14), (14, 30), (30, 30)]
    for recent_days, baseline_days in signal_pairs:
        if coverage_days < recent_days + baseline_days:
            continue
        recent_start = coverage_end - timedelta(days=recent_days - 1)
        baseline_end = recent_start - timedelta(days=1)
        baseline_start = baseline_end - timedelta(days=baseline_days - 1)
        recent_tables, recent_data = aggregate_window(recent_start, coverage_end)
        baseline_tables, baseline_data = aggregate_window(baseline_start, baseline_end)
        recent_sufficiency = daily_window_sufficiency(recent_data)
        baseline_sufficiency = daily_window_sufficiency(baseline_data)
        evaluation = {
            "recent_days": recent_days,
            "baseline_days": baseline_days,
            "recent_range": _daily_range(recent_start, coverage_end, "recent"),
            "baseline_range": _daily_range(baseline_start, baseline_end, "baseline"),
            "recent_sufficiency": recent_sufficiency,
            "baseline_sufficiency": baseline_sufficiency,
            "both_sufficient": bool(recent_sufficiency["sufficient"] and baseline_sufficiency["sufficient"]),
        }
        signal_evaluations.append(evaluation)
        if signal_window is None and evaluation["both_sufficient"]:
            signal_window = evaluation
        elif signal_window is not None and validation_window is None:
            validation_window = evaluation
            break

    decision_evaluations: list[dict[str, Any]] = []
    for recent_days in [7, 14]:
        if coverage_days < recent_days + 1:
            continue
        recent_start = coverage_end - timedelta(days=recent_days - 1)
        available_baseline_days = (recent_start - coverage_start).days
        recent_tables, recent_data = aggregate_window(recent_start, coverage_end)
        sufficiency = daily_window_sufficiency(recent_data)
        decision_evaluations.append(
            {
                "recent_days": recent_days,
                "recent_range": _daily_range(recent_start, coverage_end, "recent"),
                "sufficiency": sufficiency,
            }
        )
        if not sufficiency["sufficient"] or available_baseline_days < 7:
            continue
        baseline_days = min(30, available_baseline_days)
        baseline_end = recent_start - timedelta(days=1)
        baseline_start = baseline_end - timedelta(days=baseline_days - 1)
        baseline_tables, baseline_data = aggregate_window(baseline_start, baseline_end)
        return {
            "status": "comparison",
            "selection_reason": f"recent_{recent_days}_sufficient",
            "recent_days": recent_days,
            "baseline_days": baseline_days,
            "recent_range": _daily_range(recent_start, coverage_end, "recent"),
            "baseline_range": _daily_range(baseline_start, baseline_end, "baseline"),
            "recent_tables": recent_tables,
            "baseline_tables": baseline_tables,
            "recent_data": recent_data,
            "baseline_data": baseline_data,
            "full_history_data": aggregate_ad_data(tables),
            "evaluations": decision_evaluations,
            "signal_window": signal_window,
            "validation_window": validation_window,
            "signal_evaluations": signal_evaluations,
            "daily_report_context": context,
        }

    if coverage_days >= 60:
        recent_start = coverage_end - timedelta(days=29)
        baseline_end = recent_start - timedelta(days=1)
        baseline_start = baseline_end - timedelta(days=29)
        recent_tables, recent_data = aggregate_window(recent_start, coverage_end)
        baseline_tables, baseline_data = aggregate_window(baseline_start, baseline_end)
        return {
            "status": "comparison",
            "selection_reason": "recent_7_and_14_insufficient_use_30_vs_30",
            "recent_days": 30,
            "baseline_days": 30,
            "recent_range": _daily_range(recent_start, coverage_end, "recent"),
            "baseline_range": _daily_range(baseline_start, baseline_end, "baseline"),
            "recent_tables": recent_tables,
            "baseline_tables": baseline_tables,
            "recent_data": recent_data,
            "baseline_data": baseline_data,
            "full_history_data": aggregate_ad_data(tables),
            "evaluations": decision_evaluations,
            "signal_window": signal_window,
            "validation_window": validation_window,
            "signal_evaluations": signal_evaluations,
            "daily_report_context": context,
        }

    if signal_window:
        recent_range = signal_window["recent_range"]
        baseline_range = signal_window["baseline_range"]
        recent_start = date.fromisoformat(str(recent_range["start"]))
        baseline_start = date.fromisoformat(str(baseline_range["start"]))
        baseline_end = date.fromisoformat(str(baseline_range["end"]))
        recent_tables, recent_data = aggregate_window(recent_start, coverage_end)
        baseline_tables, baseline_data = aggregate_window(baseline_start, baseline_end)
        return {
            "status": "comparison",
            "selection_reason": f"short_signal_{signal_window['recent_days']}_vs_{signal_window['baseline_days']}",
            "recent_days": signal_window["recent_days"],
            "baseline_days": signal_window["baseline_days"],
            "recent_range": recent_range,
            "baseline_range": baseline_range,
            "recent_tables": recent_tables,
            "baseline_tables": baseline_tables,
            "recent_data": recent_data,
            "baseline_data": baseline_data,
            "full_history_data": aggregate_ad_data(tables),
            "evaluations": decision_evaluations,
            "signal_window": signal_window,
            "validation_window": validation_window,
            "signal_evaluations": signal_evaluations,
            "daily_report_context": context,
        }

    single_days = min(30, coverage_days)
    single_start = coverage_end - timedelta(days=single_days - 1)
    recent_tables = filter_daily_tables(tables, single_start, coverage_end)
    return {
        "status": "single_period",
        "selection_reason": "insufficient_signal_or_history_for_independent_comparison",
        "recent_days": single_days,
        "baseline_days": 0,
        "recent_range": _daily_range(single_start, coverage_end, "single_period"),
        "baseline_range": None,
        "recent_tables": recent_tables,
        "baseline_tables": [],
        "recent_data": aggregate_ad_data(recent_tables),
        "baseline_data": None,
        "full_history_data": aggregate_ad_data(tables),
        "evaluations": decision_evaluations,
        "signal_window": signal_window,
        "validation_window": validation_window,
        "signal_evaluations": signal_evaluations,
        "daily_report_context": context,
    }


def build_daily_term_action_evidence(
    tables: list[dict[str, Any]],
    context: dict[str, Any],
    cvr_baseline: dict[str, Any],
    profit_context: dict[str, Any],
    target_acos: float | None,
) -> dict[str, dict[str, Any]]:
    """Build action-specific evidence once from daily buckets; never use a global click gate."""
    if context.get("status") != "daily":
        return {}
    coverage_start = date.fromisoformat(str(context["coverage_start"]))
    coverage_end = date.fromisoformat(str(context["coverage_end"]))
    by_term_date: dict[str, dict[date, dict[str, Any]]] = defaultdict(
        lambda: defaultdict(
            lambda: {
                "orders": 0.0,
                "spend": 0.0,
                "clicks": 0.0,
                "impressions": 0.0,
                "ad_sales": 0.0,
                "sales_available": False,
            }
        )
    )
    for table in tables:
        if not all(column in table.get("headers", []) for column in REQUIRED_AD_COLUMNS):
            continue
        for record in table.get("records", []):
            term = clean_text(record.get("用户搜索词"))
            raw_date = record.get(INTERNAL_DAILY_DATE_KEY)
            if not term or not raw_date:
                continue
            try:
                record_date = date.fromisoformat(str(raw_date))
            except ValueError:
                continue
            bucket = by_term_date[term][record_date]
            bucket["orders"] += parse_number(record.get("广告订单量"))
            bucket["spend"] += parse_number(record.get("广告花费"))
            bucket["clicks"] += parse_number(record.get("广告点击量"))
            bucket["impressions"] += parse_number(record.get("广告曝光量"))
            sales = parse_optional_number(record.get("广告销售额"))
            if sales is not None:
                bucket["ad_sales"] += sales
                bucket["sales_available"] = True

    baseline_cvr = parse_optional_number(cvr_baseline.get("value"))
    baseline_confidence = str(cvr_baseline.get("confidence") or "low")
    allowable_spend = parse_optional_number(profit_context.get("estimated_gross_profit_per_order"))
    profit_cap = None
    margin = profit_context.get("gross_margin_range") or {}
    margin_midpoint = parse_optional_number(margin.get("midpoint"))
    if target_acos is not None and margin_midpoint is not None:
        profit_cap = min(target_acos, margin_midpoint)
    elif target_acos is not None:
        profit_cap = target_acos
    elif margin_midpoint is not None:
        profit_cap = margin_midpoint

    def aggregate_days(daily: dict[date, dict[str, Any]], start: date, end: date) -> dict[str, Any]:
        orders = spend = clicks = impressions = sales = 0.0
        sales_available = False
        for day, row in daily.items():
            if start <= day <= end:
                orders += metric_float(row.get("orders"))
                spend += metric_float(row.get("spend"))
                clicks += metric_float(row.get("clicks"))
                impressions += metric_float(row.get("impressions"))
                if row.get("sales_available"):
                    sales += metric_float(row.get("ad_sales"))
                    sales_available = True
        ad_sales = sales if sales_available else None
        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "days": (end - start).days + 1,
            "orders": maybe_int(orders),
            "spend": round(spend, 4),
            "clicks": maybe_int(clicks),
            "impressions": maybe_int(impressions),
            "ad_sales": round(ad_sales, 4) if ad_sales is not None else None,
            "cpc": safe_ratio(spend, clicks),
            "cvr": safe_ratio(orders, clicks),
            "acos": safe_ratio(spend, ad_sales),
        }

    evidence_by_term: dict[str, dict[str, Any]] = {}
    for term, daily in by_term_date.items():
        horizons: list[dict[str, Any]] = []
        for days in [1, 2, 3, 7, 14, 30]:
            start = coverage_end - timedelta(days=days - 1)
            if start < coverage_start:
                continue
            horizons.append(aggregate_days(daily, start, coverage_end))

        consecutive_days = 0
        day_cursor = coverage_end
        while day_cursor >= coverage_start:
            row = daily.get(day_cursor)
            if not row or metric_float(row.get("clicks")) <= 0 or metric_float(row.get("orders")) > 0:
                break
            consecutive_days += 1
            day_cursor -= timedelta(days=1)
        consecutive_metrics = (
            aggregate_days(daily, coverage_end - timedelta(days=consecutive_days - 1), coverage_end)
            if consecutive_days
            else None
        )

        scale_signal = None
        for row in horizons:
            orders = metric_float(row.get("orders"))
            acos = row.get("acos")
            if orders < 3 or acos is None or profit_cap is None or metric_float(acos) > profit_cap:
                continue
            longer_profitable = any(
                metric_float(other.get("days")) > metric_float(row.get("days"))
                and metric_float(other.get("orders")) >= 3
                and other.get("acos") is not None
                and metric_float(other.get("acos")) <= profit_cap
                for other in horizons
            )
            if orders >= 10:
                bid_change = "+10%到+15%" if longer_profitable else "+5%到+10%"
            elif orders >= 5:
                bid_change = "+5%到+10%" if longer_profitable else "+5%"
            else:
                bid_change = "+5%"
            scale_signal = {
                **row,
                "signal": "profitable_scale",
                "profit_acos_cap": profit_cap,
                "longer_window_consistent": longer_profitable,
                "recommended_action": "increase_test",
                "suggested_bid_change": bid_change,
                "confidence": "high" if longer_profitable and orders >= 5 else "medium",
                "reason": "daily_profitable_multi_order_scale_signal",
            }
            break

        zero_order_signal = None
        if consecutive_metrics:
            clicks = metric_float(consecutive_metrics.get("clicks"))
            spend = metric_float(consecutive_metrics.get("spend"))
            zero_probability = None
            expected_orders = None
            if baseline_cvr is not None and 0 < baseline_cvr < 1 and baseline_confidence in {"medium", "high"}:
                expected_orders = clicks * baseline_cvr
                zero_probability = (1 - baseline_cvr) ** clicks
            spend_multiple = safe_ratio(spend, allowable_spend) if allowable_spend else None
            longer_zero_consistent = any(
                metric_float(row.get("days")) > consecutive_days
                and metric_float(row.get("clicks")) > 0
                and metric_float(row.get("orders")) == 0
                for row in horizons
            )
            strong_probability = zero_probability is not None and zero_probability <= 0.10
            strong_economics = spend_multiple is not None and spend_multiple >= 1
            if strong_probability or strong_economics or clicks > CLICK_ONLY_OUTPUT_CLICK_THRESHOLD:
                if spend_multiple is not None and spend_multiple > 3:
                    bid_change = "-20%到-30%或暂停弱范围"
                elif spend_multiple is not None and spend_multiple > 2:
                    bid_change = "-15%到-20%"
                elif spend_multiple is not None and spend_multiple > 1:
                    bid_change = "-10%到-15%"
                elif zero_probability is not None and zero_probability <= 0.01:
                    bid_change = "-5%到-10%"
                else:
                    bid_change = "保持或-5%"
                high_confidence = bool(
                    (zero_probability is not None and zero_probability <= 0.01)
                    or (spend_multiple is not None and spend_multiple > 2)
                    or (consecutive_days >= 3 and longer_zero_consistent)
                )
                zero_order_signal = {
                    **consecutive_metrics,
                    "signal": "persistent_zero_order",
                    "consecutive_days": consecutive_days,
                    "baseline_cvr": baseline_cvr,
                    "baseline_cvr_confidence": baseline_confidence,
                    "expected_orders": expected_orders,
                    "zero_order_probability": zero_probability,
                    "allowable_spend_per_order": allowable_spend,
                    "spend_multiple": spend_multiple,
                    "longer_window_consistent": longer_zero_consistent,
                    "recommended_action": "diagnose/controlled_bid_down",
                    "suggested_bid_change": bid_change,
                    "confidence": "high" if high_confidence else "medium",
                    "reason": "persistent_zero_order_probability_and_economics",
                    "negative_requires_semantic_mismatch": True,
                }

        if scale_signal or zero_order_signal:
            evidence_by_term[term] = {
                "scale_signal": scale_signal,
                "zero_order_signal": zero_order_signal,
                "rolling_windows": horizons,
                "decision_rule": "Orders/economics decide scale; zero probability/spend/persistence decide control; semantic facts decide negatives.",
            }
    return evidence_by_term


def enrich_ad_data_with_daily_action_evidence(
    ad_data: dict[str, Any] | None,
    evidence_by_term: dict[str, dict[str, Any]],
) -> None:
    if not ad_data or ad_data.get("status") != "ok" or not evidence_by_term:
        return
    for term in ad_data.get("all_terms", []):
        evidence = evidence_by_term.get(clean_text(term.get("search_term")))
        if evidence:
            term["daily_action_evidence"] = evidence
    ad_data["top_converting_terms"] = [
        term for term in ad_data.get("all_terms", []) if term.get("classification") == "converting"
    ][:50]
    ad_data["top_click_only_terms"] = [
        term for term in ad_data.get("all_terms", []) if term.get("classification") == "click_only"
    ][:50]


def _parse_localized_numeric_token(token: str, marketplace: Any = None) -> float | None:
    token = token.strip().replace("\u00a0", "").replace(" ", "").replace("'", "")
    token = token.rstrip(".,")
    if not token or not re.search(r"\d", token):
        return None
    negative = token.startswith("-")
    token = token.lstrip("+-")
    comma_count = token.count(",")
    dot_count = token.count(".")
    if comma_count and dot_count:
        decimal_separator = "," if token.rfind(",") > token.rfind(".") else "."
        thousands_separator = "." if decimal_separator == "," else ","
        token = token.replace(thousands_separator, "").replace(decimal_separator, ".")
    elif comma_count:
        comma_thousands = bool(re.fullmatch(r"\d{1,3}(?:,\d{3})+", token))
        if marketplace_is_de(marketplace) or not comma_thousands:
            if comma_count > 1:
                parts = token.split(",")
                token = "".join(parts[:-1]) + "." + parts[-1]
            else:
                token = token.replace(",", ".")
        else:
            token = token.replace(",", "")
    elif dot_count and marketplace_is_de(marketplace):
        if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", token):
            token = token.replace(".", "")
    try:
        number = float(token)
    except ValueError:
        return None
    return -number if negative else number


def _parse_strict_localized_numeric_token(token: str, marketplace: Any = None) -> float | None:
    """Parse one complete localized numeric token without accepting a valid prefix."""
    token = token.strip().replace("\u00a0", "").replace(" ", "").replace("'", "")
    if not re.fullmatch(r"[+-]?\d[\d.,]*", token):
        return None
    negative = token.startswith("-")
    token = token.lstrip("+-")
    comma_count = token.count(",")
    dot_count = token.count(".")

    if comma_count and dot_count:
        decimal_separator = "," if token.rfind(",") > token.rfind(".") else "."
        thousands_separator = "." if decimal_separator == "," else ","
        integer_part, fractional_part = token.rsplit(decimal_separator, 1)
        if not fractional_part.isdigit() or decimal_separator in integer_part:
            return None
        if thousands_separator in integer_part and not re.fullmatch(
            rf"\d{{1,3}}(?:{re.escape(thousands_separator)}\d{{3}})+",
            integer_part,
        ):
            return None
        token = integer_part.replace(thousands_separator, "") + "." + fractional_part
    elif comma_count:
        comma_thousands = bool(re.fullmatch(r"\d{1,3}(?:,\d{3})+", token))
        if comma_count > 1:
            if not comma_thousands:
                return None
            token = token.replace(",", "")
        elif comma_thousands and not marketplace_is_de(marketplace):
            token = token.replace(",", "")
        else:
            token = token.replace(",", ".")
    elif dot_count:
        dot_thousands = bool(re.fullmatch(r"\d{1,3}(?:\.\d{3})+", token))
        if dot_count > 1:
            if not dot_thousands:
                return None
            token = token.replace(".", "")
        elif dot_thousands and marketplace_is_de(marketplace):
            token = token.replace(".", "")

    try:
        number = float(token)
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    return -number if negative else number


def parse_strict_localized_number(value: Any, marketplace: Any = None) -> float | None:
    """Parse one complete scalar, allowing only currency affixes and one trailing percent sign."""
    if is_missing(value):
        return None
    try:
        number, _explicit_percent = parse_unit_value(value, kind="number", marketplace=marketplace)
    except ValueError:
        raise ValueError(f"Invalid numeric value {value!r}.")
    return number


def strict_localized_range(value: Any, marketplace: Any = None) -> list[float]:
    """Parse one scalar or a complete two-value range."""
    try:
        number = parse_strict_localized_number(value, marketplace)
    except ValueError:
        number = None
    if number is not None:
        return [number]
    if not isinstance(value, str):
        raise ValueError(f"Invalid numeric range {value!r}.")
    parts = re.split(r"\s*(?:-|–|—|~|至|to)\s*", value.strip(), flags=re.I)
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"Invalid numeric range {value!r}.")
    numbers = [parse_strict_localized_number(part, marketplace) for part in parts]
    if any(number is None for number in numbers):
        raise ValueError(f"Invalid numeric range {value!r}.")
    return [float(number) for number in numbers if number is not None]


def localized_numbers(value: Any, marketplace: Any = None) -> list[float]:
    if value is None:
        return []
    if isinstance(value, bool):
        return [float(value)]
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return []
        return [float(value)]
    text = str(value).strip()
    if not text or text in {"-", "--", "N/A", "NA", "None", "null"}:
        return []
    text = re.sub(r"(?i)(US\$|USD|EUR|€|\$|￥|¥|%)", "", text)
    leading_negative = bool(re.match(r"^\s*-", text))
    output: list[float] = []
    for index, token in enumerate(re.findall(r"\d[\d\s.,']*", text)):
        number = _parse_localized_numeric_token(token, marketplace)
        if number is not None:
            if index == 0 and leading_negative:
                number = -number
            output.append(number)
    return output


def parse_localized_number(value: Any, marketplace: Any = None) -> float | None:
    numbers = localized_numbers(value, marketplace)
    return numbers[0] if numbers else None


def parse_number(value: Any) -> float:
    number = parse_localized_number(value)
    return number if number is not None else 0.0


def parse_optional_number(value: Any) -> float | None:
    return parse_localized_number(value)


def parse_rate(value: Any, field_name: str = "rate") -> float | None:
    """Normalize decimal and percentage-style rates to the inclusive 0..1 scale."""
    try:
        number = normalize_rate(value, field_name=field_name)
    except ValueError:
        raise ValueError(
            f"Invalid {field_name} value {value!r}; use a decimal such as 0.15 or a percentage such as 15%."
        )
    return number


def parse_number_midpoint(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return 0.0
        return float(value)
    text = str(value).strip()
    if not text or text in {"-", "--", "N/A", "NA", "None", "null"}:
        return 0.0
    numbers = localized_numbers(text)
    if not numbers:
        return 0.0
    if len(numbers) == 1:
        return numbers[0]
    return sum(numbers[:2]) / 2


def parse_positive_midpoint(value: Any, field_name: str) -> float | None:
    try:
        numbers = normalize_positive_range(value, field_name=field_name, kind="money")
    except ValueError:
        raise ValueError(f"Invalid {field_name} value {value!r}; use one positive number or a two-value range.")
    if numbers is None:
        return None
    return numbers[0] if len(numbers) == 1 else sum(numbers) / 2


def maybe_int(value: float) -> int | float:
    return int(value) if abs(value - int(value)) < 1e-9 else round(value, 4)


def parse_margin_range(value: Any) -> dict[str, Any] | None:
    try:
        numbers = normalize_positive_range(
            value,
            field_name="gross_margin_range",
            kind="rate",
            as_rate=True,
        )
    except ValueError:
        raise ValueError(
            f"Invalid gross_margin_range value {value!r}; use a decimal, percentage, or two-value range."
        )
    if numbers is None:
        return None
    text = str(value).strip()
    if len(numbers) == 1:
        minimum = maximum = numbers[0]
    else:
        minimum, maximum = numbers
    return {
        "raw": text,
        "min": minimum,
        "max": maximum,
        "midpoint": (minimum + maximum) / 2,
    }


def profitability_context(product: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    aov_source = args.average_order_value if args.average_order_value is not None else product.get("average_order_value")
    margin_source = args.gross_margin_range if args.gross_margin_range is not None else product.get("gross_margin_range")
    aov = parse_positive_midpoint(aov_source, "average_order_value")
    margin = parse_margin_range(margin_source)
    gross_profit = None
    thresholds = None
    if aov and margin and margin.get("midpoint") is not None:
        gross_profit = aov * margin["midpoint"]
        thresholds = {
            "watch_spend": round(gross_profit * 0.3, 4),
            "strong_action_spend": round(gross_profit * 0.6, 4),
            "critical_waste_spend": round(gross_profit, 4),
        }
    return {
        "average_order_value": aov,
        "gross_margin_range": margin,
        "estimated_gross_profit_per_order": round(gross_profit, 4) if gross_profit is not None else None,
        "zero_order_spend_thresholds": thresholds,
        "note": "If AOV or gross margin is missing, spend tolerance is provisional and should use relative spend/click ranking.",
    }


def cpc_at_acos(aov: float | None, cvr: float | None, acos: float | None) -> float | None:
    if not aov or not cvr or not acos:
        return None
    return aov * cvr * acos


def product_acos_cpc_ladder(
    profit_context: dict[str, Any],
    target_acos: float | None,
    cvr_baseline: dict[str, Any],
) -> dict[str, Any]:
    aov = profit_context.get("average_order_value")
    margin = profit_context.get("gross_margin_range") or {}
    gross_profit = profit_context.get("estimated_gross_profit_per_order")
    cvr = cvr_baseline.get("value")
    margin_midpoint = margin.get("midpoint") if isinstance(margin, dict) else None
    ladder = {
        "formula": "CPC = AOV × baseline CVR × ACoS",
        "aov": aov,
        "gross_margin_midpoint": margin_midpoint,
        "estimated_gross_profit_per_order": gross_profit,
        "baseline_cvr": cvr,
        "baseline_cvr_source": cvr_baseline.get("source"),
        "baseline_cvr_confidence": cvr_baseline.get("confidence"),
        "target_acos": target_acos,
        "target_acos_cpc": None,
        "break_even_acos": margin_midpoint,
        "break_even_cpc": None,
        "acos_cpc_points": [],
        "note": "CPC limits require CVR; values are estimates using the baseline CVR source shown.",
    }
    ladder["target_acos_cpc"] = cpc_at_acos(aov, cvr, target_acos)
    ladder["break_even_cpc"] = cpc_at_acos(aov, cvr, margin_midpoint)
    for acos in CONFIRMATION_ACOS_LADDER:
        ladder["acos_cpc_points"].append({"acos": acos, "cpc": cpc_at_acos(aov, cvr, acos)})
    return ladder


def fmt_money_or_na(value: Any, currency_symbol: str = "$") -> str:
    number = metric_float(value)
    return f"{currency_symbol}{number:.2f}" if number is not None else "N/A"


def fmt_percent_or_na(value: Any) -> str:
    number = metric_float(value)
    return f"{number * 100:.2f}%" if number is not None else "N/A"


def cpc_ladder_confirmation_text(
    ladder: dict[str, Any] | None,
    currency_symbol: str = "$",
) -> str:
    if not ladder:
        return "目标CPC估算=N/A（缺少AOV、目标ACoS或基准CVR）"
    points = "；".join(
        f"{fmt_percent_or_na(item.get('acos'))} ACoS CPC {fmt_money_or_na(item.get('cpc'), currency_symbol)}"
        for item in ladder.get("acos_cpc_points", [])
    )
    return (
        f"基准CVR={fmt_percent_or_na(ladder.get('baseline_cvr'))}"
        f"({ladder.get('baseline_cvr_source') or 'none'}, {ladder.get('baseline_cvr_confidence') or 'low'})；"
        f"目标ACoS CPC={fmt_money_or_na(ladder.get('target_acos_cpc'), currency_symbol)}；"
        f"盈亏平衡ACoS={fmt_percent_or_na(ladder.get('break_even_acos'))}；"
        f"盈亏平衡CPC={fmt_money_or_na(ladder.get('break_even_cpc'), currency_symbol)}；"
        f"{points}"
    )


def effective_target_acos(product: dict[str, Any]) -> float | None:
    value = parse_rate(product.get("target_acos"), "target_acos")
    if value is not None and value <= 0:
        raise ValueError("Invalid target_acos value; target ACoS must be greater than zero.")
    return value


def compute_cvr_baseline(
    product: dict[str, Any],
    ad_data: dict[str, Any],
    source_name: str = "ad_data_weighted_average",
) -> dict[str, Any]:
    db_cvr = product.get("baseline_cvr")
    if db_cvr is not None:
        value = parse_rate(db_cvr, "baseline_cvr")
        if value is not None and value <= 0:
            raise ValueError("Invalid baseline_cvr value; baseline CVR must be greater than zero.")
        if value is not None:
            return {"value": value, "source": "product_db", "confidence": "high"}

    if ad_data.get("status") != "ok":
        return {"value": None, "source": "none", "confidence": "low"}

    converting_orders = 0.0
    converting_clicks = 0.0
    for term in ad_data.get("all_terms", []):
        if float(term.get("orders", 0)) > 0:
            converting_orders += float(term.get("orders", 0))
            converting_clicks += float(term.get("clicks", 0))

    if converting_clicks <= 0:
        return {"value": None, "source": "none", "confidence": "low"}

    value = converting_orders / converting_clicks
    confidence = "medium" if converting_clicks >= 100 else "low"
    return {"value": round(value, 6), "source": source_name, "confidence": confidence}


def ctr_signal_label(impressions: Any, ctr: Any, baseline_ctr: Any) -> str:
    impression_count = metric_float(impressions)
    ctr_value = metric_float(ctr) if ctr is not None else None
    baseline_value = metric_float(baseline_ctr) if baseline_ctr is not None else None
    if impression_count < CTR_MIN_IMPRESSIONS_WEAK or ctr_value is None or not baseline_value:
        return "insufficient_ctr_signal"
    ratio = ctr_value / baseline_value if baseline_value else None
    if ratio is None:
        return "insufficient_ctr_signal"
    if impression_count < CTR_MIN_IMPRESSIONS_USABLE:
        if ratio < 0.33:
            return "weak_sample_strong_ctr_weakness"
        if ratio < 0.5:
            return "weak_sample_ctr_weakness"
        return "weak_sample_ctr_normal"
    if ratio < 0.33:
        return "strong_ctr_weakness"
    if ratio < 0.5:
        return "ctr_weakness"
    if ratio >= 1.2:
        return "above_baseline_ctr"
    return "near_baseline_ctr"


def ctr_context_for_term(term: dict[str, Any], peer_sets: list[dict[str, Any]]) -> dict[str, Any]:
    impressions = metric_float(term.get("impressions"))
    ctr = term.get("ctr")
    output: dict[str, Any] = {
        "ctr": ctr,
        "impressions": term.get("impressions", 0),
        "baseline_ctr": None,
        "baseline_scope": "none",
        "baseline_confidence": "low",
        "relative_ctr": None,
        "ctr_signal": "insufficient_ctr_signal",
        "note": "CTR is an auxiliary relevance signal. Do not use it as a standalone negative rule.",
    }
    if impressions < CTR_MIN_IMPRESSIONS_WEAK or ctr is None:
        return output

    for peer in peer_sets:
        peer_impressions = metric_float(peer.get("impressions"))
        peer_terms = int(metric_float(peer.get("terms")))
        peer_ctr = peer.get("ctr")
        if peer_ctr is None or peer_impressions < CTR_BASELINE_MIN_PEER_IMPRESSIONS or peer_terms < CTR_BASELINE_MIN_PEER_TERMS:
            continue
        relative_ctr = safe_ratio(metric_float(ctr), metric_float(peer_ctr))
        output.update(
            {
                "baseline_ctr": peer_ctr,
                "baseline_scope": peer.get("scope", "unknown"),
                "baseline_confidence": peer.get("confidence", "medium"),
                "relative_ctr": relative_ctr,
                "ctr_signal": ctr_signal_label(impressions, ctr, peer_ctr),
            }
        )
        return output

    return output


def build_ctr_peer_maps(
    ad_data: dict[str, Any],
    campaign_data: dict[str, Any],
    ad_group_data: dict[str, Any],
    match_type_data: dict[str, Any],
) -> dict[str, Any]:
    def peer_summary(items: list[dict[str, Any]], scope: str, confidence: str) -> dict[str, Any]:
        impressions = sum(metric_float(item.get("impressions")) for item in items)
        clicks = sum(metric_float(item.get("clicks")) for item in items)
        return {
            "scope": scope,
            "confidence": confidence,
            "impressions": maybe_int(impressions),
            "clicks": maybe_int(clicks),
            "terms": len(items),
            "ctr": safe_ratio(clicks, impressions),
        }

    maps: dict[str, Any] = {
        "account": {"status": "not_available"},
        "campaign": {},
        "ad_group": {},
        "match_type": {},
        "policy": {
            "min_term_impressions_weak": CTR_MIN_IMPRESSIONS_WEAK,
            "min_term_impressions_usable": CTR_MIN_IMPRESSIONS_USABLE,
            "min_peer_impressions": CTR_BASELINE_MIN_PEER_IMPRESSIONS,
            "min_peer_terms": CTR_BASELINE_MIN_PEER_TERMS,
            "baseline_priority": ["ad_group", "campaign", "match_type", "account"],
        },
    }
    if ad_data.get("status") == "ok":
        terms = ad_data.get("all_terms", [])
        maps["account"] = peer_summary(terms, "account", "low")

    for dimension_name, dimension_data in [
        ("campaign", campaign_data),
        ("ad_group", ad_group_data),
        ("match_type", match_type_data),
    ]:
        if dimension_data.get("status") != "ok":
            continue
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in dimension_data.get("all_pairs", []):
            dimension_value = clean_text(item.get(dimension_name))
            if dimension_value:
                grouped[dimension_value].append(item)
        confidence = "medium" if dimension_name == "match_type" else "high"
        maps[dimension_name] = {
            value: peer_summary(items, f"{dimension_name}:{value}", confidence)
            for value, items in grouped.items()
        }
    return maps


def enrich_terms_with_ctr_context(
    ad_data: dict[str, Any] | None,
    ctr_peer_maps: dict[str, Any],
) -> None:
    if not ad_data or ad_data.get("status") != "ok":
        return
    account_peer = ctr_peer_maps.get("account", {})
    campaign_map = ctr_peer_maps.get("campaign", {})
    ad_group_map = ctr_peer_maps.get("ad_group", {})
    match_type_map = ctr_peer_maps.get("match_type", {})
    for term in ad_data.get("all_terms", []):
        peer_sets = []
        for ad_group in term.get("ad_groups", [])[:3]:
            peer = ad_group_map.get(ad_group)
            if peer:
                peer_sets.append(peer)
        for campaign in term.get("campaigns", [])[:3]:
            peer = campaign_map.get(campaign)
            if peer:
                peer_sets.append(peer)
        for match_type in term.get("match_types", [])[:2]:
            peer = match_type_map.get(match_type)
            if peer:
                peer_sets.append(peer)
        if account_peer.get("status") != "not_available":
            peer_sets.append(account_peer)
        term["ctr_context"] = ctr_context_for_term(term, peer_sets)


def bid_economics_for_term(
    term: dict[str, Any],
    target_acos: float | None,
    aov: float | None,
    cvr_baseline: dict[str, Any],
    period_category: str | None = None,
    account_cpc_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clicks = metric_float(term.get("clicks"))
    click_count = clicks or 0
    orders = metric_float(term.get("orders"))
    cvr = term.get("cvr")
    cpc = term.get("cpc")
    impressions = metric_float(term.get("impressions"))
    cvr_value = metric_float(cvr) if cvr is not None else None
    cpc_value = metric_float(cpc) if cpc is not None else None
    ctr_context = term.get("ctr_context") or {}
    baseline_value = cvr_baseline.get("value")
    baseline_confidence = cvr_baseline.get("confidence")
    bid_cvr_value = None
    bid_cvr_source = "none"
    bid_cvr_confidence = "low"
    bid_cvr_note = "No usable local or baseline CVR."
    if (
        cvr_value
        and orders
        and orders >= LOCAL_CVR_MIN_ORDERS_FOR_BID
        and click_count >= LOCAL_CVR_MIN_CLICKS_FOR_BID
    ):
        bid_cvr_value = cvr_value
        bid_cvr_source = "term_local_cvr"
        bid_cvr_confidence = "high" if click_count >= 100 else "medium"
        bid_cvr_note = "Using term CVR because local order/click sample is usable."
    elif baseline_value:
        bid_cvr_value = metric_float(baseline_value)
        bid_cvr_source = cvr_baseline.get("source") or "baseline_cvr"
        bid_cvr_confidence = baseline_confidence or "low"
        if cvr_value and orders and orders > 0:
            bid_cvr_note = (
                f"Local CVR requires at least {LOCAL_CVR_MIN_ORDERS_FOR_BID} orders and "
                f"{LOCAL_CVR_MIN_CLICKS_FOR_BID} clicks; using product/baseline CVR for bid economics."
            )
        else:
            bid_cvr_note = "No converting local CVR; using product/baseline CVR for bid economics."
    elif cvr_value and orders and orders > 0 and click_count > 0:
        bid_cvr_value = cvr_value
        bid_cvr_source = "term_local_cvr_low_sample_no_baseline"
        bid_cvr_confidence = "low"
        bid_cvr_note = "Using low-sample local CVR because no baseline CVR is available."
    output: dict[str, Any] = {
        "max_cpc": None,
        "baseline_max_cpc": None,
        "min_acos": None,
        "required_cvr": None,
        "bid_cvr_used": bid_cvr_value,
        "bid_cvr_source": bid_cvr_source,
        "bid_cvr_confidence": bid_cvr_confidence,
        "bid_cvr_note": bid_cvr_note,
        "cpc_decision_mode": None,
        "cpc_decision_note": None,
        "traffic_action_mode": None,
        "traffic_action_note": None,
        "cvr_health": "not_available",
        "bid_feasibility_note": "AOV, Target ACoS, CPC, or CVR missing.",
    }
    if aov and target_acos and baseline_value:
        output["baseline_max_cpc"] = target_acos * aov * metric_float(baseline_value)
    if aov and target_acos and bid_cvr_value and click_count > 0:
        output["max_cpc"] = target_acos * aov * bid_cvr_value
        if cpc_value is not None:
            output["min_acos"] = safe_ratio(cpc_value, aov * bid_cvr_value)
    if aov and target_acos and cpc_value is not None:
        output["required_cvr"] = safe_ratio(cpc_value, target_acos * aov)

    if output["max_cpc"] is not None and cpc_value is not None:
        output["bid_feasibility_note"] = (
            "cpc_above_max_cpc" if cpc_value > output["max_cpc"] else "cpc_within_max_cpc"
        )

    if (
        baseline_confidence in {"medium", "high"}
        and baseline_value
        and cvr_value is not None
        and orders
        and orders == 1
    ):
        output["cvr_health"] = "one_order_low_confidence"
    elif (
        baseline_confidence in {"medium", "high"}
        and baseline_value
        and cvr_value is not None
        and orders
        and orders > 0
        and click_count < LOCAL_CVR_MIN_CLICKS_FOR_BID
    ):
        output["cvr_health"] = "skipped_low_local_sample"
    elif baseline_confidence in {"medium", "high"} and baseline_value and cvr_value is not None:
        baseline = metric_float(baseline_value)
        if baseline > 0:
            if cvr_value >= baseline:
                output["cvr_health"] = "healthy"
            elif cvr_value >= baseline * 0.5:
                output["cvr_health"] = "conversion_risk"
            else:
                output["cvr_health"] = "high_conversion_risk"
    elif baseline_confidence == "low":
        output["cvr_health"] = "skipped_low_baseline_confidence"

    account_mode = (account_cpc_context or {}).get("mode")
    if account_mode == "launch_or_low_history":
        output["cpc_decision_mode"] = "launch_test"
        output["cpc_decision_note"] = (
            "Low account history: use profitability CPC as a reference, but do not aggressively cut relevant core terms before learning budget/click thresholds are reached."
        )
    elif period_category in {"historically_good_recently_weak", "historically_good_no_recent_traffic", "recent_efficiency_decline"}:
        output["cpc_decision_mode"] = "diagnostic_protect"
        output["cpc_decision_note"] = (
            "Historical performance exists but recent efficiency weakened: protect the term from negatives and diagnose price/listing/ranking/competition before strict CPC cuts."
        )
    elif period_category in {"consistently_converting", "recently_improved"}:
        output["cpc_decision_mode"] = "historical_protect"
        output["cpc_decision_note"] = (
            "Term has conversion history: use CPC limits for bid control, not as a reason to abandon the term."
        )
    elif output["bid_cvr_source"] != "term_local_cvr":
        output["cpc_decision_mode"] = "learning_test"
        output["cpc_decision_note"] = (
            "Local CVR is missing or low-sample: use baseline CPC as a reference and cap spend before making strict bid cuts."
        )
    elif period_category == "consistently_click_only":
        output["cpc_decision_mode"] = "strict_cpc_control"
        output["cpc_decision_note"] = (
            "Term is click-only across periods: if semantic fit is not strong, strict CPC control or negative review is appropriate after threshold checks."
        )
    else:
        output["cpc_decision_mode"] = "standard_cpc_control"
        output["cpc_decision_note"] = "Use Max CPC/Required CVR together with semantic fit, history, and click sufficiency."

    historically_good = period_category in {
        "historically_good_recently_weak",
        "historically_good_no_recent_traffic",
        "recent_efficiency_decline",
        "consistently_converting",
        "recently_improved",
    }
    cpc_within_limit = (
        output["max_cpc"] is not None
        and cpc_value is not None
        and cpc_value <= output["max_cpc"]
    )
    cpc_above_limit = (
        output["max_cpc"] is not None
        and cpc_value is not None
        and cpc_value > output["max_cpc"]
    )
    ctr_signal = ctr_context.get("ctr_signal")
    if historically_good and click_count <= LOW_TRAFFIC_CLICK_THRESHOLD and impressions < LOW_TRAFFIC_IMPRESSION_THRESHOLD:
        if cpc_within_limit:
            output["traffic_action_mode"] = "increase_test"
            output["traffic_action_note"] = "Historical performer has very low clicks/exposure while CPC is within Max CPC; determine the final 0% to +10% recovery test from baseline order strength and historical ACoS, then check budget, rank, and placement."
        elif cpc_above_limit:
            output["traffic_action_mode"] = "hold_diagnose_visibility"
            output["traffic_action_note"] = "Historical performer has low clicks/exposure but CPC is already above Max CPC; do not raise bid blindly, check budget, rank, and placement."
        else:
            output["traffic_action_mode"] = "observe_low_sample"
            output["traffic_action_note"] = "Historical performer has low traffic, but CPC feasibility is unavailable; observe or run capped test."
    elif historically_good and period_category in {"historically_good_recently_weak", "historically_good_no_recent_traffic", "recent_efficiency_decline"}:
        output["traffic_action_mode"] = "soft_bid_control"
        output["traffic_action_note"] = "Historical performer with enough recent traffic but weaker conversion: diagnose conversion drivers first; use only soft bid control."
    elif historically_good and impressions >= HIGH_EXPOSURE_IMPRESSION_THRESHOLD and click_count <= LOW_TRAFFIC_CLICK_THRESHOLD:
        output["traffic_action_mode"] = "diagnose_ctr_or_creative"
        output["traffic_action_note"] = "Historical performer has exposure but few clicks; diagnose main image, title, price, coupon, rating, and relevance before raising bid."
    elif ctr_signal in {"ctr_weakness", "strong_ctr_weakness", "weak_sample_ctr_weakness", "weak_sample_strong_ctr_weakness"}:
        output["traffic_action_mode"] = "diagnose_ctr_or_creative"
        output["traffic_action_note"] = "CTR is weak relative to peers; diagnose creative/relevance before increasing bid."
    else:
        output["traffic_action_mode"] = "no_specific_traffic_action"
        output["traffic_action_note"] = "No low-traffic bid increase signal; follow CPC decision mode and semantic/performance checks."

    return output


def term_action_eligibility(
    term: dict[str, Any],
    profit_acos_cap: float | None,
) -> dict[str, Any]:
    """Resolve the one user-facing action class after economics are available."""
    orders = metric_float(term.get("orders"))
    clicks = metric_float(term.get("clicks"))
    acos = parse_optional_number(term.get("acos"))
    cpc = parse_optional_number(term.get("cpc"))
    bid_economics = term.get("bid_economics") or {}
    max_cpc = parse_optional_number(
        bid_economics.get("baseline_max_cpc") or bid_economics.get("max_cpc")
    )
    daily_signal = ((term.get("daily_action_evidence") or {}).get("scale_signal") or {})
    blockers: list[str] = []

    if profit_acos_cap is not None and acos is not None and acos > profit_acos_cap:
        blockers.append("acos_above_profit_cap")
    if max_cpc is not None and cpc is not None and cpc > max_cpc:
        blockers.append("cpc_above_max_cpc")
    if daily_signal and not bool(daily_signal.get("longer_window_consistent")):
        blockers.append("longer_window_not_confirmed")

    abnormal_attribution = bool(clicks > 0 and orders > clicks)
    local_conversion_usable = bool(
        orders >= LOCAL_CVR_MIN_ORDERS_FOR_BID
        and clicks >= LOCAL_CVR_MIN_CLICKS_FOR_BID
        and not abnormal_attribution
    )
    local_conversion_confidence = "usable" if local_conversion_usable else "low"

    if orders <= 0:
        action_class = "historical_protected_hold" if term.get("period_category") in {
            "historically_good_recently_weak",
            "historically_good_no_recent_traffic",
            "recent_efficiency_decline",
            "consistently_converting",
            "recently_improved",
        } else "observe"
    elif "acos_above_profit_cap" in blockers or "cpc_above_max_cpc" in blockers:
        action_class = "profit_repair"
    elif not local_conversion_usable:
        action_class = "low_confidence_validation"
    elif orders >= 3 and "longer_window_not_confirmed" not in blockers:
        action_class = "scale_ready"
    else:
        action_class = "controlled_scale_test"

    return {
        "action_eligibility": action_class,
        "local_conversion_confidence": local_conversion_confidence,
        "abnormal_attribution_sample": abnormal_attribution,
        "scale_blockers": blockers,
        "profit_acos_cap": profit_acos_cap,
        "decision_rule": (
            "Scale requires at least 3 orders, usable local conversion evidence, ACoS at or below "
            "profit_acos_cap, CPC at or below Max CPC, and no conflicting longer-window signal."
        ),
    }


def enrich_ad_data_with_action_eligibility(
    ad_data: dict[str, Any] | None,
    profit_acos_cap: float | None,
) -> None:
    if not ad_data or ad_data.get("status") != "ok":
        return
    for term in ad_data.get("all_terms", []):
        term.update(term_action_eligibility(term, profit_acos_cap))


def pct(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def infer_marketplace_from_headers(headers: Iterable[Any]) -> str | None:
    normalized = {re.sub(r"\s+", " ", normalize_header(value)).casefold() for value in headers}
    german_markers = {
        "kundensuchbegriff", "suchbegriff des kunden", "ausgaben", "werbeausgaben",
        "klicks", "kampagnenname", "anzeigengruppenname", "platzierung", "datum",
    }
    return "DE" if normalized & german_markers else None


def resolve_marketplace(
    requested: Any,
    tables: list[dict[str, Any]],
    product_default: Any = None,
) -> str:
    def canonical(value: Any, source: str) -> str:
        text = clean_text(value).upper().replace("_", "-")
        aliases = {"US": "US", "NA-US": "US", "DE": "DE", "EU-DE": "DE"}
        if not text:
            return ""
        if text not in aliases:
            raise ActionRequired("fix_marketplace", f"{source}中的站点{value!r}不受支持；仅支持US或DE。")
        return aliases[text]

    requested_value = canonical(requested, "用户参数")
    product_value = canonical(product_default, "产品库")
    report_hints = {
        clean_text(table.get("marketplace_hint")).upper()
        for table in tables
        if clean_text(table.get("marketplace_hint"))
    }
    if len(report_hints) > 1:
        raise ActionRequired(
            "marketplace_conflict",
            f"输入报表包含相互冲突的站点提示：{sorted(report_hints)}。请核对报表后重新运行。",
        )
    report_value = canonical(next(iter(report_hints), ""), "报表")
    evidence = [value for value in [requested_value, report_value, product_value] if value]
    if len(set(evidence)) > 1:
        raise ActionRequired(
            "marketplace_conflict",
            (
                "站点信息冲突："
                f"用户参数={requested_value or 'N/A'}；报表提示={report_value or 'N/A'}；"
                f"产品库默认值={product_value or 'N/A'}。请核对后重新运行。"
            ),
        )
    return requested_value or report_value or product_value or "US"


def normalize_table_numeric_values(
    tables: list[dict[str, Any]],
    marketplace: Any = None,
    *,
    strict_required: bool = True,
) -> None:
    for table in tables:
        table_marketplace = marketplace or table.get("marketplace_hint")
        strict_ad_table = strict_required and all(column in table.get("headers", []) for column in REQUIRED_AD_COLUMNS)
        placement_table = bool(
            "广告位" in table.get("headers", [])
            and "所在广告活动" in table.get("headers", [])
        )
        strict_decision_table = strict_ad_table or placement_table
        for record in table.get("records", []):
            if table_marketplace:
                record["__marketplace"] = str(table_marketplace).upper()
            for column in NUMERIC_CANONICAL_COLUMNS:
                if column not in record:
                    continue
                raw_value = record.get(column)
                invalid_strict_value = False
                try:
                    number = (
                        (
                            None
                            if is_missing(raw_value)
                            else parse_unit_value(
                                raw_value,
                                kind=NUMERIC_COLUMN_KINDS[column],
                                marketplace=table_marketplace,
                            )[0]
                        )
                        if strict_decision_table
                        else parse_localized_number(raw_value, table_marketplace)
                    )
                except ValueError:
                    number = None
                    invalid_strict_value = True
                if number is not None:
                    if strict_decision_table and number < 0:
                        details = {
                            "file": record.get("__source_file") or table.get("source_file"),
                            "sheet": record.get("__source_sheet") or table.get("sheet"),
                            "row": record.get("__source_row"),
                            "column": column,
                            "value": raw_value,
                        }
                        raise ActionRequired(
                            "fix_invalid_numeric_value",
                            (
                                f"文件{details['file'] or 'N/A'}，工作表{details['sheet'] or 'N/A'}，"
                                f"第{details['row'] or 'N/A'}行，{column}列不能为负数：{raw_value!r}。"
                            ),
                            details,
                        )
                    record[column] = number
                elif strict_decision_table and (
                    (column in {"广告订单量", "广告花费", "广告点击量"} and is_missing(raw_value))
                    or (
                        clean_text(raw_value)
                        and (
                            invalid_strict_value
                            or column in {"广告订单量", "广告花费", "广告点击量"}
                            or clean_text(raw_value).casefold() not in {"-", "--", "n/a", "na", "none", "null"}
                        )
                    )
                ):
                    details = {
                        "file": record.get("__source_file") or table.get("source_file"),
                        "sheet": record.get("__source_sheet") or table.get("sheet"),
                        "row": record.get("__source_row"),
                        "column": column,
                        "value": raw_value,
                    }
                    raise ActionRequired(
                        "fix_invalid_numeric_value",
                        (
                            f"文件{details['file'] or 'N/A'}，工作表{details['sheet'] or 'N/A'}，"
                            f"第{details['row'] or 'N/A'}行，{column}列的值{raw_value!r}无法解析。"
                        ),
                        details,
                    )


def load_table(path: Path, marketplace: Any = None) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix in {".csv", ".tsv"}:
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                if suffix == ".tsv":
                    delimiter = "\t"
                else:
                    sample = handle.read(32768)
                    handle.seek(0)
                    try:
                        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
                    except csv.Error:
                        delimiter = ","
                reader = csv.reader(handle, delimiter=delimiter)
                rows = list(reader)
        except UnicodeError as exc:
            raise ActionRequired(
                "fix_input_file",
                f"报表文件不是有效的UTF-8编码，请重新导出为UTF-8 CSV/TSV：{path}",
                {"file": str(path), "reason": "invalid_utf8_encoding"},
            ) from exc
        except (OSError, csv.Error) as exc:
            raise ActionRequired(
                "fix_input_file",
                f"报表文件无法读取，请确认文件完整且可访问：{path}",
                {"file": str(path), "reason": type(exc).__name__},
            ) from exc
        return rows_to_tables(path.name, rows, marketplace, source_file=path.name)
    if suffix in {".xlsx", ".xlsm", ".xltx", ".xltm"}:
        if load_workbook is None:
            raise ActionRequired(
                "fix_runtime_dependency",
                "读取Excel文件需要openpyxl；请安装openpyxl（pip install openpyxl）或使用包含openpyxl的Python环境。",
            )
        workbook = None
        try:
            workbook = load_workbook(path, read_only=True, data_only=True)
            raw_sheets: list[tuple[str, list[tuple[Any, ...]]]] = []
            for ws in workbook.worksheets:
                try:
                    ws.reset_dimensions()
                except Exception:
                    pass
                rows = [tuple(row) for row in ws.iter_rows(values_only=True)]
                raw_sheets.append((ws.title, rows))
        except (BadZipFile, InvalidFileException, ParseError, OSError, ValueError, KeyError, EOFError) as exc:
            raise ActionRequired(
                "fix_input_file",
                f"报表文件不是有效或完整的Excel工作簿，请重新导出：{path}",
                {"file": str(path), "reason": type(exc).__name__},
            ) from exc
        finally:
            if workbook is not None:
                workbook.close()
        tables: list[dict[str, Any]] = []
        for sheet_name, rows in raw_sheets:
            tables.extend(rows_to_tables(sheet_name, rows, marketplace, source_file=path.name))
        return tables
    if suffix == ".json":
        raise ActionRequired(
            "fix_input_file",
            f"JSON 产品数据库请使用 --product-db，而不是 --product-file：{path}",
            {"file": str(path), "reason": "json_product_database_requires_product_db"},
        )
    raise ActionRequired("fix_input_file", f"不支持的文件类型：{path}")


def rows_to_tables(
    sheet_name: str,
    rows: list[Iterable[Any]],
    marketplace: Any = None,
    *,
    source_file: str | None = None,
) -> list[dict[str, Any]]:
    materialized_with_rows = [
        (row_number, tuple(row))
        for row_number, row in enumerate(rows, start=1)
        if any(cell is not None and str(cell).strip() for cell in row)
    ]
    materialized = [row for _row_number, row in materialized_with_rows]
    if not materialized:
        return []
    header_index = detect_header_row(materialized)
    source_headers = [normalize_header(cell) for cell in materialized[header_index]]
    canonical_headers = [canonical_header(header) for header in source_headers]
    headers = list(dict.fromkeys([*source_headers, *canonical_headers]))
    records = []
    for source_row_number, row in materialized_with_rows[header_index + 1 :]:
        padded = list(row) + [None] * max(0, len(source_headers) - len(row))
        record = {source_headers[i]: padded[i] for i in range(len(source_headers)) if source_headers[i]}
        for source_header, canonical in zip(source_headers, canonical_headers):
            if not canonical or canonical == source_header:
                continue
            value = record.get(source_header)
            if canonical not in record or record.get(canonical) in {None, ""}:
                record[canonical] = value
        if any(value is not None and str(value).strip() for value in record.values()):
            record["__source_file"] = source_file or sheet_name
            record["__source_sheet"] = sheet_name
            record["__source_row"] = source_row_number
            records.append(record)
    marketplace_hint = infer_marketplace_from_headers(source_headers)
    parse_marketplace = marketplace or marketplace_hint
    table = {
        "sheet": sheet_name,
        "source_file": source_file or sheet_name,
        "header_row": materialized_with_rows[header_index][0],
        "headers": headers,
        "source_headers": source_headers,
        "marketplace_hint": marketplace_hint,
        "provided_marketplace": marketplace,
        "records": records,
    }
    tables = [table]
    if parse_marketplace:
        normalize_table_numeric_values(tables, parse_marketplace)
    return tables


def detect_header_row(rows: list[tuple[Any, ...]]) -> int:
    best_index = 0
    best_score = -1
    for index, row in enumerate(rows[:30]):
        raw_headers = {normalize_header(cell) for cell in row}
        headers = {*raw_headers, *(canonical_header(cell) for cell in row)}
        required_score = sum(1 for col in REQUIRED_AD_COLUMNS if col in headers) * 10
        known_score = sum(1 for col in PRODUCT_IMPORTANT_COLUMNS if col in headers)
        ad_score = sum(1 for col in KNOWN_AD_COLUMNS if col in headers)
        score = required_score + known_score + ad_score
        if score > best_score:
            best_index = index
            best_score = score
    return best_index


def load_product_db(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() != ".json":
        raise ActionRequired(
            "fix_product_database",
            f"产品数据库必须是JSON文件：{path}",
        )
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ActionRequired(
            "fix_product_database",
            f"产品数据库无法读取为有效JSON：{path}",
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("products"), list):
        raise ActionRequired(
            "fix_product_database",
            f"产品数据库结构无效，根对象必须包含products数组：{path}",
        )

    products = data["products"]
    owners: dict[str, list[str]] = {}
    for index, product in enumerate(products):
        if not isinstance(product, dict):
            raise ActionRequired(
                "fix_product_database",
                f"产品数据库第{index + 1}条记录必须是对象：{path}",
            )
        aliases = product.get("aliases", [])
        if not isinstance(aliases, list):
            raise ActionRequired(
                "fix_product_database",
                f"产品数据库第{index + 1}条记录的aliases必须是数组：{path}",
            )
        asin = clean_text(product.get("asin")).upper()
        if asin and not re.fullmatch(r"[A-Z0-9]{10}", asin):
            raise ActionRequired(
                "fix_product_database",
                f"产品数据库第{index + 1}条记录的ASIN无效：{product.get('asin')!r}",
            )
        owner = clean_text(product.get("key") or product.get("asin") or f"product-{index + 1}")
        identifiers = {
            normalized
            for value in [product.get("key"), product.get("asin"), product.get("sku"), *aliases]
            if (normalized := normalize_product_identifier(value))
        }
        if not identifiers:
            raise ActionRequired(
                "fix_product_database",
                f"产品数据库第{index + 1}条记录缺少key、ASIN、SKU或alias：{path}",
            )
        for identifier in identifiers:
            owners.setdefault(identifier, []).append(owner)
    collisions = {
        identifier: product_owners
        for identifier, product_owners in owners.items()
        if len(product_owners) > 1
    }
    if collisions:
        raise ActionRequired(
            "fix_product_database",
            f"产品数据库存在跨产品重复标识：{collisions}",
            {"collisions": collisions},
        )
    return products


def text_tokens_from_files(paths: list[Path]) -> str:
    return " ".join(path.name.lower() for path in paths if path)


def text_tokens_from_identifier_columns(tables: list[dict[str, Any]]) -> str:
    tokens: list[str] = []
    for table in tables:
        for record in table.get("records", [])[:200]:
            for column, value in record.items():
                header = clean_text(column).lower()
                if any(hint in header for hint in PRODUCT_IDENTIFIER_COLUMN_HINTS):
                    text = clean_text(value)
                    if text:
                        tokens.append(text.lower())
    return " ".join(tokens)


def normalize_asin(value: Any) -> str:
    match = re.search(r"(?i)\b[a-z0-9]{10}\b", clean_text(value))
    return match.group(0).upper() if match else ""


def is_asin_token(value: Any) -> bool:
    """Recognize an exact ASIN/ISBN-style token without treating plain ten-letter words as products."""
    text = clean_text(value)
    return bool(re.fullmatch(r"(?i)[a-z0-9]{10}", text) and re.search(r"\d", text))


def normalize_product_identifier(value: Any) -> str:
    return re.sub(r"\s+", " ", clean_text(value)).strip().casefold()


def product_owned_asins(product: dict[str, Any]) -> set[str]:
    values = [product.get("asin"), *product.get("aliases", [])]
    return {asin for value in values if (asin := normalize_asin(value))}


def product_owned_identifiers(product: dict[str, Any]) -> set[str]:
    values = [
        product.get("key"), product.get("asin"), product.get("sku"),
        *product.get("aliases", []),
    ]
    return {
        normalized
        for value in values
        if (normalized := normalize_product_identifier(value))
    }


def products_owning_asin(products: list[dict[str, Any]], asin: str) -> list[dict[str, Any]]:
    normalized = normalize_asin(asin)
    if not normalized:
        return []
    return [product for product in products if normalized in product_owned_asins(product)]


def extract_report_product_identifiers(tables: list[dict[str, Any]]) -> dict[str, Any]:
    asins: set[str] = set()
    skus: set[str] = set()
    sources: list[dict[str, Any]] = []
    for table in tables:
        for record in table.get("records", []):
            for column, value in record.items():
                header = normalize_product_identifier(column)
                if header.startswith("__") or not clean_text(value):
                    continue
                if "asin" in header:
                    found = {match.upper() for match in re.findall(r"(?i)\b[a-z0-9]{10}\b", clean_text(value))}
                    asins.update(found)
                    identifier_type = "asin"
                    normalized_values = sorted(found)
                elif "fnsku" in header:
                    continue
                elif any(token in header for token in ["sku", "msku", "商品编码"]):
                    normalized = normalize_product_identifier(value)
                    if normalized:
                        skus.add(normalized)
                    identifier_type = "sku"
                    normalized_values = [normalized] if normalized else []
                else:
                    continue
                if normalized_values:
                    sources.append({
                        "file": record.get("__source_file") or table.get("source_file"),
                        "sheet": record.get("__source_sheet") or table.get("sheet"),
                        "row": record.get("__source_row"),
                        "column": column,
                        "type": identifier_type,
                        "values": normalized_values,
                    })
    return {"asins": sorted(asins), "skus": sorted(skus), "sources": sources[:20]}


def text_tokens_from_product_context(tables: list[dict[str, Any]]) -> str:
    tokens: list[str] = []
    context_columns = {column.lower() for column in PRODUCT_CONTEXT_COLUMNS}
    for table in tables:
        for record in table.get("records", [])[:1000]:
            for column in PRODUCT_CONTEXT_COLUMNS:
                text = clean_text(record.get(column))
                if text:
                    tokens.append(text.lower())
            for column, value in record.items():
                if clean_text(column).lower() in context_columns:
                    continue
                header = clean_text(column).lower()
                if "campaign" in header or "广告活动" in header or "广告组" in header:
                    text = clean_text(value)
                    if text:
                        tokens.append(text.lower())
    return " ".join(tokens)


def product_match_terms(product: dict[str, Any]) -> list[tuple[str, int]]:
    weighted_terms: list[tuple[str, int]] = []
    strong_values = [
        product.get("key"),
        product.get("asin"),
        product.get("sku"),
        *product.get("aliases", []),
    ]
    for value in strong_values:
        text = clean_text(value).lower()
        if text:
            weighted_terms.append((text, 6))

    for value in [product.get("core_category"), product.get("title")]:
        text = clean_text(value).lower()
        if text and len(text) >= 8:
            weighted_terms.append((text, 3))

    for value in product.get("key_attributes", [])[:12]:
        text = clean_text(value).lower()
        if len(text) >= 5 and not re.fullmatch(r"\d+(?:\.\d+)?", text):
            weighted_terms.append((text, 2))

    compacted: dict[str, int] = {}
    for term, weight in weighted_terms:
        compacted[term] = max(compacted.get(term, 0), weight)
    return sorted(compacted.items(), key=lambda item: (-item[1], -len(item[0]), item[0]))


def score_product_context_match(product: dict[str, Any], context_text: str) -> dict[str, Any]:
    if not context_text:
        return {"score": 0, "matched_terms": []}
    matched_terms = []
    score = 0
    owned_identifiers = product_owned_identifiers(product)
    for term, weight in product_match_terms(product):
        matches = (
            bounded_identifier_in_text(term, context_text)
            if term in owned_identifiers
            else term in context_text
        )
        if term and matches:
            score += weight
            matched_terms.append({"term": term, "weight": weight})
    return {"score": score, "matched_terms": matched_terms[:8]}


def match_products(
    products: list[dict[str, Any]],
    file_paths: list[Path],
    explicit_key: str | None = None,
    explicit_asin: str | None = None,
    table_identifier_text: str = "",
    product_context_text: str = "",
    report_identifiers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report_identifiers = report_identifiers or {"asins": [], "skus": [], "sources": []}
    report_asins = {
        normalized
        for value in (report_identifiers.get("asins") or [])
        if (normalized := normalize_asin(value))
    }
    report_skus = {
        normalized
        for value in (report_identifiers.get("skus") or [])
        if (normalized := normalize_product_identifier(value))
    }

    def product_key(product: dict[str, Any]) -> str:
        return normalize_product_identifier(product.get("key") or product.get("asin"))

    products_by_key = {product_key(product): product for product in products if product_key(product)}

    def multiple_report_products_result() -> dict[str, Any]:
        status = "multiple_report_products"
        return {
            "status": status,
            "matched_product": None,
            "matched_identity": None,
            "candidate_keys": [],
            "candidate_identities": [],
            "report_identifiers": report_identifiers,
            "confirmation_required": True,
            "confirmation_message": product_match_message(status, None, [], report_identifiers=report_identifiers),
        }

    def identifier_conflict_result() -> dict[str, Any]:
        status = "identifier_conflict"
        return {
            "status": status,
            "matched_product": None,
            "matched_identity": None,
            "candidate_keys": [],
            "candidate_identities": [],
            "report_identifiers": report_identifiers,
            "confirmation_required": True,
            "confirmation_message": product_match_message(
                status, None, [], report_identifiers=report_identifiers
            ),
        }

    report_owner: dict[str, Any] | None = None
    report_owner_sets: list[set[str]] = []
    if report_asins:
        asin_owner_sets = [
            {product_key(product) for product in products_owning_asin(products, asin)}
            for asin in sorted(report_asins)
        ]
        if any(not owners for owners in asin_owner_sets):
            return (
                multiple_report_products_result()
                if len(report_asins) > 1
                else identifier_conflict_result()
            )
        report_owner_sets.extend(asin_owner_sets)
    if report_skus:
        sku_owner_sets = [
            {
                product_key(product)
                for product in products
                if sku in product_owned_identifiers(product)
            }
            for sku in sorted(report_skus)
        ]
        if any(not owners for owners in sku_owner_sets):
            return (
                multiple_report_products_result()
                if len(report_skus) > 1
                else identifier_conflict_result()
            )
        report_owner_sets.extend(sku_owner_sets)
    if report_owner_sets:
        common_owners = set.intersection(*report_owner_sets)
        if len(common_owners) != 1:
            return (
                identifier_conflict_result()
                if report_asins and report_skus
                else multiple_report_products_result()
            )
        report_owner = products_by_key.get(next(iter(common_owners)))

    normalized_explicit_asin = normalize_asin(explicit_asin)
    if explicit_asin and not normalized_explicit_asin:
        status = "identifier_conflict"
        return {
            "status": status,
            "matched_product": None,
            "matched_identity": None,
            "candidate_keys": [],
            "candidate_identities": [],
            "report_identifiers": report_identifiers,
            "confirmation_required": True,
            "confirmation_message": product_match_message(
                status,
                {"asin": normalized_explicit_asin, "key": explicit_key},
                [],
                report_identifiers=report_identifiers,
            ),
        }
    if explicit_key or explicit_asin:
        key_matches = [
            product for product in products
            if explicit_key and normalize_product_identifier(explicit_key) in product_owned_identifiers(product)
        ]
        asin_matches = products_owning_asin(products, normalized_explicit_asin) if explicit_asin else []
        if explicit_key and explicit_asin:
            key_match_keys = {product_key(product) for product in key_matches}
            matches = [product for product in asin_matches if product_key(product) in key_match_keys]
            if not matches:
                status = "identifier_conflict"
                return {
                    "status": status,
                    "matched_product": None,
                    "matched_identity": None,
                    "candidate_keys": sorted(key_match_keys | {product_key(product) for product in asin_matches}),
                    "candidate_identities": [],
                    "report_identifiers": report_identifiers,
                    "confirmation_required": True,
                    "confirmation_message": product_match_message(
                        status,
                        {"asin": normalized_explicit_asin, "key": explicit_key},
                        [],
                        report_identifiers=report_identifiers,
                    ),
                }
        else:
            matches = key_matches or asin_matches
    else:
        filename_text = text_tokens_from_files(file_paths)
        identifier_text = table_identifier_text.lower()
        context_text = product_context_text.lower()
        haystack = " ".join([filename_text, identifier_text, context_text]).strip()
        asins = set(
            re.findall(r"(?<![a-z0-9])b0[a-z0-9]{8}(?![a-z0-9])", haystack, flags=re.I)
        )
        matches = [report_owner] if report_owner else []
        scored_context_matches: list[tuple[int, dict[str, Any], list[dict[str, Any]]]] = []
        if not report_owner:
            for product in products:
                identifiers = [
                    product.get("key"),
                    product.get("asin"),
                    product.get("sku"),
                    *product.get("aliases", []),
                ]
                identifiers = [str(item).lower() for item in identifiers if item]
                if any(
                    bounded_identifier_in_text(identifier, f"{filename_text} {identifier_text}")
                    for identifier in identifiers
                ):
                    matches.append(product)
                    continue
                asin = str(product.get("asin", "")).lower()
                if asin and asin in asins:
                    matches.append(product)
                    continue
                context_match = score_product_context_match(product, context_text)
                if context_match["score"] >= 6:
                    scored_context_matches.append((context_match["score"], product, context_match["matched_terms"]))
        if not matches and scored_context_matches:
            top_score = max(score for score, _product, _terms in scored_context_matches)
            matches = [
                product
                for score, product, _terms in scored_context_matches
                if score == top_score
            ]
    unique = []
    seen = set()
    for product in matches:
        key = product.get("key") or product.get("asin")
        if key not in seen:
            seen.add(key)
            unique.append(product)
    status = "none"
    if len(unique) == 1:
        status = "matched"
    elif len(unique) > 1:
        status = "multiple"
    matched_product = unique[0] if len(unique) == 1 else None
    matched_identity = product_identity(matched_product) if matched_product else None
    candidate_identities = [product_identity(product) for product in unique]
    if matched_identity:
        matched_asins = product_owned_asins(matched_product or {})
        asin_conflict = bool(report_asins and not report_asins.issubset(matched_asins))
        sku_conflict = bool(
            report_skus and not report_skus.issubset(product_owned_identifiers(matched_product or {}))
        )
        if asin_conflict or sku_conflict:
            status = "identifier_conflict"
            matched_product = None
            matched_identity = None
    return {
        "status": status,
        "matched_product": matched_product,
        "matched_identity": matched_identity,
        "candidate_keys": [product.get("key") for product in unique],
        "candidate_identities": candidate_identities,
        "report_identifiers": report_identifiers,
        "confirmation_required": status != "matched",
        "confirmation_message": product_match_message(
            status,
            matched_identity or (candidate_identities[0] if len(candidate_identities) == 1 else None),
            candidate_identities,
            report_identifiers=report_identifiers,
        ),
    }


def product_match_has_explicit_identifier(
    product_match: dict[str, Any],
    file_paths: list[Path],
    table_identifier_text: str,
    explicit_key: str | None = None,
    explicit_asin: str | None = None,
) -> bool:
    if explicit_key or explicit_asin:
        return True
    product = product_match.get("matched_product") or {}
    if not product:
        return False
    explicit_haystack = f"{text_tokens_from_files(file_paths)} {table_identifier_text.lower()}"
    identifier_values = [product.get("asin"), product.get("sku")]
    identifier_values.extend(
        alias
        for alias in product.get("aliases", [])
        if re.fullmatch(r"[a-z0-9]{10}", clean_text(alias), flags=re.I)
    )
    return any(
        bounded_identifier_in_text(identifier, explicit_haystack)
        for identifier in identifier_values
        if clean_text(identifier)
    )


def apply_product_overrides(product: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    effective = dict(product)
    if args.marketplace is not None:
        effective["marketplace_default"] = args.marketplace
    if args.target_acos is not None:
        effective["target_acos"] = args.target_acos
    if args.average_order_value is not None:
        effective["average_order_value"] = args.average_order_value
    if args.gross_margin_range is not None:
        effective["gross_margin_range"] = args.gross_margin_range
    if args.baseline_cvr is not None:
        effective["baseline_cvr"] = args.baseline_cvr
    if args.lifecycle_stage is not None:
        effective["lifecycle_stage"] = args.lifecycle_stage
    return effective


def product_identity(product: dict[str, Any] | None) -> dict[str, Any]:
    if not product:
        return {}
    return {
        "key": product.get("key"),
        "asin": product.get("asin"),
        "sku": product.get("sku"),
        "marketplace": product.get("marketplace_default"),
        "target_acos": product.get("target_acos"),
        "average_order_value": product.get("average_order_value"),
        "gross_margin_range": product.get("gross_margin_range"),
        "baseline_cvr": product.get("baseline_cvr"),
        "lifecycle_stage": product.get("lifecycle_stage"),
        "title": product.get("title"),
        "core_category": product.get("core_category"),
        "aliases": product.get("aliases", [])[:8],
    }


def product_data_quality(product: dict[str, Any]) -> dict[str, Any]:
    important_fields = [
        "target_acos",
        "average_order_value",
        "gross_margin_range",
        "baseline_cvr",
        "lifecycle_stage",
    ]
    missing = [field for field in important_fields if product.get(field) in {None, ""}]
    impacts = []
    if "baseline_cvr" in missing:
        impacts.append("Max CPC confidence falls back to report-derived CVR.")
    if "lifecycle_stage" in missing:
        impacts.append("Single-period mature-product status cannot be confirmed from the product database.")
    if {"average_order_value", "gross_margin_range"} & set(missing):
        impacts.append("Profit-based spend tolerance or incrementality gate is incomplete.")
    return {
        "status": "complete" if not missing else "incomplete",
        "missing_fields": missing,
        "impacts": impacts,
        "instruction": "Use reliable user/dashboard/history facts to fill missing fields; never invent values.",
    }


def product_match_message(
    status: str,
    matched_identity: dict[str, Any] | None,
    candidate_identities: list[dict[str, Any]],
    acos_cpc_ladder: dict[str, Any] | None = None,
    currency_symbol: str = "$",
    report_identifiers: dict[str, Any] | None = None,
) -> str:
    if status == "matched" and matched_identity:
        ladder_text = cpc_ladder_confirmation_text(acos_cpc_ladder, currency_symbol)
        return (
            "已匹配产品，请先向用户展示确认："
            f"ASIN={matched_identity.get('asin') or 'N/A'}；"
            f"产品key={matched_identity.get('key') or 'N/A'}；"
            f"中文/原文名称={matched_identity.get('title') or 'N/A'}；"
            f"目标ACoS={matched_identity.get('target_acos') or 'N/A'}；"
            f"{ladder_text}；"
            f"售价/AOV={matched_identity.get('average_order_value') or 'N/A'}；"
            f"毛利率={matched_identity.get('gross_margin_range') or 'N/A'}。"
        )
    if status == "multiple":
        compact = [
            {
                "asin": item.get("asin"),
                "key": item.get("key"),
                "title": item.get("title"),
                "target_acos": item.get("target_acos"),
                "average_order_value": item.get("average_order_value"),
                "gross_margin_range": item.get("gross_margin_range"),
            }
            for item in candidate_identities
        ]
        return f"匹配到多个可能产品，请立即停止并让用户选择后再继续：{compact}"
    if status == "identifier_required":
        return "每日明细报告缺少可验证的ASIN/SKU标识。请明确提供ASIN或产品key后重新运行，禁止仅凭搜索词或广告活动语义确认产品。"
    if status == "multiple_report_products":
        identifiers = report_identifiers or {}
        return (
            "广告报表包含多个产品标识，无法安全套用单一产品事实。"
            f"ASIN={identifiers.get('asins') or []}；SKU={identifiers.get('skus') or []}。"
            "请提供仅包含一个产品的报表后重新运行。"
        )
    if status == "identifier_conflict":
        identifiers = report_identifiers or {}
        return (
            "显式产品或产品库标识与广告报表冲突。"
            f"已选择ASIN={((matched_identity or {}).get('asin') or 'N/A')}；"
            f"报表ASIN={identifiers.get('asins') or []}；报表SKU={identifiers.get('skus') or []}。"
            "请核对产品和报表后重新运行。"
        )
    return "未匹配到产品，请立即停止并让用户提供产品ASIN、标题、卖点、兼容性、Target ACoS等信息后再继续。"


def missing_required_columns_message(file_label: str, ad_data: dict[str, Any]) -> str:
    required = ad_data.get("required_columns") or REQUIRED_AD_COLUMNS
    available_headers = ad_data.get("available_headers") or []
    preview_chunks: list[str] = []
    for headers in available_headers[:3]:
        if isinstance(headers, list) and headers:
            preview_chunks.append("、".join(str(column) for column in headers[:12]))
        else:
            preview_chunks.append("N/A")
    preview = "；".join(preview_chunks) if preview_chunks else "N/A"
    return (
        f"{file_label}缺少必要列：{'、'.join(required)}。"
        f"已识别表头：{preview}。"
        "请先重新导出或修正列映射后再继续。"
    )


def normalize_search_key(value: Any) -> str:
    return re.sub(r"\s+", " ", clean_text(value).lower())


def market_volume_tier(aba_rank: Any) -> str:
    rank = parse_optional_number(aba_rank)
    if rank is None or rank <= 0:
        return "unknown"
    if rank <= ABA_BIG_RANK_THRESHOLD:
        return "big/core"
    if rank <= ABA_MID_RANK_THRESHOLD:
        return "mid"
    return "small/niche"


def ad_exposure_tier(impressions: Any) -> str:
    count = metric_float(impressions)
    if count >= 1000:
        return "strong"
    if count >= CTR_MIN_IMPRESSIONS_USABLE:
        return "usable"
    if count >= CTR_MIN_IMPRESSIONS_WEAK:
        return "weak"
    return "insufficient"


def data_sufficiency_for_term(term: dict[str, Any]) -> str:
    orders = metric_float(term.get("orders"))
    clicks = metric_float(term.get("clicks"))
    spend = metric_float(term.get("spend"))
    impressions = metric_float(term.get("impressions"))
    if orders >= 2:
        return "strong"
    if orders > 0:
        return "usable"
    if clicks >= 20 or impressions >= 1000:
        return "usable_zero_order"
    if clicks > CLICK_ONLY_OUTPUT_CLICK_THRESHOLD or spend > 0:
        return "weak"
    return "insufficient"


def promotion_priority_for_term(term: dict[str, Any]) -> str:
    orders = metric_float(term.get("orders"))
    clicks = metric_float(term.get("clicks"))
    acos = metric_float(term.get("acos")) if term.get("acos") is not None else None
    volume = term.get("market_volume_tier") or "unknown"
    sufficiency = term.get("data_sufficiency") or data_sufficiency_for_term(term)
    if orders >= 2:
        if volume == "big/core" and acos is not None and acos > 0:
            return "steady"
        return "scale"
    if orders > 0:
        return "steady" if volume in {"big/core", "mid"} else "observe"
    if clicks > CLICK_ONLY_OUTPUT_CLICK_THRESHOLD:
        if volume == "big/core":
            return "diagnose"
        if volume == "small/niche" or sufficiency == "usable_zero_order":
            return "drop_or_negative_review"
        return "diagnose"
    return "observe"


def load_aba_rank_map(tables: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    rank_map: dict[str, dict[str, Any]] = {}
    for table in tables:
        term_columns = columns_present(table.get("headers", []), ABA_SEARCH_TERM_COLUMNS)
        rank_columns = columns_present(table.get("headers", []), ABA_RANK_COLUMNS)
        if not term_columns or not rank_columns:
            continue
        term_column = term_columns[0]
        rank_column = rank_columns[0]
        for record in table.get("records", []):
            term = clean_text(record.get(term_column))
            rank = parse_optional_number(record.get(rank_column))
            if not term or rank is None or rank <= 0:
                continue
            key = normalize_search_key(term)
            current = rank_map.get(key)
            if current is None or rank < current["aba_rank"]:
                rank_map[key] = {
                    "search_term": term,
                    "aba_rank": maybe_int(rank),
                    "aba_source_column": rank_column,
                    "aba_source_sheet": table.get("sheet"),
                }
    return rank_map


def enrich_terms_with_market_signal(ad_data: dict[str, Any] | None, aba_rank_map: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    if not ad_data or ad_data.get("status") != "ok":
        return {"status": "not_available"}
    rank_map = aba_rank_map or {}
    matched = 0
    for term in ad_data.get("all_terms", []):
        aba_info = rank_map.get(normalize_search_key(term.get("search_term")))
        if aba_info:
            term["aba_rank"] = aba_info.get("aba_rank")
            term["aba_source"] = {
                "sheet": aba_info.get("aba_source_sheet"),
                "rank_column": aba_info.get("aba_source_column"),
            }
            matched += 1
        elif term.get("aba_rank") is None:
            term["aba_rank"] = None
        term["market_volume_tier"] = market_volume_tier(term.get("aba_rank"))
        term["ad_exposure_tier"] = ad_exposure_tier(term.get("impressions"))
        term["data_sufficiency"] = data_sufficiency_for_term(term)
        term["promotion_priority"] = promotion_priority_for_term(term)
    ad_data["top_converting_terms"] = [
        term for term in ad_data.get("all_terms", []) if term.get("classification") == "converting"
    ][:50]
    ad_data["top_click_only_terms"] = [
        term for term in ad_data.get("all_terms", []) if term.get("classification") == "click_only"
    ][:50]
    terms_with_aba = sum(1 for term in ad_data.get("all_terms", []) if term.get("aba_rank") is not None)
    return {
        "status": "ok",
        "terms_with_aba_rank": terms_with_aba,
        "terms_matched_from_aba_file": matched,
        "market_volume_tiers": {
            "big/core": f"ABA rank <= {ABA_BIG_RANK_THRESHOLD}",
            "mid": f"ABA rank <= {ABA_MID_RANK_THRESHOLD}",
            "small/niche": f"ABA rank > {ABA_MID_RANK_THRESHOLD}",
            "unknown": "No ABA rank available",
        },
        "decision_rule": "ABA changes priority, not semantic truth: big/core terms can be steadied or diagnosed before abandoning; small/niche weak performers should not be promoted.",
    }


def new_metric_bucket(search_term: str = "") -> dict[str, Any]:
    return {
        "search_term": search_term,
        "orders": 0.0,
        "spend": 0.0,
        "clicks": 0.0,
        "impressions": 0.0,
        "ad_sales": 0.0,
        "sales_available": False,
        "rows": 0,
        "campaigns": set(),
        "ad_groups": set(),
        "match_types": set(),
        "campaign_types": set(),
        "targeting_expressions": set(),
        "date_values": set(),
        "translations_seen": set(),
        "aba_ranks": [],
        # Keep the latest usable source bid and scope provenance so every
        # executable recommendation can be traced back to an activity/ad group.
        "source_bid_candidates": [],
    }


def add_record_to_bucket(bucket: dict[str, Any], record: dict[str, Any]) -> None:
    orders = parse_number(record.get("广告订单量"))
    spend = parse_number(record.get("广告花费"))
    clicks = parse_number(record.get("广告点击量"))
    impressions = parse_number(record.get("广告曝光量"))
    bucket["orders"] += orders
    bucket["spend"] += spend
    bucket["clicks"] += clicks
    bucket["impressions"] += impressions
    ad_sales = parse_optional_number(record.get("广告销售额"))
    if ad_sales is not None:
        bucket["ad_sales"] += ad_sales
        bucket["sales_available"] = True
    else:
        average_order_value = parse_optional_number(record.get("广告笔单价"))
        if average_order_value is not None:
            bucket["ad_sales"] += average_order_value * orders
            bucket["sales_available"] = True
    for columns, key in [
        (CAMPAIGN_COLUMNS, "campaigns"),
        (MATCH_TYPE_COLUMNS, "match_types"),
        (CAMPAIGN_TYPE_COLUMNS, "campaign_types"),
        (TARGETING_COLUMNS, "targeting_expressions"),
    ]:
        value = first_text(record, columns)
        if value:
            bucket[key].add(value)
    translation = clean_text(record.get("用户搜索词翻译"))
    if translation:
        bucket["translations_seen"].add(translation)
    ad_group = first_text(record, AD_GROUP_COLUMNS)
    if ad_group:
        bucket["ad_groups"].add(ad_group)
    date_value = first_text(record, DATE_COLUMNS)
    if date_value:
        bucket["date_values"].add(date_value)
    for rank_column in ABA_RANK_COLUMNS:
        rank = parse_optional_number(record.get(rank_column))
        if rank is not None and rank > 0:
            bucket["aba_ranks"].append(rank)
    source_bid = parse_optional_number(record.get("源竞价"))
    if source_bid is not None and source_bid > 0:
        source_date = first_text(record, DATE_COLUMNS)
        campaign = first_text(record, CAMPAIGN_COLUMNS)
        ad_group = first_text(record, AD_GROUP_COLUMNS)
        targeting = first_text(record, TARGETING_COLUMNS)
        match_type = first_text(record, MATCH_TYPE_COLUMNS)
        bucket["source_bid_candidates"].append(
            {
                "source_bid": round(source_bid, 4),
                "date": source_date,
                "date_sort": normalized_date_sort_key(source_date, record.get("__marketplace")),
                "campaign": campaign,
                "ad_group": ad_group,
                "targeting": targeting,
                "match_type": match_type,
            }
        )
    bucket["rows"] += 1


def metrics_from_bucket(bucket: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    sales = bucket["ad_sales"] if bucket["sales_available"] else None
    ctr = pct(bucket["clicks"], bucket["impressions"])
    cvr = pct(bucket["orders"], bucket["clicks"])
    cpc = pct(bucket["spend"], bucket["clicks"])
    acos = pct(bucket["spend"], sales) if sales is not None else None
    roas = pct(sales, bucket["spend"]) if sales is not None else None
    item = {
        "search_term": bucket["search_term"],
        "aba_rank": maybe_int(min(bucket["aba_ranks"])) if bucket["aba_ranks"] else None,
        "orders": maybe_int(bucket["orders"]),
        "spend": round(bucket["spend"], 4),
        "clicks": maybe_int(bucket["clicks"]),
        "impressions": maybe_int(bucket["impressions"]),
        "ad_sales": round(sales, 4) if sales is not None else None,
        "ctr": ctr,
        "cvr": cvr,
        "cpc": cpc,
        "acos": acos,
        "roas": roas,
        "classification": "converting"
        if bucket["orders"] > 0
        else "click_only"
        if bucket["clicks"] > 0
        else "no_click",
        "rows_aggregated": bucket["rows"],
        "campaigns": sorted(bucket["campaigns"]),
        "ad_groups": sorted(bucket["ad_groups"]),
        "match_types": sorted(bucket["match_types"]),
        "campaign_types": sorted(bucket["campaign_types"]),
        "targeting_expressions": sorted(bucket["targeting_expressions"]),
        "date_values_seen": sorted(bucket["date_values"])[:5],
        "translations_seen_reference_only": sorted(bucket["translations_seen"])[:3],
    }
    source_candidates = list(bucket.get("source_bid_candidates") or [])
    source_candidates.sort(
        key=lambda row: (str(row.get("date_sort") or ""), str(row.get("date") or "")),
        reverse=True,
    )
    if source_candidates:
        latest = source_candidates[0]
        unique_source_bids = sorted({
            round(metric_float(candidate.get("source_bid")), 4)
            for candidate in source_candidates
            if metric_float(candidate.get("source_bid")) > 0
        })
        item.update(
            {
                "source_bid": latest.get("source_bid"),
                "source_bid_date": latest.get("date"),
                "source_bid_count": len(source_candidates),
                "source_bid_unique_count": len(unique_source_bids),
                "source_bid_stable": len(unique_source_bids) == 1,
                "source_campaign": latest.get("campaign"),
                "source_ad_group": latest.get("ad_group"),
                "source_targeting": latest.get("targeting"),
                "source_match_type": latest.get("match_type"),
                "source_bid_history": source_candidates[:5],
            }
        )
    if extra:
        item.update(extra)
    return item


def sort_terms(terms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(terms, key=lambda term: (-float(term["orders"]), -float(term["spend"]), term["search_term"]))


def aggregate_ad_data(tables: list[dict[str, Any]]) -> dict[str, Any]:
    ad_tables = [
        table
        for table in tables
        if all(col in table["headers"] for col in REQUIRED_AD_COLUMNS)
    ]
    if not ad_tables:
        return {
            "status": "missing_required_columns",
            "required_columns": REQUIRED_AD_COLUMNS,
            "available_headers": [table["headers"] for table in tables],
        }

    grouped: dict[str, dict[str, Any]] = defaultdict(new_metric_bucket)
    ad_group_columns = sorted({column for table in ad_tables for column in columns_present(table["headers"], AD_GROUP_COLUMNS)})
    date_columns = sorted({column for table in ad_tables for column in columns_present(table["headers"], DATE_COLUMNS)})
    match_type_columns = sorted({column for table in ad_tables for column in columns_present(table["headers"], MATCH_TYPE_COLUMNS)})
    campaign_type_columns = sorted({column for table in ad_tables for column in columns_present(table["headers"], CAMPAIGN_TYPE_COLUMNS)})
    targeting_columns = sorted({column for table in ad_tables for column in columns_present(table["headers"], TARGETING_COLUMNS)})

    total_rows = 0
    for table in ad_tables:
        for record in table["records"]:
            term = clean_text(record.get("用户搜索词"))
            if not term:
                continue
            total_rows += 1
            bucket = grouped[term]
            bucket["search_term"] = term
            add_record_to_bucket(bucket, record)

    terms = [metrics_from_bucket(bucket) for bucket in grouped.values()]

    converting = sorted(
        [term for term in terms if term["classification"] == "converting"],
        key=lambda term: (-float(term["orders"]), -float(term["spend"]), term["search_term"]),
    )
    click_only = sorted(
        [term for term in terms if term["classification"] == "click_only"],
        key=lambda term: (-float(term["spend"]), -float(term["clicks"]), term["search_term"]),
    )

    return {
        "status": "ok",
        "source_rows": total_rows,
        "unique_search_terms": len(terms),
        "ad_group_columns_found": ad_group_columns,
        "date_columns_found": date_columns,
        "match_type_columns_found": match_type_columns,
        "campaign_type_columns_found": campaign_type_columns,
        "targeting_columns_found": targeting_columns,
        "converting_count": len(converting),
        "click_only_count": len(click_only),
        "top_converting_terms": converting[:50],
        "top_click_only_terms": click_only[:50],
        "all_terms": sort_terms(terms),
    }


def _period_difference_failure(
    status: str,
    reason: str,
    overlap: dict[str, Any],
    **details: Any,
) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "original_overlap": overlap,
        **details,
    }


def derive_exclusive_baseline(
    baseline_data: dict[str, Any],
    recent_data: dict[str, Any],
    baseline_range: dict[str, Any] | None,
    recent_range: dict[str, Any] | None,
) -> dict[str, Any]:
    """Safely derive a prior-period baseline when a long report ends with the recent period."""
    overlap = period_overlap_context(baseline_range, recent_range)
    if overlap.get("status") != "overlap":
        return _period_difference_failure(
            "not_applicable",
            "The supplied periods do not have a known overlap that needs subtraction.",
            overlap,
        )
    if baseline_data.get("status") != "ok" or recent_data.get("status") != "ok":
        return _period_difference_failure(
            "fallback_invalid_ad_data",
            "One or both aggregated reports are unavailable, so subtraction was not attempted.",
            overlap,
        )
    try:
        baseline_start = date.fromisoformat(str((baseline_range or {})["start"]))
        baseline_end = date.fromisoformat(str((baseline_range or {})["end"]))
        recent_start = date.fromisoformat(str((recent_range or {})["start"]))
        recent_end = date.fromisoformat(str((recent_range or {})["end"]))
    except (KeyError, TypeError, ValueError):
        return _period_difference_failure(
            "fallback_invalid_date_range",
            "A supplied date range is invalid, so subtraction was not attempted.",
            overlap,
        )
    if baseline_end != recent_end or not (baseline_start < recent_start <= recent_end):
        return _period_difference_failure(
            "fallback_not_trailing_containment",
            (
                "Automatic subtraction requires the longer baseline to end on the same date as the "
                "recent report and to start earlier."
            ),
            overlap,
        )

    exclusive_end = recent_start - timedelta(days=1)
    exclusive_range = {
        "start": baseline_start.isoformat(),
        "end": exclusive_end.isoformat(),
        "source": "derived_baseline_minus_recent",
        "source_baseline_range": {
            "start": baseline_start.isoformat(),
            "end": baseline_end.isoformat(),
        },
        "subtracted_recent_range": {
            "start": recent_start.isoformat(),
            "end": recent_end.isoformat(),
        },
    }
    baseline_terms = term_map(baseline_data)
    recent_terms = term_map(recent_data)
    derived_terms: list[dict[str, Any]] = []
    checked_metric_values = 0

    for search_term in sorted(set(baseline_terms) | set(recent_terms)):
        baseline_term = baseline_terms.get(search_term, {})
        recent_term = recent_terms.get(search_term, {})
        derived_values: dict[str, float | None] = {}
        for metric in ["orders", "spend", "clicks", "impressions"]:
            baseline_value = metric_float(baseline_term.get(metric))
            recent_value = metric_float(recent_term.get(metric))
            tolerance = (
                PERIOD_DIFFERENCE_CURRENCY_TOLERANCE
                if metric == "spend"
                else PERIOD_DIFFERENCE_COUNT_TOLERANCE
            )
            checked_metric_values += 1
            if recent_value > baseline_value + tolerance:
                return _period_difference_failure(
                    "fallback_inconsistent_metrics",
                    "Recent additive metrics exceed the containing baseline report.",
                    overlap,
                    failed_term=search_term,
                    failed_metric=metric,
                    baseline_value=baseline_value,
                    recent_value=recent_value,
                    tolerance=tolerance,
                )
            derived_values[metric] = max(0.0, baseline_value - recent_value)

        baseline_sales_raw = baseline_term.get("ad_sales")
        recent_sales_raw = recent_term.get("ad_sales")
        if baseline_sales_raw is None and recent_sales_raw is None:
            derived_values["ad_sales"] = None
        else:
            baseline_sales = metric_float(baseline_sales_raw)
            if recent_sales_raw is None:
                if metric_float(recent_term.get("orders")) > 0:
                    return _period_difference_failure(
                        "fallback_inconsistent_sales",
                        "Recent orders exist without comparable attributed sales, so sales cannot be subtracted safely.",
                        overlap,
                        failed_term=search_term,
                    )
                recent_sales = 0.0
            else:
                recent_sales = metric_float(recent_sales_raw)
            checked_metric_values += 1
            if recent_sales > baseline_sales + PERIOD_DIFFERENCE_CURRENCY_TOLERANCE:
                return _period_difference_failure(
                    "fallback_inconsistent_metrics",
                    "Recent attributed sales exceed the containing baseline report.",
                    overlap,
                    failed_term=search_term,
                    failed_metric="ad_sales",
                    baseline_value=baseline_sales,
                    recent_value=recent_sales,
                    tolerance=PERIOD_DIFFERENCE_CURRENCY_TOLERANCE,
                )
            derived_values["ad_sales"] = max(0.0, baseline_sales - recent_sales)

        if not any(
            float(derived_values.get(metric) or 0) > PERIOD_DIFFERENCE_COUNT_TOLERANCE
            for metric in ["orders", "spend", "clicks", "impressions", "ad_sales"]
        ):
            continue

        item = dict(baseline_term)
        orders = float(derived_values["orders"] or 0)
        spend = float(derived_values["spend"] or 0)
        clicks = float(derived_values["clicks"] or 0)
        impressions = float(derived_values["impressions"] or 0)
        ad_sales = derived_values["ad_sales"]
        item.update(
            {
                "orders": maybe_int(orders),
                "spend": round(spend, 4),
                "clicks": maybe_int(clicks),
                "impressions": maybe_int(impressions),
                "ad_sales": round(float(ad_sales), 4) if ad_sales is not None else None,
                "ctr": pct(clicks, impressions),
                "cvr": pct(orders, clicks),
                "cpc": pct(spend, clicks),
                "acos": pct(spend, float(ad_sales)) if ad_sales is not None else None,
                "roas": pct(float(ad_sales), spend) if ad_sales is not None else None,
                "classification": "converting" if orders > 0 else "click_only" if clicks > 0 else "no_click",
                "date_values_seen": [],
                "period_derivation": "baseline_minus_recent",
            }
        )
        derived_terms.append(item)

    if not derived_terms:
        return _period_difference_failure(
            "fallback_empty_difference",
            "The subtraction produced no prior-period activity.",
            overlap,
        )

    converting = sorted(
        [term for term in derived_terms if term["classification"] == "converting"],
        key=lambda term: (-float(term["orders"]), -float(term["spend"]), term["search_term"]),
    )
    click_only = sorted(
        [term for term in derived_terms if term["classification"] == "click_only"],
        key=lambda term: (-float(term["spend"]), -float(term["clicks"]), term["search_term"]),
    )
    derived_data = {
        **baseline_data,
        "source_rows": None,
        "unique_search_terms": len(derived_terms),
        "converting_count": len(converting),
        "click_only_count": len(click_only),
        "top_converting_terms": converting[:50],
        "top_click_only_terms": click_only[:50],
        "all_terms": sort_terms(derived_terms),
        "period_derivation": {
            "method": "baseline_minus_recent",
            "comparison_baseline_date_range": exclusive_range,
            "checked_metric_values": checked_metric_values,
        },
    }
    return {
        "status": "success",
        "method": "baseline_minus_recent",
        "baseline_data": derived_data,
        "comparison_baseline_date_range": exclusive_range,
        "original_overlap": overlap,
        "checked_terms": len(set(baseline_terms) | set(recent_terms)),
        "checked_metric_values": checked_metric_values,
        "note": (
            "The recent suffix was safely subtracted from the longer cumulative report; additive metrics "
            "were validated term by term and rate metrics were recomputed from the differences."
        ),
    }


def aggregate_terms_by_dimension(
    tables: list[dict[str, Any]],
    dimension_columns: list[str],
    dimension_name: str,
) -> dict[str, Any]:
    ad_tables = [
        table
        for table in tables
        if all(col in table["headers"] for col in REQUIRED_AD_COLUMNS)
    ]
    found_columns = sorted({column for table in ad_tables for column in columns_present(table["headers"], dimension_columns)})
    if not ad_tables:
        return {"status": "missing_required_columns", "required_columns": REQUIRED_AD_COLUMNS}
    if not found_columns:
        return {"status": "not_available", "dimension": dimension_name, "columns_found": []}

    grouped: dict[tuple[str, str], dict[str, Any]] = defaultdict(new_metric_bucket)
    for table in ad_tables:
        for record in table["records"]:
            term = clean_text(record.get("用户搜索词"))
            dimension = first_text(record, dimension_columns)
            if not term or not dimension:
                continue
            bucket = grouped[(term, dimension)]
            bucket["search_term"] = term
            add_record_to_bucket(bucket, record)

    terms = []
    for (term, dimension), bucket in grouped.items():
        terms.append(metrics_from_bucket(bucket, {dimension_name: dimension}))
    return {
        "status": "ok",
        "dimension": dimension_name,
        "columns_found": found_columns,
        "unique_pairs": len(terms),
        "top_pairs": sort_terms(terms)[:80],
        "all_pairs": sort_terms(terms),
    }


def aggregate_terms_by_scope(tables: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate by the complete executable source scope used for conflict actions."""
    ad_tables = [
        table
        for table in tables
        if all(col in table["headers"] for col in REQUIRED_AD_COLUMNS)
    ]
    campaign_columns = sorted(
        {column for table in ad_tables for column in columns_present(table["headers"], CAMPAIGN_COLUMNS)}
    )
    ad_group_columns = sorted(
        {column for table in ad_tables for column in columns_present(table["headers"], AD_GROUP_COLUMNS)}
    )
    targeting_columns = sorted(
        {column for table in ad_tables for column in columns_present(table["headers"], TARGETING_COLUMNS)}
    )
    if not ad_tables:
        return {"status": "missing_required_columns", "required_columns": REQUIRED_AD_COLUMNS}
    if not campaign_columns or not ad_group_columns or not targeting_columns:
        return {
            "status": "not_available",
            "dimension": "scope",
            "campaign_columns_found": campaign_columns,
            "ad_group_columns_found": ad_group_columns,
            "targeting_columns_found": targeting_columns,
        }

    grouped: dict[tuple[str, str, str, str, str], dict[str, Any]] = defaultdict(new_metric_bucket)
    incomplete_scope_rows: list[dict[str, Any]] = []
    for table in ad_tables:
        for record in table["records"]:
            term = clean_text(record.get("用户搜索词"))
            campaign = first_text(record, CAMPAIGN_COLUMNS)
            ad_group = first_text(record, AD_GROUP_COLUMNS)
            targeting = first_text(record, TARGETING_COLUMNS)
            match_type = first_text(record, MATCH_TYPE_COLUMNS)
            match_family = source_match_family(match_type or targeting)
            if not term:
                continue
            missing = [
                name
                for name, value in [("campaign", campaign), ("ad_group", ad_group), ("targeting", targeting)]
                if not value
            ]
            if missing:
                incomplete_scope_rows.append({
                    "file": record.get("__source_file") or table.get("source_file"),
                    "sheet": record.get("__source_sheet") or table.get("sheet"),
                    "row": record.get("__source_row"),
                    "search_term": term,
                    "missing": missing,
                })
                continue
            bucket = grouped[(term, campaign, ad_group, targeting, match_family)]
            bucket["search_term"] = term
            add_record_to_bucket(bucket, record)

    if incomplete_scope_rows:
        return {
            "status": "invalid_source_scope_rows",
            "dimension": "scope",
            "campaign_columns_found": campaign_columns,
            "ad_group_columns_found": ad_group_columns,
            "targeting_columns_found": targeting_columns,
            "invalid_rows": incomplete_scope_rows[:20],
        }

    rows = []
    for (term, campaign, ad_group, targeting, match_family), bucket in grouped.items():
        scope_parts = [campaign, ad_group]
        if targeting:
            scope_parts.append(targeting)
        scope_parts.append(match_family)
        rows.append(
            metrics_from_bucket(
                bucket,
                {
                    "scope": " :: ".join(scope_parts),
                    "campaign": campaign,
                    "ad_group": ad_group,
                    "source_campaign": campaign,
                    "source_ad_group": ad_group,
                    "source_targeting": targeting or None,
                    "source_match_type": sorted(bucket["match_types"])[0] if bucket["match_types"] else None,
                    "source_match_family": match_family,
                },
            )
        )
    return {
        "status": "ok",
        "dimension": "scope",
        "campaign_columns_found": campaign_columns,
        "ad_group_columns_found": ad_group_columns,
        "targeting_columns_found": targeting_columns,
        "unique_pairs": len(rows),
        "top_pairs": sort_terms(rows)[:80],
        "all_pairs": sort_terms(rows),
    }


def _scope_metrics(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in [
            "scope", "campaign", "ad_group", "orders", "spend", "clicks",
            "impressions", "ctr", "ad_sales", "cvr", "cpc", "acos", "roas",
            "match_types", "campaign_types", "targeting_expressions", "source_campaign",
            "source_ad_group", "source_targeting", "source_match_type", "source_match_family",
        ]
        if item.get(key) is not None
    }


def build_scoped_conflict_evidence(
    scope_data: dict[str, Any],
    period_comparison: dict[str, Any] | None = None,
    profit_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Precompute complete good/weak scope evidence so no second model read is needed."""
    if scope_data.get("status") != "ok":
        return []
    by_term: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in scope_data.get("all_pairs", []):
        by_term[str(item.get("search_term") or "")].append(item)

    category_lookup = period_category_map(period_comparison or {})
    thresholds = (profit_context or {}).get("zero_order_spend_thresholds") or {}
    strong_spend = metric_float(thresholds.get("strong_action_spend"))
    critical_spend = metric_float(thresholds.get("critical_waste_spend"))
    conflicts: list[dict[str, Any]] = []
    for term, items in by_term.items():
        if not term:
            continue
        good = [item for item in items if metric_float(item.get("orders")) > 0]
        weak = [
            item
            for item in items
            if metric_float(item.get("orders")) == 0
            and metric_float(item.get("clicks")) > 0
            and metric_float(item.get("spend")) > 0
        ]
        if not good or not weak:
            continue

        good_sorted = sorted(
            good,
            key=lambda item: (
                -metric_float(item.get("orders")),
                metric_float(item.get("acos")) if item.get("acos") is not None else 999,
                str(item.get("scope") or ""),
            ),
        )[:5]
        weak_sorted = sorted(
            weak,
            key=lambda item: (
                -metric_float(item.get("spend")),
                -metric_float(item.get("clicks")),
                str(item.get("scope") or ""),
            ),
        )[:5]
        weak_spend = sum(metric_float(item.get("spend")) for item in weak)
        weak_clicks = sum(metric_float(item.get("clicks")) for item in weak)
        if weak_clicks > CLICK_ONLY_OUTPUT_CLICK_THRESHOLD or (
            critical_spend > 0 and weak_spend >= critical_spend
        ):
            urgency = "high"
            weak_action = "ad_group_negative_exact_or_bid_down_after_semantic_review"
        elif weak_clicks >= 6 or (strong_spend > 0 and weak_spend >= strong_spend):
            urgency = "medium"
            weak_action = "ad_group_bid_down_or_observe"
        else:
            urgency = "low"
            weak_action = "observe_weak_scope"
        conflicts.append(
            {
                "search_term": term,
                "period_category": category_lookup.get(term),
                "good_scopes": [_scope_metrics(item) for item in good_sorted],
                "weak_scopes": [_scope_metrics(item) for item in weak_sorted],
                "good_orders": maybe_int(sum(metric_float(item.get("orders")) for item in good)),
                "good_spend": round(sum(metric_float(item.get("spend")) for item in good), 4),
                "weak_spend": round(weak_spend, 4),
                "weak_clicks": maybe_int(weak_clicks),
                "global_negative_blocked": True,
                "preserve_good_scopes": True,
                "recommended_scope": "weak_campaign_and_ad_group_only",
                "recommended_weak_scope_action": weak_action,
                "semantic_review_required_before_negative": "negative" in weak_action,
                "urgency": urgency,
                "rule": "Any converting scope blocks global negatives; preserve good scopes and act only on weak campaign/ad-group scopes.",
            }
        )
    urgency_rank = {"high": 0, "medium": 1, "low": 2}
    return sorted(
        conflicts,
        key=lambda item: (
            urgency_rank.get(str(item.get("urgency")), 9),
            -metric_float(item.get("weak_spend")),
            -metric_float(item.get("good_orders")),
            str(item.get("search_term")),
        ),
    )[:30]


def find_ad_group_conflicts(ad_group_data: dict[str, Any]) -> list[dict[str, Any]]:
    if ad_group_data.get("status") != "ok":
        return []
    by_term: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in ad_group_data.get("all_pairs", []):
        by_term[item["search_term"]].append(item)

    conflicts = []
    for term, items in by_term.items():
        good = [
            item for item in items
            if float(item.get("orders") or 0) > 0
        ]
        weak = [
            item for item in items
            if float(item.get("orders") or 0) == 0
            and float(item.get("clicks") or 0) > 0
            and float(item.get("spend") or 0) > 0
        ]
        if not good or not weak:
            continue
        weak_sorted = sorted(weak, key=lambda item: (-float(item.get("spend") or 0), -float(item.get("clicks") or 0)))[:5]
        good_sorted = sorted(good, key=lambda item: (-float(item.get("orders") or 0), float(item.get("acos") or 999)))[:5]
        conflicts.append(
            {
                "search_term": term,
                "good_ad_groups": [
                    {
                        "ad_group": item.get("ad_group"),
                        "orders": item.get("orders"),
                        "spend": item.get("spend"),
                        "acos": item.get("acos"),
                        "cvr": item.get("cvr"),
                    }
                    for item in good_sorted
                ],
                "weak_ad_groups": [
                    {
                        "ad_group": item.get("ad_group"),
                        "spend": item.get("spend"),
                        "clicks": item.get("clicks"),
                        "cpc": item.get("cpc"),
                    }
                    for item in weak_sorted
                ],
                "recommendation_hint": "Do not make a broad/global negative from this term; keep or scale the good ad group and review bid or negative exact only in weak ad groups.",
                "weak_spend": round(sum(float(item.get("spend") or 0) for item in weak), 4),
                "good_orders": sum(float(item.get("orders") or 0) for item in good),
            }
        )
    return sorted(conflicts, key=lambda item: (-item["weak_spend"], -item["good_orders"], item["search_term"]))[:30]


def term_map(ad_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if ad_data.get("status") != "ok":
        return {}
    return {item["search_term"]: item for item in ad_data.get("all_terms", [])}


def compare_periods(baseline_data: dict[str, Any], recent_data: dict[str, Any]) -> dict[str, Any]:
    baseline = term_map(baseline_data)
    recent = term_map(recent_data)
    if not baseline or not recent:
        return {"status": "not_available", "reason": "One or both periods have no usable ad data."}

    rows = []
    for term in sorted(set(baseline) | set(recent)):
        old = baseline.get(term, {})
        new = recent.get(term, {})
        old_orders = float(old.get("orders") or 0)
        new_orders = float(new.get("orders") or 0)
        old_clicks = float(old.get("clicks") or 0)
        new_clicks = float(new.get("clicks") or 0)
        old_spend = float(old.get("spend") or 0)
        new_spend = float(new.get("spend") or 0)
        old_acos = old.get("acos")
        new_acos = new.get("acos")
        old_cvr = old.get("cvr")
        new_cvr = new.get("cvr")
        category = "mixed_or_low_signal"
        if old_orders > 0 and new_orders > 0:
            category = "consistently_converting"
            if old_acos is not None and new_acos is not None and float(new_acos) > float(old_acos) * 1.5:
                category = "recent_efficiency_decline"
        elif old_orders > 0 and new_orders == 0:
            category = (
                "historically_good_recently_weak"
                if new_clicks > 0 or new_spend > 0
                else "historically_good_no_recent_traffic"
            )
        elif old_orders == 0 and new_orders > 0:
            category = "recently_improved"
        elif old_orders == 0 and new_orders == 0 and (old_clicks + new_clicks) > 0:
            category = "consistently_click_only"
        rows.append(
            {
                "search_term": term,
                "category": category,
                "baseline": {
                    "orders": old.get("orders", 0),
                    "spend": old.get("spend", 0),
                    "clicks": old.get("clicks", 0),
                    "acos": old_acos,
                    "cvr": old_cvr,
                    "campaigns": old.get("campaigns", []),
                    "ad_groups": old.get("ad_groups", []),
                    "match_types": old.get("match_types", []),
                    "campaign_types": old.get("campaign_types", []),
                    "targeting_expressions": old.get("targeting_expressions", []),
                },
                "recent": {
                    "orders": new.get("orders", 0),
                    "spend": new.get("spend", 0),
                    "clicks": new.get("clicks", 0),
                    "acos": new_acos,
                    "cvr": new_cvr,
                    "campaigns": new.get("campaigns", []),
                    "ad_groups": new.get("ad_groups", []),
                    "match_types": new.get("match_types", []),
                    "campaign_types": new.get("campaign_types", []),
                    "targeting_expressions": new.get("targeting_expressions", []),
                },
                "recent_spend": new_spend,
                "recent_clicks": new_clicks,
                "baseline_orders": old_orders,
                "recent_orders": new_orders,
            }
        )

    def top(category: str, limit: int = 20) -> list[dict[str, Any]]:
        candidates = [row for row in rows if row["category"] == category]
        return sorted(
            candidates,
            key=lambda row: (
                -row["recent_spend"],
                -row["recent_clicks"],
                -row["baseline_orders"],
                -metric_float((row.get("baseline") or {}).get("spend")),
                row["search_term"],
            ),
        )[:limit]

    return {
        "status": "ok",
        "terms_compared": len(rows),
        "_all_rows": rows,
        "categories": {
            "historically_good_recently_weak": top("historically_good_recently_weak"),
            "historically_good_no_recent_traffic": top("historically_good_no_recent_traffic"),
            "recent_efficiency_decline": top("recent_efficiency_decline"),
            "recently_improved": top("recently_improved"),
            "consistently_converting": top("consistently_converting"),
            "consistently_click_only": top("consistently_click_only"),
        },
        "decision_rule": "When a term was historically good but recently weak or has no recent traffic, show its baseline orders/spend/ACoS and prefer coverage, bid, budget, or listing diagnostics before negatives. Use negatives more confidently when both periods are consistently click-only or semantically mismatched.",
    }


def period_all_rows(period_comparison: dict[str, Any]) -> list[dict[str, Any]]:
    internal_rows = period_comparison.get("_all_rows")
    if isinstance(internal_rows, list):
        return internal_rows
    return [
        row
        for rows in (period_comparison.get("categories") or {}).values()
        for row in rows or []
    ]


def compact_period_comparison_for_package(period_comparison: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: value
        for key, value in period_comparison.items()
        if key not in {"_all_rows", "categories"}
    }
    compact["categories"] = {
        category: [
            {
                "search_term": row.get("search_term"),
                "category": row.get("category"),
                "baseline": {
                    key: (row.get("baseline") or {}).get(key)
                    for key in ["orders", "spend", "clicks", "acos", "cvr"]
                },
                "recent": {
                    key: (row.get("recent") or {}).get(key)
                    for key in ["orders", "spend", "clicks", "acos", "cvr"]
                },
            }
            for row in (rows or [])[:10]
        ]
        for category, rows in (period_comparison.get("categories") or {}).items()
    }
    return compact


def summarize_product_dashboard(tables: list[dict[str, Any]]) -> dict[str, Any]:
    if not tables:
        return {"status": "not_supplied"}

    summaries = []
    for table in tables:
        records = table["records"]
        headers = set(table["headers"])
        important = [column for column in PRODUCT_IMPORTANT_COLUMNS if column in headers]
        totals: dict[str, float] = {}
        latest: dict[str, Any] = {}
        recent_rows = []
        date_values = []

        for record in records:
            label = clean_text(record.get("日期/合计"))
            if label:
                date_values.append(label)
            for column in PRODUCT_ADDITIVE_COLUMNS:
                if column in record:
                    totals[column] = totals.get(column, 0.0) + parse_number(record.get(column))
            compact = {column: record.get(column) for column in important if column in record}
            if any(value not in (None, "") for value in compact.values()):
                latest = compact
                recent_rows.append(compact)

        derived = {}
        if totals.get("Sessions"):
            derived["period_order_conversion_rate"] = pct(totals.get("订单量", 0.0), totals["Sessions"])
        if totals.get("广告点击量"):
            derived["period_ad_conversion_rate"] = pct(totals.get("广告订单量", 0.0), totals["广告点击量"])
            derived["period_cpc"] = pct(totals.get("广告花费", 0.0), totals["广告点击量"])
        if totals.get("广告销售额"):
            derived["period_acos"] = pct(totals.get("广告花费", 0.0), totals["广告销售额"])
        if totals.get("广告花费"):
            derived["period_roas"] = pct(totals.get("广告销售额", 0.0), totals["广告花费"])
        if totals.get("销售额"):
            derived["period_acoas_ad_spend_to_total_sales"] = pct(totals.get("广告花费", 0.0), totals["销售额"])
            derived["period_ad_sales_share"] = pct(totals.get("广告销售额", 0.0), totals["销售额"])

        summaries.append(
            {
                "sheet": table["sheet"],
                "headers_found": table["headers"],
                "row_count": len(records),
                "important_columns_found": important,
                "date_range_observed": [date_values[0], date_values[-1]] if date_values else None,
                "period_totals_for_additive_columns": {k: round(v, 4) for k, v in sorted(totals.items())},
                "derived_period_metrics": derived,
                "latest_observed_row": latest,
                "recent_rows": recent_rows[-7:],
            }
        )
    return {"status": "ok", "sheets": summaries}


def build_prompt(output_dir: Path, package_path: Path) -> dict[str, str]:
    """Write the compact runtime payload, deterministic draft, and short prompt."""
    spec = importlib.util.spec_from_file_location("amazon_ads_report_renderer", REPORT_RENDERER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load report renderer: {REPORT_RENDERER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.write_runtime_files(package_path, output_dir)


def write_search_term_files(output_dir: Path, ad_data: dict[str, Any], prefix: str = "search_terms_aggregated") -> dict[str, str] | None:
    if ad_data.get("status") != "ok":
        return None
    all_terms = ad_data.get("all_terms", [])
    if not all_terms:
        return None

    jsonl_path = output_dir / f"{prefix}.jsonl"
    csv_path = output_dir / f"{prefix}.csv"
    csv_fields = [
        "search_term",
        "aba_rank",
        "market_volume_tier",
        "ad_exposure_tier",
        "data_sufficiency",
        "promotion_priority",
        "period_category",
        "classification",
        "orders",
        "spend",
        "clicks",
        "impressions",
        "ctr",
        "ctr_signal",
        "relative_ctr",
        "ctr_baseline_scope",
        "ctr_baseline_confidence",
        "ad_sales",
        "cvr",
        "cpc",
        "acos",
        "roas",
        "max_cpc",
        "baseline_max_cpc",
        "min_acos",
        "required_cvr",
        "bid_cvr_used",
        "bid_cvr_source",
        "bid_cvr_confidence",
        "bid_cvr_note",
        "cpc_decision_mode",
        "cpc_decision_note",
        "traffic_action_mode",
        "traffic_action_note",
        "cvr_health",
        "bid_feasibility_note",
        "rows_aggregated",
        "campaigns",
        "ad_groups",
        "match_types",
        "campaign_types",
        "targeting_expressions",
        "campaign",
        "ad_group",
        "scope",
        "source_bid",
        "source_bid_date",
        "source_campaign",
        "source_ad_group",
        "source_targeting",
        "source_match_type",
        "source_match_family",
    ]

    with jsonl_path.open("w", encoding="utf-8") as handle:
        for term in all_terms:
            handle.write(json.dumps(term, ensure_ascii=False, default=json_default) + "\n")

    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fields)
        writer.writeheader()
        for term in all_terms:
            row = {}
            for field in csv_fields:
                value = term.get(field)
                ctr_context = term.get("ctr_context") or {}
                if value is None and field == "ctr_signal":
                    value = ctr_context.get("ctr_signal")
                if value is None and field == "relative_ctr":
                    value = ctr_context.get("relative_ctr")
                if value is None and field == "ctr_baseline_scope":
                    value = ctr_context.get("baseline_scope")
                if value is None and field == "ctr_baseline_confidence":
                    value = ctr_context.get("baseline_confidence")
                if value is None and field in {
                    "max_cpc",
                    "baseline_max_cpc",
                    "min_acos",
                    "required_cvr",
                    "bid_cvr_used",
                    "bid_cvr_source",
                    "bid_cvr_confidence",
                    "bid_cvr_note",
                    "cpc_decision_mode",
                    "cpc_decision_note",
                    "traffic_action_mode",
                    "traffic_action_note",
                    "cvr_health",
                    "bid_feasibility_note",
                }:
                    value = (term.get("bid_economics") or {}).get(field)
                if isinstance(value, list):
                    value = "; ".join(str(item) for item in value)
                row[field] = value
            writer.writerow(row)

    return {
        "jsonl": str(jsonl_path),
        "csv": str(csv_path),
    }


def write_dimension_files(
    output_dir: Path,
    dimension_data: dict[str, Any],
    prefix: str,
    dimension_name: str,
) -> dict[str, str] | None:
    if dimension_data.get("status") != "ok":
        return None
    all_pairs = dimension_data.get("all_pairs", [])
    if not all_pairs:
        return None

    jsonl_path = output_dir / f"{prefix}.jsonl"
    csv_path = output_dir / f"{prefix}.csv"
    csv_fields = [
        "search_term",
        dimension_name,
        "aba_rank",
        "classification",
        "orders",
        "spend",
        "clicks",
        "impressions",
        "ctr",
        "ad_sales",
        "cvr",
        "cpc",
        "acos",
        "roas",
        "rows_aggregated",
        "campaigns",
        "match_types",
        "campaign_types",
        "targeting_expressions",
        "campaign",
        "ad_group",
        "scope",
        "source_bid",
        "source_bid_date",
        "source_bid_count",
        "source_campaign",
        "source_ad_group",
        "source_targeting",
        "source_match_type",
        "source_match_family",
    ]

    with jsonl_path.open("w", encoding="utf-8") as handle:
        for item in all_pairs:
            handle.write(json.dumps(item, ensure_ascii=False, default=json_default) + "\n")

    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fields)
        writer.writeheader()
        for item in all_pairs:
            row = {}
            for field in csv_fields:
                value = item.get(field)
                if isinstance(value, list):
                    value = "; ".join(str(part) for part in value)
                row[field] = value
            writer.writerow(row)

    return {
        "jsonl": str(jsonl_path),
        "csv": str(csv_path),
    }


def enrich_ad_data_with_bid_economics(
    ad_data: dict[str, Any] | None,
    target_acos: float | None,
    aov: float | None,
    cvr_baseline: dict[str, Any],
    term_period_categories: dict[str, str] | None = None,
    account_cpc_context: dict[str, Any] | None = None,
) -> None:
    if not ad_data or ad_data.get("status") != "ok":
        return
    term_period_categories = term_period_categories or {}
    for term in ad_data.get("all_terms", []):
        period_category = term_period_categories.get(clean_text(term.get("search_term")))
        term["period_category"] = period_category
        term["bid_economics"] = bid_economics_for_term(
            term,
            target_acos,
            aov,
            cvr_baseline,
            period_category=period_category,
            account_cpc_context=account_cpc_context,
        )
    ad_data["top_converting_terms"] = [
        term for term in ad_data.get("all_terms", []) if term.get("classification") == "converting"
    ][:50]
    ad_data["top_click_only_terms"] = [
        term for term in ad_data.get("all_terms", []) if term.get("classification") == "click_only"
    ][:50]


def compact_term_for_package(term: dict[str, Any]) -> dict[str, Any]:
    fields = [
        "search_term",
        "translations_seen_reference_only",
        "classification",
        "orders",
        "spend",
        "clicks",
        "impressions",
        "ctr",
        "ad_sales",
        "cvr",
        "cpc",
        "acos",
        "roas",
        "aba_rank",
        "market_volume_tier",
        "ad_exposure_tier",
        "data_sufficiency",
        "promotion_priority",
        "action_eligibility",
        "local_conversion_confidence",
        "abnormal_attribution_sample",
        "scale_blockers",
        "profit_acos_cap",
        "period_category",
        "campaigns",
        "ad_groups",
        "match_types",
        "campaign_types",
        "targeting_expressions",
        "campaign",
        "ad_group",
        "scope",
        "source_bid",
        "source_bid_date",
        "source_bid_count",
        "source_campaign",
        "source_ad_group",
        "source_targeting",
        "source_match_type",
        "source_match_family",
    ]
    compact = {field: term.get(field) for field in fields if term.get(field) is not None}
    ctr_context = term.get("ctr_context") or {}
    compact["ctr_context"] = {
        key: ctr_context.get(key)
        for key in ["ctr_signal", "relative_ctr", "baseline_scope", "baseline_confidence"]
        if ctr_context.get(key) is not None
    }
    bid_economics = term.get("bid_economics") or {}
    compact["bid_economics"] = {
        key: bid_economics.get(key)
        for key in [
            "max_cpc",
            "baseline_max_cpc",
            "min_acos",
            "required_cvr",
            "bid_cvr_used",
            "bid_cvr_source",
            "bid_cvr_confidence",
            "cpc_decision_mode",
            "traffic_action_mode",
            "cvr_health",
        ]
        if bid_economics.get(key) is not None
    }
    daily_evidence = term.get("daily_action_evidence") or {}
    active_daily_signal = daily_evidence.get("scale_signal") or daily_evidence.get("zero_order_signal")
    if active_daily_signal:
        compact["daily_action_evidence"] = {
            key: active_daily_signal.get(key)
            for key in [
                "signal", "days", "consecutive_days", "orders", "spend", "clicks", "acos", "cvr",
                "expected_orders", "zero_order_probability", "allowable_spend_per_order", "spend_multiple",
                "longer_window_consistent", "recommended_action", "suggested_bid_change", "confidence", "reason",
            ]
            if active_daily_signal.get(key) is not None
        }
    return compact


def compact_ad_data_for_package(ad_data: dict[str, Any]) -> dict[str, Any]:
    if ad_data.get("status") != "ok":
        return ad_data
    priority_converting_terms = sorted(
        [
            term for term in ad_data.get("all_terms", [])
            if term.get("classification") == "converting"
            and term.get("action_eligibility") == "scale_ready"
        ],
        key=lambda term: (
            -metric_float(term.get("orders")),
            metric_float(term.get("acos")) if term.get("acos") is not None else 999,
            -metric_float(term.get("clicks")),
            clean_text(term.get("search_term")),
        ),
    )
    return {
        key: value
        for key, value in ad_data.items()
        if key not in {"all_terms", "top_converting_terms", "top_click_only_terms"}
    } | {
        "top_converting_terms": [compact_term_for_package(term) for term in ad_data.get("top_converting_terms", [])[:10]],
        "all_converting_terms": [
            compact_term_for_package(term)
            for term in ad_data.get("all_terms", [])
            if term.get("classification") == "converting"
        ],
        "priority_converting_terms": [
            compact_term_for_package(term) for term in priority_converting_terms
        ],
        "top_click_only_terms": [compact_term_for_package(term) for term in ad_data.get("top_click_only_terms", [])[:10]],
        "full_terms_note": "Full aggregated search terms are stored outside this compact JSON in search_terms_aggregated.csv and search_terms_aggregated.jsonl.",
    }


def compact_dimension_for_package(dimension_data: dict[str, Any], conflict_terms: list[dict[str, Any]]) -> dict[str, Any]:
    if dimension_data.get("status") != "ok":
        return dimension_data
    return {
        key: value
        for key, value in dimension_data.items()
        if key not in {"all_pairs", "top_pairs"}
    } | {
        "top_pairs": [compact_term_for_package(pair) for pair in dimension_data.get("top_pairs", [])[:10]],
        "conflict_terms": conflict_terms,
        "full_pairs_note": "Full dimension-level search terms are stored in the sidecar CSV/JSONL files.",
    }


def compact_scope_analysis_for_package(
    scope_data: dict[str, Any],
    conflict_terms: list[dict[str, Any]],
) -> dict[str, Any]:
    """Keep every executable source scope in-package; sidecars retain raw audit detail."""
    if scope_data.get("status") != "ok":
        return scope_data
    return {
        key: value
        for key, value in scope_data.items()
        if key not in {"all_pairs", "top_pairs"}
    } | {
        "all_pairs": [
            compact_term_for_package(pair)
            for pair in scope_data.get("all_pairs", [])
        ],
        "top_pairs": [compact_term_for_package(pair) for pair in scope_data.get("top_pairs", [])[:30]],
        "conflict_count": len(conflict_terms),
        "conflict_evidence_path": "scoped_conflict_evidence",
        "full_pairs_note": "Raw campaign/ad-group scope rows are also stored in search_terms_by_scope.csv/jsonl.",
    }


def metric_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def select_concrete_bid_change_pct(value: Any, confidence: Any = None) -> float:
    """Choose one executable percentage from a textual bid-change range."""
    text = clean_text(value)
    numbers = [float(item) for item in re.findall(r"[+-]?\d+(?:\.\d+)?", text)]
    if "保持" in text and 0.0 not in numbers:
        numbers.append(0.0)
    if not numbers:
        return 0.0
    confidence_text = clean_text(confidence).lower()
    low, high = min(numbers), max(numbers)
    if low < 0:
        if confidence_text == "high":
            return low
        if confidence_text == "low":
            return high
        return round((low + high) / 2, 2)
    if confidence_text == "high":
        return high
    if confidence_text == "low":
        return low
    return round((low + high) / 2, 2)


def concrete_target_bid(
    source_bid: Any,
    suggested_change: Any,
    confidence: Any = None,
    cpc_cap: Any = None,
) -> tuple[float | None, float]:
    """Backward-compatible percentage target; CPC caps never hard-clamp old base bids."""
    plan = existing_campaign_bid_plan(
        source_bid=source_bid,
        suggested_change=suggested_change,
        confidence=confidence,
        target_cpc=cpc_cap,
        actual_cpc=None,
        clicks=0,
        source_bid_stable=False,
    )
    return plan["target_bid"], plan["selected_bid_change_pct"]


def existing_campaign_bid_plan(
    source_bid: Any,
    suggested_change: Any,
    confidence: Any = None,
    target_cpc: Any = None,
    actual_cpc: Any = None,
    clicks: Any = None,
    source_bid_stable: bool = False,
) -> dict[str, Any]:
    """Plan one old-campaign base bid without confusing CPC targets with base bids."""
    source = metric_float(source_bid)
    selected_pct = select_concrete_bid_change_pct(suggested_change, confidence)
    target_cpc_value = metric_float(target_cpc)
    actual_cpc_value = metric_float(actual_cpc)
    click_count = metric_float(clicks)
    result = {
        "target_bid": None,
        "selected_bid_change_pct": selected_pct,
        "actual_bid_change_pct": None,
        "required_bid_change_pct": None,
        "theoretical_base_bid": None,
        "old_campaign_target_cpc": round(target_cpc_value, 2) if target_cpc_value > 0 else None,
        "bid_plan_method": "unavailable",
        "cpc_to_base_ratio": None,
    }
    if source <= 0:
        return result

    percentage_target = source * (1 + selected_pct / 100)
    final_target = percentage_target
    method = "percentage_step"
    ratio_eligible = bool(
        selected_pct < 0
        and target_cpc_value > 0
        and actual_cpc_value > target_cpc_value
        and click_count >= 3
        and source_bid_stable
    )
    if ratio_eligible:
        theoretical_target = source * target_cpc_value / actual_cpc_value
        # The selected percentage is the maximum permitted move this cycle.
        # Approach the CPC target in stages instead of hard-clamping the base bid.
        final_target = max(theoretical_target, percentage_target)
        final_target = min(final_target, source)
        result["required_bid_change_pct"] = round((theoretical_target / source - 1) * 100, 2)
        result["theoretical_base_bid"] = round(max(theoretical_target, 0.02), 2)
        result["cpc_to_base_ratio"] = round(actual_cpc_value / source, 4)
        method = "cpc_ratio_step_limited"

    final_target = round(max(final_target, 0.02), 2)
    result.update({
        "target_bid": final_target,
        "actual_bid_change_pct": round((final_target / source - 1) * 100, 2),
        "bid_plan_method": method,
    })
    return result


def existing_campaign_pause_reason(
    *,
    orders: Any,
    clicks: Any,
    acos: Any,
    profit_acos_cap: Any,
    required_bid_change_pct: Any,
    daily_signal: Any,
    daily_spend_multiple: Any,
    daily_longer_window_consistent: Any,
) -> str | None:
    """Pause only when a staged bid would otherwise create a proven loss-making zombie."""
    required_change = metric_float(required_bid_change_pct)
    if required_change >= -30 or not bool(daily_longer_window_consistent):
        return None
    order_count = metric_float(orders)
    click_count = metric_float(clicks)
    acos_value = metric_float(acos) if acos is not None else None
    profit_cap = metric_float(profit_acos_cap) or None
    if (
        order_count <= 0
        and click_count >= 6
        and str(daily_signal or "") == "persistent_zero_order"
        and metric_float(daily_spend_multiple) >= 2
    ):
        return "persistent_zero_order_extreme_repair_pause_scope"
    if (
        order_count >= 10 or click_count >= 40
    ) and acos_value is not None and profit_cap and acos_value > profit_cap * 2.5:
        return "stable_high_volume_extreme_loss_pause_scope"
    return None


def normalize_campaign_name(value: Any) -> str:
    """Normalize only whitespace/case for exact campaign joins; never fuzzy-match names."""
    return re.sub(r"\s+", " ", clean_text(value)).strip().casefold()


def normalize_placement_name(value: Any) -> str | None:
    text = clean_text(value).casefold()
    if not text:
        return None
    if any(token in text for token in [
        "顶部", "首页", "top of search", "top", "oberste suchergebnisse", "erste seite",
    ]):
        return "top_of_search"
    if any(token in text for token in ["其余", "rest of search", "rest", "übrige suchergebnisse"]):
        return "rest_of_search"
    if any(token in text for token in [
        "产品页面", "商品页面", "product pages", "product page", "produktseiten", "produktseite",
    ]):
        return "product_pages"
    return None


def placement_adjustment_percent(value: Any) -> float | None:
    if value is None or clean_text(value) == "":
        return None
    number = metric_float(value)
    if abs(number) <= 1:
        number *= 100
    return round(number, 2)


def placement_columns(headers: list[str]) -> dict[str, str | None]:
    def first(candidates: list[str]) -> str | None:
        for candidate in candidates:
            if candidate in headers:
                return candidate
        return None

    return {
        "date": first(PLACEMENT_DATE_COLUMNS),
        "shop": first(PLACEMENT_SHOP_COLUMNS),
        "campaign": first(PLACEMENT_CAMPAIGN_COLUMNS),
        "placement": first(PLACEMENT_NAME_COLUMNS),
        "strategy": first(PLACEMENT_STRATEGY_COLUMNS),
        "adjustment": first(PLACEMENT_ADJUSTMENT_COLUMNS),
        "impressions": first(PLACEMENT_IMPRESSIONS_COLUMNS),
        "clicks": first(PLACEMENT_CLICKS_COLUMNS),
        "spend": first(PLACEMENT_SPEND_COLUMNS),
        "orders": first(PLACEMENT_ORDERS_COLUMNS),
        "sales": first(PLACEMENT_SALES_COLUMNS),
    }


def placement_metric_row() -> dict[str, Any]:
    return {
        "rows": 0,
        "impressions": 0.0,
        "clicks": 0.0,
        "spend": 0.0,
        "orders": 0.0,
        "sales": 0.0,
    }


def aggregate_placement_rows(
    tables: list[dict[str, Any]],
    columns: dict[str, str | None],
    campaigns: set[str],
    start: str | None = None,
    end: str | None = None,
    marketplace: str | None = None,
) -> list[dict[str, Any]]:
    if not columns.get("date") or not columns.get("campaign") or not columns.get("placement"):
        return []
    start_date = date.fromisoformat(start) if start else None
    end_date = date.fromisoformat(end) if end else None
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for table in tables:
        for record in table.get("records", []):
            parsed_dates = parse_date_candidates(record.get(columns["date"]), marketplace)
            if len(parsed_dates) != 1:
                continue
            row_date = parsed_dates[0]
            if start_date and row_date < start_date:
                continue
            if end_date and row_date > end_date:
                continue
            campaign_raw = clean_text(record.get(columns["campaign"]))
            campaign_key = normalize_campaign_name(campaign_raw)
            placement_key = normalize_placement_name(record.get(columns["placement"]))
            if not campaign_key or campaign_key not in campaigns or not placement_key:
                continue
            key = (campaign_key, placement_key)
            row = grouped.setdefault(key, {
                "campaign_key": campaign_key,
                "campaign": campaign_raw,
                "placement": placement_key,
                "current_adjustments": [],
                "strategies": [],
                **placement_metric_row(),
            })
            row["rows"] += 1
            row["impressions"] += metric_float(record.get(columns.get("impressions") or ""))
            row["clicks"] += metric_float(record.get(columns.get("clicks") or ""))
            row["spend"] += metric_float(record.get(columns.get("spend") or ""))
            row["orders"] += metric_float(record.get(columns.get("orders") or ""))
            row["sales"] += metric_float(record.get(columns.get("sales") or ""))
            adjustment = placement_adjustment_percent(record.get(columns.get("adjustment") or ""))
            if adjustment is not None:
                row["current_adjustments"].append(adjustment)
            strategy = clean_text(record.get(columns.get("strategy") or ""))
            if strategy:
                row["strategies"].append(strategy)
    output: list[dict[str, Any]] = []
    for row in grouped.values():
        clicks = row["clicks"]
        sales = row["sales"]
        output.append({
            **row,
            "impressions": round(row["impressions"], 4),
            "clicks": round(clicks, 4),
            "spend": round(row["spend"], 4),
            "orders": round(row["orders"], 4),
            "sales": round(sales, 4),
            "ctr": pct(clicks, row["impressions"]),
            "cpc": pct(row["spend"], clicks),
            "cvr": pct(row["orders"], clicks),
            "acos": pct(row["spend"], sales),
            "current_adjustment": round(max(row["current_adjustments"]), 2) if row["current_adjustments"] else 0.0,
            "strategy": sorted(set(row["strategies"]))[0] if row["strategies"] else None,
        })
    return sorted(output, key=lambda row: (row["campaign"], row["placement"]))


def build_placement_analysis(
    placement_tables: list[dict[str, Any]],
    source_ad_tables: list[dict[str, Any]],
    recent_range: dict[str, Any] | None,
    baseline_range: dict[str, Any] | None,
    marketplace: str | None,
    profit_context: dict[str, Any] | None,
    profit_acos_cap: float | None = None,
) -> dict[str, Any]:
    if not placement_tables:
        return {
            "status": "not_provided",
            "enabled": False,
            "reason": "placement_file_not_supplied",
            "placement_actions": [],
            "campaign_placement_metrics": [],
        }
    table = next((item for item in placement_tables if item.get("records")), None)
    if not table:
        return {"status": "invalid_schema", "enabled": False, "reason": "empty_placement_workbook"}
    columns = placement_columns(table.get("headers", []))
    required = ["date", "shop", "campaign", "placement", "impressions", "clicks", "spend", "orders", "sales"]
    missing = [key for key in required if not columns.get(key)]
    if missing:
        return {
            "status": "invalid_schema",
            "enabled": False,
            "reason": "missing_required_placement_columns",
            "missing_columns": missing,
            "headers": table.get("headers", []),
        }
    placement_shops = {
        clean_text(record.get(columns["shop"]))
        for item in placement_tables
        for record in item.get("records", [])
        if clean_text(record.get(columns["shop"]))
    }
    search_shops = {
        clean_text(record.get(column))
        for item in source_ad_tables
        for record in item.get("records", [])
        for column in (columns_present(item.get("headers", []), ["店铺名称", "店铺", "Store"]))
        if clean_text(record.get(column))
    }
    if not placement_shops or not search_shops:
        return {
            "status": "shop_unverifiable",
            "enabled": False,
            "reason": "placement_or_search_term_shop_missing",
            "placement_shops": sorted(placement_shops),
            "search_shops": sorted(search_shops),
        }
    if placement_shops and search_shops and not placement_shops.intersection(search_shops):
        return {
            "status": "shop_mismatch",
            "enabled": False,
            "reason": "placement_shop_does_not_match_search_term_shop",
            "placement_shops": sorted(placement_shops),
            "search_shops": sorted(search_shops),
        }
    search_campaign_column = next(
        (column for column in CAMPAIGN_COLUMNS if any(column in item.get("headers", []) for item in source_ad_tables)),
        None,
    )
    search_campaigns = {
        normalize_campaign_name(record.get(search_campaign_column))
        for item in source_ad_tables
        for record in item.get("records", [])
        if search_campaign_column and normalize_campaign_name(record.get(search_campaign_column))
    }
    placement_campaigns = {
        normalize_campaign_name(record.get(columns["campaign"]))
        for item in placement_tables
        for record in item.get("records", [])
        if normalize_campaign_name(record.get(columns["campaign"]))
    }
    campaigns = search_campaigns.intersection(placement_campaigns)
    if not campaigns:
        return {
            "status": "no_campaign_overlap",
            "enabled": False,
            "reason": "no_exact_campaign_name_overlap",
            "placement_shops": sorted(placement_shops),
            "search_shops": sorted(search_shops),
        }
    placement_date_range = extract_table_date_range(placement_tables, marketplace)
    if not placement_date_range:
        return {
            "status": "date_range_unverifiable",
            "enabled": False,
            "reason": "placement_dates_missing_or_unparseable",
        }
    if not recent_range or not recent_range.get("start") or not recent_range.get("end"):
        return {
            "status": "date_range_unverifiable",
            "enabled": False,
            "reason": "search_term_analysis_date_range_missing",
            "placement_date_range": placement_date_range,
        }
    placement_overlap = period_overlap_context(placement_date_range, recent_range)
    if placement_overlap.get("status") != "overlap":
        return {
            "status": "date_range_mismatch",
            "enabled": False,
            "reason": "placement_dates_do_not_overlap_recent_analysis_window",
            "placement_date_range": placement_date_range,
            "recent_range": recent_range,
        }
    recent_metrics = aggregate_placement_rows(
        placement_tables,
        columns,
        campaigns,
        (recent_range or {}).get("start"),
        (recent_range or {}).get("end"),
        marketplace,
    )
    baseline_metrics = aggregate_placement_rows(
        placement_tables,
        columns,
        campaigns,
        (baseline_range or {}).get("start"),
        (baseline_range or {}).get("end"),
        marketplace,
    )
    if not recent_metrics:
        return {
            "status": "no_usable_recent_rows",
            "enabled": False,
            "reason": "no_valid_placement_rows_in_recent_analysis_window",
            "placement_date_range": placement_date_range,
            "recent_range": recent_range,
            "campaigns": sorted(campaigns),
        }
    profit_cap_value = profit_acos_cap
    if profit_cap_value is None:
        profit_cap_value = (profit_context or {}).get("profit_acos_cap")
    profit_cap = metric_float(profit_cap_value)
    source_bid_by_campaign: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in source_ad_tables:
        for record in item.get("records", []):
            campaign_key = normalize_campaign_name(first_text(record, CAMPAIGN_COLUMNS))
            source_bid = parse_optional_number(record.get("源竞价"))
            if campaign_key and source_bid is not None and source_bid > 0:
                source_bid_by_campaign[campaign_key].append(
                    {
                        "source_bid": round(source_bid, 4),
                        "date": first_text(record, DATE_COLUMNS),
                        "date_sort": normalized_date_sort_key(
                            first_text(record, DATE_COLUMNS),
                            marketplace or record.get("__marketplace"),
                        ),
                    }
                )
    baseline_by_key = {(row["campaign_key"], row["placement"]): row for row in baseline_metrics}
    actions: list[dict[str, Any]] = []
    for row in recent_metrics:
        baseline = baseline_by_key.get((row["campaign_key"], row["placement"]), {})
        orders = row["orders"]
        clicks = row["clicks"]
        acos = row.get("acos")
        baseline_orders = metric_float(baseline.get("orders"))
        baseline_acos = baseline.get("acos")
        strong = orders >= 3 or clicks >= 30 or baseline_orders >= 3
        profitable = bool(
            orders > 0 and acos is not None and (not profit_cap or metric_float(acos) <= profit_cap)
        )
        baseline_profitable = bool(
            baseline_orders > 0 and baseline_acos is not None
            and (not profit_cap or metric_float(baseline_acos) <= profit_cap)
        )
        source_candidates = source_bid_by_campaign.get(row["campaign_key"], [])
        source_candidates.sort(
            key=lambda item: (str(item.get("date_sort") or ""), str(item.get("date") or "")),
            reverse=True,
        )
        source_bid = source_candidates[0].get("source_bid") if source_candidates else None
        current_adjustment = metric_float(row.get("current_adjustment"))
        target_adjustment = current_adjustment
        target_effective_bid = None
        suggested_base_bid = None
        if source_bid is not None:
            # Source bid is the best available activity-level base-bid anchor.
            target_effective_bid = round(source_bid * (1 + current_adjustment / 100), 2)
            suggested_base_bid = round(source_bid, 2)

        if not strong:
            action = "observe"
            action_text = "近期和长期样本不足，保持当前广告位，不作提价"
        elif profitable or baseline_profitable:
            action = "keep_or_scale"
            action_text = "该广告位近期或历史达到利润线；先保持，只有长期一致且整体有CPC余量才提高溢价"
            if profitable and not baseline_profitable and current_adjustment < PLACEMENT_DEFAULT_UPLIFT_PCT and (
                orders >= 3 or baseline_orders >= 3
            ):
                target_adjustment = PLACEMENT_DEFAULT_UPLIFT_PCT
        elif row["spend"] > metric_float((profit_context or {}).get("estimated_gross_profit_per_order")):
            action = "base_bid_down_or_rebalance"
            action_text = "广告位近期亏损且花费超过单订单毛利容忍线；先降低基础竞价，必要时把该位置溢价降到0%"
            target_adjustment = 0
        else:
            action = "observe"
            action_text = "继续观察近期与长期窗口，不凭单一短期波动调整"
        if source_bid is not None:
            target_effective_bid = round(source_bid * (1 + target_adjustment / 100), 2)
            suggested_base_bid = round(
                min(source_bid, target_effective_bid / (1 + target_adjustment / 100)), 2
            )
        actions.append({
            "campaign": row["campaign"],
            "campaign_key": row["campaign_key"],
            "placement": row["placement"],
            "current_adjustment": row.get("current_adjustment", 0),
            "suggested_adjustment": round(target_adjustment, 2),
            "adjustment_change": round(target_adjustment - current_adjustment, 2),
            "source_bid": source_bid,
            "source_bid_date": source_candidates[0].get("date") if source_candidates else None,
            "suggested_base_bid": suggested_base_bid,
            "target_effective_bid": target_effective_bid,
            "profit_acos_cap": profit_cap_value,
            "recent_profitable": profitable,
            "baseline_profitable": baseline_profitable,
            "recent": row,
            "baseline": baseline,
            "action": action,
            "action_text": action_text,
            "evidence_strength": "strong" if strong else "low",
        })
    # Decide whether the activity has a placement-specific problem or an
    # account-wide bid problem. If every material placement is unprofitable,
    # lowering only the base bid is safer than pretending one placement can
    # rescue the activity. When one placement is good, keep it and rebalance
    # the weak placement's multiplier instead.
    actions_by_campaign: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for action_row in actions:
        actions_by_campaign[action_row["campaign_key"]].append(action_row)
    for campaign_key, campaign_actions in actions_by_campaign.items():
        material = [item for item in campaign_actions if metric_float((item.get("recent") or {}).get("clicks")) > 0 or metric_float((item.get("recent") or {}).get("orders")) > 0]
        if not material:
            continue
        has_good = any(
            (
                bool(item.get("recent_profitable"))
                and (
                    metric_float((item.get("baseline") or {}).get("orders")) == 0
                    or bool(item.get("baseline_profitable"))
                )
            )
            if (
                metric_float((item.get("recent") or {}).get("orders")) > 0
                or metric_float((item.get("recent") or {}).get("clicks")) >= 20
            )
            else bool(item.get("baseline_profitable"))
            for item in material
        )
        if has_good:
            continue
        for item in material:
            source_bid = item.get("source_bid")
            if source_bid is None:
                continue
            item["all_positions_bad"] = True
            item["suggested_adjustment"] = 0.0
            item["adjustment_change"] = round(-metric_float(item.get("current_adjustment")), 2)
            item["suggested_base_bid"] = None
            item["target_effective_bid"] = None
            item["base_bid_action_deferred_to_section1"] = True
            item["action"] = "base_bid_down_only"
            item["action_text"] = "来源活动各广告位未达利润线；仅作为新广告全部0%设计证据，原活动广告位由用户手动控制"

    # Placement is an activity-level routing decision. Collapse the raw
    # placement rows into one compact campaign recommendation so the report
    # does not repeat the same keyword/base-bid action for every placement.
    campaign_summaries: list[dict[str, Any]] = []
    for campaign_key, campaign_actions in sorted(actions_by_campaign.items()):
        material = [
            item for item in campaign_actions
            if metric_float((item.get("recent") or {}).get("clicks")) > 0
            or metric_float((item.get("recent") or {}).get("orders")) > 0
            or metric_float((item.get("baseline") or {}).get("clicks")) > 0
        ]
        def placement_is_good(item: dict[str, Any]) -> bool:
            recent = item.get("recent") or {}
            baseline = item.get("baseline") or {}
            recent_signal = (
                metric_float(recent.get("orders")) > 0
                or metric_float(recent.get("clicks")) >= 20
            )
            # Recent evidence has priority. Historical profitability can
            # protect a no-signal placement, but cannot justify a new uplift
            # when the recent window is materially poor.
            if recent_signal:
                return bool(item.get("recent_profitable")) and (
                    metric_float(baseline.get("orders")) == 0
                    or bool(item.get("baseline_profitable"))
                )
            return bool(item.get("baseline_profitable"))

        good = [item for item in material if placement_is_good(item)]
        current_settings = {
            item.get("placement"): metric_float(item.get("current_adjustment"))
            for item in campaign_actions
            if item.get("placement")
        }
        source_bids = [metric_float(item.get("source_bid")) for item in campaign_actions if item.get("source_bid")]
        source_bid = round(source_bids[0], 2) if source_bids else None
        suggested_adjustments = {"rest_of_search": 0, "top_of_search": 0, "product_pages": 0}
        linked_pct = 0.0
        action = "observe"
        action_text = "样本不足或位置差异不稳定，保持原广告位设置"
        target_placements: list[str] = []

        search_good = [item for item in good if item.get("placement") in {"rest_of_search", "top_of_search"}]
        product_good = [item for item in good if item.get("placement") == "product_pages"]
        if search_good:
            target_placements = sorted({str(item.get("placement")) for item in search_good})
            # Controlled placement uses the configured default for a new
            # campaign only. It never creates a linked old-keyword change.
            for placement_name in target_placements:
                suggested_adjustments[placement_name] = PLACEMENT_DEFAULT_UPLIFT_PCT
            bad_exists = any(item not in search_good for item in material)
            action = "route_good_search_placement" if bad_exists else "keep_good_search_placement"
            action_text = (
                "搜索结果目标位置表现较好；仅建议新广告集中到该位置，其他位置保持0%"
                if bad_exists
                else "搜索结果目标位置近期与历史均较好；仅作为新广告集中路由证据"
            )
        elif product_good and not search_good:
            action = "product_page_requires_separate_targeting"
            action_text = "产品页面有较好证据，但关键词活动不直接路由产品页面；另建商品投放活动复核"
        elif material:
            suggested_adjustments = {"rest_of_search": 0, "top_of_search": 0, "product_pages": 0}
            linked_pct = 0.0
            action = "base_bid_down_only"
            action_text = "有量广告位均未达到利润线；仅建议新广告全部0%，原活动广告位不调整"

        recent_summary = {
            "spend": round(sum(metric_float((item.get("recent") or {}).get("spend")) for item in campaign_actions), 2),
            "clicks": round(sum(metric_float((item.get("recent") or {}).get("clicks")) for item in campaign_actions), 2),
            "orders": round(sum(metric_float((item.get("recent") or {}).get("orders")) for item in campaign_actions), 2),
            "sales": round(sum(metric_float((item.get("recent") or {}).get("sales")) for item in campaign_actions), 2),
        }
        recent_summary["cpc"] = round(
            recent_summary["spend"] / recent_summary["clicks"], 2
        ) if recent_summary["clicks"] else None
        recent_summary["acos"] = round(
            recent_summary["spend"] / recent_summary["sales"], 4
        ) if recent_summary["sales"] else None
        campaign_summaries.append({
            "campaign": campaign_actions[0].get("campaign"),
            "campaign_key": campaign_key,
            "current_settings": current_settings,
            "suggested_adjustments": suggested_adjustments,
            "target_placements": target_placements,
            "uniform_keyword_adjustment_pct": linked_pct,
            "source_bid": source_bid,
            "recent": recent_summary,
            "placement_evidence": [
                {
                    "placement": item.get("placement"),
                    "recent": item.get("recent") or {},
                    "baseline": item.get("baseline") or {},
                    "recent_profitable": item.get("recent_profitable"),
                    "baseline_profitable": item.get("baseline_profitable"),
                    "evidence_strength": item.get("evidence_strength"),
                }
                for item in campaign_actions
            ],
            "placement_count": len(campaign_actions),
            "material_placement_count": len(material),
            "action": action,
            "action_text": action_text,
            "evidence_rule": "广告位为活动级证据，不伪分配为关键词×广告位订单；近期权重高，历史用于验证。",
        })
    return {
        "status": "ok",
        "enabled": True,
        "reason": "placement_file_valid_and_campaigns_joined",
        "columns": columns,
        "placement_shops": sorted(placement_shops),
        "search_shops": sorted(search_shops),
        "campaign_count": len(campaigns),
        "campaigns": sorted(campaigns),
        "placement_date_range": placement_date_range,
        "recent_range": recent_range,
        "baseline_range": baseline_range,
        "profit_acos_cap": profit_cap_value,
        "placements": sorted({row["placement"] for row in recent_metrics}),
        "recent_metrics": recent_metrics,
        "baseline_metrics": baseline_metrics,
        "placement_actions": actions,
        "campaign_summaries": campaign_summaries,
        "attribution_rule": "活动级广告位数据不得直接分配为关键词级订单；同一活动多目标时仅输出活动级证据，需按词分流时建议拆活动。",
    }


def click_signal_label(clicks: Any) -> str:
    count = metric_float(clicks)
    if count >= 20:
        return "very_strong_zero_order_signal"
    if count > CLICK_ONLY_OUTPUT_CLICK_THRESHOLD:
        return "strong_zero_order_signal"
    if count >= 6:
        return "medium_zero_order_signal"
    if count >= 3:
        return "weak_zero_order_signal"
    if count > 0:
        return "low_zero_order_signal"
    return "no_click_signal"


def compact_term_evidence(term: dict[str, Any], reason: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    ctr_context = term.get("ctr_context") or {}
    bid_economics = term.get("bid_economics") or {}
    evidence = {
        "search_term": term.get("search_term"),
        "aba_rank": term.get("aba_rank"),
        "market_volume_tier": term.get("market_volume_tier"),
        "ad_exposure_tier": term.get("ad_exposure_tier"),
        "data_sufficiency": term.get("data_sufficiency"),
        "promotion_priority": term.get("promotion_priority"),
        "action_eligibility": term.get("action_eligibility"),
        "local_conversion_confidence": term.get("local_conversion_confidence"),
        "abnormal_attribution_sample": term.get("abnormal_attribution_sample"),
        "scale_blockers": term.get("scale_blockers", []),
        "profit_acos_cap": term.get("profit_acos_cap"),
        "period_category": term.get("period_category"),
        "orders": term.get("orders", 0),
        "spend": term.get("spend", 0),
        "clicks": term.get("clicks", 0),
        "impressions": term.get("impressions", 0),
        "ctr": term.get("ctr"),
        "ctr_signal": ctr_context.get("ctr_signal"),
        "relative_ctr": ctr_context.get("relative_ctr"),
        "ctr_baseline_scope": ctr_context.get("baseline_scope"),
        "cpc": term.get("cpc"),
        "acos": term.get("acos"),
        "cvr": term.get("cvr"),
        "max_cpc": bid_economics.get("max_cpc"),
        "baseline_max_cpc": bid_economics.get("baseline_max_cpc"),
        "required_cvr": bid_economics.get("required_cvr"),
        "bid_cvr_used": bid_economics.get("bid_cvr_used"),
        "bid_cvr_source": bid_economics.get("bid_cvr_source"),
        "bid_cvr_confidence": bid_economics.get("bid_cvr_confidence"),
        "cpc_decision_mode": bid_economics.get("cpc_decision_mode"),
        "cpc_decision_note": bid_economics.get("cpc_decision_note"),
        "traffic_action_mode": bid_economics.get("traffic_action_mode"),
        "traffic_action_note": bid_economics.get("traffic_action_note"),
        "classification": term.get("classification"),
        "campaigns": term.get("campaigns", [])[:5],
        "ad_groups": term.get("ad_groups", [])[:5],
        "match_types": term.get("match_types", [])[:5],
        "campaign_types": term.get("campaign_types", [])[:5],
        "targeting_expressions": term.get("targeting_expressions", [])[:5],
        "source_bid": term.get("source_bid"),
        "source_bid_date": term.get("source_bid_date"),
        "source_bid_count": term.get("source_bid_count"),
        "source_campaign": term.get("source_campaign"),
        "source_ad_group": term.get("source_ad_group"),
        "source_targeting": term.get("source_targeting"),
        "source_match_type": term.get("source_match_type"),
        "current_targeting_status": manual_targeting_status(term),
        "reason": reason,
    }
    if extra:
        evidence.update(extra)
    return evidence


CANDIDATE_PACKAGE_FIELDS = [
    "search_term",
    "reason",
    "orders",
    "spend",
    "clicks",
    "impressions",
    "ctr",
    "ctr_signal",
    "relative_ctr",
    "ctr_baseline_scope",
    "cpc",
    "acos",
    "cvr",
    "aba_rank",
    "market_volume_tier",
    "ad_exposure_tier",
    "data_sufficiency",
    "promotion_priority",
    "action_eligibility",
    "local_conversion_confidence",
    "abnormal_attribution_sample",
    "scale_blockers",
    "period_category",
    "current_targeting_status",
    "source_term_targeting_status",
    "targeting_status_applies_to",
    "recommended_manual_action",
    "recommended_product_targeting_action",
    "exclude_from_manual_keyword_tables",
    "historical_orders",
    "historical_spend",
    "historical_acos",
    "historical_clicks",
    "historical_cvr",
    "historical_metric_scope",
    "historical_strength",
    "historical_recovery_priority",
    "historical_recovery_confidence",
    "historical_recovery_bid_change",
    "historical_recovery_reason",
    "recent_orders",
    "recent_spend",
    "recent_acos",
    "recent_clicks",
    "recent_cvr",
    "max_cpc",
    "baseline_max_cpc",
    "required_cvr",
    "bid_cvr_source",
    "bid_cvr_confidence",
    "cpc_decision_mode",
    "traffic_action_mode",
    "campaigns",
    "ad_groups",
    "match_types",
    "campaign_types",
    "targeting_expressions",
    "source_bid",
    "source_bid_date",
    "source_bid_count",
    "source_campaign",
    "source_ad_group",
    "source_targeting",
    "source_match_type",
    "default_scope",
    "recommended_negative_scope",
    "negative_source_eligible",
    "negative_decision_tier",
    "performance_direct_negative_exact",
    "performance_negative_review",
    "evidence_families",
    "evidence_family_count",
    "expected_orders",
    "zero_order_probability",
    "estimated_gross_profit_per_order",
    "spend_multiple",
    "required_cvr_ratio_to_baseline",
    "cpc_to_max_cpc_ratio",
    "consecutive_zero_order_days",
    "longer_zero_order_consistent",
    "ctr_is_diagnostic_only",
    "do_not_output_unless_semantic_mismatch",
    "root_protected",
    "click_signal",
    "required_human_or_model_check",
    "source_channels",
    "candidate_kind",
    "negative_type",
    "source_family",
    "protected",
    "early_negative_review",
    "semantic_relevance_score",
    "semantic_matched_tokens",
    "semantic_unmatched_tokens",
    "semantic_modifier_tokens",
    "root",
    "supporting_terms",
    "supporting_term_count",
    "performance_direct_negative_phrase",
    "active_day_count",
    "protected_source_terms",
    "growth_stage",
    "growth_confidence",
    "net_new_query_candidate",
    "profit_acos_cap",
    "ad_attributed_profit_proxy",
    "semantic_check_required_before_negative",
    "campaign",
    "campaign_intent",
    "ad_group_scope",
    "recommended_action",
    "suggested_bid_change",
    "suggested_bid_change_range",
    "selected_bid_change_pct",
    "actual_bid_change_pct",
    "required_bid_change_pct",
    "target_bid",
    "theoretical_base_bid",
    "old_campaign_target_cpc",
    "bid_plan_method",
    "cpc_to_base_ratio",
    "pause_scope_only",
    "action_id",
    "watchlist_id",
    "action_priority",
    "historical_evidence_independent",
    "section3_action_reference",
    "scope",
    "confidence",
    "daily_signal",
    "daily_signal_days",
    "daily_consecutive_days",
    "daily_expected_orders",
    "daily_zero_order_probability",
    "daily_spend_multiple",
    "daily_longer_window_consistent",
]


def compact_candidate_record_for_package(record: dict[str, Any]) -> dict[str, Any]:
    return {
        field: record.get(field)
        for field in CANDIDATE_PACKAGE_FIELDS
        if record.get(field) is not None
    }


def compact_candidate_actions_for_package(candidate_actions: dict[str, Any]) -> dict[str, Any]:
    if candidate_actions.get("status") != "ok":
        return candidate_actions
    compact = {
        key: value
        for key, value in candidate_actions.items()
        if not isinstance(value, list)
    }
    compact["protected_terms"] = [
        row.get("search_term")
        for row in candidate_actions.get("protected_terms", [])
        if row.get("search_term")
    ]
    compact["protected_terms_rule"] = "Never place these converting, historical, or scoped-conflict terms into global negatives."
    compact["root_protected_terms"] = [
        row.get("term")
        for row in candidate_actions.get("root_protected_terms", [])
        if row.get("term")
    ]
    compact["root_protected_rule"] = "Never use Negative Phrase for these roots; Exact negative requires explicit high-confidence evidence and caution."
    candidate_limits: dict[str, int | None] = {
        "performance_qualified_negative_exact_candidates": None,
        "negative_exact_review_candidates": None,
        "low_sample_semantic_mismatch_candidates": None,
        "negative_root_phrase_candidates": None,
        "blocked_negative_root_candidates": None,
        "manual_exact_candidates": 10,
        "manual_phrase_seed_candidates": 10,
        "manual_broad_seed_candidates": 8,
        "zero_order_exact_candidates": 5,
        "zero_order_phrase_candidates": 2,
        "product_targeting_candidates": 20,
        "diagnostic_candidates": 10,
        "low_ctr_diagnostic_candidates": 15,
        "campaign_action_candidates": 5,
        "campaign_watchlist_candidates": 10,
    }
    profile_fields = {
        "performance_qualified_negative_exact_candidates": [
            "search_term", "reason", "orders", "spend", "clicks", "impressions", "ctr",
            "ctr_signal", "cpc", "current_targeting_status", "source_channels", "default_scope",
            "recommended_negative_scope", "negative_source_eligible", "negative_decision_tier",
            "performance_direct_negative_exact", "evidence_families", "evidence_family_count",
            "expected_orders", "zero_order_probability", "estimated_gross_profit_per_order",
            "spend_multiple", "required_cvr_ratio_to_baseline", "cpc_to_max_cpc_ratio",
            "consecutive_zero_order_days", "longer_zero_order_consistent", "root_protected",
            "required_human_or_model_check", "candidate_kind", "negative_type", "source_family",
            "campaign", "ad_group", "scope", "source_campaign", "source_ad_group",
            "source_targeting", "source_match_type", "source_match_family", "historical_orders",
            "semantic_relevance_score",
            "semantic_matched_tokens", "semantic_unmatched_tokens", "semantic_modifier_tokens",
            "relative_ctr", "early_negative_review",
        ],
        "negative_exact_review_candidates": [
            "search_term", "reason", "orders", "spend", "clicks", "cpc", "acos", "cvr",
            "market_volume_tier", "data_sufficiency", "current_targeting_status", "default_scope",
            "recommended_negative_scope", "negative_source_eligible", "negative_decision_tier",
            "performance_negative_review", "evidence_families", "evidence_family_count",
            "expected_orders", "zero_order_probability", "spend_multiple", "required_cvr_ratio_to_baseline",
            "root_protected", "click_signal", "required_human_or_model_check", "candidate_kind",
            "negative_type", "source_family", "campaign", "ad_group", "scope",
            "source_campaign", "source_ad_group", "source_targeting", "source_match_type",
            "source_match_family", "historical_orders",
            "semantic_relevance_score", "semantic_matched_tokens", "semantic_unmatched_tokens",
            "semantic_modifier_tokens", "relative_ctr", "early_negative_review",
        ],
        "low_sample_semantic_mismatch_candidates": [
            "search_term", "reason", "orders", "spend", "clicks", "impressions", "ctr",
            "ctr_signal", "relative_ctr", "cpc", "current_targeting_status", "source_channels",
            "default_scope", "recommended_negative_scope", "negative_source_eligible", "negative_decision_tier",
            "do_not_output_unless_semantic_mismatch", "spend_multiple", "root_protected",
            "required_human_or_model_check", "candidate_kind", "negative_type", "source_family",
            "campaign", "ad_group", "scope", "source_campaign", "source_ad_group",
            "source_targeting", "source_match_type", "source_match_family", "historical_orders",
            "semantic_relevance_score",
            "semantic_matched_tokens", "semantic_unmatched_tokens", "semantic_modifier_tokens",
            "relative_ctr", "protected",
        ],
        "negative_root_phrase_candidates": [
            "search_term", "root", "reason", "candidate_kind", "negative_type",
            "negative_decision_tier", "performance_direct_negative_phrase", "campaign", "ad_group",
            "source_campaign", "source_ad_group", "source_targeting", "source_family", "source_match_type",
            "source_match_family", "historical_orders",
            "recommended_negative_scope", "scope", "supporting_terms", "supporting_term_count",
            "clicks", "impressions", "spend", "orders", "ctr", "relative_ctr", "cpc", "max_cpc",
            "cpc_to_max_cpc_ratio", "expected_orders", "estimated_gross_profit_per_order",
            "spend_multiple", "active_day_count", "semantic_relevance_score", "semantic_matched_tokens",
            "root_protected", "protected_source_terms", "required_human_or_model_check",
        ],
        "blocked_negative_root_candidates": [
            "search_term", "root", "reason", "candidate_kind", "negative_type",
            "negative_decision_tier", "campaign", "ad_group", "source_targeting", "source_family",
            "source_match_type", "source_match_family", "historical_orders",
            "supporting_terms", "supporting_term_count", "clicks", "spend", "orders",
            "root_protected", "protected_source_terms",
        ],
        "manual_exact_candidates": [
            "search_term", "reason", "orders", "spend", "clicks", "cpc", "acos", "cvr",
            "aba_rank", "market_volume_tier", "data_sufficiency", "promotion_priority",
            "action_eligibility", "local_conversion_confidence", "abnormal_attribution_sample", "scale_blockers",
            "period_category", "current_targeting_status", "recommended_manual_action",
            "historical_orders", "historical_spend", "historical_acos", "historical_metric_scope",
            "historical_strength", "historical_recovery_priority", "historical_recovery_confidence",
            "historical_recovery_bid_change", "historical_recovery_reason", "recent_orders",
            "recent_spend", "recent_acos", "recent_clicks", "recent_cvr", "max_cpc", "baseline_max_cpc",
            "traffic_action_mode", "section3_action_reference", "placement_plan",
            "campaigns", "ad_groups", "match_types", "targeting_expressions",
            "source_bid", "source_bid_date", "source_campaign", "source_ad_group",
            "source_targeting", "source_match_type",
        ],
        "manual_phrase_seed_candidates": [
            "search_term", "reason", "orders", "spend", "cpc", "acos", "cvr", "aba_rank",
            "market_volume_tier", "data_sufficiency", "promotion_priority", "current_targeting_status",
            "source_term_targeting_status", "targeting_status_applies_to", "section3_action_reference",
            "placement_plan", "campaigns", "ad_groups", "match_types",
            "targeting_expressions", "source_bid", "source_bid_date",
            "source_campaign", "source_ad_group", "source_targeting", "source_match_type",
        ],
        "manual_broad_seed_candidates": [
            "search_term", "reason", "orders", "spend", "cpc", "acos", "cvr", "aba_rank",
            "market_volume_tier", "data_sufficiency", "promotion_priority", "current_targeting_status",
            "source_term_targeting_status", "targeting_status_applies_to", "section3_action_reference",
            "placement_plan", "campaigns", "ad_groups", "match_types",
            "targeting_expressions", "source_bid", "source_bid_date",
            "source_campaign", "source_ad_group", "source_targeting", "source_match_type",
        ],
        "zero_order_exact_candidates": [
            "search_term", "reason", "orders", "spend", "clicks", "impressions", "ctr",
            "relative_ctr", "cpc", "semantic_relevance_score", "semantic_matched_tokens",
            "discovery_score", "suggested_bid", "recommended_action", "evidence_confidence",
            "zero_order_potential", "recommended_discovery_match_type", "does_not_authorize_scale",
            "campaigns", "ad_groups", "match_types", "campaign_types", "targeting_expressions",
            "source_bid", "source_bid_date", "source_campaign", "source_ad_group",
            "source_targeting", "source_match_type",
        ],
        "zero_order_phrase_candidates": [
            "search_term", "reason", "orders", "spend", "clicks", "impressions", "ctr",
            "relative_ctr", "cpc", "semantic_relevance_score", "semantic_matched_tokens",
            "discovery_score", "suggested_bid", "recommended_action", "evidence_confidence",
            "zero_order_potential", "recommended_discovery_match_type", "does_not_authorize_scale",
            "source_support_terms", "supporting_term_count", "campaigns", "ad_groups",
            "match_types", "campaign_types", "targeting_expressions", "source_campaign",
            "source_ad_group", "source_targeting", "source_match_type",
        ],
        "product_targeting_candidates": [
            "search_term", "reason", "orders", "spend", "clicks", "cpc", "acos", "cvr",
            "match_types", "targeting_expressions", "campaigns", "ad_groups",
            "recommended_product_targeting_action", "historical_orders", "historical_spend",
            "historical_acos", "recent_orders", "recent_spend", "recent_acos", "placement_plan",
        ],
        "diagnostic_candidates": [
            "search_term", "reason", "orders", "spend", "clicks", "cpc", "acos", "cvr",
            "period_category", "current_targeting_status", "historical_orders", "historical_spend",
            "historical_acos", "recent_orders", "recent_spend", "recent_acos", "recent_clicks",
            "max_cpc", "traffic_action_mode",
        ],
        "low_ctr_diagnostic_candidates": [
            "search_term", "reason", "orders", "spend", "clicks", "impressions", "ctr",
            "ctr_signal", "relative_ctr", "market_volume_tier", "required_human_or_model_check",
        ],
        "campaign_action_candidates": [
            "action_id", "campaign", "campaign_intent", "ad_group_scope", "search_term", "orders",
            "spend", "clicks", "impressions", "ctr", "cpc", "acos", "cvr", "historical_orders",
            "historical_spend", "historical_acos", "historical_metric_scope", "historical_strength",
            "historical_recovery_priority", "max_cpc", "baseline_max_cpc", "recommended_action",
            "suggested_bid_change", "confidence", "reason", "period_category", "traffic_action_mode",
            "suggested_bid_change_range", "selected_bid_change_pct", "actual_bid_change_pct",
            "required_bid_change_pct", "target_bid", "theoretical_base_bid",
            "old_campaign_target_cpc", "bid_plan_method", "cpc_to_base_ratio", "pause_scope_only",
            "action_eligibility", "local_conversion_confidence", "abnormal_attribution_sample", "scale_blockers",
            "action_priority", "historical_evidence_independent", "source_bid", "source_bid_date",
            "source_bid_count", "source_bid_unique_count", "source_bid_stable",
            "source_campaign", "source_ad_group", "source_targeting", "source_match_type",
            "source_match_family",
            "daily_signal", "daily_signal_days", "daily_consecutive_days", "daily_expected_orders",
            "daily_zero_order_probability", "daily_spend_multiple", "daily_longer_window_consistent",
        ],
        "campaign_watchlist_candidates": [
            "watchlist_id", "campaign", "campaign_intent", "ad_group_scope", "search_term", "orders",
            "spend", "clicks", "cpc", "acos", "cvr", "historical_orders", "historical_spend",
            "historical_acos", "max_cpc", "baseline_max_cpc", "recommended_action",
            "suggested_bid_change", "suggested_bid_change_range", "confidence", "reason", "period_category", "traffic_action_mode",
            "selected_bid_change_pct", "actual_bid_change_pct", "required_bid_change_pct",
            "target_bid", "theoretical_base_bid", "old_campaign_target_cpc",
            "bid_plan_method", "cpc_to_base_ratio", "pause_scope_only",
            "action_eligibility", "local_conversion_confidence", "abnormal_attribution_sample", "scale_blockers",
            "action_priority", "historical_evidence_independent", "source_bid", "source_bid_date",
            "source_bid_count", "source_bid_unique_count", "source_bid_stable",
            "source_campaign", "source_ad_group", "source_targeting", "source_match_type",
            "source_match_family",
            "daily_signal", "daily_signal_days", "daily_consecutive_days", "daily_expected_orders",
            "daily_zero_order_probability", "daily_spend_multiple", "daily_longer_window_consistent",
        ],
    }
    for key, limit in candidate_limits.items():
        fields = profile_fields[key]
        rows = candidate_actions.get(key, [])
        if limit is not None:
            rows = rows[:limit]
        compact[key] = [
            {field: row.get(field) for field in fields if row.get(field) is not None}
            for row in rows
        ]
    # Section 1 is an executable old-campaign source-scope table. Keep the
    # complete action set available for scope joins; immediate/watchlist lists
    # above remain compact presentation aids for other sections.
    compact["campaign_bid_merge_sources"] = [
        compact_candidate_record_for_package(row)
        for row in candidate_actions.get("campaign_bid_merge_sources", [])
    ]
    compact["ad_group_scoped_actions"] = candidate_actions.get("ad_group_scoped_actions", [])
    compact["model_instructions"] = candidate_actions.get("model_instructions", [])
    return compact


def compact_mature_growth_context_for_package(context: dict[str, Any]) -> dict[str, Any]:
    if context.get("status") != "ok":
        return context
    return {
        key: (
            [compact_candidate_record_for_package(row) for row in value]
            if key == "growth_candidates"
            else value
        )
        for key, value in context.items()
    }


def discovery_channel_label(value: Any) -> str | None:
    text = clean_text(value).lower().replace("_", " ").replace("-", " ")
    if not text:
        return None
    if any(token in text for token in ["substitute", "complement", "替代", "互补", "关联商品"]):
        return "auto_product_targeting"
    if "close match" in text or "紧密匹配" in text or "enge übereinstimmung" in text:
        return "auto_close"
    if "loose match" in text or "宽泛匹配" in text or "lose übereinstimmung" in text:
        return "auto_loose"
    if "exact" in text or "精准" in text or "genau" in text:
        return "manual_exact"
    if "phrase" in text or "词组" in text:
        return "manual_phrase"
    if "broad" in text or "广泛" in text or "weitgehend" in text:
        return "manual_broad"
    if "automatic" in text or "automatisch" in text or re.search(r"\bauto\b", text) or "自动" in text:
        return "auto_other"
    return None


def discovery_channels_for_term(term: dict[str, Any]) -> list[str]:
    channels: set[str] = set()
    for field in ["match_types", "targeting_expressions"]:
        for value in term.get(field, []) or []:
            channel = discovery_channel_label(value)
            if channel:
                channels.add(channel)
    if not channels:
        for field in ["campaign_types", "campaigns"]:
            for value in term.get(field, []) or []:
                channel = discovery_channel_label(value)
                if channel:
                    channels.add(channel)
    return sorted(channels) or ["unknown"]


def source_match_family(value: Any) -> str:
    """Normalize source targeting into the decision family used by negative planning."""
    text = clean_text(value).casefold()
    if any(token in text for token in ["exact", "精准", "精确", "genau"]):
        return "exact"
    if any(token in text for token in ["phrase", "词组"]):
        return "phrase"
    if any(token in text for token in ["broad", "广泛", "weitgehend"]):
        return "broad"
    if any(token in text for token in [
        "auto", "自动", "紧密", "宽泛", "close", "loose", "automatisch",
        "enge übereinstimmung", "lose übereinstimmung",
    ]):
        return "auto"
    return "unknown"


def manual_targeting_status(term: dict[str, Any]) -> str:
    channels = set(discovery_channels_for_term(term))
    discovery = channels & {"manual_phrase", "manual_broad", "auto_close", "auto_loose", "auto_other"}
    if "manual_exact" in channels and discovery:
        return "精准重叠"
    if "manual_exact" in channels:
        return "已有精准"
    if "manual_phrase" in channels:
        return "来自词组"
    if channels & {"auto_close", "auto_loose", "auto_other"}:
        return "来自自动"
    if "manual_broad" in channels:
        return "来自广泛"
    return "未确认覆盖"


def is_auto_product_targeting_term(term: dict[str, Any]) -> bool:
    channels = set(discovery_channels_for_term(term))
    if "auto_product_targeting" in channels:
        return True
    return is_asin_token(term.get("search_term"))


def term_from_period_side(search_term: str, side: dict[str, Any] | None) -> dict[str, Any]:
    side = side or {}
    return {
        "search_term": search_term,
        "orders": side.get("orders", 0),
        "spend": side.get("spend", 0),
        "clicks": side.get("clicks", 0),
        "acos": side.get("acos"),
        "cvr": side.get("cvr"),
        "campaigns": side.get("campaigns", []),
        "ad_groups": side.get("ad_groups", []),
        "match_types": side.get("match_types", []),
        "campaign_types": side.get("campaign_types", []),
        "targeting_expressions": side.get("targeting_expressions", []),
    }


def product_targeting_action(term: dict[str, Any], target_acos: float | None) -> str:
    orders = metric_float(term.get("orders"))
    clicks = metric_float(term.get("clicks"))
    acos = metric_float(term.get("acos")) if term.get("acos") is not None else None
    if orders > 0 and target_acos and acos is not None:
        return "keep_or_scale_product_target" if acos <= target_acos else "bid_down_product_target"
    if orders > 0:
        return "keep_product_target_observe_economics"
    if clicks > CLICK_ONLY_OUTPUT_CLICK_THRESHOLD:
        return "bid_down_or_negative_product_review"
    return "observe_product_target"


def build_mature_growth_context(
    ad_data: dict[str, Any],
    match_type_data: dict[str, Any],
    product: dict[str, Any],
    account_macro_summary: dict[str, Any],
    cpc_control_context: dict[str, Any],
    profit_context: dict[str, Any],
    target_acos: float | None,
    currency_symbol: str = "$",
) -> dict[str, Any]:
    if ad_data.get("status") != "ok":
        return {"status": "not_available", "reason": "ad_data_not_available"}

    margin = profit_context.get("gross_margin_range") or {}
    margin_midpoint = margin.get("midpoint") if isinstance(margin, dict) else None
    profit_cap_values = [value for value in [target_acos, margin_midpoint] if value is not None]
    profit_acos_cap = min(profit_cap_values) if profit_cap_values else None
    economics_complete = target_acos is not None and margin_midpoint is not None

    lifecycle = clean_text(product.get("lifecycle_stage")).lower()
    explicit_mature = any(token in lifecycle for token in ["mature", "stable", "steady", "成熟", "稳定"])
    history_mature = cpc_control_context.get("mode") == "stable_or_profit_control"
    mature_signal = explicit_mature or history_mature

    all_terms = ad_data.get("all_terms", [])
    exact_terms = [
        pair
        for pair in (match_type_data.get("all_pairs", []) if match_type_data.get("status") == "ok" else [])
        if "manual_exact" in discovery_channels_for_term(pair)
    ]
    exact_coverage_present = bool(exact_terms)
    profitable_exact_terms = [
        term
        for term in exact_terms
        if metric_float(term.get("orders")) > 0
        and term.get("acos") is not None
        and profit_acos_cap is not None
        and metric_float(term.get("acos")) <= profit_acos_cap
    ]
    profitable_exact_coverage_present = bool(profitable_exact_terms)
    aggregate_lookup = term_lookup(ad_data)
    micro_phrase_candidates = []
    for exact_term in profitable_exact_terms:
        search_term = clean_text(exact_term.get("search_term"))
        orders = metric_float(exact_term.get("orders"))
        cpc = metric_float(exact_term.get("cpc")) if exact_term.get("cpc") is not None else None
        if not search_term or orders < 2 or cpc is None or cpc <= 0:
            continue
        aggregate_term = aggregate_lookup.get(search_term) or {}
        bid_economics = aggregate_term.get("bid_economics") or {}
        baseline_max_cpc = metric_float(bid_economics.get("baseline_max_cpc")) or None
        cvr_health = bid_economics.get("cvr_health")
        existing_channels = set(discovery_channels_for_term(aggregate_term))
        if cvr_health == "high_conversion_risk" or "manual_phrase" in existing_channels:
            continue
        if baseline_max_cpc is None:
            continue
        word_count = len(search_term.split())
        direct_test_eligible = word_count >= 3
        bid_low = min(cpc * 0.60, baseline_max_cpc * 0.45)
        bid_high = min(cpc * 0.75, baseline_max_cpc * 0.60)
        daily_budget_min, daily_budget_max = ((3.0, 5.0) if orders >= 4 else (2.0, 3.0))
        micro_phrase_candidates.append(
            {
                "search_term": search_term,
                "source_match_type": "manual_exact",
                "source_orders": exact_term.get("orders"),
                "source_spend": exact_term.get("spend"),
                "source_clicks": exact_term.get("clicks"),
                "source_cpc": exact_term.get("cpc"),
                "source_acos": exact_term.get("acos"),
                "source_cvr": exact_term.get("cvr"),
                "source_cvr_health": cvr_health,
                "current_targeting_status": "未确认覆盖",
                "source_term_targeting_status": "已有精准",
                "direct_test_eligible": direct_test_eligible,
                "semantic_scope_review_required": not direct_test_eligible,
                "suggested_bid_min": round(bid_low, 2),
                "suggested_bid_max": round(max(bid_low, bid_high), 2),
                "suggested_daily_budget_min": daily_budget_min,
                "suggested_daily_budget_max": daily_budget_max,
                "discovery_isolation": "add_negative_exact_for_seed_in_phrase_discovery_campaign",
                "negative_exact_isolation_keyword": search_term,
                "recommended_action": (
                    "micro_phrase_low_budget_test"
                    if direct_test_eligible
                    else "semantic_scope_review_before_optional_micro_phrase_test"
                ),
            }
        )
    micro_phrase_candidates.sort(
        key=lambda row: (
            not bool(row.get("direct_test_eligible")),
            -metric_float(row.get("source_orders")),
            metric_float(row.get("source_acos")),
            str(row.get("search_term")),
        )
    )
    direct_micro_phrase_candidates = [
        row for row in micro_phrase_candidates if row.get("direct_test_eligible")
    ]
    recent_acos = (account_macro_summary.get("recent") or {}).get("acos")
    if not mature_signal:
        recommendation_mode = "stabilize_profit_only"
        stabilization_reason = "maturity_not_confirmed"
    elif not economics_complete:
        recommendation_mode = "stabilize_profit_only"
        stabilization_reason = "economics_incomplete"
    elif not profitable_exact_coverage_present:
        recommendation_mode = "stabilize_profit_only"
        stabilization_reason = "profitable_exact_base_missing"
    elif cpc_control_context.get("mode") == "diagnostic_period":
        recommendation_mode = "stabilize_profit_only"
        stabilization_reason = "account_diagnostic_period"
    elif recent_acos is not None and profit_acos_cap is not None and recent_acos > profit_acos_cap:
        recommendation_mode = (
            "stabilize_with_micro_phrase"
            if direct_micro_phrase_candidates
            else "stabilize_profit_only"
        )
        stabilization_reason = (
            "account_acos_above_profit_cap_with_eligible_micro_phrase"
            if direct_micro_phrase_candidates
            else "account_acos_above_profit_cap_no_eligible_micro_phrase"
        )
    else:
        recommendation_mode = "ready_for_controlled_discovery"
        stabilization_reason = None

    channel_rollup: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"spend": 0.0, "orders": 0.0, "clicks": 0.0, "ad_sales": 0.0, "sales_available": False}
    )
    if match_type_data.get("status") == "ok":
        for pair in match_type_data.get("all_pairs", []):
            channels = discovery_channels_for_term(pair)
            channel = channels[0]
            row = channel_rollup[channel]
            row["spend"] += metric_float(pair.get("spend"))
            row["orders"] += metric_float(pair.get("orders"))
            row["clicks"] += metric_float(pair.get("clicks"))
            if pair.get("ad_sales") is not None:
                row["ad_sales"] += metric_float(pair.get("ad_sales"))
                row["sales_available"] = True

    channel_economics = []
    for channel, row in channel_rollup.items():
        sales = row["ad_sales"] if row["sales_available"] else None
        profit_proxy = sales * margin_midpoint - row["spend"] if sales is not None and margin_midpoint is not None else None
        channel_economics.append(
            {
                "channel": channel,
                "orders": maybe_int(row["orders"]),
                "spend": round(row["spend"], 4),
                "clicks": maybe_int(row["clicks"]),
                "ad_sales": round(sales, 4) if sales is not None else None,
                "acos": safe_ratio(row["spend"], sales),
                "ad_attributed_profit_proxy": round(profit_proxy, 4) if profit_proxy is not None else None,
            }
        )
    channel_economics.sort(key=lambda row: (-metric_float(row.get("spend")), str(row.get("channel"))))

    strong_waste_spend = ((profit_context.get("zero_order_spend_thresholds") or {}).get("strong_action_spend"))
    growth_candidates = []
    for term in all_terms:
        channels = discovery_channels_for_term(term)
        has_exact = "manual_exact" in channels
        discovery_channels = [
            channel for channel in channels
            if channel in {"manual_phrase", "manual_broad", "auto_close", "auto_loose", "auto_other"}
        ]
        has_product_targeting = "auto_product_targeting" in channels
        orders = metric_float(term.get("orders"))
        spend = metric_float(term.get("spend"))
        clicks = metric_float(term.get("clicks"))
        sales = term.get("ad_sales")
        acos = term.get("acos")
        cpc = term.get("cpc")
        max_cpc = (term.get("bid_economics") or {}).get("max_cpc")
        profit_proxy = (
            metric_float(sales) * margin_midpoint - spend
            if sales is not None and margin_midpoint is not None
            else None
        )
        profitable = acos is not None and profit_acos_cap is not None and metric_float(acos) <= profit_acos_cap
        cpc_viable = cpc is not None and max_cpc is not None and metric_float(cpc) <= metric_float(max_cpc)
        low_confidence_conversion = bool(
            term.get("local_conversion_confidence") == "low"
            or term.get("abnormal_attribution_sample")
        )

        if has_product_targeting:
            stage = "analyze_as_product_targeting"
            net_new = False
        elif discovery_channels and has_exact:
            stage = "exact_overlap_not_incremental"
            net_new = False
        elif discovery_channels and orders >= 2 and profitable and not low_confidence_conversion:
            stage = "harvest_to_exact"
            net_new = True
        elif (
            discovery_channels
            and orders >= 1
            and term.get("period_category") == "consistently_converting"
            and profitable
            and not low_confidence_conversion
        ):
            stage = "harvest_to_exact"
            net_new = True
        elif discovery_channels and orders >= 1 and (profitable or cpc_viable):
            stage = "validate_in_exact"
            net_new = True
        elif discovery_channels and orders >= 1:
            stage = "control_bid_before_harvest"
            net_new = True
        elif discovery_channels and orders == 0 and (
            clicks > CLICK_ONLY_OUTPUT_CLICK_THRESHOLD
            or (strong_waste_spend is not None and spend >= metric_float(strong_waste_spend))
        ):
            stage = "discovery_waste_review"
            net_new = True
        elif discovery_channels:
            stage = "observe_discovery"
            net_new = True
        else:
            continue

        growth_candidates.append(
            compact_term_evidence(
                term,
                "mature_product_incremental_discovery",
                {
                    "source_channels": channels,
                    "growth_stage": stage,
                    "net_new_query_candidate": net_new,
                    "profit_acos_cap": profit_acos_cap,
                    "growth_confidence": "low" if low_confidence_conversion else "standard",
                    "ad_attributed_profit_proxy": round(profit_proxy, 4) if profit_proxy is not None else None,
                    "semantic_check_required_before_negative": stage == "discovery_waste_review",
                },
            )
        )

    stage_priority = {
        "harvest_to_exact": 0,
        "validate_in_exact": 1,
        "control_bid_before_harvest": 2,
        "exact_overlap_not_incremental": 3,
        "discovery_waste_review": 4,
        "analyze_as_product_targeting": 5,
        "observe_discovery": 6,
    }
    growth_candidates.sort(
        key=lambda row: (
            stage_priority.get(str(row.get("growth_stage")), 99),
            -metric_float(row.get("orders")),
            -metric_float(row.get("spend")),
            str(row.get("search_term")),
        )
    )

    return {
        "status": "ok",
        "recommendation_mode": recommendation_mode,
        "stabilization_reason": stabilization_reason,
        "maturity_signal": {
            "product_lifecycle_stage": product.get("lifecycle_stage"),
            "explicit_mature": explicit_mature,
            "history_supports_mature_control": history_mature,
        },
        "profitability_gate": {
            "target_acos": target_acos,
            "gross_margin_midpoint": margin_midpoint,
            "profit_acos_cap": profit_acos_cap,
            "economics_complete": economics_complete,
            "formula": "profit_acos_cap = min(target_acos, gross_margin_midpoint)",
            "profit_proxy_formula": "ad_sales × gross_margin_midpoint − ad_spend",
            "profit_proxy_limit": "Advertising-attributed proxy only; do not claim true incrementality without holdout or organic/cannibalization evidence.",
        },
        "exact_coverage_present": exact_coverage_present,
        "profitable_exact_coverage_present": profitable_exact_coverage_present,
        "exact_coverage_quality": {
            "exact_term_count": len(exact_terms),
            "profitable_exact_term_count": len(profitable_exact_terms),
            "rule": "Profitable Exact base requires orders > 0 and ACoS at or below profit_acos_cap.",
        },
        "source_data": {
            "match_type_columns_found": ad_data.get("match_type_columns_found", []),
            "campaign_type_columns_found": ad_data.get("campaign_type_columns_found", []),
            "targeting_columns_found": ad_data.get("targeting_columns_found", []),
            "granularity_note": "Without targeting/match-source columns, do not make granular Auto Close/Loose/Substitutes/Complements recommendations.",
        },
        "channel_roles": {
            "manual_phrase": "Primary controlled long-tail discovery channel.",
            "auto_close_loose": "Discover real customer query expressions.",
            "manual_broad": "Small-budget exploration for new roots and use cases.",
            "auto_product_targeting": "Analyze substitutes/complements separately from keyword long-tail discovery.",
            "manual_exact": "Protect the profitable base and validate/harvest discovered terms.",
        },
        "recommended_priority": (
            ["manual_phrase_micro_test", "manual_exact_profit_repair"]
            if recommendation_mode == "stabilize_with_micro_phrase"
            else [
                {
                    "maturity_not_confirmed": "confirm_mature_lifecycle_before_discovery",
                    "economics_incomplete": "complete_target_acos_and_margin_economics",
                    "profitable_exact_base_missing": "build_profitable_manual_exact_base",
                    "account_diagnostic_period": "resolve_account_diagnostic_before_discovery",
                    "account_acos_above_profit_cap_no_eligible_micro_phrase": "manual_exact_profit_repair",
                }.get(stabilization_reason, "manual_exact_profit_repair")
            ]
            if recommendation_mode == "stabilize_profit_only"
            else ["manual_phrase", "auto_close", "auto_loose", "manual_broad"]
            if recommendation_mode == "ready_for_controlled_discovery"
            else []
        ),
        "micro_phrase_policy": {
            "enabled": recommendation_mode == "stabilize_with_micro_phrase",
            "eligibility_rule": "Manual Exact has at least 2 recent orders, ACoS at or below profit_acos_cap, no high-conversion-risk flag, CPC/baseline Max CPC available, and no confirmed Phrase coverage.",
            "allowed_channel": "manual_phrase_only",
            "manual_broad_unlocked": recommendation_mode == "ready_for_controlled_discovery",
            "auto_discovery_unlocked": recommendation_mode == "ready_for_controlled_discovery",
            "broad_unlock_rule": "Unlock small-budget Broad only after Phrase discovers at least 2 net-new orders at ACoS no higher than Target ACoS, or the account reaches ready_for_controlled_discovery.",
            "budget_rule": (
                f"Ring-fence each micro Phrase test at the suggested "
                f"{currency_symbol}2-{currency_symbol}5 daily budget and do not fund it by weakening profitable Exact coverage."
            ),
            "isolation_rule": "If the goal is net-new query discovery, add the seed as Negative Exact only inside the Phrase discovery campaign; never apply it globally.",
        },
        "micro_phrase_candidates": micro_phrase_candidates[:3],
        "channel_economics": channel_economics,
        "growth_candidates": growth_candidates[:30],
        "model_instructions": [
            "Use three growth states: stabilize_profit_only blocks discovery; stabilize_with_micro_phrase allows only ring-fenced Phrase tests from profitable Exact seeds; ready_for_controlled_discovery unlocks Phrase, Auto, and small-budget Broad.",
            "When recommendation_mode is stabilize_with_micro_phrase, render the direct-test-eligible micro_phrase_candidates in the Section 2 new-campaign table and keep Auto/Broad closed.",
            "Do not treat mere Exact existence as a ready base; profitable_exact_coverage_present must be true.",
            "If profitable Exact still has obvious low-risk room, scale Exact before funding more discovery.",
            "Treat exact_overlap_not_incremental as routing/attribution overlap, not new growth; do not auto-negative it.",
            "Move harvest_to_exact terms into Manual Exact; use low-bid Exact validation for validate_in_exact terms.",
            "Keep Auto product-targeting findings separate from keyword long-tail recommendations.",
            "Use discovery_waste_review only after semantic review; relevance determines negative versus bid-down/observe.",
        ],
    }


def build_controlled_exploration(
    candidate_actions: dict[str, Any],
    mature_growth_context: dict[str, Any],
    match_type_data: dict[str, Any],
    product: dict[str, Any],
    period_comparison: dict[str, Any],
    daily_window_selection: dict[str, Any],
    profit_context: dict[str, Any],
    acos_cpc_ladder: dict[str, Any],
) -> dict[str, Any]:
    """Add small evidence-buying tests without changing any core action or growth gate."""
    base_mode = mature_growth_context.get("recommendation_mode")
    profit_acos_cap = product_profit_acos_cap(product)
    baseline_max_cpc = metric_float(acos_cpc_ladder.get("target_acos_cpc")) or None
    stop_thresholds = profit_context.get("zero_order_spend_thresholds") or {}
    policy = {
        "does_not_override_core_actions": True,
        "max_phrase_candidates": 3,
        "default_shared_daily_budget": 2.0,
        "user_budget_share_cap": 0.15,
        "broad_policy": "unchanged_existing_unlock_rule",
        "negative_isolation": "add_each_seed_as_negative_exact_only_inside_the_controlled_phrase_campaign",
        "stop_rules": {
            "watch_spend": stop_thresholds.get("watch_spend"),
            "strong_action_spend": stop_thresholds.get("strong_action_spend"),
            "critical_waste_spend": stop_thresholds.get("critical_waste_spend"),
        },
    }
    if base_mode != "stabilize_profit_only":
        return {
            "status": "not_needed",
            "reason": "base_growth_mode_already_handles_discovery",
            "base_recommendation_mode": base_mode,
            "phrase_candidates": [],
            "auto_close_test": None,
            **policy,
        }
    if profit_acos_cap is None or baseline_max_cpc is None:
        return {
            "status": "not_available",
            "reason": "profit_or_cpc_economics_incomplete",
            "base_recommendation_mode": base_mode,
            "phrase_candidates": [],
            "auto_close_test": None,
            **policy,
        }

    history_independent = historical_evidence_is_independent(period_comparison)
    phrase_candidates: list[dict[str, Any]] = []
    seen_terms: set[str] = set()
    for row in candidate_actions.get("manual_exact_candidates", []) or []:
        search_term = clean_text(row.get("search_term"))
        word_count = len(search_term.split())
        if not search_term or search_term in seen_terms or not 3 <= word_count <= 5:
            continue
        if row.get("abnormal_attribution_sample"):
            continue
        if row.get("current_targeting_status") == "来自词组":
            continue

        recent_orders = metric_float(row.get("recent_orders", row.get("orders")))
        recent_acos = row.get("recent_acos", row.get("acos"))
        historical_orders = metric_float(row.get("historical_orders"))
        historical_acos = row.get("historical_acos")
        if (
            recent_orders >= 1
            and recent_acos is not None
            and metric_float(recent_acos) <= profit_acos_cap
        ):
            evidence_window = "recent"
            source_orders = recent_orders
            source_spend = metric_float(row.get("recent_spend", row.get("spend")))
            source_clicks = metric_float(row.get("recent_clicks", row.get("clicks")))
            source_acos = metric_float(recent_acos)
            source_cvr = row.get("recent_cvr", row.get("cvr"))
        elif (
            history_independent
            and historical_orders >= 1
            and historical_acos is not None
            and metric_float(historical_acos) <= profit_acos_cap
        ):
            evidence_window = "independent_history"
            source_orders = historical_orders
            source_spend = metric_float(row.get("historical_spend"))
            source_clicks = metric_float(row.get("historical_clicks"))
            source_acos = metric_float(historical_acos)
            source_cvr = row.get("historical_cvr")
        else:
            continue

        source_cpc = safe_ratio(source_spend, source_clicks) if source_clicks > 0 else None
        bid_high = baseline_max_cpc * 0.40
        if source_cpc is not None and source_cpc > 0:
            bid_high = min(bid_high, source_cpc * 0.60)
        bid_high = max(0.02, round(bid_high, 2))
        bid_low = max(0.02, round(bid_high * 0.75, 2))
        phrase_candidates.append(
            {
                "search_term": search_term,
                "source_match_type": "historical_or_recent_converting_search_term",
                "source_orders": maybe_int(source_orders),
                "source_spend": round(source_spend, 4),
                "source_clicks": maybe_int(source_clicks),
                "source_cpc": source_cpc,
                "source_acos": source_acos,
                "source_cvr": source_cvr,
                "evidence_window": evidence_window,
                "evidence_confidence": "low" if source_orders < 2 else "medium",
                "current_targeting_status": "未确认覆盖",
                "source_term_targeting_status": row.get("current_targeting_status"),
                "source_campaigns": list(row.get("campaigns") or []),
                "source_ad_groups": list(row.get("ad_groups") or []),
                "source_targeting": row.get("source_targeting") or search_term,
                "source_support_terms": [search_term],
                "direct_test_eligible": True,
                "controlled_test_overlay": True,
                "semantic_review_required": True,
                "suggested_bid_min": bid_low,
                "suggested_bid_max": bid_high,
                "suggested_daily_budget_min": 2.0,
                "suggested_daily_budget_max": 2.0,
                "suggested_shared_daily_budget": 2.0,
                "discovery_isolation": "add_negative_exact_for_seed_in_controlled_phrase_campaign",
                "negative_exact_isolation_keyword": search_term,
                "isolation_requires_exact_coverage_confirmed": True,
                "recommended_action": "low_confidence_phrase_test",
                "reason": "profitable_low_sample_term_controlled_phrase_expansion",
                "profit_acos_cap": profit_acos_cap,
                "does_not_authorize_scale": True,
            }
        )
        seen_terms.add(search_term)

    phrase_candidates.sort(
        key=lambda row: (
            -metric_float(row.get("source_orders")),
            metric_float(row.get("source_acos")),
            str(row.get("search_term")),
        )
    )
    phrase_candidates = phrase_candidates[:3]

    recent_sufficient: bool | None = None
    signal_window = daily_window_selection.get("signal_window") or {}
    recent_signal = signal_window.get("recent_sufficiency") or {}
    if recent_signal.get("sufficient") is not None:
        recent_sufficient = bool(recent_signal.get("sufficient"))
    else:
        evaluations = daily_window_selection.get("evaluations") or []
        if evaluations:
            last_sufficiency = (evaluations[-1] or {}).get("sufficiency") or {}
            if last_sufficiency.get("sufficient") is not None:
                recent_sufficient = bool(last_sufficiency.get("sufficient"))

    existing_channels: set[str] = set()
    if match_type_data.get("status") == "ok":
        for pair in match_type_data.get("all_pairs", []) or []:
            existing_channels.update(discovery_channels_for_term(pair))
    auto_close_test = None
    if (
        recent_sufficient is False
        and "auto_close" not in existing_channels
        and clean_text(product.get("title") or product.get("core_category"))
    ):
        auto_close_test = {
            "channel": "auto_close",
            "search_term": "自动紧密",
            "recommended_action": "micro_auto_close_test",
            "suggested_bid": max(0.02, round(baseline_max_cpc * 0.35, 2)),
            "suggested_daily_budget": 2.0,
            "dynamic_bidding": "down_only",
            "placement_adjustment": 0,
            "reason": "recent_signal_insufficient_buy_small_amount_of_listing_driven_evidence",
            "does_not_authorize_auto_loose_or_product_targeting": True,
            "controlled_test_overlay": True,
            "compact_visibility_required": True,
        }

    enabled = bool(phrase_candidates or auto_close_test)
    return {
        "status": "enabled" if enabled else "not_needed",
        "reason": (
            "controlled_tests_available_without_changing_core_actions"
            if enabled
            else "no_profitable_low_sample_phrase_seed_and_recent_signal_not_sparse"
        ),
        "base_recommendation_mode": base_mode,
        "phrase_candidates": phrase_candidates,
        "auto_close_test": auto_close_test,
        **policy,
    }


def period_rows(period_comparison: dict[str, Any], category: str, limit: int = 20) -> list[dict[str, Any]]:
    if period_comparison.get("status") != "ok":
        return []
    rows = period_comparison.get("categories", {}).get(category, [])
    return rows[:limit]


def period_term_names(period_comparison: dict[str, Any], categories: list[str]) -> set[str]:
    names: set[str] = set()
    if period_comparison.get("status") != "ok":
        return names
    category_set = set(categories)
    for row in period_all_rows(period_comparison):
        if row.get("category") not in category_set:
            continue
        term = row.get("search_term")
        if term:
            names.add(term)
    return names


def period_category_map(period_comparison: dict[str, Any]) -> dict[str, str]:
    if period_comparison.get("status") != "ok":
        return {}
    mapping: dict[str, str] = {}
    for row in period_all_rows(period_comparison):
        name = clean_text(row.get("search_term"))
        category = clean_text(row.get("category"))
        if name and category:
            mapping[name] = category
    return mapping


def period_comparison_lookup(period_comparison: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        clean_text(row.get("search_term")): row
        for row in period_all_rows(period_comparison)
        if clean_text(row.get("search_term"))
    }


def historical_evidence_is_independent(period_comparison: dict[str, Any]) -> bool:
    """Return False only when supplied date ranges are known to overlap."""
    overlap_status = (period_comparison.get("date_overlap_check") or {}).get("status")
    return overlap_status != "overlap"


def period_evidence(row: dict[str, Any] | None) -> dict[str, Any]:
    baseline = (row or {}).get("baseline") or {}
    recent = (row or {}).get("recent") or {}
    return {
        "historical_orders": baseline.get("orders"),
        "historical_spend": baseline.get("spend"),
        "historical_acos": baseline.get("acos"),
        "historical_clicks": baseline.get("clicks"),
        "historical_cvr": baseline.get("cvr"),
        "historical_metric_scope": "search_term_all_campaigns" if row else None,
        "recent_orders": recent.get("orders"),
        "recent_spend": recent.get("spend"),
        "recent_acos": recent.get("acos"),
        "recent_clicks": recent.get("clicks"),
        "recent_cvr": recent.get("cvr"),
    }


def historical_recovery_assessment(
    row: dict[str, Any] | None,
    target_acos: float | None,
    history_independent: bool = True,
) -> dict[str, str]:
    baseline = (row or {}).get("baseline") or {}
    orders = metric_float(baseline.get("orders"))
    acos = metric_float(baseline.get("acos")) if baseline.get("acos") is not None else None
    if orders <= 0:
        return {
            "strength": "no_historical_orders",
            "priority": "none",
            "confidence": "low",
            "bid_change": "保持",
            "action": "no_historical_recovery_action",
            "reason": "no_historical_orders_do_not_label_as_recovery",
        }
    if not history_independent:
        return {
            "strength": "overlapping_history_not_independent",
            "priority": "low",
            "confidence": "low",
            "bid_change": "保持",
            "action": "validate_without_bid_increase",
            "reason": "overlapping_period_history_not_independent_no_bid_raise",
        }
    if target_acos and acos is not None and acos > target_acos * 1.2:
        return {
            "strength": "economically_weak_history",
            "priority": "low",
            "confidence": "medium" if orders >= 3 else "low",
            "bid_change": "保持或-5%",
            "action": "diagnose_historical_economics_before_recovery",
            "reason": "historical_acos_materially_above_target_do_not_raise_first",
        }
    if orders >= 3:
        return {
            "strength": "proven_history",
            "priority": "high",
            "confidence": "high",
            "bid_change": "+5%到+10%",
            "action": "prioritized_restore_or_bid_test",
            "reason": "multiple_historical_orders_and_economics_acceptable",
        }
    if orders >= 2:
        return {
            "strength": "moderate_history",
            "priority": "medium",
            "confidence": "medium",
            "bid_change": "+5%",
            "action": "controlled_restore_test",
            "reason": "two_historical_orders_controlled_retest",
        }
    return {
        "strength": "one_order_weak_history",
        "priority": "low",
        "confidence": "low",
        "bid_change": "0%到+5%",
        "action": "low_risk_validation_only",
        "reason": "one_historical_order_is_weak_evidence",
    }


def term_lookup(ad_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if ad_data.get("status") != "ok":
        return {}
    return {term.get("search_term"): term for term in ad_data.get("all_terms", []) if term.get("search_term")}


def infer_campaign_intent(campaign: str, match_types: list[str] | None = None, campaign_types: list[str] | None = None) -> str:
    text = " ".join([campaign, *(match_types or []), *(campaign_types or [])]).lower()
    placements = []
    if any(token in text for token in ["top of search", "tos", "首页", "首位", "顶部"]):
        placements.append("首页")
    if any(token in text for token in ["product page", "product pages", "detail page", "商品页", "产品页", "详情页", "asin"]):
        placements.append("产品页")

    match = None
    if "exact" in text or "精准" in text:
        match = "精准"
    elif "phrase" in text or "词组" in text:
        match = "词组"
    elif "broad" in text or "广泛" in text:
        match = "广泛"

    parts = placements[:1]
    if match:
        parts.append(match)
    return "/".join(parts) if parts else "普通"


def safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def account_metrics(ad_data: dict[str, Any]) -> dict[str, Any]:
    if ad_data.get("status") != "ok":
        return {"status": ad_data.get("status", "not_available")}

    terms = ad_data.get("all_terms", [])
    spend = sum(metric_float(term.get("spend")) for term in terms)
    clicks = sum(metric_float(term.get("clicks")) for term in terms)
    orders = sum(metric_float(term.get("orders")) for term in terms)
    impressions = sum(metric_float(term.get("impressions")) for term in terms)
    sales_terms = [term for term in terms if term.get("ad_sales") is not None]
    ad_sales = sum(metric_float(term.get("ad_sales")) for term in sales_terms) if sales_terms else None
    zero_order_spend = sum(metric_float(term.get("spend")) for term in terms if metric_float(term.get("orders")) == 0)
    click_only_spend = sum(
        metric_float(term.get("spend"))
        for term in terms
        if metric_float(term.get("orders")) == 0 and metric_float(term.get("clicks")) > 0
    )
    converting_spend = sum(metric_float(term.get("spend")) for term in terms if metric_float(term.get("orders")) > 0)

    return {
        "status": "ok",
        "unique_search_terms": len(terms),
        "spend": round(spend, 4),
        "ad_sales": round(ad_sales, 4) if ad_sales is not None else None,
        "orders": maybe_int(orders),
        "clicks": maybe_int(clicks),
        "impressions": maybe_int(impressions),
        "ctr": safe_ratio(clicks, impressions),
        "cpc": safe_ratio(spend, clicks),
        "cvr": safe_ratio(orders, clicks),
        "acos": safe_ratio(spend, ad_sales),
        "roas": safe_ratio(ad_sales, spend),
        "zero_order_spend": round(zero_order_spend, 4),
        "click_only_spend": round(click_only_spend, 4),
        "converting_spend": round(converting_spend, 4),
        "zero_order_spend_share": safe_ratio(zero_order_spend, spend),
        "converting_spend_share": safe_ratio(converting_spend, spend),
        "converting_terms": ad_data.get("converting_count"),
        "click_only_terms": ad_data.get("click_only_count"),
    }


def scaled_weekly_metrics(metrics: dict[str, Any], days: int | None) -> dict[str, Any] | None:
    if metrics.get("status") != "ok" or not days:
        return None
    factor = 7 / days
    spend = metric_float(metrics.get("spend")) * factor
    ad_sales = metric_float(metrics.get("ad_sales")) * factor if metrics.get("ad_sales") is not None else None
    orders = metric_float(metrics.get("orders")) * factor
    clicks = metric_float(metrics.get("clicks")) * factor
    impressions = metric_float(metrics.get("impressions")) * factor
    zero_order_spend = metric_float(metrics.get("zero_order_spend")) * factor
    click_only_spend = metric_float(metrics.get("click_only_spend")) * factor
    converting_spend = metric_float(metrics.get("converting_spend")) * factor
    return {
        "period_days": days,
        "spend": round(spend, 4),
        "ad_sales": round(ad_sales, 4) if ad_sales is not None else None,
        "orders": round(orders, 4),
        "clicks": round(clicks, 4),
        "impressions": round(impressions, 4),
        "cpc": safe_ratio(spend, clicks),
        "cvr": safe_ratio(orders, clicks),
        "acos": safe_ratio(spend, ad_sales),
        "roas": safe_ratio(ad_sales, spend),
        "zero_order_spend": round(zero_order_spend, 4),
        "click_only_spend": round(click_only_spend, 4),
        "converting_spend": round(converting_spend, 4),
        "zero_order_spend_share": safe_ratio(zero_order_spend, spend),
        "converting_spend_share": safe_ratio(converting_spend, spend),
    }


def date_range_days(date_range: dict[str, Any] | None) -> int | None:
    if not date_range:
        return None
    try:
        start = datetime.fromisoformat(str(date_range.get("start"))).date()
        end = datetime.fromisoformat(str(date_range.get("end"))).date()
    except (TypeError, ValueError):
        return None
    days = (end - start).days + 1
    return days if days > 0 else None


def dimension_rollup(dimension_data: dict[str, Any], dimension_name: str, limit: int = 8) -> list[dict[str, Any]]:
    if dimension_data.get("status") != "ok":
        return []
    grouped: dict[str, dict[str, float]] = defaultdict(lambda: {"spend": 0.0, "orders": 0.0, "clicks": 0.0, "ad_sales": 0.0, "zero_order_spend": 0.0})
    sales_available: dict[str, bool] = defaultdict(bool)
    for item in dimension_data.get("all_pairs", []):
        dimension = clean_text(item.get(dimension_name))
        if not dimension:
            continue
        row = grouped[dimension]
        orders = metric_float(item.get("orders"))
        spend = metric_float(item.get("spend"))
        clicks = metric_float(item.get("clicks"))
        row["orders"] += orders
        row["spend"] += spend
        row["clicks"] += clicks
        if item.get("ad_sales") is not None:
            row["ad_sales"] += metric_float(item.get("ad_sales"))
            sales_available[dimension] = True
        if orders == 0:
            row["zero_order_spend"] += spend

    output = []
    for dimension, row in grouped.items():
        ad_sales = row["ad_sales"] if sales_available[dimension] else None
        output.append(
            {
                dimension_name: dimension,
                "spend": round(row["spend"], 4),
                "orders": maybe_int(row["orders"]),
                "clicks": maybe_int(row["clicks"]),
                "ad_sales": round(ad_sales, 4) if ad_sales is not None else None,
                "acos": safe_ratio(row["spend"], ad_sales),
                "cvr": safe_ratio(row["orders"], row["clicks"]),
                "zero_order_spend": round(row["zero_order_spend"], 4),
                "zero_order_spend_share": safe_ratio(row["zero_order_spend"], row["spend"]),
            }
        )
    return sorted(output, key=lambda item: (-metric_float(item.get("spend")), str(item.get(dimension_name))))[:limit]


def build_account_macro_summary(
    recent_ad_data: dict[str, Any],
    baseline_ad_data: dict[str, Any] | None,
    recent_date_range: dict[str, Any] | None,
    baseline_date_range: dict[str, Any] | None,
    match_type_data: dict[str, Any],
    campaign_data: dict[str, Any],
    ad_group_data: dict[str, Any],
) -> dict[str, Any]:
    recent = account_metrics(recent_ad_data)
    baseline = account_metrics(baseline_ad_data or {})
    baseline_days = date_range_days(baseline_date_range)
    baseline_weekly_avg = scaled_weekly_metrics(baseline, baseline_days)

    comparisons = {}
    flags = []
    if recent.get("status") == "ok" and baseline_weekly_avg:
        spend_ratio = safe_ratio(metric_float(recent.get("spend")), metric_float(baseline_weekly_avg.get("spend")))
        sales_ratio = safe_ratio(metric_float(recent.get("ad_sales")), metric_float(baseline_weekly_avg.get("ad_sales")))
        order_ratio = safe_ratio(metric_float(recent.get("orders")), metric_float(baseline_weekly_avg.get("orders")))
        comparisons = {
            "recent_vs_baseline_weekly_spend_ratio": spend_ratio,
            "recent_vs_baseline_weekly_sales_ratio": sales_ratio,
            "recent_vs_baseline_weekly_order_ratio": order_ratio,
        }
        if spend_ratio is not None and spend_ratio >= 2:
            flags.append("spend_spike")
        if recent.get("acos") is not None and baseline.get("acos") is not None and recent["acos"] > baseline["acos"] * 1.2:
            flags.append("acos_worse")
        if recent.get("cvr") is not None and baseline.get("cvr") is not None and recent["cvr"] < baseline["cvr"] * 0.75:
            flags.append("conversion_drop")
        if spend_ratio is not None and spend_ratio >= 2 and (sales_ratio is None or sales_ratio < spend_ratio * 0.7):
            flags.append("spend_growth_outpaced_sales")
    if recent.get("zero_order_spend_share") is not None and recent["zero_order_spend_share"] >= 0.5:
        flags.append("zero_order_waste_high")

    return {
        "status": "ok" if recent.get("status") == "ok" else "not_available",
        "recent_date_range": recent_date_range,
        "baseline_date_range": baseline_date_range,
        "recent": recent,
        "baseline": baseline if baseline.get("status") == "ok" else None,
        "baseline_weekly_avg": baseline_weekly_avg,
        "comparisons": comparisons,
        "flags": flags,
        "top_spend_by_match_type": dimension_rollup(match_type_data, "match_type"),
        "top_spend_by_campaign": dimension_rollup(campaign_data, "campaign"),
        "top_spend_by_ad_group": dimension_rollup(ad_group_data, "ad_group"),
        "model_instruction": (
            "Use these account-level metrics only as account context. Do not route macro diagnosis "
            "into the three numbered sections or derive keyword actions or quick-copy negatives from "
            "them; ground keyword decisions in candidate_actions."
        ),
    }


def build_cpc_control_context(account_macro_summary: dict[str, Any]) -> dict[str, Any]:
    baseline = account_macro_summary.get("baseline") or {}
    recent = account_macro_summary.get("recent") or {}
    flags = set(account_macro_summary.get("flags") or [])
    baseline_clicks = metric_float(baseline.get("clicks"))
    baseline_orders = metric_float(baseline.get("orders"))
    recent_clicks = metric_float(recent.get("clicks"))
    recent_orders = metric_float(recent.get("orders"))
    if baseline_clicks < ACCOUNT_HISTORY_MIN_CLICKS_FOR_STRICT_CPC or baseline_orders < ACCOUNT_HISTORY_MIN_ORDERS_FOR_STRICT_CPC:
        mode = "launch_or_low_history"
        note = "Account/product ad history is limited; CPC thresholds are learning references, not hard cut lines for relevant traffic."
    elif {"conversion_drop", "acos_worse"} & flags:
        mode = "diagnostic_period"
        note = "Historical data exists but recent efficiency weakened; diagnose listing, price, ranking, reviews, inventory, competition, and placement before aggressive CPC cuts."
    else:
        mode = "stable_or_profit_control"
        note = "History is sufficient and no major recent deterioration flag is present; stricter CPC control is appropriate after semantic and period checks."
    return {
        "mode": mode,
        "baseline_clicks": maybe_int(baseline_clicks),
        "baseline_orders": maybe_int(baseline_orders),
        "recent_clicks": maybe_int(recent_clicks),
        "recent_orders": maybe_int(recent_orders),
        "flags": sorted(flags),
        "strict_cpc_history_thresholds": {
            "baseline_clicks": ACCOUNT_HISTORY_MIN_CLICKS_FOR_STRICT_CPC,
            "baseline_orders": ACCOUNT_HISTORY_MIN_ORDERS_FOR_STRICT_CPC,
        },
        "note": note,
    }


def text_words(value: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9]+", value.lower())


def phrase_in_text(phrase: str, text: str) -> bool:
    return f" {phrase.lower()} " in f" {text.lower()} "


def build_root_protected_terms(
    product: dict[str, Any],
    ad_data: dict[str, Any],
    period_comparison: dict[str, Any],
) -> list[dict[str, Any]]:
    if ad_data.get("status") != "ok":
        return []
    all_terms = ad_data.get("all_terms", [])
    converting_names = [
        str(term.get("search_term"))
        for term in all_terms
        if term.get("search_term") and metric_float(term.get("orders")) > 0
    ]
    protected_names = converting_names + sorted(
        period_term_names(
            period_comparison,
            [
                "historically_good_recently_weak",
                "historically_good_no_recent_traffic",
                "consistently_converting",
                "recently_improved",
            ],
        )
    )
    protected_names += [
        str(term)
        for term in period_comparison.get("long_history_protected_terms", []) or []
        if clean_text(term)
    ]
    click_only_names = [
        str(term.get("search_term"))
        for term in all_terms
        if term.get("search_term") and metric_float(term.get("orders")) == 0 and metric_float(term.get("clicks")) > 0
    ]

    roots: dict[str, dict[str, Any]] = {}

    def add_root(root: str, reason: str, source_term: str | None = None) -> None:
        root = " ".join(root.split()).strip()
        if len(text_words(root)) < 2:
            return
        item = roots.setdefault(root, {"term": root, "reasons": set(), "source_terms": set()})
        item["reasons"].add(reason)
        if source_term:
            item["source_terms"].add(source_term)

    for field in ["core_category", "title", "description"]:
        value = clean_text(product.get(field))
        words = text_words(value)
        if 2 <= len(words) <= 6:
            add_root(value, f"product_{field}")
    for value in product.get("key_attributes", []) + product.get("compatibility", []):
        text = clean_text(value)
        if 2 <= len(text_words(text)) <= 6:
            add_root(text, "product_fact")

    for candidate in click_only_names:
        if len(text_words(candidate)) < 2:
            continue
        for protected in protected_names:
            if phrase_in_text(candidate, protected):
                add_root(candidate, "appears_inside_converting_or_historically_protected_term", protected)
                break

    for protected in protected_names:
        words = protected.split()
        if 2 <= len(words) <= 6:
            add_root(protected, "converting_or_historically_protected_full_term", protected)

    output = []
    for item in roots.values():
        output.append(
            {
                "term": item["term"],
                "reasons": sorted(item["reasons"]),
                "source_terms": sorted(item["source_terms"]),
                "negative_phrase_allowed": False,
                "exact_negative_requires_explicit_evidence": True,
            }
        )
    return sorted(output, key=lambda item: (len(item["term"].split()), item["term"]))


def build_manual_targeting_candidates(
    all_terms: list[dict[str, Any]],
    converting: list[dict[str, Any]],
    period_comparison: dict[str, Any],
    comparison_lookup: dict[str, dict[str, Any]],
    lookup: dict[str, dict[str, Any]],
    target_acos: float | None,
) -> dict[str, list[dict[str, Any]]]:
    history_independent = historical_evidence_is_independent(period_comparison)
    keyword_converting_terms = [term for term in converting if not is_auto_product_targeting_term(term)]
    product_targeting_candidates = [
        compact_term_evidence(
            term,
            "analyze_as_product_targeting_not_keyword_targeting",
            {
                "recommended_product_targeting_action": product_targeting_action(term, target_acos),
                "exclude_from_manual_keyword_tables": True,
                **period_evidence(comparison_lookup.get(clean_text(term.get("search_term")))),
            },
        )
        for term in all_terms
        if is_auto_product_targeting_term(term)
        and (
            metric_float(term.get("orders")) > 0
            or metric_float(term.get("clicks")) > CLICK_ONLY_OUTPUT_CLICK_THRESHOLD
        )
    ][:20]

    manual_exact_source_terms = [
        term
        for term in keyword_converting_terms
        if term.get("data_sufficiency") in {"usable", "strong"}
    ]
    manual_exact_candidates = [
        compact_term_evidence(
            term,
            "converting_or_high_intent_manual_exact_candidate",
            period_evidence(comparison_lookup.get(clean_text(term.get("search_term")))),
        )
        for term in manual_exact_source_terms[:25]
    ]
    for row in period_rows(period_comparison, "recently_improved", 10):
        name = row.get("search_term")
        if name and name in lookup and all(item["search_term"] != name for item in manual_exact_candidates):
            term = lookup[name]
            if is_auto_product_targeting_term(term):
                continue
            manual_exact_candidates.append(
                compact_term_evidence(
                    term,
                    "recently_improved_manual_exact_candidate",
                    {"period_category": "recently_improved", **period_evidence(row)},
                )
            )

    historical_recovery_rows = [
        row
        for row in period_all_rows(period_comparison)
        if row.get("category") in {
            "historically_good_no_recent_traffic",
            "historically_good_recently_weak",
        }
    ]
    priority_order = {"high": 0, "medium": 1, "low": 2, "none": 3}
    historical_recovery_rows.sort(
        key=lambda row: (
            priority_order.get(
                historical_recovery_assessment(row, target_acos, history_independent)["priority"],
                9,
            ),
            historical_recovery_assessment(row, target_acos, history_independent)["action"]
            != "diagnose_historical_economics_before_recovery",
            -metric_float((row.get("baseline") or {}).get("orders")),
            metric_float((row.get("baseline") or {}).get("acos")) if (row.get("baseline") or {}).get("acos") is not None else 999,
            -metric_float((row.get("baseline") or {}).get("spend")),
            clean_text(row.get("search_term")),
        )
    )
    for row in historical_recovery_rows[:20]:
        name = clean_text(row.get("search_term"))
        if not name or any(item["search_term"] == name for item in manual_exact_candidates):
            continue
        current_term = lookup.get(name)
        source_term = current_term or term_from_period_side(name, row.get("baseline"))
        if is_auto_product_targeting_term(source_term):
            if not any(item["search_term"] == name for item in product_targeting_candidates):
                product_targeting_candidates.append(
                    compact_term_evidence(
                        source_term,
                        "historical_product_targeting_recovery_review",
                        {
                            "recommended_product_targeting_action": "verify_product_targeting_coverage_then_restore_or_retest",
                            "exclude_from_manual_keyword_tables": True,
                            **period_evidence(row),
                        },
                    )
                )
            continue
        status = manual_targeting_status(current_term) if current_term else "未确认覆盖"
        assessment = historical_recovery_assessment(row, target_acos, history_independent)
        traffic_mode = ((current_term or {}).get("bid_economics") or {}).get("traffic_action_mode")
        recent_orders = metric_float((row.get("recent") or {}).get("orders"))
        recent_clicks = metric_float((row.get("recent") or {}).get("clicks"))
        if current_term and recent_orders == 0 and recent_clicks > LOW_TRAFFIC_CLICK_THRESHOLD:
            recommendation = "hold_or_capped_validation_no_bid_increase"
        elif assessment["action"] == "diagnose_historical_economics_before_recovery":
            recommendation = assessment["action"]
        elif assessment["action"] == "validate_without_bid_increase":
            recommendation = "verify_coverage_and_validate_without_bid_increase"
        elif assessment["action"] == "low_risk_validation_only":
            recommendation = "low_risk_validation_only_after_coverage_check"
        elif traffic_mode and traffic_mode != "increase_test":
            recommendation = "hold_or_capped_validation_no_bid_increase"
        elif status in {"已有精准", "精准重叠"}:
            recommendation = "restore_existing_exact_traffic_do_not_recreate"
        elif status in {"来自词组", "来自自动", "来自广泛"}:
            recommendation = "review_historical_strength_then_harvest_to_exact"
        else:
            recommendation = "verify_current_coverage_then_restore_or_create_exact"
        manual_exact_candidates.append(
            compact_term_evidence(
                source_term,
                "historically_good_low_or_no_recent_traffic_manual_exact_review",
                {
                    "period_category": row.get("category"),
                    "historical_recovery_priority": assessment["priority"],
                    "historical_strength": assessment["strength"],
                    "historical_recovery_confidence": assessment["confidence"],
                    "historical_recovery_bid_change": assessment["bid_change"],
                    "historical_recovery_reason": assessment["reason"],
                    "historical_evidence_independent": history_independent,
                    "current_targeting_status": status,
                    "recommended_manual_action": recommendation,
                    **period_evidence(row),
                },
            )
        )

    def derived_seed(term: dict[str, Any], reason: str) -> dict[str, Any]:
        return compact_term_evidence(
            term,
            reason,
            {
                "source_term_targeting_status": manual_targeting_status(term),
                "current_targeting_status": "未确认覆盖",
                "targeting_status_applies_to": "derived_keyword_requires_coverage_check",
            },
        )

    manual_phrase_seed_candidates = [
        derived_seed(term, "derive_phrase_root_from_this_converting_term_do_not_blindly_copy_if_too_long")
        for term in manual_exact_source_terms
        if 2 <= len(str(term.get("search_term", "")).split()) <= 5
        and term.get("promotion_priority") != "observe"
    ][:20]
    manual_broad_seed_candidates = [
        derived_seed(term, "derive_broad_root_or_attribute_from_this_relevant_converting_term")
        for term in manual_exact_source_terms
        if len(str(term.get("search_term", "")).split()) <= 4
        and term.get("promotion_priority") in {"scale", "steady"}
    ][:15]
    return {
        "manual_exact_candidates": manual_exact_candidates[:35],
        "manual_phrase_seed_candidates": manual_phrase_seed_candidates,
        "manual_broad_seed_candidates": manual_broad_seed_candidates,
        "product_targeting_candidates": product_targeting_candidates[:20],
    }


def negative_source_scope(term: dict[str, Any]) -> dict[str, Any]:
    """Return the smallest discovery source family eligible for keyword negatives."""
    channels = set(discovery_channels_for_term(term))
    eligible = channels & {"manual_broad", "manual_phrase"}
    if eligible == {"manual_broad"}:
        scope = "source_broad_scope"
    elif eligible == {"manual_phrase"}:
        scope = "source_phrase_scope"
    elif eligible:
        scope = "source_broad_and_phrase_scopes_separately"
    else:
        scope = "source_scope_unconfirmed"
    return {
        "source_channels": sorted(channels),
        "negative_source_eligible": bool(eligible),
        "recommended_negative_scope": scope,
    }


def product_gross_profit_per_order(product: dict[str, Any]) -> float | None:
    aov = parse_positive_midpoint(product.get("average_order_value"), "average_order_value")
    margin = parse_margin_range(product.get("gross_margin_range"))
    if not aov or not margin or margin.get("midpoint") is None:
        return None
    return aov * metric_float(margin.get("midpoint"))


def product_profit_acos_cap(product: dict[str, Any]) -> float | None:
    target_acos = parse_rate(product.get("target_acos"), "target_acos")
    margin = parse_margin_range(product.get("gross_margin_range"))
    margin_midpoint = metric_float(margin.get("midpoint")) if margin else None
    values = [value for value in [target_acos, margin_midpoint] if value is not None and value > 0]
    return min(values) if values else None


DISCOVERY_STOPWORDS = {
    "a", "an", "and", "for", "in", "of", "on", "the", "to", "with",
    "best", "new", "tool", "tools", "set", "pack",
}


def _semantic_token(value: str) -> str:
    token = re.sub(r"[^a-z0-9]", "", value.lower())
    if token.startswith("weed"):
        return "weed"
    if token.startswith("garden"):
        return "garden"
    if token.startswith("pull"):
        return "pull"
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("s") and len(token) > 3:
        return token[:-1]
    return token


def semantic_tokens(value: Any) -> list[str]:
    tokens = []
    for raw in re.findall(r"[A-Za-z0-9]+", clean_text(value).lower()):
        token = _semantic_token(raw)
        if token and token not in DISCOVERY_STOPWORDS:
            tokens.append(token)
    return tokens


def product_term_relevance(search_term: str, product: dict[str, Any]) -> dict[str, Any]:
    product_values = [
        product.get("title"), product.get("core_category"), product.get("description"),
        *list(product.get("key_attributes") or []),
        *list(product.get("compatibility") or []),
    ]
    product_tokens = {
        token for value in product_values for token in semantic_tokens(value)
    }
    query_tokens = semantic_tokens(search_term)
    meaningful = [token for token in query_tokens if token not in DISCOVERY_STOPWORDS]
    matched = sorted(set(meaningful) & product_tokens)
    unmatched = sorted(set(meaningful) - product_tokens)
    denominator = max(1, min(3, len(set(meaningful))))
    score = min(1.0, len(matched) / denominator)
    return {
        "score": round(score, 4),
        "matched_tokens": matched,
        "unmatched_tokens": unmatched,
        "query_tokens": meaningful,
    }


def build_zero_order_growth_candidates(
    click_only: list[dict[str, Any]],
    product: dict[str, Any],
    max_total: int,
    max_phrase: int = 2,
) -> dict[str, list[dict[str, Any]]]:
    """Build a shared, capped Exact/Phrase discovery pool when conversions are sparse."""
    exact_candidates: list[dict[str, Any]] = []
    for term in click_only:
        if is_auto_product_targeting_term(term):
            continue
        clicks = metric_float(term.get("clicks"))
        impressions = metric_float(term.get("impressions"))
        if clicks <= 0 or clicks > 8 or impressions < 5:
            continue
        ctr_context = term.get("ctr_context") or {}
        relative_ctr = metric_float(ctr_context.get("relative_ctr"))
        if relative_ctr < 1.25:
            continue
        relevance = product_term_relevance(clean_text(term.get("search_term")), product)
        if relevance["score"] < 0.5:
            continue
        words = semantic_tokens(term.get("search_term"))
        specificity = min(1.0, len(words) / 4) if words else 0.0
        bid = term.get("bid_economics") or {}
        max_cpc = metric_float(bid.get("baseline_max_cpc") or bid.get("max_cpc"))
        cpc = metric_float(term.get("cpc"))
        if max_cpc > 0 and cpc > 0:
            cpc_feasibility = min(1.0, max_cpc / cpc)
        else:
            cpc_feasibility = 0.5
        discovery_score = (
            45 * relevance["score"]
            + 25 * min(1.0, relative_ctr / 2)
            + 15 * specificity
            + 10 * cpc_feasibility
            + 5 * min(1.0, clicks / 8)
        )
        suggested_bid = max(0.20, round(min(value for value in [cpc * 0.70, max_cpc * 0.60] if value > 0), 2)) if (cpc > 0 or max_cpc > 0) else 0.30
        exact_candidates.append(
            compact_term_evidence(
                term,
                "zero_order_high_ctr_high_relevance_exact_validation",
                {
                    "zero_order_potential": True,
                    "recommended_discovery_match_type": "exact",
                    "semantic_relevance_score": relevance["score"],
                    "semantic_matched_tokens": relevance["matched_tokens"],
                    "relative_ctr": relative_ctr,
                    "discovery_score": round(discovery_score, 2),
                    "suggested_bid": suggested_bid,
                    "recommended_action": "controlled_learning",
                    "evidence_confidence": "low",
                    "does_not_authorize_scale": True,
                },
            )
        )
    exact_candidates.sort(
        key=lambda row: (
            -metric_float(row.get("discovery_score")),
            -metric_float(row.get("clicks")),
            str(row.get("search_term")),
        )
    )
    root_support: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in exact_candidates:
        words = clean_text(row.get("search_term")).lower().split()
        for size in range(3, min(5, len(words)) + 1):
            for start in range(0, len(words) - size + 1):
                root = " ".join(words[start:start + size])
                if product_term_relevance(root, product)["score"] >= 0.5:
                    root_support[root].append(row)
    phrase_candidates: list[dict[str, Any]] = []
    for root, support in root_support.items():
        unique_terms = {clean_text(row.get("search_term")) for row in support}
        if len(unique_terms) < 2:
            continue
        best = max(support, key=lambda row: metric_float(row.get("discovery_score")))
        row = dict(best)
        row.update({
            "search_term": root,
            "source_support_terms": sorted(unique_terms),
            "supporting_term_count": len(unique_terms),
            "recommended_discovery_match_type": "phrase",
            "reason": "repeated_high_relevance_zero_order_root_phrase_micro_test",
            "discovery_score": round(metric_float(best.get("discovery_score")) * 0.95, 2),
            "suggested_bid": max(0.20, round(metric_float(best.get("suggested_bid")) * 0.85, 2)),
        })
        phrase_candidates.append(row)
    phrase_candidates.sort(
        key=lambda row: (
            -metric_float(row.get("supporting_term_count")),
            -metric_float(row.get("discovery_score")),
            str(row.get("search_term")),
        )
    )
    # Exact and Phrase candidates share one display allowance. Reserve at most
    # one Phrase slot when conversions already exist (max_total <= 2), and at
    # most two otherwise. Remove Phrase-supporting complete queries before
    # filling remaining Exact slots so the compact plan does not buy duplicate
    # evidence for the same query cluster.
    phrase_slot_cap = min(max_phrase, 1 if max_total <= 2 else 2, max_total)
    selected_phrases = phrase_candidates[:phrase_slot_cap]
    covered_terms = {
        clean_text(term)
        for row in selected_phrases
        for term in (row.get("source_support_terms") or [])
        if clean_text(term)
    }
    ranked_exact = [
        *[row for row in exact_candidates if clean_text(row.get("search_term")) not in covered_terms],
        *[row for row in exact_candidates if clean_text(row.get("search_term")) in covered_terms],
    ]
    selected_exact = ranked_exact[:max(0, max_total - len(selected_phrases))]
    return {
        "zero_order_exact_candidates": selected_exact,
        "zero_order_phrase_candidates": selected_phrases,
    }


def keyword_placement_plan(
    row: dict[str, Any],
    family: str,
    product: dict[str, Any],
) -> dict[str, Any]:
    """Route a new keyword/target by traffic intent without changing its core action."""
    minimum_controlled_adjustment = 20
    default_controlled_adjustment = PLACEMENT_DEFAULT_UPLIFT_PCT
    zero_adjustments = {
        "rest_of_search": 0,
        "top_of_search": 0,
        "product_pages": 0,
    }

    def plan(
        mode: str,
        adjustments: dict[str, int],
        campaign_bucket: str,
        confidence: str,
        reason: str,
        *,
        top_only_eligible: bool = False,
    ) -> dict[str, Any]:
        normalized = {
            key: max(0, int(value))
            for key, value in adjustments.items()
        }
        if any(0 < value < minimum_controlled_adjustment for value in normalized.values()):
            raise ValueError("Controlled placement adjustments must be 0% or at least 20%")
        max_adjustment = max(normalized.values(), default=0)
        return {
            "mode": mode,
            "adjustments": normalized,
            "campaign_bucket": campaign_bucket,
            "confidence": confidence,
            "reason": reason,
            "dynamic_bidding": "down_only",
            "non_negative_adjustments_enforced": True,
            "minimum_controlled_adjustment": minimum_controlled_adjustment,
            "default_controlled_adjustment": default_controlled_adjustment,
            "top_only_eligible_with_explicit_ranking_goal": top_only_eligible,
            "does_not_override_keyword_action": True,
            "placement_control_required": mode != "no_placement_test",
            "all_zero_learning_only": mode == "no_placement_test",
            "base_bid_backsolve_required": False,
            "base_bid_divisor": round(1 + max_adjustment / 100, 4),
        }

    no_test = lambda reason: plan(
        "no_placement_test",
        zero_adjustments,
        "Keep-Current-Structure",
        "low",
        reason,
    )
    if family == "auto_learning":
        return no_test("collect_placement_evidence_first_all_adjustments_zero")
    if family in {"manual_phrase", "manual_broad"}:
        bucket = "Phrase-Rest" if family == "manual_phrase" else "Broad-Rest"
        return plan(
            "rest_only_test",
            {"rest_of_search": default_controlled_adjustment, "top_of_search": 0, "product_pages": 0},
            bucket,
            "low" if row.get("controlled_test_overlay") else "medium",
            "discovery_or_long_tail_start_at_rest_of_search",
        )

    profit_acos_cap = product_profit_acos_cap(product)
    if family == "product_targeting":
        orders = metric_float(row.get("orders"))
        acos = row.get("acos")
        if (
            orders > 0
            and profit_acos_cap is not None
            and acos is not None
            and metric_float(acos) <= profit_acos_cap
        ):
            return plan(
                "product_page_test",
                {"rest_of_search": 0, "top_of_search": 0, "product_pages": default_controlled_adjustment},
                "Product-Page",
                "low" if orders < 3 else "medium",
                "profitable_product_target_use_separate_product_page_campaign",
            )
        return no_test("product_target_not_yet_profitable_enough_for_product_page_control")
    if family != "manual_exact" or profit_acos_cap is None:
        return no_test("placement_economics_or_target_family_unavailable")

    orders = metric_float(
        row.get("recent_orders") if row.get("recent_orders") is not None else row.get("orders")
    )
    clicks = metric_float(
        row.get("recent_clicks") if row.get("recent_clicks") is not None else row.get("clicks")
    )
    acos = row.get("recent_acos") if row.get("recent_acos") is not None else row.get("acos")
    cpc = row.get("cpc")
    max_cpc = row.get("max_cpc", row.get("baseline_max_cpc"))
    action_eligibility = row.get("action_eligibility")
    current_profitable = bool(
        orders > 0
        and clicks > 0
        and orders <= clicks
        and acos is not None
        and metric_float(acos) <= profit_acos_cap
        and not row.get("abnormal_attribution_sample")
        and (cpc is None or max_cpc is None or metric_float(cpc) <= metric_float(max_cpc))
    )
    historical_profitable = bool(
        metric_float(row.get("historical_orders")) > 0
        and metric_float(row.get("historical_clicks")) > 0
        and metric_float(row.get("historical_orders")) <= metric_float(row.get("historical_clicks"))
        and row.get("historical_acos") is not None
        and metric_float(row.get("historical_acos")) <= profit_acos_cap
        and row.get("historical_evidence_independent") is not False
    )
    if action_eligibility == "scale_ready" and current_profitable and orders >= 3:
        top_adjustment = default_controlled_adjustment
        return plan(
            "rest_and_top",
            {"rest_of_search": default_controlled_adjustment, "top_of_search": top_adjustment, "product_pages": 0},
            "Exact-Rest-Top",
            "high",
            "profitable_scale_ready_exact_use_search_placements",
            top_only_eligible=bool(orders >= 5 and row.get("market_volume_tier") == "big/core"),
        )
    if current_profitable and action_eligibility in {
        "low_confidence_validation",
        "controlled_scale_test",
    }:
        return plan(
            "rest_only_test",
            {"rest_of_search": default_controlled_adjustment, "top_of_search": 0, "product_pages": 0},
            "Exact-Rest",
            "low" if action_eligibility == "low_confidence_validation" else "medium",
            "profitable_but_low_sample_exact_start_at_rest_of_search",
        )
    if historical_profitable and orders <= 0:
        return plan(
            "rest_only_test",
            {"rest_of_search": default_controlled_adjustment, "top_of_search": 0, "product_pages": 0},
            "Exact-Rest",
            "low",
            "independent_profitable_history_low_volume_rest_of_search_validation",
        )
    reliable_search_evidence = bool(
        orders > 0
        and clicks > 0
        and orders <= clicks
        and not row.get("abnormal_attribution_sample")
    )
    if reliable_search_evidence and action_eligibility in {
        "profit_repair",
        "historical_protected_hold",
    }:
        return plan(
            "rest_only_control",
            {"rest_of_search": default_controlled_adjustment, "top_of_search": 0, "product_pages": 0},
            "Exact-Rest",
            "medium",
            "search_intent_exact_preassign_rest_placement_before_launch",
        )
    return no_test("explicit_learning_or_insufficient_search_evidence_all_adjustments_zero")


def enrich_row_with_placement_bid_math(row: dict[str, Any]) -> None:
    """Keep optional placement math as a ceiling, never a default bid reduction."""
    plan = row.get("placement_plan") or {}
    adjustments = plan.get("adjustments") or {}
    uplift = max([metric_float(value) for value in adjustments.values()] or [0])
    for suffix in ("min", "max"):
        value = row.get(f"suggested_bid_{suffix}")
        if value is not None:
            row[f"placement_effective_bid_{suffix}"] = round(
                metric_float(value) * (1 + uplift / 100), 2
            )


def enrich_candidate_actions_with_placement_plans(
    candidate_actions: dict[str, Any],
    product: dict[str, Any],
    placement_enabled: bool = True,
) -> None:
    family_by_key = {
        "manual_exact_candidates": "manual_exact",
        "manual_phrase_seed_candidates": "manual_phrase",
        "manual_broad_seed_candidates": "manual_broad",
        "product_targeting_candidates": "product_targeting",
    }
    for key, family in family_by_key.items():
        for row in candidate_actions.get(key, []) or []:
            if placement_enabled:
                row["placement_plan"] = keyword_placement_plan(row, family, product)
                enrich_row_with_placement_bid_math(row)
            else:
                row.pop("placement_plan", None)


def _traffic_feasibility_evidence(row: dict[str, Any]) -> dict[str, Any]:
    """Return a CPC-based traffic floor only for already-profitable converting terms."""
    orders = metric_float(
        row.get("recent_orders") if row.get("recent_orders") is not None else row.get("orders")
    )
    clicks = metric_float(
        row.get("recent_clicks") if row.get("recent_clicks") is not None else row.get("clicks")
    )
    acos = row.get("recent_acos") if row.get("recent_acos") is not None else row.get("acos")
    actual_cpc = (
        row.get("cpc")
        if row.get("cpc") is not None
        else row.get("source_cpc")
        if row.get("source_cpc") is not None
        else row.get("recent_cpc")
    )
    profit_acos_cap = row.get("profit_acos_cap")
    action_eligibility = str(row.get("action_eligibility") or "")
    evidence = {
        "traffic_reference_cpc": round(metric_float(actual_cpc), 2) if metric_float(actual_cpc) > 0 else None,
        "traffic_cpc_floor": None,
        "traffic_floor_discount": None,
        "traffic_floor_confidence": None,
        "traffic_feasibility_status": "not_eligible",
        "traffic_floor_applied": False,
    }
    if (
        orders <= 0
        or clicks < 3
        or orders > clicks
        or bool(row.get("abnormal_attribution_sample"))
        or acos is None
        or profit_acos_cap is None
        or metric_float(acos) > metric_float(profit_acos_cap)
        or metric_float(actual_cpc) <= 0
        or action_eligibility in {"profit_repair", "observe", "historical_protected_hold"}
    ):
        return evidence

    if action_eligibility == "scale_ready":
        discount = 1.00
        confidence = "high"
    elif action_eligibility == "controlled_scale_test":
        discount = 0.95
        confidence = "medium"
    else:
        discount = 0.90
        confidence = "low"
    evidence.update({
        "traffic_cpc_floor": round(metric_float(actual_cpc) * discount, 2),
        "traffic_floor_discount": discount,
        "traffic_floor_confidence": confidence,
        "traffic_feasibility_status": "eligible",
    })
    return evidence


def _launch_effective_bid(
    row: dict[str, Any],
    currency_symbol: str = "$",
) -> tuple[float, str, dict[str, Any]]:
    """Return the planned effective bid after action, traffic, and profit controls."""
    traffic_evidence = _traffic_feasibility_evidence(row)
    if metric_float(row.get("target_bid")) > 0:
        traffic_evidence["traffic_feasibility_status"] = "explicit_target_bid"
        return (
            round(metric_float(row.get("target_bid")), 2),
            "采用Section 1单一目标竞价；显式修复/动作目标不受跑量下限抬高",
            traffic_evidence,
        )
    if metric_float(row.get("suggested_bid_min")) > 0:
        traffic_evidence["traffic_feasibility_status"] = "explicit_test_range"
        return (
            round(metric_float(row.get("suggested_bid_min")), 2),
            "采用候选测试竞价下限",
            traffic_evidence,
        )
    if metric_float(row.get("suggested_bid")) > 0:
        traffic_evidence["traffic_feasibility_status"] = "explicit_test_bid"
        return (
            round(metric_float(row.get("suggested_bid")), 2),
            "采用引擎计算的受控测试竞价",
            traffic_evidence,
        )
    cap = row.get("max_cpc") or row.get("baseline_max_cpc") or row.get("profit_cpc_cap")
    current = row.get("source_bid") or row.get("cpc") or row.get("recent_cpc")
    cap_value = metric_float(cap) if cap is not None else 0.0
    current_value = metric_float(current)
    action = str(row.get("recommended_action") or row.get("growth_stage") or "")
    if current_value <= 0 and cap_value <= 0:
        return 0.30, f"无有效源竞价/CPC，使用{currency_symbol}0.30受控起始价", traffic_evidence
    if current_value <= 0:
        current_value = cap_value
    if "increase" in action or row.get("action_eligibility") == "scale_ready":
        value = current_value * 1.05
    elif any(token in action for token in ["down", "repair", "diagnose", "control"]):
        value = current_value * 0.90
    else:
        value = current_value
    traffic_floor = metric_float(traffic_evidence.get("traffic_cpc_floor"))
    if traffic_floor > 0 and value < traffic_floor:
        value = traffic_floor
        traffic_evidence["traffic_floor_applied"] = True
        traffic_evidence["traffic_feasibility_status"] = "applied"
        bid_reason = (
            f"盈利词按实际CPC的{metric_float(traffic_evidence.get('traffic_floor_discount')) * 100:.0f}%"
            "设置跑量下限"
        )
    elif traffic_floor > 0:
        bid_reason = "源竞价/动作价已达到实际CPC跑量下限"
    else:
        bid_reason = "按当前源竞价/CPC与利润CPC上限反算"
    if cap_value > 0:
        if traffic_floor > cap_value:
            traffic_evidence["traffic_feasibility_status"] = "profit_cap_conflict"
        value = min(value, cap_value)
    return round(max(value, 0.20), 2), bid_reason, traffic_evidence


def calculate_campaign_daily_budget(
    campaign_rows: list[dict[str, Any]],
    recent_cvr: float | None,
    baseline_cvr: float | None,
    placement_enabled: bool,
) -> dict[str, Any]:
    """Plan one campaign budget from existing CVR evidence and launch bids.

    This deliberately consumes only the already-prepared account CVRs and
    campaign rows. It must not read source reports or re-aggregate clicks and
    orders, because this is a launch-budget sizing rule rather than a second
    conversion-rate calculation.
    """
    valid_cvrs = [
        value for value in [recent_cvr, baseline_cvr]
        if value is not None and value > 0
    ]
    planning_cvr = min(valid_cvrs) if valid_cvrs else None

    keyword_count = len({
        row["keyword"]
        for row in campaign_rows
        if row.get("keyword")
    })

    if planning_cvr:
        conversion_cycle_clicks = math.ceil(1 / planning_cvr)
    else:
        conversion_cycle_clicks = 10

    budget_click_target = min(
        10,
        max(
            5,
            conversion_cycle_clicks,
            3 * keyword_count,
        ),
    )

    bid_values = []
    for row in campaign_rows:
        if placement_enabled and row.get("effective_bid_ceiling"):
            bid_values.append(row["effective_bid_ceiling"])
        elif row.get("suggested_base_bid"):
            bid_values.append(row["suggested_base_bid"])

    planning_cpc = max(bid_values) if bid_values else None
    suggested_daily_budget = (
        math.ceil(budget_click_target * planning_cpc * 1.15)
        if planning_cpc
        else None
    )

    return {
        "planning_cvr": planning_cvr,
        "budget_click_target": budget_click_target,
        "planning_cpc": planning_cpc,
        "suggested_daily_budget": suggested_daily_budget,
    }


def _campaign_plan_row(
    row: dict[str, Any],
    family: str,
    mode: str,
    date_stamp: str,
    placement_enabled: bool,
    profit_context: dict[str, Any] | None = None,
    cvr_baseline: dict[str, Any] | None = None,
    currency_symbol: str = "$",
) -> dict[str, Any]:
    term = clean_text(row.get("search_term"))
    plan = row.get("placement_plan") if placement_enabled else None
    calculation_plan = plan or {
        "mode": "no_placement_test",
        "adjustments": {"rest_of_search": 0, "top_of_search": 0, "product_pages": 0},
        "campaign_bucket": "Keep-Current-Structure",
        "dynamic_bidding": "down_only",
    }
    planned_effective_bid, bid_reason, traffic_evidence = _launch_effective_bid(row, currency_symbol)
    suggested_base_bid = planned_effective_bid
    adjustments = calculation_plan.get("adjustments") or {}
    max_adjustment = max([metric_float(value) for value in adjustments.values()] or [0])
    target_cpc_candidates = [
        metric_float(value)
        for value in [row.get("baseline_max_cpc"), row.get("max_cpc"), row.get("profit_cpc_cap")]
        if metric_float(value) > 0
    ]
    target_cpc = round(min(target_cpc_candidates), 2) if target_cpc_candidates else None
    placement_divisor = metric_float(calculation_plan.get("base_bid_divisor")) or (
        1 + max_adjustment / 100
    )
    backsolve_required = bool(calculation_plan.get("base_bid_backsolve_required"))
    safety_cap_applied = False
    if placement_enabled and backsolve_required:
        safe_effective_bid = min(planned_effective_bid, target_cpc) if target_cpc else planned_effective_bid
        safety_cap_applied = bool(target_cpc and planned_effective_bid > target_cpc)
        suggested_base_bid = round(safe_effective_bid / placement_divisor, 2)
        effective_bid_ceiling = round(suggested_base_bid * placement_divisor, 2)
        placement_reason = (
            f"无可靠广告位证据，全部位置预置{PLACEMENT_DEFAULT_UPLIFT_PCT}%；"
            f"基础竞价已除以{PLACEMENT_FALLBACK_DIVISOR:g}，计划有效竞价不增加"
            if not safety_cap_applied
            else f"计划有效竞价先按利润CPC安全线封顶，再除以{PLACEMENT_FALLBACK_DIVISOR:g}"
            f"预置全部位置{PLACEMENT_DEFAULT_UPLIFT_PCT}%"
        )
        bid_reason = f"{bid_reason}；{placement_reason}"
    else:
        effective_bid_ceiling = round(suggested_base_bid * (1 + max_adjustment / 100), 2)
    if placement_enabled and target_cpc and effective_bid_ceiling > target_cpc:
        cap_divisor = placement_divisor if backsolve_required else (1 + max_adjustment / 100)
        suggested_base_bid = math.floor((target_cpc / cap_divisor) * 100 + 1e-9) / 100
        effective_bid_ceiling = round(suggested_base_bid * (1 + max_adjustment / 100), 2)
        safety_cap_applied = True
        bid_reason += "；美分取整后重新按利润CPC安全线封顶"
    base_bid = suggested_base_bid
    source_bid = row.get("source_bid")
    if source_bid is None:
        source_bid = row.get("cpc")
    if source_bid is not None and metric_float(source_bid) > 0:
        adjustment_pct = round((base_bid / metric_float(source_bid) - 1) * 100, 1)
    else:
        adjustment_pct = None
    family_label = {
        "manual_exact": "精准",
        "manual_phrase": "词组",
        "manual_broad": "广泛",
        "auto_learning": "自动紧密",
    }.get(family, family)
    bucket = str(calculation_plan.get("campaign_bucket") or "Keep-Current-Structure")
    source_campaigns = list(row.get("source_campaigns") or row.get("campaigns") or [])
    source_ad_groups = list(row.get("source_ad_groups") or row.get("ad_groups") or [])
    source_campaign = row.get("source_campaign") or (source_campaigns or [None])[0]
    source_ad_group = row.get("source_ad_group") or (source_ad_groups or [None])[0]
    campaign_name = f"NEW-{family_label}-{bucket}-{date_stamp}"
    profit_cap = row.get("max_cpc") or row.get("baseline_max_cpc")
    source_match_text = clean_text(row.get("source_match_type")).lower()
    source_family = source_match_family(source_match_text)
    source_target_text = clean_text(row.get("source_targeting"))
    source_target_matches = not source_target_text or source_target_text.casefold() == term.casefold()
    direct_target_history = bool(
        source_bid is not None
        and source_target_matches
        and (
            family == "manual_exact" and (not source_match_text or source_family == "exact")
            or family == "manual_phrase" and source_family == "phrase"
            or family == "manual_broad" and source_family == "broad"
            or family == "auto_learning" and source_family == "auto"
        )
    )
    historical_orders = row.get("historical_orders")
    historical_spend = row.get("historical_spend")
    historical_acos = row.get("historical_acos")
    recent_orders = row.get("recent_orders") if row.get("recent_orders") is not None else row.get("orders")
    recent_spend = row.get("recent_spend") if row.get("recent_spend") is not None else row.get("spend")
    recent_acos = row.get("recent_acos") if row.get("recent_acos") is not None else row.get("acos")
    supporting_orders = row.get("source_orders", row.get("orders"))
    supporting_spend = row.get("source_spend", row.get("spend"))
    supporting_clicks = row.get("source_clicks", row.get("clicks"))
    supporting_cpc = row.get("source_cpc", row.get("cpc"))
    supporting_acos = row.get("source_acos", row.get("acos"))
    supporting_cvr = row.get("source_cvr", row.get("cvr"))
    supporting_terms = row.get("source_support_terms") or [term]
    campaign_count = len({clean_text(value) for value in source_campaigns if clean_text(value)})
    pre_negative_terms = []
    isolation_term = clean_text(row.get("negative_exact_isolation_keyword"))
    if isolation_term:
        pre_negative_terms.append(isolation_term)
    if not direct_target_history and family == "manual_exact":
        reference_note = "无该精准目标直接历史；使用来源精准投放与搜索词证据"
    elif not direct_target_history:
        reference_note = "无该投放直接历史；使用来源搜索词/词根派生证据"
    else:
        reference_note = "有该投放历史数据" if source_bid is not None else "无直接投放历史；使用来源词/词根证据"
    gross_profit_value = row.get("estimated_gross_profit_per_order")
    if gross_profit_value is None:
        gross_profit_value = (profit_context or {}).get("estimated_gross_profit_per_order")
    gross_profit = metric_float(gross_profit_value) if gross_profit_value is not None else None
    watch_spend = round(gross_profit, 2) if gross_profit and gross_profit > 0 else None
    strong_spend = round(gross_profit * 2, 2) if gross_profit and gross_profit > 0 else None
    pause_spend = round(gross_profit * 3, 2) if gross_profit and gross_profit > 0 else None
    baseline_cvr_value = metric_float((cvr_baseline or {}).get("value"))
    clicks_at_watch = round(watch_spend / base_bid, 1) if watch_spend is not None and base_bid > 0 else None
    zero_probability_at_watch = (
        round((1 - baseline_cvr_value) ** clicks_at_watch, 4)
        if clicks_at_watch is not None and 0 < baseline_cvr_value < 1
        else None
    )
    stop_rule = None
    if watch_spend is not None and strong_spend is not None and pause_spend is not None:
        stop_rule = (
            f"0单：{currency_symbol}{watch_spend:.2f}降5%｜{currency_symbol}{strong_spend:.2f}降10%-15%｜"
            f"{currency_symbol}{pause_spend:.2f}暂停复核；"
            "有单：累计2单后ACoS仍超利润线再降10%"
        )
    result = {
        "new_campaign_name": campaign_name,
        "ad_group": f"{family_label}-{term[:32]}",
        "keyword": term,
        "match_type": family_label,
        "family": family,
        "source_campaign": source_campaign,
        "source_ad_group": source_ad_group,
        "source_campaigns": source_campaigns,
        "source_ad_groups": source_ad_groups,
        "source_scope_count": row.get("source_scope_count") or campaign_count,
        "source_bid_range": row.get("source_bid_range") or [],
        "source_target_bid_range": row.get("source_target_bid_range") or [],
        "consolidated_target_bid": row.get("consolidated_target_bid"),
        "consolidated_bid_method": row.get("consolidated_bid_method"),
        "source_bid_merge_evidence": row.get("source_bid_merge_evidence") or [],
        "source_placement_evidence": row.get("source_placement_evidence") or [],
        "placement_evidence_confidence": row.get("placement_evidence_confidence"),
        "placement_source_campaigns": row.get("placement_source_campaigns") or [],
        "product_page_separate_targeting_required": bool(row.get("product_page_separate_targeting_required")),
        "source_targeting": row.get("source_targeting") or term,
        "source_match_type": row.get("source_match_type") or row.get("current_targeting_status"),
        "targeting": row.get("source_targeting") or term,
        "source_bid": round(metric_float(source_bid), 2) if direct_target_history else None,
        "source_reference_bid": round(metric_float(source_bid), 2) if source_bid is not None else None,
        "source_bid_date": row.get("source_bid_date"),
        "actual_cpc": row.get("cpc", row.get("source_cpc")),
        "profit_cpc_cap": profit_cap,
        "historical_orders": historical_orders,
        "historical_spend": historical_spend,
        "historical_acos": historical_acos,
        "recent_orders": recent_orders,
        "recent_spend": recent_spend,
        "recent_acos": recent_acos,
        "recent_clicks": row.get("recent_clicks", row.get("clicks")),
        "zero_order_potential": bool(row.get("zero_order_potential")),
        "recommended_discovery_match_type": row.get("recommended_discovery_match_type"),
        "discovery_score": row.get("discovery_score"),
        "semantic_relevance_score": row.get("semantic_relevance_score"),
        "relative_ctr": row.get("relative_ctr"),
        "action_eligibility": row.get("action_eligibility"),
        "controlled_test_overlay": bool(row.get("controlled_test_overlay")),
        "compact_visibility_required": bool(
            row.get("compact_visibility_required") or row.get("controlled_test_overlay")
        ),
        "local_conversion_confidence": row.get("local_conversion_confidence"),
        "abnormal_attribution_sample": bool(row.get("abnormal_attribution_sample")),
        "scale_blockers": row.get("scale_blockers") or [],
        "profit_acos_cap": row.get("profit_acos_cap") or (profit_context or {}).get("profit_acos_cap"),
        "supporting_orders": supporting_orders,
        "supporting_spend": supporting_spend,
        "supporting_clicks": supporting_clicks,
        "supporting_cpc": supporting_cpc,
        "supporting_acos": supporting_acos,
        "supporting_cvr": supporting_cvr,
        "supporting_term_count": len(supporting_terms),
        "supporting_terms": supporting_terms,
        "evidence_window": row.get("evidence_window"),
        "evidence_confidence": row.get("evidence_confidence"),
        "direct_target_history": direct_target_history,
        "campaign_count": campaign_count,
        "metric_scope_note": (
            f"近期为跨{campaign_count}个活动词级合计" if campaign_count > 1 else "近期为词级聚合"
        ),
        "historical_metric_scope": row.get("historical_metric_scope"),
        "reference_note": reference_note,
        "pre_negative_terms": pre_negative_terms,
        "pre_negative_group": None,
        "exact_validation": row.get("exact_validation") or {},
        "target_cpc": target_cpc,
        "planned_effective_bid": planned_effective_bid,
        **traffic_evidence,
        "suggested_base_bid": base_bid,
        "section1_target_bid": base_bid,
        "effective_bid_ceiling": effective_bid_ceiling if placement_enabled else None,
        "effective_bid_ceiling_reason": (
            "目标位置基础竞价×广告位溢价，仅作上限参考"
            if placement_enabled and not safety_cap_applied
            else "超过利润CPC安全线后封顶"
            if placement_enabled
            else None
        ),
        "base_bid_safety_cap_applied": safety_cap_applied,
        "base_bid_backsolve_applied": bool(placement_enabled and backsolve_required),
        "placement_preload_control": bool(calculation_plan.get("placement_preload_control")),
        "bid_adjustment_pct": adjustment_pct,
        "bid_reason": bid_reason,
        "dynamic_bidding": "只降低",
        "isolation_stop_rule": stop_rule,
        "zero_order_stop_tiers": {
            "watch_spend": watch_spend,
            "watch_action": "bid_down_5",
            "strong_spend": strong_spend,
            "strong_action": "bid_down_10_to_15",
            "pause_review_spend": pause_spend,
            "pause_action": "pause_and_review",
            "baseline_cvr": baseline_cvr_value or None,
            "estimated_clicks_at_watch": clicks_at_watch,
            "zero_order_probability_at_watch": zero_probability_at_watch,
        },
        "campaign_plan_mode": mode,
        "numeric_bid_required": True,
    }
    if placement_enabled:
        result.update({
            "suggested_effective_bid": effective_bid_ceiling,
            "target_placement": calculation_plan,
            "placement_adjustments": adjustments,
            "placement_adjustment_pct": max_adjustment,
        })
    else:
        for key in [
            "source_placement_evidence", "placement_evidence_confidence",
            "placement_source_campaigns", "product_page_separate_targeting_required",
            "effective_bid_ceiling", "effective_bid_ceiling_reason",
            "base_bid_safety_cap_applied",
        ]:
            result.pop(key, None)
    return result


def build_rebuild_campaign_plan(
    candidate_actions: dict[str, Any],
    mature_growth_context: dict[str, Any],
    controlled_exploration: dict[str, Any],
    campaign_plan_mode: str,
    placement_enabled: bool,
    placement_analysis: dict[str, Any] | None = None,
    profit_context: dict[str, Any] | None = None,
    cvr_baseline: dict[str, Any] | None = None,
    account_recent_cvr: float | None = None,
    currency_symbol: str = "$",
) -> dict[str, Any]:
    """Create one directly executable new-campaign table when old campaigns are closed."""
    if campaign_plan_mode != "rebuild":
        return {"status": "not_requested", "rows": [], "mode": campaign_plan_mode}
    rows: list[dict[str, Any]] = []
    stamp = datetime.now().strftime("%Y%m%d")
    seen: set[tuple[str, str]] = set()
    campaign_action_rows = candidate_actions.get("campaign_action_candidates", []) or []
    campaign_bid_merge_rows = (
        candidate_actions.get("campaign_bid_merge_sources", [])
        or campaign_action_rows
    )
    action_by_id = {
        clean_text(item.get("action_id")): item
        for item in campaign_action_rows
        if clean_text(item.get("action_id"))
    }
    sources = [
        ("manual_exact_candidates", "manual_exact"),
        ("manual_phrase_seed_candidates", "manual_phrase"),
        ("manual_broad_seed_candidates", "manual_broad"),
        ("zero_order_exact_candidates", "manual_exact"),
        ("zero_order_phrase_candidates", "manual_phrase"),
    ]

    placement_summaries = {
        normalize_campaign_name(item.get("campaign_key")): item
        for item in ((placement_analysis or {}).get("campaign_summaries") or [])
        if normalize_campaign_name(item.get("campaign_key"))
    }

    def match_family(value: Any) -> str:
        text = clean_text(value).casefold()
        if any(token in text for token in ["exact", "精准", "精确"]):
            return "exact"
        if any(token in text for token in ["phrase", "词组"]):
            return "phrase"
        if any(token in text for token in ["broad", "广泛"]):
            return "broad"
        if any(token in text for token in ["auto", "紧密", "close"]):
            return "auto"
        return text

    def consolidate_exact_source_bids(row: dict[str, Any], term: str) -> None:
        """Merge compatible direct-Exact source targets across every source campaign."""
        target = clean_text(row.get("source_targeting") or term)
        family = match_family(row.get("source_match_type"))
        # A harvested query from Phrase/Broad is a new Exact target, not direct
        # target history. Keep its source reference bid instead of merging
        # unrelated search-term action bids.
        if target.casefold() != term.casefold() or (family and family != "exact"):
            return

        matching_actions = []
        for item in campaign_bid_merge_rows:
            if clean_text(item.get("search_term")).casefold() != term.casefold():
                continue
            item_target = clean_text(item.get("source_targeting") or term)
            item_family = match_family(item.get("source_match_type"))
            if item_target.casefold() != target.casefold():
                continue
            if family and item_family and item_family != family:
                continue
            reliable_target = metric_float(item.get("target_bid"))
            reliable_source = metric_float(item.get("source_bid"))
            if reliable_target <= 0 and reliable_source <= 0:
                continue
            source = dict(item)
            source["merge_bid"] = reliable_target if reliable_target > 0 else reliable_source
            source["merge_bid_source"] = "target_bid" if reliable_target > 0 else "source_bid_fallback"
            matching_actions.append(source)
        if not matching_actions:
            return
        weighted_rows = [item for item in matching_actions if metric_float(item.get("clicks")) > 0]
        if weighted_rows:
            total_clicks = sum(metric_float(item.get("clicks")) for item in weighted_rows)
            consolidated_bid = sum(
                metric_float(item.get("merge_bid")) * metric_float(item.get("clicks"))
                for item in weighted_rows
            ) / total_clicks
            method = "click_weighted"
        else:
            reliable_targets = [
                metric_float(item.get("target_bid"))
                for item in matching_actions
                if metric_float(item.get("target_bid")) > 0
            ]
            if reliable_targets:
                consolidated_bid = min(reliable_targets)
                method = "conservative_minimum_target"
            else:
                consolidated_bid = min(metric_float(item.get("source_bid")) for item in matching_actions)
                method = "conservative_minimum_source_bid"

        source_bids = sorted({
            round(metric_float(item.get("source_bid")), 2)
            for item in matching_actions
            if metric_float(item.get("source_bid")) > 0
        })
        source_targets = sorted({
            round(metric_float(item.get("target_bid")), 2)
            for item in matching_actions
            if metric_float(item.get("target_bid")) > 0
        })
        source_campaigns = list(dict.fromkeys(
            clean_text(item.get("source_campaign") or item.get("campaign"))
            for item in matching_actions
            if clean_text(item.get("source_campaign") or item.get("campaign"))
        ))
        source_ad_groups = list(dict.fromkeys(
            clean_text(item.get("source_ad_group"))
            for item in matching_actions
            if clean_text(item.get("source_ad_group"))
        ))
        row["target_bid"] = round(consolidated_bid, 2)
        row["consolidated_target_bid"] = round(consolidated_bid, 2)
        row["consolidated_bid_method"] = method
        row["source_scope_count"] = len(source_campaigns) or len(matching_actions)
        row["source_campaigns"] = source_campaigns or list(row.get("campaigns") or [])
        row["source_ad_groups"] = source_ad_groups or list(row.get("ad_groups") or [])
        row["source_bid_range"] = source_bids
        row["source_target_bid_range"] = source_targets
        row["source_bid_merge_evidence"] = [
            {
                "campaign": clean_text(item.get("source_campaign") or item.get("campaign")),
                "ad_group": clean_text(item.get("source_ad_group")),
                "source_targeting": clean_text(item.get("source_targeting") or term),
                "source_match_type": item.get("source_match_type"),
                "clicks": item.get("clicks"),
                "source_bid": item.get("source_bid"),
                "target_bid": item.get("target_bid"),
                "merge_bid": round(metric_float(item.get("merge_bid")), 2),
                "merge_bid_source": item.get("merge_bid_source"),
            }
            for item in matching_actions
        ]

    def apply_source_placement_evidence(row: dict[str, Any], family: str) -> None:
        if not placement_enabled:
            return

        def apply_all_placement_preload(reason: str, confidence: str = "low") -> None:
            row["placement_plan"] = {
                "mode": "all_placement_preload",
                "adjustments": {
                    "rest_of_search": PLACEMENT_DEFAULT_UPLIFT_PCT,
                    "top_of_search": PLACEMENT_DEFAULT_UPLIFT_PCT,
                    "product_pages": PLACEMENT_DEFAULT_UPLIFT_PCT,
                },
                "campaign_bucket": "All-Placement-Preload",
                "dynamic_bidding": "down_only",
                "reason": reason,
                "confidence": confidence,
                "placement_control_required": True,
                "placement_preload_control": True,
                "base_bid_backsolve_required": True,
                "base_bid_divisor": PLACEMENT_FALLBACK_DIVISOR,
                "followup_rule": f"盈利位置保留{PLACEMENT_DEFAULT_UPLIFT_PCT}%；较差位置改为0%；0%不代表排除展示",
            }
            row["placement_evidence_confidence"] = confidence

        if not placement_analysis or not placement_analysis.get("enabled"):
            apply_all_placement_preload(
                f"无可靠广告位分期数据；全部位置预置{PLACEMENT_DEFAULT_UPLIFT_PCT}%，"
                f"基础竞价除以{PLACEMENT_FALLBACK_DIVISOR:g}，后续将较差位置改为0%"
            )
            return
        source_campaigns = list(dict.fromkeys(
            normalize_campaign_name(value)
            for value in (row.get("source_campaigns") or row.get("campaigns") or [])
            if normalize_campaign_name(value)
        ))
        if row.get("source_campaign") and normalize_campaign_name(row.get("source_campaign")) not in source_campaigns:
            source_campaigns.append(normalize_campaign_name(row.get("source_campaign")))
        matched = [placement_summaries[key] for key in source_campaigns if key in placement_summaries]
        row["placement_source_campaigns"] = source_campaigns
        row["source_placement_evidence"] = [
            {
                "campaign": item.get("campaign"),
                "campaign_key": item.get("campaign_key"),
                "action": item.get("action"),
                "suggested_adjustments": item.get("suggested_adjustments") or {},
                "target_placements": item.get("target_placements") or [],
                "placement_evidence": item.get("placement_evidence") or [],
                "evidence_rule": item.get("evidence_rule"),
            }
            for item in matched
        ]
        evidence_strengths = [
            clean_text(evidence.get("evidence_strength"))
            for item in matched
            for evidence in (item.get("placement_evidence") or [])
            if clean_text(evidence.get("evidence_strength"))
        ]
        complete_match = bool(source_campaigns) and len(matched) == len(source_campaigns)
        if complete_match and evidence_strengths and all(value == "strong" for value in evidence_strengths):
            evidence_confidence = "high"
        elif matched:
            evidence_confidence = "medium"
        else:
            evidence_confidence = "low"
        row["placement_evidence_confidence"] = evidence_confidence

        supported_sets = []
        for item in matched:
            if item.get("action") not in {"route_good_search_placement", "keep_good_search_placement"}:
                supported_sets.append(set())
                continue
            supported_sets.append({
                placement for placement in item.get("target_placements", []) or []
                if placement in {"rest_of_search", "top_of_search"}
            })
        common_search_placements = set.intersection(*supported_sets) if supported_sets else set()
        product_page_only = bool(matched) and any(
            item.get("action") == "product_page_requires_separate_targeting"
            for item in matched
        )

        if not matched:
            apply_all_placement_preload(
                f"没有匹配的来源活动广告位证据；全部位置预置{PLACEMENT_DEFAULT_UPLIFT_PCT}%，"
                "后续将较差位置改为0%"
            )
            return
        elif not common_search_placements:
            reason = (
                f"产品页面证据不转移到关键词活动；关键词活动全部位置预置{PLACEMENT_DEFAULT_UPLIFT_PCT}%，"
                "另建商品投放活动复核"
                if product_page_only
                else f"来源活动没有共同且可靠盈利的广告位；全部位置预置{PLACEMENT_DEFAULT_UPLIFT_PCT}%，"
                "后续将较差位置改为0%"
            )
            apply_all_placement_preload(reason, evidence_confidence)
            row["product_page_separate_targeting_required"] = product_page_only
            return
        else:
            adjustments = {
                "rest_of_search": PLACEMENT_DEFAULT_UPLIFT_PCT if "rest_of_search" in common_search_placements else 0,
                "top_of_search": PLACEMENT_DEFAULT_UPLIFT_PCT if "top_of_search" in common_search_placements else 0,
                "product_pages": 0,
            }
            if common_search_placements == {"rest_of_search"}:
                mode = "rest_only_control"
                bucket = {
                    "manual_phrase": "Phrase-Rest",
                    "manual_broad": "Broad-Rest",
                }.get(family, "Exact-Rest")
            elif common_search_placements == {"top_of_search"}:
                mode = "top_only_ranking"
                bucket = "Exact-Top-Ranking"
            else:
                mode = "rest_and_top"
                bucket = "Exact-Rest-Top"
            row["placement_plan"] = {
                "mode": mode,
                "adjustments": adjustments,
                "campaign_bucket": bucket,
                "dynamic_bidding": "down_only",
                "confidence": evidence_confidence,
                "reason": "仅继承所有来源活动共同且可靠盈利的搜索广告位",
                "base_bid_backsolve_required": False,
                "base_bid_divisor": PLACEMENT_FALLBACK_DIVISOR,
            }
            return

    paused_exact_terms = {
        clean_text(item.get("search_term")).casefold()
        for item in campaign_bid_merge_rows
        if item.get("recommended_action") == "pause/review"
        and match_family(item.get("source_match_type")) == "exact"
        and clean_text(item.get("search_term"))
    }

    for key, family in sources:
        for row in candidate_actions.get(key, []) or []:
            row = dict(row)
            term = clean_text(row.get("search_term"))
            if not term or (family, term) in seen:
                continue
            if family == "manual_exact" and term.casefold() in paused_exact_terms:
                continue
            # Profit-repair mode only permits the separately emitted micro
            # Phrase overlay. Do not turn every converting Exact term into a
            # new Phrase campaign before the growth gate is open.
            if family == "manual_phrase" and mature_growth_context.get("recommendation_mode") not in {
                "ready_for_controlled_discovery",
            } and not row.get("zero_order_potential"):
                continue
            # Broad is not launched while the growth gate says profit repair only.
            if family == "manual_broad" and (mature_growth_context.get("recommendation_mode") != "ready_for_controlled_discovery"):
                continue
            if family == "manual_exact":
                source_campaign_key = normalize_campaign_name(row.get("source_campaign"))
                action_row = next(
                    (
                        item for item in campaign_action_rows
                        if clean_text(item.get("search_term")) == term
                        and normalize_campaign_name(item.get("source_campaign") or item.get("campaign")) == source_campaign_key
                    ),
                    None,
                )
                if action_row is None:
                    action_row = action_by_id.get(clean_text(row.get("section3_action_reference")))
                if action_row:
                    for field in [
                        "recommended_action", "suggested_bid_change", "selected_bid_change_pct",
                        "actual_bid_change_pct", "required_bid_change_pct", "target_bid",
                        "old_campaign_target_cpc", "bid_plan_method", "confidence",
                        "max_cpc", "baseline_max_cpc",
                    ]:
                        if action_row.get(field) is not None:
                            row[field] = action_row.get(field)
                consolidate_exact_source_bids(row, term)
            apply_source_placement_evidence(row, family)
            rows.append(
                _campaign_plan_row(
                    row, family, "rebuild", stamp, placement_enabled,
                    profit_context, cvr_baseline, currency_symbol,
                )
            )
            seen.add((family, term))
    if mature_growth_context.get("recommendation_mode") == "stabilize_with_micro_phrase":
        for row in mature_growth_context.get("micro_phrase_candidates", []) or []:
            row = dict(row)
            term = clean_text(row.get("search_term"))
            if not term or not row.get("direct_test_eligible") or ("manual_phrase", term) in seen:
                continue
            row["controlled_test_overlay"] = True
            row["compact_visibility_required"] = True
            apply_source_placement_evidence(row, "manual_phrase")
            rows.append(
                _campaign_plan_row(
                    row,
                    "manual_phrase",
                    "rebuild",
                    stamp,
                    placement_enabled,
                    profit_context,
                    cvr_baseline,
                    currency_symbol,
                )
            )
            seen.add(("manual_phrase", term))
    for row in controlled_exploration.get("phrase_candidates", []) or []:
        row = dict(row)
        term = clean_text(row.get("search_term"))
        if term and ("manual_phrase", term) not in seen:
            row["compact_visibility_required"] = True
            apply_source_placement_evidence(row, "manual_phrase")
            rows.append(
                _campaign_plan_row(
                    row, "manual_phrase", "rebuild", stamp, placement_enabled,
                    profit_context, cvr_baseline, currency_symbol,
                )
            )
            seen.add(("manual_phrase", term))
    auto_close = controlled_exploration.get("auto_close_test")
    if auto_close:
        auto_close = dict(auto_close)
        apply_source_placement_evidence(auto_close, "auto_learning")
        rows.append(
            _campaign_plan_row(
                auto_close, "auto_learning", "rebuild", stamp, placement_enabled,
                profit_context, cvr_baseline, currency_symbol,
            )
        )

    # Group rows that can share one new campaign. High-volume Exact terms get
    # their own campaign so they cannot consume the shared budget of smaller
    # terms. The remaining Exact terms are pooled by economics and capped group
    # size; exploration remains separate by match family and placement mode.
    family_labels = {
        "manual_exact": "精准",
        "manual_phrase": "词组",
        "manual_broad": "广泛",
        "auto_learning": "自动紧密",
    }
    group_keys: dict[tuple[Any, ...], str] = {}
    group_row_counts: defaultdict[tuple[Any, ...], int] = defaultdict(int)
    bucket_labels = {
        "Evidence-Base-Bid-Only": "广告位证据降基础",
        "All-Zero-Profit-Repair": "全部0%利润修复",
        "Phrase-Rest": "搜索结果其余位置",
        "Broad-Rest": "搜索结果其余位置",
        "Exact-Rest": "搜索结果其余位置",
        "Exact-Rest-Top": "搜索结果其余位置和顶部",
        "Product-Page": "产品页面",
        "Exact-Top-Ranking": "搜索结果顶部",
        "Keep-Current-Structure": "全部0%学习测试",
        "All-Placement-Preload": "全部位置预置控制",
    }
    for row in rows:
        family = str(row.get("family") or "")
        plan = row.get("target_placement") or {}
        adjustments = plan.get("adjustments") or {}
        placement_signature = tuple(round(metric_float(adjustments.get(key)), 2) for key in ("rest_of_search", "top_of_search", "product_pages"))
        clicks = metric_float(
            row.get("recent_clicks")
            if row.get("recent_clicks") is not None
            else row.get("supporting_clicks", row.get("actual_clicks"))
        )
        orders = metric_float(
            row.get("recent_orders")
            if row.get("recent_orders") is not None
            else row.get("supporting_orders")
        )
        spend = metric_float(
            row.get("recent_spend")
            if row.get("recent_spend") is not None
            else row.get("supporting_spend")
        )
        acos_source = (
            row.get("recent_acos")
            if row.get("recent_acos") is not None
            else row.get("supporting_acos")
        )
        acos = metric_float(acos_source) if acos_source is not None else None
        profit_acos_cap = metric_float((profit_context or {}).get("profit_acos_cap")) or None
        gross_profit = metric_float((profit_context or {}).get("estimated_gross_profit_per_order"))

        row_profit_cap = metric_float(row.get("profit_acos_cap")) or metric_float(
            (profit_context or {}).get("profit_acos_cap")
        )
        row_strict_good = bool(
            row.get("action_eligibility") == "scale_ready"
            and orders >= 3
            and acos is not None
            and row_profit_cap > 0
            and acos <= row_profit_cap
            and not row.get("abnormal_attribution_sample")
        )

        if row.get("zero_order_potential"):
            purpose = "零单潜力词组" if family == "manual_phrase" else "零单潜力精准"
            max_group_keywords = 3
        elif family in {"manual_phrase", "manual_broad", "auto_learning"}:
            purpose = "探索"
            max_group_keywords = 3
        elif row_strict_good:
            purpose = "盈利优质"
            max_group_keywords = 1 if orders >= 10 or clicks >= 40 else 8
        elif orders >= 10 or clicks >= 40:
            purpose = "核心单词"
            max_group_keywords = 1
        elif orders >= 2 and (acos is None or profit_acos_cap is None or acos <= profit_acos_cap):
            purpose = "盈利中尾"
            max_group_keywords = 8
        elif orders >= 2:
            purpose = "高ACoS修复"
            max_group_keywords = 5
        elif (
            gross_profit > 0 and spend >= gross_profit * 2
        ) or (
            acos is not None and profit_acos_cap is not None and acos > profit_acos_cap * 1.5
        ):
            purpose = "高风险泛词"
            max_group_keywords = 5
        else:
            purpose = "低流量长尾"
            max_group_keywords = 8

        base_key = (family, str(plan.get("mode") or "no_placement_test"), placement_signature, purpose)
        slot = group_row_counts[base_key] // max_group_keywords
        group_row_counts[base_key] += 1
        key = (*base_key, slot)
        if key not in group_keys:
            label = f"{family_labels.get(family, family)}-{purpose}"
            group_keys[key] = f"A{len(group_keys) + 1} {label}"
        group_id = group_keys[key]
        row["campaign_group_id"] = group_id
        row["campaign_group_label"] = group_id.split(" ", 1)[-1]
        row["campaign_purpose"] = purpose
        row["campaign_group_max_keywords"] = max_group_keywords
        bucket = str(row.get("target_placement", {}).get("campaign_bucket") or "测试")
        bucket_label = bucket_labels.get(bucket, "受控测试") if placement_enabled else "受控测试"
        row["new_campaign_name"] = f"新建-{group_id}-{bucket_label}-{stamp}"
        row["ad_group"] = f"{row['campaign_group_label']}广告组"

    # Reorder completed campaign groups by the same weighted policy used by
    # the compact renderer: strict winners first, then orders 55%, ACoS 35%,
    # and evidence quality 10%; zero-order discovery remains last.
    group_quality: dict[str, tuple[Any, ...]] = {}
    for row in rows:
        group_id = str(row.get("campaign_group_id"))
        orders = metric_float(
            row.get("recent_orders")
            if row.get("recent_orders") is not None
            else row.get("supporting_orders")
        )
        acos = (
            row.get("recent_acos")
            if row.get("recent_acos") is not None
            else row.get("supporting_acos")
        )
        profit_cap = metric_float(row.get("profit_acos_cap")) or metric_float(
            (profit_context or {}).get("profit_acos_cap")
        )
        strict_good = bool(
            row.get("action_eligibility") == "scale_ready"
            and orders >= 3
            and acos is not None
            and profit_cap > 0
            and metric_float(acos) <= profit_cap
            and not row.get("abnormal_attribution_sample")
        )
        order_score = 55 * (math.log1p(orders) / math.log(11)) if orders > 0 else 0.0
        if acos is not None and metric_float(acos) > 0 and profit_cap > 0:
            acos_score = 35 * min(1.25, profit_cap / metric_float(acos))
        else:
            acos_score = 0.0
        quality_score = 0.0
        if row.get("local_conversion_confidence") == "usable":
            quality_score += 4
        if not row.get("abnormal_attribution_sample"):
            quality_score += 2
        if not (row.get("scale_blockers") or []):
            quality_score += 2
        if row.get("evidence_confidence") in {"high", "medium"}:
            quality_score += 2
        weighted_score = order_score + acos_score + min(10.0, quality_score)
        if row.get("zero_order_potential"):
            tier = 2
            weighted_score = metric_float(row.get("discovery_score"))
        else:
            tier = 0 if strict_good else 1
        candidate = (
            tier,
            -weighted_score,
            -orders,
            metric_float(acos) if acos is not None else 999,
            clean_text(row.get("keyword")),
        )
        group_quality[group_id] = min(group_quality.get(group_id, candidate), candidate)

    ordered_group_ids = sorted(group_quality, key=lambda group_id: group_quality[group_id])
    group_id_map = {
        old_id: f"A{index} {old_id.split(' ', 1)[-1]}"
        for index, old_id in enumerate(ordered_group_ids, start=1)
    }
    for row in rows:
        old_id = str(row.get("campaign_group_id"))
        new_id = group_id_map[old_id]
        row["campaign_group_id"] = new_id
        row["campaign_group_label"] = new_id.split(" ", 1)[-1]
        bucket = str(row.get("target_placement", {}).get("campaign_bucket") or "测试")
        bucket_label = bucket_labels.get(bucket, "受控测试") if placement_enabled else "受控测试"
        row["new_campaign_name"] = f"新建-{new_id}-{bucket_label}-{stamp}"
        row["ad_group"] = f"{row['campaign_group_label']}广告组"
    new_group_order = {
        group_id_map[old_id]: index
        for index, old_id in enumerate(ordered_group_ids)
    }
    rows.sort(key=lambda row: (
        new_group_order[str(row.get("campaign_group_id"))],
        clean_text(row.get("keyword")),
    ))

    rows_per_group: defaultdict[str, int] = defaultdict(int)
    exact_group_by_term: dict[str, str] = {}
    for row in rows:
        rows_per_group[str(row.get("campaign_group_id"))] += 1
        if row.get("family") == "manual_exact":
            exact_group_by_term[clean_text(row.get("keyword"))] = str(row.get("campaign_group_id"))

    for row in rows:
        group_id = str(row.get("campaign_group_id"))
        group_count = rows_per_group[group_id]
        row["campaign_group_keyword_count"] = group_count
        row["same_group_note"] = f"{group_count}词同投" if group_count > 1 else "1词"
        if row.get("pre_negative_terms"):
            exact_group = exact_group_by_term.get(clean_text(row.get("keyword")))
            validation = row.get("exact_validation") or {}
            actual_days = metric_float(validation.get("active_days"))
            actual_orders = metric_float(validation.get("orders"))
            profit_economics_met = validation.get("profit_economics_met") is True
            exact_coverage_confirmed = validation.get("exact_coverage_confirmed") is True
            ready = bool(
                exact_group
                and actual_days >= 7
                and actual_orders >= 2
                and profit_economics_met
                and exact_coverage_confirmed
            )
            negative_terms = "、".join(clean_text(term) for term in row["pre_negative_terms"] if clean_text(term))
            status = "ready" if ready else "conditional_after_exact_validation"
            condition = {
                "status": status,
                "keyword": negative_terms,
                "exact_group": exact_group,
                "minimum_days": 7,
                "minimum_orders": 2,
                "requires_profit_economics": True,
                "requires_exact_coverage": True,
                "scope": group_id,
                "actual_days": maybe_int(actual_days),
                "actual_orders": maybe_int(actual_orders),
                "profit_economics_met": profit_economics_met,
                "exact_coverage_confirmed": exact_coverage_confirmed,
                "ready": ready,
            }
            row["pre_negative_group"] = None
            row["pre_negative_status"] = status
            row["pre_negative_condition"] = condition
            row["pre_negative_trigger"] = condition
            if ready:
                row["pre_negative_note"] = f"在{group_id}中否定精准 {negative_terms}"
            else:
                row["pre_negative_note"] = (
                    f"待{exact_group or '对应精准活动'}运行≥7天、≥2单且利润达标并确认完整精准承接后，"
                    f"在{group_id}中否定精准 {negative_terms}；当前不执行"
                )
        else:
            row["pre_negative_status"] = "none"
            row["pre_negative_note"] = "无｜并行验证"

    # A campaign budget is shared by every keyword in its completed group.
    # First calculate each new group independently. Then preserve a single
    # Exact launch-budget pool based on the former core/long-tail structure.
    # A displayed campaign must still have enough budget to learn: at least
    # five effective clicks/day, or roughly one effective click per keyword,
    # whichever is higher. When every group's functional floor cannot fit in
    # the shared pool, schedule groups in priority waves instead of diluting
    # them into unusable $3/day fragments.
    grouped_rows: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped_rows[str(row.get("campaign_group_id"))].append(row)
    cvr_baseline_value = (cvr_baseline or {}).get("value")
    group_budget_plans: dict[str, dict[str, Any]] = {}
    for group_id, campaign_rows in grouped_rows.items():
        budget_plan = calculate_campaign_daily_budget(
            campaign_rows=campaign_rows,
            recent_cvr=account_recent_cvr,
            baseline_cvr=cvr_baseline_value,
            placement_enabled=placement_enabled,
        )
        group_budget_plans[group_id] = budget_plan

    exact_rows = [row for row in rows if row.get("family") == "manual_exact"]
    exact_group_ids = [
        group_id for group_id, campaign_rows in grouped_rows.items()
        if campaign_rows and campaign_rows[0].get("family") == "manual_exact"
    ]
    if exact_rows and exact_group_ids:
        coarse_exact_groups: defaultdict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
        for row in exact_rows:
            placement = row.get("target_placement") or {}
            adjustments = placement.get("adjustments") or {}
            placement_signature = tuple(
                round(metric_float(adjustments.get(key)), 2)
                for key in ("rest_of_search", "top_of_search", "product_pages")
            )
            clicks = metric_float(
                row.get("recent_clicks")
                if row.get("recent_clicks") is not None
                else row.get("supporting_clicks")
            )
            orders = metric_float(
                row.get("recent_orders")
                if row.get("recent_orders") is not None
                else row.get("supporting_orders")
            )
            former_volume_class = "长尾" if clicks <= 10 and orders <= 1 else "核心"
            coarse_exact_groups[(str(placement.get("mode") or "no_placement_test"), placement_signature, former_volume_class)].append(row)

        exact_budget_pool = int(round(sum(
            metric_float(calculate_campaign_daily_budget(
                campaign_rows=campaign_rows,
                recent_cvr=account_recent_cvr,
                baseline_cvr=cvr_baseline_value,
                placement_enabled=placement_enabled,
            ).get("suggested_daily_budget"))
            for campaign_rows in coarse_exact_groups.values()
        )))
        functional_floors: dict[str, int] = {}
        for group_id in exact_group_ids:
            campaign_rows = grouped_rows[group_id]
            effective_bids = sorted(
                metric_float(
                    row.get("effective_bid_ceiling")
                    if placement_enabled and row.get("effective_bid_ceiling")
                    else row.get("suggested_base_bid")
                )
                for row in campaign_rows
                if metric_float(
                    row.get("effective_bid_ceiling")
                    if placement_enabled and row.get("effective_bid_ceiling")
                    else row.get("suggested_base_bid")
                ) > 0
            )
            if effective_bids:
                midpoint = len(effective_bids) // 2
                median_bid = (
                    effective_bids[midpoint]
                    if len(effective_bids) % 2
                    else (effective_bids[midpoint - 1] + effective_bids[midpoint]) / 2
                )
                functional_floors[group_id] = max(
                    1,
                    math.ceil(max(
                        5 * max(effective_bids),
                        len(campaign_rows) * median_bid,
                    )),
                )
            else:
                functional_floors[group_id] = 3
        exact_budget_pool = max(exact_budget_pool, max(functional_floors.values()))

        weights: dict[str, float] = {}
        for group_id in exact_group_ids:
            campaign_rows = grouped_rows[group_id]
            spend_weight = sum(metric_float(
                row.get("recent_spend")
                if row.get("recent_spend") is not None
                else row.get("supporting_spend")
            ) for row in campaign_rows)
            click_weight = sum(metric_float(
                row.get("recent_clicks")
                if row.get("recent_clicks") is not None
                else row.get("supporting_clicks")
            ) for row in campaign_rows)
            planning_cpc = metric_float(group_budget_plans[group_id].get("planning_cpc"))
            weights[group_id] = spend_weight or (click_weight * planning_cpc) or planning_cpc or 1.0

        weight_total = sum(weights.values()) or float(len(exact_group_ids))
        raw_allocations = {
            group_id: exact_budget_pool * weights[group_id] / weight_total
            for group_id in exact_group_ids
        }
        allocations = {
            group_id: max(
                functional_floors[group_id],
                int(round(raw_allocations[group_id])),
            )
            for group_id in exact_group_ids
        }

        launch_waves: dict[str, int] = {}
        wave = 1
        wave_budget = 0
        for group_id in exact_group_ids:
            group_budget = allocations[group_id]
            if wave_budget and wave_budget + group_budget > exact_budget_pool:
                wave += 1
                wave_budget = 0
            launch_waves[group_id] = wave
            wave_budget += group_budget

        for group_id in exact_group_ids:
            group_budget_plans[group_id]["suggested_daily_budget"] = allocations[group_id]
            group_budget_plans[group_id]["functional_daily_budget_floor"] = functional_floors[group_id]
            group_budget_plans[group_id]["launch_wave"] = launch_waves[group_id]
            group_budget_plans[group_id]["launch_wave_count"] = max(launch_waves.values())
            group_budget_plans[group_id]["budget_allocation_basis"] = (
                "精准池按历史花费权重分配且不低于活动组可学习下限；"
                "超出同批预算池时按优先级分批启动"
            )
            group_budget_plans[group_id]["exact_launch_budget_pool"] = exact_budget_pool

    for group_id, campaign_rows in grouped_rows.items():
        budget_plan = group_budget_plans[group_id]
        for index, row in enumerate(campaign_rows):
            row.update(budget_plan)
            row["show_campaign_budget"] = index == 0

    return {
        "status": "ok" if rows else "no_candidates",
        "mode": campaign_plan_mode,
        "placement_enabled": placement_enabled,
        "rows": rows,
        "rule": "旧活动仅作历史证据；高流量精准词单独建活动，其余精准词按盈利中尾、高ACoS修复、高风险和长尾分池并限制组内词数；精准活动预算不低于活动组可学习下限，超出共享启动预算池时按优先级分批启动；同组关键词保留各自竞价；基础竞价由目标有效竞价反算。",
    }


def negative_performance_evidence(
    term: dict[str, Any],
    product: dict[str, Any],
    period_category: str | None = None,
) -> dict[str, Any]:
    """Classify zero-order evidence without making the semantic negative decision."""
    clicks = metric_float(term.get("clicks"))
    spend = metric_float(term.get("spend"))
    orders = metric_float(term.get("orders"))
    bid = term.get("bid_economics") or {}
    baseline_cvr = bid.get("bid_cvr_used")
    baseline_confidence = bid.get("bid_cvr_confidence")
    baseline_value = (
        metric_float(baseline_cvr)
        if baseline_cvr is not None and baseline_confidence in {"medium", "high"}
        else None
    )
    expected_orders = clicks * baseline_value if baseline_value else None
    zero_probability = (1 - baseline_value) ** clicks if baseline_value and 0 < baseline_value < 1 else None
    gross_profit = product_gross_profit_per_order(product)
    spend_multiple = safe_ratio(spend, gross_profit) if gross_profit else None
    required_cvr = bid.get("required_cvr")
    required_cvr_ratio = (
        safe_ratio(metric_float(required_cvr), baseline_value)
        if required_cvr is not None and baseline_value
        else None
    )
    cpc = term.get("cpc")
    max_cpc = bid.get("max_cpc") or bid.get("baseline_max_cpc")
    cpc_to_max_ratio = (
        safe_ratio(metric_float(cpc), metric_float(max_cpc))
        if cpc is not None and max_cpc is not None and metric_float(max_cpc) > 0
        else None
    )
    daily = (term.get("daily_action_evidence") or {}).get("zero_order_signal") or {}
    consecutive_days = metric_float(daily.get("consecutive_days"))
    longer_zero_consistent = bool(daily.get("longer_window_consistent"))
    ctr_context = term.get("ctr_context") or {}
    relative_ctr = ctr_context.get("relative_ctr")
    low_relative_ctr = bool(
        metric_float(term.get("impressions")) >= CTR_MIN_IMPRESSIONS_WEAK
        and relative_ctr is not None
        and metric_float(relative_ctr) <= 0.33
    )
    semantic = product_term_relevance(clean_text(term.get("search_term")), product)
    semantic_weakness = metric_float(semantic.get("score")) < 0.5
    raw_query_tokens = {
        _semantic_token(token)
        for token in re.findall(r"[a-z0-9]+", clean_text(term.get("search_term")).casefold())
        if _semantic_token(token)
    }
    semantic_modifier_tokens = sorted(raw_query_tokens & NEGATIVE_REVIEW_MODIFIERS)
    query_text = clean_text(term.get("search_term")).casefold()
    if (
        re.search(r"\b\d+(?:\.\d+)?\s*(?:inch|inches|mm|cm)\b", query_text)
        or re.search(r"\b\d+\.\d+\b", query_text)
    ):
        semantic_modifier_tokens = sorted({*semantic_modifier_tokens, "measurement"})

    evidence_families = {
        "conversion_probability": bool(
            (zero_probability is not None and zero_probability <= 0.35)
            or (expected_orders is not None and expected_orders >= 1)
        ),
        "profit_spend": bool(
            spend_multiple is not None
            and spend_multiple >= DIRECT_PROFIT_SPEND_MULTIPLE
        ),
        "persistence": bool(
            (consecutive_days >= 2 and longer_zero_consistent)
            or period_category == "consistently_click_only"
        ),
        "cpc_feasibility": bool(
            (required_cvr_ratio is not None and required_cvr_ratio >= 1.5)
            or (cpc_to_max_ratio is not None and cpc_to_max_ratio >= 1.3)
        ),
        "low_relative_ctr": low_relative_ctr,
        "semantic_weakness": semantic_weakness,
    }
    evidence_count = sum(1 for active in evidence_families.values() if active)
    source = negative_source_scope(term)
    primary_evidence = evidence_families["conversion_probability"] or evidence_families["profit_spend"]
    minimum_direct_gate = bool(
        clicks >= DISCOVERY_DIRECT_NEGATIVE_CLICKS
        or (
            spend_multiple is not None
            and spend_multiple >= DIRECT_PROFIT_SPEND_MULTIPLE
        )
    )
    strong_relevant_primary = bool(
        semantic_weakness
        or (
            expected_orders is not None
            and expected_orders >= RELEVANT_QUERY_EXPECTED_ORDERS
        )
        or (
            spend_multiple is not None
            and spend_multiple >= RELEVANT_QUERY_PROFIT_SPEND_MULTIPLE
        )
    )
    direct_performance = bool(
        orders == 0
        and source["negative_source_eligible"]
        and minimum_direct_gate
        and primary_evidence
        and evidence_count >= 2
        and strong_relevant_primary
    )
    secondary_evidence = bool(
        evidence_families["persistence"]
        or evidence_families["cpc_feasibility"]
        or evidence_families["low_relative_ctr"]
        or evidence_families["semantic_weakness"]
    )
    early_review = bool(
        orders == 0
        and clicks >= DISCOVERY_EARLY_REVIEW_CLICKS
        and (
            (
                semantic_weakness
                and (
                    low_relative_ctr
                    or evidence_families["cpc_feasibility"]
                    or (spend_multiple is not None and spend_multiple >= 0.5)
                )
            )
            or (
                secondary_evidence
                and (
                    (expected_orders is not None and expected_orders >= 0.75)
                    or (spend_multiple is not None and spend_multiple >= 0.5)
                )
            )
        )
    )
    review_performance = bool(
        orders == 0
        and source["negative_source_eligible"]
        and (
            early_review
            or (clicks >= DISCOVERY_DIRECT_NEGATIVE_CLICKS and evidence_count >= 1)
            or (
                spend_multiple is not None
                and spend_multiple >= DIRECT_PROFIT_SPEND_MULTIPLE
            )
        )
    )
    if direct_performance:
        tier = "direct_negative_exact_after_semantic_safety_check"
    elif review_performance:
        tier = "negative_candidate_review"
    else:
        tier = "semantic_mismatch_only_or_diagnostic"
    return {
        **source,
        "negative_decision_tier": tier,
        "performance_direct_negative_exact": direct_performance,
        "performance_negative_review": review_performance,
        "evidence_families": evidence_families,
        "evidence_family_count": evidence_count,
        "early_negative_review": early_review,
        "expected_orders": expected_orders,
        "zero_order_probability": zero_probability,
        "estimated_gross_profit_per_order": gross_profit,
        "spend_multiple": spend_multiple,
        "required_cvr_ratio_to_baseline": required_cvr_ratio,
        "cpc_to_max_cpc_ratio": cpc_to_max_ratio,
        "consecutive_zero_order_days": maybe_int(consecutive_days),
        "longer_zero_order_consistent": longer_zero_consistent,
        "semantic_relevance_score": semantic.get("score"),
        "semantic_matched_tokens": semantic.get("matched_tokens"),
        "semantic_unmatched_tokens": semantic.get("unmatched_tokens"),
        "semantic_modifier_tokens": semantic_modifier_tokens,
        "relative_ctr": relative_ctr,
        "ctr_is_diagnostic_only": True,
        "decision_rule": (
            "Phrase/Broad search terms enter review from three clicks when weak semantics combine with "
            "CTR/CPC/spend evidence. Direct source-scope Negative Exact requires at least six clicks or "
            "one gross-profit unit of spend, two evidence families, and conversion-probability or profit-spend "
            "evidence. CTR never authorizes a negative alone."
        ),
    }


NEGATIVE_ROOT_STOPWORDS = DISCOVERY_STOPWORDS | {
    "is", "are", "it", "my", "our", "your", "by", "from", "as",
    "easy", "small", "combo", "universal",
}
NEGATIVE_REVIEW_MODIFIERS = {
    "pack", "pcs", "piece", "pieces", "count",
    "gold", "black", "white", "red", "blue", "green", "gray", "grey",
    "repair", "replacement", "kit", "rv", "utility", "bathroom", "shower",
    "bathtub", "cactus", "gasket", "reversible",
}


def negative_root_phrases(search_term: Any, product: dict[str, Any]) -> list[str]:
    """Generate compact roots; single-token roots must be non-core discriminators."""
    normalized = clean_text(search_term).casefold()

    def mixed_fraction(match: re.Match[str]) -> str:
        whole, numerator, denominator = map(float, match.groups())
        return str(round(whole + numerator / denominator, 4)) if denominator else match.group(0)

    normalized = re.sub(r"(\d+)\s*-\s*(\d+)\s*/\s*(\d+)", mixed_fraction, normalized)
    tokens = re.findall(r"[a-z]+|\d+(?:\.\d+)?", normalized)
    if not tokens:
        return []
    product_values = [
        product.get("title"), product.get("core_category"), product.get("description"),
        *list(product.get("key_attributes") or []),
        *list(product.get("compatibility") or []),
    ]
    product_tokens = {
        token for value in product_values for token in semantic_tokens(value)
    }
    roots: set[str] = set()
    for size in range(1, min(4, len(tokens)) + 1):
        for start in range(0, len(tokens) - size + 1):
            chunk = tokens[start:start + size]
            if all(token in NEGATIVE_ROOT_STOPWORDS for token in chunk):
                continue
            numeric_present = any(re.fullmatch(r"\d+(?:\.\d+)?", token) for token in chunk)
            measurement_or_quantity = any(
                token in {"inch", "inches", "mm", "cm", "pack", "pcs", "pc", "piece", "pieces", "count"}
                for token in chunk
            )
            if numeric_present and size > 1 and not measurement_or_quantity:
                continue
            if size == 1:
                token = _semantic_token(chunk[0])
                if (
                    len(token) < 2
                    or token.isdigit()
                    or token in NEGATIVE_ROOT_STOPWORDS
                    or token in product_tokens
                ):
                    continue
            roots.add(" ".join(chunk))
    return sorted(roots, key=lambda value: (len(value.split()), value))


def _scope_source_family(row: dict[str, Any]) -> str:
    values = [
        row.get("source_match_type"),
        *(row.get("match_types") or []),
        *(row.get("targeting_expressions") or []),
    ]
    for value in values:
        family = source_match_family(value)
        if family != "unknown":
            return family
    return "unknown"


def _negative_scope_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        clean_text(row.get("campaign") or row.get("source_campaign")),
        clean_text(row.get("ad_group") or row.get("source_ad_group")),
        clean_text(row.get("source_targeting") or ((row.get("targeting_expressions") or [""])[0])),
        _scope_source_family(row),
    )


def build_scoped_negative_plan(
    scope_data: dict[str, Any],
    lookup: dict[str, dict[str, Any]],
    comparison_lookup: dict[str, dict[str, Any]],
    product: dict[str, Any],
    protected_names: set[str],
    root_protected_names: set[str],
) -> dict[str, list[dict[str, Any]]]:
    """Plan query and root negatives at the smallest actionable discovery scope."""
    if scope_data.get("status") != "ok":
        return {
            "direct_exact": [], "review_exact": [], "semantic_exact": [],
            "root_phrase": [], "blocked_roots": [],
        }

    prepared_rows: list[dict[str, Any]] = []
    scope_totals: defaultdict[tuple[str, str, str, str], dict[str, float]] = defaultdict(
        lambda: {"clicks": 0.0, "impressions": 0.0, "spend": 0.0, "orders": 0.0}
    )
    scope_all_rows: defaultdict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for pair in scope_data.get("all_pairs", []) or []:
        term_name = clean_text(pair.get("search_term"))
        if not term_name:
            continue
        aggregate = lookup.get(term_name) or {}
        row = dict(aggregate)
        # Preserve aggregate economics/CTR context, then replace the performance
        # metrics and source identity with this campaign/ad-group scope.
        aggregate_bid = aggregate.get("bid_economics") or {}
        aggregate_ctr = aggregate.get("ctr_context") or {}
        row.update(pair)
        row["bid_economics"] = aggregate_bid
        row["ctr_context"] = aggregate_ctr
        row["source_campaign"] = pair.get("campaign") or pair.get("source_campaign")
        row["source_ad_group"] = pair.get("ad_group") or pair.get("source_ad_group")
        row["source_targeting"] = pair.get("source_targeting") or ((pair.get("targeting_expressions") or [None])[0])
        row["source_match_type"] = pair.get("source_match_type") or ((pair.get("match_types") or [None])[0])
        key = _negative_scope_key(row)
        totals = scope_totals[key]
        for metric in ["clicks", "impressions", "spend", "orders"]:
            totals[metric] += metric_float(row.get(metric))
        scope_all_rows[key].append(row)
        prepared_rows.append(row)

    direct_exact: list[dict[str, Any]] = []
    review_exact: list[dict[str, Any]] = []
    semantic_exact: list[dict[str, Any]] = []
    discovery_zero_rows: list[dict[str, Any]] = []
    for row in prepared_rows:
        name = clean_text(row.get("search_term"))
        clicks = metric_float(row.get("clicks"))
        orders = metric_float(row.get("orders"))
        family = _scope_source_family(row)
        if orders > 0 or clicks <= 0 or family not in {"phrase", "broad"}:
            continue
        if is_auto_product_targeting_term(row):
            continue
        discovery_zero_rows.append(row)
        comparison_row = comparison_lookup.get(name) or {}
        evidence = negative_performance_evidence(
            row,
            product,
            comparison_row.get("category") or row.get("period_category"),
        )
        protected = name in protected_names
        root_protected = name.casefold() in root_protected_names
        candidate = compact_term_evidence(
            row,
            "scoped_phrase_broad_zero_order_review",
            {
                **evidence,
                "candidate_kind": "search_term",
                "negative_type": "negative_exact",
                "source_family": family,
                "campaign": row.get("source_campaign"),
                "ad_group": row.get("source_ad_group"),
                "scope": "source_campaign_ad_group_target",
                "recommended_negative_scope": "source_campaign_ad_group_only",
                "root_protected": root_protected,
                "protected": protected,
                "click_signal": click_signal_label(clicks),
                "required_human_or_model_check": (
                    "Confirm semantic safety, then add Negative Exact only to the listed Phrase/Broad source scope."
                ),
            },
        )
        if protected or root_protected:
            candidate["negative_decision_tier"] = "protected_review_only"
            candidate["performance_direct_negative_exact"] = False
            semantic_exact.append(candidate)
        elif evidence.get("performance_direct_negative_exact"):
            direct_exact.append(candidate)
        elif evidence.get("performance_negative_review"):
            review_exact.append(candidate)
        else:
            candidate["negative_decision_tier"] = "semantic_mismatch_only_low_sample"
            candidate["do_not_output_unless_semantic_mismatch"] = True
            semantic_exact.append(candidate)

    gross_profit = product_gross_profit_per_order(product)
    product_root_values = [
        clean_text(product.get("title")),
        clean_text(product.get("core_category")),
        clean_text(product.get("description")),
        *[clean_text(value) for value in (product.get("key_attributes") or [])],
        *[clean_text(value) for value in (product.get("compatibility") or [])],
    ]
    product_root_tokens = {
        token for value in product_root_values for token in semantic_tokens(value)
    }
    root_groups: defaultdict[tuple[tuple[str, str, str, str], str], list[dict[str, Any]]] = defaultdict(list)
    for row in discovery_zero_rows:
        key = _negative_scope_key(row)
        for root in negative_root_phrases(row.get("search_term"), product):
            root_groups[(key, root)].append(row)

    root_candidates: list[dict[str, Any]] = []
    blocked_roots: list[dict[str, Any]] = []
    for (scope_key, root), rows in root_groups.items():
        by_term: dict[str, dict[str, Any]] = {}
        for row in rows:
            by_term.setdefault(clean_text(row.get("search_term")), row)
        if len(by_term) < 2:
            continue
        supporting_rows = list(by_term.values())
        clicks = sum(metric_float(row.get("clicks")) for row in supporting_rows)
        impressions = sum(metric_float(row.get("impressions")) for row in supporting_rows)
        spend = sum(metric_float(row.get("spend")) for row in supporting_rows)
        orders = sum(metric_float(row.get("orders")) for row in supporting_rows)
        if orders > 0:
            continue
        active_dates = {
            str(value)
            for row in supporting_rows
            for value in (row.get("date_values_seen") or [])
            if value
        }
        root_ctr = safe_ratio(clicks, impressions)
        source_ctr = safe_ratio(
            scope_totals[scope_key]["clicks"],
            scope_totals[scope_key]["impressions"],
        )
        relative_ctr = safe_ratio(root_ctr, source_ctr) if root_ctr is not None and source_ctr else None
        spend_multiple = safe_ratio(spend, gross_profit) if gross_profit else None
        baseline_values = [
            metric_float((row.get("bid_economics") or {}).get("bid_cvr_used"))
            for row in supporting_rows
            if (row.get("bid_economics") or {}).get("bid_cvr_confidence") in {"medium", "high"}
            and metric_float((row.get("bid_economics") or {}).get("bid_cvr_used")) > 0
        ]
        baseline_cvr = sum(baseline_values) / len(baseline_values) if baseline_values else None
        expected_orders = clicks * baseline_cvr if baseline_cvr else None
        max_cpcs = [
            metric_float((row.get("bid_economics") or {}).get("max_cpc") or (row.get("bid_economics") or {}).get("baseline_max_cpc"))
            for row in supporting_rows
            if metric_float((row.get("bid_economics") or {}).get("max_cpc") or (row.get("bid_economics") or {}).get("baseline_max_cpc")) > 0
        ]
        max_cpc = min(max_cpcs) if max_cpcs else None
        root_cpc = safe_ratio(spend, clicks)
        cpc_ratio = safe_ratio(root_cpc, max_cpc) if root_cpc is not None and max_cpc else None
        protected_sources = sorted({
            protected
            for protected in protected_names
            if phrase_in_text(root, protected)
        })
        root_is_protected = bool(
            root.casefold() in root_protected_names
            or protected_sources
            or any(
                metric_float(row.get("orders")) > 0
                and phrase_in_text(root, clean_text(row.get("search_term")))
                for row in scope_all_rows[scope_key]
            )
        )
        semantic = product_term_relevance(root, product)
        token_count = len(root.split())
        distinct_terms = len(by_term)
        root_semantic_tokens = [
            _semantic_token(token)
            for token in re.findall(r"[a-z0-9]+", root.casefold())
            if token not in NEGATIVE_ROOT_STOPWORDS and _semantic_token(token)
        ]
        root_has_number = any(re.search(r"\d", token) for token in root.split())
        product_core_root = bool(
            not root_has_number
            and root_semantic_tokens
            and all(token in product_root_tokens for token in root_semantic_tokens)
        ) or any(phrase_in_text(root, value) for value in product_root_values if value)
        if product_core_root:
            root_is_protected = True
        if token_count == 1:
            volume_gate = (
                distinct_terms >= ROOT_NEGATIVE_SINGLE_TOKEN_MIN_TERMS
                and clicks >= ROOT_NEGATIVE_SINGLE_TOKEN_MIN_CLICKS
                and spend_multiple is not None
                and spend_multiple >= 2
            )
        else:
            volume_gate = distinct_terms >= ROOT_NEGATIVE_MIN_TERMS and clicks >= ROOT_NEGATIVE_MIN_CLICKS
        primary_gate = bool(
            (
                spend_multiple is not None
                and spend_multiple >= RELEVANT_QUERY_PROFIT_SPEND_MULTIPLE
            )
            or (
                expected_orders is not None
                and expected_orders >= RELEVANT_QUERY_EXPECTED_ORDERS
            )
        )
        secondary_gate = bool(
            (relative_ctr is not None and impressions >= CTR_MIN_IMPRESSIONS_WEAK and relative_ctr <= 0.5)
            or (cpc_ratio is not None and cpc_ratio >= 1.3)
            or (spend_multiple is not None and spend_multiple >= 2)
        )
        has_daily_dates = any(row.get("date_values_seen") for row in supporting_rows)
        persistence_gate = (
            len(active_dates) >= 2
            if has_daily_dates
            else sum(metric_float(row.get("rows_aggregated")) for row in supporting_rows) >= 2
        )
        performance_ready = bool(
            volume_gate and primary_gate and secondary_gate and persistence_gate and not root_is_protected
        )
        campaign, ad_group, source_targeting, family = scope_key
        candidate = {
            "search_term": root,
            "root": root,
            "candidate_kind": "root_cluster",
            "negative_type": "negative_phrase",
            "negative_decision_tier": (
                "root_negative_phrase_after_semantic_safety_check"
                if performance_ready
                else "root_negative_phrase_review"
            ),
            "performance_direct_negative_phrase": performance_ready,
            "campaign": campaign,
            "ad_group": ad_group,
            "source_campaign": campaign,
            "source_ad_group": ad_group,
            "source_targeting": source_targeting,
            "source_family": family,
            "source_match_type": family,
            "recommended_negative_scope": "source_campaign_ad_group_only",
            "scope": "source_campaign_ad_group_target",
            "supporting_terms": sorted(by_term),
            "supporting_term_count": distinct_terms,
            "clicks": maybe_int(clicks),
            "impressions": maybe_int(impressions),
            "spend": round(spend, 4),
            "orders": 0,
            "ctr": root_ctr,
            "relative_ctr": relative_ctr,
            "cpc": root_cpc,
            "max_cpc": max_cpc,
            "cpc_to_max_cpc_ratio": cpc_ratio,
            "expected_orders": expected_orders,
            "estimated_gross_profit_per_order": gross_profit,
            "spend_multiple": spend_multiple,
            "active_day_count": len(active_dates),
            "semantic_relevance_score": semantic.get("score"),
            "semantic_matched_tokens": semantic.get("matched_tokens"),
            "root_protected": root_is_protected,
            "protected_source_terms": protected_sources,
            "required_human_or_model_check": (
                "Confirm the whole smallest root is unsafe. Never approve when a converting, historical, Exact, or protected variant contains it."
            ),
            "reason": (
                "repeated_zero_order_root_performance_gate_met"
                if performance_ready
                else "repeated_low_sample_root_semantic_review"
            ),
        }
        if root_is_protected:
            blocked_roots.append(candidate)
        else:
            root_candidates.append(candidate)

    # When multiple roots cover exactly the same queries in one source scope,
    # retain the shortest safe discriminator rather than flooding the report.
    root_candidates.sort(key=lambda row: (
        not bool(row.get("performance_direct_negative_phrase")),
        -metric_float(row.get("spend")),
        -metric_float(row.get("supporting_term_count")),
        len(str(row.get("root") or "").split()),
        str(row.get("root") or ""),
    ))
    deduped_roots: list[dict[str, Any]] = []
    seen_support: set[tuple[Any, ...]] = set()
    for row in root_candidates:
        signature = (
            row.get("campaign"), row.get("ad_group"), row.get("source_targeting"),
            tuple(row.get("supporting_terms") or []),
        )
        if signature in seen_support:
            continue
        seen_support.add(signature)
        deduped_roots.append(row)

    sort_key = lambda row: (
        -metric_float(row.get("spend")),
        -metric_float(row.get("clicks")),
        str(row.get("campaign") or ""),
        str(row.get("search_term") or ""),
    )
    return {
        "direct_exact": sorted(direct_exact, key=sort_key),
        "review_exact": sorted(review_exact, key=sort_key),
        "semantic_exact": sorted(semantic_exact, key=lambda row: (
            not bool(row.get("semantic_modifier_tokens")),
            metric_float(row.get("semantic_relevance_score")) >= 0.5,
            -len(row.get("semantic_unmatched_tokens") or []),
            -metric_float(row.get("spend")),
            -metric_float(row.get("clicks")),
            str(row.get("search_term") or ""),
        )),
        "root_phrase": deduped_roots,
        "blocked_roots": blocked_roots,
    }


def build_campaign_action_candidates(
    scope_data: dict[str, Any],
    lookup: dict[str, dict[str, Any]],
    comparison_lookup: dict[str, dict[str, Any]],
    target_acos: float | None,
    history_independent: bool = True,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen_source_scopes: set[tuple[str, str, str, str, str]] = set()

    def action_from_metrics(
        search_term: str,
        orders: Any,
        spend: Any,
        clicks: Any,
        acos: Any,
        ctr_signal: str | None = None,
    ) -> tuple[str, str, str, str] | None:
        order_count = metric_float(orders)
        click_count = metric_float(clicks)
        acos_value = metric_float(acos) if acos is not None else None
        aggregate_term = lookup.get(search_term) or {}
        if is_auto_product_targeting_term(aggregate_term):
            return None
        market_volume = str(aggregate_term.get("market_volume_tier") or "unknown")
        bid_economics = aggregate_term.get("bid_economics") or {}
        cpc_mode = bid_economics.get("cpc_decision_mode")
        traffic_mode = bid_economics.get("traffic_action_mode")
        current_cpc = safe_ratio(metric_float(spend), click_count)
        baseline_max_cpc = metric_float(bid_economics.get("baseline_max_cpc")) or None
        selected_max_cpc = metric_float(bid_economics.get("max_cpc")) or None
        protection_max_cpc = baseline_max_cpc or selected_max_cpc
        daily_evidence = aggregate_term.get("daily_action_evidence") or {}
        scale_signal = daily_evidence.get("scale_signal") or {}
        zero_order_signal = daily_evidence.get("zero_order_signal") or {}

        if order_count >= 3 and scale_signal:
            aggregate_acos = aggregate_term.get("acos")
            profit_cap = scale_signal.get("profit_acos_cap")
            longer_consistent = bool(scale_signal.get("longer_window_consistent"))
            if (
                aggregate_acos is not None
                and profit_cap is not None
                and metric_float(aggregate_acos) > metric_float(profit_cap)
                and not longer_consistent
            ):
                return (
                    "hold/diagnose",
                    "保持或+5%",
                    "short_term_scale_signal_conflicts_with_longer_economics",
                    "medium",
                )
            return (
                str(scale_signal.get("recommended_action") or "increase_test"),
                str(scale_signal.get("suggested_bid_change") or "+5%"),
                str(scale_signal.get("reason") or "daily_profitable_multi_order_scale_signal"),
                str(scale_signal.get("confidence") or "medium"),
            )

        if order_count == 0 and zero_order_signal:
            return (
                str(zero_order_signal.get("recommended_action") or "diagnose/controlled_bid_down"),
                str(zero_order_signal.get("suggested_bid_change") or "保持或-5%"),
                str(zero_order_signal.get("reason") or "persistent_zero_order_probability_and_economics"),
                str(zero_order_signal.get("confidence") or "medium"),
            )

        # One attributed order is too unstable for a hard local-CVR bid cut. Protect the
        # keyword from negatives/large reductions, but align CPC gently to the baseline cap.
        if order_count == 1 and target_acos and acos_value is not None and acos_value > target_acos * 1.2:
            if protection_max_cpc and current_cpc is not None and current_cpc > protection_max_cpc:
                cpc_ratio = current_cpc / protection_max_cpc
                bid_change = "-10%" if cpc_ratio > 1.05 else "-5%到-10%"
                return (
                    "diagnose/controlled_bid_down",
                    bid_change,
                    "one_order_protect_keyword_cpc_above_baseline_max",
                    "medium",
                )
            return (
                "hold/diagnose",
                "保持或-5%",
                "one_order_protect_keyword_cpc_within_baseline_max",
                "medium",
            )

        # With multiple orders, materially bad economics should override soft diagnostic
        # traffic modes. History still protects the term from negatives, not from bid control.
        if order_count > 1 and target_acos and acos_value is not None and acos_value > target_acos * 1.2:
            if acos_value > target_acos * 2 or (
                protection_max_cpc and current_cpc is not None and current_cpc > protection_max_cpc * 1.05
            ):
                return (
                    "diagnose/controlled_bid_down",
                    "-10%到-20%",
                    "multi_order_extreme_economics_override_diagnostic_protection",
                    "high",
                )
            return (
                "diagnose/controlled_bid_down",
                "-5%到-10%",
                "multi_order_high_acos_requires_bid_control",
                "medium",
            )
        if traffic_mode == "increase_test":
            recovery = historical_recovery_assessment(
                comparison_lookup.get(search_term),
                target_acos,
                history_independent,
            )
            if recovery["action"] in {
                "diagnose_historical_economics_before_recovery",
                "no_historical_recovery_action",
                "validate_without_bid_increase",
            }:
                return ("hold/diagnose", recovery["bid_change"], recovery["reason"], recovery["confidence"])
            return ("increase_test", recovery["bid_change"], recovery["reason"], recovery["confidence"])
        if traffic_mode in {"hold_diagnose_visibility", "diagnose_ctr_or_creative"}:
            return ("hold/diagnose", "保持", traffic_mode, "medium")
        if traffic_mode == "soft_bid_control":
            return ("diagnose/soft_bid_control", "保持或-5%", "historical_recent_weakness", "medium")
        if order_count > 0:
            if cpc_mode == "launch_test":
                if target_acos and acos_value is not None and acos_value > target_acos * 1.2:
                    return ("diagnose/soft_bid_control", "-5%到-10%", "launch_high_acos", "medium")
                return None
            if cpc_mode == "diagnostic_protect":
                if target_acos and acos_value is not None and acos_value > target_acos * 1.2:
                    return ("diagnose/soft_bid_control", "-5%到-10%", "historical_diagnostic_high_acos", "medium")
                return ("hold/diagnose", "保持", "historical_diagnostic_protect", "medium")
            if target_acos and acos_value is not None:
                if acos_value <= target_acos:
                    return None
                if acos_value <= target_acos * 1.2:
                    return ("bid_down", "-5%", "slightly_above_target_acos", "medium")
                if market_volume == "big/core":
                    return ("steady/bid_down", "-5%到-10%", "big_core_high_acos_keep_diagnostic", "medium")
                return ("bid_down", "-10%", "above_target_acos", "high")
            return None
        if click_count > CLICK_ONLY_OUTPUT_CLICK_THRESHOLD:
            if market_volume == "big/core":
                return ("diagnose/bid_down", "-5%到-10%", "big_core_zero_order_diagnose_before_negative", "medium")
            if ctr_signal in {"ctr_weakness", "strong_ctr_weakness"}:
                return ("semantic/creative review", "-5%到-10%", "zero_order_plus_low_relative_ctr", "medium")
            return ("bid_down", "-10%", "strong_zero_order_campaign_signal", "medium")
        return None

    campaign_pairs = scope_data.get("all_pairs", []) if scope_data.get("status") == "ok" else []
    scored_pairs = []
    for item in campaign_pairs:
        orders = metric_float(item.get("orders"))
        spend = metric_float(item.get("spend"))
        clicks = metric_float(item.get("clicks"))
        acos_value = metric_float(item.get("acos")) if item.get("acos") is not None else None
        score = spend
        if orders > 0:
            if target_acos and acos_value is not None:
                score += 500 if acos_value <= target_acos * 0.8 else 400 if acos_value <= target_acos else 300 if acos_value <= target_acos * 1.2 else 200
            else:
                score += 250
        elif clicks >= 20:
            score += 280
        elif clicks > CLICK_ONLY_OUTPUT_CLICK_THRESHOLD:
            score += 260
        search_term = clean_text(item.get("search_term"))
        market_volume = (lookup.get(search_term) or {}).get("market_volume_tier")
        if market_volume == "big/core":
            score += 80
        elif market_volume == "small/niche" and orders == 0:
            score -= 80
        traffic_mode = ((lookup.get(search_term) or {}).get("bid_economics") or {}).get("traffic_action_mode")
        if traffic_mode == "increase_test":
            recovery = historical_recovery_assessment(
                comparison_lookup.get(search_term),
                target_acos,
                history_independent,
            )
            score += {"high": 700, "medium": 450, "low": 150}.get(recovery["priority"], 0)
        scored_pairs.append((score, item))

    for _score, item in sorted(
        scored_pairs,
        key=lambda pair: (-pair[0], str(pair[1].get("campaign") or ""), str(pair[1].get("search_term") or "")),
    ):
        campaign = clean_text(item.get("campaign"))
        ad_group = clean_text(item.get("ad_group") or item.get("source_ad_group"))
        source_targeting = clean_text(item.get("source_targeting"))
        source_match_family = clean_text(item.get("source_match_family")) or _scope_source_family(item)
        search_term = clean_text(item.get("search_term"))
        scope_key = (campaign, ad_group, source_targeting, source_match_family, search_term)
        if not campaign or not ad_group or not source_targeting or not search_term or scope_key in seen_source_scopes:
            continue
        aggregate_term = lookup.get(search_term) or {}
        ctr_context = aggregate_term.get("ctr_context") or {}
        action = action_from_metrics(
            search_term,
            item.get("orders"),
            item.get("spend"),
            item.get("clicks"),
            item.get("acos"),
            ctr_context.get("ctr_signal"),
        )
        if action is None:
            continue
        recommended_action, suggested_bid_change, reason, confidence = action
        scope_detail = f"广告组：{ad_group}"
        bid_economics = aggregate_term.get("bid_economics") or {}
        daily_action_evidence = aggregate_term.get("daily_action_evidence") or {}
        active_daily_signal = (
            daily_action_evidence.get("scale_signal")
            or daily_action_evidence.get("zero_order_signal")
            or {}
        )
        comparison_row = comparison_lookup.get(search_term)
        target_cpc_candidates = [
            metric_float(value)
            for value in [bid_economics.get("max_cpc"), bid_economics.get("baseline_max_cpc")]
            if metric_float(value) > 0
        ]
        old_campaign_target_cpc = min(target_cpc_candidates) if target_cpc_candidates else None
        actual_cpc = safe_ratio(metric_float(item.get("spend")), metric_float(item.get("clicks")))
        bid_plan = existing_campaign_bid_plan(
            source_bid=item.get("source_bid"),
            suggested_change=suggested_bid_change,
            confidence=confidence,
            target_cpc=old_campaign_target_cpc,
            actual_cpc=actual_cpc,
            clicks=item.get("clicks"),
            source_bid_stable=bool(item.get("source_bid_stable")),
        )
        pause_reason = existing_campaign_pause_reason(
            orders=item.get("orders"),
            clicks=item.get("clicks"),
            acos=item.get("acos"),
            profit_acos_cap=aggregate_term.get("profit_acos_cap"),
            required_bid_change_pct=bid_plan.get("required_bid_change_pct"),
            daily_signal=active_daily_signal.get("signal"),
            daily_spend_multiple=active_daily_signal.get("spend_multiple"),
            daily_longer_window_consistent=active_daily_signal.get("longer_window_consistent"),
        )
        original_suggested_bid_change = suggested_bid_change
        if pause_reason:
            recommended_action = "pause/review"
            suggested_bid_change = "暂停"
            reason = pause_reason
            confidence = "high"
            bid_plan["target_bid"] = None
            bid_plan["actual_bid_change_pct"] = None
        candidate = {
            "action_id": f"S3-{len(candidates) + 1:02d}",
            "campaign": campaign,
            "campaign_intent": infer_campaign_intent(campaign, item.get("match_types", []), item.get("campaign_types", [])),
            "ad_group_scope": scope_detail,
            "search_term": search_term,
            "aba_rank": aggregate_term.get("aba_rank"),
            "market_volume_tier": aggregate_term.get("market_volume_tier"),
            "ad_exposure_tier": aggregate_term.get("ad_exposure_tier"),
            "data_sufficiency": aggregate_term.get("data_sufficiency"),
            "promotion_priority": aggregate_term.get("promotion_priority"),
            "action_eligibility": aggregate_term.get("action_eligibility"),
            "local_conversion_confidence": aggregate_term.get("local_conversion_confidence"),
            "abnormal_attribution_sample": aggregate_term.get("abnormal_attribution_sample"),
            "scale_blockers": aggregate_term.get("scale_blockers", []),
            "orders": item.get("orders", 0),
            "spend": item.get("spend", 0),
            "clicks": item.get("clicks", 0),
            "impressions": item.get("impressions", 0),
            "ctr": item.get("ctr"),
            "ctr_signal": ctr_context.get("ctr_signal"),
            "relative_ctr": ctr_context.get("relative_ctr"),
            "cpc": actual_cpc,
            "acos": item.get("acos"),
            "cvr": item.get("cvr"),
            "recommended_action": recommended_action,
            "suggested_bid_change": suggested_bid_change,
            "suggested_bid_change_range": original_suggested_bid_change,
            "selected_bid_change_pct": bid_plan.get("selected_bid_change_pct"),
            "actual_bid_change_pct": bid_plan.get("actual_bid_change_pct"),
            "required_bid_change_pct": bid_plan.get("required_bid_change_pct"),
            "target_bid": bid_plan.get("target_bid"),
            "theoretical_base_bid": bid_plan.get("theoretical_base_bid"),
            "old_campaign_target_cpc": bid_plan.get("old_campaign_target_cpc"),
            "bid_plan_method": bid_plan.get("bid_plan_method"),
            "cpc_to_base_ratio": bid_plan.get("cpc_to_base_ratio"),
            "pause_scope_only": bool(pause_reason),
            "scope": item.get("scope") or "source_scope",
            "match_types": item.get("match_types", []),
            "campaign_types": item.get("campaign_types", []),
            "targeting_expressions": item.get("targeting_expressions", []),
            "source_bid": item.get("source_bid"),
            "source_bid_date": item.get("source_bid_date"),
            "source_bid_count": item.get("source_bid_count"),
            "source_bid_unique_count": item.get("source_bid_unique_count"),
            "source_bid_stable": item.get("source_bid_stable"),
            "source_campaign": item.get("source_campaign"),
            "source_ad_group": item.get("source_ad_group"),
            "source_targeting": item.get("source_targeting"),
            "source_match_type": item.get("source_match_type"),
            "source_match_family": source_match_family,
            "confidence": confidence,
            "reason": reason,
            "period_category": aggregate_term.get("period_category"),
            "max_cpc": bid_economics.get("max_cpc"),
            "baseline_max_cpc": bid_economics.get("baseline_max_cpc"),
            "bid_cvr_source": bid_economics.get("bid_cvr_source"),
            "bid_cvr_confidence": bid_economics.get("bid_cvr_confidence"),
            "cpc_decision_mode": bid_economics.get("cpc_decision_mode"),
            "cpc_decision_note": bid_economics.get("cpc_decision_note"),
            "traffic_action_mode": bid_economics.get("traffic_action_mode"),
            "traffic_action_note": bid_economics.get("traffic_action_note"),
            "historical_evidence_independent": history_independent,
            "daily_action_evidence": aggregate_term.get("daily_action_evidence"),
            "daily_signal": active_daily_signal.get("signal"),
            "daily_signal_days": active_daily_signal.get("days"),
            "daily_consecutive_days": active_daily_signal.get("consecutive_days"),
            "daily_expected_orders": active_daily_signal.get("expected_orders"),
            "daily_zero_order_probability": active_daily_signal.get("zero_order_probability"),
            "daily_spend_multiple": active_daily_signal.get("spend_multiple"),
            "daily_longer_window_consistent": active_daily_signal.get("longer_window_consistent"),
            **period_evidence(comparison_row),
        }
        if comparison_row and metric_float((comparison_row.get("baseline") or {}).get("orders")) > 0:
            recovery = historical_recovery_assessment(comparison_row, target_acos, history_independent)
            candidate["historical_strength"] = recovery["strength"]
            candidate["historical_recovery_priority"] = recovery["priority"]
        candidates.append(candidate)
        seen_source_scopes.add(scope_key)
    return candidates


def prioritize_campaign_action_candidates(
    candidates: list[dict[str, Any]],
    target_acos: float | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep Section 3 focused on immediate actions and move lower-signal items to a watchlist."""
    priority_rank = {"high": 0, "medium": 1, "watch": 2}
    for row in candidates:
        orders = metric_float(row.get("orders"))
        clicks = metric_float(row.get("clicks"))
        acos = metric_float(row.get("acos")) if row.get("acos") is not None else None
        action = str(row.get("recommended_action") or "")
        if target_acos and orders > 0 and acos is not None and acos > target_acos * 1.2:
            priority = "high"
        elif row.get("daily_signal") == "persistent_zero_order" and row.get("confidence") == "high":
            priority = "high"
        elif orders == 0 and clicks > CLICK_ONLY_OUTPUT_CLICK_THRESHOLD:
            priority = "high"
        elif action == "increase_test" and row.get("confidence") in {"medium", "high"}:
            priority = "medium"
        elif "bid_down" in action:
            priority = "medium"
        else:
            priority = "watch"
        row["action_priority"] = priority

    ordered = sorted(
        candidates,
        key=lambda row: (
            priority_rank.get(str(row.get("action_priority")), 9),
            -metric_float(row.get("spend")),
            -metric_float(row.get("clicks")),
            str(row.get("search_term") or ""),
        ),
    )
    actionable = [row for row in ordered if row.get("action_priority") != "watch"]
    recovery_rows = [row for row in actionable if row.get("recommended_action") == "increase_test"]
    other_rows = [row for row in actionable if row.get("recommended_action") != "increase_test"]
    immediate = [*recovery_rows[:1], *other_rows[:4]]
    if len(immediate) < 5:
        selected_ids = {id(row) for row in immediate}
        immediate.extend(row for row in actionable if id(row) not in selected_ids)
        immediate = immediate[:5]
    immediate_ids = {id(row) for row in immediate}
    watchlist = [row for row in ordered if id(row) not in immediate_ids][:10]
    for index, row in enumerate(immediate, start=1):
        row["action_id"] = f"S3-{index:02d}"
        row.pop("watchlist_id", None)
    for index, row in enumerate(watchlist, start=1):
        row["watchlist_id"] = f"W-{index:02d}"
        row.pop("action_id", None)
    return immediate, watchlist


def build_candidate_actions(
    ad_data: dict[str, Any],
    period_comparison: dict[str, Any],
    campaign_data: dict[str, Any],
    scope_data: dict[str, Any],
    ad_group_conflicts: list[dict[str, Any]],
    product: dict[str, Any],
) -> dict[str, Any]:
    if ad_data.get("status") != "ok":
        return {
            "status": "not_available",
            "reason": "Ad data was not parsed successfully; fix required columns before building candidate actions.",
        }

    all_terms = ad_data.get("all_terms", [])
    lookup = term_lookup(ad_data)
    comparison_lookup = period_comparison_lookup(period_comparison)
    target_acos = product_profit_acos_cap(product)
    converting = sorted(
        [term for term in all_terms if metric_float(term.get("orders")) > 0],
        key=lambda term: (-metric_float(term.get("orders")), -metric_float(term.get("spend")), str(term.get("search_term"))),
    )
    click_only = sorted(
        [term for term in all_terms if metric_float(term.get("orders")) == 0 and metric_float(term.get("clicks")) > 0],
        key=lambda term: (-metric_float(term.get("spend")), -metric_float(term.get("clicks")), str(term.get("search_term"))),
    )
    root_protected_terms = build_root_protected_terms(product, ad_data, period_comparison)
    root_protected_names = {item["term"].lower() for item in root_protected_terms}

    conflict_terms = {item.get("search_term") for item in ad_group_conflicts if item.get("search_term")}
    historically_protected = period_term_names(
        period_comparison,
        ["historically_good_recently_weak", "historically_good_no_recent_traffic", "consistently_converting", "recently_improved"],
    )
    historically_protected |= {
        str(term)
        for term in period_comparison.get("long_history_protected_terms", []) or []
        if clean_text(term)
    }
    protected_names = {term.get("search_term") for term in converting if term.get("search_term")}
    protected_names |= conflict_terms
    protected_names |= historically_protected

    protected_terms = []
    for name in sorted(protected_names):
        term = lookup.get(name, {"search_term": name})
        reasons = []
        if metric_float(term.get("orders")) > 0:
            reasons.append("has_orders")
        if name in conflict_terms:
            reasons.append("ad_group_conflict")
        if name in historically_protected:
            reasons.append("period_protected")
        protected_terms.append(compact_term_evidence(term, ",".join(reasons) or "protected"))

    low_ctr_diagnostic_candidates = []
    for term in all_terms:
        ctr_context = term.get("ctr_context") or {}
        ctr_signal = ctr_context.get("ctr_signal")
        if ctr_signal not in {"ctr_weakness", "strong_ctr_weakness", "weak_sample_strong_ctr_weakness"}:
            continue
        if term.get("search_term") in protected_names and metric_float(term.get("orders")) == 0:
            continue
        if metric_float(term.get("impressions")) < CTR_MIN_IMPRESSIONS_WEAK:
            continue
        low_ctr_diagnostic_candidates.append(
            compact_term_evidence(
                term,
                "low_relative_ctr_review_semantic_fit_and_creative_relevance",
                {
                    "required_human_or_model_check": "Use CTR only as a relevance/creative diagnostic. Negative only when product facts also prove semantic mismatch.",
                    "default_action": "diagnose_or_bid_down; not_auto_negative",
                },
            )
        )
    low_ctr_diagnostic_candidates = sorted(
        low_ctr_diagnostic_candidates,
        key=lambda item: (
            item.get("ctr_signal") != "strong_ctr_weakness",
            -metric_float(item.get("impressions")),
            -metric_float(item.get("spend")),
            str(item.get("search_term")),
        ),
    )[:30]

    performance_qualified_negative_exact_candidates = []
    negative_exact_review_candidates = []
    low_sample_semantic_mismatch_candidates = []
    for term in click_only:
        name = term.get("search_term")
        clicks = metric_float(term.get("clicks"))
        if is_auto_product_targeting_term(term):
            continue
        if name in protected_names:
            continue
        comparison_row = comparison_lookup.get(clean_text(name)) or {}
        period_category = comparison_row.get("category") or term.get("period_category")
        negative_evidence = negative_performance_evidence(term, product, period_category)
        if (
            term.get("data_sufficiency") == "weak"
            and term.get("market_volume_tier") in {"small/niche", "unknown"}
            and not negative_evidence["performance_negative_review"]
            and clicks > CLICK_ONLY_OUTPUT_CLICK_THRESHOLD
        ):
            continue
        root_protected = str(name).lower() in root_protected_names
        default_scope = negative_evidence["recommended_negative_scope"]
        if root_protected:
            default_scope = "source_exact_review_only_root_phrase_protected"
        if term.get("market_volume_tier") == "big/core":
            default_scope = "diagnose_or_bid_down_before_negative_exact"
        candidate = compact_term_evidence(
            term,
            "zero_orders_review_semantic_fit_before_negative",
            {
                "click_signal": click_signal_label(term.get("clicks")),
                "required_human_or_model_check": (
                    "Approve Negative Exact only in the listed Broad/Phrase source scope after checking "
                    "semantic safety. A clear product mismatch may be negated with low sample; CTR alone may not."
                ),
                "default_scope": default_scope,
                "root_protected": root_protected,
                **negative_evidence,
            },
        )
        if clicks <= CLICK_ONLY_OUTPUT_CLICK_THRESHOLD:
            if not negative_evidence["negative_source_eligible"]:
                continue
            candidate["negative_decision_tier"] = "semantic_mismatch_only_low_sample"
            candidate["performance_direct_negative_exact"] = False
            candidate["do_not_output_unless_semantic_mismatch"] = True
            low_sample_semantic_mismatch_candidates.append(candidate)
        elif negative_evidence["performance_direct_negative_exact"] and not root_protected:
            performance_qualified_negative_exact_candidates.append(candidate)
        else:
            negative_exact_review_candidates.append(candidate)

    low_sample_semantic_mismatch_candidates = sorted(
        low_sample_semantic_mismatch_candidates,
        key=lambda item: (
            item.get("ctr_signal") not in {"strong_ctr_weakness", "weak_sample_strong_ctr_weakness"},
            -metric_float(item.get("spend_multiple")),
            -metric_float(item.get("spend")),
            -metric_float(item.get("impressions")),
            str(item.get("search_term")),
        ),
    )

    scoped_negative_plan = build_scoped_negative_plan(
        scope_data,
        lookup,
        comparison_lookup,
        product,
        {clean_text(name) for name in protected_names if clean_text(name)},
        root_protected_names,
    )
    if scope_data.get("status") == "ok":
        performance_qualified_negative_exact_candidates = scoped_negative_plan["direct_exact"]
        negative_exact_review_candidates = scoped_negative_plan["review_exact"]
        low_sample_semantic_mismatch_candidates = scoped_negative_plan["semantic_exact"]
    negative_root_phrase_candidates = scoped_negative_plan["root_phrase"]
    blocked_negative_root_candidates = scoped_negative_plan["blocked_roots"]

    diagnostic_candidates = []
    for category, reason in [
        ("historically_good_recently_weak", "diagnose_before_negative"),
        ("historically_good_no_recent_traffic", "verify_coverage_and_restore_traffic"),
        ("recent_efficiency_decline", "bid_or_listing_diagnostic"),
    ]:
        for row in period_rows(period_comparison, category, 20):
            name = row.get("search_term")
            if not name:
                continue
            term = lookup.get(name, {"search_term": name})
            diagnostic_candidates.append(
                compact_term_evidence(
                    term,
                    reason,
                    {
                        "period_category": category,
                        "baseline": row.get("baseline"),
                        "recent": row.get("recent"),
                        **period_evidence(row),
                    },
                )
            )

    manual_targeting = build_manual_targeting_candidates(
        all_terms,
        converting,
        period_comparison,
        comparison_lookup,
        lookup,
        target_acos,
    )
    keyword_converting = [
        term for term in converting if not is_auto_product_targeting_term(term)
    ]
    converting_candidate_count = len(keyword_converting)
    converting_total_orders = sum(metric_float(term.get("orders")) for term in keyword_converting)
    top3_orders = sum(
        metric_float(term.get("orders"))
        for term in sorted(
            keyword_converting,
            key=lambda term: -metric_float(term.get("orders")),
        )[:3]
    )
    converting_pool_sufficient = bool(
        (converting_candidate_count >= 6 and converting_total_orders >= 12)
        or (converting_candidate_count >= 3 and top3_orders >= 15)
    )
    if converting_pool_sufficient:
        zero_growth = {
            "zero_order_exact_candidates": [],
            "zero_order_phrase_candidates": [],
        }
        zero_candidate_limit = 0
    else:
        if converting_candidate_count == 0:
            zero_candidate_limit = ZERO_ORDER_MAX_WITHOUT_CONVERSIONS
        else:
            zero_candidate_limit = ZERO_ORDER_MAX_WITH_CONVERSIONS
        zero_growth = build_zero_order_growth_candidates(
            click_only,
            product,
            max_total=zero_candidate_limit,
            max_phrase=min(2, zero_candidate_limit),
        )
    manual_targeting.update(zero_growth)
    discovery_gate = {
        "converting_candidate_count": converting_candidate_count,
        "converting_total_orders": maybe_int(converting_total_orders),
        "top3_converting_orders": maybe_int(top3_orders),
        "converting_pool_sufficient": converting_pool_sufficient,
        "zero_order_discovery_enabled": not converting_pool_sufficient,
        "zero_order_candidate_limit": zero_candidate_limit,
        "rule": (
            "Disable zero-order discovery when there are at least 6 converting candidates and 12 total orders, "
            "or at least 3 converting candidates whose top 3 total 15 orders."
        ),
    }

    ad_group_scoped_actions = []
    for conflict in ad_group_conflicts[:30]:
        good_scopes = conflict.get("good_scopes") or conflict.get("good_ad_groups") or []
        weak_scopes = conflict.get("weak_scopes") or conflict.get("weak_ad_groups") or []
        ad_group_scoped_actions.append(
            {
                "search_term": conflict.get("search_term"),
                "reason": "term_converts_in_some_ad_groups_but_wastes_spend_in_others",
                "recommended_scope": conflict.get("recommended_scope") or "weak_ad_group_only",
                "good_scopes": good_scopes,
                "weak_scopes": weak_scopes,
                "global_negative_blocked": True,
                "recommended_weak_scope_action": conflict.get("recommended_weak_scope_action") or "ad_group_review",
                "do_not_put_in_global_negative_tables": True,
            }
        )

    history_independent = historical_evidence_is_independent(period_comparison)
    all_campaign_action_candidates = build_campaign_action_candidates(
        scope_data,
        lookup,
        comparison_lookup,
        target_acos,
        history_independent,
    )
    campaign_action_candidates, campaign_watchlist_candidates = prioritize_campaign_action_candidates(
        all_campaign_action_candidates,
        target_acos,
    )
    section3_references = {
        row.get("search_term"): row.get("action_id")
        for row in campaign_action_candidates
        if row.get("search_term") and row.get("action_id")
    }
    for key in [
        "manual_exact_candidates",
        "manual_phrase_seed_candidates",
        "manual_broad_seed_candidates",
    ]:
        for row in manual_targeting.get(key, []):
            reference = section3_references.get(row.get("search_term"))
            if reference:
                row["section3_action_reference"] = reference

    return {
        "status": "ok",
        "decision_plan_schema": {
            "required_fields": [
                "search_term",
                "evidence",
                "semantic_fit",
                "performance_signal",
                "recommended_action",
                "scope",
                "confidence",
            ],
            "semantic_fit_values": ["relevant", "mismatch", "uncertain", "not_enough_product_facts"],
            "confidence_rule": "Low-confidence semantic guesses must not become global negatives.",
        },
        "report_presentation_policy": {
            "section_count": int(DECISION_POLICY["output"]["section_count"]),
            "cross_section_priority": ["Section 1 old-campaign actions", "Section 2 new-campaign execution", "Section 3 quick-copy negatives"],
            "section1_rule": "Preserve prepared source-scope actions without applying new-campaign visibility filtering.",
            "section2_rule": "Use the deterministic weighted visibility and completed campaign-group expansion.",
            "section3_rule": "Render only semantically verified executable negatives at the smallest safe scope.",
            "product_targeting_rule": "Render product/ASIN targeting once in the dedicated Section 2 product-targeting table.",
            "watchlist_rule": "Keep lower-signal protected terms in Section 1 only when the prepared renderer selects them.",
        },
        "protected_terms": protected_terms,
        "root_protected_terms": root_protected_terms,
        "click_only_output_policy": {
            "phrase_broad_early_review_clicks": DISCOVERY_EARLY_REVIEW_CLICKS,
            "phrase_broad_direct_gate_clicks": DISCOVERY_DIRECT_NEGATIVE_CLICKS,
            "default_rule": "Exact targets use economics-based bid/pause ladders. Phrase/Broad queries enter scoped review from three clicks when a second relevance/economics signal exists; every low-sample hard-mismatch candidate remains available for semantic audit. CTR alone never qualifies a negative.",
            "applies_to": "click-only terms and negative candidates only; converting terms are still analyzed normally.",
        },
        "negative_decision_levels": {
            "direct": "Performance-qualified source-scope Negative Exact after semantic safety audit, or any sample size with product-fact-proven hard mismatch.",
            "review": "One evidence family or ordinary zero-order volume; keep it in Section 1 review evidence and out of quick-copy execution unless product facts prove a hard mismatch.",
            "blocked": "Protected, scoped-conflict, product-targeting, CTR-only, or low-sample non-mismatch terms never enter Section 3 quick-copy negatives.",
        },
        "performance_qualified_negative_exact_candidates": performance_qualified_negative_exact_candidates,
        "negative_exact_review_candidates": negative_exact_review_candidates,
        "low_sample_semantic_mismatch_candidates": low_sample_semantic_mismatch_candidates,
        "negative_root_phrase_candidates": negative_root_phrase_candidates,
        "blocked_negative_root_candidates": blocked_negative_root_candidates,
        "negative_phrase_rule": "Use the smallest source-scoped root. A product-fact-proven invalid root may qualify after two distinct zero-order queries. A performance root requires at least three queries/eight clicks (four/twelve for one token), 1.5× gross-profit spend or 1.5 expected orders, persistence, and a second CTR/CPC/spend signal. Any converting, historical, Exact, or root-protected variant blocks it.",
        "zero_order_discovery_gate": discovery_gate,
        **manual_targeting,
        "diagnostic_candidates": diagnostic_candidates[:40],
        "low_ctr_diagnostic_candidates": low_ctr_diagnostic_candidates,
        "campaign_action_candidates": campaign_action_candidates,
        "campaign_watchlist_candidates": campaign_watchlist_candidates,
        # Internal, untruncated source rows for rebuilding one new keyword
        # from every matching source campaign. The compact package omits this
        # list; the rebuild plan consumes it before serialization.
        "campaign_bid_merge_sources": all_campaign_action_candidates,
        "ad_group_scoped_actions": ad_group_scoped_actions,
        "model_instructions": [
            "Start the report from these candidates, then verify semantic fit using product_facts.",
            "Section 1 is the prepared old-campaign source-scope action table; do not filter, rerank, or aggregate it with the new-campaign policy.",
            "Section 2 is the prepared new-campaign execution table; preserve its weighted visibility and complete campaign groups.",
            "Use low_ctr_diagnostic_candidates as auxiliary semantic/creative relevance evidence; never recommend a negative from CTR alone.",
            "Put performance_qualified_negative_exact_candidates into Section 3 quick copy only after semantic safety and the exact listed Phrase/Broad campaign/ad-group/source-target check; keep ordinary negative_exact_review_candidates visible as review actions.",
            "Audit negative_root_phrase_candidates using every supporting_terms row. Approve only the smallest unsafe root in the listed source scope; any protected or converting variant blocks approval.",
            "Keep prioritized low_sample_semantic_mismatch_candidates visible as review-only. Move one to quick-copy execution only when product facts prove a hard mismatch; never negate from low CTR alone.",
            "Use Negative Phrase for a product-fact-proven invalid recurring root or a performance-qualified root cluster only when the entire smallest root is unsafe and no converting/protected/relevant variant contains it.",
            "Never place protected_terms or ad_group_scoped_actions into Section 3 quick-copy negatives.",
            "Never put root_protected_terms into Negative Phrase; exact negatives for these terms need explicit evidence and caution.",
            "Respect cpc_decision_mode: launch_test/diagnostic_protect/historical_protect protect relevant terms from negatives, but multiple-order extreme economics may still require controlled bid reduction.",
            "For exactly one attributed order, use baseline_max_cpc rather than local Min ACoS for the first bid move: hold or reduce 0%-5% within the cap and reduce only 5%-10% above it.",
            "Respect traffic_action_mode for historically good low-traffic terms: increase_test can raise bids slightly; hold_diagnose_visibility/diagnose_ctr_or_creative should diagnose before raising.",
            "Never use overlapping baseline orders to justify a bid increase; overlapping history is non-independent evidence.",
            "Keep product_targeting_candidates out of Exact/Phrase/Broad keyword rows; render them in a separate Section 2 product-targeting table.",
            "For derived Phrase/Broad roots, current_targeting_status refers to the proposed keyword and must be verified; source_term_targeting_status refers only to the supporting search term.",
            "Apply report_presentation_policy and preserve the deterministic three-section renderer output.",
            "Use the sidecar search-term files only when these candidate lists are insufficient.",
        ],
    }


def _run_analysis() -> int:
    configure_utf8_output()
    parser = ActionArgumentParser(description=__doc__)
    parser.add_argument("--ad-file", type=Path, help="Single-period ad search-term report, or current report when not using dual-period mode.")
    parser.add_argument("--recent-ad-file", type=Path, help="Recent-period ad search-term report for dual-period comparison.")
    parser.add_argument("--baseline-ad-file", type=Path, help="Longer/historical baseline ad search-term report for dual-period comparison.")
    parser.add_argument("--placement-file", type=Path, help="Optional daily Sponsored Products placement report. Placement analysis is disabled when omitted.")
    parser.add_argument(
        "--campaign-plan-mode",
        choices=["optimize-existing", "rebuild"],
        default="rebuild",
        help=(
            "Defaults to rebuild for a complete new-campaign execution table. "
            "Pass optimize-existing explicitly only when live campaigns should be adjusted in place."
        ),
    )
    parser.add_argument("--product-file", type=Path)
    parser.add_argument("--aba-file", type=Path, help="Optional ABA/Search Query Performance file with search term and ABA/Search Frequency Rank columns.")
    parser.add_argument("--product-db", type=Path, default=default_product_db())
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--product-key")
    parser.add_argument("--asin")
    parser.add_argument("--marketplace", choices=["US", "DE"])
    parser.add_argument("--target-acos")
    parser.add_argument("--average-order-value")
    parser.add_argument("--gross-margin-range")
    parser.add_argument("--baseline-cvr", help="Baseline product conversion rate, such as 12%% or 0.12.")
    parser.add_argument("--lifecycle-stage", help="Product lifecycle stage, such as launch, growth, mature, or stable.")
    args = parser.parse_args()

    requested_dual_period = bool(args.recent_ad_file or args.baseline_ad_file)
    if requested_dual_period and not (args.recent_ad_file and args.baseline_ad_file):
        raise ActionRequired(
            "fix_invalid_arguments",
            "--recent-ad-file和--baseline-ad-file必须同时提供。",
        )
    if not requested_dual_period and not args.ad_file:
        raise ActionRequired(
            "fix_invalid_arguments",
            "单期模式请提供--ad-file；双期模式请同时提供--recent-ad-file和--baseline-ad-file。",
        )

    current_ad_file = args.recent_ad_file if requested_dual_period else args.ad_file
    assert current_ad_file is not None

    ad_files_to_check = [current_ad_file] + ([args.baseline_ad_file] if args.baseline_ad_file else [])
    for ad_file in ad_files_to_check:
        if not ad_file.exists():
            raise ActionRequired("fix_input_file", f"找不到广告报表：{ad_file}")
    if args.product_file and not args.product_file.exists():
        raise ActionRequired("fix_input_file", f"找不到产品报表：{args.product_file}")
    if args.aba_file and not args.aba_file.exists():
        raise ActionRequired("fix_input_file", f"找不到ABA报表：{args.aba_file}")
    if args.placement_file and not args.placement_file.exists():
        raise ActionRequired("fix_input_file", f"找不到广告位报表：{args.placement_file}")

    if args.output_dir is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        args.output_dir = Path(tempfile.gettempdir()) / f"amazon-ad-analysis-{stamp}"

    if args.output_dir.exists():
        raise ActionRequired("choose_new_output_directory", f"输出目录已存在，请使用新目录：{args.output_dir}")
    args.output_dir.mkdir(parents=True, exist_ok=False)

    products = load_product_db(args.product_db)
    ad_tables = load_table(current_ad_file, args.marketplace)
    placement_tables = load_table(args.placement_file, args.marketplace) if args.placement_file else []
    product_tables = load_table(args.product_file, args.marketplace) if args.product_file else []
    baseline_tables = load_table(args.baseline_ad_file, args.marketplace) if args.baseline_ad_file else []
    aba_tables = load_table(args.aba_file, args.marketplace) if args.aba_file else []
    file_paths = ad_files_to_check + ([args.product_file] if args.product_file else []) + ([args.aba_file] if args.aba_file else [])
    identifier_text = " ".join(
        [
            text_tokens_from_identifier_columns(ad_tables),
            text_tokens_from_identifier_columns(baseline_tables),
            text_tokens_from_identifier_columns(product_tables),
        ]
    )
    product_context_text = " ".join(
        [
            text_tokens_from_product_context(ad_tables),
            text_tokens_from_product_context(baseline_tables),
            text_tokens_from_product_context(product_tables),
        ]
    )
    report_identifiers = extract_report_product_identifiers(ad_tables + baseline_tables)
    product_match = match_products(
        products,
        file_paths,
        args.product_key,
        args.asin,
        identifier_text,
        product_context_text,
        report_identifiers,
    )
    matched_product_for_dates = product_match.get("matched_product") or {}
    marketplace_for_dates = resolve_marketplace(
        args.marketplace,
        ad_tables + placement_tables + baseline_tables + product_tables + aba_tables,
        matched_product_for_dates.get("marketplace_default"),
    )
    currency_code = currency_code_for_marketplace(marketplace_for_dates)
    currency_symbol = currency_symbol_for_marketplace(marketplace_for_dates)
    for tables in [ad_tables, placement_tables, product_tables, baseline_tables, aba_tables]:
        normalize_table_numeric_values(tables, marketplace_for_dates)
    current_date_range = resolve_date_range(current_ad_file, ad_tables, marketplace_for_dates)
    recent_date_range = (
        resolve_date_range(args.recent_ad_file, ad_tables, marketplace_for_dates)
        if args.recent_ad_file
        else None
    )
    baseline_date_range = (
        resolve_date_range(args.baseline_ad_file, baseline_tables, marketplace_for_dates)
        if args.baseline_ad_file
        else None
    )

    source_ad_tables = ad_tables
    source_baseline_tables = baseline_tables
    source_ad_file_date_range = current_date_range
    daily_context = {"status": "not_applicable", "reason": "explicit_dual_period_input"}
    daily_selection = {"status": "not_applicable"}
    daily_mode = False
    daily_comparison = False
    full_history_ad_data = None
    analysis_mode = "dual_period_comparison" if requested_dual_period else "single_period"
    if not requested_dual_period:
        daily_context = daily_report_context(current_ad_file, ad_tables, marketplace_for_dates)
        if daily_context.get("status") == "daily":
            daily_mode = True
            daily_selection = select_daily_analysis_windows(ad_tables, daily_context)
            ad_tables = daily_selection.get("recent_tables") or ad_tables
            full_history_ad_data = daily_selection.get("full_history_data")
            recent_date_range = daily_selection.get("recent_range")
            current_date_range = recent_date_range
            if daily_selection.get("status") == "comparison":
                daily_comparison = True
                baseline_tables = daily_selection.get("baseline_tables") or []
                baseline_date_range = daily_selection.get("baseline_range")
                analysis_mode = "daily_window_comparison"
            else:
                baseline_tables = []
                baseline_date_range = None
                analysis_mode = "daily_single_period"

            if product_match.get("status") == "matched" and not product_match_has_explicit_identifier(
                product_match,
                file_paths,
                identifier_text,
                args.product_key,
                args.asin,
            ):
                product_match.update(
                    {
                        "status": "identifier_required",
                        "matched_product": None,
                        "matched_identity": None,
                        "candidate_keys": [],
                        "candidate_identities": [],
                        "confirmation_required": True,
                        "confirmation_message": product_match_message("identifier_required", None, []),
                    }
                )

    dual_period = requested_dual_period or daily_comparison
    daily_selection_context = {
        key: value
        for key, value in daily_selection.items()
        if key not in {
            "recent_tables",
            "baseline_tables",
            "recent_data",
            "baseline_data",
            "full_history_data",
            "signal_evaluations",
        }
    }
    if daily_selection.get("signal_evaluations") is not None:
        daily_selection_context["signal_evaluation_count"] = len(
            daily_selection.get("signal_evaluations") or []
        )

    ad_data = daily_selection.get("recent_data") if daily_mode else None
    if not ad_data:
        ad_data = aggregate_ad_data(ad_tables)
    ad_group_data = aggregate_terms_by_dimension(ad_tables, AD_GROUP_COLUMNS, "ad_group")
    match_type_data = aggregate_terms_by_dimension(ad_tables, MATCH_TYPE_COLUMNS, "match_type")
    campaign_data = aggregate_terms_by_dimension(ad_tables, CAMPAIGN_COLUMNS, "campaign")
    scope_data = aggregate_terms_by_scope(ad_tables)
    ad_group_conflicts = find_ad_group_conflicts(ad_group_data)

    if ad_data.get("status") == "missing_required_columns":
        raise ActionRequired(
            "fix_required_columns",
            missing_required_columns_message("当前广告文件", ad_data),
        )
    if scope_data.get("status") != "ok":
        raise ActionRequired(
            "fix_source_scope_columns",
            "广告报表必须包含可映射的广告活动、广告组和投放目标列，才能生成可执行的来源范围动作。",
            {
                "campaign_columns_found": scope_data.get("campaign_columns_found") or [],
                "ad_group_columns_found": scope_data.get("ad_group_columns_found") or [],
                "targeting_columns_found": scope_data.get("targeting_columns_found") or [],
                "invalid_rows": scope_data.get("invalid_rows") or [],
            },
        )
    product_action_codes = {
        "identifier_required": "confirm_product_identifier",
        "none": "ask_user_for_product_facts",
        "multiple": "ask_user_to_choose_product",
        "multiple_report_products": "use_single_product_report",
        "identifier_conflict": "fix_product_identifier_conflict",
    }
    if product_match.get("status") in product_action_codes:
        raise ActionRequired(
            product_action_codes[str(product_match.get("status"))],
            str(product_match.get("confirmation_message") or "请核对产品身份后重新运行。"),
            {"report_identifiers": product_match.get("report_identifiers") or report_identifiers},
        )

    baseline_ad_data = None
    comparison_baseline_date_range = baseline_date_range
    baseline_derivation = {
        "status": "not_applicable",
        "reason": "A derived baseline is only considered in dual-period mode with overlapping ranges.",
    }
    period_comparison = {
        "status": "single_period",
        "note": "Only one ad report was supplied; analyze the supplied period without historical trend conclusions.",
    }
    if daily_comparison:
        baseline_ad_data = daily_selection.get("baseline_data")
        comparison_baseline_date_range = daily_selection.get("baseline_range")
        baseline_derivation = {
            "status": "daily_window_split",
            "method": "single_daily_report_date_windows",
            "selection_reason": daily_selection.get("selection_reason"),
            "recent_range": daily_selection.get("recent_range"),
            "comparison_baseline_date_range": comparison_baseline_date_range,
            "evaluations": daily_selection.get("evaluations", []),
            "signal_window": daily_selection.get("signal_window"),
            "validation_window": daily_selection.get("validation_window"),
            "source_file_date_range": source_ad_file_date_range,
        }
        period_comparison = compare_periods(baseline_ad_data, ad_data)
        period_comparison["date_overlap_check"] = {
            "status": "daily_windows_non_overlapping",
            "recent_start": (recent_date_range or {}).get("start"),
            "recent_end": (recent_date_range or {}).get("end"),
            "recent_days": (recent_date_range or {}).get("days"),
            "comparison_baseline_start": (comparison_baseline_date_range or {}).get("start"),
            "comparison_baseline_end": (comparison_baseline_date_range or {}).get("end"),
            "comparison_baseline_days": (comparison_baseline_date_range or {}).get("days"),
            "warning": None,
            "note": "Recent and baseline windows were split directly from one row-level daily report.",
            "signal_window": daily_selection.get("signal_window"),
            "validation_window": daily_selection.get("validation_window"),
        }
        period_comparison["baseline_derivation"] = baseline_derivation
        period_comparison["decision_rule"] = (
            f"{period_comparison.get('decision_rule', '')} "
            "The recent and baseline periods come from non-overlapping date windows in one daily report, "
            "so they are independent and may support trend and bid decisions under the normal guardrails."
        ).strip()
    elif daily_mode:
        baseline_derivation = {
            "status": "daily_single_period",
            "method": "single_daily_report_date_window",
            "selection_reason": daily_selection.get("selection_reason"),
            "recent_range": daily_selection.get("recent_range"),
            "evaluations": daily_selection.get("evaluations", []),
            "source_file_date_range": source_ad_file_date_range,
        }
        period_comparison["baseline_derivation"] = baseline_derivation
    elif requested_dual_period and args.baseline_ad_file:
        source_baseline_ad_data = aggregate_ad_data(baseline_tables)
        if source_baseline_ad_data.get("status") == "missing_required_columns":
            raise ActionRequired(
                "fix_required_columns",
                missing_required_columns_message("基准广告文件", source_baseline_ad_data),
            )
        baseline_derivation_result = derive_exclusive_baseline(
            source_baseline_ad_data,
            ad_data,
            baseline_date_range,
            recent_date_range,
        )
        baseline_derivation = {
            key: value
            for key, value in baseline_derivation_result.items()
            if key != "baseline_data"
        }
        if baseline_derivation_result.get("status") == "success":
            baseline_ad_data = baseline_derivation_result["baseline_data"]
            comparison_baseline_date_range = baseline_derivation_result["comparison_baseline_date_range"]
        else:
            baseline_ad_data = source_baseline_ad_data
        period_comparison = compare_periods(baseline_ad_data, ad_data)
        if baseline_derivation_result.get("status") == "success":
            original_overlap = baseline_derivation_result.get("original_overlap") or {}
            period_comparison["date_overlap_check"] = {
                "status": "overlap_resolved_by_subtraction",
                "overlap_start": original_overlap.get("overlap_start"),
                "overlap_end": original_overlap.get("overlap_end"),
                "overlap_days": original_overlap.get("overlap_days"),
                "comparison_baseline_start": comparison_baseline_date_range.get("start"),
                "comparison_baseline_end": comparison_baseline_date_range.get("end"),
                "comparison_baseline_days": date_range_days(comparison_baseline_date_range),
                "warning": None,
                "note": baseline_derivation_result.get("note"),
            }
            period_comparison["baseline_derivation"] = baseline_derivation
            period_comparison["decision_rule"] = (
                f"{period_comparison.get('decision_rule', '')} "
                "The overlapping recent suffix was removed from the cumulative baseline after term-level "
                "additive-metric validation, so the derived prior period is independent and may support "
                "trend and bid decisions subject to the normal evidence guardrails."
            ).strip()
        else:
            period_comparison = attach_period_overlap_warning(
                period_comparison,
                baseline_date_range,
                recent_date_range,
            )
            period_comparison["baseline_derivation"] = baseline_derivation

    if daily_mode and full_history_ad_data and full_history_ad_data.get("status") == "ok":
        long_history_protected_terms = sorted(
            str(term.get("search_term"))
            for term in full_history_ad_data.get("all_terms", [])
            if term.get("search_term") and metric_float(term.get("orders")) > 0
        )
        period_comparison["long_history_protected_terms"] = long_history_protected_terms
        period_comparison["long_history_context"] = {
            "source": "full_single_daily_report",
            "source_range": source_ad_file_date_range,
            "protected_term_count": len(long_history_protected_terms),
            "decision_rule": "Older history protects terms from unsafe negatives but does not independently justify a bid increase.",
        }

    aba_rank_map = load_aba_rank_map(source_ad_tables + source_baseline_tables + aba_tables)
    market_signal_context = enrich_terms_with_market_signal(ad_data, aba_rank_map)
    enrich_terms_with_market_signal(baseline_ad_data, aba_rank_map)

    product_dashboard = {"status": "not_supplied"}
    if args.product_file:
        product_dashboard = summarize_product_dashboard(product_tables)

    matched_product_raw = product_match.get("matched_product") or {}
    matched_product = apply_product_overrides(matched_product_raw, args)
    matched_identity = product_identity(matched_product) if matched_product else None
    product_match["matched_product"] = matched_product if product_match.get("status") == "matched" else None
    product_match["matched_identity"] = matched_identity
    try:
        profit_context = profitability_context(matched_product, args)
        target_acos_value = effective_target_acos(matched_product)
    except ValueError as exc:
        raise ActionRequired("fix_product_economics", f"产品经济参数无效：{exc}") from exc
    if args.campaign_plan_mode == "rebuild" and profit_context.get("estimated_gross_profit_per_order") is None:
        missing_economics = []
        if profit_context.get("average_order_value") is None:
            missing_economics.append("AOV")
        if profit_context.get("gross_margin_range") is None:
            missing_economics.append("毛利率")
        raise ActionRequired(
            "missing_product_economics",
            f"重建计划需要可验证的金额停止线，请补充{'和'.join(missing_economics) or 'AOV和毛利率'}后重新运行。",
            {"missing_fields": missing_economics},
        )
    margin_context = profit_context.get("gross_margin_range") or {}
    margin_midpoint = margin_context.get("midpoint") if isinstance(margin_context, dict) else None
    profit_caps = [
        value for value in [target_acos_value, margin_midpoint]
        if value is not None and value > 0
    ]
    profit_context["target_acos"] = target_acos_value
    profit_context["profit_acos_cap"] = min(profit_caps) if profit_caps else None
    if baseline_ad_data and baseline_ad_data.get("status") == "ok":
        cvr_source_ad_data = baseline_ad_data
        cvr_source_name = "baseline_ad_data_weighted_average"
    else:
        cvr_source_ad_data = ad_data
        cvr_source_name = "ad_data_weighted_average"
    try:
        cvr_baseline = compute_cvr_baseline(matched_product, cvr_source_ad_data, cvr_source_name)
    except ValueError as exc:
        raise ActionRequired("fix_product_economics", f"产品经济参数无效：{exc}") from exc
    acos_cpc_ladder = product_acos_cpc_ladder(profit_context, target_acos_value, cvr_baseline)
    product_match["confirmation_message"] = product_match_message(
        product_match.get("status", "none"),
        matched_identity,
        product_match.get("candidate_identities", []),
        acos_cpc_ladder,
        currency_symbol,
    )
    ctr_peer_maps = build_ctr_peer_maps(ad_data, campaign_data, ad_group_data, match_type_data)
    enrich_terms_with_ctr_context(ad_data, ctr_peer_maps)
    enrich_terms_with_ctr_context(baseline_ad_data, ctr_peer_maps)
    aov_value = profit_context.get("average_order_value")
    account_macro_summary = build_account_macro_summary(
        ad_data,
        baseline_ad_data,
        current_date_range if not dual_period else recent_date_range,
        comparison_baseline_date_range,
        match_type_data,
        campaign_data,
        ad_group_data,
    )
    cpc_control_context = build_cpc_control_context(account_macro_summary)
    term_period_categories = period_category_map(period_comparison)
    enrich_ad_data_with_bid_economics(
        ad_data,
        target_acos_value,
        aov_value,
        cvr_baseline,
        term_period_categories,
        cpc_control_context,
    )
    enrich_ad_data_with_bid_economics(
        baseline_ad_data,
        target_acos_value,
        aov_value,
        cvr_baseline,
        term_period_categories,
        cpc_control_context,
    )
    daily_action_evidence: dict[str, dict[str, Any]] = {}
    if daily_mode:
        daily_action_evidence = build_daily_term_action_evidence(
            source_ad_tables,
            daily_context,
            cvr_baseline,
            profit_context,
            target_acos_value,
        )
        enrich_ad_data_with_daily_action_evidence(ad_data, daily_action_evidence)
    profit_acos_cap = product_profit_acos_cap(matched_product)
    placement_analysis = build_placement_analysis(
        placement_tables,
        source_ad_tables,
        recent_date_range or current_date_range,
        comparison_baseline_date_range,
        marketplace_for_dates,
        profit_context,
        profit_acos_cap,
    )
    if args.placement_file and placement_analysis.get("status") != "ok":
        raise ActionRequired(
            "fix_placement_input",
            f"广告位报表无法作为匹配证据：{placement_analysis.get('reason') or placement_analysis.get('status')}。",
            placement_analysis,
        )
    enrich_ad_data_with_action_eligibility(ad_data, profit_acos_cap)
    enrich_ad_data_with_action_eligibility(baseline_ad_data, profit_acos_cap)
    scoped_conflict_evidence = build_scoped_conflict_evidence(
        scope_data,
        period_comparison,
        profit_context,
    )
    candidate_actions = build_candidate_actions(
        ad_data,
        period_comparison,
        campaign_data,
        scope_data,
        scoped_conflict_evidence or ad_group_conflicts,
        matched_product,
    )
    # Placement evidence is optional and only designs newly created campaigns.
    # Existing campaigns remain user-controlled: their bid recommendations use
    # source bid, actual CPC, and economics without a placement adjustment.
    placement_evidence_enabled = bool(placement_analysis.get("enabled"))
    placement_enabled = args.campaign_plan_mode == "rebuild"
    enrich_candidate_actions_with_placement_plans(
        candidate_actions,
        matched_product,
        placement_enabled=placement_enabled,
    )
    mature_growth_context = build_mature_growth_context(
        ad_data,
        match_type_data,
        matched_product,
        account_macro_summary,
        cpc_control_context,
        profit_context,
        target_acos_value,
        currency_symbol,
    )
    for row in mature_growth_context.get("micro_phrase_candidates", []) or []:
        if placement_enabled:
            row["placement_plan"] = keyword_placement_plan(row, "manual_phrase", matched_product)
            enrich_row_with_placement_bid_math(row)
        else:
            row.pop("placement_plan", None)
    controlled_exploration = build_controlled_exploration(
        candidate_actions,
        mature_growth_context,
        match_type_data,
        matched_product,
        period_comparison,
        daily_selection_context,
        profit_context,
        acos_cpc_ladder,
    )
    for row in controlled_exploration.get("phrase_candidates", []) or []:
        if placement_enabled:
            row["placement_plan"] = keyword_placement_plan(row, "manual_phrase", matched_product)
            enrich_row_with_placement_bid_math(row)
        else:
            row.pop("placement_plan", None)
    if controlled_exploration.get("auto_close_test"):
        if placement_enabled:
            controlled_exploration["auto_close_test"]["placement_plan"] = keyword_placement_plan(
                controlled_exploration["auto_close_test"],
                "auto_learning",
                matched_product,
            )
        else:
            controlled_exploration["auto_close_test"].pop("placement_plan", None)
    rebuild_campaign_plan = build_rebuild_campaign_plan(
        candidate_actions,
        mature_growth_context,
        controlled_exploration,
        args.campaign_plan_mode,
        placement_enabled,
        placement_analysis,
        profit_context,
        cvr_baseline,
        (account_macro_summary.get("recent") or {}).get("cvr"),
        currency_symbol,
    )
    term_files = write_search_term_files(args.output_dir, ad_data)
    baseline_term_files = write_search_term_files(args.output_dir, baseline_ad_data, "baseline_search_terms_aggregated") if baseline_ad_data else None
    ad_group_files = write_dimension_files(args.output_dir, ad_group_data, "search_terms_by_ad_group", "ad_group")
    scope_files = write_dimension_files(args.output_dir, scope_data, "search_terms_by_scope", "scope")

    package = {
        "prepared_at": datetime.now().isoformat(timespec="seconds"),
        "files": {
            "ad_file": str(current_ad_file),
            "recent_ad_file": str(args.recent_ad_file) if args.recent_ad_file else None,
            "baseline_ad_file": str(args.baseline_ad_file) if args.baseline_ad_file else None,
            "placement_file": str(args.placement_file) if args.placement_file else None,
            "product_file": str(args.product_file) if args.product_file else None,
            "aba_file": str(args.aba_file) if args.aba_file else None,
            "product_db": str(args.product_db),
            "aggregated_search_terms": term_files,
            "baseline_aggregated_search_terms": baseline_term_files,
            "search_terms_by_ad_group": ad_group_files,
            "search_terms_by_scope": scope_files,
        },
        "user_overrides": {
            "marketplace": args.marketplace,
            "target_acos": args.target_acos,
            "average_order_value": args.average_order_value,
            "gross_margin_range": args.gross_margin_range,
            "baseline_cvr": args.baseline_cvr,
            "lifecycle_stage": args.lifecycle_stage,
            "asin": args.asin,
            "product_key": args.product_key,
            "campaign_plan_mode": args.campaign_plan_mode,
        },
        "analysis_context": {
            "analysis_mode": analysis_mode,
            "ad_file_date_range": current_date_range,
            "recent_file_date_range": recent_date_range,
            "baseline_file_date_range": baseline_date_range,
            "source_ad_file_date_range": source_ad_file_date_range,
            "comparison_baseline_date_range": comparison_baseline_date_range,
            "baseline_derivation": baseline_derivation,
            "daily_report_context": daily_context,
            "daily_window_selection": daily_selection_context,
            "daily_action_evidence_summary": {
                "terms_with_action_evidence": len(daily_action_evidence),
                "scale_signal_terms": sum(
                    1 for evidence in daily_action_evidence.values() if evidence.get("scale_signal")
                ),
                "zero_order_signal_terms": sum(
                    1 for evidence in daily_action_evidence.values() if evidence.get("zero_order_signal")
                ),
            },
            "period_note": "Use a validated single-file daily split first, explicit dual reports second, and single-period analysis only when no independent comparison is available.",
            "marketplace": marketplace_for_dates,
            "currency_code": currency_code,
            "currency_symbol": currency_symbol,
            "target_acos": args.target_acos if args.target_acos is not None else matched_product.get("target_acos"),
            "target_acos_note": "If null, evaluate ACoS relatively rather than labeling objectively good/bad.",
            "profitability": profit_context,
            "cvr_baseline": cvr_baseline,
            "acos_cpc_ladder": acos_cpc_ladder,
            "cpc_control_context": cpc_control_context,
            "ctr_relevance_check": {
                "policy": ctr_peer_maps.get("policy"),
                "account_ctr_baseline": ctr_peer_maps.get("account"),
                "decision_rule": "Use relative CTR only as auxiliary relevance/creative evidence. Compare within peer baselines; do not use absolute CTR across products or categories.",
                "negative_guardrail": "Low CTR alone must not create a negative. Combine low relative CTR with semantic mismatch and click/spend/CVR economics.",
            },
            "market_signal_check": market_signal_context,
            "click_only_output_policy": {
                "phrase_broad_early_review_clicks": DISCOVERY_EARLY_REVIEW_CLICKS,
                "phrase_broad_direct_gate_clicks": DISCOVERY_DIRECT_NEGATIVE_CLICKS,
                "default_rule": "Do not use one universal click threshold. Exact targets follow expected-order/profit-spend bid controls; Phrase/Broad queries enter scoped negative review from three clicks when a second signal exists, and hard semantic mismatches remain reviewable at any clicked sample.",
            },
            "zero_order_click_signal_tiers": {
                "0-2": "low_signal_observe",
                "3-5": "phrase_broad_early_review_with_second_signal",
                "6-10": "phrase_broad_direct_negative_gate_with_two_evidence_families",
                "11+": "strong_zero_order_signal",
                "20+": "very_strong_waste_signal",
            },
            "placement_analysis": {
                "status": placement_analysis.get("status"),
                "enabled": placement_analysis.get("enabled", False),
                "reason": placement_analysis.get("reason"),
                "placement_date_range": placement_analysis.get("placement_date_range"),
                "recent_range": placement_analysis.get("recent_range"),
                "baseline_range": placement_analysis.get("baseline_range"),
            },
        },
        "product_match": product_match,
        "product_facts": matched_product,
        "product_data_quality": product_data_quality(matched_product),
        "account_macro_summary": account_macro_summary,
        "candidate_actions": compact_candidate_actions_for_package(candidate_actions),
        "mature_growth_context": compact_mature_growth_context_for_package(mature_growth_context),
        "controlled_exploration": controlled_exploration,
        "ad_data": compact_ad_data_for_package(ad_data),
        "baseline_ad_data": compact_ad_data_for_package(baseline_ad_data) if baseline_ad_data else None,
        "period_comparison": compact_period_comparison_for_package(period_comparison),
        "campaign_analysis": compact_dimension_for_package(campaign_data, []),
        "ad_group_analysis": compact_dimension_for_package(ad_group_data, ad_group_conflicts),
        "scope_analysis": compact_scope_analysis_for_package(scope_data, scoped_conflict_evidence),
        "scoped_conflict_evidence": scoped_conflict_evidence,
        "product_dashboard": product_dashboard,
        "placement_analysis": placement_analysis,
        "placement_columns_enabled": placement_enabled,
        "placement_used_for_new_campaigns": placement_enabled,
        "placement_evidence_enabled": placement_evidence_enabled,
        "campaign_plan_mode": args.campaign_plan_mode,
        "rebuild_campaign_plan": rebuild_campaign_plan,
    }

    package_path = args.output_dir / "analysis_package.json"
    package_path.write_text(
        json.dumps(package, ensure_ascii=False, separators=(",", ":"), default=json_default),
        encoding="utf-8",
    )
    runtime_files = build_prompt(args.output_dir, package_path)

    summary = {
        "output_dir": str(args.output_dir),
        "analysis_package": str(package_path),
        "analysis_prompt": runtime_files["analysis_prompt"],
        "report_payload": runtime_files["report_payload"],
        "report_draft": runtime_files["report_draft"],
        "report_full": runtime_files["report_full"],
        "analysis_mode": package["analysis_context"]["analysis_mode"],
        "ad_file_date_range": package["analysis_context"]["ad_file_date_range"],
        "recent_file_date_range": package["analysis_context"]["recent_file_date_range"],
        "baseline_file_date_range": package["analysis_context"]["baseline_file_date_range"],
        "comparison_baseline_date_range": package["analysis_context"]["comparison_baseline_date_range"],
        "source_ad_file_date_range": package["analysis_context"]["source_ad_file_date_range"],
        "daily_report_status": daily_context.get("status"),
        "daily_window_selection": daily_selection_context,
        "baseline_derivation_status": baseline_derivation.get("status"),
        "product_match_status": product_match["status"],
        "product_match_identity": product_match.get("matched_identity"),
        "product_match_message": product_match.get("confirmation_message"),
        "ad_data_status": ad_data["status"],
        "baseline_ad_data_status": baseline_ad_data["status"] if baseline_ad_data else "not_supplied",
        "campaign_analysis_status": campaign_data["status"],
        "ad_group_analysis_status": ad_group_data["status"],
        "scope_analysis_status": scope_data["status"],
        "scoped_conflict_count": len(scoped_conflict_evidence),
        "period_comparison_status": period_comparison["status"],
        "period_overlap_status": (period_comparison.get("date_overlap_check") or {}).get("status"),
        "period_overlap_warning": period_comparison.get("warning"),
        "unique_search_terms": ad_data.get("unique_search_terms"),
        "converting_count": ad_data.get("converting_count"),
        "click_only_count": ad_data.get("click_only_count"),
        "mature_growth_mode": mature_growth_context.get("recommendation_mode"),
        "controlled_exploration_status": controlled_exploration.get("status"),
        "controlled_phrase_candidate_count": len(controlled_exploration.get("phrase_candidates") or []),
        "controlled_auto_close_enabled": bool(controlled_exploration.get("auto_close_test")),
        "placement_analysis_status": placement_analysis.get("status"),
        "placement_analysis_enabled": placement_analysis.get("enabled", False),
        "campaign_plan_mode": args.campaign_plan_mode,
        "rebuild_campaign_plan_status": rebuild_campaign_plan.get("status"),
        "rebuild_campaign_plan_count": len(rebuild_campaign_plan.get("rows") or []),
    }
    missing_required_column_messages = []
    if ad_data["status"] == "missing_required_columns":
        missing_required_column_messages.append(missing_required_columns_message("当前广告文件", ad_data))
    if baseline_ad_data and baseline_ad_data["status"] == "missing_required_columns":
        missing_required_column_messages.append(missing_required_columns_message("基准广告文件", baseline_ad_data))
    if missing_required_column_messages:
        summary["action_required"] = "fix_required_columns"
        summary["action_message"] = "；".join(missing_required_column_messages)
    elif product_match["status"] == "identifier_required":
        summary["action_required"] = "confirm_product_identifier"
        summary["action_message"] = product_match.get("confirmation_message")
    elif product_match["status"] == "none":
        summary["action_required"] = "ask_user_for_product_facts"
        summary["action_message"] = product_match.get("confirmation_message")
    elif product_match["status"] == "multiple":
        summary["action_required"] = "ask_user_to_choose_product"
        summary["action_message"] = product_match.get("confirmation_message")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    try:
        return _run_analysis()
    except ActionRequired as exc:
        payload: dict[str, Any] = {
            "status": "action_required",
            "action_required": exc.code,
            "action_message": exc.message,
        }
        if exc.details:
            payload["action_details"] = exc.details
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=json_default))
        return 0


if __name__ == "__main__":
    sys.exit(main())
