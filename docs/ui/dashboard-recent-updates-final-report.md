# Dashboard "Senast uppdaterat" final relevance/volume pass

Narrow, dashboard-only change. Airport pages, runway presentation, Signal
detail, the Signals register, database/domain models, evidence architecture,
and reconciliation semantics were not touched.

## 1. Relevance logic retained unchanged

`app/static_export/build.py`'s `_recent_changes_view` still applies exactly
the same Slice 2B rules as before; only its `limit` default changed (see
section 2). Unchanged behavior:

- Source is `signal_views` only — the function still never reads
  installations or incidents (the `airport_views` parameter is accepted and
  immediately discarded, as before), so historical Incident rows and legacy
  Installation-row touches cannot appear.
- A Signal is skipped unless it has a governed `source_published_date`
  (`source_date is None` -> excluded) — no database `updated_at` is read
  anywhere in this function.
- `status == "identified"` is excluded (watch-only noise).
- "Active" requires `status` in `{funded, design, procurement, under
  construction, environmental_review, master_plan, alp, cip}` **and** either
  a source published within the last 365 days or an upcoming
  `target_year`/`planning_year`.
- "Completed" requires a source published within the last 365 days.
- Sort order is unchanged: `(evidence_date, id)` descending.
- The displayed date is still the governed `source_published_date`
  (`date_label = e.evidence_date.isoformat()`), never a row-touch timestamp.
- No AI ranking, no new status/category semantics were introduced.

## 2. Display-limit change

`_recent_changes_view(..., limit: int = 8, ...)` -> `limit: int = 5`. This is
the function's only default-parameter change — a display cap, not a filter
change. Nothing about which Signals qualify was touched; a database with
more than 5 qualifying Signals now simply shows fewer of them on the
dashboard.

Added a new default-parameter test,
`test_dashboard_feed_default_limit_is_five` (`tests/test_static_presentation.py`),
which feeds 7 qualifying signals and asserts exactly 5 are returned, most
recent first.

## 3. Desktop table

Unchanged from the prior corrected layout: `.intelligence-head`/
`.intelligence-item` share one 4-column CSS Grid
(`grid-template-columns:minmax(120px,.45fr) minmax(180px,.7fr)
minmax(0,1fr) minmax(88px,max-content)`), so `TYP | FLYGPLATS | BESKRIVNING
| DATUM` headers stay pixel-aligned with the corresponding row cells,
`BESKRIVNING` remains the flexible column, rows keep subtle
`border-bottom` separators, and there is no card conversion. No CSS for this
block was touched in this task; the row count shrinking from 8 to 5 is the
only visible change, confirmed in the desktop screenshot.

## 4. Mobile

Unchanged: the `max-width:700px` media query still sets `.intelligence-head
{ display:none; }` and keeps the existing 2-row stacked-card layout per
item. With only 5 items instead of 8, the feed is visibly shorter and no
longer dominates the page (see mobile screenshot). No horizontal overflow.

## 5. "Visa alla uppdateringar" link

Added directly below the 5 rendered items (`app/static_export/templates/index.html`):

```html
<div class="card-body" style="padding-top:14px"><a class="card-action" href="{{ root }}/signals/index.html">{{ t("view_all_updates") }} →</a></div>
```

- No new page or route was created — it links to the existing public
  Signals register (`/signals/index.html`), the closest existing view to a
  full intelligence/updates log, same target the existing "Utvalda
  signaler" -> "Se alla signaler" link already uses.
- The label text was added to the centralized presentation/localization
  layer, `app/static_export/presentation.py`'s `LOCALES` dict, as a new
  `view_all_updates` key (`"Visa alla uppdateringar"` for `sv`, `"View all
  updates"` for `en`), read through the existing `t()` Jinja global — not
  hardcoded in the template.

## 6. Utvalda signaler

Not touched. `build.py`'s `top_signals=signal_views[:5]` was already 5
before this task and remains unchanged; no ranking, content, or
presentation edits were made to that card.

## 7. Actual feed content (fresh query against the live development database)

Ran `_recent_changes_view` directly against a freshly loaded `signal_views`
list from `data/runway_safe.db` (read-only). The 5 items it currently
produces, most recent first:

| # | Airport | Type/status label | Description | Governed date |
|---|---|---|---|---|
| 1 | SFO — San Francisco International Airport | Underhåll (maintenance) | Runway 1R/19L EMAS seam replacement | 2026-07-01 |
| 2 | BOS — Boston Logan International Airport | Ny installation | Runway 9/27 RSA and EMAS phase 2 | 2026-07-01 |
| 3 | MHT — Manchester-Boston Regional Airport | Ersättning (replacement) | Runway 6 departure-end EMAS replacement | 2026-06-01 |
| 4 | FTY — Fulton County Executive Airport | Ny installation | Runway 8/26 EMAS safety improvements | 2026-04-01 |
| 5 | WLG — Wellington International Airport | Ny installation | Wellington EMAS-order (Runway Safe bekräftad leverantör) | 2026-03-25 |

