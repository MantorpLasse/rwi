# Public UI regression fix report

## TODO

Public runway inventory is intentionally suppressed until canonical runway
and runway-end coverage is governed and sufficiently complete. The
airport-detail "Banor" section was removed as a presentation-only safety
correction after `docs/ui/mdw-runway-diagnosis.md` found the underlying
`runways` table is a non-exhaustive, one-row-per-airport placeholder (no
airport has more than one row) that could misleadingly imply a complete
runway inventory next to the governed "EMAS idag" reviewed-ends list.
Restore once there is a governed canonical runway/runway-end inventory.

## Scope

This was a targeted dashboard correction only. Airport profiles, Signal detail,
the Signals register, charts, selected Signals, metrics, disclosures, public
data rules, and all domain/evidence behavior remain unchanged.

## Changes

- Removed the forced line break and narrow title constraint from the dashboard
  product title. At the 1440 px desktop review viewport, `Runway Safe
  Intelligence` is one confident line; at the 390 px mobile viewport it wraps
  naturally without overflow.
- Reworked `Senast uppdaterat` into a restrained, structured register. Desktop
  headings are `TYP`, `FLYGPLATS`, `BESKRIVNING`, and `DATUM`; rows use subtle
  separators rather than cards.
- On mobile, each update is a compact record ordered as airport/type,
  description, then governed evidence/source date.
- Follow-up density pass (dashboard only): the hero-to-KPI gap measured 84px
  versus the ~38-42px rhythm used everywhere else on the page (subtitle
  margin-bottom 28px + hero-copy padding-bottom 18px + hero-intelligence
  margin-bottom 38px, stacked). Tightened to 8+10px+26px margins/padding via
  the dashboard-scoped `.hero-intelligence`/`.hero-copy`/`.hero-copy
  .subtitle` selectors only, bringing the gap to ~50px and putting the hero
  in the same rhythm as the KPI-to-table and table-to-chart gaps. No other
  page uses these selectors, so airport/Signals/Signal-detail pages are
  unaffected (confirmed by grep and by fresh overflow checks).

The existing bounded/relevance-filtered `recent_changes` projection was not
modified: it remains limited to eight entries, excludes historical incidents
and identified/watch-only noise, and uses governed source dates rather than
database row timestamps.

## Files changed

- `app/static_export/templates/index.html`
- `app/static_export/static/style.css`
- `docs/ui/screenshots/regression-fix/desktop-dashboard.png`
- `docs/ui/screenshots/regression-fix/mobile-dashboard.png`
- `docs/ui/screenshots/final-polish/desktop-dashboard.png`
- `docs/ui/screenshots/final-polish/mobile-dashboard.png`
- `docs/ui/public-ui-regression-fix-report.md`

## Visual review

- Desktop title: one line; no hero art and no excessive empty right-hand area.
- Mobile title: intentionally wraps and remains compact.
- Desktop recent updates: clearly scannable by type, airport, description, and
  date.
- Mobile recent updates: readable as compact structured records, with no
  four-column squeeze or horizontal overflow.
- BGM, CGF, and MDW were freshly rendered for regression checks (desktop and
  mobile viewports); their airport-profile layouts had no horizontal overflow
  or unexpected dashboard-driven change.
- All measurements and screenshots in this report were independently
  reproduced from a from-scratch `build_site()` export rendered with
  Playwright, not carried over from an earlier run.

## Semantic and public-data checks

- BGM retains `Aktuell EMAS-status ej verifierad`.
- Signal 6 remains `Projektuppgift under granskning` with its no-construction/
  no-completion qualification.
- CGF retains reviewed ends 06 and 24; MDW retains 04R, 13L, 22L, and 31R.
- Historical installation disclosures remain collapsed by default.
- Signals 52 and 54 remain excluded from the public export.
- Dashboard output has exactly 8 recent updates and 5 selected Signals.
- Generated public data excludes SourceAssertion, physical-identity,
  reconciliation, manual-year-estimate, raw source-note, and review actor/
  reason fields.

## Verification

- Focused static/UI tests: **16 passed**.
- Full suite: **362 passed**.
- Python compilation: passed.
- Static export: passed.
- Generated-site link check: **157 pages**, no broken local references.
- Public-boundary and semantic guard check: passed.
- Desktop/mobile dashboard and BGM/MDW overflow checks: passed.
- `git diff --check`: passed.

No database write, commit, push, deployment, or unrelated redesign work was
performed.

READY_FOR_HUMAN_UI_REVIEW
