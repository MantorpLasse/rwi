# MAC Granicus claim extraction — Slice 2 report

Slice 2 of the roadmap in [evidence-to-signal-semantics-design.md](evidence-to-signal-semantics-design.md),
building on the pure claim core from Slice 1
([evidence-claim-semantics-core-report.md](evidence-claim-semantics-core-report.md)).

## 1. Starting HEAD

`fc7eb86784526695d10525305c5f6a29e0bdfec3` ("Add evidence claim semantics core"), branch `main`,
matching origin/main. Baseline full pytest: 851 passed. Verified before any implementation work began
in this task.

## 2. Adapter API

```python
def extract_mac_claims(fragment: CandidateFragment) -> tuple[Claim, ...]
```

in `app/acquisition/mac_granicus_claims.py`. Single argument, single return value, no keyword
parameters, no defaults, no I/O. Matches the task's own suggested conceptual API exactly.

## 3. Source-specific boundary

The module is MAC-Granicus-specific by design: every regex matches wording actually observed in
the real, committed MAC memo template ("sole source procurement with X", "Purchase Order to X",
"under the oversight of X", "has reached its life expectancy", "requires replacement", "FOR ACTION",
"will be bid in {year}"). None of these patterns search for a literal vendor name — the same patterns
would find whatever vendor a differently-worded MAC memo using this template happened to name. This
mirrors the discipline already established in `app/acquisition/mac_granicus_extractor.py`'s own
`_VENDOR_SOLE_SOURCE`/`_VENDOR_PURCHASE_ORDER` patterns.

The OUTPUT is fully generic: every produced object is a plain `Claim`/`FinancialFact`/
`RelationshipFact`/`TemporalContext` from the unmodified Slice 1 core. A future Massport, SFO, or
international extractor produces the identical `Claim` shape from its own, separately written phrase
detection — this module contributes zero shared logic beyond the core itself.

## 4. Why CandidateFragment-only input (and one deliberate deviation from pure field-reuse)

`extract_mac_claims` takes only a `CandidateFragment`, never the vendor-name tuple that
`mac_granicus_extractor.extract_candidate_fragment()` returns alongside it. That tuple is
deliberately NOT part of `CandidateFragment` (confirmed by re-reading
`app/services/discovery_candidate_fragment.py` in full — it has no vendor/relationship field at
all), so it is discarded once the extractor call returns unless the caller threads it through
separately — which the task's own suggested single-argument signature does not.

Consequence: `extract_mac_claims` re-derives vendor names for its RELATIONSHIP claims (C, I) via its
own regex on `fragment.raw_text`, rather than reusing an already-computed vendor list. This is the
one place this module does NOT purely "reuse already-extracted fields" — it is a necessary
consequence of the chosen single-argument API, not an oversight, and is structurally identical in
spirit to `mac_granicus_extractor.py`'s own vendor patterns (same phrase shapes, independently
applied). All money and date extraction, by contrast, is 100% reused from
`fragment.money_values`/`fragment.dates` with zero raw-text re-parsing of numbers or dates.

## 5. Provenance mapping

Every `Claim.provenance` is built by the single private helper `_provenance(fragment, excerpt)`,
which copies `fragment.artifact_identity`, `fragment.source_locator`, and `fragment.fragment_hash`
verbatim and sets `raw_text_excerpt` to the actual matched substring (plus a small amount of
surrounding context for readability) — never the full raw text, and never a paraphrase. Proven by
test (`TestProvenance`): every claim from one fragment carries identical
artifact_identity/source_locator/fragment_hash, every excerpt is strictly shorter than the full raw
text, and every excerpt is a genuine substring of `fragment.raw_text`.

## 6. Real MSP A–I result

Running `extract_mac_claims()` against the real, already-committed fixture
(`tests/fixtures/mac_granicus_emas_procurement_memo_sample.pdf`, via
`mac_granicus_extractor.extract_candidate_fragment()`) produces exactly **7** `Claim` objects:

| # | category | subject (abridged) | financial | temporal | relationship |
|---|---|---|---|---|---|
| A | EXPLICIT_DOCUMENT_FACT | EMAS bed, 12R/30L | — | — | — |
| B | EXPLICIT_DOCUMENT_FACT | EMAS bed, 12R/30L | — | — | — |
| C | RELATIONSHIP | EMAS material procurement | — | — | Runway Safe / `requested_sole_source_vendor` |
| D | PROCEDURAL_REQUEST | advance-deposit PO | $1,590,000 USD / `advance_deposit_purchase_order` | REQUESTED_PENDING_APPROVAL, as_of 2024-08-28 | — |
| F | EXPLICIT_DOCUMENT_FACT | CIP project | $19,000,000 USD / `cip_project_ceiling`, not_established=(contract_value, confirmed_vendor_award_amount, estimated_vendor_revenue) | HISTORICAL_FACT, as_of 2023-12-18 | — |
| H | TEMPORAL_STATEMENT | installation contract | — | PLANNED_FUTURE_ACTION, as_of 2024-08-28, detail "target year 2025" | — |
| I | RELATIONSHIP | installation | — | — | Runway Safe / `installation_oversight` |

