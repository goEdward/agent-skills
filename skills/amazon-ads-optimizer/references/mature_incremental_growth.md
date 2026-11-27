# Mature Product Profitable Discovery

Use this reference when `mature_growth_context.recommendation_mode` is `stabilize_with_micro_phrase` or `ready_for_controlled_discovery`, or when the user explicitly requests mature-product growth.

## Entry gate

- `stabilize_profit_only`: repair the emitted maturity, economics, Exact coverage, or account-profit prerequisite. Do not unlock normal discovery.
- `stabilize_with_micro_phrase`: allow only emitted, ring-fenced Phrase tests. Keep Auto and Broad closed.
- `ready_for_controlled_discovery`: allow the emitted Phrase → Auto Close/Loose → small-budget Broad sequence.

Require profitable Manual Exact coverage before normal discovery. Exact existence without orders and acceptable ACoS is insufficient. Treat attributed profit as a proxy, not proof of incrementality.

## Controlled-exploration overlay

When the base mode is `stabilize_profit_only`, a separately emitted `controlled_exploration` overlay may buy a small amount of evidence without changing that base state. Keep at most three semantically verified 3-5 word Phrase tests; one-order evidence remains low confidence and never authorizes scale. Use only emitted bids and the emitted shared micro budget.

Allow an emitted Auto Close test only when the daily-window signal is insufficient, economics and product identity are usable, and Auto Close coverage is not confirmed. Never unlock Auto Loose, substitutes/complements, or Broad through this overlay.

For placement, use the engine-emitted plan. With reliable source placement evidence, use the configured controlled routing. Without reliable evidence, use the configured all-placement neutral preload and base-bid divisor. Do not hard-code a separate 20% Phrase rule or all-zero Auto rule here.

Add campaign-only Negative Exact isolation only after confirming full-query Exact coverage. In rebuild mode it remains conditional until the corresponding new Exact group has run for at least seven days, produced at least two orders, stayed within profit economics, and confirmed full-query coverage.

## Channel roles

1. Manual Exact protects the profitable base and validates or harvests discovered complete queries.
2. Manual Phrase is the primary controlled long-tail discovery channel.
3. Auto Close/Loose discovers listing-driven query language only when the base gate permits it.
4. Manual Broad remains a low-bid, small-budget later-stage channel.
5. Auto substitutes/complements and ASIN targets remain separate product targeting.

Keep discovery campaigns separate from Exact so budgets, placements, and economics remain auditable.

## Growth stages

- `harvest_to_exact`: at least two profitable orders or consistent profitable conversion across independent periods.
- `validate_in_exact`: relevant one-order or low-confidence query with viable CPC; use low-bid Exact validation.
- `control_bid_before_harvest`: converting query above the profit cap; control the source bid first.
- `exact_overlap_not_incremental`: already covered by Manual Exact; do not count as net-new growth or automatically negative it.
- `discovery_waste_review`: zero-order discovery waste requires semantic review before bid-down or negative action.
- `analyze_as_product_targeting`: keep the target out of keyword tables.

## Profit rules

Use `profit_acos_cap = min(Target ACoS, gross-margin midpoint)` and `ad_attributed_profit_proxy = ad sales × gross-margin midpoint − ad spend`. Do not promise incremental profit without holdout, organic-sales, or cannibalization evidence.

## Report placement

- Section 1 contains only prepared old-campaign source-scope actions.
- Section 2 contains emitted Exact validation/harvest rows, allowed discovery rows, product-targeting rows, placement structure, budgets, and pending isolation conditions.
- Section 3 contains only semantically verified executable negatives; exclude Exact overlap, converting, historically protected, scoped-conflict, uncertain, and product-targeting terms.
