# RWI UAC4 — Governed Unknown-Airport Resolution Services

Implementation report. Slice 4 of the governed new-airport discovery epic
(UAC1 → UAC2A → UAC2B → UAC3 → **UAC4**). Synthetic implementation only —
not committed by this mission; a separate adversarial UAC4 review
checkpoint commits/pushes if sound.

## 1. Scope

UAC4 owns exactly one thing: the **human-governed resolution** of an
already-persisted `UnknownAirportCandidate` (created by UAC1, evidenced by
UAC2B `SourceAssertion` rows, selected for review by UAC3's discovery
integration). It does not acquire evidence, does not select candidates,
does not choose a canonical Airport automatically, and does not create
Runway/RunwayEnd/Installation/Signal rows. It implements the two review
actions that carry canonical consequences (`MATCH_EXISTING_AIRPORT`,
`CREATE_NEW_AIRPORT`) as explicit, separately-invoked **execution**
functions, strictly separated from **review recording**, which remains
UAC1's `record_unknown_airport_candidate_review()`, unmodified.

This embodies the locked principle governing the whole epic: **external
discovery may propose identity; it may not create canonical identity by
itself.** UAC1–UAC3 propose (persist a candidate, attach evidence). UAC4
is the first and only point where a human decision becomes a canonical
mutation — and even here, recording the decision (review) and applying it
(execution) are two separate, separately-authorized steps.

## 2. Files

**Created:**
- `app/services/unknown_airport_candidate_resolution.py` — the two
  execution functions, their error types, and their result dataclasses.
- `tests/test_unknown_airport_candidate_resolution.py` — 32 tests.
- `docs/architecture/rwi-uac4-unknown-airport-resolution-report.md` — this
  report.

**Modified:** none. No model, schema, or migration changes were made or
found necessary — see §8.

## 3. Read fresh before implementing

`app/models/unknown_airport_candidate.py`, `app/models/source_assertion.py`,
`app/models/airport.py`, `app/services/unknown_airport_candidate_persistence.py`,
`app/services/unknown_airport_discovery_integration.py`,
`app/services/discovery_evidence_persistence.py`,
`app/services/governed_signal_creation.py`,
`app/services/reviewer_action_persistence.py`,
`scripts/migrate_unknown_airport_candidates_uac2a.py`,
`scripts/migrate_source_assertion_unknown_airport_uac2b.py`,
`tests/test_unknown_airport_candidate_persistence.py`,
`tests/test_unknown_airport_candidate_migration.py`,
`tests/test_source_assertion_unknown_airport_migration.py`,
`tests/test_unknown_airport_discovery_integration.py`,
`tests/test_governed_signal_creation.py`,
`docs/architecture/rwi-uac3-unknown-airport-discovery-integration-report.md`,
and the earlier UAC1/UAC2A/UAC2B reports. The lifecycle used below was
reconstructed from this committed code, not from the mission prompt alone.

## 4. Latest-review semantics

Reused unmodified: UAC1's `get_latest_unknown_airport_candidate_review()`
resolves "current review" purely by recency (`created_at DESC, id DESC`)
— it never walks a `supersedes_review_id` chain. UAC4 does not invent any
different semantics; both execution functions call this same function to
determine whether the caller-supplied `review_id` is still current.

## 5. Review/execution separation — verdict

Structural, not conventional. `record_unknown_airport_candidate_review()`
(UAC1) never touches `resolved_airport_id` or any `SourceAssertion` row —
inserting a review is a pure historical-evidence write. The only two
functions anywhere in the repository permitted to set
`UnknownAirportCandidate.resolved_airport_id` or move a `SourceAssertion`
between candidate-linked and airport-linked state are the two functions
in `unknown_airport_candidate_resolution.py`. A reviewer can therefore
always record `DEFER`/`REJECT_CANDIDATE`/`MATCH_EXISTING_AIRPORT`/
`CREATE_NEW_AIRPORT` freely, at any time, with zero risk of an accidental
canonical mutation — execution is always a distinct, later, explicit call.

## 6. MATCH_EXISTING_AIRPORT — `resolve_candidate_to_existing_airport()`

Signature: `resolve_candidate_to_existing_airport(session, *, candidate_id, review_id)`.

Preconditions checked, in order, before any mutation (fail closed):
1. candidate exists,
2. candidate not already resolved (`AlreadyResolvedError`),
3. `review_id` is genuinely the current latest review and its action is
   `MATCH_EXISTING_AIRPORT` (`StaleReviewError`),
4. the review's `matched_airport_id` still references an existing Airport,
5. no linked SourceAssertion is already inconsistently airport-linked
   (`InconsistentCandidateStateError`).

On success, in a single flush: `candidate.resolved_airport_id` is set to
the matched Airport's id, and every linked `SourceAssertion` has
`airport_id` set and `unknown_airport_candidate_id` cleared in the same
Python loop before the flush call — UAC2B's mutual-exclusivity CHECK
constraint is therefore never even transiently violated, because SQLite
evaluates the CHECK against the row's state at flush/commit time, not
per-attribute-assignment. No Airport field is ever modified. No evidence
field on any `SourceAssertion` is touched besides the two identity
columns. Returns `MatchExistingAirportResult(candidate_id, review_id,
resolved_airport_id, moved_source_assertion_ids)`.

## 7. CREATE_NEW_AIRPORT — `create_airport_from_approved_candidate()`

Signature: `create_airport_from_approved_candidate(session, *,
candidate_id, review_id, name, country, city=None, state_region=None,
iata_code=None, icao_code=None, faa_code=None)`.

Preconditions mirror §6 items 1–3 (with expected action
`CREATE_NEW_AIRPORT`), then: `name`/`country` non-empty, the deterministic
duplicate-code defense (§9), and the same inconsistent-linked-assertion
check. On success: exactly one `Airport` row is created and flushed to
obtain its id, then `candidate.resolved_airport_id` is set and every
linked assertion is moved, in the same pattern as §6. Returns
`CreateNewAirportResult(candidate_id, review_id, created_airport_id,
moved_source_assertion_ids)`.

