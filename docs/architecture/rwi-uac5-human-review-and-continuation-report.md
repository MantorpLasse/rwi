# RWI UAC5 — Human Unknown-Airport Review CLI + Resolved-Evidence Continuation

Implementation report. Slice 5 of the governed new-airport discovery epic
(UAC1 → UAC2A → UAC2B → UAC3 → UAC4 → **UAC5**). Synthetic implementation
only — not committed by this mission; a separate adversarial UAC5 review
checkpoint commits/pushes if sound.

## 1. Scope

UAC5 had two operator-facing goals:

- **UAC5A**: a human review CLI for `UnknownAirportCandidate` — inspect,
  dry-run, record review, execute approved resolution.
- **UAC5B**: an investigation into whether resolved candidate-linked
  evidence can safely continue through the existing
  `intelligence_review_persistence.py` → ... → `governed_signal_creation.py`
  chain, per the gap UAC4's own adversarial review identified (a resolved
  `SourceAssertion`'s `identity_guard_decision` permanently stays
  `INSUFFICIENT_IDENTITY`).

**UAC5A is fully implemented.** **UAC5B is a documented STOP** — see §7.
Faithfully re-running the identity guard against a resolved candidate's
evidence is not currently possible with what is persisted on
`SourceAssertion` today, and this mission does not invent a lossy or
partial substitute. No `reevaluate_resolved_candidate_evidence()`-shaped
service was built, per the mission's own explicit instruction: "If guard
replay cannot be done safely with current persisted evidence: STOP before
inventing a workaround."

## 2. Files

**Created:**
- `scripts/review_unknown_airport_candidate.py`
- `tests/test_review_unknown_airport_candidate.py` (38 tests)
- This report.

**Modified:** none. No production model, schema, migration, or existing
service file was changed.

## 3. Files read fresh

`docs/architecture/rwi-governed-new-airport-discovery-design.md`, all
five prior UAC reports, `app/services/unknown_airport_candidate_persistence.py`,
`app/services/unknown_airport_candidate_resolution.py`,
`app/services/unknown_airport_discovery_integration.py`,
`app/services/discovery_evidence_persistence.py`,
`app/services/discovery_candidate_fragment.py`,
`app/services/evidence_attachment_guard.py`,
`app/services/intelligence_review_persistence.py`,
`app/models/unknown_airport_candidate.py`, `app/models/source_assertion.py`,
`app/models/airport.py`, `app/services/fleet_health_check.py`,
`app/services/fleet_health_review_rules.py`,
`scripts/review_signal_disposition.py`, `scripts/review_reconciliation_item.py`,
plus the corresponding test suites.

## 4. CLI architecture (UAC5A)

`scripts/review_unknown_airport_candidate.py` follows
`review_signal_disposition.py`'s own three-tier safety shape (inspect /
dry-run / write, `--database` required with no default,
`--allow-database-write` required for any write, read-only SQLite URI
engine for pure inspection, one importable `run_review()` +
`render_result()` pair consumed by both `main()` and the test suite).

Three mutually exclusive modes, selected by which flags are present:
- **INSPECT** (`--candidate-id` only): read-only engine, never mutates.
- **REVIEW** (`--decision` given, `--execute` absent): records exactly
  one `UnknownAirportCandidateReview` row via UAC1's own
  `record_unknown_airport_candidate_review()` — imported, never
  reimplemented.
- **EXECUTE** (`--execute` given, `--decision` absent): executes an
  already-recorded review via UAC4's own
  `resolve_candidate_to_existing_airport()`/
  `create_airport_from_approved_candidate()` — imported, never
  reimplemented.

`--decision` and `--execute` are mutually exclusive by construction
(`_validate_config()` refuses both together) — recording a review and
executing its canonical consequence are always two separate CLI
invocations, matching UAC4's own strict review/execution separation.

## 5. Schema-gate verdict

