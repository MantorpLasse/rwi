# Existing-Signal Reconciliation — R2 Candidate-Discovery Adapter Report

Implements R2 of `docs/architecture/existing-signal-reconciliation-guard-design.md`'s
own S16 roadmap (commit `cdb80c8b280e393b823a086ffea8273639b01813`): the read-only
DB/ORM adapter that discovers candidate Signals and builds the already-committed,
unmodified R1 pure-core input shapes. No decision logic lives here; no Signal,
SourceAssertion, or ReviewerAction is created, modified, or persisted; no schema
change; no migration; no commit; no push.

## 1. Starting HEAD

`cdb80c8b280e393b823a086ffea8273639b01813` — confirmed matching `origin/main` before
starting. Baseline full-suite: 1300 passed.

## 2. Adapter API

`app/services/existing_signal_reconciliation_candidates.py`:

```python
def build_reconciliation_subject(
    source_assertion: SourceAssertion,
    claims: tuple[Claim, ...] = (),
    *,
    category: str | None = None,
    reference_year: int | None = None,
) -> ExistingSignalReconciliationSubject: ...

def find_reconciliation_candidates(
    session: Session, source_assertion: SourceAssertion,
) -> tuple[ReconciliationCandidateSignal, ...]: ...
```

Both functions perform only `session.query(...)` SELECT calls; neither calls
`session.add()`, `session.flush()`, `session.commit()`, `session.delete()`, or
assigns any attribute on an ORM-mapped instance. Neither imports `app.acquisition` or
anything source-family-specific.

## 3. Subject adapter

`build_reconciliation_subject()` populates the R1 `ExistingSignalReconciliationSubject`
in two distinct ways, and the report documents which fields fall into which category
because that distinction turned out to be the central design question of this slice
(Section 25):

**Read directly, structurally, from the `SourceAssertion` row/relationships — no
claims needed**: `existing_signal_id` (← `SourceAssertion.signal_id`), `airport_id`,
`runway_id` (the canonical column — never a free-text runway-wording column on the
same row), `source_id`, `artifact_identity`, and `physical_installation_ids` (every
`physical_installation_id` from this assertion's own `installation_assertion_links`
whose `outcome` is `SAME_PHYSICAL_INSTALLATION` — the transitive anchor path the
design's own review checkpoint identified).

**Derived from an explicit, already-extracted `claims: tuple[Claim, ...]` parameter**:
`vendor_names` (the `party` of every claim's `relationship`, where present — deduped,
sorted); `evidence_date` (the earliest/minimum `temporal.as_of_date` among claims that
carry one — a single document's claims typically share one date, so any consistent
tie-break is safe, and "earliest" never overstates recency).

**Explicit, caller-supplied context, not derived at all — `category` and
`reference_year`**: see Section 25 for why. Both default to `None`.

## 4. Candidate query scope

`find_reconciliation_candidates()`: airport-scoped to `source_assertion.airport_id`,
plus — defensively — whatever Signal `source_assertion.signal_id` already names, even
if that Signal's own `airport_id` differs (a real data-integrity anomaly that must
surface, never be silently hidden by a scoped filter). One combined query
(`Signal.airport_id == X OR Signal.id == Y`), never two separate round-trips merged in
Python. If `airport_id` is `None` and `signal_id` is also `None`, returns an empty
tuple — a bounded, fail-closed default; this function never scans every Signal in the
database.

## 5. Candidate dataclass mapping

`_build_candidate()` maps `ReconciliationCandidateSignal`'s eleven fields exactly:
`signal_id` ← `Signal.id`; `airport_id` ← `Signal.airport_id`; `runway_id` ←
`Signal.runway_id`; `category` ← `Signal.category`; `confirmed_vendor` ←
`Signal.confirmed_vendor`; `likely_supplier` ← `Signal.likely_supplier`;
`supporting_source_ids`/`supporting_artifact_identities`/`physical_installation_ids`
— see Sections 7-8 below; `evidence_date`/`reference_year` — see Section 9. No new
field was added to R1's dataclass; none was needed.

