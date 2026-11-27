---
name: amazon-st-search-terms
description: Manually selected only. Use only when the user explicitly selects or names /amazon-st-search-terms, $amazon-st-search-terms, or amazon-st-search-terms to generate Amazon backend Search Terms for a specified marketplace from a product title, bullet points, and pasted or exported competitor search-term data. Do not invoke implicitly for generic Amazon, SEO, listing, keyword, ST, search term, Rufus, or Cosmo requests.
---

# Amazon ST Search Terms

## Use

Use this skill only when the user manually selects `/amazon-st-search-terms`, `$amazon-st-search-terms`, or explicitly asks to use this exact skill.

This skill generates Amazon backend Search Terms from:

- Target Amazon marketplace
- Product title
- Bullet points
- Competitor search-term data sorted by traffic, highest to lowest

Do not use this skill for listing title writing, bullet writing, Amazon Ads search-term reports, product research, SellerSprite scans, or policy research unless the user explicitly invokes `/amazon-st-search-terms` or `$amazon-st-search-terms`.

## Required Inputs

Before generating anything, verify the user provided all required inputs:

- `Marketplace`: target Amazon marketplace, such as US, UK, or DE
- `Title`: product title
- `Bullet Points`: product bullet points, preferably all five
- Competitor search-term data: actual search terms pasted or exported from one or more competitors

An ordinary Amazon listing URL does not count as competitor search-term data because it does not provide traffic-ranked terms. If the data is missing or the user provides only listing URLs, stop immediately and ask the user to paste or export the actual competitor ST/search-term data. Do not generate placeholder terms, infer them from the title, or continue with only title and bullet points.

If the marketplace, title, or bullet points are missing, stop and ask for the missing field before generating. Do not infer the marketplace from the listing language.

## Competitor Data Parsing

Use strict mixed grouping:

- Treat an ASIN line as the start of a competitor group.
- Treat a blank line as a competitor group boundary.
- Terms inside each group are already sorted by traffic from high to low.
- Preserve the user's order within each group.
- Accept either one search term per line or `search term<TAB>traffic value`. Treat a trailing traffic value as ranking metadata, never as a candidate keyword. If a numeric value cannot be distinguished reliably from the search term, stop and ask the user to use tab-separated columns.
- If ASIN boundaries and blank-line boundaries conflict so badly that groups cannot be determined, stop and ask the user to re-paste the data with one competitor per group.

After parsing the groups, merge them deterministically:

- If every term has a comparable numeric traffic value, merge all groups and sort globally by traffic descending. Break ties by competitor-group order, then original term order.
- If only within-group rankings are available, interleave by rank: take the first term from every group in user order, then the second term from every group, and continue until exhausted.
- Never exhaust one entire group before using the next group unless the user explicitly marks that group as higher priority.

Common acceptable inputs:

```text
B0XXXXXXXX
term one
term two
term three

B0YYYYYYYY
term four
term five
```

```text
term one
term two

term three
term four
```

## Candidate Selection Rules

Build a candidate word list in priority order:

1. High-traffic competitor terms first, using the deterministic cross-group merge order above.
2. Semantically relevant terms derived from the product title and bullet points for Rufus/Cosmo alignment.
3. Lower-priority overflow terms that may be useful for ads, broad match, or A+ content.

Clean candidates before fitting:

- Remove words already present in the title.
- Ignore bullet points for deduplication; bullet points are semantic reference only.
- Remove pesticide-related terms and irrelevant words.
- Remove prepositions, conjunctions, articles, and other low-value stop words.
- Convert plurals to singular forms when confident.
- Use the language appropriate for the provided target marketplace.
- Keep final ST lowercase, single-spaced, and without punctuation.
- Keep numbers only when they are part of a relevant size, quantity, version, generation, or model expression and are not already covered by the title. Remove unrelated standalone numbers and all traffic values.
- Deduplicate globally; a word may appear only once in the final ST string.