Underlying `signals` table rows confirmed for each (id / status / category / governed `sources.published_date`):

- id 4, status `under construction`, category `maintenance`, source published `2026-07-01` ("FAA Airport Construction Impact Report")
- id 3, status `under construction`, category `new_installation`, source published `2026-07-01` ("FAA Airport Construction Impact Report")
- id 2, status `procurement`, category `replacement`, source published `2026-06-01` ("Runway 6 Departure End EMAS Project")
- id 5, status `design`, category `new_installation`, source published `2026-04-01` (Fulton County draft Environmental Assessment)
- id 65, status `completed`, category `new_installation`, source published `2026-03-25` ("EMAS - new runway buffer zones")

Confirmation against the exclusion rules:

- **None is an imported historical incident.** `_recent_changes_view` only ever emits `kind="signal"` entries built from `signal_views`; it does not read `airport.incidents` at all (the `airport_views` argument is discarded unused). No Incident row can appear here by construction.
- **None is an `identified` watch-only record.** Confirmed statuses above are `under construction` (x2), `procurement`, `design`, `completed` — none is `identified`.
- **None is an old database-touch masquerading as new intelligence.** Each row's displayed date is the governed `sources.published_date` queried directly from the `sources` table, not any `created_at`/`updated_at` column; the function never reads those columns.

## 8. Screenshots

Regenerated the static site from scratch (`build_site()` against the live
database) and captured fresh screenshots with Playwright:

- `docs/ui/screenshots/recent-updates-final/desktop-dashboard.png` (1440x1000 viewport, full page)
- `docs/ui/screenshots/recent-updates-final/mobile-dashboard.png` (390x844 viewport, full page)

Verified by inspecting both images:

- Exactly 5 rows under "Senast uppdaterat" on both viewports.
- Desktop: `TYP`/`FLYGPLATS`/`BESKRIVNING`/`DATUM` headers align with the corresponding column content in every row; row separators are subtle single lines; no card conversion.
- Desktop: `document.documentElement.scrollWidth > clientWidth` is `false` (no horizontal overflow).
- Mobile: header row is hidden, each of the 5 updates renders as the existing compact stacked record, `document.documentElement.scrollWidth > clientWidth` is `false`, and the feed is visibly shorter than before — it no longer dominates the page above the chart and "Utvalda signaler".
- No layout regression elsewhere: KPI row, "EMAS-installationer över tid" chart, and "Utvalda signaler" (still 5 rows) are all unchanged and correctly positioned on both viewports.

## 9. Tests and verification

- Focused static/UI tests (`tests/test_static_export.py`, `tests/test_static_presentation.py`): **17 passed** (16 prior + 1 new default-limit test).
- Full suite: **363 passed** (362 prior + 1 new test).
- Python compilation (`build.py`, `presentation.py`): passed.
- Static export: passed, fresh `build_site()` run produced 157 HTML pages.
- Internal-link check (fresh custom scan of the regenerated site): **0 broken local references**.
- Public-boundary check: `data.json` scan clean of `SourceAssertion`/physical-identity/reconciliation/manual-year-estimate/source-note/review-actor fields; BGM's `Aktuell EMAS-status ej verifierad` protection still present.
- Horizontal-overflow check, desktop and mobile dashboard: both `false`.
- `git diff --check`: passed (only benign LF→CRLF notices).

## Files changed

- `app/static_export/build.py` — `_recent_changes_view` default `limit` 8 -> 5.
- `app/static_export/presentation.py` — added `view_all_updates` to both locales.
- `app/static_export/templates/index.html` — added the "Visa alla uppdateringar →" link below the feed.
- `tests/test_static_presentation.py` — added `test_dashboard_feed_default_limit_is_five`.
- `docs/ui/dashboard-recent-updates-final-report.md` — this report.
- `docs/ui/screenshots/recent-updates-final/desktop-dashboard.png`, `mobile-dashboard.png` — new.

(`app/static_export/static/style.css`, `app/static_export/templates/airport_detail.html`, and `tests/test_static_export.py` also show as modified in `git status`, but those changes are carried over from the separate, already-reported runway-inventory-suppression task and were not touched again here.)

## git status

```
 M app/static_export/build.py
 M app/static_export/presentation.py
 M app/static_export/static/style.css
 M app/static_export/templates/airport_detail.html
 M app/static_export/templates/index.html
 M tests/test_static_export.py
 M tests/test_static_presentation.py
?? docs/research/
?? docs/ui/mdw-runway-diagnosis.md
?? docs/ui/public-ui-hero-integration-report.md
?? docs/ui/public-ui-regression-fix-report.md
?? docs/ui/screenshots/... (multiple prior review folders, plus new recent-updates-final/)
```

Nothing staged, nothing committed, nothing pushed, no database write performed.

READY_FOR_HUMAN_DASHBOARD_REVIEW
