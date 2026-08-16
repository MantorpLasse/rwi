# Public UI visual redesign report

## Outcome

The public site now uses an editorial aviation-intelligence presentation rather than a repeated-card application shell. The redesign is visual and compositional only: public content, evidence boundaries, disclosures, signals, filters, exports, and domain semantics are unchanged.

## Design diagnosis and principles

The pre-redesign presentation was safe and information-rich, but repeated bordered surfaces, chips, and uniform panels diluted hierarchy. This pass applies four principles:

- Lead with typography, spacing, and restrained rules rather than containers.
- Use a dark aviation-oriented editorial identity, with gold as a precise navigation and signal accent.
- Keep structured evidence legible and explicitly qualified; do not make it look like generic UI metadata.
- Preserve the information architecture and make mobile a deliberate single-column reading flow.

## Major layout changes

- The dashboard hero image and its visual container were removed. The hero is now typographic, with a small abstract runway motif that makes no factual or documentary claim.
- Dashboard metrics, updates, timeline, and selected signals use section rules and rhythm instead of floating card treatment.
- Airport pages read as a profile: a strong airport identifier and title lead into current status, projects, timeline, historical disclosure, and a quiet runway rail.
- The Signals index remains filterable and dense, but its table is flatter, with clearer signal-title emphasis and quieter supporting metadata.
- Signal detail uses the same brief-like hierarchy: identity and assessment lead, followed by details, economy, and source provenance.
- Small screens use a single-column profile flow; the runway rail follows the evidence rather than forcing a cramped side-by-side layout.

## Files changed in this pass

- `app/static_export/templates/index.html`
- `app/static_export/static/style.css`
- `docs/ui/public-ui-visual-redesign-plan.md`
- `docs/ui/screenshots/visual-redesign/`

The working tree also contains the accepted, earlier public-UI Slice work on shared export/templates/tests. It was preserved and not redesigned beyond the visual presentation layer.

## Screenshots

Desktop:

- `docs/ui/screenshots/visual-redesign/desktop-dashboard.png`
- `docs/ui/screenshots/visual-redesign/desktop-bgm.png`
- `docs/ui/screenshots/visual-redesign/desktop-cgf.png`
- `docs/ui/screenshots/visual-redesign/desktop-mdw.png`
- `docs/ui/screenshots/visual-redesign/desktop-signals.png`
- `docs/ui/screenshots/visual-redesign/desktop-signal-detail.png`

Mobile:

- `docs/ui/screenshots/visual-redesign/mobile-dashboard.png`
- `docs/ui/screenshots/visual-redesign/mobile-bgm.png`
- `docs/ui/screenshots/visual-redesign/mobile-mdw.png`
- `docs/ui/screenshots/visual-redesign/mobile-signals.png`

## Semantic and data-safety verification

- No database operation was performed.
- No evidence, reconciliation, installation, Signal, project-state, freshness, localization, or disclosure semantics were changed.
- The generated `site/data.json` contains no `SourceAssertion`, physical-installation-identity, or reconciliation-link fields.
- Non-public Signals 52 and 54 remain absent from the public export.
- All 157 generated HTML pages have valid local asset/page references.

## Visual self-critique against pre-deploy screenshots

The hierarchy is now materially clearer: the dashboard identifies the product before showing its measures, airport identity is more immediate, and the Signal inventory reads as a ranked intelligence register rather than a collection of boxed widgets. The redesign reduces visual noise without hiding qualification, source, or historical material. Mobile retains intentional grouping and does not horizontally overflow.

The Signals index is necessarily long because it presents the complete public register; it remains a table-like comparison surface by design. That density is functional and was preserved rather than replaced with lossy visual cards.

## Verification

- Focused static/export tests: **16 passed**.
- Python compilation: passed.
- Full test suite: **362 passed** (25.82s).
- Warnings: existing FastAPI/SQLAlchemy deprecation warnings; none are caused by this visual slice.
- Static export: passed.
- Generated-site link verification: **157 pages**, no broken local references.
- Public-boundary verification: passed.
- `git diff --check`: passed.

No commit, push, deployment, database write, or domain/reconciliation work was performed.

READY_FOR_HUMAN_VISUAL_REVIEW