## 6. Runway anchor mapping

`Signal.runway_id` copied verbatim, `NULL` stays `NULL`. No parsing, no inference from
`Signal.title` or any notes field — confirmed by the structural-firewall tests
(Section 10) and by direct code review: the module never reads `Signal.title` at all.

## 7. Physical-installation transitive mapping

**Corrected at the review checkpoint (Section 27).** The exact path the design's own
review checkpoint identified, implemented as two batched queries (not one query per
Signal, Section 11): (1) every `SourceAssertion` with `signal_id` in the candidate
Signal-id set; (2) every `InstallationAssertionLink` for those assertions' ids — every
one, regardless of outcome, reduced in Python to the single most-recently-recorded
link per `assertion_id` (mirroring `ReviewerAction`'s own "latest decision wins, no
chain-walking" discipline), kept only when *that* latest link's outcome is
`SAME_PHYSICAL_INSTALLATION`. Each candidate's `physical_installation_ids` is the
deduplicated, sorted union of the resulting `physical_installation_id` across every
one of its own supporting assertions. A link whose outcome is `UNRESOLVED` or
`DIFFERENT_PHYSICAL_INSTALLATION` — including one that *used to be*
`SAME_PHYSICAL_INSTALLATION` before a human corrected it — contributes nothing (tested
explicitly, including the retraction case — `TestSupersededInstallationLinkNotCounted`).
No installation link at all → empty tuple (tested —
`test_no_installation_link_produces_empty_tuple`). Two different supporting
assertions linked to the *same* identity are deduplicated to one id, not two (tested —
`test_two_assertions_linked_to_same_installation_identity_deduped`). An installation
link belonging to an assertion outside the candidate's own supporting-assertion set
never leaks in (tested — `test_unrelated_installation_link_on_a_different_assertion_does_not_leak_in`).

## 8. Supporting provenance mapping

`supporting_source_ids`/`supporting_artifact_identities` are built **exclusively**
from `SourceAssertion` rows whose `signal_id` equals the candidate Signal's id (i.e.
`Signal.supporting_source_assertions`, Slice 9C's governed relationship) —
**deliberately never** from `Signal.source_id`, the older, single-document column.
Tested explicitly: `test_signal_own_source_id_column_not_used_as_supporting_provenance`
constructs a Signal with `source_id` set but zero `supporting_source_assertions` and
confirms `supporting_source_ids == ()`. This follows the R1 contract's own docstring
verbatim ("the AGGREGATE of every `supporting_source_assertion` already linked to
this Signal") rather than widening that already-reviewed meaning unilaterally; see
Section 25 for the real-world consequence this has for legacy Signals.

## 9. Vendor/category/temporal mapping

**Vendor** (candidate side): `Signal.confirmed_vendor`/`Signal.likely_supplier`
columns verbatim — matches R1's own comparison logic exactly, no change needed.
**Category** (candidate side): `Signal.category` verbatim, no remapping.
**Temporal/year** (candidate side): `evidence_date` ← `Signal.last_verified_at` (the
one column whose documented meaning is closest to "as of this date, this Signal's
information was confirmed," deliberately not `created_at`/`updated_at`, which
describe database-row bookkeeping time, never evidence time). `reference_year` ← the
first non-null value, in order, of `target_year`, `planning_year`, `procurement_year`,
`construction_start.year`, `completion_date.year` — a Signal's own private, unverified
annotation field is excluded from this list entirely (Section 25).

## 10. Structural firewalls

Verified two ways: (1) `dataclasses.fields()` inspection on the R1 dataclasses
themselves (inherited from R1, unchanged); (2) direct `inspect.getsource()` scans of
this module's own text for money/title/notes/fuzzy-matching tokens
(`TestStructuralFirewalls`, `TestProviderAgnosticism`). Writing these tests surfaced
the same lesson the R1 review checkpoint already learned once: a docstring that
*names* the excluded column, even only to explain the exclusion, defeats a firewall
verified by plain-text inspection. This module's docstrings were written (and, during
this slice's own first pass, corrected — Section 25) to describe every exclusion in
fully generic terms for exactly that reason.

## 11. Query strategy

Three batched queries total for `find_reconciliation_candidates()`, regardless of how
many candidate Signals are found: (1) the Signal rows; (2) every supporting
`SourceAssertion` for the whole candidate set, in one `IN (...)` query; (3) every
`InstallationAssertionLink` for the whole supporting-assertion set, in one more
`IN (...)` query. Everything else (grouping by `signal_id`/`assertion_id`, building
`ReconciliationCandidateSignal` instances) is pure in-memory Python — no query inside
any loop. `TestQueryEfficiency.test_query_count_does_not_scale_linearly_with_candidate_count`
verifies this directly: 8 candidates, each with a supporting assertion and an
installation link, produce fewer than 10 SQL statements total (a naive per-Signal
loop would have produced 24+).

## 12. MSP pre-resolution result

`TestMSPShapedFixture` builds a structural, ORM-based fixture mirroring the real
#222/#67/#41 shapes (not by hand-constructing R1 dataclasses — by inserting real
`Airport`/`Signal`/`Source`/`SourceAssertion` rows and running both adapter functions
against them). Result: `CLEAR_TO_CREATE`, `candidate_signal_ids=()`, `reasons=()`,
`advisory_candidate_signal_ids={67, 41}` — exactly R1's own already-proven golden-case
result, now reached through real ORM traversal rather than hand-built dataclasses, and
confirmed with a dedicated `test_pre_resolution_no_hidden_anchor_manufactured` test
that no anchor reason is present.

## 13. MSP current result

Both the ORM-fixture version (`test_post_resolution_already_linked`) and a real,
read-only pilot against the production database (Section 16) confirm:
`ALREADY_LINKED(signal_id=67)`.

## 14. Synthetic anchor result

`TestAnchorBearingFixtures`: a shared, populated `runway_id` between a real
`SourceAssertion` and a real `Signal` row reaches `POSSIBLE_EXISTING_SIGNAL_MATCH`
through the full adapter → core path; a shared `PhysicalInstallationIdentity`,
reached transitively through two independent `InstallationAssertionLink` rows (one on
the subject assertion, one on a Signal's supporting assertion), reaches the same
outcome. Both prove the blocking branch is reachable from real ORM data with zero
inference.

## 15. Multiple-candidate result

`test_multiple_independently_anchored_candidates_both_returned`: one Signal anchored
via `runway_id`, a second, different Signal anchored via a shared physical-installation
identity — both ids returned together, sorted ascending, exactly matching R1's own
already-proven fail-closed multiple-candidate behavior. No `LIMIT`/`ORDER BY` trick in
the adapter's queries could have silently dropped either — the adapter fetches the
full candidate Signal set unconditionally within its airport scope; nothing in this
module truncates results.

## 16. Read-only result

`TestReadOnlySafety`: row counts across `Signal`, `SourceAssertion`, and
`InstallationAssertionLink` are identical before and after both adapter functions run,
for both a populated-candidate case and a subject-building case. An AST-based test
(`test_module_never_calls_add_flush_commit_delete_update`) confirms no
`session.add`/`session.add_all`/`session.flush`/`session.commit`/`session.delete`/
`session.merge` call exists anywhere in the module's source — deliberately scoped to
calls on a variable literally named `session` (not a blanket scan for the attribute
name `add`, which would false-positive on this module's own legitimate `set.add(...)`
deduplication calls; that false positive was caught and fixed during this slice,
Section 25).

## 17. Real DB pilot

Ran read-only (via a `mode=ro` SQLite connection, so even an accidental write attempt
would raise rather than succeed) against the real production database for
SourceAssertion #222:

```
assertion #222: airport_id=45, signal_id=67, runway_id=None, source_id=70
subject:  existing_signal_id=67, airport_id=45, runway_id=None,
          physical_installation_ids=(), source_id=70,
          artifact_identity='mac.granicus.document.4.2349.105406',
          category=None, vendor_names=(), evidence_date=None, reference_year=None
candidates discovered: signal_id=41, signal_id=67
  #41: category='replacement', confirmed_vendor=None, reference_year=2025,
       supporting_source_ids=(), physical_installation_ids=()
  #67: category='replacement', confirmed_vendor='Runway Safe',
       supporting_source_ids=(70,),
       supporting_artifact_identities=('mac.granicus.document.4.2349.105406',),
       physical_installation_ids=()
DECISION: ALREADY_LINKED(signal_id=67), candidate_signal_ids=(), reasons=()
```

Exactly the expected result — no reinterpretation. One genuinely interesting,
unprompted real finding worth recording: Signal #67's own `supporting_source_ids` now
shows `(70,)` and its `supporting_artifact_identities` now shows the real MAC-memo
artifact identity — because #222 (`source_id=70`) is now, itself, one of #67's
governed `supporting_source_assertions`, a direct, structural consequence of the real
`MARK_DUPLICATE` resolution recorded earlier in this session. This means a *third*,
future SourceAssertion citing `source_id=70` or that same `artifact_identity` would
now find a genuine **provenance anchor** against Signal #67 — the human resolution
already recorded has retroactively made future reconciliation at this airport more
capable, not just resolved one case. `runway_id` and `physical_installation_ids`
remain empty for both real candidates, confirming the design doc's own Section 10
finding that those anchors are still sparse in the live corpus.

DB hash/size/mtime were identical before and after this pilot (Section "Real DB
safety" below).

## 18. International readiness

No airport, vendor, category, or runway comparison anywhere in this module is
US-specific, Unicode-restricted, or currency-dependent — airport/runway/installation
ids are opaque integers throughout, and string comparisons (`category`, vendor names)
use plain Unicode `.casefold()`/set-membership already established in R1, never touched
by this adapter. `TestInternationalCase.test_non_us_airport_and_vendor_reach_identical_semantics`
constructs a non-US airport/vendor fixture and confirms it reaches
`POSSIBLE_EXISTING_SIGNAL_MATCH` through the identical adapter → core code path as any
domestic case.

## 19. Focused tests

```
tests/test_existing_signal_reconciliation.py             (unchanged, R1)
tests/test_existing_signal_reconciliation_candidates.py  59 passed (new, R2; +5 at the review checkpoint, S27)
tests/test_governed_signal_creation.py                   (unchanged)
tests/test_governed_signal_creation_migration.py         (unchanged)
tests/test_reviewer_action_persistence.py                (unchanged)
tests/test_reviewer_action_migration.py                  (unchanged)
tests/test_human_review_queue.py                         (unchanged)
tests/test_physical_installation_reconciliation.py       (unchanged)
tests/test_physical_installation_identity_linking.py     (unchanged)
tests/test_cgf_physical_installation_pilot.py             (unchanged)
```
Combined: **335 passed**, 0 failed (re-run at the review checkpoint after S27's correction).

## 20. Full pytest

**1359 passed** (1300 baseline + 59 new), 0 failed (post-checkpoint total; originally 1354 with 54 tests
before the review checkpoint's correction and its 5 regression tests, S27).

## 21. `py_compile`

`python -m py_compile app/services/existing_signal_reconciliation_candidates.py
tests/test_existing_signal_reconciliation_candidates.py` — clean, no output.

## 22. `git diff --check`

Clean (exit 0).

## 23. Exact files changed

- `app/services/existing_signal_reconciliation_candidates.py` (new)
- `tests/test_existing_signal_reconciliation_candidates.py` (new)
- `docs/architecture/existing-signal-reconciliation-r2-candidate-adapter-report.md`
  (new, this file)

`app/services/existing_signal_reconciliation.py` (R1 core) was **not** modified — no
real defect was found in it during this slice. `app/services/governed_signal_creation.py`
was **not** modified.

## 24. `git status`

Only the three new files above appear as additions. Every other untracked path in the
working tree predates this task. No file was staged. No commit was made.

## 25. Corrections discovered

**Real architectural finding — `category` and `reference_year` cannot be derived
structurally for the subject side today.** `Claim.category` (from
`app.services.evidence_claim_semantics`) is an unrelated, epistemic-shape concept
(explicit-document-fact / procedural-request / temporal-statement / relationship) —
not a domain lifecycle category like `Signal.category`'s own values. No field
anywhere on `SourceAssertion` or `Claim` represents an independent "target/planning
year" distinct from a document's own date. Rather than inventing a mapping (which
would mean either misusing `Claim.category` for something it doesn't mean, or reading
one of `SourceAssertion`'s several free-text year/wording columns — exactly the raw-text
inference this whole design forbids), both became explicit, optional, caller-supplied
keyword arguments defaulting to `None`. This is not a workaround: for a genuine future
R3 integration, `create_signal_from_approved_review()` already receives a
human-selected `category` argument directly — the natural, honest source for this
value is that same human decision, not an automatic derivation this module has no
safe way to perform.

**A Signal's private, unverified year-guess annotation is deliberately excluded**
from the `reference_year` priority list, for the same reason `Signal`'s private notes
field is already excluded from every reconciliation field R1 defines — an outside
hunch must not silently become structural reconciliation evidence.

**Two test-authoring defects found and fixed within this slice** (not defects in the
production module, defects in how it was initially being verified): (1) an AST test
meant to catch stray `session.add()`/`session.flush()`/`session.commit()` calls
originally flagged *any* attribute named `add`, including the module's own legitimate,
harmless `set.add(...)` calls used for deduplication — narrowed to only flag calls
made specifically on a variable named `session`. (2) The module's own docstrings, in
an early draft, named the specific excluded columns (`.title`, `.notes`,
`source_notes`, free-text runway-wording columns) to explain *why* they were excluded
— which then tripped the firewall tests meant to verify those very names never appear
in the module's source at all. Rewritten in fully generic terms, exactly mirroring the
correction the R1 review checkpoint already made once for the same reason (a firewall
verified by plain-text inspection cannot itself contain the text it forbids).

No correction was needed to `app/services/existing_signal_reconciliation.py` (R1) —
it was read fresh at the start of this slice and used entirely unmodified.

## 27. Correction made at the R2 review checkpoint (2026-08-19, same HEAD)

The review checkpoint (RWI_EXISTING_SIGNAL_RECONCILIATION_R2_REVIEW_COMMIT_PUSH) found
one genuine, real R2-scope defect by attacking the physical-installation traversal
against the domain's own established supersession pattern (`docs/domain/
reconciliation-physical-installation-design.md`; regression-tested precedent in
`tests/test_physical_installation_reconciliation.py::test_reviewed_outcomes_supersession_and_assertion_immutability`,
which already exercises exactly this "SAME_PHYSICAL_INSTALLATION superseded by
UNRESOLVED" shape for a different purpose):

**Defect**: both `build_reconciliation_subject()` and `find_reconciliation_candidates()`
originally read every historical `InstallationAssertionLink` row with
`outcome == 'SAME_PHYSICAL_INSTALLATION'` for an assertion, with no awareness that a
*later* link can supersede (retract or correct) an earlier one — the same append-only
"latest decision wins" shape `ReviewerAction` already has, and that
`app.services.reviewer_action_persistence.get_latest_reviewer_action()` already
handles correctly for that table. A SAME_PHYSICAL_INSTALLATION decision a human later
corrected to UNRESOLVED (or to DIFFERENT_PHYSICAL_INSTALLATION) kept contributing a
live identity anchor forever. Reproduced directly: a supporting assertion originally
linked SAME_PHYSICAL_INSTALLATION to an identity, then that link superseded by an
UNRESOLVED correction; a subject assertion genuinely, currently linked to the same
identity. Before the fix, this produced `POSSIBLE_EXISTING_SIGNAL_MATCH` off the
*retracted* decision; after, correctly `CLEAR_TO_CREATE`. This is exactly the class of
error the whole reconciliation design exists to prevent — an anchor must be a
genuine, current, human-confirmed fact, not stale data a human has since taken back.

**Fix** (within R2 scope, no R1 change): added
`_latest_installation_links_by_assertion_id()`, reducing a list of links to the single
most-recently-recorded (`(reviewed_at, id)`-ordered, mirroring `ReviewerAction`'s own
tie-break) row per `assertion_id`. Both `build_reconciliation_subject()` and
`find_reconciliation_candidates()` now fetch *every* link for the relevant
assertion(s) — not pre-filtered by outcome, since filtering up front would hide that a
retraction ever happened — then apply this reduction, and only count a
`physical_installation_id` when that assertion's *latest* link's outcome is
`SAME_PHYSICAL_INSTALLATION`. Query count is unchanged (still one query for links,
now unfiltered by outcome instead of filtered) — the "three batched queries" claim in
S11 still holds.

**Regression tests added** (`TestSupersededInstallationLinkNotCounted`, four tests):
a retracted SAME link on a candidate's supporting assertion no longer counts; the same
on the subject's own assertion; the reverse direction (an initial
DIFFERENT_PHYSICAL_INSTALLATION decision later *corrected to* SAME_PHYSICAL_INSTALLATION
correctly counts the corrected decision, proving the fix isn't merely "ignore
everything," it genuinely tracks recency in both directions); and one full adapter→core
integration test confirming no false-positive `POSSIBLE_EXISTING_SIGNAL_MATCH` results
from a retracted link. A fifth test
(`test_unrelated_installation_link_on_a_different_assertion_does_not_leak_in`) was
added alongside these, confirming an `InstallationAssertionLink` belonging to an
assertion that is not a supporting assertion of any candidate Signal cannot
contaminate a candidate's `physical_installation_ids` — already true by construction
(the query only ever fetches links for assertion ids it already knows belong to
candidates), but not previously exercised by a dedicated test. Test count: 54 → 59.

The real #222/#41/#67 pilot (S17) was re-run after this fix and produced an identical
result — neither #222, #41, nor #67 has any `InstallationAssertionLink` history at
all, so this defect had zero effect on the one real case this design was built
around; it matters for future cases where installation reconciliation has actually
been recorded and later corrected.

## 26. Ready for R3

**Ready**, with one honest caveat flagged rather than hidden: R3 (governed-creation
integration) will need to decide *where* `category`/`reference_year` come from when
wiring this adapter into `create_signal_from_approved_review()` — the natural answer
(reuse that function's own existing `category` parameter, and derive `reference_year`
from whichever of its own `target_year`/`planning_year`/etc. arguments the human
supplied, using the same priority order this module already established for reading
it back off a Signal) is not itself built here, since R3 is out of this slice's scope.
Everything else — subject construction from real governed evidence, airport-scoped
candidate discovery with the defensive already-linked-Signal inclusion, all three
anchor families reachable through real ORM traversal, batched query strategy, and a
successful, byte-proven read-only pilot against the real #222/#67/#41 case — is built,
tested, and independently confirmed against the live database.

## Real DB safety

Read-only pilot only (`mode=ro` SQLite connection) — no write was possible, let alone
attempted. Hash/size/mtime captured before and after the pilot, identical to each
other and to every prior checkpoint in this session:
`sha256=71b43b7954b803600805f1ea8fec24db4652835f312b70734e1797453703e710`, size
`1789952` bytes, mtime `1787158044.8543456`.

Re-captured, and re-run, at the review checkpoint (after the S27 fix): identical hash,
size, and mtime before and after, and an identical decision (`ALREADY_LINKED(67)`) for
#222 — confirming the fix changed nothing about the one real case this design was
built around, only future cases involving corrected installation-identity decisions.
