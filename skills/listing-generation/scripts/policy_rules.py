#!/usr/bin/env python3
"""Shared hard-policy patterns for listing classification and final validation."""

from __future__ import annotations

import re


HIGH_RISK_TYPES = {
    "certification",
    "bpa_free",
    "food_contact",
    "non_toxic",
    "medical",
    "child_safety",
    "pet_safety",
    "antibacterial",
    "electrical_safety",
    "fire_resistance",
    "waterproof_rating",
    "leakproof",
    "load_capacity",
    "temperature_rating",
    "precision",
    "filter_class",
    "filtration_efficiency",
    "allergy_suitability",
    "lifespan",
    "environmental",
    "regulated_performance",
}

EXACT_MATERIAL_GRADE_PATTERNS = (
    r"\b(?:304|316)(?:er)?(?:[\s-]+grade)?[\s-]*"
    r"(?:stainless[\s-]+steel|edelstahl\w*)\b",
    r"\b(?:stainless[\s-]+steel|edelstahl\w*)[\s-]*"
    r"(?:(?:grade|type|typ|werkstoff|aisi)[\s:#-]*)?(?:304|316)\b",
    r"\b(?:aisi|sus|ss)[\s-]?(?:304|316)\b",
)


HIGH_RISK_PATTERNS = EXACT_MATERIAL_GRADE_PATTERNS + (
    # English
    r"\bbpa[\s-]?free\b", r"\bfood[\s-]?grade\b", r"\bfood[\s-]?contact\b",
    r"\bnon[\s-]?toxic\b", r"\bchild[\s-]?safe\b", r"\bsafe\s+for\s+children\b",
    r"\bpet[\s-]?safe\b", r"\bsafe\s+for\s+pets\b", r"\bantibacterial\b",
    r"\bantimicrobial\b", r"\bmedical[\s-]?grade\b",
    r"\bhypoallergenic\b", r"\bwater[\s-]?(?:proof|resistant)\b",
    r"\bleak[\s-]?proof\b", r"\b(?:flame|fire)[\s-]?resistant\b",
    r"\bload\s+capacity\b", r"\bscratch[\s-]?resistant\b", r"\bshatter[\s-]?proof\b",
    r"\beco[\s-]?friendly\b", r"\bbiodegradable\b", r"\bcarbon\s+neutral\b",
    r"\bsustainable(?:\s+certified)?\b", r"\bfood[\s-]?safe\b",
    r"\bsafe\s+for\s+food\b", r"\blead[\s-]?free\b", r"\bphthalate[\s-]?free\b",
    r"\bpfas[\s-]?free\b", r"\bpfoa[\s-]?free\b", r"\blatex[\s-]?free\b",
    r"\bheat[\s-]?resistant\b", r"\brecyclable\b",
    r"\b(?:true[\s-]+)?hepa\b", r"\bh(?:13|14)\b",
    r"\ballergy[\s-]?friendly\b", r"\bsuitable\s+for\s+allergy\s+sufferers\b",
    # German
    r"\bbpa[\s-]?frei\w*\b", r"\blebensmittelecht\w*\b",
    r"\blebensmittelgeeignet\w*\b", r"\bkontakt\s+mit\s+lebensmitteln\b",
    r"\bungiftig\w*\b", r"\bschadstofffrei\w*\b", r"\bkindersicher\w*\b",
    r"\btiersicher\w*\b", r"\bantibakteriell\w*\b", r"\bantimikrobiell\w*\b",
    r"\bwasserdicht\w*\b", r"\bwasserabweisend\w*\b", r"\bauslaufsicher\w*\b",
    r"\bleckagefrei\w*\b", r"\b(?:flammhemmend|feuerfest)\w*\b",
    r"\btragf(?:ä|a)higkeit\b", r"\bkratzfest\w*\b", r"\bbruchsicher\w*\b",
    r"\bumweltfreundlich\w*\b", r"\bbiologisch\s+abbaubar\w*\b",
    r"\bklimaneutral\w*\b", r"\bnachhaltig\w*\b", r"\brecycelbar\w*\b",
    r"\bbleifrei\w*\b", r"\bphthalatfrei\w*\b", r"\bpfas[\s-]?frei\w*\b",
    r"\bpfoa[\s-]?frei\w*\b", r"\blatexfrei\w*\b",
    r"\bhitzebest(?:ä|a)ndig\w*\b", r"\btemperaturbest(?:ä|a)ndig\w*\b",
    r"\ballergikergeeignet\w*\b", r"\bfür\s+allergiker\s+geeignet\b",
)

