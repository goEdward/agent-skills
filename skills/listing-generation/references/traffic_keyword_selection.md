# Traffic and Alias Keyword Selection

Read this reference only when the input contains traffic or conversion data,
multiple aliases for one search concept, or an explicit traffic-focused request.
This is optional preprocessing for either listing scope, not a new pipeline mode.
The core keyword pass still runs when no performance data is supplied.

## Evidence order

Rank relevant terms by the strongest available evidence:

1. target-product orders across the complete supplied period
2. reliable clicks plus period conversion, recalculated as target-product
   orders divided by clicks rather than averaged daily rates
3. an explicit user priority list
4. product-form-matched competitor coverage
5. an ordinary semantic candidate with no traffic evidence

Competitor frequency proves that a term is common in the comparison set; it
does not prove traffic or conversion. Never describe a competitor-only or
semantic candidate as a traffic term.

Do not promote a one-click/one-order term solely because it shows 100%
conversion. Do not lock a zero-order performance term. Keyword value never
creates factual support: confirm color, quantity, size, compatibility,
component, occasion, and product-form meaning against the exact target SKU.

## Alias resolution

Group expressions that serve the same buyer search concept before drafting.
Select one winning visible term per concept for each field, based on evidence,
relevance, native phrasing, and fit with the planned fact. Do not place a list of
aliases in Item Highlights merely to increase coverage.

Use `variants` only for safe singular/plural, spacing, hyphenation, and ordinary
inflection forms of the selected term. Do not put semantically distinct aliases
such as `Christmas tree`, `Xmas tree`, and `holiday tree` into `variants` just so
the pipeline counts them as interchangeable. A term such as `Christmas
ornament`, `tree topper`, or `climbing Santa` is eligible only when that exact
product identity is supported.

Distribute a second alias to another visible field only when it expresses a
distinct truthful buyer intent and reads naturally there. Put remaining safe
search variants in backend fields only when the user requests backend guidance.

For example, `Climbs up and down a Christmas tree` is a useful Item Highlights
fact when the target product demonstrably performs that action and is intended
for tree use. It earns `Christmas tree` through a concrete buyer scene; do not
append `Xmas tree`, `holiday tree`, or other aliases to the same field unless a
separate supported buyer intent genuinely requires one.

## Selection and placement

Lock terms before drafting. Prefer a supported buyer-value fact that naturally
carries the winning term. Title receives the primary product identity and the
highest-value purchase-fit phrase. Item Highlights receives distinct comparison
facts and secondary terms that did not fit naturally in Title. Bullets may use
another term only where it answers the assigned buyer question.

For attached `.csv` or `.xlsx` performance data (including WPS exports), use an
available spreadsheet skill or run a one-off local Python command (`csv` or
`openpyxl`) to aggregate and evaluate the report; if given legacy `.xls` or
`.et` files, ask the user to save them as `.xlsx` or `.csv` first. Do not add a
spreadsheet parser to this skill or pass the complete report to the Listing
pipeline. Pass only the selected terms as `priority_keywords`; omit discarded
aliases. Lock at most two performance-backed phrases in `title_highlights`. In
`full_listing`, keep only the small set that has a planned, fact-supported
placement.

Keyword volume never creates a minimum inclusion count. If a term cannot be
placed without displacing a stronger fact, changing product meaning, or harming
native copy, do not lock it.

Apply the shared keyword decision record in `core_generation.md`. When traffic
or alias evidence is available, add the traffic evidence, selected winner,
discarded aliases, and the concrete evidence-based reason for each omission;
do not replace the shared natural-placement reason with SEO, ranking, or density
language.
