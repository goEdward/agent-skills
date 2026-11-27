# Full Listing Rules

Read this reference only for `scope: full_listing`. It contains rules that do
not apply when migrating only Title and Item Highlights.

## Length And Mobile Allocation

| Field | Amazon hard limit | Mobile handling | Working target |
|---|---:|---:|---:|
| Each Bullet | 10-255 | — | answer one buyer question |
| Bullet 1 | 255 maximum | prefer 145 or less | 125-140 when evidence supports it |
| Bullet 2 | 255 maximum | prefer 165 or less | 140-160 when evidence supports it |
| Bullet 1 + 2 | — | hard maximum 320 | 270-300 |
| Bullets 3-5 | 255 maximum | — | normally 150-200 |

The combined Bullet 1 + 2 limit protects two-point mobile visibility. The
individual 145/165 values are preferred allocations, not Amazon hard limits;
exceeding either may WARN when the combined total remains within 320. Working
targets are not minimum quotas. Never add filler. Bullets 3-5 may be shorter
when their buyer question is complete.

## Bullet Points

Generate exactly five Bullets with natural lead phrases. No Bullet may end with
punctuation; use semicolons between clauses.

Keep every Bullet between 10 and 255 characters. Start with a capitalized lead
and normally use `Lead: description`. Spell out standalone numbers 1-9 except
in names, models, and measurements; keep a space between a measurement number
and unit when marketplace-native grammar permits it. Treat these style items
as WARN-level review signals while keeping hard limits and prohibited content
as blocking rules. Keep Bullets free of Amazon-restricted bullet symbols
(`™®€…†‡¢£¥©±~°*—`) and bullet-prohibited wording (`eco-friendly`,
`antibacterial`, `bamboo`, `soy`, and their German equivalents); write verified
temperatures in Bullets without `°` or place full temperature and confirmed
Group B details in Description.

For fit-dependent parts, assign the five buyer questions in this order:

1. compatibility and purchase-error prevention
2. primary function or observable outcome
3. material and structure
4. package contents or care
5. a distinct constraint, maintenance, installation, or remaining buyer question

Give each Bullet one primary buyer question and normally two or three directly
related supports. Package lists are the narrow multi-item exception. Bullet 5
must not substantially repeat Bullet 1.

Lock keyword choice before prose optimization. Improve naturalness by changing
the sentence, clause order, or related field placement; do not silently replace
a validated term with an easier synonym. Facts and policy remain hard gates.

Before drafting, calculate the benchmark once with `scripts/kwfreq.py` when
eligible competitor Bullets and candidates exist. Use its existing JSON stdin
interface: `{"mode":"bullet","keywords":["term"],"competitors":[["original Bullet"]]}`.
Invoke it without CLI arguments (with `-X utf8` on Windows); feed UTF-8 JSON
without a BOM. Store the original Bullet records once in temporary data and
reuse them mechanically as `competitor_bullets` in the final payload, rather
than retyping or rewriting them. Preserve each Bullet boundary. The final
pipeline uses the same counting functions and recomputes the benchmark.
Skip this calculation when there is no eligible comparison data, and for
`title_highlights`; it is not an extra generation mode.

Benchmark competitor Bullet text only. A phrase is broadly supported when
present in at least two listings and at least 60% of eligible competitors. Use
the non-zero median as the preferred lower signal and rounded non-zero mean as
the preferred upper signal. Cap a multi-token phrase at three uses and a
one-token term at six uses. A zero-frequency benchmark is informational, not a
prohibition on natural factual wording.

Treat the preferred competitor lower signal as flexible by one occurrence: a
draft exactly one below it and still using the term at least once gets a WARN
when another use would require an unrelated or repetitive sentence; a larger
shortfall requires the existing per-keyword exception below or remains blocking.
Record the affected field and naturalness reason.
Never satisfy a missing count by appending a keyword to an unrelated buyer
question.

For a justified frequency exception in `full_listing`, add only the affected
term to `draft.keywords` using its `phrase`, the unchanged benchmark `target`,
`source: "competitor"`, and `override_reason`. The reason must identify the
affected buyer question and why the normal count would make the copy repetitive,
unrelated, or unclear; it must be at least 24 characters and must not rely on SEO,
ranking, density, or filling space. For example:
`{"phrase":"side handle","target":"3","source":"competitor","override_reason":"Bullet 2 explains pouring control; repeating the handle in material, package or care adds no new buyer information"}`.
The pipeline reviews this exception as a WARN while retaining other candidates'
benchmarks and all explicit placement constraints. Review the reason yourself;
the script checks its form, not its truth. Do not lower `target` to match the
draft. User-required words and locations remain mandatory.

Treat user priority keywords separately. Require at least one use across the
five Bullets. Permit Item Highlights as the sole placement only under the room
and relevance rule in the core reference. After the first valid placement, use
the term as many additional times as distinct buyer questions naturally
support; absence of another natural placement is not a failure. Title or
Description alone does not satisfy the requirement. Preserve the exact phrase
when grammar permits; otherwise use only declared formatting or inflection
variants. Put accepted forms in `variants`.

Plan each submitted keyword's eligible locations before drafting, using the
calculated benchmark and any explicit user placements. Verify the complete
five-Bullet draft against that plan before translation and mapping; the final
pipeline computes exact actual counts. Treat spaced, hyphenated, and joined
measurement forms such as `51 mm`, `51-mm`, and `51mm` as equivalent. Avoid
overlapping candidates that create conflicting targets for a generic root and
its longer phrase; retain the independently valuable buyer search term.

Draft at most three paired Title/Item Highlights options only when a real
keyword, function, scene, material, size, or conversion tradeoff exists. Mark
one as recommended and label backups by strategy. Validate each complete option
with the combined pipeline; do not generate independent field candidates or
cosmetic paraphrases. Update `listing_matches` and `translation_matches` to the
exact wording of every option being returned, and complete the marketplace
native-copy gate before running the pipeline. Follow the shared field-and-
dependency repair procedure in `core_generation.md`; never attach a keyword
fragment to clear a count.

## Product Description

Keep source-language Description at or below 2,000 characters, including
headings, spaces, line breaks, and the fixed tips. The Chinese reference
translation does not share this limit. This is the skill's generic US/DE
generation budget, based on [Amazon's listing guidance](https://sellercentral.amazon.com/seller-forums/discussions/t/5477ea7e-c310-4c7d-be0c-d4e7ea845ee9)
(checked 2026-10-08), not verification of every category's limits. Follow any
known stricter applicable category requirement as well.

Write three to five concise feature points rather than one prose paragraph.
Add Specifications and Package Includes only when supported. Finish with
exactly these localized tips:

DE:

1. Aufgrund unterschiedlicher Bildschirme und Lichtverhältnisse kann es zu leichten Farbabweichungen kommen. Bitte beziehen Sie sich auf das tatsächliche Produkt.
2. Alle Größenangaben werden manuell gemessen und dienen nur als Referenz; geringfügige Abweichungen sind möglich. Vielen Dank für Ihr Verständnis.

US:

1. Due to variations in screens and lighting conditions, slight color differences may occur, which is normal. Please refer to the actual product.
2. All size measurements are manually measured and provided for reference only; minor deviations may exist. Thank you for your understanding.

## Full-Scope Native Copy Gate

In addition to the shared gate, confirm that every Bullet answers one distinct
buyer question, the five leads do not repeat one instruction, and Description
adds a readable summary instead of copying all five Bullets.
