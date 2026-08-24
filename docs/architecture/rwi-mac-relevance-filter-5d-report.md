# RWI Controlled Live Pilot 5D — MAC Relevance Filter Real-World Calibration

Status: IMPLEMENTED, ADVERSARIALLY REVIEWED, COMMITTED. §8 below documents the review's own independent findings and the one strengthening addition it made before commit.

## 1. Problem

Controlled Live Pilot 5C's bounded live reconnaissance (20 MAC meetings, Feb 17 – Aug 17, 2026, 436 real agenda items) found that the production relevance filter (`app.acquisition.mac_granicus.RELEVANT_KEYWORDS`/`is_relevant_title`, duplicated identically in `app.acquisition.mac_granicus_extractor.RELEVANT_KEYWORDS`/`is_relevant_text`) matched **zero** of the 436 titles — despite 18 of them being unambiguously about runway reconstruction/rehabilitation/replacement work by ordinary human reading.

Root cause, independently confirmed: the filter's 9 keyword phrases require an exact contiguous substring (e.g. `"runway reconstruction"`), but MAC's real title convention is always `"Runway <designation> <work type>"` (e.g. *"Runway 14-32 Reconstruction"*, *"Runway 9-27 Edge Lighting and PAPI Replacement"*) — the designation always sits between "runway" and the work word, so the required phrase never appears verbatim.

## 2. Design

Split the existing 9-phrase vocabulary into two groups, matched differently:

- **4 standalone phrases** (`emas`, `engineered material arresting`, `arresting system`, `runway safety area`) — unchanged, still matched as a raw substring anywhere in the text.
- **5 single-word work concepts** (`reconstruction`, `rehabilitation`, `replacement`, `resurfacing`, `repair`) — now matched **structurally**: the text is tokenized (`\w+`, case-folded), and a match requires some occurrence of the token `"runway"` whose *immediately following* token is either the work concept itself (the original zero-gap case) or a designation-shaped token (`^\d{1,2}[lrc]?$` — one or two digits with an optional L/R/C suffix, e.g. `"14"`, `"09"`, `"9r"`). If it's a designation, a qualifying work concept must then appear within `_MAX_RUNWAY_WORK_GAP_TOKENS = 8` tokens after it.

**Why the adjacency requirement, not just a distance bound.** A pure "runway and a work word within N tokens of each other" rule would match a genuine false positive: *"Parking-Ramp Reconstruction With an Unrelated Runway Reference Elsewhere"* — "reconstruction" and "runway" are only 3 tokens apart there, well within any reasonable bound. Requiring the token immediately after "runway" to be either the work concept or a designation shape defeats this: "reference" is neither, so the match correctly fails. This was verified by direct construction and test (`test_is_relevant_title_recognizes_runway_designation_between_runway_and_work_concept`).

**Why 8 tokens.** The widest real gap in the 5C corpus — *"Runway 9-27 Edge Lighting and PAPI Replacement"* — is 6 tokens between "runway" and "replacement" (9, 27, edge, lighting, and, papi). 8 gives a small, explicit margin without approaching "unbounded." The bound is directly tested at its exact edge (`test_is_relevant_title_gap_boundary_is_enforced`): a title with exactly 8 intervening tokens matches; one with 9 does not.

**Runway numbers are never hardcoded** — `_DESIGNATION_TOKEN` is a structural shape pattern (`\d{1,2}[lrc]?`), not a list of specific designations. No airport name, runway number, or MAC-specific identity string appears anywhere in the matching logic itself.

## 3. Scope

Two production files touched, both pre-existing, both already independent (zero-cross-import) copies of the same vocabulary by deliberate, documented repository convention:

- `app/acquisition/mac_granicus.py` — `is_relevant_title()` and its supporting constants/helpers.
- `app/acquisition/mac_granicus_extractor.py` — `is_relevant_text()` and an identical, independently-written copy of the same logic.

