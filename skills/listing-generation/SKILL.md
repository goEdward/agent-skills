---
name: listing-generation
description: >-
  为 Amazon 美国站和德国站生成符合 2026-07-27 新标题/商品亮点规则的完整 listing；仅在用户明确要求“仅迁移/只生成 Title 与 Item Highlights”等低频模式时执行旧链接字段迁移。仅在用户明确调用 /listing-generation、$listing-generation 或明确要求使用 listing-generation skill 时执行。
---

# Amazon US/DE Listing 生成

Generate non-media Amazon US and DE listings under the Title and Item Highlights policy effective July 27, 2026. Do not apply this workflow to other marketplaces.

If the user explicitly names a marketplace outside US or DE, state that this
skill does not support it and stop. When the marketplace is omitted, infer it
only from the target product's authoritative language; competitor language
alone is not a marketplace signal, and unresolved language requires
clarification.

## Scope Routing (重要)

默认使用 `scope: full_listing`，生成新链接所需的完整 listing（Title、Item Highlights、Bullets 和 Description，按完整 listing 规则输出）。输入中出现旧标题、旧五点、竞品标题或 ASIN，不会自动触发迁移模式；这些内容默认作为新 listing 的事实与市场参考资料。

只有当用户明确表达以下任一意图时，才使用 `scope: title_highlights` 并读取 `references/legacy_title_highlights.md`：

- “仅迁移/只迁移 Title 和 Item Highlights”；
- “只生成 Title 与 Item Highlights”；
- “根据旧标题和五点改写标题和商品亮点，不生成完整 listing”；
- 明确指定 `title_highlights` scope 或 legacy migration。

如果用户只提供旧标题和五点但没有明确要求迁移，仍按 `full_listing` 处理；不要根据“旧/legacy/五点”这些数据标签猜测 scope。

### Target/Competitor Mode Routing

Keep mode routing separate from scope routing. Apply this precedence:

1. An explicit mode request wins: “竞品共识模式”/`consensus_only` selects
   `consensus_only`; “目标模式”/`target_anchored` selects `target_anchored`.
   If explicit `target_anchored` has no complete target block to bind, stop for
   clarification; never silently fall back to `consensus_only`. If explicit
   `consensus_only` appears with a target marker, keep `consensus_only` and
   treat the marker as non-binding metadata.
2. Otherwise, “子 ASIN”, “指定子体”, “待售子体”, “目标产品”, or equivalent
   standalone/structural wording marks the next complete product block as the
   target and selects `target_anchored`. Do not extract a target marker from a
   product Title, Bullet, or competitor copy. The marker may be on its own line
   or in an earlier message, and the block need not contain an ASIN.
3. Without an explicit mode or target marker, multiple product blocks select
   `consensus_only`; do not treat “产品1” alone as a target marker.

When a target marker binds the first complete product block, later unmarked
blocks are competitors. Before drafting, record the resolved mode, target block
(if any), and competitor blocks. A lower-priority marker never overrides an
explicit mode.

## Workflow

Read each applicable reference completely once while it remains available in context. Reapply its checks after edits; reread only if the content is unavailable or the file changed. Normal generation uses documented script interfaces. Inspect implementation or tests only for a traceback or a validation result unexplained by the documented rules.

1. Read [references/core_generation.md](references/core_generation.md) for every task. Apply its shared fact, evidence, compliance, Title, Item Highlights, native-copy, and final-validation rules. Only in the explicitly requested `title_highlights` migration mode do not read the full-listing reference.
2. For `full_listing`, also read [references/full_listing_rules.md](references/full_listing_rules.md) completely for Bullet, Description, mobile allocation, and full-listing keyword placement rules.
3. Read [references/legacy_title_highlights.md](references/legacy_title_highlights.md) completely only when the user explicitly asks for migration or only Title and Item Highlights from an existing/legacy Title and Bullets. Use `scope: title_highlights` only then; otherwise use the default `scope: full_listing`, even when legacy-looking fields are supplied.
4. Read [references/native_copy_us.md](references/native_copy_us.md) completely for US or [references/native_copy_de.md](references/native_copy_de.md) completely for DE.
5. Read [references/regulated_claims.md](references/regulated_claims.md) completely only when the input contains certification, food-contact, filtration-grade, safety, environmental, medical, exact alloy/material grades such as 304/316 stainless steel, or other test-dependent wording.
6. Read [references/full_audit.md](references/full_audit.md) completely only when the user explicitly requests a detailed/full audit, full keyword analysis, or backend-attribute recommendations.
7. Read [references/traffic_keyword_selection.md](references/traffic_keyword_selection.md) completely when the input contains traffic/conversion data, aliases, or an explicit traffic-focused request. Always perform the core keyword-and-placement pass in `core_generation.md`; traffic data adds evidence, not a separate mode.
8. Treat the invocation as raw listing data. Apply the Target/Competitor Mode
   Routing rules above, then choose `target_anchored` only for the resolved
   target block. If an explicit target mode has no bindable block, stop for
   clarification; otherwise choose `consensus_only`. Normalize only the facts
   used in final copy plus valuable Group B candidates. Treat target wording as
   semantic evidence, not reusable copy.