Letters E and G are not separate `Claim` objects — per the task's own explicit allowance ("the
implementation may merge or structure claims differently only if the Claim Core requires it and the
semantic distinctions remain exact"), E is D's `.financial` attachment and G is F's
`.financial.not_established` tuple, exactly matching the pattern Slice 1's own report already
established and tested for this identical MSP case.

## 7. Financial extraction

- Reuses `fragment.money_values` (`ExtractedMoney.context_label`) as the sole input signal — no new
  money regex, no re-parsing of `$` amounts from raw text anywhere in this module.
- A small, explicit, documented table (`_CONTEXT_LABEL_TO_SEMANTIC_ROLE`) reconciles two
  independently-evolved vocabularies: the extractor's own `context_label` value `"advance_deposit"`
  is renamed to the claim core's required `semantic_role` `"advance_deposit_purchase_order"`;
  `"cip_project_ceiling"` passes through unchanged (identity mapping, included for explicitness).
  Any `context_label` not in this table passes through unchanged rather than being dropped or
  guessed at, consistent with `FinancialFact.semantic_role` being deliberately free-text.
- Never emits `contract_value`, `vendor_revenue`, or `total_project_cost` — confirmed by
  `test_no_contract_value_or_award_claim_fabricated` and by construction (the table has no entry that
  produces any of those three strings).
- The $19M `FinancialFact.not_established` carries exactly
  `("contract_value", "confirmed_vendor_award_amount", "estimated_vendor_revenue")`, matching claim
  G's requirement that the CIP ceiling not be misread as a confirmed Runway Safe contract value.

## 8. Procedural semantics

`FOR ACTION` detection (`_FOR_ACTION_MARKER`) gates the `temporal.detail` text on claim D, and the
PO-request phrase (`_ADVANCE_DEPOSIT_PO_REQUEST`) is the sole trigger for `ClaimCategory.PROCEDURAL_REQUEST`.
No claim anywhere in this module ever hard-codes the words "approved", "awarded", "executed", or
"completed" onto a still-pending request — claim D and claim C (both describing staff requests
pending Commission authorization) are proven by test never to contain those words. Claim F, which
describes the memo's own genuinely historical, already-approved CIP action, is allowed to use
"approved" — because it is true, explicitly stated, and dated (2023-12-18), not because the module
assumes approval.

## 9. Temporal semantics

- `as_of_date` for every temporal claim comes from `fragment.dates` (`semantic_role == "memo_date"`
  or `"prior_approval_date"`), never from a freshly parsed date and never from `date.today()`.
- Claim H (`PLANNED_FUTURE_ACTION`, target year 2025, as_of 2024-08-28) is proven, by an explicit test
  run under the current session's real system date (2026-08-19, well past the memo's "planned 2025"),
  to be completely unaffected — the claim's meaning is a property of the evidence, not of wall-clock
  time.
- `ast`-based verification (`TestTemporalSafetyAgainstCurrentDate.test_no_date_today_or_now_call_anywhere_in_module`)
  confirms no `.today()`/`.now()`/`.utcnow()` call exists anywhere in `mac_granicus_claims.py`,
  reusing the AST-node-inspection pattern (not naive substring search) already established in the
  Slice 1 review, since a naive substring check would false-positive on this module's own docstrings.

## 10. Relationship semantics

Two structurally distinct `RelationshipFact` roles are produced, never merged:
`requested_sole_source_vendor` (claim C, gated on "sole source procurement with X for") and
`installation_oversight` (claim I, gated on "under the oversight of X", explicitly scoped
"not the installation contractor — a separate contractor performs the installation work"). Both are
proven, by test, to remain separate even in a synthetic fragment containing only the oversight
phrasing with no sole-source phrasing present (adversarial case 6). No relationship claim is ever
produced from vendor-name-plus-nearby-dollar-amount proximity alone with no explicit relationship
phrase (adversarial case 2, the SFO-$40M case, and the money-and-vendor-no-phrase test).

## 11. Duplicate suppression