The pre-existing duplication between these two files was **not** collapsed into a shared module. That duplication is itself a deliberate, explicitly-documented architectural decision (extraction modules never import their own provider module — see both files' own comments), and collapsing it would be exactly the kind of unrelated refactoring this mission's scope firewall prohibits. Both copies were fixed identically and independently tested for consistency.

No other file was touched. `scripts/capture_mac_discovery.py`, UAC3, UAC4, EB1–EB5, EvidenceBag semantics, airport matching, CandidateFragment semantics, SourceAssertion persistence, UnknownAirportCandidate persistence, the identity guard, intelligence review, promotion policy, Signal creation, publishing, and the database schema/migrations are all untouched — confirmed by `git status`.

## 4. Taxiway finding

`taxiway` does not appear anywhere in the current relevance vocabulary or design, in either file. Per the mission's own instruction, this was **not** added — the current code/design does not treat taxiway mentions as relevant, so none was introduced here. Reported as a separate, standalone finding: several real 5C titles describe taxiway-specific work (e.g. *"Taxiway R Pavement Reconstruction"*) that stays — correctly, under the current design's own scope — outside this filter's relevance vocabulary. Whether taxiway work should ever become in-scope is a product/design decision for a future, separately-authorized mission, not something inferred here.

## 5. Real-world regression corpus

Both test files (`tests/test_mac_granicus_provider.py`, `tests/test_mac_granicus_extractor.py`) were extended with a small, explainable, real-world-anchored corpus — titles marked "(5C real title)" were observed live during Pilot 5C's own reconnaissance, not fabricated. No live network is used by any test; the corpus is static.

## 6. Live verification (optional, performed once)

Per the mission's allowance for a single, non-tuning, preview-only live check: one bounded live preview was run against `--historical-meeting-clip-id 2559` (Jul 7, 2026 PD&E committee meeting) — a target already identified, by clip_id, during Pilot 5C's own prior reconnaissance, not newly searched for in this mission. Command:

```
python -m scripts.capture_mac_discovery --database data/runway_safe.db --allow-live-network --max-recent-meetings 0 --historical-meeting-clip-id 2559
```

Result: item 3.1, *"Bids Received with Capital Improvement Program Adjustment - MAC Contracts - 2026 Anoka County-Blaine Airport Runway 18-36 Pavement Reconstruction and Electrical Vault Improvements..."*, was correctly flagged relevant, fetched (974,025 real bytes), and extracted into a genuine `CandidateFragment` with `runway_ends=["18","36"]`, `runway_pairs=["18/36"]`, `issuers=["Metropolitan Airports Commission"]`. The identity guard correctly evaluated this against five real candidate airports (topology-matched on the very common "18/36" heading, none of them MSP) and returned `REVIEW_REQUIRED` — ambiguous across four independently-qualifying airports — never assuming MSP merely because the source is MAC, exactly as the governed identity logic is designed to behave. `would_form_unknown_airport_candidate` was correctly `false` (ambiguity among known candidates is a human-review case, not a new-candidate case). No apply was requested; no database write occurred (verified byte-identical before/after, including mtime).

Observed, out-of-scope, unmodified: `--max-recent-meetings 0` still returned one recent meeting (an existing behavior of `discover_recent_meetings`'s append-then-check-bound loop, unrelated to the relevance filter and not touched by this mission).

## 7. Adversarial self-review

1. **Over-broad matching** — defeated by the immediate-adjacency requirement; proven against the parking-ramp trap case.
2. **Punctuation variants** — tokenization strips all punctuation as separators; tested with commas and colons.
3. **Designation formats** (`14-32`, `14/32`, `09-27`) — all tested explicitly; the structural regex accepts any 1-2 digit + optional L/R/C shape.
4. **Intervening modifiers** (`pavement`, `edge lighting and PAPI`) — tested via the real 5C titles that contain them.
5. **Existing exact-keyword compatibility** — all pre-existing tests (37 in the two files combined) pass unmodified.
6. **Accidental false positives** (terminal/road/generic airport/taxiway) — explicitly tested, including two real 5C titles that must stay irrelevant.
7. **Duplicated relevance logic** — pre-existing and deliberate; preserved, not collapsed; both copies independently tested.
8. **Nondeterminism** — none; pure functions, no randomness, no external state.
9. **Source-specific identity leakage** — none; this change lives entirely in the acquisition/extraction relevance-gate layer, never touches identity/guard/persistence logic.

No defects found requiring correction beyond the design itself.

## 8. Adversarial review findings

An independent adversarial review pass re-derived every claim above from the diff and fresh code reading, importing and calling the actual production functions rather than reasoning abstractly or trusting this report.

**Logic verdict: sound, no code defect found.** A 41-case attack matrix — every designation-suffix format named in the review mission (`14-32`, `14/32`, `09-27`, `9/27`, `4L-22R`, `04L/22R`, `18R`, `36L`), heavier intervening-modifier variants, the full false-positive list (including inspection/maintenance/snow-removal work-adjacent-but-unapproved-vocabulary cases), all 5 individual work concepts as bare phrases, tokenization robustness (en/em dash, repeated whitespace, parentheses, colon+ampersand, non-breaking space, mixed case), and the taxiway boundary — was run directly against the real `is_relevant_title`/`is_relevant_text` functions. **All 41 cases passed on the first run**, including the L/R/C-suffixed designations (`4L-22R`, `18R`, `36L`) that were not part of the original implementation's own test corpus — the `_DESIGNATION_TOKEN` regex (`^\d{1,2}[lrc]?$`) already handled them correctly by construction; this was a genuine gap in test *coverage*, not in the matching *logic*.

**Test-quality gap found and closed.** None of the L/R/C-suffix cases, several of the false-positive variants (inspection/maintenance/snow-removal), the individual bare-phrase forms for all 5 work concepts, or a genuine acquisition/extractor parity proof existed as permanent regression tests before this review — exactly the risks the review mission's own Test Quality section named ("no suffix-designation coverage," "no acquisition/extractor parity proof"). Closed by adding `tests/test_mac_relevance_parity.py`: a single shared 42-case corpus run through *both* independent implementations in one assertion, proving parity rather than assuming it from shared authorship.

**Live corpus replay performed (mission §15, judged useful rather than skipped).** One additional bounded, read-only reconnaissance was run against the same 20-meeting/436-item corpus from Pilot 5C, computing old-rule and new-rule match counts side by side (no apply, no DB write, no tuning — the rule was not touched based on this result). Old rule: 0 matches (reproducing 5C exactly). New rule: **5 matches**, all genuine runway-reconstruction/replacement titles, including one (clip_id 2529, *"Capital Improvement Program Adjustment - 2026 Anoka County-Blaine Airport Runway 18-36 Pavement Reconstruction"*) not previously surfaced in this session. **Zero regressions**: nothing the old rule matched was missed by the new rule (checked explicitly, not merely assumed). Zero suspicious false positives among the 5 real matches.

**Source-specific safety verdict: clean.** No occurrence of `MSP`, `STP`, `Anoka`, or any FAA/IATA/ICAO code anywhere in the production matching logic — confirmed by direct inspection of the full diff, not merely by design intent.

**Firewall verdict: clean.** `git status` confirms only `app/acquisition/mac_granicus.py`, `app/acquisition/mac_granicus_extractor.py`, three test files, and this report are touched — no UAC3, UAC4, EB1–EB5, EvidenceBag, identity guard, persistence, promotion, Signal, publishing, or schema/migration file anywhere in the change.

No correction to the production matching logic itself was needed — the one substantive addition was test coverage, not a behavior change.
