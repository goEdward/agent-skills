# Amazon Ads Report Contract

Use this file for development or an explicitly requested full audit. During normal generation, read only the `Semantic finalization` section below; the remaining sections are audit references.

## Sources of truth

- `references/decision_policy.json` owns thresholds shared by scripts and runtime instructions.
- `scripts/prepare_amazon_analysis.py` owns aggregation, economics, scope protection, actions, campaign grouping, budgets, and placement routing.
- `scripts/render_amazon_report.py` owns compact visibility and deterministic rendering.
- `report_payload.json` is the semantic-audit input.
- `report_draft.md` is the factual user-facing base and must remain unchanged except for semantic-safety corrections to negative decisions.
- `report_full.md` is audit evidence, not a second rule system.

Do not copy numeric thresholds into this contract. Update `decision_policy.json` and its contract tests instead.

## Hard stops

Stop without a partial report when required metrics or executable source-scope columns are missing, decision values are invalid, product identity is unresolved or conflicting, supplied economics are malformed, or a supplied placement report cannot be matched and validated. Multiple report ASINs are valid only when every ASIN belongs to one database product. Relay the generated `action_message`.

When product facts are missing, write reliable user-supplied facts into the configured JSON product database with `scripts/product_db.py upsert` and rerun preparation before semantic review. Facts that exist only in the conversation or an edited payload cannot satisfy `product_facts.*` evidence validation.

## Invariants

- Calculate CTR, CPC, CVR, ACoS, and ROAS from aggregate totals.
- Use only product database facts or facts supplied by the user for semantic decisions.
- Preserve converting, historical, Exact, root-protected, and scope-conflict terms from unsafe negatives.
- Use performance negatives only for originating Phrase/Broad scopes. Exact sources use bid control or scope-level pause.
- Never negate from CTR alone.
- Preserve source-language keywords. Translations are reference-only.
- Keep product/ASIN targets in a dedicated product-targeting table inside Section 2.
- Treat `action_eligibility` as the only authority for user-facing scale labels.
- Use independent daily windows first, validated cumulative subtraction second, and unresolved overlap only as protected context.
- Keep old-campaign bid changes in Section 1 and new-campaign placement structure in Section 2.
- Build every Section 1 metric and action from one complete search-term/campaign/ad-group/source-target/match-family scope; never fall back to another scope's action.

## Three-section output

Return the product confirmation line followed by exactly these sections:

1. `搜索词表现、来源与具体动作`
   - Preserve every prepared old-campaign source-scope row, metric, bid action, and display limit. Compact presentation limits for watchlists or other sections must never truncate this table or replace a prepared source action with “无立即动作”.
   - Combine converting and zero-order review rows in this section.
   - Do not apply new-campaign ranking or aggregation to these rows.

2. `新广告创建建议`
   - In rebuild mode, render the prepared grouped execution table.
   - Put every `scale_ready` row first, then use the prepared weighted visibility order.
   - Selecting a campaign group displays every sibling with the same settings and shared budget.
   - Enforce the zero-order display caps compiled from `decision_policy.json` across Exact and Phrase combined.
   - Always retain separately bounded controlled Phrase/Auto Close tests outside the converting and zero-order visibility allowances.
   - Render product/ASIN targets in an independent table even when the grouped keyword execution table has rows; never mix them into keyword campaign groups.
   - With no reliable placement evidence, rebuild mode uses the configured neutral preload and base-bid divisor; optimize-existing omits placement output.
   - Keep detailed source evidence, original bids, placement rationale, budget basis, and stop rules in `report_full.md` only.

3. `快捷复制——否定关键词`
   - Include only source-language, semantically verified, immediately executable negatives at the smallest safe scope.
   - Keep pending isolation negatives in Section 2 with their complete validation condition.
   - Display `无` when nothing is safe to execute.

## Growth modes

- `stabilize_profit_only` blocks normal discovery. It may still carry a separately emitted, low-confidence `controlled_exploration` evidence overlay without changing the base state.
- `stabilize_with_micro_phrase` permits only emitted micro Phrase tests; keep Auto and Broad closed.
- `ready_for_controlled_discovery` may use the emitted Phrase, Auto, and Broad sequence.
- Controlled exploration never changes Exact actions, negatives, bid controls, scope protection, scale labels, or the Broad unlock rule.

## Semantic audit

Audit only candidates exposed in `semantic_review`. Product facts may approve or remove a provisional negative, but may not change calculated metrics, ranking, budgets, bids, campaign groups, placement math, or unaffected draft rows. A scope conflict always preserves good scopes and blocks global negatives.

## Semantic finalization

The renderer emits a pending payload and an empty executable-negative table. The model remains the semantic authority; validation only enforces the report contract and does not claim to prove relevance.

Create a JSON object with exactly these top-level fields:

```json
{
  "schema_version": 1,
  "payload_fingerprint": "<copy semantic_review.payload_fingerprint>",
  "review_json": "semantic_review.json",
  "verdicts": [
    {
      "candidate_id": "<exact candidate_id>",
      "verdict": "approve|reject|review",
      "reason": "<nonempty semantic judgment>",
      "factual_evidence": ["product_facts.title"]
    }
  ]
}
```

- Include exactly one verdict for every candidate, including diagnostic and controlled-exploration context rows. Unknown, duplicate, or missing IDs and unknown fields are invalid.
- All four top-level review fields are required, including the nonempty `review_json` identifier.
- `factual_evidence` must be a nonempty list of existing, nonempty `product_facts` field references. It records the factual basis; it is not an algorithmic semantic proof.
- Only candidates explicitly classified as `negative_exact` or `negative_phrase` may be approved. Diagnostic and controlled-exploration rows cannot become negatives by default.
- Review-tier, low-sample, and every root approval require `"hard_mismatch": true`.
- A root approval also requires `supporting_terms_checked` to reproduce the complete `supporting_terms` list exactly and in order.
- Approvals fail closed for protected, converting, historical, Exact, root-protected, ASIN/product-targeting, explicitly ineligible, unknown-tier, conflicting, incomplete-scope, or non-Broad/Phrase candidates. ASIN detection uses the token/target expression itself and does not depend on optional flags. Executable Amazon keyword-negative scope is campaign/ad group; source target and match family remain validation evidence, not a narrower executable target scope.
- Conditional isolation negatives require complete engine-emitted gate data and every gate to be ready. Missing gate data never autoapproves.

Run:

```text
<python> <skill-dir>/scripts/finalize_amazon_report.py <output-directory> <review.json>
```

The finalizer validates and renders all three artifacts before replacing `report_payload.json`, `report_draft.md`, and `report_full.md`. On a caught write error it attempts to restore the originals; this is synchronized application-level rollback, not a claim of crash-atomic multi-file storage. Re-running the same valid review is idempotent.

## Validation

Before delivery, require automated contract tests to confirm the three headings, zero-order caps, placement fallback, group completeness, protected-term negative safety, semantic-review completeness, unchanged Section 1/2 content, and absence of internal display codes. If validation fails, do not publish the draft.