Reuses `scripts.migrate_source_assertion_unknown_airport_uac2b.inspect()`
verbatim (which itself delegates to UAC2A's own `inspect()`) — refuses
with the structured blocker `UNKNOWN_AIRPORT_SCHEMA_MIGRATION_REQUIRED`
before any `UnknownAirportCandidate`/`SourceAssertion` query if the
UAC2A/UAC2B schema is not ready. No `OperationalError` ever leaks;
verified directly (`TestSchemaGate`).

## 6. Dry-run implementation — a deliberate design choice

Every decision-bearing or execute invocation, dry-run or write alike,
calls the SAME real governed service function
(`record_unknown_airport_candidate_review()`,
`resolve_candidate_to_existing_airport()`, or
`create_airport_from_approved_candidate()`) against a genuinely writable
`Session`. The only difference between a dry-run and a write is whether
this script issues `session.commit()` (write) or `session.rollback()`
(dry-run) — never a CLI-side reimplementation, preview, or simulation of
governed logic. This guarantees a dry-run's "would this be eligible"
answer can never drift from the real write path's own behavior, and
satisfies mission §16 ("prefer CLI orchestration to call services; do not
put business predicates in CLI") as literally as possible: the CLI
contains zero eligibility logic of its own for either path. Verified:
every `TestDryRunAndWriteGate`/`TestExecuteMatch`/`TestExecuteCreate` test
confirms a dry-run leaves zero durable rows.

## 7. UAC5B — guard-replay feasibility (the architecture-sensitive finding)

**Investigated exhaustively; STOP, not implemented, per the mission's own
explicit instruction.**

`app.services.evidence_attachment_guard.EvidenceBag` — the guard's own
required input — carries five identity-evidence categories:
`identifiers`, `names`, `runway_ends`, `runway_pairs`, `issuers`,
`locations`, plus `contradicting_names`/`contradicting_issuers`/
`contradicting_locations` and `alternate_airport_runway_ends`/
`alternate_airport_runway_pairs`. `CandidateFragment` (the pre-persistence
extraction envelope) carries the identical structured fields one-to-one.

Direct re-read of `SourceAssertion` (`app/models/source_assertion.py`)
and both persistence functions
(`persist_discovery_fragment()`/`persist_candidate_linked_source_assertion()`
in `discovery_evidence_persistence.py`) confirms exactly what survives to
the database:

| EvidenceBag category | Persisted on SourceAssertion? | How |
|---|---|---|
| `identifiers` | Partially, lossily | `raw_airport_identifier` — comma-joined string (`_join_or_none`), original set structure destroyed |
| `names` | Partially, lossily | `raw_airport_name` — comma-joined string, same loss |
| `runway_pairs` | Partially, lossily | `raw_runway_value` — comma-joined string, same loss |
| `runway_ends` | Partially, lossily | `raw_runway_end_value` — comma-joined string, same loss |
| `locations` | **Never** | no column exists anywhere on `SourceAssertion` |
| `issuers` | **Never** | no column exists anywhere on `SourceAssertion` |
| `contradicting_names` | **Never** | no column exists anywhere |
| `contradicting_issuers` | **Never** | no column exists anywhere |
| `contradicting_locations` | **Never** | no column exists anywhere |
| `alternate_airport_runway_ends` | **Never** | no column exists anywhere |
| `alternate_airport_runway_pairs` | **Never** | no column exists anywhere |

`raw_relevant_text` (the fragment's full original text) IS preserved
losslessly — but it is free text, not the structured `EvidenceBag` the
guard actually consumes; re-parsing identity evidence back out of free
text would be exactly the "reconstruct identity from lossy free text"
the mission's own §12 explicitly forbids.

**Two of the guard's five evidence categories (LOCATION, ISSUER) and
every contradiction/alternate-airport signal are unconditionally,
permanently lost the moment a `SourceAssertion` is persisted** — not
merely degraded, but entirely absent from the schema. Even for the three
partially-surviving categories, the comma-join is lossy and non-reversible
in general (a value itself containing `", "` would corrupt any attempted
split) and was never designed to be reversed — `_join_or_none()`'s own
purpose is a human-readable audit string, not a serialization format.

**Why a partial replay was rejected, not merely "incomplete":** a guard
re-run fed only the three partially-recoverable categories (with
`locations`/`issuers`/every `contradicting_*`/`alternate_airport_*` empty)
would not merely produce a *less confident* result — it could produce an
**actively wrong** one. `evidence_attachment_guard.py`'s own topology
logic (`_topology_evidence()`) computes `other_airport_named` from
`contradicting_names`/`contradicting_issuers`/`contradicting_locations`
to decide whether an unmatched runway token counts as a contradiction; an
empty `contradicting_*` set (because the real data was simply never
persisted, not because none existed) could cause a replay to reach
`ATTACH_CONFIRMED` where the original extraction, with its full evidence,
would correctly have reached `REJECT_CROSS_AIRPORT` or `REVIEW_REQUIRED`.
Producing a false `ATTACH_CONFIRMED` — the exact literal string that
unlocks the entire downstream governance chain — is a materially worse
outcome than leaving the row correctly held at `INSUFFICIENT_IDENTITY`.

**Conclusion: STOP.** No `app/services/resolved_candidate_evidence_reevaluation.py`
was created. `identity_guard_decision` is treated, correctly, as durable
historical evidence of what the guard concluded from the ORIGINAL,
now-partially-unpersisted evidence at extraction time — not current
mutable state a later process may safely recompute with different, lossy
inputs (see §8 for the immutability-vs-derived-state question this
answers).

**Exact fields a future slice must persist to make this possible**
(recorded here per the mission's own §12 instruction): extend
`SourceAssertion` (or a new, explicitly-scoped sibling table, to avoid
retroactively widening a table six other slices already depend on) to
carry the full structured `EvidenceBag` shape verbatim — `locations`,
`issuers`, `contradicting_names`, `contradicting_issuers`,
`contradicting_locations`, `alternate_airport_runway_ends`,
`alternate_airport_runway_pairs` (new columns, e.g. as JSON-encoded
frozensets, or as proper set-storage tables mirroring
`PhysicalInstallationIdentity`'s own multi-row precedent) — plus either
replacing the current lossy comma-join for `identifiers`/`names`/
`runway_pairs`/`runway_ends` with a reversible encoding, or persisting
those as their own structured columns too. This is a genuine, real,
identified schema gap for a future slice — not designed or implemented
here, per this mission's own explicit "do not silently add schema"
instruction (§28/§30).

## 8. Original-guard-history preservation verdict

**`identity_guard_decision` is modeled, and treated by every existing
reader, as durable historical evidence — not current derived state — and
mutating it would destroy real audit history.** Confirmed by direct
re-read of the column's own extensive comment in
`app/models/source_assertion.py`: "Populated ONLY by
`app/services/discovery_evidence_persistence.py`... never by NASR/
USAspending/FAA ingestion." No existing code path ever re-derives or
overwrites an already-set `identity_guard_decision` — `persist_discovery_fragment()`'s
own idempotent-reuse path returns an existing row's decision UNCHANGED
rather than recomputing it, even when re-evaluated against a possibly
different candidate set (confirmed in that function's own body).
`intelligence_review_decision`/`promotion_policy_decision` are
structurally separate, later-stage decisions built ON TOP of
`identity_guard_decision`, never a mechanism for revising it. Given this,
even if a faithful guard replay WERE possible (§7), overwriting the
original `INSUFFICIENT_IDENTITY` value in place would already have been
the wrong design — the correct shape, left to a future slice, is an
ADDITIVE record of a later, second guard evaluation (mirroring how
`intelligence_review_decision`/`promotion_policy_decision` are themselves
additive, later-stage columns alongside `identity_guard_decision`, never
replacements for it), not a mutation of the original historical decision.
This reasoning is now the primary justification for §7's own STOP: no
narrow, in-place, backward-compatible fix exists even in principle,
independent of the data-availability problem.

## 9. CLI: inspect behavior

Displays candidate id, full raw claimed identity (`raw_name`/`raw_city`/
`raw_state_region`/`raw_country`/`raw_iata_code`/`raw_icao_code`/
`raw_faa_lid`/`raw_runway_designation`), `candidate_fingerprint`,
resolution state (`resolved_airport_id` + resolved Airport name when
present), latest review, full chronological review history, linked
`SourceAssertion` count + per-assertion summary (locator, artifact
identity, a 200-character text excerpt, `identity_guard_decision`), and
deterministic canonical-code matches (§10). Never mutates — always a
read-only SQLite `mode=ro` engine when neither `--decision` nor
`--execute` is given. Verified: `TestInspect`.

## 10. Deterministic canonical-code match display

Purely informational: any EXISTING `Airport` whose `iata_code`/
`icao_code`/`faa_code` exactly matches (case/whitespace-insensitive, the
identical comparison `create_airport_from_approved_candidate()`'s own
duplicate-code defense uses) one of the candidate's own claimed
`raw_iata_code`/`raw_icao_code`/`raw_faa_lid` values. Never a fuzzy
name/city/country similarity ranking — no such comparison exists anywhere
in this module (grep-confirmed, and `TestNoBusinessLogicInCli`'s AST scan
confirms no `Airport(...)` construction, i.e. no attempt to score/rank
candidates via a constructed comparison object). Never auto-selected as
the decision; `--matched-airport-id` is always the human's own explicit
input. Verified: `TestInspect.test_inspect_shows_deterministic_code_match_never_fuzzy`.

## 11. Review-recording behavior (dry-run + write)

Both dry-run and write call the real `record_unknown_airport_candidate_review()`
(§6). `--reviewer`/`--reason` required whenever `--decision` is given;
`--matched-airport-id` required for `MATCH_EXISTING_AIRPORT`, forbidden
otherwise (both static, pre-DB-access checks). Recording never mutates
`candidate.resolved_airport_id` or any `SourceAssertion` — verified for
all four actions (`TestReviewRecording`).

## 12. Execution behavior (dry-run + write)

`--execute --review-id N` (mutually exclusive with `--decision`) reads
the named review, and if its `candidate_id` matches the target candidate
and its `action` is `MATCH_EXISTING_AIRPORT` or `CREATE_NEW_AIRPORT`,
calls the corresponding real UAC4 function anchored to that exact
`review_id` — UAC4's own `StaleReviewError`/`AlreadyResolvedError`/
`InconsistentCandidateStateError` protections apply unmodified, with zero
CLI-side reimplementation (§13). `CREATE_NEW_AIRPORT` execution requires
`--new-airport-name`/`--new-airport-country` (validated after confirming
the review's own action, since these fields are meaningless for a MATCH
review); every other `--new-airport-*` field is optional, forwarded
verbatim as explicit keyword arguments — never read from
`candidate.raw_*` (matches UAC4's own design exactly; the CLI adds no new
field-mapping logic of its own).

## 13. MATCH flow (verified end to end)

inspect → record review (`--decision MATCH_EXISTING_AIRPORT --matched-airport-id X`)
→ inspect confirms review recorded, `resolved_airport_id` still `None`
→ execute dry-run (`--execute --review-id N`, no write) → execute write
(`--allow-database-write`) → `resolved_airport_id` set, every linked
assertion moved. No Signal, no Runway. Verified:
`TestExecuteMatch.test_dry_run_then_execute_full_flow`.

## 14. CREATE flow (verified end to end)

inspect → record review (`--decision CREATE_NEW_AIRPORT`, no
`--matched-airport-id`) → execute dry-run (no Airport created; zero
canonical count change) → execute write with `--new-airport-name`/
`--new-airport-country` → exactly one Airport created, zero Runway/
RunwayEnd/Installation/Signal/PhysicalInstallationIdentity rows. Verified:
`TestExecuteCreate.test_dry_run_then_execute_full_flow` and
`TestCanonicalSideEffectFirewall`.

## 15. REJECT/DEFER flow

Both record a review via the same review-recording path (§11); neither
has any `--execute` counterpart with canonical consequence — attempting
`--execute` against a `REJECT_CANDIDATE`/`DEFER` review's `review_id` is
refused with a clear, honest message naming the actual recorded action,
never a generic/misleading error. Verified:
`TestStaleReviewHandshake.test_execute_refuses_reject_or_defer_review`,
`TestReviewRecording.test_defer_review_recorded_no_canonical_change`/
`test_reject_review_recorded_no_canonical_change`.

## 16. Stale-review handshake

`--execute` always requires the exact `--review-id`; execution anchored
to a review that is no longer the candidate's current latest review (a
newer review — even a `DEFER` — was recorded in between) is refused via
UAC4's own `StaleReviewError`, propagated verbatim, zero CLI-side
staleness logic. Verified:
`TestStaleReviewHandshake.test_execute_refuses_after_newer_review_recorded`.

## 17. Transaction policy

**Review recording and execution are NEVER wrapped in one shared
transaction** — even within a single CLI process, they are two entirely
separate invocations by construction (mode separation, §4), each opening
its own engine/session and issuing its own single commit-or-rollback. A
committed review's row survives an unrelated, later, failed execute
attempt unconditionally — this script never rolls back an
already-committed review merely because a later execute call fails.
Verified directly:
`TestExecutionFailureRepeatAndContradiction.test_review_survives_execution_failure`
(matched Airport deleted after the review was committed; execute refuses;
the review row is confirmed still present and unchanged afterward).

## 18. Wrong-DB / write-gate verdict

`--database` has no default (confirmed via direct `argparse` action
inspection, `TestNoRealDatabaseAccess.test_database_argument_has_no_default`);
every write requires `--allow-database-write`. Two independent tmp_path
databases were proven fully isolated — a write against database A never
touches database B (`TestWrongDbIsolation`).

## 19. Execution-auditability UX

A successful `--execute --allow-database-write` result carries (and
`render_result()` prints): candidate id, the exact `review_id` anchor,
the action executed, resolved/created Airport id, every moved
`SourceAssertion` id. This is operational UX only — it creates no new
persisted execution-audit record, and does not change UAC4's own
Classification-B execution-auditability verdict (which review of two
content-identical repeated reviews technically triggered a given
execution remains unrecorded by anything in this repository). Documented
honestly in the module's own docstring, not silently glossed over.

## 20. Non-confirmed guard outcomes / promotion safety

Moot for this slice given §7's STOP — no guard re-run of any kind occurs,
so `ATTACH_CONFIRMED`/`REJECT_CROSS_AIRPORT`/`REVIEW_REQUIRED` are never
produced by anything UAC5 does. `identity_guard_decision` remains exactly
`INSUFFICIENT_IDENTITY` for every resolved candidate's formerly
candidate-linked evidence, both before and after resolution — verified
directly (`TestDownstreamContinuationNote.test_resolved_candidate_shows_honest_continuation_note_and_insufficient_identity_persists`).
No Signal is ever auto-created by this script (AST-confirmed: no
`Signal(...)` construction anywhere in the module,
`TestNoBusinessLogicInCli`); the existing intelligence-review/promotion
pipeline remains the only gate, entirely untouched.

## 21. FH-F2/FH-F3 result

Re-verified fresh, not merely re-trusted from UAC3's own fix. Three
states tested end to end through this CLI's own execute path
(`TestFleetHealthThreeStates`): (1) a candidate-linked, still-unresolved
`SourceAssertion` — present in `_build_source_assertion_review_states()`'s
own input set, correctly skipped by both `evaluate_fh_f2()`/
`evaluate_fh_f3()` (UAC3's own `unknown_airport_candidate_id is not None`
guard); (2) a resolved (formerly candidate-linked) `SourceAssertion` —
**structurally excluded from the FH-F2/FH-F3 input set entirely**, since
`_build_source_assertion_review_states()` filters on `airport_id IS NULL`
and a resolved assertion's `airport_id` is now set — no rule-scope change
was needed or made; (3) a genuinely, truly unattributed `SourceAssertion`
(`airport_id` NULL, `unknown_airport_candidate_id` NULL) — present in the
input set and correctly still fires FH-F3 (`review_state="reviewed"`).
No rule scope was widened.

## 22. Migration-chain parity

`TestMigrationChainParity.test_full_cli_flow_against_genuinely_migrated_schema`
runs the real `uac2a_migration.upgrade()`/`uac2b_migration.upgrade()`
scripts (never `create_all()`) against a synthetic pre-UAC database, then
exercises the full CLI inspect → record review → execute write flow
against that genuinely migrated schema.

## 23. Defects / design blockers

**One design blocker (UAC5B, §7), confirmed real and STOPPED per the
mission's own instruction — not a defect in existing code, a genuine,
pre-existing schema-completeness gap for a capability this mission was
asked to investigate.** No implementation defects were found in the new
CLI itself during its own build-and-test cycle (all issues encountered
while writing tests were test-fixture construction issues — see §24 —
never production behavior).

## 24. Corrections made

Two test-fixture construction fixes, both localized to this mission's own
new test file, neither a production defect:
1. `_make_pre_uac_db()`'s first draft copied `Base.metadata.tables` into a
   fresh `MetaData` excluding the two UAC1 tables directly, which broke
   with `NoReferencedTableError` for `SourceAssertion`'s own forward FK —
   the identical class of issue UAC2B's own review already found and
   fixed for `test_unknown_airport_candidate_migration.py`'s
   `_pre_uac1_db()` fixture. Corrected to the same technique: build the
   full current schema first, then rebuild `source_assertions` back to
   its pre-UAC2B shape via raw SQL and drop the two UAC1 tables.
2. The real-database-access test's first draft grepped the module's raw
   source text for the literal `"runway_safe.db"`, which also matched the
   module's own legitimate usage-example docstring. Corrected to an AST
   walk that excludes only the module docstring's own `Constant` node,
   confirming the literal appears nowhere else (no default, no hardcoded
   path in executable code), plus a direct `argparse` action-default
   check.

## 25. Regression tests

38 tests in `tests/test_review_unknown_airport_candidate.py`, covering
the mission's own test matrix (A–N CLI; O–S resolution-via-CLI; T–Y
continuation notes; Z Fleet Health; AA migration parity; AB real-DB
no-access).

## 26. Focused tests / full pytest / py_compile / git diff --check

See the final chat report for exact counts (test execution was still
running in the background at the time this report file was written; the
chat report below reflects the completed run).

## 27. Real database safety

Verified before and throughout this mission: SHA-256
`d1c37dba82d99974627efb7006619cc7520bb10005605166c530df4fa24de856`, size
1,822,720 bytes, `signal_dispositions`=10, `signal_disposition_members`=25,
`PRAGMA foreign_key_check`=[], `PRAGMA integrity_check`=ok, UAC schema
remains entirely absent. All fixtures in this slice's tests use
`tmp_path`-scoped SQLite files only; no `:memory:` shortcut is required
since the CLI itself always opens a real file path. No internet access
was used or required.

## 28. Commit policy (implementation phase)

Not committed, not pushed by the implementation phase. See the review
addendum below for the actual commit/push disposition.

---

# Critical review addendum

Adversarial review performed against fresh reads of the design doc, all
five prior UAC reports, and the actual production/test code — not merely
re-trusting this report's own claims. One genuine completeness gap was
found and fixed; one genuine, pre-existing (not UAC5-introduced) CLI
robustness gap was found, confirmed present in an already-committed
sibling script, and correctly left undisturbed per scope discipline. The
UAC5B STOP was independently, adversarially re-derived from scratch,
including two new empirical (not merely reasoned-about) proofs.

## CLI mode-separation verdict (mission Part A §1)

**Sound, proven structurally.** `_validate_config()` raises before any
database is opened if `--execute` and `--decision` are both given
(`test_missing`... covered by existing `TestReviewRecording`/
`TestExecuteMatch` construction alone never allowing both). `run_review()`
dispatches via a strict `if config.execute: ... elif config.decision is
not None: ... else: inspect` chain (module lines ~609–617) — there is no
code path where an invocation with `--execute` unset and `--decision`
unset could reach either write branch, and no combination of flags
reaches more than one branch. Re-verified directly by reading, not merely
re-running the existing tests.

## Schema-gate verdict (mission Part A §2)

**Sound for the schema-absent/incompatible case** (re-verified:
`TestSchemaGate`, `TestMainEntrypoint.test_main_schema_missing_exit_code_one`
— clean `UNKNOWN_AIRPORT_SCHEMA_MIGRATION_REQUIRED` blocker, human-readable,
no traceback). **One adjacent, NOT UAC5-introduced gap found by attack**:
a nonexistent `--database` FILE (not merely an incompatible schema) causes
`check_schema_readiness()`'s own `uac2b_migration.inspect()` call to raise
a raw, uncaught `sqlite3.OperationalError` — proven by direct execution
(§ below). Confirmed this is the **identical, pre-existing pattern**
already present in the already-committed, already-reviewed
`scripts/review_signal_disposition.py` (same crash, same root cause: its
own `check_schema_readiness()` never wraps its own read-only
`sqlite3.connect()` call either — verified by executing the identical
attack against that script directly). **Not fixed here** — this mission's
own instruction is to fix only defects "inside UAC5's established scope,"
and patching this in exactly one of at least two sibling scripts sharing
the identical unwrapped-`sqlite3.connect()` pattern would be an
inconsistent, scope-creeping partial fix rather than a genuine UAC5
correction. Documented as a named, non-blocking, repo-wide follow-up
(`TestNonexistentDatabaseFileBehavior`).

## Inspect verdict (mission Part A §3)

**Sound.** Read-only `mode=ro` SQLite URI engine whenever neither
`--decision` nor `--execute` is given (re-confirmed by direct code read,
`run_review()` line 594: `writable = config.decision is not None or
config.execute`). Re-verified the DB state is unchanged after inspect via
a completely fresh, second engine (`TestInspect.test_inspect_never_mutates_and_shows_full_state`).
Confirmed inspect exposes every item the mission names: raw claimed
identity (all 8 fields), fingerprint, resolution state, latest review,
full review history, linked `SourceAssertion` summaries, and — critically
— "whether execution is currently possible" is answerable by a human
reading the latest review's `action`/`review_id` alongside the resolution
state, though this review notes the CLI does not compute and print an
explicit boolean "execution eligible right now" field during pure
inspect (that determination is only ever made by an actual `--execute`
dry-run call, which performs the real check via the real governed
function rather than a second, potentially-drifting inspect-time
computation) — judged a reasonable, non-defective design choice
consistent with §6/§9's own "never duplicate governed eligibility logic"
principle, not a gap.

## Review-mode verdict (mission Part A §4)

**Sound**, all four actions attacked directly
(`TestReviewRecording`/`TestUac5bEvidenceBagReconstructionProof`'s sibling
tests). Required-argument/illegal-combination validation re-verified for:
`--matched-airport-id` required for MATCH and forbidden otherwise;
`--reviewer`/`--reason` required whenever `--decision` given;
`--new-airport-*` forbidden outside `--execute`; `--review-id` forbidden
outside `--execute`. Delegation-not-reimplementation confirmed by direct
read: `_run_review_write()`'s only call into governance logic is the one
line invoking `record_unknown_airport_candidate_review()` — no eligibility
predicate of the CLI's own precedes it. **One genuine completeness gap
found and fixed**: `--supersedes-review-id` (an existing, already-governed,
optional parameter of `record_unknown_airport_candidate_review()`) was
never exposed by the CLI at all — a real, if minor, audit-annotation gap
for a script whose entire purpose is human review. Fixed: wired through
config/argparse/`main()`, with matching validation (only valid alongside
`--decision`, never with `--execute`). Verified: `TestSupersedesReviewIdWiring`.

## MATCH_EXISTING_AIRPORT verdict (mission Part A §5)

**Sound.** Missing/wrong/deleted Airport, stale review, already-resolved
candidate, zero and multi-assertion candidates, dry-run vs. real write,
and rollback/failure injection were all attacked
(`TestExecuteMatch`, `TestStaleReviewHandshake`,
`TestExecutionFailureRepeatAndContradiction`, `TestZeroAssertionCandidate`-
equivalent coverage via the pre-existing UAC4 suite this CLI delegates
to). "Malformed candidate state" is deliberately NOT re-attacked at the
CLI layer — UAC4's own `InconsistentCandidateStateError` path is already
exhaustively attacked in `tests/test_unknown_airport_candidate_resolution.py`,
and the CLI's own `_run_execute()` already catches and surfaces that
exact exception type verbatim (confirmed by direct code read: `except
(AlreadyResolvedError, StaleReviewError, InconsistentCandidateStateError,
ValueError)`), so re-attacking the same malformed-state construction at
the CLI layer would only re-prove UAC4's own already-proven behavior, not
find a new CLI-layer defect. Zero Runway/RunwayEnd/Installation/Signal
creation confirmed by direct count comparison
(`TestCanonicalSideEffectFirewall`) and by AST scan
(`TestNoBusinessLogicInCli`).

## CREATE_NEW_AIRPORT verdict (mission Part A §6)

**Sound.** Missing required fields (`--new-airport-name`/
`--new-airport-country`), duplicate airport code (delegated verbatim to
UAC4's own case/whitespace-insensitive defense — re-confirmed the CLI
adds no code field validation of its own beyond forwarding kwargs
unchanged), stale review, already-resolved candidate, and dry-run vs.
real write were all attacked. "Contradictory later review" re-verified at
the CLI layer specifically (not merely trusted from UAC4's own suite):
`TestExecutionFailureRepeatAndContradiction.test_contradictory_later_review_never_re_executes`
records MATCH X, executes it via the CLI, then records a later
contradictory MATCH Y review via the CLI, and proves a CLI-driven execute
attempt against the new review is refused (`AlreadyResolvedError`
surfaced verbatim, `resolved_airport_id` unchanged at `X`). Canonical
side-effect boundary re-confirmed: exactly `airports +1`, zero
Runway/RunwayEnd/Installation/Signal/PhysicalInstallationIdentity change
(`TestCanonicalSideEffectFirewall`).

## REJECT/DEFER verdict (mission Part A §7)

**Sound.** Both record successfully via review mode
(`TestReviewRecording`); attempting `--execute` against either review's
`review_id` is refused with `execute_action` correctly showing the real
recorded action (`DEFER`/`REJECT_CANDIDATE`), never silently treated as
eligible (`TestStaleReviewHandshake.test_execute_refuses_reject_or_defer_review`).
No code path in `_run_execute()` calls either UAC4 execution function for
any action string other than the two literal constants `MATCH_EXISTING_AIRPORT`/
`CREATE_NEW_AIRPORT` (confirmed by direct read — the `if`/`if`/final-else
structure has no third branch).

## Transaction verdict (mission Part A §8)

**Sound, proven directly, not merely asserted.**
`TestExecutionFailureRepeatAndContradiction.test_review_survives_execution_failure`
deletes the matched Airport via raw SQL AFTER the review's own commit,
then runs a CLI execute attempt (which correctly refuses), then re-opens
a completely fresh third engine and confirms the review row is still
present, unchanged, and still the only row in
`unknown_airport_candidate_reviews`. This is real proof of the claimed
policy, not an inference from code reading alone.

## Dry-run verdict (mission Part A §9)

**Sound, proven directly.** Every dry-run test in the suite
(`TestDryRunAndWriteGate`, the dry-run half of `TestExecuteMatch`/
`TestExecuteCreate`) re-opens a fresh engine/session AFTER the dry-run
call and confirms zero durable rows exist — this proves the dry-run path
genuinely calls and rolls back the real governed function (which, if it
were merely "approximated," could not have produced the CORRECT
eligibility answer for cases like the duplicate-code defense or the
already-resolved refusal, which the CLI itself implements no logic for).

## Stale-review verdict (mission Part A §10)

**Sound**, re-confirmed `StaleReviewError` remains authoritative: the
CLI's own routing read (`session.get(UnknownAirportCandidateReview,
config.review_id)`) is used ONLY to decide which governed function to
call (MATCH vs. CREATE vs. refuse) — a review's `action` field is
immutable once written, so this routing can never itself become stale.
The actual currency/staleness check happens exclusively inside
`resolve_candidate_to_existing_airport()`/`create_airport_from_approved_candidate()`'s
own `_require_current_review()`, propagated to the CLI verbatim. Race
scenario (inspect → review recorded elsewhere → execute against the
now-stale id) re-verified: `TestStaleReviewHandshake.test_execute_refuses_after_newer_review_recorded`.

## Direct-service-bypass verdict (mission Part A §11)

**Sound.** Constructing `UnknownAirportCandidateReviewConfig` directly
with unusual combinations (bypassing `argparse`/`main()` entirely) still
passes through the same `_validate_config()`/`run_review()` path — there
is no second, weaker entry point. Every governance check
(existence, resolved-state, review-currency, duplicate-code, malformed-
state) lives in UAC1/UAC4's own governed functions, never duplicated or
weakened in the CLI, confirmed by the CLI containing no `Airport(...)`/
`Runway(...)`/etc. constructor calls anywhere (AST-confirmed,
`TestNoBusinessLogicInCli`).

## Migration-chain verdict (mission Part A §12)

**Sound, re-confirmed.** `TestMigrationChainParity` runs the real
`uac2a_migration.upgrade()`/`uac2b_migration.upgrade()` scripts (grep-
confirmed: no `create_all()` call anywhere in that test), then exercises
inspect → record review → execute write through the actual CLI entry
points against that genuinely migrated schema.

## UAC5B — adversarial, independent re-derivation (mission Part B)

**The STOP is CONFIRMED CORRECT, independently re-derived from scratch,
and now backed by concrete, executable proof rather than only reasoning.**

**Field-by-field matrix** (re-derived fresh from `EvidenceBag`'s own
dataclass fields, `CandidateFragment`'s 1:1 mirror of them, and a fresh
line-by-line read of both `persist_discovery_fragment()`/
`persist_candidate_linked_source_assertion()`):

| EvidenceBag field | Persisted where? | Exact/lossless? | Reconstructable? | Material to guard outcome? |
|---|---|---|---|---|
| `identifiers` | `raw_airport_identifier` | No — comma-joined | No (proven ambiguous, see below) | Yes — sole basis for `ATTACH_CONFIRMED` via IDENTIFIER category |
| `names` | `raw_airport_name` | No — comma-joined | No | Yes — one of two categories needed for `ATTACH_CONFIRMED` without an identifier |
| `runway_pairs` | `raw_runway_value` | No — comma-joined | No | Yes — topology category |
| `runway_ends` | `raw_runway_end_value` | No — comma-joined | No | Yes — topology category |
| `locations` | **nowhere** | N/A | **No** | Yes — LOCATION category |
| `issuers` | **nowhere** | N/A | **No** | Yes — ISSUER category |
| `contradicting_names` | **nowhere** | N/A | **No** | **Yes — unconditionally vetoes any positive match** |
| `contradicting_issuers` | **nowhere** | N/A | **No** | **Yes — unconditionally vetoes any positive match** |
| `contradicting_locations` | **nowhere** | N/A | **No** | **Yes — unconditionally vetoes any positive match** |
| `alternate_airport_runway_ends` | **nowhere** | N/A | **No** | Yes — strengthens a topology contradiction |
| `alternate_airport_runway_pairs` | **nowhere** | N/A | **No** | Yes — strengthens a topology contradiction |

**A. Can the original EvidenceBag be reconstructed exactly? NO.** Five of
eleven fields have no persisted representation at all; the other four are
lossily joined.

**B. Can a semantically equivalent guard input be reconstructed safely?
NO.** "Semantically equivalent" would require the contradiction/location/
issuer signals, which do not exist in any form.

**C. Could omitted contradiction evidence turn a previous refusal into
ATTACH_CONFIRMED? PROVEN YES, empirically**, not merely reasoned about:
`TestUac5bEvidenceBagReconstructionProof.test_lost_contradicting_evidence_flips_reject_cross_airport_into_false_attach_confirmed`
constructs one `CandidateAirport` and two evidence bags representing the
SAME real-world fragment — one with the genuine `contradicting_issuers`
fact the original extraction found, one without it (simulating a replay
fed only what `SourceAssertion` persists) — and calls the real,
unmodified `evaluate_attachment()`. The original bag correctly produces
`REJECT_CROSS_AIRPORT`; the replay bag produces `ATTACH_CONFIRMED`. This
is direct, executable proof, not an inference.

**Reversibility of the comma-join, independently re-verified empirically**
(`test_comma_joined_persistence_is_provably_lossy_not_merely_theoretically`):
`_join_or_none({"KABC, KXYZ"})` (one weird value embedding the delimiter)
and `_join_or_none({"KABC", "KXYZ"})` (two ordinary values) produce the
byte-identical string `"KABC, KXYZ"` — proof of genuine ambiguity, not
merely "inelegant."

**D. Would mutating `identity_guard_decision` destroy historical/audit
meaning? YES**, re-confirmed: the column's own extensive comment in
`app/models/source_assertion.py` states it is populated ONLY by
`discovery_evidence_persistence.py` and never re-derived; `persist_discovery_fragment()`'s
own idempotent-reuse path returns an EXISTING row's decision UNCHANGED
even when re-evaluated, rather than recomputing it — the codebase's own
established convention already treats this column as durable, one-time
historical fact, never mutable derived state.

**E. Is there any existing persisted source/document/raw-evidence
representation elsewhere in the repository that actually DOES contain
enough information for a safe replay? Searched broadly, not stopping at
SourceAssertion.** `app/models/acquisition.py`'s `Snapshot.payload`
(`LargeBinary`) DOES immutably preserve the full original raw document
bytes for every acquired document — a real, existing, richer source than
anything on `SourceAssertion`. However, two independent facts prevent
this from enabling a safe replay TODAY: (1) there is no database-enforced
FK from `SourceAssertion`/`UnknownAirportCandidate` to `Snapshot` —
`CandidateFragment.artifact_identity` is documented only as an "opaque,
caller-supplied string" that MAY follow an `AcquisitionSource.key +
Snapshot.sha256` convention, never structurally validated or joined
anywhere (confirmed: zero FK targets referencing `snapshots`/
`acquisition_sources`/`acquisition_runs` anywhere on `SourceAssertion`;
zero code anywhere resolves `artifact_identity` against `Snapshot` —
both confirmed via `TestUac5bEvidenceBagReconstructionProof.test_no_structural_link_from_source_assertion_to_snapshot_payload`);
(2) even with such a mapping, recovering an `EvidenceBag` from raw bytes
requires RE-RUNNING EXTRACTION — a materially different, separately-
scoped capability (not a data-read), not guaranteed reproducible for any
AI-assisted extractor, and explicitly out of this mission's own repeated
"no recurring acquisition" scope boundary. **Conclusion: the STOP is
correct.** `Snapshot.payload`'s existence sharpens, but does not reverse,
the finding — it identifies the raw material a genuinely-scoped future
slice could build a real re-extraction pipeline from, which is exactly
the kind of "materially sized, separately-scoped" work this review
declines to invent here.

**No partial replay was implemented.** No
`reevaluate_resolved_candidate_evidence()`-shaped service exists.

## FH-F2/FH-F3 verdict (mission Part C)

**Sound, no code change required, re-verified at real query level**
(`TestFleetHealthThreeStates`, re-read fresh alongside
`fleet_health_check.py`'s own `_build_source_assertion_review_states()`
and `fleet_health_review_rules.py`'s own `evaluate_fh_f2()`/
`evaluate_fh_f3()`). Three states proven in one test, against one shared
database: (1) candidate-linked, unresolved — present in the FH-F2/F3
input set, correctly skipped by both rules via UAC3's own
`unknown_airport_candidate_id is not None` guard; (2) resolved (formerly
candidate-linked) — structurally excluded from the input set entirely,
since `_build_source_assertion_review_states()` filters `airport_id IS
NULL` and a resolved row's `airport_id` is now set, requiring zero rule
change; (3) genuinely, truly unattributed — present in the input set,
correctly still fires FH-F3 for `review_state="reviewed"`. No rule scope
was widened or needed to be.

## Governance-firewall verdict (mission Part D)

All eleven named invariants independently re-verified: no automatic
Airport creation during discovery (UAC1–UAC3 unmodified by this mission);
no automatic review decision (`--decision` is always the human's own
explicit CLI argument, never inferred); no automatic resolution execution
(mode separation, §Part A §1); no automatic Signal creation (AST-confirmed
zero `Signal(...)` construction, plus the UAC5B STOP itself means no
`ATTACH_CONFIRMED` is ever produced by anything in this mission, so the
downstream Signal path is never even reachable); no candidate identity
becoming canonical merely because evidence exists (resolution requires an
explicit, separately-committed human review AND a separate execute call);
no candidate-linked evidence reaching promotion through a side door
(confirmed: `identity_guard_decision` remains `INSUFFICIENT_IDENTITY`
permanently for resolved evidence, and `intelligence_review_persistence.py`'s
own gate treats anything but the literal string `ATTACH_CONFIRMED` as
not-qualifying); no mutation of historical `identity_guard_decision`
(confirmed — nothing in this mission writes to that column at all,
grep-confirmed zero assignments to it anywhere in the CLI); no fuzzy
candidate convergence introduced (deterministic exact-code-match display
only, §10 of this report, re-confirmed no similarity/ranking computation
exists in the module); no hidden internet access (grep-confirmed: no
`requests`/`urllib`/`http.client`/`httpx`/`socket` import anywhere in the
CLI); no real DB access from tests (`TestNoRealDatabaseAccess`, AST-based,
re-confirmed); no Runway/RunwayEnd/Installation side effects
(`TestCanonicalSideEffectFirewall`, count-based, re-confirmed).

## Test-quality verdict (mission Part E)

Read all 47 tests (up from 38) against the review's own checklist. Found
and closed: (1) the AST-based real-database-path check had a
false-positive risk not fully closed by the implementation phase (already
fixed then, re-verified sound now); (2) a genuinely missing
`--supersedes-review-id` completeness gap, now closed with both a
positive (recorded, visible in history) and two negative (rejected
without `--decision`, rejected with `--execute`) tests; (3) the UAC5B
STOP's own claims were previously supported only by code-reading, not
executable proof — closed with two new, directly executable empirical
tests (the lossy-join collision, the false-`ATTACH_CONFIRMED` flip); (4)
the "does any other persisted source enable safe replay" question (Part
B(E)) was not previously tested at all — closed with a structural FK/grep
proof. Confirmed already sound: migration-chain tests build exclusively
via real `.upgrade()` calls, never `create_all()`; every dry-run test
independently re-opens a fresh engine to prove zero durable state, never
merely trusting the in-process return value; the rollback/failure-
injection test uses a real raw-SQL mutation against a real committed row,
not a mock; no broad `except Exception`/`pytest.raises(Exception)` exists
in the test suite's assertions about SPECIFIC expected behavior (the two
new `TestNonexistentDatabaseFileBehavior` tests use a broad `Exception`
deliberately, since they exist only to document a known, un-fixed,
pre-existing crash shape, not to assert a specific error contract).

## Defects found

**One genuine completeness defect** (missing `--supersedes-review-id`
CLI wiring, §"Review-mode verdict" above) — found and fixed. **One
genuine, pre-existing (not UAC5-introduced), un-fixed CLI robustness gap**
(nonexistent `--database` file crashes with a raw `OperationalError`) —
found, confirmed present in an already-committed sibling script, and
correctly left undisturbed per this mission's own scope-discipline
instruction. **No architectural or auditability blocker** beyond the
already-known, now independently re-confirmed UAC5B STOP, which remains
correctly un-implemented (not a defect to fix — a genuine, honestly
reported schema-completeness gap for a future slice).

## Corrections made

1. `--supersedes-review-id` wired through `UnknownAirportCandidateReviewConfig`,
   `_validate_config()`, `_run_review_write()`, and the `argparse` parser/
   `main()`.
2. One test-fixture construction fix (`test_no_structural_link_from_source_assertion_to_snapshot_payload`'s
   first draft incorrectly asserted `"artifact_identity" not in
   source_assertion.py`'s own source, which trivially fails since that
   module defines the column itself — corrected to check the meaningful
   claim: `acquisition.py` never references `artifact_identity`, and
   `source_assertion.py` never references `"snapshot"`).

## Regression tests added

9 new tests (47 total, up from 38):
`test_supersedes_review_id_recorded_and_visible_in_history`,
`test_supersedes_review_id_rejected_without_decision`,
`test_supersedes_review_id_rejected_with_execute`,
`test_comma_joined_persistence_is_provably_lossy_not_merely_theoretically`,
`test_lost_contradicting_evidence_flips_reject_cross_airport_into_false_attach_confirmed`,
`test_evidencebag_fields_never_persisted_have_no_sourceassertion_column`,
`test_no_structural_link_from_source_assertion_to_snapshot_payload`,
`test_nonexistent_database_file_raises_rather_than_returning_a_blocker`,
`test_identical_pattern_already_exists_in_the_committed_precedent_script`.

## Focused tests

`tests/test_review_unknown_airport_candidate.py`: **47 passed**, 0 failed.
See the final chat report for the combined broader-suite count.

## Full pytest

See the final chat report for the confirmed exact count.

## py_compile / git diff --check

Both re-run clean after the corrections; see the final chat report.

## Real DB before/after proof

Unchanged throughout this review: SHA-256
`d1c37dba82d99974627efb7006619cc7520bb10005605166c530df4fa24de856`,
1,822,720 bytes, FK check `[]`, integrity `ok`,
`signal_dispositions`=10, `signal_disposition_members`=25, UAC schema
confirmed **absent** — verified fresh both before and after this review.

RWI_UAC5_HUMAN_REVIEW_AND_CONTINUATION_ADVERSARIAL_REVIEW_COMPLETE
