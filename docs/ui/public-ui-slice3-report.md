# Public UI Slice 3 report

## Overview-first changes

- The dashboard now opens with a concise intelligence hero and an optional,
  empty hero-media slot. No image or external asset was added.
- Context metrics are labelled to avoid treating legacy Installation rows as a
  verified current-installation count.
- The Slice 2B evidence-relevance feed remains capped at eight, but is now a
  compact airport/story/type/date card list.
- Priority Signals are limited to five and link to the full Signals view.

## Progressive disclosure

Airport pages continue to put EMAS today and current projects first. Funding
and grant-derived Signals are retained but placed under native accessible
`details` disclosure, preventing BGM's historical grant records from
dominating its primary project story. Historical installation records,
timeline, incidents, and runway reference information remain accessible
below the primary story.

CGF retains a simple current-presence presentation (06 and 24). MDW retains
the four reviewed ends (04R, 13L, 22L, 31R) ahead of repair/design and watch
information. BGM retains the Slice 2 public review qualification; its grant
records remain available in the funding disclosure without changing their
stored records or asserting reconciliation.

## Public safety

No database/domain/evidence changes were made. The UI continues to omit raw
Signal source notes and reconciliation/evidence internals. Governed source
title, publisher, date, and link remain the public source presentation.

## Verification and limitations

- Focused static tests: 16 passed.
- The dashboard hero image is intentionally an empty responsive layout slot.
- Signals retain their existing lightweight search/status/country browsing;
  a richer client-side browsing redesign is deferred to avoid an SPA-like
  expansion in this slice.

## Local visual review

```powershell
.\.venv\Scripts\python.exe -m http.server 8000 --directory site
```

Review dashboard first, then CGF, MDW, BGM, Signals list, one Signal detail,
and mobile-width dashboard/airport pages. No commit, push, or deployment was
performed.
