# Public UI Slice 1 Report: Signal Hierarchy and Localization Foundation

## Result

Slice 1 refines public signal hierarchy without changing domain data, database
schema, static-site architecture, aviation visual identity, or the private
evidence boundary. The Swedish public site now presents **Status** before
**Project type** wherever signal rows were changed.

## Files changed

- `app/static_export/presentation.py`: central public Swedish/English-ready
  strings and governed Signal status presentation mapping.
- `app/static_export/build.py`: uses the centralized status/source presentation
  mapping and provides `t()` to templates.
- Static-export base, signal list/detail, dashboard, airport detail, component,
  and CSS files: hierarchy, deterministic airport navigation, status roles,
  keyboard focus styling, and score de-emphasis.
- `tests/test_static_presentation.py`; updated static-export fallback test.
- Regenerated `site/` output.

## Presentation architecture

Database values remain unchanged and language-neutral. `status_view()` supplies
a Swedish label and semantic role for known statuses, with English labels ready
for later use but no language switch/pages. Unknown statuses render a readable
fallback with the neutral `unknown` role; no lifecycle meaning is invented.
`text()` centralizes initial shared navigation and hierarchy strings. Category
presentation remains centralized in the existing build mapping.

## Hierarchy changes

- Status is now the first signal state column on dashboard, signal-list, and
  airport-detail tables.
- Category is labelled **Projekttyp** and appears second.
- Signal detail leads with status and explicitly prefixes the category as
  “Projekttyp”.
- Completed signals use the existing restrained green completed treatment, so
  they remain historical intelligence rather than reading as active opportunity.
- Active statuses retain readable status pills; unknown statuses fail safely.
- Numeric score remains available but is reduced on detail pages using the
  secondary-score treatment and an explanatory title. Confidence remains clear
  but secondary to status.

## Navigation and accessibility

Airport detail now retains the deterministic “Airports” breadcrumb and removes
the browser-history-dependent Back button. Existing responsive stacked-table
behavior remains. Added visible `:focus-visible` outlines for links, buttons,
inputs, and selects. Status text, project-type text, and confidence labels avoid
colour-only meaning.

## Source type consistency

Mapped source types retain their glossary-aware presentation. Unknown legacy
source types now render as the generic human-readable **Övrig källa** rather
than exposing a raw implementation-style database value. This does not claim a
more specific source meaning.

## Before / after examples

| Case | Before | After |
|---|---|---|
| Completed new-installation signal | Category and completed status appeared as competing badges/fields. | Status `Färdigställd` is primary; `Projekttyp: Ny installation` explains the historical project type. |
| Active signal | Category could appear before/no status in list context. | Current governed status is shown first, followed by project type. |
| Confidence/score signal | Gauge and numeric score could compete with state. | Status precedes both; detail score is visually secondary. |
| Grouped signal list | Group row had category/confidence emphasis and no usable status filter. | Top signal status is displayed and participates in status filtering. |
| Airport-detail signal | Category preceded status. | Status precedes labelled project type. |

## Export and safety

The normal static export completed. Public `site/data.json` continues to omit
private `Signal.notes`, manual estimates, SourceAssertion data, reconciliation
links, and PhysicalInstallationIdentity data. Existing URLs and static paths
remain unchanged.

## Verification

- Focused static-export/presentation tests: **15 passed**.
- Full suite: **passed** after the final template update.
- Python compilation: passed.
- Static export: passed.
- `git diff --check`: passed.

## Remaining UI issues

- Swedish public text is only partially centralized; a later localization slice
must move remaining headings, empty states, chart copy, and JavaScript strings.
- The airport detail still presents legacy Installation rows; reviewed physical
identity UI remains intentionally out of scope.
- No public map is implemented.
- Timeline/recent-update presentation can receive a separate hierarchy pass.

## Human visual review

Start a local static server from the repository root, then open the printed
local URL in a browser:

```powershell
.\.venv\Scripts\python.exe -m http.server 8000 --directory site
```

Review `/index.html`, `/signals/index.html`, a completed signal page, an active
signal page, and an airport detail at desktop and narrow viewport before
accepting the slice.