Before byte fitting, apply this mandatory prohibited-term filter to the candidate list:

- Remove the seller's own brand, every competitor brand, and all other brand names.
- Remove every ASIN, including ASINs that appear outside competitor-group header lines.
- Remove profanity.
- Remove temporary or promotional statements, including equivalents of `new`, `latest`, `today`, `available now`, `limited time`, `discounted`, and `on sale`.
- Remove subjective or promotional claims, including equivalents of `best`, `cheapest`, `amazing`, `effective`, `fastest`, `popular`, and `trending`.
- Remove abusive or offensive terms, including terms that promote illegal activity or glorify hatred, violence, or racial, sexual, or religious intolerance.
- Apply these exclusions to translations, spelling variations, and inflected forms in the target marketplace language.

Track terms removed by this filter separately as policy exclusions and never pass them to `st_byte_fit.py`. The script's `removed_terms` field must contain only terms rejected by the byte limit, not terms removed for policy risk.

## 250-Byte Rule

Under the default 250-byte rule, the final Search Terms must remain `<250` Amazon-counted UTF-8 bytes. If the user provides a documented marketplace-specific limit, pass that value to the script as the exclusive limit.

Calculate Amazon-counted bytes as the sum of the UTF-8 byte lengths of the normalized words. Do not count separator spaces or punctuation. The raw UTF-8 length of the final displayed string may therefore be higher than the Amazon-counted length.

Never calculate, estimate, or verify byte length mentally. Always call the bundled script:

1. Resolve `scripts/st_byte_fit.py` relative to this `SKILL.md` and invoke it by absolute path. Do not assume the current working directory is the skill directory.
2. Use an available Python 3 interpreter: prefer `py -3` or `python` on Windows, and `python3` or `python` on macOS/Linux.
3. Prefer the cross-platform `--terms` form. Place `--terms` last because every following argument is treated literally as a candidate term, including values that begin with `-`. Place `--limit` and `--stdin`, when used, before `--terms`. The script treats `--limit` as an exclusive limit:

```text
<python-command> "<absolute-skill-path>/scripts/st_byte_fit.py" --terms "term one" "term two" "term three"
```

For large candidate lists, use `--stdin` and feed one term or phrase per line using the current platform's standard input mechanism. Do not depend on Unix-only commands such as `printf` when running on Windows.

The script is the only authority for:

- Final ST string
- Amazon-counted UTF-8 byte count
- Raw UTF-8 byte count
- Kept terms
- Removed terms

If script execution is unavailable, stop and tell the user that this skill requires Python to enforce the 250-byte rule. Do not replace the script with model-side counting.

## Output Format

Answer in Simplified Chinese. Preserve original search terms and keywords in their source language.

Use this structure:

```markdown
### 1. 最终优化 Search Terms
[one-line final ST]

Amazon 计数字节：[N] bytes（要求：<[exclusive limit]）
原始 UTF-8 字节：[N] bytes

### 2. 语义标签
- 适用人群：[Original Word (中文翻译), ...]
- 使用场景：[Original Word (中文翻译), ...]
- 核心功能：[Original Word (中文翻译), ...]
- 材质属性：[Original Word (中文翻译), ...]

### 3. 数据分析与建议
- 目标站点：[marketplace]
- 检测语言：[language]
- 主要采用来源：[which competitor groups or high-traffic clusters were prioritized]
- 新注入的 AI 关联词：[semantic terms added for Rufus/Cosmo alignment]
- 因 Amazon 禁用规则排除的词：[policy exclusions with brief category]
- 因字节上限移除的词：[terms removed by the script]
- 未采用但可用于广告/A+的词：[overflow terms with potential]
- 优化逻辑：[brief explanation of weighting and semantic fit]
```

If a semantic label or analysis field has no reliable content, write `无`. Do not infer or invent content merely to fill every field.

Do not claim compliance with Amazon policy beyond keyword formatting and byte fitting. If a term looks risky but uncertain, exclude it and mention it in the analysis.
