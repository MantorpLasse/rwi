# Release Candidate Verification

## Final visual change

Removed the dashboard's abstract three-panel runway motif. The hero is now a single, responsive typographic composition containing only the product eyebrow, `Runway Safe Intelligence`, and the supporting statement. No image, SVG, decorative graphic, or replacement reserved column was added.

## Release-candidate screenshots

Desktop (1440 x 1000):

- `docs/ui/screenshots/release-candidate/desktop-dashboard.png`
- `docs/ui/screenshots/release-candidate/desktop-bgm.png`
- `docs/ui/screenshots/release-candidate/desktop-cgf.png`
- `docs/ui/screenshots/release-candidate/desktop-mdw.png`
- `docs/ui/screenshots/release-candidate/desktop-signals.png`
- `docs/ui/screenshots/release-candidate/desktop-signal-detail.png`

Mobile (390 x 844):

- `docs/ui/screenshots/release-candidate/mobile-dashboard.png`
- `docs/ui/screenshots/release-candidate/mobile-bgm.png`
- `docs/ui/screenshots/release-candidate/mobile-mdw.png`
- `docs/ui/screenshots/release-candidate/mobile-signals.png`

## Visual self-check

1. The dashboard hero looks intentional without the motif: title, statement, whitespace, and the metrics form a complete composition.
2. There is no reserved right-hand column or awkward blank area on desktop.
3. The mobile hero remains compact and moves directly from the statement to metrics.
4. No unrelated layout regression was observed in the sampled dashboard, airport, Signals, or Signal-detail pages.
5. BGM, CGF, and MDW remain readable as airport intelligence profiles with their current-status, project/watch, timeline, runway, and historical-disclosure hierarchy intact.
6. Signal detail remains a readable intelligence brief with detail, economy, and public source provenance.
7. Signals remains dense but usable as a comparison register with the existing search/filter controls.

All ten sampled pages had no horizontal overflow at their capture viewport.

## Semantic verification

- BGM still states `Aktuell EMAS-status ej verifierad`; it makes no current-presence/absence or construction/completion claim.
- Signal 6 still states `Projektuppgift under granskning` and retains its no-construction/no-completion qualification.
- CGF contains the reviewed 06 and 24 ends.
- MDW contains the reviewed 04R, 13L, 22L, and 31R ends.
- Historical installation disclosures are collapsed by default for sampled airport pages.
- Public Signals 52 and 54 remain excluded.
- Dashboard output contains exactly 8 recent-update entries and 5 selected Signals.

No database, domain, evidence, reconciliation, freshness, public-projection, or route logic was changed.

## Public-data safety verification

The regenerated `site/data.json` excludes SourceAssertion, physical-installation-identity, reconciliation-link, manual-year-estimate, raw source-note, and review actor/reason data. A generated-site scan found no private evidence fields, internal implementation paths, or script paths in the public data projection. Governed public source data remains unchanged.

## Checks

- Static export: passed.
- Focused static/UI tests: **16 passed**.
- Python compilation: passed.
- Full test suite: **362 passed**.
- Warnings: existing FastAPI and SQLAlchemy deprecation warnings only; no release-candidate failure.
- Generated-site local-link verification: **157 HTML pages**, no broken local references.
- Public-boundary and semantic guard verification: passed.
- Desktop dashboard and mobile dashboard/BGM/MDW/Signals horizontal-overflow checks: passed.
- `git diff --check`: passed.

## Git working tree

Branch: `foundation/evidence-identity`.

Tracked modifications:

- `app/static_export/build.py`
- `app/static_export/static/style.css`
- `app/static_export/templates/_components.html`
- `app/static_export/templates/airport_detail.html`
- `app/static_export/templates/base.html`
- `app/static_export/templates/index.html`
- `app/static_export/templates/signal_detail.html`
- `app/static_export/templates/signals_list.html`
- `tests/test_static_export.py`

Untracked implementation/test files:

- `app/static_export/presentation.py`
- `tests/test_static_presentation.py`

Untracked documentation and captures include `docs/research/`, `docs/ui/`, and the Slice 3, Slice 3B, predeploy, visual-redesign, hero, and release-candidate screenshot directories. Nothing was staged.

## Files normally included in the eventual UI release commit

- The tracked static-export templates, stylesheet, build changes, and static-export test listed above.
- `app/static_export/presentation.py` and `tests/test_static_presentation.py`.
- The governing public-UI documentation needed to explain the accepted slices, including this release-candidate verification and the final visual-redesign plan/report.
- `docs/ui/screenshots/release-candidate/` as the final visual review record.

## Files recommended not to include in this release commit

- `app/static_export/static/images/emas-hero.png`: now unreferenced and therefore removable; it must not be included in the release commit.
- `docs/ui/screenshots/hero/` and `docs/ui/public-ui-hero-integration-report.md`: obsolete temporary-hero review material.
- `docs/research/` and older intermediate screenshot folders unless they are intentionally being committed as separate research/history records rather than as part of this final UI release.

No files were deleted automatically, no database write was performed, and no commit, push, or deployment occurred.

READY_TO_COMMIT