UNSUPPORTED_MARKETING_PATTERNS = (
    # English
    r"\bbest\b", r"\buniversal\b", r"\bfits\s+all\b", r"\bunbreakable\b",
    r"\bprofessional[\s-]?grade\b", r"\bcommercial[\s-]?grade\b", r"\bpremium\b",
    r"\bdurable\b", r"\bheavy[\s-]?duty\b", r"\bhigh[\s-]?quality\b",
    r"\beasy\s+to\s+use\b",
    # German. Deliberately do not match besteht or beständig.
    r"\bbeste(?:r|s|n|m)?\b", r"\buniversell\w*\b", r"\bunzerbrechlich\w*\b",
    r"\bprofessionell\w*\b", r"\bgewerblich\w*\b", r"\bpremium\w*\b",
    r"\blanglebig\w*\b", r"\bhochwertig\w*\b", r"\bstrapazierf(?:ä|a)hig\w*\b",
    r"\beinfach\s+zu\s+bedienen\b",
)

DE_NON_COMMERCIAL_DISCLAIMER_RE = re.compile(
    r"\bnicht\s+für\s+(?:den\s+)?gewerblich\w*",
    re.IGNORECASE,
)


def strip_non_commercial_disclaimer(text: str) -> str:
    """Remove German non-commercial disclaimers before marketing-claim scans."""
    return DE_NON_COMMERCIAL_DISCLAIMER_RE.sub("", text or "")

# These claims remain prohibited even when a seller asks to enable them.
NONRECOVERABLE_PATTERNS = {
    "US": (
        ("best", r"\bbest\b"),
        ("universal", r"\buniversal\b"),
        ("fits all", r"\bfits\s+all\b"),
        ("cure", r"\bcur(?:e|es|ed|ing)\b"),
    ),
    "DE": (
        ("beste", r"\bbeste(?:r|s|n|m)?\b"),
        ("universell", r"\buniversell\w*\b"),
        ("passt für alle", r"\bpasst\s+für\s+alle\b"),
        ("heilt", r"\bheil(?:t|en|te|ung)\w*\b"),
    ),
}

NONRECOVERABLE_TRANSLATION_PATTERNS = (
    ("最佳", r"最佳"),
    ("通用/万能", r"(?:通用|万能)"),
    ("适合所有", r"适合所有"),
    ("治愈", r"治愈"),
)