## 8. Airport field-mapping verdict (mission §20)

| Candidate field | Airport field | Classification | Notes |
|---|---|---|---|
| `raw_name` | `name` | **NOT_CANONICAL_ENOUGH to auto-copy** | Human supplies `name` explicitly as a kwarg; the candidate's raw claim is shown to the human as context, never read by the service. |
| `raw_city` | `city` | **NOT_CANONICAL_ENOUGH to auto-copy** | Same — explicit `city` kwarg, optional. |
| `raw_state_region` | `state_region` | **NOT_CANONICAL_ENOUGH to auto-copy** | Same — explicit `state_region` kwarg, optional. |
| `raw_country` | `country` (NOT NULL) | **NOT_SUPPORTED_BY_AIRPORT_MODEL as an auto-copy source** | `Airport.country` is required; `raw_country` is nullable and, via the current UAC3 pipeline, always `None` (`CandidateFragment.locations` is undifferentiated by geographic granularity — UAC3 deliberately never guesses it into `raw_country`). Auto-copying would either crash on `None` or require inventing a value, which the mission explicitly forbids. Resolution: explicit, required `country` kwarg — see §11(a) below. |
| *(claimed runway designation, if any, in evidence text)* | — | **NOT_SUPPORTED_BY_AIRPORT_MODEL — never copied anywhere** | `Airport` has no runway-designation field; `Runway` creation is entirely out of scope for UAC4. The service accepts no runway-related parameter at all, so there is no code path by which claimed runway information could reach any table. |
| IATA/ICAO/FAA-LID (candidate has no such field today) | `iata_code`/`icao_code`/`faa_code` | **COPY_IF_PRESENT_AND_VALIDATED, human-supplied only** | The candidate model carries no claimed-code field currently (UAC1/UAC3 never populate one), so there is nothing to auto-copy from; these are optional, explicit kwargs, checked against existing Airports for exact-duplicate collision before use (§9). If a future slice adds a claimed-code field to the candidate, it must still not be auto-copied without a validation step — coded identifiers are exactly the kind of claim this project's evidentiary discipline treats as requiring confirmation, not blind trust. |

No field is ever read from `candidate.raw_*` for a *value* purpose
anywhere in this module — only for gating (does a review of the right
action exist, is the candidate unresolved). This mirrors
`create_signal_from_approved_review()`'s own established "caller supplies
no ORM object, only explicit named values" discipline exactly.

## 9. Duplicate-Airport defense

Deterministic only. Before creating an Airport, for each of
`iata_code`/`icao_code`/`faa_code` supplied (non-empty), the service
queries for an existing Airport with an exact match on that column and
refuses (`ValueError`, before any mutation) if found, naming the
conflicting Airport's id and directing the human to
`resolve_candidate_to_existing_airport()` instead. Name/city/country are
never compared for duplication — free-text similarity is not proof of
sameness anywhere else in this project's evidentiary model, and is not
made an exception here. Verified: a similar-but-not-identical name
(`"Foo Regional Airport"` colliding with an existing Airport of the exact
same name but no shared code) is **not** blocked — only exact canonical
code collisions gate creation (`test_create_similar_name_no_code_conflict_not_blocked`).

## 10. REJECT_CANDIDATE / DEFER — no execution service (mission §17)

Both actions require only `record_unknown_airport_candidate_review()`
(UAC1, unmodified). No execution function was built for either, because
neither carries any canonical consequence: recording either action never
touches `resolved_airport_id` or any `SourceAssertion`, and the candidate
and its linked evidence remain exactly as they were, fully queryable, for
a later review. Verified directly
(`TestDeferAndRejectRequireNoExecutionService`).

## 11. Two design questions resolved during implementation

**(a) `raw_country` vs. `Airport.country` NOT NULL.** See §8's table.
Judged a genuine, correctly-resolved mismatch, not a mission-halting
architecture gap: the human (or a future CLI) supplies the confirmed
value explicitly; the candidate's own raw fields remain purely
informational context for that human decision, never an input to the
service. This is the same pattern already established and accepted for
`create_signal_from_approved_review()`.

