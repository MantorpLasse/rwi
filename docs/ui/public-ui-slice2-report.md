# Public UI Slice 2 report

## Outcome

Airport pages now separate public current EMAS presence from project/watch Signals and historical installation records. The work uses only existing reviewed physical identities and the preserved FAA NASR cycle evidence; it makes no database or evidence-architecture change.

## Public behavior

- **Current EMAS presence:** reviewed identities publish only runway-end labels. CGF shows 06/24; MDW shows 04R/13L/22L/31R. No identity ID, assertion/link ID, reviewer, reason, locator, vendor, lifecycle, or continuity assertion is exported.
- **FAA presence:** where preserved NASR source assertions exist, the page may show FAA-reported runway-end presence and its NASR cycle. This is explicitly limited to current runway-end presence and does not represent a project, installation date, vendor, replacement, or physical reconciliation.
- **Projects and watch items:** project Signals appear before historical installation records. `identified` is translated to “Bevakas – forskningskandidat”, not construction, funding, completion, or installation.
- **BGM:** Signal 6 is publicly contained as “Projektuppgift under granskning” with an explicit statement that it does not establish construction or completion. The stored Signal is unchanged.
- **Signals 52 and 54:** these two baseline-identified Signals are excluded from normal public static export because their Airport records lack ICAO normalization. The database rows remain unchanged.
- **Sources:** public Signal details retain governed Source title, publisher, type, date where present, and governed URL. Raw `source_notes` are retained in the database but no longer rendered or emitted in `data.json`, preventing raw URLs and research-log/cross-reference prose from dominating public presentation.
- **Legacy Installations:** retained under “Historiska installationsuppgifter”, after current presence and project/watch information.

## Files changed

- `app/static_export/presentation.py`
- `app/static_export/build.py`
- `app/static_export/templates/airport_detail.html`
- `app/static_export/templates/signal_detail.html`
- `tests/test_static_export.py`
- `docs/ui/public-ui-slice2-report.md`

## Verification

- Focused static presentation/export tests: **15 passed**.
- Python compilation: **passed**.
- Full suite: **361 passed** (existing deprecation warnings only).
- Static site regenerated with `python -m scripts.export_static_site --output site`.
- Export spot checks:
  - CGF: reviewed ends `06`, `24`.
  - MDW: reviewed ends `04R`, `13L`, `22L`, `31R`.
  - BGM: public containment label present; no construction/completion claim.
  - Signals 52 and 54 absent from public `data.json`.
  - Signal raw `source_notes`, private `notes`, and `manual_year_estimate` absent from the Signal public projection.

## Local visual review

Start a local static server from the repository root:

```powershell
.\.venv\Scripts\python.exe -m http.server 8000 --directory site
```

Inspect:

- `http://localhost:8000/` (dashboard)
- `http://localhost:8000/airports/57.html` (CGF)
- `http://localhost:8000/airports/12.html` (MDW)
- `http://localhost:8000/airports/6.html` (BGM)
- `http://localhost:8000/signals/6.html` (BGM containment)
- `http://localhost:8000/signals/` (identified/watch presentation)

No commit, push, or deployment was performed.

## Slice 2B: dashboard relevance filter

`Senast uppdaterat` is now a bounded feed of **at most eight** meaningful
project/evidence updates rather than a raw record-change log.

Deterministic rules:

- Only public Signals with a governed Source publication date can appear.
- Historical Incident rows and legacy Installation row updates are excluded.
- `identified` research/watch Signals are excluded.
- Active project statuses (`funded`, design, procurement, construction,
  environmental review, master plan, ALP, CIP) require either a source date
  within the preceding 365 days or a current/upcoming target/planning year.
- Completed work requires a source date within the preceding 365 days.
- Ordering is governed source date descending, then Signal ID; the displayed
  date is the source publication date, never database `updated_at`.

The current exported feed has eight examples: SFO maintenance, BOS phase 2,
MHT replacement procurement, FTY design, WLG completion, JFK replacement
design, MKC study, and STP replacement planning. Historic incident imports,
old grant-only records without current progression, and watch-only items are
intentionally absent.

Slice 2B verification: focused tests **16 passed**; full suite **362 passed**;
Python compilation, static export, and `git diff --check` passed. Existing
dependency deprecation warnings remain unchanged.