# Chinese certification wording must pair with the corresponding source claim.
# Keep marks and approval/clearance distinct; a confirmed unrelated B is not enough.
CERTIFICATION_TRANSLATION_RULES = (
    ("CE认证", r"\bCE[\s-]+(?:certified|certification|zertifizier\w*)\b",
     r"(?<![A-Za-z0-9])CE\s*认证"),
    ("CE标志", r"\bCE[\s-]+(?:marked|marking|mark|gekennzeichnet|Kennzeichnung)\b",
     r"(?<![A-Za-z0-9])CE\s*(?:标志|标记|标识)"),
    ("FDA批准", r"\bFDA[\s-]+(?:approved|approval|zugelassen|Zulassung)\b",
     r"(?<![A-Za-z0-9])FDA\s*(?:批准|核准)"),
    ("FDA许可", r"\bFDA[\s-]+(?:cleared|clearance)\b",
     r"(?<![A-Za-z0-9])FDA\s*(?:许可|准许)"),
    ("FDA认证", r"\bFDA[\s-]+(?:certified|certification|zertifizier\w*)\b",
     r"(?<![A-Za-z0-9])FDA\s*认证"),
    ("UL列名", r"\bUL[\s-]+(?:listed|listing|gelistet)\b",
     r"(?<![A-Za-z0-9])UL\s*(?:列名|列入名录)"),
    ("UL认证", r"\bUL[\s-]+(?:certified|certification|zertifizier\w*)\b",
     r"(?<![A-Za-z0-9])UL\s*认证"),
    ("ETL列名", r"\bETL[\s-]+(?:listed|listing|gelistet)\b",
     r"(?<![A-Za-z0-9])ETL\s*(?:列名|列入名录)"),
    ("ETL认证", r"\bETL[\s-]+(?:certified|certification|zertifizier\w*)\b",
     r"(?<![A-Za-z0-9])ETL\s*认证"),
    ("FCC认证", r"\bFCC[\s-]+(?:certified|certification|zertifizier\w*)\b",
     r"(?<![A-Za-z0-9])FCC\s*认证"),
    ("LFGB声明", r"\bLFGB\b", r"(?<![A-Za-z0-9])LFGB(?![A-Za-z0-9])"),
    ("CPSIA声明", r"\bCPSIA\b", r"(?<![A-Za-z0-9])CPSIA(?![A-Za-z0-9])"),
    ("食品安全认证", r"\bcertified[\s-]+food[\s-]?safe\b",
     r"(?:食品安全认证|经认证(?:的)?食品安全)"),
)

# Classification and source scanning accept the same finite certification
# forms as bilingual pairing, including spaces, hyphens and German forms.
HIGH_RISK_PATTERNS += tuple(
    source for _label, source, _translation in CERTIFICATION_TRANSLATION_RULES
)

HIGH_RISK_TRANSLATION_PATTERNS = (
    (
        "精确材质等级",
        r"(?:304|316)\s*(?:(?:级|号|型)\s*)?不锈钢|"
        r"不锈钢\s*(?:(?:等级|牌号|型号|类型)\s*)?(?:304|316)|"
        r"(?<![A-Za-z0-9])(?:AISI|SUS|SS)[\s-]?(?:304|316)(?![A-Za-z0-9])",
    ),
    ("BPA声明", r"(?:不含|无)\s*BPA"),
    ("食品接触声明", r"(?:食品级|食品接触|可接触食品)"),
    ("无毒声明", r"无毒"),
    ("儿童或宠物安全声明", r"(?:儿童安全|宠物安全|对儿童安全|对宠物安全)"),
    ("抗菌声明", r"(?:抗菌|抑菌)"),
    ("防水或防漏声明", r"(?:防水|防漏|不漏水)"),
    ("防火声明", r"(?:阻燃|防火)"),
    ("环保声明", r"(?:环保|可持续|可回收|可降解|碳中和)"),
    (
        "过滤等级",
        r"(?<![A-Za-z0-9])(?:true[\s-]+)?hepa(?![A-Za-z0-9])|"
        r"(?<![A-Za-z0-9])h(?:13|14)(?![A-Za-z0-9])",
    ),
    ("过敏适用性", r"(?:适合过敏人群|过敏友好)"),
    (
        "过滤效率",
        r"(?:过滤|捕获|去除)[^。；\n]{0,40}\d{1,3}(?:[.,]\d+)?\s*%|"
        r"\d{1,3}(?:[.,]\d+)?\s*%[^。；\n]{0,40}(?:颗粒|过滤)",
    ),
    (
        "粒径过滤性能",
        r"(?:过滤|颗粒)[^。；\n]{0,40}\d+(?:[.,]\d+)?\s*(?:微米|µm|μm)|"
        r"\d+(?:[.,]\d+)?\s*(?:微米|µm|μm)[^。；\n]{0,40}(?:过滤|颗粒)",
    ),
    ("承重声明", r"(?:承重|可承受)[^。；\n]{0,30}\d"),
    ("耐温声明", r"(?:耐温|耐热|可承受温度)[^。；\n]{0,30}\d"),
    ("精度声明", r"(?:精度|准确度|误差|公差)[^。；\n]{0,30}\d|±\s*\d"),
    ("寿命声明", r"(?:寿命|可使用|持续)[^。；\n]{0,30}\d+(?:小时|次|年)"),
)