9. Lock validated competitor-derived terms and user priority keywords before drafting. For `full_listing` with eligible competitors, first calculate the original-Bullet benchmark as documented in `full_listing_rules.md`; reuse those same source records in final validation. In an explicitly requested `title_highlights` migration, follow the legacy reference without a Bullet benchmark. Plan Title and Item Highlights together: reserve user-required keyword locations, then allocate remaining space to distinct buyer value.
10. Draft only selected fields directly in the target marketplace language: English for US, German for DE, without an English intermediate draft for DE. Complete source fact/risk review and the native-copy gate before building Chinese translations and exact fact mappings. Apply the evidence, pending-fact, and package-component rules in `core_generation.md`; do not infer new facts while translating.
11. Build the existing compact `schema_version: 2` payload using `scripts/run_listing_pipeline.py --help` and the core mapping rules. Omit optional defaults. For `full_listing`, provide non-empty Simplified Chinese translations for Title, Item Highlights, all five Bullets, and Description; for `title_highlights`, provide translations for Title and Item Highlights. Run one complete pipeline per validation attempt after source copy, Chinese translations, and mappings are ready.
12. On failure, repair all reported issues together using the field-and-dependency repair procedure in `core_generation.md`, then rerun the complete pipeline. Each generation attempt allows at most two repair reruns after the initial run (three complete pipeline runs total); rerun only after a material repair, never with an unchanged payload. If a `FAIL` or blocking clarification remains after the second repair rerun, stop and report the unresolved blockers instead of continuing the same attempt. Warnings alone do not trigger another run. A new seller-confirmed answer or materially changed product input starts a new attempt; a retry target never overrides the hard gates.
13. Generate directly in chat with compact output by default. When a real Title/Item Highlights tradeoff remains, provide one recommended paired option and at most two paired backups, each validated as a complete option and labeled by strategy. Do not generate independent title/highlight combinations or cosmetic paraphrases. Reuse the pipeline-rendered optimization conclusion, audit, selected Listing fields, and translations without adding a separate free-form report that claims an optimization was applied. Do not create persistent task files or artifacts unless the user requests an export. Trigger clarification only for the blocking conditions defined in the core reference.

## Bundled Scripts

Resolve `SKILL_DIR` as the absolute directory containing this `SKILL.md`, never from the current workspace directory, `$CODEX_HOME`, or a hard-coded user path. Substitute the resolved absolute path when invoking bundled scripts; do not pass the literal text `SKILL_DIR`.

Select a Python 3 launcher by platform: on macOS/Linux try `python3`, then `python`; on Windows try `py -3`, then `python3`, then `python`. Production commands:

- macOS/Linux: `<python-launcher> "<SKILL_DIR>/scripts/run_listing_pipeline.py" --format listing-markdown < "<TEMP_PAYLOAD.json>"`
- Windows PowerShell: `<python-launcher> -X utf8 "<SKILL_DIR>\scripts\run_listing_pipeline.py" --format listing-markdown --file "<TEMP_PAYLOAD.json>"`

The `listing-markdown` format is the production default. On failure it returns grouped repair issues and blocking clarification reasons, without repeating successful confirmation rows or emitting final copy. On success it returns the deterministic conclusion, bilingual confirmation block, compact audit, selected source-language fields, and complete Chinese translations. In `full_listing` it groups all source Bullets before their translations and always includes the translated Description; `title_highlights` emits only the selected pair. `--format markdown` retains the complete compact audit; a user-requested detailed/full audit still follows `references/full_audit.md` and must be assembled by the model from the validated result. JSON is for script diagnosis. The pre-draft `kwfreq.py` calculation is the only separate component call during normal generation and never replaces final validation. If no Python 3 launcher is available, apply the native-copy gate and return only a clearly labeled unverified draft.

Do not replace a successful pipeline result with a summary. Preserve the seller
confirmation block (A claims, held B claims, pending P facts, and the copyable
confirmation reply), the audit, the complete source-language Listing, and all
required Chinese translations.

The pipeline accepts UTF-8 JSON with or without a BOM through `--file`, and JSON on stdin where shell redirection is available. Boolean flags must be unquoted JSON booleans, `min_count` values must be finite non-negative JSON integers, and documented collection fields must remain lists. Keep temporary payloads in the operating system's temporary directory and reuse source records across repairs; clean them up when the generation attempt ends, including on failure. On Windows, keep `-X utf8` so Chinese output is safe under legacy code pages. The scripts use only the Python standard library. Treat `FAIL` as blocking and review every `WARN` without adding filler.

After changing the skill or its scripts, run `<python-launcher> "<SKILL_DIR>/scripts/selftest.py"` on macOS/Linux or `<python-launcher> "<SKILL_DIR>\scripts\selftest.py"` on Windows, followed by Python compilation, before using it for production output.