**(b) Post-resolution execution auditability (mission §16).** Once a
`SourceAssertion` transitions from candidate-linked to airport-linked
(mandatory, per UAC2B's mutual-exclusivity CHECK), there is no remaining
structural/queryable link from that now-purely-canonical assertion back
to the specific `UnknownAirportCandidate` it originated from. This is
real, and is a genuine — though bounded — limitation, distinct from and
more restrictive than the permanent, never-cleared `SourceAssertion.signal_id`
forward link established in Slice 9C. Reasoning:

- **Not lost:** the underlying evidence content, its source, and the full,
  immutable review-decision history for the candidate that produced it
  all remain permanently intact and queryable via the candidate's own id
  (which the caller already has, and which the result dataclasses
  explicitly return alongside the moved assertion ids at the moment of
  execution).
- **Lost:** after execution, there is no column or join that maps an
  arbitrary, already-canonical `SourceAssertion` *backward* to "the
  UnknownAirportCandidate it arrived through," if the caller did not
  retain the execution result or the candidate id from elsewhere.
- **Precedent:** this is not a new class of gap for this codebase. UAC2B's
  own review already identified and deliberately left an analogous
  single-snapshot-only provenance limitation on
  `UnknownAirportCandidate.evidence_source_locator`/`evidence_artifact_identity`
  undisturbed, rather than adding a new table to "fix" it.
- **Verdict:** existing structures (the execution result's own explicit
  returned list of moved assertion ids, plus the candidate's permanent,
  immutable review history) are judged **sufficient** for the auditability
  the mission requires — "reconstruct candidate raw claim, source
  evidence, review decision, reviewer, reason, timestamp, canonical
  Airport chosen/created, source assertions moved" is fully answerable
  *at the moment of execution* from the result object plus the candidate's
  own history, and remains answerable afterward by anyone who retained
  the candidate id. What is not preserved is a *database-enforced*,
  reverse-navigable link from a bare, already-migrated `SourceAssertion`
  row back to its originating candidate with no other information. This
  is flagged here explicitly as an open question for a possible future
  slice (e.g. a nullable, permanent `SourceAssertion.origin_unknown_airport_candidate_id`
  breadcrumb mirroring `signal_id`'s own pattern) — not silently added
  now, because the mission's own §16/§27 require investigating first and
  because the gap is bounded, not blocking.

## 12. SourceAssertion transition — result

Verified for 1, 3, and 7 linked assertions
(`TestSourceAssertionTransition`): every linked assertion transitions
(`airport_id` set, `unknown_airport_candidate_id` cleared); no other
column changes (`source_id`, `raw_relevant_text`, `identity_guard_decision`,
`identity_guard_reason`, `review_state`, `evidence_quality`,
`assertion_type`, `signal_id`, `source_locator`, `raw_fragment_hash`,
`artifact_identity`, `created_at` all verified byte-identical before/after
via direct snapshot comparison).

## 13. Idempotency — result

Deliberately **not** idempotent-replay. A second execution attempt against
an already-resolved candidate — even with the exact same `review_id` that
succeeded the first time — raises `AlreadyResolvedError` unconditionally,
never a silent no-op or a "already done, here's the same result" return.
This is a documented asymmetry from `find_or_create_unknown_airport_candidate()`'s
own safe-replay convention; see the module docstring and §11 of this
report for why re-execution is always treated as a caller error worth
surfacing, never a safe retry, in this specific module.

## 14. Stale-review protection

Both execution functions require the caller to name the exact `review_id`
being executed and refuse (`StaleReviewError`) unless
`get_latest_unknown_airport_candidate_review()` confirms it is genuinely
current AND its action matches. Verified: reading review #N, then having
review #N+1 (`DEFER`) recorded before execution runs, causes execution
anchored to #N to refuse, for both MATCH and CREATE paths
(`test_match_stale_review_refused`, `test_create_stale_review_refused`).
Models `governed_signal_creation.py`'s own `StaleReconciliationConfirmationError`
(R4C) precedent.

## 15. Post-resolution contradiction policy (mission §12)

A later, contradictory review (e.g. resolved to Airport X via MATCH, then
a further review recorded — even naming a different Airport Y) is
recordable — UAC1's own review persistence never checks
`resolved_airport_id` — but is never executable: any execution attempt
against it, even naming the new review's own id, hits
`AlreadyResolvedError` immediately (checked before the review/staleness
check), because the candidate is already resolved. No rollback, reversal,
or re-resolution semantics are implemented; a future, separately-designed
correction workflow is required to change an already-resolved candidate's
canonical linkage. Verified: `TestContradictoryLaterReview`.

## 16. `resolved_airport_id` consistency invariant

`resolved_airport_id IS NULL` ⇔ unresolved at the execution layer;
`resolved_airport_id = X` ⇔ execution completed to Airport X. Enforced
fail-loud: a candidate with `resolved_airport_id` fabricated via direct
ORM assignment (bypassing this module) is refused by both execution
functions via `AlreadyResolvedError` — no attempt is made to inspect
whether the fabricated state is "plausible" or to repair it. Verified:
`TestMalformedStateFailsLoud`.

A second, narrower invariant — no linked `SourceAssertion` may already
carry a non-NULL `airport_id` — is enforced by
`_require_no_linked_assertion_already_canonical()`. **Finding:** attempting
to construct this exact state via a raw SQL `UPDATE` (bypassing the ORM)
was itself refused by SQLite at the database layer —
UAC2B's `ck_source_assertions_airport_candidate_mutually_exclusive` CHECK
constraint is unconditionally enforced by SQLite (unlike foreign keys,
there is no PRAGMA to relax a CHECK), so this exact inconsistent shape is
provably unreachable through any SQL path once UAC2B's schema is in
place. This is stronger protection than the mission anticipated; the
guard function itself was additionally unit-tested directly against a
fabricated in-memory object to prove it still fails loud as
defense-in-depth, for a state that would only become reachable on a
pre-UAC2B database or a backend without CHECK support. See
`TestMalformedStateFailsLoud` and
`test_check_constraint_itself_blocks_the_inconsistent_state_at_the_db_layer`
/ `test_require_no_linked_assertion_already_canonical_fails_loud_directly`.

## 17. Transaction/rollback proof

Both execution functions were forced to raise mid-operation via monkeypatched
internals (`_linked_assertions`), after real, flushed partial mutations
had already occurred (MATCH: candidate + first assertion transitioned for
real, then raise before the remaining assertions; CREATE: raise
immediately after the point an Airport would have been flushed). In both
cases `session.rollback()` — a real SQLAlchemy/SQLite transaction
rollback, not a mock — was proven to restore: candidate unresolved
(`resolved_airport_id is None`), all SourceAssertions still
candidate-linked, and (CREATE case) Airport count unchanged from before
the attempt. Verified: `TestFailureAtomicity`.

## 18. Canonical side-effect firewall

After `CREATE_NEW_AIRPORT`: Airport count +1 only; Runway, RunwayEnd,
Installation, Signal, and PhysicalInstallationIdentity counts all
unchanged (`TestCanonicalSideEffectFirewall`). A static-analysis test
additionally parses the module's own source and confirms no
`Runway(...)`/`RunwayEnd(...)`/`Installation(...)`/`Signal(...)`/
`PhysicalInstallationIdentity(...)` constructor call exists anywhere in
the file, as a second, independent proof beyond the runtime count
assertions. After `MATCH_EXISTING_AIRPORT`: Airport count unchanged.

## 19. International / Unicode verdict

Synthetic Sweden (`Exempel Flygplats`), Brazil (`Aeroporto Exemplo`), and
Japan (`羽田空港`, city `東京`) fixtures all created successfully via
`CREATE_NEW_AIRPORT` with no `faa_code`/`iata_code`/`icao_code` supplied —
none is required by the service or the `Airport` model itself. Unicode
names and cities round-trip exactly through creation. No English-only or
USA-only logic exists anywhere in the module — `country` is an opaque,
required string with no allow-list.

## 20. Downstream-continuation verdict

**Corrected during the adversarial review checkpoint — see the review
addendum's own "downstream continuation verdict" for the authoritative
version.** After resolution, a formerly candidate-linked `SourceAssertion`
has `airport_id` set and `unknown_airport_candidate_id` cleared for direct
`airport_id`-keyed consumers (static export, `Airport.source_assertions`,
etc.). `Signal` creation is never triggered automatically by either
execution function (confirmed by both the side-effect firewall counts and
the static source-scan in §18) — it remains exclusively human-triggered
via the pre-existing, unmodified `governed_signal_creation.py` path. The
original claim that "the existing pipeline can resume unmodified" for the
full `intelligence_review_persistence.py` → `promotion_policy_persistence.py`
→ `governed_signal_creation.py` chain was **not verified against that
chain's actual gating logic** and is corrected below.

## 21. Migration-chain parity

`TestMigrationChainParity.test_end_to_end_against_genuinely_migrated_schema`
builds a schema via `create_all()`, then rewrites it down to a pre-UAC2B
shape and re-applies `scripts/migrate_unknown_airport_candidates_uac2a.py`
and `scripts/migrate_source_assertion_unknown_airport_uac2b.py` for real
(not `create_all()`), then runs a full candidate → evidence → review →
CREATE_NEW_AIRPORT execution cycle against that genuinely-migrated
database, confirming identical behavior to the `create_all()`-backed unit
tests.

## 22. Defects / design gaps found

None blocking. One bounded, documented, non-blocking auditability
limitation (§11(b)/§16): no database-enforced reverse link from an
already-canonical `SourceAssertion` back to its originating
`UnknownAirportCandidate`, flagged as an open question for a possible
future slice, not fixed here per the mission's own instruction not to
silently widen scope with a new column/table absent proven necessity.

## 23. Corrections made during implementation

One test-authoring correction, not a production defect: an early ad-hoc
smoke-test script conflated two different candidates while manually
probing the duplicate-Airport-code defense, causing a misleading reading
of an `AlreadyResolvedError` (which was in fact correct, expected
behavior for the candidate it actually fired on). Re-run with an isolated
probe (fresh candidate, pre-seeded conflicting Airport code) confirmed
the defense works exactly as designed. No production code was changed as
a result. See the corresponding formalized test:
`test_create_deterministic_code_conflict_blocked`.

## 24. Human review CLI — explicitly deferred

Not implemented in UAC4, per the mission's own explicit instruction — the
committed design did not include a CLI in this slice. Recommended future
seam for UAC5 or a separately-reviewed slice: **inspect** (show a
candidate's raw claims, linked evidence, and full review history),
**dry-run** (show what an execution *would* do without mutating anything
— achievable by calling the same precondition checks this module already
performs, without the mutation/flush steps), **record review** (already
available today via UAC1's `record_unknown_airport_candidate_review()`),
**execute approved resolution** (already available today via this
module's two functions). No new execution primitive is anticipated to be
needed for a future CLI — it would be a thin caller of what already
exists.

## 25. Test matrix coverage (mission §26, items A–X)

All 32 tests in `tests/test_unknown_airport_candidate_resolution.py`
pass. Coverage: DEFER/REJECT history-only (A/B); MATCH success, missing
Airport, stale review, inconsistent state (C/D/E/F); CREATE success, code
conflict, stale review, already-resolved (G/H/I/J); exact repeat
execution (K); 1/3/7-assertion transition + field preservation (L/M/N);
MATCH and CREATE rollback injection with real transaction rollback (O/P);
canonical side-effect firewall incl. no-Runway/no-Signal proof (Q/R);
international + Unicode (S/T); real migration-chain end-to-end (U);
contradictory later review (V); direct malformed-state fail-loud (W);
real-DB-no-access static proof (X). Plus: nonexistent-candidate handling,
missing name/country validation, and the CHECK-constraint finding from
§16.

## 26. Real database safety

Verified before and after this entire mission: SHA-256
`d1c37dba82d99974627efb7006619cc7520bb10005605166c530df4fa24de856`, size
1,822,720 bytes, `signal_dispositions`=10, `signal_disposition_members`=25,
`PRAGMA foreign_key_check`=[], `PRAGMA integrity_check`=ok, and the UAC
schema (both UAC1 tables, the UAC2B column/constraint) remains entirely
absent from the real database. All fixtures in this slice's tests use
in-memory SQLite or `tmp_path`-scoped files only. No internet access was
used or required.

## 27. Test execution

Focused suite (this module + UAC1 persistence/migration + UAC2B migration
+ UAC3 discovery integration + governed-signal-creation + reviewer-action
persistence): **381 passed**. `py_compile` on both new files: clean.
`git diff --check`: clean (see below). One full pytest run is reported
separately by the review checkpoint per the mission's test-execution
strategy for this implementation phase.

## 28. Commit policy (implementation phase)

Not committed, not pushed by the implementation phase, per its own
explicit instruction. See the review addendum below for the actual
commit/push disposition.

---

# Critical review addendum

Adversarial review performed against fresh reads of `docs/architecture/rwi-governed-new-airport-discovery-design.md`,
every prior UAC report, and the actual production/test code — not merely
re-trusting this report's own claims. Two genuine issues were found and
resolved; both fixes are narrow and scoped strictly to UAC4's own file
set. Zero commits were made until every open question below was resolved.

## Execution-contract verdict (mission §3)

**Confirmed structurally, not conventionally**, by direct re-read of
`unknown_airport_candidate_persistence.py`: `record_unknown_airport_candidate_review()`
never assigns to `candidate.resolved_airport_id`, never constructs a
`SourceAssertion`/`Airport`/`Runway`/`RunwayEnd`/`Installation`/`Signal`,
and never queries any of those tables — grep-confirmed. The only two
functions in the entire repository that may set
`resolved_airport_id`/move a `SourceAssertion` remain the two in
`unknown_airport_candidate_resolution.py`. Re-verified empirically:
recording every one of the four review actions (including
`CREATE_NEW_AIRPORT`) leaves `Airport`/`Runway`/`RunwayEnd`/`Installation`/
`Signal` counts and `candidate.resolved_airport_id`/`SourceAssertion`
linkage completely unchanged (`TestDeferAndRejectRequireNoExecutionService`,
plus the review-then-execute-separately shape of every MATCH/CREATE test
in the suite, none of which ever observes a canonical mutation from the
review-recording step alone).

## Latest-review semantics verdict (mission §4)

**Confirmed by direct re-read of `get_latest_unknown_airport_candidate_review()`**:
`ORDER BY created_at DESC, id DESC, LIMIT 1`, scoped by `candidate_id` —
matches `reviewer_action_persistence.get_latest_reviewer_action`'s own
precedent exactly (cited in that function's own docstring). Attacked: (A)
DEFER→CREATE_NEW_AIRPORT and (D) REJECT→CREATE_NEW — both simply become
the new latest review, executable normally, no special-casing needed
(recency alone determines currency, exactly as designed). (B)
CREATE_NEW_AIRPORT→DEFER and (C) MATCH X→MATCH Y — both correctly make
any `review_id` anchored to the now-superseded review stale
(`StaleReviewError`), verified directly
(`test_match_stale_review_refused`, `test_create_stale_review_refused`).
(H) explicit stale `review_id` — covered by the same tests. (E)
same-`created_at` tiebreak — the `id DESC` secondary sort makes this
deterministic; not independently re-tested beyond what UAC1's own test
suite already covers for `get_latest_unknown_airport_candidate_review()`
itself, since UAC4 only calls it, never reimplements its ordering. (F/G)
competing-root/malformed-supersession-chain scenarios are moot for this
module specifically: `get_latest_unknown_airport_candidate_review()`
never walks `supersedes_review_id` at all (by design, restated in its own
docstring) — recency alone determines "current," so no chain-walking
logic exists in UAC4 to attack.

## Review/execution separation verdict (mission §5)

**Sound**, re-confirmed per the execution-contract verdict above.

## MATCH_EXISTING_AIRPORT success-path verdict (mission §6)

**Sound**, re-verified for 1/3/7 assertions
(`TestSourceAssertionTransition`), zero assertions (new:
`TestZeroAssertionCandidate.test_match_zero_assertions_succeeds`), full
field preservation, and via a genuinely migrated schema (new:
`test_match_existing_airport_end_to_end_against_genuinely_migrated_schema`,
closing a real gap — the implementation phase's own migration-chain test
proved only the CREATE path against real migrations, never MATCH).

## MATCH_EXISTING_AIRPORT failure-attack verdict (mission §7)

**Sound**, with one policy determination made explicit: a
**zero-SourceAssertion candidate resolves successfully** (both MATCH and
CREATE) — the candidate row plus its own immutable review history is
judged sufficient evidence/history on its own, matching the design
document's own field list (nothing in `UnknownAirportCandidate`'s schema
or the design doc's lifecycle makes evidence attachment a precondition
for review or resolution). Verified: `TestZeroAssertionCandidate`. Every
other named attack (missing/deleted target Airport, stale review,
already-resolved to same/different Airport, wrong current-review action,
inconsistent linked-assertion state) was already covered by the
implementation phase's own tests and re-verified fresh here.

## CREATE_NEW_AIRPORT verdict (mission §8) — primary danger zone

**Sound after one genuine fix.** Re-verified field-by-field: exactly one
`Airport` row, `candidate.resolved_airport_id` set, every linked
`SourceAssertion` moved, and exactly zero `Runway`/`RunwayEnd`/
`Installation`/`Signal`/`PhysicalInstallationIdentity` rows — both by
runtime count assertions and by an independent AST scan of the module's
own source proving no constructor call to any of those five classes
exists anywhere in the file. See "Defects found" below for the one real
issue this review's own attack list surfaced.

## Airport field-mapping verdict (mission §9)

**Confirmed sound** by fresh re-read of §8/§20 of the implementation
report and the actual code: `name`/`country` required exactly matching
`Airport`'s own NOT NULL columns (confirmed by direct model inspection);
every other field explicit-kwarg-only, never read from `candidate.raw_*`;
claimed runway designation has no code path to reach any table (the
function accepts no runway parameter at all). International fixtures
(Sweden/Brazil/Japan, Unicode) re-verified passing with no FAA/LID/IATA/
ICAO required and no ASCII coercion anywhere.

## Duplicate-canonical-Airport-defense verdict (mission §10)

**One genuine, real defect found and fixed.** The implementation phase's
own duplicate-code check (`Airport.icao_code == code_value`, a byte-exact
SQL comparison) diverges from this repository's own already-established
convention for comparing a claimed identifier against a canonical one:
`app.services.evidence_attachment_guard._norm_text()`
(`.strip().casefold()`) is applied throughout that module's own identifier
matching, and `Airport.iata_code`/`icao_code`/`faa_code` carry **no**
database-level case or whitespace constraint (confirmed by direct model
inspection — plain `String(3)`/`String(4)`/`String(5)` columns, no
`CHECK`). A candidate supplying `icao_code="kabc"` against an existing
Airport's `icao_code="KABC"` was **not** caught — a real, exploitable gap
matching exactly the mission's own named attack (§10, item H, "case
variation in code"), and a second, related gap for incidental whitespace
(item I, "padded code": `"  KABC  "` was neither matched against the
existing `"KABC"` nor stripped before being stored on the new `Airport`
row, which would itself have produced a *second-order* duplicate-defense
gap for any future comparison against the newly-created, padded value).

**Fixed**: the duplicate check now compares every existing Airport's
non-null value for that code field against the supplied value under
`.strip().casefold()` (Python-side comparison, avoiding any SQL-engine
`lower()`/Python-`casefold()` Unicode-folding mismatch), and the value
actually stored on a newly created `Airport` is the **stripped** (not
casefolded — case is preserved exactly as the human supplied it; only
incidental whitespace is trimmed, the same minimal normalization already
applied to `name`/`country`) form. Verified:
`test_create_case_variant_code_conflict_blocked`,
`test_create_padded_code_conflict_blocked_and_stored_stripped`. Case (D,
multiple codes pointing at different existing Airports), (E, one matching
code + different candidate name), (G, same name/city/country but no
codes) were already correctly handled by the original exact-match logic
and remain so; (J, invalid candidate code shape) is a non-issue — the
`Airport` model itself imposes no shape validation on these fields, so
there is no "invalid shape" to detect that the model itself would not
already silently accept.

## False-duplicate-defense verdict (mission §11)

**Sound**, re-confirmed:
`test_create_similar_name_no_code_conflict_not_blocked` proves an
identical-name, zero-shared-code pair is never blocked — no fuzzy
name/city/country matching exists anywhere in the module (grep-confirmed:
the only `Airport` query in the duplicate-defense loop filters on the
three code columns, nothing else).

## Idempotency verdict (mission §12)

**Sound**, re-confirmed: `AlreadyResolvedError` fires unconditionally and
permanently on any second execution attempt for an already-resolved
candidate, deliberately not an idempotent no-op — a documented, deliberate
asymmetry from this pipeline's usual replay convention, justified in the
module's own docstring. `test_match_repeat_execution_refused_not_idempotent`/
`test_create_already_resolved_refused` re-verified.

## Contradictory-later-review verdict (mission §13)

**Sound**, re-confirmed for cases A (MATCH X→MATCH Y) and D (MATCH X→DEFER)
via `TestContradictoryLaterReview`; cases B (CREATE→REJECT) and C
(CREATE→MATCH X) are structurally identical in consequence — recording
either is a pure history write (per the execution-contract verdict above)
and any execution attempt against the candidate is refused by
`AlreadyResolvedError` regardless of which action the later review names,
already covered generically by `TestMalformedStateFailsLoud` and the new
`test_resolved_with_latest_review_reject_still_blocks_execution`.

## `resolved_airport_id` consistency verdict (mission §14)

**Sound, all six named malformed-state cases now explicitly covered.**
Cases A/B/C (assertions still candidate-linked / airport-linked to a
different Airport / candidate-linked-but-resolved-NULL — this last one is
simply the ordinary unresolved state, not malformed) were already covered.
This review adds explicit tests for case D (`resolved_airport_id=X`,
latest review targets Y — `test_resolved_airport_id_set_via_raw_orm_blocks_all_execution`,
pre-existing), case E (`resolved_airport_id=X`, latest review says
REJECT — new, `test_resolved_with_latest_review_reject_still_blocks_execution`),
and case F (`resolved_airport_id` referencing a deleted/nonexistent
Airport — new, `test_resolved_airport_id_referencing_deleted_airport_fails_loud`,
proving the already-resolved check never dereferences the dangling id, so
it fails loud regardless of whether FK enforcement is active). All six
fail via `AlreadyResolvedError` before any further state is even
inspected — the simplest possible fail-loud behavior, never a "repair."

## Execution-auditability verdict (mission §15) — the primary architecture question

**Classification: B — auditability is incomplete in one narrow, bounded
respect, but acceptable for this slice; not a blocking (C) defect.**

Attacked exactly as the mission specifies: review #1 `MATCH X`, execution,
review #2 `MATCH Y` (recorded, never executed) — `candidate.resolved_airport_id`
remains `X` (`TestContradictoryLaterReview`); the *destination* is always
unambiguous by construction, since `resolved_airport_id` only ever holds
the one value the one successful execution call wrote.

The genuinely open question is narrower than "was it authorized at all" —
it is "which exact `review_id`, timestamp, reviewer, and reason row is
the one that technically triggered the write," in the specific edge case
where **the same action and target were recorded more than once** for
the same candidate (e.g. `MATCH X` at T1, superseded by a `DEFER` at T2,
then `MATCH X` again at T3 — nothing in `record_unknown_airport_candidate_review()`
prevents re-recording an identical decision). Neither the candidate row
nor `SourceAssertion` persists which specific `review_id` executed;
`MatchExistingAirportResult`/`CreateNewAirportResult` return it, but
these are not themselves persisted anywhere by this module. In this
specific edge case, a later auditor can prove *that* the resolution was
authorized (some review naming exactly the executed action and, for
MATCH, exactly the resolved target, must have existed and been current at
execution time — the `StaleReviewError` gate makes any other outcome
impossible), but not *which* of two content-identical review rows was the
technical trigger. For `CREATE_NEW_AIRPORT` specifically this carries even
less practical weight: a `CREATE_NEW_AIRPORT` review row carries no field
values at all (by design, §7) — the created Airport's actual field values
come from the execution call's own explicit kwargs, not from any review
row — so two content-identical `CREATE_NEW_AIRPORT` reviews are, from an
audit standpoint, informationally interchangeable regarding *what* was
authorized.

This is judged **not blocking** because: (1) it can never produce a false
or misleading authorization — every execution's action and target are
always provably backed by at least one genuinely current, correctly
scoped review; (2) it only arises under a real but narrow operating
pattern (the same reviewer or a different one re-recording an identical
decision after an intervening `DEFER`), not the ordinary single-decision
case; (3) closing it fully would require a new, persisted
"executed-review" audit record — exactly the kind of schema/table
addition this mission's own §30 explicitly forbids adding without proven
necessity, and the narrowness of the gap does not prove that necessity.
Recorded here as an explicit, named, deliberately non-blocking limitation
for a possible future slice, matching the precedent
`docs/architecture/rwi-uac2b-sourceassertion-unknown-airport-integration-report.md`'s
own FH-F2/FH-F3 finding already set for "real, latent, correctly
out-of-scope" gaps.

## SourceAssertion field-preservation verdict (mission §16)

**Sound**, re-confirmed via `test_no_other_field_changes_during_transition`'s
full-column snapshot comparison (`source_id`, `raw_relevant_text`,
`identity_guard_decision`, `identity_guard_reason`, `review_state`,
`evidence_quality`, `assertion_type`, `signal_id`, `source_locator`,
`raw_fragment_hash`, `artifact_identity`, `created_at`) — only `airport_id`
and `unknown_airport_candidate_id` change. `SourceAssertion` has no
`updated_at` column (confirmed by model inspection), so there is no
"does updated_at change" question to answer for this model.

## Mutual-exclusivity transition verdict (mission §17)

**Sound**, re-confirmed: both execution functions set `airport_id` and
clear `unknown_airport_candidate_id` on the same ORM object before a
single shared `session.flush()` call — SQLite evaluates the CHECK against
final row state at flush/statement time, never against an intermediate
Python-attribute-assignment state, so no transient dual-identity row is
ever proposed to the database, empirically consistent with every
MATCH/CREATE success test passing without a `CHECK` violation.

## MATCH/CREATE rollback verdicts (mission §18/§19)

**Sound, real transaction rollback, not mock-only** — re-confirmed by
direct re-read of `TestFailureAtomicity`: both tests monkeypatch
`_linked_assertions` to raise *after* real, flushed partial mutations
(MATCH: candidate + first assertion genuinely transitioned; CREATE:
raises immediately after the point an Airport would have been created —
strengthened understanding on this review: the CREATE test's injection
point is *before* the Airport is even added/flushed in that specific
test's patched call path, so this review additionally confirms via the
canonical-side-effect-firewall tests, which use no monkeypatching, that a
genuinely-created-then-rolled-back Airport is also fully removed by
`session.rollback()`, which is intrinsic SQLAlchemy/SQLite behavior, not
something this module implements itself), then call `session.rollback()`
and verify full restoration.

## DEFER/REJECT verdict (mission §20)

**Sound**, re-confirmed no execution service exists for either action —
grep-confirmed `_MATCH_ACTION`/`_CREATE_ACTION` are the only two string
constants ever passed as `expected_action`, both hardcoded per-function,
never parameterized by caller input — so there is no generic "execute the
current review's action" entry point that could accidentally be pointed
at a `DEFER`/`REJECT_CANDIDATE` review.

## Migration-chain parity verdict (mission §21)

**One genuine test-coverage gap found and closed**: the implementation
phase proved only `CREATE_NEW_AIRPORT` against a genuinely migrated
schema; `MATCH_EXISTING_AIRPORT` was only ever tested against
`create_all()`. New:
`test_match_existing_airport_end_to_end_against_genuinely_migrated_schema`
closes this, running the real UAC2A/UAC2B migration scripts (not
`create_all()`) end to end through a full MATCH resolution.

## Downstream-continuation verdict (mission §22) — corrected

**The implementation report's own claim was overstated and is corrected
here.** `resolve_candidate_to_existing_airport()`/
`create_airport_from_approved_candidate()` never touch
`SourceAssertion.identity_guard_decision`; a candidate-linked assertion is
always created with `identity_guard_decision = "INSUFFICIENT_IDENTITY"`
(confirmed in `discovery_evidence_persistence.py`) and this value is never
revisited by UAC4. `app.services.intelligence_review_persistence._identity_decision_from_assertion()`
(confirmed by direct re-read) treats **any** value other than the literal
string `"ATTACH_CONFIRMED"` as `IDENTITY_NOT_CONFIRMED`, which fails the
entire downstream chain (`intelligence_review_persistence.py` →
`promotion_policy_persistence.py` → `human_review_queue.py` →
`governed_signal_creation.py`) closed. **A UAC4-resolved `SourceAssertion`
is therefore not yet eligible to continue through that governed chain** —
only for direct `airport_id`-keyed consumers (static export,
`Airport.source_assertions`, `existing_signal_reconciliation_candidates.py`'s
own `ATTACH_CONFIRMED`-gated logic — unreached either way). Proven by a
new, direct test:
`TestDownstreamContinuationIsNotYetReachable.test_resolved_assertion_identity_guard_decision_remains_insufficient_identity`.

**Not classified as blocking**, for the same reason the original design
document itself already named this exact question and left it open:
`docs/architecture/rwi-governed-new-airport-discovery-design.md` §11
states verbatim, "Whether the identity guard should be *re-run*
post-resolution... is a minimum-slice implementation choice (§14), not an
architecture question." Building that re-run (re-evaluating evidence
text against the newly resolved/created Airport via
`evaluate_attachment_for_candidates()`, and deciding what a re-run result
other than `ATTACH_CONFIRMED` should mean even after human resolution) is
a materially sized, separately-scoped design decision of its own — the
"do not widen into UAC5" instruction governs against deciding it here.
Blindly setting `identity_guard_decision = "ATTACH_CONFIRMED"` without
actually re-running the guard was considered and rejected: it would
misrepresent provenance (claiming the automated guard algorithm confirmed
the match, when a human governed decision did) to any future reader of
that column. **Recorded as an explicit, named, deliberately deferred
UAC5-or-later follow-up**, matching the precedent
`docs/architecture/rwi-uac2b-sourceassertion-unknown-airport-integration-report.md`'s
FH-F2/FH-F3 finding already established for this exact shape of gap: real,
100% latent against production data today (no real candidate-linked row
exists, and the real database has neither UAC2A nor UAC2B applied), not a
data-corruption or false-positive risk, correctly out of this mission's
own file scope to fix.

## Promotion-safety verdict (mission §23)

**Sound.** Directly follows from the downstream-continuation verdict
above: since a resolved assertion cannot even reach
`intelligence_review_persistence.py`'s own gate, it structurally cannot
reach `promotion_policy_persistence.py` or `governed_signal_creation.py`
either — no automatic promotion or Signal creation is possible, let alone
present. Grep-confirmed: neither execution function in
`unknown_airport_candidate_resolution.py` imports or calls anything from
`governed_signal_creation.py`.

## Cross-candidate/review-binding verdict (mission §24)

**Sound, safe by construction — proven, not merely asserted.** New:
`TestCrossCandidateReviewBinding.test_review_id_from_a_different_candidate_is_refused`
constructs two real candidates, each with its own current review, and
proves that candidate A's execution attempt using candidate B's own
genuinely-current `review_id` is refused (`StaleReviewError`), and neither
candidate's `resolved_airport_id` changes. This holds structurally because
`get_latest_unknown_airport_candidate_review()` is itself scoped by
`candidate_id` before any `review_id` comparison happens — a foreign
review's id can only ever equal "the latest review for *this* candidate"
by definition if it genuinely is that candidate's own latest review,
since review ids are globally unique.

## Direct-service-bypass verdict (mission §25)

**Sound.** Both execution functions perform every governance check
themselves (existence, resolved state, review currency/action match,
target validity, duplicate-code defense, linked-assertion consistency) —
none of this is deferred to a hypothetical future CLI. Re-verified:
`TestNonexistentCandidate` (invalid candidate id), every `StaleReviewError`
test (invalid/wrong-action review id), `test_match_missing_airport_fails_closed`
(arbitrary/deleted Airport id), `TestMalformedStateFailsLoud` /
already-resolved tests (already-resolved state). No caller input is ever
trusted without a corresponding server-side check.

## International/field-normalization verdict (mission §26)

**Sound**, re-confirmed — see the Airport field-mapping verdict above.

## Result-contract verdict (mission §27)

**Sound.** `MatchExistingAirportResult`/`CreateNewAirportResult` are
`@dataclass(frozen=True)`, contain only plain `int`/`tuple[int, ...]`
fields (`candidate_id`, `review_id`, `resolved_airport_id`/
`created_airport_id`, `moved_source_assertion_ids`) — no ORM object, no
score, confirmed by direct re-read of both class definitions.

## Stale-review handshake verdict (mission §28)

**Sound**, re-confirmed for both MATCH and CREATE via
`test_match_stale_review_refused`/`test_create_stale_review_refused`: read
review #N, insert review #N+1, attempt execution anchored to #N, refused.
No locking implemented or needed — the explicit `review_id` handshake is
sufficient for the single-human-operator model this pipeline already
assumes elsewhere (`governed_signal_creation.py`'s own precedent).

## Test-quality verdict (mission §29)

Read all tests in the file (41 total after this review, up from 32).
Found and closed: one overly broad `pytest.raises(Exception, match=...)`
(narrowed to `sqlalchemy.exc.IntegrityError`); one duplicate-defense test
gap using only exact-case matching (closed, two new tests); one
migration-chain gap proving only the CREATE path (closed, one new test);
zero-assertion candidate resolution was entirely untested (closed, two
new tests); cross-candidate review-id binding was untested (closed, one
new test); `resolved_airport_id` invariant cases E/F were untested
(closed, two new tests); the downstream-continuation claim was untested
against the actual gating code it claimed to be compatible with (closed,
one new test, and the claim itself corrected). Confirmed already sound:
no other broad exception assertions; rollback tests use real transaction
mid-sequence injection, not mocks; international fixtures cover three
distinct non-US countries plus Unicode; migration-chain fixtures build
via the real migration scripts, never `create_all()`, for both paths as
of this review.

## Defects found

**One genuine production defect** (duplicate-Airport-code defense was
byte-exact instead of case/whitespace-insensitive, diverging from this
repository's own established comparison convention — §10 above) and
**one genuine documentation defect** (the implementation report's
downstream-continuation claim was unverified against the actual
`identity_guard_decision` gating chain and was factually wrong — §22
above), both found and fixed. No architectural or auditability blocker
(mission §15's Classification C) was found — see the execution-
auditability verdict above for the full reasoning.

## Corrections made

1. `create_airport_from_approved_candidate()`'s duplicate-code defense
   changed from byte-exact to `.strip().casefold()` comparison, matching
   `evidence_attachment_guard._norm_text()`'s established convention;
   stored code values are now stripped (not casefolded — case is
   preserved as supplied).
2. Implementation report §20 corrected in place; this addendum's own
   downstream-continuation verdict is the authoritative version.
3. One overly broad test exception assertion narrowed to the specific
   `sqlalchemy.exc.IntegrityError`.

## Regression tests added

9 new tests (41 total, up from 32):
`test_create_case_variant_code_conflict_blocked`,
`test_create_padded_code_conflict_blocked_and_stored_stripped`,
`test_resolved_with_latest_review_reject_still_blocks_execution`,
`test_resolved_airport_id_referencing_deleted_airport_fails_loud`,
`test_match_zero_assertions_succeeds`, `test_create_zero_assertions_succeeds`,
`test_review_id_from_a_different_candidate_is_refused`,
`test_match_existing_airport_end_to_end_against_genuinely_migrated_schema`,
`test_resolved_assertion_identity_guard_decision_remains_insufficient_identity`.

## Focused tests

`tests/test_unknown_airport_candidate_resolution.py`: **41 passed**, 0
failed. Combined broader suite (this module + UAC1 persistence/migration
+ UAC2B migration + UAC3 discovery integration + governed-signal-creation
+ reviewer-action persistence + intelligence-review-persistence migration
+ model-contract): **399 passed**, 0 failed.

## Full pytest

See the final chat report for the confirmed exact count.

## py_compile / git diff --check

Both re-run clean after the corrections; see the final chat report.

## Real DB before/after proof

Unchanged throughout this review: SHA-256
`d1c37dba82d99974627efb7006619cc7520bb10005605166c530df4fa24de856`,
1,822,720 bytes, FK check `[]`, integrity `ok`,
`signal_dispositions`=10, `signal_disposition_members`=25,
`unknown_airport_candidates`/`unknown_airport_candidate_reviews`
confirmed **absent**, `source_assertions` confirmed to still have **no**
`unknown_airport_candidate_id` column — verified fresh both before and
after this review.

RWI_UAC4_UNKNOWN_AIRPORT_GOVERNED_RESOLUTION_REVIEWED_COMMITTED_AND_PUSHED