# Extra test-dependent forms that are often expressed without a named claim type.
TEST_DEPENDENT_PATTERNS = (
    (
        "filtration efficiency",
        r"\b(?:filter\w*|filtr\w*|capture\w*|remove\w*|retain\w*)\b"
        r"[^.;\n]{0,80}\b\d{1,3}(?:[.,]\d+)?\s*%|"
        r"\b\d{1,3}(?:[.,]\d+)?\s*%[^.;\n]{0,80}"
        r"\b(?:filtration|filtering|particles?|filterleistung|partikel)\b",
    ),
    (
        "particle-size performance",
        r"\b(?:filter\w*|filtr\w*|particle\w*|partikel\w*)\b"
        r"[^.;\n]{0,80}\b\d+(?:[.,]\d+)?\s*(?:microns?|µm|μm|mikrometer)\b",
    ),
    (
        "load rating",
        r"\b(?:supports?\s+up\s+to|load\s+capacity|rated\s+for|"
        r"trägt\s+bis|belastbar\s+bis|tragfähigkeit)\b[^.;\n]{0,50}\d|"
        r"\b(?:holds?|supports?|trägt)\s+(?:up\s+to\s+|bis\s+zu\s+)?"
        r"\d+(?:[.,]\d+)?\s*(?:kg|lbs?)\b",
    ),
    (
        "temperature rating",
        r"\b(?:rated\s+to|withstands?\s+up\s+to|safe\s+up\s+to|"
        r"temperatur\w*\s+bis|hitzebeständig\s+bis)\b[^.;\n]{0,50}\d",
    ),
    (
        "precision or tolerance",
        r"(?:±\s*\d|\b(?:accuracy|precision|tolerance|genauigkeit|präzision|toleranz)\b[^.;\n]{0,50}\d)",
    ),
    (
        "tested lifespan",
        r"\b(?:lasts?\s+(?:up\s+to\s+)?\d|rated\s+for\s+\d|"
        r"lebensdauer\s+(?:von|bis)\s+\d|hält\s+bis\s+zu\s+\d)\b",
    ),
)

ALL_HIGH_RISK_SOURCE_PATTERNS = HIGH_RISK_PATTERNS + tuple(
    pattern for _label, pattern in TEST_DEPENDENT_PATTERNS
)

REGULATED_DELIVERABLE_PATTERNS = tuple(
    (f"regulated claim {index}", pattern)
    for index, pattern in enumerate(HIGH_RISK_PATTERNS, start=1)
) + TEST_DEPENDENT_PATTERNS

PACKAGE_DISCLOSURE_PATTERNS = {
    "US": re.compile(
        r"\b(?:package\s+(?:includes?|contents?)|set\s+(?:includes?|contains?|contents?)|"
        r"comes?\s+with|included)\b|\b\d+\s*(?:piece|pc)[-\s]?set\b|"
        r"\b\d+[-\s]?pack\b",
        re.IGNORECASE,
    ),
    "DE": re.compile(
        r"\b(?:lieferumfang|im\s+set\s+(?:enthalten|enthaltene)|"
        r"mitgeliefert\w*|enthalten\w*)\b|\b\d+[-\s]?teilig\w*\s+set\b",
        re.IGNORECASE,
    ),
}
