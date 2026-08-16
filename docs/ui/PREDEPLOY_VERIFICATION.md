# Pre-deploy verification

**Verdict: READY_FOR_HUMAN_REVIEW**

No further implementation is required before human screenshot review. No
database write, commit, push, or deployment was performed.

## Static site and core pages

- `site/` regenerated successfully through the normal export command.
- 157 HTML pages were generated.
- Lightweight local link check: 2,117 internal links, **0 broken** targets;
  215 governed external links were retained as external URLs.
- Dashboard, Signals list, BGM/CGF/MDW airport pages, BGM Signal 6, completed
  Signal 65, glossary, and about/disclaimer pages are generated and linked.

## Dashboard and airport checks

- Hero image exists in the generated site with factual aircraft-in-EMAS-bed
  alt text and no documentary/source claim.
- `Senast uppdaterat` has 8 items; historic incidents are excluded.
- Selected Signal preview is bounded at 5 and links to the full Signals view.
- Chart/timeline markup renders.
- CGF publishes reviewed ends 06 and 24; MDW publishes 04R, 13L, 22L, 31R.
- Historical-installation disclosures are collapsed by default.
- BGM says “Aktuell EMAS-status ej verifierad”, preserves the Signal 6
  under-review qualification, makes no presence/absence or
  construction/completion claim, and retains five grants behind disclosure.

## Public-data safety

- Generated Signal JSON has no `notes`, `manual_year_estimate`,
  `source_notes`, SourceAssertion/reconciliation keys, actor/reason, or raw
  evidence locator/hash keys.
- Signals 52 and 54 are absent from the normal public Signal export.
- No local filesystem paths or internal audit classifications were found in
  the generated public Signal projection.

## Responsive/accessibility sanity check

Playwright rendered the required desktop/mobile pages with no horizontal
overflow. BGM and MDW history controls accepted keyboard focus. This is a
practical sanity check, not WCAG certification.

## Screenshots

`docs/ui/screenshots/predeploy/` contains all requested full-page captures:

- desktop-dashboard.png
- desktop-bgm.png
- desktop-cgf.png
- desktop-mdw.png
- desktop-signals.png
- desktop-signal-detail.png
- mobile-dashboard.png
- mobile-bgm.png
- mobile-mdw.png

## Checks

- Focused static/UI tests: **16 passed**.
- Full suite: **362 passed**.
- Python compilation: passed.
- `git diff --check`: passed (existing CRLF conversion warnings only).

## Working tree and release content

- Branch: `foundation/evidence-identity`.
- The working tree contains the accumulated uncommitted Slice 1–3B and hero
  integration changes, associated tests/docs, image asset, and screenshots.
- `site/` is ignored by `.gitignore` and has zero tracked files; it is an
  expected generated deployment artifact, not a production-commit input under
  current repository practice.
- Screenshot directories are human-review artifacts. Include them in a
  production commit only if the project intentionally keeps visual-review
  evidence in version control; they are not needed to serve the generated
  site.
