#!/usr/bin/env python3
"""Validate a finalized US/DE Amazon listing from JSON.

Read JSON from stdin or from ``--file PATH``. This script uses only the
Python standard library so it runs on macOS and Windows.

Required keys:
  marketplace: "US" or "DE"
  title: string
  highlights: string
  claim_audit: non-empty output from claim_review.py, including exact source
               and Chinese location matches for every used claim
  field_claim_ids: mapping from every scope-selected field to used claim IDs
  translations: Chinese translations of every scope-selected field

Optional keys:
  scope: "full_listing" (default) or "title_highlights". Full scope requires
         exactly five bullets and description; partial scope must omit them.
  bullets: exactly five strings in full_listing
  description: source-language description string in full_listing
  keywords: [{"phrase": str, "target": int|"N-M", "reported_used": int,
              "source": "competitor"|"user_priority",
              "eligible_locations": [field names],
              "override_reason": str}]
  blocked_terms: [{"term": str, "match": "phrase"|"substring",
                   "reason": str}]
  own_brand: string
  competitor_brands: [strings]
  package_components: [{"component": str, "source_singular": [str],
                       "source_plural": [str], "translation_terms": [str],
                       "present": bool, "count": int|null,
                       "listing_matches": {field: source wording},
                       "translation_matches": {field: Chinese wording}}]
  compatibility_targets: [Brand/Model strings]
  competitors: [competitor Bullet text strings]
  copy_exemptions: [allowed source-language phrases]

Exit status is 1 when any hard check fails, otherwise 0. Working-target and
justified keyword-override notices are WARN results and do not fail the process.
Security-sensitive collection fields must use the documented JSON list types;
wrong collection types are hard schema failures rather than empty defaults.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from collections import Counter
from pathlib import Path

import kwfreq
from policy_rules import (
    CERTIFICATION_TRANSLATION_RULES,
    HIGH_RISK_TRANSLATION_PATTERNS,
    NONRECOVERABLE_PATTERNS,
    NONRECOVERABLE_TRANSLATION_PATTERNS,
    PACKAGE_DISCLOSURE_PATTERNS,
    REGULATED_DELIVERABLE_PATTERNS,
    UNSUPPORTED_MARKETING_PATTERNS,
    strip_non_commercial_disclaimer,
)

TOKEN_RE = kwfreq.TOKEN_RE
JOINED_UNIT_RE = kwfreq.JOINED_UNIT_RE
INVALID_KEYWORD_OVERRIDE_RE = re.compile(
    r"\b(?:seo|rank(?:ing)?|keyword\s+density|search\s+volume|important\s+keyword|"
    r"competitor\s+frequency|fill(?:ing)?\s+length)\b|"
    r"提高排名|关键词密度|搜索量|重要关键词|竞品频次|凑字(?:数)?",
    re.IGNORECASE,
)
ASIN_RE = re.compile(r"\bB0[A-Z0-9]{8}\b", re.IGNORECASE)
URL_RE = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)
ENCODED_ENTITY_RE = re.compile(r"&(?:#x?[0-9A-Fa-f]+|[A-Za-z]+);")
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE_RE = re.compile(r"(?<!\w)\+?\d[\d\s()./-]{6,}\d(?!\w)")
ISO_DATE_RE = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$")
# Slash-separated size specs never use phone-style leading zeros or 5+ digit
# segments, and must be followed by an unambiguous multi-letter unit (not "in").
SLASH_DIM_SEGMENT = r"(?:0[.,]\d+|[1-9]\d{0,3}(?:[.,]\d+)?)"
SLASH_DIMENSION_RE = re.compile(rf"^{SLASH_DIM_SEGMENT}(?:/{SLASH_DIM_SEGMENT})+$")
SLASH_UNIT_FOLLOW_RE = re.compile(
    r"^\s*(?:mm|cm|km|kg|mg|ml|cl|oz|lbs?|ft|µm|μm|inch(?:es)?|zoll)\b",
    re.IGNORECASE,
)
NUMERIC_MODEL_RE = re.compile(
    r"\b(?:model\b(?:[ \t]+(?:number\b|no\b\.?))?|"
    r"part[ \t]+(?:number\b|no\b\.?)|modell(?:nummer)?\b|teilenummer\b)"
    r"[ \t]*[:#]?[ \t]*(?P<number>[0-9]+)(?!\w)",
    re.IGNORECASE,
)
MODEL_CONTACT_RE = re.compile(
    r"\b(?:call|phone|telephone|tel|contact|fax|whatsapp|hotline|"
    r"telefon|anrufen|rufnummer|kontakt)\b", re.IGNORECASE,
)
END_PUNCTUATION = tuple(".!?;,:。！？；：")
# Generic source-copy budget; category-specific requirements may be stricter.
DESCRIPTION_LIMIT = 2000
PROHIBITED_TITLE_CHARS = set("!$?_{}^¬¦")
PROHIBITED_BULLET_CHARS = set("™®€…†‡¢£¥©±~°*—")
JOINED_BULLET_UNIT_RE = re.compile(
    r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)?(?:mm|cm|ml|kg|oz|lbs?|in|ft|m|l|g)\b",
    re.IGNORECASE,
)
STANDALONE_SINGLE_DIGIT_RE = re.compile(
    r"(?<![A-Za-z0-9])([1-9])(?![A-Za-z0-9])"
    r"(?!\s*-?\s*(?:mm|cm|ml|kg|oz|lbs?|in|ft|m|l|g)\b)",
    re.IGNORECASE,
)
EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "]"
)

DE_TIP_1 = (
    "1. Aufgrund unterschiedlicher Bildschirme und Lichtverhältnisse kann es "
    "zu leichten Farbabweichungen kommen. Bitte beziehen Sie sich auf das "
    "tatsächliche Produkt."
)
DE_TIP_2 = (
    "2. Alle Größenangaben werden manuell gemessen und dienen nur als Referenz; "
    "geringfügige Abweichungen sind möglich. Vielen Dank für Ihr Verständnis."
)
US_TIP_1 = (
    "1. Due to variations in screens and lighting conditions, slight color "
    "differences may occur, which is normal. Please refer to the actual product."
)
US_TIP_2 = (
    "2. All size measurements are manually measured and provided for reference "
    "only; minor deviations may exist. Thank you for your understanding."
)

PROMISE_PATTERNS = {
    "US": (
        r"\bwarrant(?:y|ies)\b", r"\bguarantee(?:d|s)?\b", r"\brefund(?:s|ed)?\b",
        r"\bmoney[\s-]?back\b", r"\blifetime\s+(?:replacement|support)\b",
        r"\bcontact\s+us\b", r"\bcustomer\s+(?:service|support)\b",
    ),
    "DE": (
        r"\bgarantie\w*\b", r"\bgew(?:ä|a)hrleistung\w*\b", r"\berstattung\w*\b",
        r"\bgeld[\s-]?zur(?:ü|u)ck\b", r"\blebenslang\w*\s+(?:ersatz|support)\b",
        r"\bkontaktieren\s+sie\s+uns\b", r"\bkundenservice\b",
    ),
}

META_LANGUAGE_PATTERNS = (
    r"\baccording\s+to\s+(?:the\s+)?(?:provided|source)\s+(?:data|information)\b",
    r"\btarget\s+sku\b",
    r"\brelevant\s+(?:information|details)\b",
    r"\bdie\s+angaben\s+beziehen\s+sich\b",
    r"\brelevanten\s+angaben\b",
    r"\blaut\s+den\s+(?:bereitgestellten\s+)?daten\b",
    r"\bziel[\s-]?sku\b",
    r"\bquelldaten\b",
)

REGULATED_FILTER_PATTERNS = (
    ("HEPA", r"\b(?:true[\s-]+)?hepa\b"),
    ("H13/H14", r"\bh(?:13|14)\b"),
    (
        "allergy suitability",
        r"\b(?:allergy[\s-]?friendly|allergikergeeignet\w*|für\s+allergiker\s+geeignet|suitable\s+for\s+allergy\s+sufferers)\b",
    ),
)

BULLET_PROHIBITED_PATTERNS = {
    "US": (
        ("environmental claim", r"\b(?:eco[\s-]?friendly|environmentally[\s-]?friendly)\b"),
        ("antibacterial claim", r"\b(?:anti[\s-]?bacterial|anti[\s-]?microbial)\b"),
        ("bamboo wording", r"\bbamboo\b"),
        ("soy wording", r"\b(?:soy|soya)\b"),
    ),
    "DE": (
        ("environmental claim", r"\bumweltfreundlich\w*\b"),
        ("antibacterial claim", r"\b(?:antibakteriell\w*|antimikrobiell\w*)\b"),
        ("bamboo wording", r"\bbambus\w*\b"),
        ("soy wording", r"\bsoja\w*\b"),
    ),
}

TITLE_STOP = {
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or",
    "the", "to", "with",
    "am", "an", "auf", "aus", "bei", "bis", "der", "die", "das", "den", "dem",
    "des", "ein", "eine", "einer", "einem", "einen", "für", "im", "mit", "und",
    "oder", "von", "zu", "zum", "zur",
}

STYLE_STOP = TITLE_STOP | {
    "this", "that", "these", "those", "is", "are", "has", "have", "from",
    "in", "on", "at", "by", "as", "it", "its", "not", "your", "you",
    "dieser", "diese", "dieses", "ist", "sind", "hat", "haben", "aus", "im",
    "am", "an", "auf", "nicht", "ihr", "ihre",
}

FIELD_ALIASES = {
    "title": "Title",
    "item highlights": "Product Highlights",
    "product highlights": "Product Highlights",
    "highlights": "Product Highlights",
    "bullet 1": "Bullet 1",
    "bullet 2": "Bullet 2",
    "bullet 3": "Bullet 3",
    "bullet 4": "Bullet 4",
    "bullet 5": "Bullet 5",
    "description": "Description",
    "features": "Description",
    "specifications": "Description",
    "package includes": "Description",
    "care": "Description",
    "produktmerkmale": "Description",
    "spezifikationen": "Description",
    "lieferumfang": "Description",
    "pflege": "Description",
}

NEGATIVE_SOURCE_RE = re.compile(
    r"\b(?:not|no|without|do\s+not|does\s+not|cannot|non[\s-]|nicht|kein\w*|ohne)\b",
    re.IGNORECASE,
)
NEGATIVE_ZH_RE = re.compile(r"(?:不|非|无|未|勿|没|禁|不能|不可|仅限|只适用)")
# Idiomatic Chinese renders specific compound negations with 防/免 rather than
# a standalone negative (non-slip -> 防滑, no-drill -> 免打孔, ohne Werkzeug -> 免工具).
# Pair each source form with its exact Chinese counterpart so unrelated 防/免
# words (such as 免费 or 防刮) cannot mask an untranslated negation.
IDIOMATIC_NEGATION_PAIRS = (
    (r"\b(?:non|no)[\s-]slip\b", r"防滑"),
    (r"\b(?:non|no)[\s-]skid\b", r"防滑"),
    (r"\b(?:non|no)[\s-]spill\b", r"(?:防漏|防溢|防洒)"),
    (r"\b(?:non|no)[\s-]drip\b", r"(?:防滴|防漏)"),
    (r"\b(?:non|no)[\s-]leak\b", r"防漏"),
    (r"\b(?:non|no)[\s-]scratch\b", r"(?:防刮|防划)"),
    (r"\b(?:non|no)[\s-]rust\b", r"防锈"),
    (r"\b(?:non|no)[\s-]fog\b", r"防雾"),
    (r"\b(?:non|no)[\s-]glare\b", r"防眩"),
    (r"\b(?:non|no)[\s-]static\b", r"防静电"),
    (r"\b(?:non|no)[\s-]fade\b", r"防褪色"),
    (r"\b(?:non|no)[\s-](?:kink|tangle)\b", r"(?:防缠绕|防打结)"),
    (r"\b(?:no[\s-]drill\w*|without\s+drill\w*|ohne\s+bohren)\b", r"免打孔"),
    (r"\b(?:no[\s-]punch\w*|without\s+punch\w*)\b", r"免打孔"),
    (r"\b(?:no[\s-]tools?|without\s+(?:any\s+)?tools?|ohne\s+(?:zusätzliches\s+)?werkzeug\w*)\b", r"免工具"),
    (r"\b(?:no[\s-]assembly|without\s+assembly|ohne\s+montage)\b", r"免安装"),
    (r"\b(?:no[\s-]nail\w*|without\s+nails?|ohne\s+n(?:ä|a)gel)\b", r"免钉"),
    (r"\b(?:no[\s-]glue|without\s+glue|ohne\s+kleb\w*)\b", r"免胶"),
    (r"\b(?:no[\s-]rinse|without\s+rinsing|ohne\s+ausspülen)\b", r"(?:免洗|免冲洗)"),
    (r"\b(?:no[\s-]wash|without\s+washing)\b", r"免洗"),
    (r"\b(?:no[\s-]iron\w*|without\s+ironing|ohne\s+bügeln)\b", r"(?:免熨烫|免烫)"),
)
NUMBER_RE = re.compile(r"(?<![A-Za-z0-9.,])\d+(?:,\d{3})*(?:[.,]\d+)?(?![A-Za-z0-9.,])")
UNIT_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(\d+(?:,\d{3})*(?:[.,]\d+)?)\s*-?\s*(?:°\s*)?"
    r"(mm|cm|km|kg|mg|ml|cl|oz|lbs?|in|ft|µm|μm|m|g|l|c|f)"
    r"(?![A-Za-zµμ])",
    re.IGNORECASE,
)
IN_PREPOSITION_FOLLOW_RE = re.compile(
    r"^(?:\s*-\s*(?:\d+|one\b)|"
    r"\s+(?:\d+|one|a|an|the|each|every|total|stock|"
    r"seconds?|minutes?|hours?|days?|"
    r"einem|einer|ein|den|dem|der|die|das|deutschland|europa)\b)",
    re.IGNORECASE,
)
WIRELESS_G_RE = re.compile(r"^(?:2\.4|5\.8|[23456])G$")
UNIT_ALIASES = {
    "um": ("µm", "μm", "微米"), "mm": ("mm", "毫米"),
    "cm": ("cm", "厘米"), "m": ("m", "米"), "km": ("km", "千米", "公里"),
    "mg": ("mg", "毫克"), "g": ("g", "克"), "kg": ("kg", "千克", "公斤"),
    "ml": ("ml", "毫升"), "cl": ("cl", "厘升"), "l": ("l", "升"),
    "oz": ("oz", "盎司"), "lb": ("lb", "lbs", "磅"),
    "in": ("in", "英寸"), "ft": ("ft", "英尺"),
    "c": ("c", "°c", "℃", "摄氏"), "f": ("f", "°f", "℉", "华氏"),
}
UNIT_CANONICAL = {"µm": "um", "μm": "um", "lbs": "lb"}
TRANSLATED_UNIT_ALIASES = {
    "um": ("µm", "μm", "微米"),
    "mm": ("mm", "毫米"),
    "cm": ("cm", "厘米"),
    "m": ("m", "米"),
    "km": ("km", "千米", "公里"),
    "mg": ("mg", "毫克"),
    "g": ("g", "克"),
    "kg": ("kg", "千克", "公斤"),
    "ml": ("ml", "毫升"),
    "cl": ("cl", "厘升"),
    "l": ("l", "升"),
    "oz": ("oz", "盎司"),
    "lb": ("lb", "lbs", "磅"),
    "in": ("in", "英寸"),
    "ft": ("ft", "英尺"),
    "c": ("c", "°c", "℃", "摄氏"),
    "f": ("f", "°f", "℉", "华氏"),
}
TRANSLATED_UNIT_PATTERN = "|".join(
    sorted(
        {
            alias
            for aliases in TRANSLATED_UNIT_ALIASES.values()
            for alias in aliases
        },
        key=len,
        reverse=True,
    )
)
ZH_NUMERAL_CHARS = "零一二两三四五六七八九十百千万亿〇"
NUMBER_NEIGHBORS = rf"\d{ZH_NUMERAL_CHARS}"
# Do not accept a single digit from a compound number, decimal or fraction.
ZH_NUMBER_LEFT = rf"(?<![{NUMBER_NEIGHBORS}点])(?<![{NUMBER_NEIGHBORS}]分之)"
ZH_NUMBER_RIGHT = rf"(?![{NUMBER_NEIGHBORS}点]|分之[{NUMBER_NEIGHBORS}])"
TRANSLATED_MEASUREMENT_RE = re.compile(
    ZH_NUMBER_LEFT
    + rf"(?P<number>\d+(?:,\d{{3}})*(?:[.,]\d+)?|[零一二两三四五六七八九十])"
    + ZH_NUMBER_RIGHT + r"\s*-?\s*(?:°\s*)?"
    rf"(?P<unit>{TRANSLATED_UNIT_PATTERN})(?![A-Za-zµμ])",
    re.IGNORECASE,
)
ZH_SHORT_UNIT_PATTERNS = {
    "m": r"(?<![毫厘千])米",
    "g": r"(?<![毫千])克",
    "l": r"(?<![毫厘])升",
}

ZH_DIGITS = {
    "0": "零", "1": "一", "2": "二", "3": "三", "4": "四",
    "5": "五", "6": "六", "7": "七", "8": "八", "9": "九",
}

COUNT_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "ein": 1, "eine": 1, "einen": 1, "einem": 1, "einer": 1, "eines": 1,
    "eins": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "funf": 5,
    "sechs": 6, "sieben": 7, "acht": 8, "neun": 9, "zehn": 10,
}
ZH_COUNT_WORDS = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


@dataclass
class Result:
    level: str
    code: str
    message: str
    location: str = ""
    claim_id: str = ""


def tokens(text: str) -> list[str]:
    return kwfreq.tokens(text)


def phrase_count(text: str, phrase: str) -> int:
    return kwfreq.phrase_count(text, phrase)


def keyword_phrases(item: dict[str, object]) -> list[str]:
    values = [str(item.get("phrase", "")).strip()]
    variants = item.get("variants", [])
    if isinstance(variants, list):
        values.extend(str(value).strip() for value in variants)
    result: list[str] = []
    seen: set[tuple[str, ...]] = set()
    for value in values:
        key = tuple(kwfreq.tokens(value))
        if value and key and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def keyword_count(text: str, item: dict[str, object]) -> int:
    return kwfreq.alternative_phrase_count(text, keyword_phrases(item))


def keyword_override_reason_valid(reason: str) -> bool:
    return len(reason.strip()) >= 24 and not INVALID_KEYWORD_OVERRIDE_RE.search(reason)


def chinese_phrase_found(text: str, phrase: str) -> bool:
    """Keep CJK wording intact without borrowing part of an adjacent number."""
    normalized_text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))
    normalized_phrase = re.sub(r"\s+", "", unicodedata.normalize("NFKC", phrase))
    if not normalized_phrase:
        return False
    prefix = suffix = ""
    if normalized_phrase[0].isdigit() or normalized_phrase[0] in ZH_NUMERAL_CHARS:
        prefix = ZH_NUMBER_LEFT + r"(?<!\d[.,])"
    if normalized_phrase[-1].isdigit() or normalized_phrase[-1] in ZH_NUMERAL_CHARS:
        suffix = ZH_NUMBER_RIGHT + r"(?![.,]\d)"
    if not prefix and not suffix:
        return normalized_phrase in normalized_text
    return bool(re.search(prefix + re.escape(normalized_phrase) + suffix, normalized_text))


def phrase_found(text: str, phrase: str, match_mode: str) -> bool:
    # CJK phrases must retain their characters.  The generic keyword tokenizer
    # intentionally extracts Latin/digit tokens, so using it for a Chinese
    # pending-term guard can reduce ``2个滤篮`` to just ``2`` and match an
    # unrelated numbered tip.
    if re.search(r"[\u3400-\u9fff]", phrase):
        return chinese_phrase_found(text, phrase)
    if match_mode == "substring":
        return phrase.casefold() in text.casefold()
    if match_mode == "phrase":
        return (
            phrase_count(text, phrase) > 0
            if tokens(phrase)
            else phrase.casefold() in text.casefold()
        )
    raise ValueError(f"unsupported match mode: {match_mode}")



def strict_phrase_found(text: str, phrase: str) -> bool:
    """Match a required visible phrase without crossing punctuation."""
    if not phrase.strip():
        return False
    if re.search(r"[\u3400-\u9fff]", phrase):
        return chinese_phrase_found(text, phrase)
    normalized_text = JOINED_UNIT_RE.sub(" ", text or "")
    # Share complete numeric/model tokens with keyword counting, including M2.5.
    parts = kwfreq.tokens(phrase)
    if not parts:
        return phrase.casefold() in (text or "").casefold()
    prefix = rf"(?<![{kwfreq.TOKEN_CLASS}])"
    suffix = rf"(?![{kwfreq.TOKEN_CLASS}])"
    if parts[0][0].isdigit():
        prefix += r"(?<!\d[.,])"
    if parts[-1][-1].isdigit():
        suffix += r"(?![.,]\d)"
    pattern = prefix + r"[\s-]+".join(re.escape(part) for part in parts) + suffix
    return bool(re.search(pattern, normalized_text, re.IGNORECASE))

def string_list(value: object) -> list[str] | None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return None
    return [item.strip() for item in value if item.strip()]


def term_positions(text: str, term: str) -> list[int]:
    haystack = tokens(text)
    needle = tokens(term)
    if not needle or len(needle) > len(haystack):
        return []
    width = len(needle)
    return [
        index for index in range(len(haystack) - width + 1)
        if haystack[index:index + width] == needle
    ]


def _phrase_spans(text: str, phrase: str) -> list[tuple[int, int]]:
    """Return token or character spans for exact field-to-claim coverage."""
    if re.search(r"[\u3400-\u9fff]", phrase):
        haystack = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()
        needle = re.sub(r"\s+", "", unicodedata.normalize("NFKC", phrase)).casefold()
        if not needle:
            return []
        spans: list[tuple[int, int]] = []
        start = 0
        while True:
            position = haystack.find(needle, start)
            if position < 0:
                return spans
            spans.append((position, position + len(needle)))
            start = position + 1
    haystack = tokens(text)
    needle = tokens(phrase)
    if not needle:
        return []
    width = len(needle)
    return [
        (index, index + width)
        for index in range(len(haystack) - width + 1)
        if haystack[index:index + width] == needle
    ]


def _risk_claim_covered(
    field_text: str, risk_match: re.Match[str], confirmed_text: str
) -> bool:
    """Require a risk match to lie inside one complete confirmed mapping."""
    matched_text = risk_match.group(0)
    # Use the mapping's coordinate system, including Latin risks inside Chinese.
    chinese_mapping = bool(re.search(r"[\u3400-\u9fff]", confirmed_text))
    if re.search(r"[\u3400-\u9fff]", matched_text) and not chinese_mapping:
        return False
    if chinese_mapping:
        normalized_prefix = re.sub(r"\s+", "", unicodedata.normalize("NFKC", field_text[:risk_match.start()])).casefold()
        normalized_match = re.sub(r"\s+", "", unicodedata.normalize("NFKC", matched_text)).casefold()
        risk_start = len(normalized_prefix)
        risk_end = risk_start + len(normalized_match)
        confirmed_spans = _phrase_spans(field_text, confirmed_text)
    else:
        # Keep original offsets while splitting joined units exactly as tokens().
        token_spans: list[tuple[int, int]] = []
        unit_boundaries = [split.start() for split in JOINED_UNIT_RE.finditer(field_text)]
        for token_match in TOKEN_RE.finditer(field_text):
            boundaries = [token_match.start()]
            boundaries.extend(
                position for position in unit_boundaries
                if token_match.start() < position < token_match.end()
            )
            boundaries.append(token_match.end())
            token_spans.extend(zip(boundaries, boundaries[1:]))
        risk_tokens = [
            index for index, (start, end) in enumerate(token_spans)
            if end > risk_match.start() and start < risk_match.end()
        ]
        if not risk_tokens:
            return False
        risk_start, risk_end = risk_tokens[0], risk_tokens[-1] + 1
        confirmed_spans = _phrase_spans(field_text, confirmed_text)
    return any(
        confirmed_start <= risk_start and risk_end <= confirmed_end
        for confirmed_start, confirmed_end in confirmed_spans
    )


def latin_counts_near_terms(
    text: str, terms: list[str], component_terms: list[str] | None = None,
) -> list[int]:
    found: list[int] = []
    terms = [UNIT_RE.sub(" ", term).strip() for term in terms]
    component_terms = [UNIT_RE.sub(" ", term).strip() for term in (component_terms or terms)]
    # A quantity in a preceding clause belongs to that clause's component.
    for segment in text.replace(";", "\n").splitlines():
        # Strip leading numbered-list markers ("1. ...") so Description items
        # are not misread as component quantities.
        segment = re.sub(r"^\s*\d+\.\s+", "", segment)
        # Ignore sizes only for quantity extraction; original copy still goes
        # through measurement and mapping checks. Never borrow another
        # component's quantity after removing an intervening size.
        segment = UNIT_RE.sub(" ", segment)
        haystack = tokens(segment)
        component_ends = [
            start + len(tokens(term))
            for term in component_terms
            for start in term_positions(segment, term)
        ]
        for term in terms:
            for start in term_positions(segment, term):
                window_start = max(0, start - 4, *(end for end in component_ends if end <= start))
                for index in range(start - 1, window_start - 1, -1):
                    token = haystack[index]
                    multiplier = re.fullmatch(r"(\d+)x", token)
                    if token.isdigit():
                        found.append(int(token))
                        break
                    if multiplier:
                        found.append(int(multiplier.group(1)))
                        break
                    if token in COUNT_WORDS:
                        found.append(COUNT_WORDS[token])
                        break
    return found


def chinese_counts_near_terms(text: str, terms: list[str]) -> list[int | None]:
    found: list[int | None] = []
    terms = [TRANSLATED_MEASUREMENT_RE.sub(" ", term).strip() for term in terms]
    for segment in text.splitlines():
        segment = TRANSLATED_MEASUREMENT_RE.sub(" ", segment)
        for term in terms:
            if not term:
                continue
            escaped = re.escape(term)
            pattern = (
                ZH_NUMBER_LEFT
                + rf"(?P<count>\d+(?:[.,]\d+)?|[{ZH_NUMERAL_CHARS}]+"
                rf"(?:点[{NUMBER_NEIGHBORS}]+|分之[{NUMBER_NEIGHBORS}]+)?)"
                rf"\s*(?:个|只|件|套|枚|组|对)?\s*{escaped}"
            )
            for match in re.finditer(pattern, segment, re.IGNORECASE):
                raw = match.group("count")
                # None preserves an explicit unsupported quantity for both gates.
                found.append(int(raw) if raw.isdigit() else ZH_COUNT_WORDS.get(raw))
    return found


def package_absence_covered(text: str, expected: str, terms: list[str], language: str) -> bool:
    """Accept a mapped direct absence clause, without borrowing a negation."""
    def normalize(value: str) -> str:
        if language == "ZH":
            return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()
        return " ".join(tokens(value))

    names = "|".join(re.escape(normalize(term)) for term in terms)
    if language == "ZH":
        pattern = rf"(?:不含|不包含|未包含)(?:{names})"
    elif language == "DE":
        pattern = rf"(?:(?:der|die|das) )?(?:{names})(?: ist| sind)? nicht enthalten"
    else:
        pattern = rf"(?:the )?(?:{names})(?: is| are)? not included"
    mapped = normalize(expected)
    if not re.fullmatch(pattern, mapped):
        return False
    # Keep decimal/model punctuation intact, and require every mention to be
    # its own complete mapped clause. A second positive mention cannot hide
    # inside the same field or a longer, ambiguous negative sentence.
    clauses = re.split(r"[;:\n。；：，!?！？]|(?<!\d)[.,]|[.,](?!\d)", text)
    mentions = [
        clause for clause in clauses
        if any(phrase_found(clause, term, "phrase") for term in terms)
    ]
    return bool(mentions) and all(normalize(clause) == mapped for clause in mentions)


def validate_package_components(
    results: list[Result], data: dict[str, object], fields: dict[str, str],
    translated_fields: dict[str, str],
) -> None:
    listing_text = "\n".join(fields.values())
    raw_components = data.get("package_components", [])
    if not isinstance(raw_components, list):
        add(results, "FAIL", "PACKAGE_SCHEMA", "package_components must be a list")
        return
    component_terms = [
        term
        for item in raw_components if isinstance(item, dict)
        for key in ("source_singular", "source_plural")
        for term in (string_list(item.get(key, [])) or [])
    ]
    marketplace = str(data.get("marketplace", "")).strip().upper()
    disclosure_pattern = PACKAGE_DISCLOSURE_PATTERNS.get(marketplace)
    if disclosure_pattern and disclosure_pattern.search(listing_text) and not raw_components:
        add(
            results,
            "FAIL",
            "PACKAGE_COMPONENTS_REQUIRED",
            "package-content wording requires explicit package_components",
        )
    for index, raw in enumerate(raw_components, start=1):
        if not isinstance(raw, dict):
            add(results, "FAIL", "PACKAGE_SCHEMA", f"package component {index} must be an object")
            continue
        component = str(raw.get("component", "")).strip() or f"component {index}"
        singular = string_list(raw.get("source_singular", []))
        plural = string_list(raw.get("source_plural", []))
        translated_terms = string_list(raw.get("translation_terms", []))
        if singular is None or plural is None or translated_terms is None or not singular or not translated_terms:
            add(
                results, "FAIL", "PACKAGE_SCHEMA",
                f"{component}: source_singular and translation_terms must be non-empty lists; source_plural must be a list",
            )
            continue
        present = raw.get("present")
        count = raw.get("count")
        if not isinstance(present, bool):
            add(results, "FAIL", "PACKAGE_SCHEMA", f"{component}: present must be boolean")
            continue
        if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 1):
            add(results, "FAIL", "PACKAGE_SCHEMA", f"{component}: count must be a positive integer or null")
            continue

        source_map = raw.get("listing_matches", {})
        translation_map = raw.get("translation_matches", {})
        if not isinstance(source_map, dict) or not isinstance(translation_map, dict):
            add(
                results, "FAIL", "PACKAGE_SCHEMA",
                f"{component}: listing_matches and translation_matches must be objects",
            )
            continue

        normalized_source_map: dict[str, str] = {}
        normalized_translation_map: dict[str, str] = {}
        for raw_location, raw_text in source_map.items():
            location = resolve_location(str(raw_location))
            expected = str(raw_text).strip()
            if not location or location not in fields or not expected:
                add(results, "FAIL", "PACKAGE_LOCATION", f"{component}: invalid source location match {raw_location!r}")
                continue
            normalized_source_map[location] = expected
            if not phrase_found(fields[location], expected, "phrase"):
                add(results, "FAIL", "PACKAGE_LOCATION", f"{component}: {expected!r} is absent from {location}", location=location)
            if not any(phrase_found(expected, term, "phrase") for term in [*singular, *plural]):
                add(results, "FAIL", "PACKAGE_LOCATION", f"{component}: source match does not name the component", location=location)
        for raw_location, raw_text in translation_map.items():
            location = resolve_location(str(raw_location))
            expected = str(raw_text).strip()
            if not location or location not in translated_fields or not expected:
                add(results, "FAIL", "PACKAGE_TRANSLATION", f"{component}: invalid translation location match {raw_location!r}")
                continue
            normalized_translation_map[location] = expected
            if not phrase_found(translated_fields[location], expected, "phrase"):
                add(results, "FAIL", "PACKAGE_TRANSLATION", f"{component}: {expected!r} is absent from translated {location}", location=location)
            if not any(phrase_found(expected, term, "phrase") for term in translated_terms):
                add(results, "FAIL", "PACKAGE_TRANSLATION", f"{component}: translation match does not name the component", location=location)

        if set(normalized_source_map) != set(normalized_translation_map):
            add(
                results, "FAIL", "PACKAGE_TRANSLATION",
                f"{component}: source and translation component locations differ",
            )

        source_terms = [*singular, *plural]
        source_component_locations = {
            location for location, text in fields.items()
            if any(phrase_found(text, term, "phrase") for term in source_terms)
        }
        translation_component_locations = {
            location for location, text in translated_fields.items()
            if any(phrase_found(text, term, "phrase") for term in translated_terms)
        }
        source_found = bool(source_component_locations)
        translation_found = bool(translation_component_locations)
        if not present:
            if count is not None:
                add(results, "FAIL", "PACKAGE_PRESENCE", f"{component}: absent component must use count null")
            for locations, matches, texts, terms, language in (
                (source_component_locations, normalized_source_map, fields, source_terms, marketplace),
                (translation_component_locations, normalized_translation_map, translated_fields, translated_terms, "ZH"),
            ):
                if locations != set(matches):
                    add(results, "FAIL", "PACKAGE_PRESENCE", f"{component}: every absence statement needs a complete location match")
                for location in locations:
                    if not package_absence_covered(texts[location], matches.get(location, ""), terms, language):
                        add(results, "FAIL", "PACKAGE_PRESENCE", f"{component}: component mention is not a complete mapped absence clause", location=location)
            continue
        if not normalized_source_map or not normalized_translation_map:
            add(
                results, "FAIL", "PACKAGE_LOCATION",
                f"{component}: present component requires source and translation location matches",
            )
        if not source_found:
            add(results, "FAIL", "PACKAGE_PRESENCE", f"{component}: present component is absent from source listing")
        if not translation_found:
            add(results, "FAIL", "PACKAGE_TRANSLATION", f"{component}: component is absent from Chinese translation")

        if source_component_locations != set(normalized_source_map):
            add(
                results, "FAIL", "PACKAGE_LOCATION",
                f"{component}: every source field containing the component needs a listing match",
            )
        if translation_component_locations != set(normalized_translation_map):
            add(
                results, "FAIL", "PACKAGE_TRANSLATION",
                f"{component}: every translated field containing the component needs a translation match",
            )

        mapped_source_text = "\n".join(
            fields[location] for location in source_component_locations
        )
        mapped_translation_text = "\n".join(
            translated_fields[location] for location in translation_component_locations
        )
        source_counts = latin_counts_near_terms(mapped_source_text, source_terms, component_terms)
        translated_counts = chinese_counts_near_terms(mapped_translation_text, translated_terms)
        plural_found = any(phrase_found(mapped_source_text, term, "phrase") for term in plural)
        if count is None:
            if source_counts or translated_counts or plural_found:
                add(
                    results, "FAIL", "PACKAGE_COUNT_UNKNOWN",
                    f"{component}: unknown quantity must not use a number or source-language plural",
                )
            continue
        if count not in source_counts or any(value != count for value in source_counts):
            add(results, "FAIL", "PACKAGE_COUNT", f"{component}: source copy does not express only the confirmed count {count}")
        if count not in translated_counts or any(value != count for value in translated_counts):
            add(results, "FAIL", "PACKAGE_COUNT_TRANSLATION", f"{component}: Chinese translation does not express only the confirmed count {count}")


def resolve_location(name: str) -> str | None:
    return FIELD_ALIASES.get(name.strip().casefold())


def compatible_target_ok(marketplace: str, text: str, target: str) -> bool:
    value = text.casefold()
    escaped = re.escape(target.casefold())
    if not re.search(escaped, value):
        return True
    if marketplace == "US" and re.search(r"\b(?:fit|fits)\s+for\b", value):
        return False
    relation = (
        r"(?:compatible\s+with|for|fits)"
        if marketplace == "US"
        else r"(?:kompatibel\s+mit|passend\s+für|für)"
    )
    for match in re.finditer(escaped, value):
        clause_start = max(
            value.rfind(";", 0, match.start()), value.rfind(".", 0, match.start()),
            value.rfind(":", 0, match.start()), value.rfind("\n", 0, match.start()),
        ) + 1
        prefix = value[clause_start:match.end()]
        if not re.search(rf"\b{relation}\s+[^.;:\n]{{0,80}}{escaped}", prefix):
            return False
    return True


def normalize_numeric_token(value: str, marketplace: str = "US") -> str:
    if marketplace != "DE" and re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", value):
        return value.replace(",", "")
    return value.replace(",", ".")


def _non_measurement(
    text: str, match: re.Match[str], number_group: int | str, unit_group: int | str,
) -> bool:
    """Skip ``in`` used as a preposition (3-in-1, 2026 in) and wireless ``5G``."""
    raw_num = match.group(number_group)
    raw_unit = match.group(unit_group)
    if raw_unit.casefold() == "in":
        if IN_PREPOSITION_FOLLOW_RE.match(text[match.end():]):
            return True
        gap = text[match.end(number_group):match.start(unit_group)]
        if gap and gap.isspace() and re.fullmatch(r"(?:19|20)\d{2}", raw_num):
            return True
    return raw_unit == "G" and bool(WIRELESS_G_RE.fullmatch(match.group(0)))


def extract_measurement_matches(
    text: str, marketplace: str = "US"
) -> list[re.Match[str]]:
    source = text or ""
    return [
        match for match in UNIT_RE.finditer(source)
        if not _non_measurement(source, match, 1, 2)
    ]


def normalized_numbers(text: str, marketplace: str = "US") -> list[str]:
    # Mask complete number-unit spans before generic extraction so ``8.66in``
    # contributes only ``8.66`` rather than the spurious prefix ``8``.
    masked = list(text or "")
    unit_values: list[str] = []
    for match in extract_measurement_matches(text or "", marketplace):
        unit_values.append(normalize_numeric_token(match.group(1), marketplace))
        for index in range(*match.span()):
            masked[index] = " "
    values = [
        normalize_numeric_token(value, marketplace)
        for value in NUMBER_RE.findall("".join(masked))
    ]
    values.extend(unit_values)
    return values


def canonical_unit(value: str) -> str:
    folded = value.casefold()
    return UNIT_CANONICAL.get(folded, folded)


def translated_unit_present(translated: str, unit: str) -> bool:
    canonical = canonical_unit(unit)
    short_pattern = ZH_SHORT_UNIT_PATTERNS.get(canonical)
    if short_pattern and re.search(short_pattern, translated):
        return True
    for alias in UNIT_ALIASES[canonical]:
        if alias in {"米", "克", "升"}:
            continue
        if re.fullmatch(r"[A-Za-zµμ]+", alias):
            if re.search(
                rf"(?<![A-Za-zµμ]){re.escape(alias)}(?![A-Za-zµμ])",
                translated,
                re.IGNORECASE,
            ):
                return True
        elif alias.casefold() in translated.casefold():
            return True
    return False


def translated_measurement_pairs(text: str) -> list[tuple[str, str]]:
    reverse_units = {
        alias.casefold(): canonical
        for canonical, aliases in TRANSLATED_UNIT_ALIASES.items()
        for alias in aliases
    }
    reverse_digits = {value: key for key, value in ZH_DIGITS.items()}
    reverse_digits["两"] = "2"
    reverse_digits["十"] = "10"
    pairs: list[tuple[str, str]] = []
    for match in TRANSLATED_MEASUREMENT_RE.finditer(text):
        # Mirror the source-side filter so a literal "3-in-1" or "5G" kept in
        # the Chinese copy is not counted as an inch or gram measurement.
        if _non_measurement(text, match, "number", "unit"):
            continue
        raw_number = normalize_numeric_token(match.group("number"), "US")
        number = reverse_digits.get(raw_number, raw_number)
        unit = reverse_units[match.group("unit").casefold()]
        pairs.append((number, unit))
    return pairs


def translated_field_values(
    translations: object, full_listing: bool
) -> dict[str, str]:
    if not isinstance(translations, dict):
        return {}
    result = {
        "Title": str(translations.get("title", "")).strip(),
        "Product Highlights": str(translations.get("highlights", "")).strip(),
    }
    if not full_listing:
        return result
    raw_bullets = translations.get("bullets", [])
    if isinstance(raw_bullets, list):
        result.update({
            f"Bullet {index}": str(value).strip()
            for index, value in enumerate(raw_bullets[:5], start=1)
        })
    result["Description"] = str(translations.get("description", "")).strip()
    return result


def translated_number_present(number: str, translated: str, translated_numbers: list[str]) -> bool:
    normalized = normalize_numeric_token(number, "US")
    if normalized in translated_numbers:
        return True
    if re.fullmatch(r"\d", normalized):
        chinese_digit = "[二两]" if normalized == "2" else ZH_DIGITS[normalized]
        return bool(re.search(
            ZH_NUMBER_LEFT + chinese_digit + ZH_NUMBER_RIGHT,
            translated,
        ))
    if normalized == "10":
        return bool(re.search(ZH_NUMBER_LEFT + "十" + ZH_NUMBER_RIGHT, translated))
    return False


def _mapped_numeric_model(
    field_text: str, phone_match: re.Match[str], mappings: list[str],
) -> bool:
    """Exempt only a labeled, mapped numeric model at this exact occurrence."""
    if not mappings or not phone_match.group(0).isascii() or not phone_match.group(0).isdigit():
        return False
    for model_match in NUMERIC_MODEL_RE.finditer(field_text):
        if model_match.span("number") != phone_match.span():
            continue
        clause_start = max(
            field_text.rfind(";", 0, model_match.start()),
            field_text.rfind("\n", 0, model_match.start()),
        ) + 1
        clause_end = re.search(r"[;\n]", field_text[model_match.end():])
        clause = field_text[clause_start:(
            model_match.end() + clause_end.start() if clause_end else len(field_text)
        )]
        return not MODEL_CONTACT_RE.search(clause) and any(
            _risk_claim_covered(field_text, model_match, mapping)
            for mapping in mappings
        )
    return False


def translation_consistency(
    results: list[Result], field: str, source: str, translated: str,
    compatibility_targets: list[str], marketplace: str = "US",
) -> None:
    if not translated.strip():
        add(results, "FAIL", "TRANSLATION_EMPTY", f"{field} Chinese translation is blank", location=field)
        return
    source_numbers = normalized_numbers(source, marketplace)
    translated_numbers = normalized_numbers(translated, "US")
    for number in source_numbers:
        if not translated_number_present(number, translated, translated_numbers):
            add(results, "FAIL", "TRANSLATION_NUMBER", f"{field} translation is missing number {number}", location=field)
    measurement_matches = extract_measurement_matches(source, marketplace)
    for match in measurement_matches:
        number, unit = match.group(1), match.group(2)
        normalized = normalize_numeric_token(number, marketplace)
        if (
            not translated_number_present(normalized, translated, translated_numbers)
            or not translated_unit_present(translated, unit)
        ):
            add(results, "FAIL", "TRANSLATION_UNIT", f"{field} translation is missing {number} {unit}", location=field)
    source_pairs = [
        (normalize_numeric_token(match.group(1), marketplace), canonical_unit(match.group(2)))
        for match in measurement_matches
    ]
    translated_pairs = translated_measurement_pairs(translated)
    if source_pairs and Counter(source_pairs) != Counter(translated_pairs):
        add(
            results,
            "FAIL",
            "TRANSLATION_UNIT",
            f"{field} translation does not preserve number-unit pairs",
            location=field,
        )
    for target in compatibility_targets:
        if target.casefold() in source.casefold() and target.casefold() not in translated.casefold():
            add(results, "FAIL", "TRANSLATION_TARGET", f"{field} translation is missing compatibility target {target!r}", location=field)
    # "No." inside a numeric model label means "number", not negation.
    polarity_source = NUMERIC_MODEL_RE.sub("", source)
    # "不锈钢" (stainless steel) contains "不" as part of a material noun, not negation.
    polarity_translated = translated.replace("不锈钢", "")
    if NEGATIVE_SOURCE_RE.search(polarity_source) and not NEGATIVE_ZH_RE.search(polarity_translated):
        residual = polarity_source
        for source_pattern, zh_pattern in IDIOMATIC_NEGATION_PAIRS:
            if re.search(zh_pattern, polarity_translated):
                residual = re.sub(source_pattern, " ", residual, flags=re.IGNORECASE)
        if NEGATIVE_SOURCE_RE.search(residual):
            add(results, "FAIL", "TRANSLATION_POLARITY", f"{field} translation lacks a Chinese negative marker", location=field)


def repeated_content_token(text: str) -> tuple[str, int] | None:
    counts: dict[str, int] = {}
    for token in tokens(text):
        if len(token) < 4 or token in STYLE_STOP or token.isdigit():
            continue
        counts[token] = counts.get(token, 0) + 1
    if not counts:
        return None
    token, count = max(counts.items(), key=lambda item: item[1])
    return (token, count) if count >= 3 else None


def content_token_set(text: str) -> set[str]:
    return {
        token for token in tokens(text)
        if token not in STYLE_STOP and (len(token) >= 3 or token.isdigit())
    }


def overlap_ratio(left: str, right: str) -> tuple[float, set[str]]:
    a, b = content_token_set(left), content_token_set(right)
    if not a or not b:
        return 0.0, set()
    shared = a & b
    return len(shared) / min(len(a), len(b)), shared


def parse_target(value: object) -> tuple[int, int]:
    if type(value) is int:
        if value < 0:
            raise ValueError(f"Invalid keyword target: {value!r}")
        return value, value
    raw = str(value).strip()
    if re.fullmatch(r"\d+", raw):
        parsed = int(raw)
        return parsed, parsed
    match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", raw)
    if not match:
        raise ValueError(f"Invalid keyword target: {value!r}")
    lower, upper = int(match.group(1)), int(match.group(2))
    if lower > upper:
        raise ValueError(f"Keyword target lower bound exceeds upper bound: {value!r}")
    return lower, upper


def longest_common_run(left: str, right: str) -> tuple[int, str]:
    a, b = tokens(left), tokens(right)
    if not a or not b:
        return 0, ""
    previous = [0] * (len(b) + 1)
    best_length = 0
    best_end = 0
    for index_a, token_a in enumerate(a, start=1):
        current = [0] * (len(b) + 1)
        for index_b, token_b in enumerate(b, start=1):
            if token_a == token_b:
                current[index_b] = previous[index_b - 1] + 1
                if current[index_b] > best_length:
                    best_length = current[index_b]
                    best_end = index_a
        previous = current
    return best_length, " ".join(a[best_end - best_length:best_end])


def add(
    results: list[Result], level: str, code: str, message: str,
    *, location: str = "", claim_id: str = "",
) -> None:
    results.append(Result(level, code, message, location, claim_id))


def validated_text(
    results: list[Result], data: dict[str, object], key: str, label: str
) -> str:
    value = data.get(key, "")
    if not isinstance(value, str):
        add(results, "FAIL", "FIELD_SCHEMA", f"{label} must be a string")
        return ""
    return value.strip()


def validate(data: dict[str, object]) -> list[Result]:
    results: list[Result] = []
    marketplace = str(data.get("marketplace", "")).upper().strip()
    scope = str(data.get("scope", "full_listing")).lower().strip() or "full_listing"
    if scope not in {"full_listing", "title_highlights"}:
        add(results, "FAIL", "SCOPE", "scope must be full_listing or title_highlights")
        scope = "full_listing"
    full_listing = scope == "full_listing"
    title = validated_text(results, data, "title", "Title")
    highlights = validated_text(
        results, data, "highlights", "Product Highlights"
    )
    description = validated_text(results, data, "description", "Description")
    raw_bullets = data.get("bullets", [])
    bullets: list[str] = []
    if not isinstance(raw_bullets, list):
        add(results, "FAIL", "BULLET_SCHEMA", "bullets must be a list of strings")
    else:
        for index, value in enumerate(raw_bullets, start=1):
            if not isinstance(value, str):
                add(
                    results, "FAIL", "BULLET_SCHEMA",
                    f"Bullet {index} must be a string",
                )
                bullets.append("")
            else:
                bullets.append(value.strip())

    if marketplace not in {"US", "DE"}:
        add(results, "FAIL", "MARKETPLACE", "marketplace must be US or DE")
    if full_listing and len(bullets) != 5:
        add(results, "FAIL", "BULLET_COUNT", f"expected 5 Bullets, got {len(bullets)}")
    if not full_listing:
        if "bullets" in data:
            add(results, "FAIL", "SCOPE_FIELD", "title_highlights must omit Bullets")
        if "description" in data:
            add(results, "FAIL", "SCOPE_FIELD", "title_highlights must omit Description")

    fields = {"Title": title, "Product Highlights": highlights}
    if full_listing:
        fields["Description"] = description
        fields.update({f"Bullet {index}": value for index, value in enumerate(bullets, start=1)})
    for name, value in fields.items():
        if ENCODED_ENTITY_RE.search(value):
            add(results, "FAIL", "ENCODED_ENTITY", f"{name} contains an HTML entity; decode it before final output", location=name)
    raw_delivery_translations = data.get("translations", {})
    translated_fields_for_claims = translated_field_values(
        raw_delivery_translations, full_listing
    )
    required_fields = (
        ("Title", "Product Highlights", "Description")
        if full_listing else ("Title", "Product Highlights")
    )
    for required in required_fields:
        if not fields.get(required, "").strip():
            add(results, "FAIL", "REQUIRED_FIELD", f"{required} must not be blank", location=required)
    for index, bullet in enumerate(bullets if full_listing else [], start=1):
        if not bullet:
            add(results, "FAIL", "REQUIRED_FIELD", f"Bullet {index} must not be blank", location=f"Bullet {index}")
    hard_limits = {
        "Title": 75,
        "Product Highlights": 125,
        "Bullet 1": 255,
        "Bullet 2": 255,
        "Bullet 3": 255,
        "Bullet 4": 255,
        "Bullet 5": 255,
        "Description": DESCRIPTION_LIMIT,
    }
    mobile_preferred_limits = {"Bullet 1": 145, "Bullet 2": 165}
    working_ranges = {
        "Bullet 1": (125, 140),
        "Bullet 2": (140, 160),
        "Bullet 3": (150, 200),
        "Bullet 4": (150, 200),
        "Bullet 5": (150, 200),
    }

    active_limits = (
        hard_limits if full_listing
        else {name: hard_limits[name] for name in ("Title", "Product Highlights")}
    )
    for name, limit in active_limits.items():
        length = len(fields.get(name, ""))
        if length > limit:
            add(results, "FAIL", "CHAR_LIMIT", f"{name} is {length}/{limit} characters", location=name)
        else:
            add(results, "PASS", "CHAR_LIMIT", f"{name} is {length}/{limit} characters")
    if full_listing and len(bullets) >= 2:
        first_two = len(bullets[0]) + len(bullets[1])
        add(
            results,
            "FAIL" if first_two > 320 else "PASS",
            "MOBILE_FIRST_TWO",
            f"Bullet 1 + 2 mobile total is {first_two}/320 characters",
            location="Bullet 1 + 2",
        )
        if not 270 <= first_two <= 300:
            add(
                results,
                "WARN",
                "WORKING_TARGET",
                "Bullet 1 + 2 is outside the 270-300 working target",
                location="Bullet 1 + 2",
            )

    for name, preferred in (mobile_preferred_limits.items() if full_listing else []):
        length = len(fields.get(name, ""))
        if length > preferred:
            add(
                results,
                "WARN",
                "MOBILE_DISTRIBUTION",
                f"{name} is {length}; mobile preferred allocation is {preferred} or less",
                location=name,
            )

    for name, (lower, upper) in (working_ranges.items() if full_listing else []):
        length = len(fields.get(name, ""))
        if length and not lower <= length <= upper:
            add(
                results,
                "WARN",
                "WORKING_TARGET",
                f"{name} is {length}; review against the {lower}-{upper} working target without adding filler",
                location=name,
            )

    first_highlight_alpha = next((char for char in highlights if char.isalpha()), "")
    if first_highlight_alpha and not first_highlight_alpha.isupper():
        add(
            results,
            "WARN",
            "HIGHLIGHTS_CAPITALIZATION",
            "Item Highlights does not start with a capitalized alphabetic word",
            location="Product Highlights",
        )
    # Periods may separate independent highlight clusters. Review only long
    # sentence-like segments; short clusters such as a function followed by a
    # scene or mode are valid and keep the field scannable.
    highlight_segments = [segment.strip() for segment in re.split(r"[.!?]\s+", highlights) if segment.strip()]
    if len(highlight_segments) > 1 and any(len(segment.split()) >= 14 for segment in highlight_segments):
        add(
            results, "WARN", "HIGHLIGHTS_LONG_SENTENCE",
            "Item Highlights contains a long sentence-like segment; keep independent clusters concise",
            location="Product Highlights",
        )

    for index, bullet in enumerate(bullets if full_listing else [], start=1):
        bullet_loc = f"Bullet {index}"
        if len(bullet) < 10:
            add(results, "FAIL", "BULLET_MIN_LENGTH", f"Bullet {index} is {len(bullet)}/10 characters", location=bullet_loc)
        if bullet.endswith(END_PUNCTUATION):
            add(results, "FAIL", "BULLET_END", f"Bullet {index} ends in punctuation", location=bullet_loc)
        if bullet and ":" not in bullet[:40]:
            add(results, "WARN", "READABILITY_LEAD", f"Bullet {index} lacks a short lead phrase", location=bullet_loc)
        first_alpha = next((char for char in bullet if char.isalpha()), "")
        if first_alpha and not first_alpha.isupper():
            add(results, "WARN", "BULLET_CAPITALIZATION", f"Bullet {index} does not start with a capitalized lead", location=bullet_loc)
        if JOINED_BULLET_UNIT_RE.search(bullet):
            add(results, "WARN", "BULLET_UNIT_SPACING", f"Bullet {index} joins a measurement number and unit", location=bullet_loc)
        if STANDALONE_SINGLE_DIGIT_RE.search(bullet):
            wording = "one through nine" if marketplace == "US" else "eins bis neun"
            add(results, "WARN", "BULLET_NUMBER_STYLE", f"Bullet {index} uses a standalone digit; review {wording} wording", location=bullet_loc)
        prohibited_chars = sorted(set(bullet) & PROHIBITED_BULLET_CHARS)
        if prohibited_chars:
            add(
                results, "FAIL", "BULLET_PROHIBITED_CHAR",
                f"Bullet {index} contains prohibited characters: {''.join(prohibited_chars)}",
                location=bullet_loc,
            )
        for label, pattern in BULLET_PROHIBITED_PATTERNS.get(marketplace, ()):
            if re.search(pattern, bullet, re.IGNORECASE):
                add(results, "FAIL", "BULLET_PROHIBITED_CONTENT", f"Bullet {index} contains prohibited {label}", location=bullet_loc)
        repeated = repeated_content_token(bullet)
        if repeated:
            token, count = repeated
            add(results, "WARN", "READABILITY_REPEAT", f"Bullet {index} repeats content token {token!r} {count} times", location=bullet_loc)
        if re.search(r"\btop\s+rack\b", bullet, re.IGNORECASE) and not re.search(r"\bdishwasher\b", bullet, re.IGNORECASE):
            add(results, "WARN", "READABILITY_CONTEXT", f"Bullet {index} says top rack without dishwasher context", location=bullet_loc)
        if re.search(r"\bober\w*\s+korb\b", bullet, re.IGNORECASE) and not re.search(r"\bspülmaschine\w*\b", bullet, re.IGNORECASE):
            add(results, "WARN", "READABILITY_CONTEXT", f"Bullet {index} mentions an upper rack without dishwasher context", location=bullet_loc)

    if full_listing and len(bullets) == 5:
        ratio, shared = overlap_ratio(bullets[0], bullets[4])
        if ratio >= 0.55 and len(shared) >= 4:
            add(
                results,
                "WARN",
                "READABILITY_DUPLICATE",
                "Bullet 5 substantially repeats Bullet 1; replace it with package, care, constraint, or another new buyer answer",
                location="Bullet 5",
            )

    listing_text = "\n".join(fields.values())
    translation_delivery_parts: list[str] = []
    if isinstance(raw_delivery_translations, dict):
        translation_delivery_parts.extend([
            str(raw_delivery_translations.get("title", "")),
            str(raw_delivery_translations.get("highlights", "")),
        ])
        if full_listing:
            raw_delivery_bullets = raw_delivery_translations.get("bullets", [])
            if isinstance(raw_delivery_bullets, list):
                translation_delivery_parts.extend(str(value) for value in raw_delivery_bullets)
            translation_delivery_parts.append(str(raw_delivery_translations.get("description", "")))
    deliverable_text = "\n".join([listing_text, *translation_delivery_parts])
    for pattern in META_LANGUAGE_PATTERNS:
        if re.search(pattern, listing_text, re.IGNORECASE):
            add(results, "FAIL", "META_LANGUAGE", f"listing contains internal audit wording matching {pattern!r}")
    title_prohibited = sorted(set(title) & PROHIBITED_TITLE_CHARS)
    if title_prohibited:
        add(results, "FAIL", "TITLE_CHAR", f"Title contains prohibited characters: {''.join(title_prohibited)}", location="Title")
    if EMOJI_RE.search(listing_text):
        add(results, "FAIL", "EMOJI", "listing fields contain an emoji or symbol from an emoji range")
    if ASIN_RE.search(listing_text):
        add(results, "FAIL", "ASIN", "listing fields contain an ASIN-like token")
    if URL_RE.search(listing_text):
        add(results, "FAIL", "URL", "listing fields contain a URL")
    if EMAIL_RE.search(listing_text):
        add(results, "FAIL", "EMAIL", "listing fields contain an email address")
    for pattern in PROMISE_PATTERNS.get(marketplace, ()):
        if re.search(pattern, listing_text, re.IGNORECASE):
            add(results, "FAIL", "PROMISE_OR_CONTACT", f"listing contains prohibited promise/contact wording matching {pattern!r}")

    title_counts: dict[str, int] = {}
    for token in tokens(title):
        if token not in TITLE_STOP and len(token) >= 2:
            title_counts[token] = title_counts.get(token, 0) + 1
    repeated_title = sorted(token for token, count in title_counts.items() if count > 2)
    if repeated_title:
        add(results, "FAIL", "TITLE_REPETITION", f"Title repeats content words more than twice: {', '.join(repeated_title)}", location="Title")

    bullet_text = "\n".join(bullets)
    keyword_text = (
        "\n".join([title, highlights]) if not full_listing else bullet_text
    )
    keywords = data.get("keywords", [])
    raw_competitors = data.get("competitors", [])
    competitors: list[str] = []
    if not isinstance(raw_competitors, list):
        add(results, "FAIL", "COMPETITOR_SCHEMA", "competitors must be a list")
    else:
        for index, competitor in enumerate(raw_competitors):
            if not isinstance(competitor, str):
                add(
                    results, "FAIL", "COMPETITOR_SCHEMA",
                    f"competitors[{index}] must be a string",
                )
            elif competitor.strip():
                competitors.append(competitor.strip())
    if not isinstance(keywords, list):
        add(results, "FAIL", "KEYWORD_SCHEMA", "keywords must be a list")
    else:
        if not full_listing and len(keywords) > 2:
            add(results, "FAIL", "KEYWORD_SCOPE", "title_highlights accepts at most two locked keywords")
        if competitors and not keywords:
            reason = str(data.get("keyword_audit_reason", "")).strip()
            if reason:
                add(results, "WARN", "KEYWORD_EMPTY", f"competitor data has no keyword audit: {reason}")
            else:
                add(results, "FAIL", "KEYWORD_EMPTY", "competitor data requires a non-empty keyword audit or keyword_audit_reason")
        for item in keywords:
            if not isinstance(item, dict):
                add(results, "FAIL", "KEYWORD_SCHEMA", "keyword entries must be objects")
                continue
            phrase = str(item.get("phrase", "")).strip()
            if not phrase:
                add(results, "FAIL", "KEYWORD_SCHEMA", "keyword phrase is blank")
                continue
            variants = item.get("variants", [])
            if isinstance(variants, str) or not isinstance(variants, list):
                add(results, "FAIL", "KEYWORD_SCHEMA", f"{phrase!r}: variants must be a list")
                continue
            if any(not isinstance(value, str) for value in variants):
                add(results, "FAIL", "KEYWORD_SCHEMA", f"{phrase!r}: variants must contain strings")
                continue
            actual = keyword_count(keyword_text, item)
            reported = item.get("reported_used")
            if reported is not None:
                if type(reported) is not int or reported < 0:
                    add(
                        results, "FAIL", "KEYWORD_SCHEMA",
                        f"{phrase!r}: reported_used must be a non-negative JSON integer",
                    )
                elif actual != reported:
                    add(results, "FAIL", "KEYWORD_REPORTED", f"{phrase!r}: actual {actual}, reported {reported}")
            source = str(item.get("source", "competitor")).strip() or "competitor"
            if not full_listing and source != "user_priority":
                add(
                    results, "FAIL", "KEYWORD_SCOPE",
                    f"{phrase!r}: title_highlights accepts only prefiltered user_priority keywords",
                )
                continue
            eligible_raw = item.get("eligible_locations", [])
            eligible: set[str] = set()
            if eligible_raw:
                if not isinstance(eligible_raw, list):
                    add(results, "FAIL", "KEYWORD_SCHEMA", f"{phrase!r}: eligible_locations must be a list")
                else:
                    for raw_location in eligible_raw:
                        location = resolve_location(str(raw_location))
                        if not location:
                            add(results, "FAIL", "KEYWORD_SCHEMA", f"{phrase!r}: unknown eligible location {raw_location!r}")
                        else:
                            eligible.add(location)
                    placement_fields = (
                        {"Title": title, "Product Highlights": highlights}
                        if not full_listing else
                        {f"Bullet {index}": bullet for index, bullet in enumerate(bullets, start=1)}
                    )
                    for location, field_text in placement_fields.items():
                        if keyword_count(field_text, item) and location not in eligible:
                            add(
                                results, "FAIL", "KEYWORD_PLACEMENT",
                                f"{phrase!r} appears in {location}, outside its planned related locations",
                                location=location,
                            )

            # Required locations are an explicit user or workflow constraint.
            # They must be enforced even when eligible_locations is omitted;
            # the latter controls placement restrictions, not activation.
            required_raw = item.get("required_locations", [])
            required: list[str] = []
            if required_raw:
                if not isinstance(required_raw, list) or any(not isinstance(value, str) for value in required_raw):
                    add(results, "FAIL", "KEYWORD_SCHEMA", f"{phrase!r}: required_locations must be a list of strings")
                else:
                    for raw_location in required_raw:
                        location = resolve_location(raw_location)
                        if not location:
                            add(results, "FAIL", "KEYWORD_SCHEMA", f"{phrase!r}: unknown required location {raw_location!r}")
                        else:
                            required.append(location)
                    for location in dict.fromkeys(required):
                        field_text = fields.get(location, "")
                        if not strict_phrase_found(field_text, phrase) and not any(
                            strict_phrase_found(field_text, variant)
                            for variant in item.get("variants", []) if isinstance(variant, str)
                        ):
                            add(
                                results, "FAIL", "KEYWORD_REQUIRED_LOCATION",
                                f"{phrase!r} is missing from required {location}",
                                location=location,
                            )
            if source == "user_priority":
                highlight_used = keyword_count(highlights, item)
                if not full_listing:
                    if not eligible:
                        add(
                            results, "FAIL", "KEYWORD_PRIORITY",
                            f"{phrase!r}: title_highlights requires an explicit Title or Item Highlights location",
                        )
                    elif actual < 1:
                        add(
                            results, "FAIL", "KEYWORD_PRIORITY",
                            f"{phrase!r}: locked keyword is absent from Title and Item Highlights",
                        )
                    elif actual > 2:
                        add(
                            results, "FAIL", "KEYWORD_PRIORITY",
                            f"{phrase!r}: locked keyword appears {actual} times across Title and Item Highlights; maximum is 2",
                        )
                    else:
                        add(
                            results, "PASS", "KEYWORD_PRIORITY",
                            f"{phrase!r}: locked keyword has a valid Title/Item Highlights placement",
                        )
                else:
                    highlight_allowed = (
                        "Product Highlights" in eligible
                        and highlight_used > 0
                    )
                    if actual < 1 and not highlight_allowed:
                        add(
                            results, "FAIL", "KEYWORD_PRIORITY",
                            f"{phrase!r}: user priority keyword must appear in a Bullet; "
                            "an Item-Highlights-only placement requires an explicit relevant location",
                        )
                    else:
                        add(
                            results, "PASS", "KEYWORD_PRIORITY",
                            f"{phrase!r}: user priority keyword has a valid Bullet or Item Highlights placement",
                        )
            elif "target" in item:
                try:
                    lower, upper = parse_target(item["target"])
                except ValueError as error:
                    add(results, "FAIL", "KEYWORD_TARGET", str(error))
                    continue
                if actual < lower and lower - actual == 1 and actual >= 1:
                    add(
                        results,
                        "WARN",
                        "KEYWORD_BELOW_PREFERRED",
                        f"{phrase!r}: actual {actual} is one below preferred {lower}-{upper}; "
                        "keep only when another use would make a related field repetitive or unnatural",
                    )
                elif not lower <= actual <= upper:
                    reason = str(item.get("override_reason", "")).strip()
                    if reason and keyword_override_reason_valid(reason):
                        add(
                            results,
                            "WARN",
                            "KEYWORD_OVERRIDE",
                            f"{phrase!r}: actual {actual}, target {lower}-{upper}; override: {reason}",
                        )
                    elif reason:
                        add(
                            results,
                            "FAIL",
                            "KEYWORD_OVERRIDE",
                            f"{phrase!r}: override reason is too vague or uses SEO/ranking justification",
                        )
                    elif actual > upper:
                        warning_cap = 6 if len(tokens(phrase)) == 1 else 3
                        if actual <= warning_cap:
                            add(
                                results,
                                "WARN",
                                "KEYWORD_UPPER",
                                f"{phrase!r}: actual {actual} is above target {lower}-{upper} "
                                f"but not above the {warning_cap}-use cap",
                            )
                        else:
                            add(
                                results,
                                "FAIL",
                                "KEYWORD_TARGET",
                                f"{phrase!r}: actual {actual}, target {lower}-{upper} without override reason",
                            )
                    elif actual < lower:
                        add(
                            results,
                            "FAIL",
                            "KEYWORD_TARGET",
                            f"{phrase!r}: actual {actual}, target {lower}-{upper} without override reason",
                        )
            count_scope = "Title/Highlights" if not full_listing else "final-Bullet"
            add(results, "PASS", "KEYWORD_ACTUAL", f"{phrase!r}: actual {count_scope} count {actual}")

    blocked_terms = data.get("blocked_terms", [])
    if isinstance(blocked_terms, list):
        for item in blocked_terms:
            if isinstance(item, str):
                term, match_mode, reason = item.strip(), "phrase", ""
            elif isinstance(item, dict):
                term = str(item.get("term", "")).strip()
                match_mode = str(item.get("match", "phrase")).strip().lower()
                reason = str(item.get("reason", "")).strip()
            else:
                add(results, "FAIL", "BLOCKED_SCHEMA", "blocked-term entries must be strings or objects")
                continue
            if not term:
                continue
            try:
                found = phrase_found(deliverable_text, term, match_mode)
            except ValueError:
                add(results, "FAIL", "BLOCKED_SCHEMA", f"unsupported blocked-term match mode: {match_mode}")
                continue
            if found:
                suffix = f"; {reason}" if reason else ""
                add(results, "FAIL", "BLOCKED_TERM", f"blocked term {term!r} appears in deliverable copy{suffix}")
    else:
        add(results, "FAIL", "BLOCKED_SCHEMA", "blocked_terms must be a list")

    pending_blocked_terms = data.get("pending_blocked_terms", [])
    if not isinstance(pending_blocked_terms, list):
        add(results, "FAIL", "PENDING_SCHEMA", "pending_blocked_terms must be a list")
    else:
        for item in pending_blocked_terms:
            if not isinstance(item, dict):
                add(results, "FAIL", "PENDING_SCHEMA", "pending blocked entries must be objects")
                continue
            term = str(item.get("term", "")).strip()
            match_mode = str(item.get("match", "phrase")).strip().lower()
            try:
                found = bool(term) and phrase_found(deliverable_text, term, match_mode)
            except ValueError:
                add(results, "FAIL", "PENDING_SCHEMA", f"unsupported pending match mode: {match_mode}")
                continue
            if found:
                add(results, "FAIL", "PENDING_LEAK", f"pending wording {term!r} appears in deliverable copy")

    competitor_brands = data.get("competitor_brands", [])
    if not isinstance(competitor_brands, list):
        add(results, "FAIL", "BRAND_SCHEMA", "competitor_brands must be a list")
        competitor_brands = []
    normalized_competitor_brands: list[str] = []
    for index, brand in enumerate(competitor_brands):
        if not isinstance(brand, str):
            add(
                results, "FAIL", "BRAND_SCHEMA",
                f"competitor_brands[{index}] must be a string",
            )
            continue
        value = brand.strip()
        if value:
            normalized_competitor_brands.append(value)
    if competitors and not normalized_competitor_brands:
        reason = str(data.get("competitor_brand_audit_reason", "")).strip()
        if reason:
            add(results, "WARN", "BRAND_EMPTY", f"competitor data has no seller brands: {reason}")
        else:
            add(results, "FAIL", "BRAND_EMPTY", "competitor data requires competitor_brands or competitor_brand_audit_reason")
    for value in normalized_competitor_brands:
        if value and phrase_found(deliverable_text, value, "phrase"):
            add(results, "FAIL", "COMPETITOR_BRAND", f"competitor seller brand {value!r} appears in deliverable copy")

    compatibility_targets_raw = data.get("compatibility_targets", [])
    if not isinstance(compatibility_targets_raw, list):
        add(results, "FAIL", "COMPATIBILITY_SCHEMA", "compatibility_targets must be a list")
        compatibility_targets: list[str] = []
    else:
        compatibility_targets = []
        for index, value in enumerate(compatibility_targets_raw):
            if not isinstance(value, str):
                add(
                    results, "FAIL", "COMPATIBILITY_SCHEMA",
                    f"compatibility_targets[{index}] must be a string",
                )
            elif value.strip():
                compatibility_targets.append(value.strip())
    if marketplace == "US" and re.search(r"\b(?:fit|fits)\s+for\b", listing_text, re.IGNORECASE):
        add(results, "FAIL", "COMPATIBILITY_RELATION", "US compatibility wording must not use fit/fits for")
    for target in compatibility_targets:
        for field_name, field_text in fields.items():
            if target.casefold() in field_text.casefold() and not compatible_target_ok(marketplace, field_text, target):
                allowed = (
                    "compatible with / for / fits"
                    if marketplace == "US"
                    else "kompatibel mit / passend für / für"
                )
                add(results, "FAIL", "COMPATIBILITY_RELATION", f"{field_name}: {target!r} is not bound by {allowed}", location=field_name)

    claim_audit = data.get("claim_audit", [])
    allowed_claim_statuses = {"temporary_use", "adjusted_use", "confirmed_use", "omit", "hold"}
    used_claim_statuses = {"temporary_use", "adjusted_use", "confirmed_use"}
    seen_claim_ids: set[str] = set()
    claim_statuses: dict[str, str] = {}
    claim_location_fields: dict[str, set[str]] = {}
    used_mappings_by_field: dict[str, list[str]] = {}
    confirmed_b_by_field: dict[str, list[str]] = {}
    confirmed_b_translation_by_field: dict[str, list[str]] = {}
    confirmed_b_pairs_by_field: dict[str, list[tuple[str, str]]] = {}
    if isinstance(claim_audit, list) and claim_audit:
        for item in claim_audit:
            if not isinstance(item, dict):
                add(results, "FAIL", "CLAIM_SCHEMA", "claim-audit entries must be objects")
                continue
            claim_id = str(item.get("id", "")).strip()
            status = str(item.get("status", "")).strip()
            phrase = str(item.get("phrase", "")).strip()
            match_mode = str(item.get("match", "phrase")).strip().lower()
            if not re.fullmatch(r"[AB]\d+", claim_id):
                add(results, "FAIL", "CLAIM_SCHEMA", f"invalid claim id: {claim_id!r}")
                continue
            if claim_id in seen_claim_ids:
                add(results, "FAIL", "CLAIM_SCHEMA", f"duplicate claim id: {claim_id}")
                continue
            seen_claim_ids.add(claim_id)
            claim_statuses[claim_id] = status
            if status not in allowed_claim_statuses:
                add(results, "FAIL", "CLAIM_SCHEMA", f"{claim_id}: unsupported status {status!r}")
                continue
            if not phrase:
                add(results, "FAIL", "CLAIM_SCHEMA", f"{claim_id}: phrase is blank")
                continue
            locations = item.get("locations", {})
            try:
                found = (
                    bool(locations)
                    if status in used_claim_statuses and isinstance(locations, dict)
                    else phrase_found(listing_text, phrase, match_mode)
                )
            except ValueError:
                add(results, "FAIL", "CLAIM_SCHEMA", f"{claim_id}: unsupported match mode {match_mode!r}")
                continue
            if status in used_claim_statuses and not found:
                add(results, "FAIL", "CLAIM_REQUIRED", f"{claim_id}: used claim {phrase!r} is absent from listing")
            elif status not in used_claim_statuses and found:
                add(results, "FAIL", "CLAIM_HELD", f"{claim_id}: held or omitted claim {phrase!r} leaked into listing")
            else:
                add(results, "PASS", "CLAIM_AUDIT", f"{claim_id}: {status} matches final listing")
            if status in used_claim_statuses:
                if not isinstance(locations, dict) or not locations:
                    add(results, "FAIL", "CLAIM_LOCATION", f"{claim_id}: used claim requires non-empty location matches")
                    continue
                normalized_source_locations: dict[str, str] = {}
                for raw_location, raw_text in locations.items():
                    location = resolve_location(str(raw_location))
                    expected = str(raw_text).strip()
                    if not location:
                        add(results, "FAIL", "CLAIM_LOCATION", f"{claim_id}: unknown location {raw_location!r}")
                    elif location not in fields:
                        add(
                            results, "FAIL", "CLAIM_LOCATION",
                            f"{claim_id}: location {location} is outside scope {scope}",
                        )
                    elif not expected:
                        add(results, "FAIL", "CLAIM_LOCATION", f"{claim_id}: blank location match for {raw_location!r}", location=location, claim_id=claim_id)
                    elif not phrase_found(fields[location], expected, "phrase"):
                        add(results, "FAIL", "CLAIM_LOCATION", f"{claim_id}: {expected!r} is absent from {location}", location=location, claim_id=claim_id)
                    else:
                        claim_location_fields.setdefault(claim_id, set()).add(location)
                        normalized_source_locations[location] = expected
                translation_locations = item.get("translation_locations", {})
                normalized_translation_locations: dict[str, str] = {}
                if not isinstance(translation_locations, dict) or not translation_locations:
                    add(
                        results, "FAIL", "CLAIM_TRANSLATION_LOCATION",
                        f"{claim_id}: used claim requires non-empty translation location matches",
                    )
                else:
                    for raw_location, raw_text in translation_locations.items():
                        location = resolve_location(str(raw_location))
                        expected = str(raw_text).strip()
                        if not location or location not in fields:
                            add(
                                results, "FAIL", "CLAIM_TRANSLATION_LOCATION",
                                f"{claim_id}: invalid translated location {raw_location!r}",
                            )
                        elif not expected:
                            add(
                                results, "FAIL", "CLAIM_TRANSLATION_LOCATION",
                                f"{claim_id}: blank translated match for {raw_location!r}",
                                location=location, claim_id=claim_id,
                            )
                        elif not phrase_found(
                            translated_fields_for_claims.get(location, ""),
                            expected,
                            "phrase",
                        ):
                            add(
                                results, "FAIL", "CLAIM_TRANSLATION_LOCATION",
                                f"{claim_id}: {expected!r} is absent from translated {location}",
                                location=location, claim_id=claim_id,
                            )
                        else:
                            normalized_translation_locations[location] = expected
                if set(normalized_translation_locations) != claim_location_fields.get(claim_id, set()):
                    add(
                        results, "FAIL", "CLAIM_TRANSLATION_COVERAGE",
                        f"{claim_id}: source and translation claim locations differ",
                        claim_id=claim_id,
                    )
                for location, source_text in normalized_source_locations.items():
                    if location in normalized_translation_locations:
                        used_mappings_by_field.setdefault(location, []).append(source_text)
                if claim_id.startswith("B") and status == "confirmed_use":
                    for raw_location, raw_text in locations.items():
                        location = resolve_location(str(raw_location))
                        if location in fields:
                            confirmed_b_by_field.setdefault(location, []).append(
                                str(raw_text)
                            )
                    for location, translated_text in normalized_translation_locations.items():
                        confirmed_b_translation_by_field.setdefault(location, []).append(
                            translated_text
                        )
                        if location in normalized_source_locations:
                            confirmed_b_pairs_by_field.setdefault(location, []).append((
                                normalized_source_locations[location], translated_text,
                            ))
    else:
        add(results, "FAIL", "CLAIM_SCHEMA", "claim_audit must be a non-empty list")

    nonrecoverable_pattern_set = {
        pattern for _label, pattern in NONRECOVERABLE_PATTERNS.get(marketplace, ())
    }
    for field_name, field_text in fields.items():
        for match in PHONE_RE.finditer(field_text):
            matched_phone = match.group(0)
            trailing = field_text[match.end():]
            is_date = bool(ISO_DATE_RE.fullmatch(matched_phone))
            # Slash-separated sizes are exempt only when a unit follows them
            # (120/150/180 mm); bare slash numbers may be phone numbers.
            is_slash_dimensions = bool(
                SLASH_DIMENSION_RE.fullmatch(matched_phone)
                and SLASH_UNIT_FOLLOW_RE.match(trailing)
            )
            if (
                sum(char.isdigit() for char in matched_phone) >= 7
                and not is_date
                and not is_slash_dimensions
                and not _mapped_numeric_model(
                    field_text, match, used_mappings_by_field.get(field_name, []),
                )
            ):
                add(
                    results, "FAIL", "PHONE",
                    f"listing fields contain a phone-like number: {matched_phone!r}",
                    location=field_name,
                )
        for label, pattern in NONRECOVERABLE_PATTERNS.get(marketplace, ()):
            if re.search(pattern, field_text, re.IGNORECASE):
                add(
                    results,
                    "FAIL",
                    "NONRECOVERABLE_CLAIM",
                    f"{field_name} contains independently prohibited wording: {label}",
                    location=field_name,
                )
        marketing_text = strip_non_commercial_disclaimer(field_text)
        for pattern in UNSUPPORTED_MARKETING_PATTERNS:
            if pattern in nonrecoverable_pattern_set:
                continue
            if re.search(pattern, marketing_text, re.IGNORECASE):
                add(
                    results,
                    "FAIL",
                    "UNSUPPORTED_MARKETING_CLAIM",
                    f"{field_name} contains unsupported marketing wording matching {pattern!r}",
                    location=field_name,
                )

        confirmed_for_field = confirmed_b_by_field.get(field_name, [])
        filter_pattern_strings = {pattern for _label, pattern in REGULATED_FILTER_PATTERNS}
        for label, pattern in REGULATED_FILTER_PATTERNS:
            matches = list(re.finditer(pattern, field_text, re.IGNORECASE))
            for risk_match in matches:
                if not any(
                    _risk_claim_covered(
                        field_text, risk_match, text
                    )
                    for text in confirmed_for_field
                ):
                    add(
                        results,
                        "FAIL",
                        "FILTER_CLAIM_CONFIRMATION",
                        f"{field_name} uses {label} wording without a matching confirmed Group B claim in the same field",
                        location=field_name,
                    )
        for label, pattern in REGULATED_DELIVERABLE_PATTERNS:
            if pattern in filter_pattern_strings:
                continue
            matches = list(re.finditer(pattern, field_text, re.IGNORECASE))
            for risk_match in matches:
                if not any(
                    _risk_claim_covered(
                        field_text, risk_match, text
                    )
                    for text in confirmed_for_field
                ):
                    add(
                        results,
                        "FAIL",
                        "RISK_CLAIM_CONFIRMATION",
                        f"{field_name} uses unconfirmed {label} wording {risk_match.group(0)!r} without a matching Group B claim in the same field",
                        location=field_name,
                    )

    for field_name, translated_text in translated_fields_for_claims.items():
        for label, pattern in NONRECOVERABLE_TRANSLATION_PATTERNS:
            if re.search(pattern, translated_text, re.IGNORECASE):
                add(
                    results,
                    "FAIL",
                    "TRANSLATION_NONRECOVERABLE_CLAIM",
                    f"translated {field_name} contains independently prohibited wording: {label}",
                    location=field_name,
                )
        confirmed_pairs = confirmed_b_pairs_by_field.get(field_name, [])
        for label, source_pattern, translation_pattern in CERTIFICATION_TRANSLATION_RULES:
            matches = list(re.finditer(translation_pattern, translated_text, re.IGNORECASE))
            if not matches:
                continue
            confirmed_translations = [
                translation for source, translation in confirmed_pairs
                if re.search(source_pattern, source, re.IGNORECASE)
            ]
            for risk_match in matches:
                if not any(
                    _risk_claim_covered(translated_text, risk_match, translation)
                    for translation in confirmed_translations
                ):
                    add(
                        results, "FAIL", "TRANSLATION_RISK_CLAIM_CONFIRMATION",
                        f"translated {field_name} uses {label} without a matching confirmed source claim in the same field",
                        location=field_name,
                    )
        confirmed_for_field = confirmed_b_translation_by_field.get(field_name, [])
        for label, pattern in HIGH_RISK_TRANSLATION_PATTERNS:
            matches = list(re.finditer(pattern, translated_text, re.IGNORECASE))
            for risk_match in matches:
                if not any(
                    _risk_claim_covered(
                        translated_text, risk_match, text
                    )
                    for text in confirmed_for_field
                ):
                    add(
                        results,
                        "FAIL",
                        "TRANSLATION_RISK_CLAIM_CONFIRMATION",
                        f"translated {field_name} uses unconfirmed {label} wording {risk_match.group(0)!r}",
                        location=field_name,
                    )

    field_claim_ids = data.get("field_claim_ids", {})
    required_claim_fields = list(fields)
    normalized_field_ids: dict[str, list[str]] = {}
    if not isinstance(field_claim_ids, dict):
        add(results, "FAIL", "CLAIM_COVERAGE", "field_claim_ids must be an object")
    else:
        for raw_field, raw_ids in field_claim_ids.items():
            location = resolve_location(str(raw_field))
            if not location:
                add(results, "FAIL", "CLAIM_COVERAGE", f"field_claim_ids contains unknown field {raw_field!r}")
                continue
            if location not in fields:
                add(
                    results, "FAIL", "CLAIM_COVERAGE",
                    f"field_claim_ids contains {location}, outside scope {scope}",
                )
                continue
            if not isinstance(raw_ids, list):
                add(results, "FAIL", "CLAIM_COVERAGE", f"field_claim_ids.{raw_field} must be a list")
                continue
            normalized_field_ids.setdefault(location, []).extend(str(value).strip() for value in raw_ids if str(value).strip())
        for field_name in required_claim_fields:
            ids = normalized_field_ids.get(field_name, [])
            if not ids:
                add(results, "FAIL", "CLAIM_COVERAGE", f"{field_name} has no mapped claim IDs", location=field_name)
            for claim_id in ids:
                if claim_id not in claim_statuses:
                    add(results, "FAIL", "CLAIM_COVERAGE", f"{field_name} references unknown claim ID {claim_id}", location=field_name, claim_id=claim_id)
                elif claim_statuses[claim_id] not in used_claim_statuses:
                    add(results, "FAIL", "CLAIM_COVERAGE", f"{field_name} references non-used claim ID {claim_id}", location=field_name, claim_id=claim_id)
                elif field_name not in claim_location_fields.get(claim_id, set()):
                    add(results, "FAIL", "CLAIM_COVERAGE", f"{field_name} maps {claim_id}, but that claim has no verified match in the field", location=field_name, claim_id=claim_id)
        mapped_ids = {claim_id for ids in normalized_field_ids.values() for claim_id in ids}
        for claim_id, status in claim_statuses.items():
            if status in used_claim_statuses and claim_id not in mapped_ids:
                add(results, "FAIL", "CLAIM_COVERAGE", f"used claim {claim_id} is not mapped to any final field")
        for claim_id, locations in claim_location_fields.items():
            for location in locations:
                if claim_id not in normalized_field_ids.get(location, []):
                    add(results, "FAIL", "CLAIM_COVERAGE", f"{claim_id} has a verified {location} match but is missing from that field's claim map", location=location, claim_id=claim_id)
        bullet_one_ids = set(normalized_field_ids.get("Bullet 1", []))
        bullet_five_ids = set(normalized_field_ids.get("Bullet 5", []))
        if full_listing and len(bullet_five_ids) >= 2 and bullet_five_ids <= bullet_one_ids:
            add(
                results,
                "WARN",
                "READABILITY_DUPLICATE_CLAIMS",
                "Bullet 5 is supported only by claims already used in Bullet 1; add a distinct buyer answer or keep the limited-data wording concise",
                location="Bullet 5",
            )

    if marketplace == "DE":
        if full_listing:
            if "Warm Tips" in description:
                add(results, "FAIL", "LOCALIZED_HEADING", "DE Description uses the English heading 'Warm Tips'", location="Description")
            if not any(heading in description for heading in ("Hinweise", "Wichtige Hinweise")):
                add(results, "FAIL", "LOCALIZED_HEADING", "DE Description lacks a localized Hinweise heading", location="Description")
        required_tips = (DE_TIP_1, DE_TIP_2)
        polarity_risks = ("nicht drucklos",)
    else:
        required_tips = (US_TIP_1, US_TIP_2)
        polarity_risks = ("not non-pressurized",)
    for risk in polarity_risks:
        if risk.casefold() in listing_text.casefold():
            add(results, "FAIL", "POLARITY_RISK", f"review likely polarity reversal: {risk!r}")
    if full_listing:
        if not all(tip in description for tip in required_tips):
            add(results, "FAIL", "WARM_TIPS", "Description does not contain both fixed marketplace Warm Tips exactly", location="Description")
        elif not description.endswith(required_tips[1]):
            add(results, "FAIL", "WARM_TIPS_END", "Description does not end with the second fixed Warm Tip", location="Description")
        else:
            add(results, "PASS", "WARM_TIPS", "Description ends with both fixed marketplace Warm Tips")

    translations = data.get("translations", {})
    if not isinstance(translations, dict):
        add(results, "FAIL", "TRANSLATION_SCHEMA", "translations must be an object")
        translations = {}
    translated_title = validated_text(results, translations, "title", "translated Title")
    translated_highlights = validated_text(
        results, translations, "highlights", "translated Product Highlights"
    )
    translated_description = validated_text(
        results, translations, "description", "translated Description"
    )
    raw_translated_bullets = translations.get("bullets", [])
    if full_listing and (not isinstance(raw_translated_bullets, list) or len(raw_translated_bullets) != 5):
        add(results, "FAIL", "TRANSLATION_SCHEMA", "translations.bullets must contain exactly five strings")
        translated_bullets = ["", "", "", "", ""]
    elif not full_listing:
        translated_bullets = []
        if raw_translated_bullets:
            add(results, "FAIL", "SCOPE_FIELD", "title_highlights translations must omit Bullets")
        if translated_description:
            add(results, "FAIL", "SCOPE_FIELD", "title_highlights translations must omit Description")
    else:
        translated_bullets = []
        for index, value in enumerate(raw_translated_bullets, start=1):
            if not isinstance(value, str):
                add(
                    results, "FAIL", "TRANSLATION_SCHEMA",
                    f"translated Bullet {index} must be a string",
                )
                translated_bullets.append("")
            else:
                translated_bullets.append(value.strip())
    translation_consistency(results, "Title", title, translated_title, compatibility_targets, marketplace)
    translation_consistency(results, "Product Highlights", highlights, translated_highlights, compatibility_targets, marketplace)
    if full_listing:
        for index, (source, translated) in enumerate(zip(bullets, translated_bullets), start=1):
            translation_consistency(results, f"Bullet {index}", source, translated, compatibility_targets, marketplace)
        translation_consistency(results, "Description", description, translated_description, compatibility_targets, marketplace)

    translated_listing = "\n".join(
        [translated_title, translated_highlights]
        if not full_listing else
        [translated_title, translated_highlights, *translated_bullets, translated_description]
    )
    validate_package_components(
        results,
        data,
        fields,
        translated_field_values(translations, full_listing),
    )

    translation_terms = data.get("translation_terms", [])
    if not isinstance(translation_terms, list):
        add(results, "FAIL", "TRANSLATION_SCHEMA", "translation_terms must be a list")
    else:
        for item in translation_terms:
            if not isinstance(item, dict):
                add(results, "FAIL", "TRANSLATION_SCHEMA", "translation_terms entries must be objects")
                continue
            source_term = str(item.get("source", "")).strip()
            target_term = str(item.get("target", "")).strip()
            if not source_term or not target_term:
                add(results, "FAIL", "TRANSLATION_SCHEMA", "translation_terms require source and target")
            elif phrase_count(listing_text, source_term) and target_term not in translated_listing:
                add(results, "FAIL", "TRANSLATION_TERM", f"translation is missing mapped term {source_term!r} -> {target_term!r}")

    raw_exemptions = data.get("copy_exemptions", [])
    exemptions = [str(value) for value in raw_exemptions] if isinstance(raw_exemptions, list) else []
    for competitor_index, competitor in enumerate(competitors, start=1):
        for field_name, field in fields.items():
            length, sequence = longest_common_run(field, competitor)
            exempt = any(sequence and sequence in " ".join(tokens(value)) for value in exemptions)
            if length >= 8 and not exempt:
                add(
                    results,
                    "FAIL",
                    "ANTI_COPY",
                    f"{field_name} and competitor {competitor_index} share {length} consecutive tokens: {sequence!r}",
                    location=field_name,
                )

    if not any(result.level == "FAIL" for result in results):
        add(results, "PASS", "FINAL", "all hard validation checks passed")
    return results


def read_input(path: str | None) -> dict[str, object]:
    if path:
        raw = Path(path).read_text(encoding="utf-8")
    else:
        raw = sys.stdin.read()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("top-level JSON value must be an object")
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", help="read validation JSON from this UTF-8 file instead of stdin")
    args = parser.parse_args()
    try:
        data = read_input(args.file)
        results = validate(data)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"FAIL\tINPUT\t{error}")
        raise SystemExit(1)
    for result in results:
        print(f"{result.level}\t{result.code}\t{result.message}")
    raise SystemExit(1 if any(result.level == "FAIL" for result in results) else 0)


if __name__ == "__main__":
    main()