The real fragment's `money_values` contains the $1,590,000.00 amount twice — once labeled
`context_label="advance_deposit"`, once `context_label=None` (the second occurrence's "deposit"
wording falls outside the extractor's 120-character lookback window). `_resolved_financial_facts()`
groups `money_values` by `(numeric_value, currency)` before any claim is built, so this produces
exactly one `FinancialFact` for $1,590,000 — proven directly by
`test_real_fragment_has_only_one_advance_deposit_financial_claim` (exactly 1 claim carries
`semantic_role == "advance_deposit_purchase_order"`) and by the overall 7-claim golden count (not 8).

Two additional edge cases the real fixture does not exercise were added deliberately: an amount with
**two different, conflicting** non-`None` context_labels (a genuine ambiguity) produces **no**
`FinancialFact` at all rather than an arbitrary pick; an amount whose every occurrence is unlabeled
also produces no `FinancialFact` (an unlabeled number is not evidence of any role by itself — the
same SFO-$40M lesson the claim core's own docstring already documents, applied here to this
extractor's grouping logic). Deduplication of `Claim` objects themselves is also applied as a final
defensive step (`tuple(dict.fromkeys(claims))`), using the claim core's own frozen/hashable
structural equality per the task's own instruction — no invented claim identifiers.

## 12. Fail-closed behavior

- An unrelated MAC agenda item fragment (radio-system purchase, no EMAS/procurement wording at all)
  produces `()` — proven by `test_unrelated_agenda_item_produces_empty_tuple` and adversarial case 9.
- A fragment naming both a vendor and a dollar amount but no relationship-establishing phrase never
  produces a RelationshipFact or a `contract_value`-shaped FinancialFact — proven by
  `test_money_and_vendor_present_but_no_relationship_phrase_yields_no_relationship_claim` and
  adversarial case 2.
- No claim category, financial role, temporal qualifier, or relationship role is ever emitted from
  inference alone — every one is gated on an explicit regex match against actual wording in
  `fragment.raw_text` or an explicit, non-`None` field already present on the fragment.

## 13. Adversarial results (task §15, 10 named cases)

All ten implemented in `TestAdversarialCases`, all pass:

1. Amount with no context label → no financial claim fabricated.
2. SFO-$40M case: unlabeled $40M near a mentioned (not confirmed) vendor → no financial, no
   relationship claim.
3. No `FOR ACTION` marker, but explicit lifecycle/replacement wording present → still detects the two
   EXPLICIT_DOCUMENT_FACT claims; does not fabricate a PROCEDURAL_REQUEST.
4. Vendor named in an unrelated, past-tense sentence with no sole-source phrase → `()`.
5. Genuinely past-tense, already-approved CIP fact → correctly `HISTORICAL_FACT`, not
   `REQUESTED_PENDING_APPROVAL`.
6. Installation-oversight phrase present with no sole-source phrase anywhere → oversight claim
   produced, sole-source claim correctly absent (not fabricated from context).
7. Two different vendors + two different PO amounts in one fragment → exactly one
   PROCEDURAL_REQUEST claim (this regex-based extractor matches the first PO-shaped phrase only; see
   §21 for the noted Slice 3 follow-up on multi-PO fragments — a case this module was never asked to
   handle and the real MSP memo does not contain).
8. PO phrase present but `money_values` empty → PROCEDURAL_REQUEST claim produced with
   `financial=None` (never fabricates the amount from the raw-text phrase alone, since the required
   input for financial claims is `fragment.money_values`, not a fresh regex over dollar signs).
9. Fully generic, non-MAC-procurement boilerplate → `()`.
10. Two lifecycle/replacement phrases with no procurement content at all → exactly the two
    EXPLICIT_DOCUMENT_FACT claims, no procedural/financial/relationship leakage.

## 14. Purity and determinism

`app/acquisition/mac_granicus_claims.py` imports only `__future__`, `re`, `decimal.Decimal`,
`app.services.discovery_candidate_fragment` (type hints only), and
`app.services.evidence_claim_semantics`. AST-verified (`TestPurity.test_no_forbidden_imports`) to
contain no `sqlalchemy`, `httpx`, `requests`, `app.database`, or `app.models` import anywhere.
`test_deterministic_same_fragment_same_result` confirms `extract_mac_claims(fragment)` called twice
on the same fragment returns equal tuples. No filesystem, network, or database access anywhere in the
module — confirmed by inspection (no `open`, no `httpx`/`requests` call, no SQLAlchemy session use).

## 15. Focused tests run

```
python -m pytest tests/test_evidence_claim_semantics.py tests/test_mac_granicus_extractor.py \
    tests/test_discovery_candidate_fragment.py tests/test_mac_granicus_claims.py -q
```

Result: **119 passed**, 0 failed (updated during the checkpoint review — see §20; one regression test
was added for the claim-F correction).

## 16. Full pytest

```
python -m pytest -q
```

Result: **886 passed** (baseline 851 + 35 tests in `tests/test_mac_granicus_claims.py`), 0 failed,
0 skipped. (Originally reported as 885/34 before the checkpoint review added one regression test for
the §20 correction.)

## 17. py_compile / git diff --check

`python -m py_compile app/acquisition/mac_granicus_claims.py tests/test_mac_granicus_claims.py` — no
output, exit 0. `git diff --check` — no output, exit 0 (no trailing-whitespace/conflict-marker
issues).

## 18. Exact files changed

- `app/acquisition/mac_granicus_claims.py` (new)
- `tests/test_mac_granicus_claims.py` (new)
- `docs/architecture/mac-granicus-claim-extraction-slice2-report.md` (new, this file)

`app/services/evidence_claim_semantics.py` and `app/acquisition/mac_granicus_extractor.py` were both
re-read in full for grounding and were **not modified** — no genuine defect was found in either
during this task.

## 19. git status (post-implementation)

```
?? app/acquisition/mac_granicus_claims.py
?? tests/test_mac_granicus_claims.py
?? docs/architecture/mac-granicus-claim-extraction-slice2-report.md
```

plus pre-existing untracked files from earlier, unrelated work already present at task start
(`docs/architecture/evidence-to-signal-semantics-design.md`,
`docs/product/sfo-2026-emas-temporal-evidence-pilot.md`, `docs/research/`, `docs/ui/...` screenshot
and report directories) — none touched by this task. No file was staged, committed, or pushed.

## 20. Design corrections discovered

**Checkpoint review correction:** claim F (the historical CIP-ceiling fact) was originally emitted
only when its dollar amount successfully resolved to a `FinancialFact` (`if financial is not None:`
guarding the whole `claims.append(...)` call) — meaning a failed/ambiguous financial resolution would
silently delete the entire historical fact, not just withhold the unresolved amount. This was
inconsistent with claim D, which was already correctly designed to always emit with `financial=None`
when its own amount fails to resolve. Fixed by removing the guard so claim F always emits when its
triggering phrase matches, with `financial` attached only when `_resolved_financial_facts()` actually
resolves the amount — matching claim D's shape exactly. Added
`test_cip_approval_fact_still_emitted_when_amount_not_in_money_values` as a regression test. Does not
change the real MSP golden-case result (still exactly 7 claims — the real $19M amount always resolves
cleanly), only affects the previously-untested edge case where a CIP-approval phrase's dollar figure
cannot be matched back to `fragment.money_values`.

**Implementation-time correction (unchanged from the original report):** the initial
`_INSTALLATION_OVERSIGHT` and vendor-capture regexes assumed PDF-
extracted vendor names never wrap across a line break. The real fixture's own text contains
`"...under the oversight of Runway\nSafe."` (a page-width wrap inserted by pdfplumber mid-name), which
an early version of the regex mis-captured as `"Runway"` alone. Fixed by widening the capture
character class to tolerate embedded whitespace/newlines and adding a `_normalize_vendor()` helper
that collapses any such internal whitespace run to a single space before the name is used in a
`RelationshipFact`. No defect was found in the already-committed `evidence_claim_semantics.py` or
`mac_granicus_extractor.py` — neither was modified.

## 21. Ready for Slice 3?

Yes, with one noted scope boundary: this adapter is proven against the real MSP single-vendor,
single-PO memo shape (the only real fixture available) and against synthetic single-PO adversarial
cases. A MAC memo containing multiple, independent PO requests in one document (not present in the
real fixture) would currently only produce a claim for the first PO-shaped phrase matched — a real
limitation, not a silent-fabrication risk (no claim is ever wrongly attributed), but worth widening
with a `finditer`-based loop if/when a real multi-PO MAC memo is acquired. This does not block Slice 3.

## 22. Recommended Slice 3 scope

Per the roadmap in [evidence-to-signal-semantics-design.md](evidence-to-signal-semantics-design.md):
`tuple[Claim, ...] → SignalCandidate` promotion-safety evaluation — deciding, for the real MSP claims
D/F/H/I produced here, which (if any) meet the bar for a *candidate* Signal update (never an
auto-applied one), keeping the existing `evidence_attachment_guard` identity review and this new
intelligence-content review as two structurally separate concerns, exactly as designed in §9 of the
design doc. Should remain read-only against the real DB (SourceAssertion #222) and must not create,
modify, or promote any `Signal` row.

---

`RWI_MAC_CLAIM_EXTRACTION_SLICE2_COMPLETE`
