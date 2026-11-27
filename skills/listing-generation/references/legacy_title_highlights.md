# Legacy Title and Item Highlights Migration

Use this reference only when the user asks to derive a new Title and Item
Highlights from an existing Title and Bullets. Set `scope: title_highlights`.
Treat labels such as `source_type: legacy_listing` as workflow hints, not
pipeline fields.

## Source and fact handling

- Use the legacy Title and Bullets as semantic evidence, never reusable prose.
- Use explicit, internally consistent ordinary facts. Put conflicting or
  ambiguous values in `pending_facts` and block their risky renderings.
- Keep certification, safety, environmental, medical, test-dependent, and
  other high-risk wording out unless seller-confirmed under the core rules.
- Keep exact color, size, compatibility, and package count only when they apply
  unambiguously to the target SKU. Never blend child-variation facts.
- Do not store legacy Bullets as `competitor_bullets` and do not benchmark their
  frequency as competitor evidence.
- Require at least three usable facts under the core richness gate; do not use
  keywords as substitutes for missing product facts.

## Optional keyword-performance reports

When search-term performance or keyword aliases are supplied, read
`traffic_keyword_selection.md` completely. In this scope, lock at most two
selected phrases and pass them as `priority_keywords` with explicit
`eligible_locations` limited to `Title` and/or `Item Highlights`. Omit
`keyword_candidates`. Never pass the full report or discarded aliases to the
Listing pipeline.

## Draft and validation payload

Draft only `title`, `highlights`, and their Simplified Chinese translations. When
there is a material keyword-versus-conversion tradeoff, draft one recommended
paired option and at most two paired backups; never combine a Title from one
option with Highlights from another.
Do not place legacy Bullets or Description inside `draft`.

```json
{
  "schema_version": 2,
  "marketplace": "US",
  "mode": "target_anchored",
  "scope": "title_highlights",
  "facts": [
    {
      "field": "product_type",
      "claim": "Pipe cleaners",
      "claim_zh": "扭扭棒",
      "listing_matches": {"Title": "Pipe Cleaners"},
      "translation_matches": {"Title": "扭扭棒"}
    },
    {
      "field": "craft_use",
      "claim": "For crafts",
      "claim_zh": "用于手工制作",
      "listing_matches": {"Title": "for Crafts"},
      "translation_matches": {"Title": "手工制作"}
    },
    {
      "field": "color",
      "claim": "Pink",
      "claim_zh": "粉色",
      "listing_matches": {"Item Highlights": "Pink"},
      "translation_matches": {"Item Highlights": "粉色"}
    }
  ],
  "package_components": [],
  "draft": {
    "title": "Pipe Cleaners for Crafts",
    "highlights": "Pink",
    "translations": {
      "title": "手工制作扭扭棒",
      "highlights": "粉色"
    }
  },
  "priority_keywords": [
    {
      "term": "pipe cleaners",
      "variants": ["pipe cleaner"],
      "eligible_locations": ["Title"]
    }
  ]
}
```

Every used fact must map source and Chinese wording to the same selected field.
Item Highlights must remain at or below Amazon's 125-character hard limit when
it carries a locked keyword. Return the pipeline-rendered optimization conclusion, seller confirmation
block, audit, Title, Item Highlights, and their Chinese translations, omitting
Bullets and Description.
