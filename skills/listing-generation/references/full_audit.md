# Full Audit Output

Read this reference only when the user explicitly requests a detailed audit, full keyword analysis, or backend-attribute recommendations. The production pipeline and hard validation remain identical to compact mode.

For `full_listing`, output all sections below in sequence. For
`title_highlights`, output only sections 1, 2, 3, and 6; never generate or
analyze Bullets or Description. Put any requested backend-attribute gaps in
section 6 for that scope.

## 1. Conflict Detection Report

Include:

- summary and data status
- target-supported ordinary attributes, one field per line
- Group A temporary attributes with the same IDs as the confirmation table and source coverage
- seller-confirmed facts, identifying any enabled Group B fact
- Probable attributes used only in analysis
- Conflicting attributes and automatic exclusions
- target-silent competitor consensus additions in target-anchored mode
- generation impact and content richness

If clarification fallback is active, output only the report and one to three
necessary questions, then stop; do not render a confirmation block or final
copy until the blocking facts are resolved. Otherwise, insert the exact
pipeline confirmation block after the report.

## 2. Title

Provide source-language Title, measured count, complete Chinese translation, and a short explanation of retained identity information.

## 3. Item Highlights

Provide source-language highlights, measured count, complete Chinese translation, and a short explanation of information moved from the Title.

## 4. Five Bullet Points (`full_listing` only)

Provide exactly five source-language Bullets and measured counts for every Bullet plus Bullet 1 + 2. Then provide five complete Chinese translations.

## 5. Product Description (`full_listing` only)

Provide localized feature points, supported Specifications/Package Includes sections, and the two fixed Warm Tips. Then provide a structurally matching complete Chinese translation.

## 6. Title and Highlights Analysis

Report only:

- adopted validated terms and their locations
- one to three category-specific recommendations
- a compact source-language keyword/position/Chinese-meaning table
- in `title_highlights`, confirmed-fillable and seller-required backend fields when the user requested them

## 7. Bullet and Description Analysis (`full_listing` only)

Explain buyer-question coverage, evidence use, and material frequency decisions using:

`keyword: coverage M/N; counts a,b,c; suggested x-y; used z`

Then include:

- natural rewrites table, if any
- exclusions and reasons without exposing competitor seller brands
- held Group B terms
- missing facts and backend fields split into confirmed-fillable vs seller-required
- one consolidated keyword audit table

Do not repeat adopted keywords across multiple redundant tables. Do not expose internal field-claim maps or validator traces when hard checks pass.
