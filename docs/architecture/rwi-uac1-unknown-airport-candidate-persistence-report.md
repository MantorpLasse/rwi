# RWI UAC1 — Governed Unknown-Airport Candidate Persistence Foundation

Implementation report. Slice 1 of
`docs/architecture/rwi-governed-new-airport-discovery-design.md`. Starting
checkpoint: HEAD `8dd920c2ebca44addf69d5eccc2f408a903c4166` (== origin/main),
real DB SHA-256 `d1c37dba82d99974627efb7006619cc7520bb10005605166c530df4fa24de856`
(1,822,720 bytes, `signal_dispositions`=10, `signal_disposition_members`=25,
FK check `[]`, integrity `ok`) — verified fresh at the start of this
mission and confirmed byte-identical at the end. No commit, no push, no
migration executed against the real database.

## Files read fresh before implementing

`docs/architecture/rwi-governed-new-airport-discovery-design.md` (this
slice's own governing design); `app/models/airport.py`,
`app/models/source_assertion.py`, `app/models/reviewer_action.py`,
`app/models/physical_installation_identity.py`; `app/services/governed_signal_creation.py`,
`app/services/reviewer_action_persistence.py`, `app/services/discovery_evidence_persistence.py`;
`scripts/migrate_reviewer_action_slice9b.py` and
`tests/test_reviewer_action_migration.py` (migration convention);
`app/models/__init__.py`; `tests/test_reviewer_action_persistence.py`
(test fixture convention). `docs/architecture/ai-discovery-candidate-envelope-lifecycle.md`
§13 was already re-read fresh in the immediately preceding design
mission and not re-read a second time in this one — its content did not
bear on UAC1's own narrower persistence-only scope.

## Files created

- `app/models/unknown_airport_candidate.py` — `UnknownAirportCandidate`,
  `UnknownAirportCandidateReview`, `UNKNOWN_AIRPORT_CANDIDATE_REVIEW_ACTIONS`.
- `app/services/unknown_airport_candidate_persistence.py` —
  `compute_candidate_fingerprint()`, `find_or_create_unknown_airport_candidate()`,
  `record_unknown_airport_candidate_review()`,
  `get_latest_unknown_airport_candidate_review()`, `UnknownAirportCandidateResult`.
- `tests/test_unknown_airport_candidate_persistence.py` — 61 tests.
- This report.

## Files modified

- `app/models/__init__.py` — registered `UnknownAirportCandidate` and
  `UnknownAirportCandidateReview` in imports and `__all__` (additive
  only; no existing entry changed).
- `tests/test_model_contract.py` — extended (not weakened) to include
  the two new tables. See "existing test extended" below.

No other file was touched. `app/models/source_assertion.py` was **not**
modified — see "SourceAssertion-change verdict" below.

## Existing test extended: `tests/test_model_contract.py`

The full pytest run surfaced 3 failures, all in this one file:
`test_model_table_contract_is_unchanged`, `test_model_relationship_contract_is_unchanged`,
`test_current_metadata_creates_cleanly_in_isolated_sqlite`. This is a
frozen schema-drift-detection contract test that hardcodes every table's
exact columns/PK/FKs/indexes and every model's exact relationships
(`EXPECTED_COLUMNS`/`EXPECTED_FOREIGN_KEYS`/`EXPECTED_INDEXES`/
`EXPECTED_RELATIONSHIPS`) and fails whenever `Base.metadata` gains
anything not already listed — its entire purpose is to force an
explicit, reviewed acknowledgment of any schema change, exactly the
guard this project's own established convention (see D4D8B's identical
handling of two hardcoded query-count tests that legitimately changed)
says must be *extended*, never silenced, when the change is real and
intended. Extended by adding `UnknownAirportCandidate`/
`UnknownAirportCandidateReview` to the test file's own model import list
and to all five expectation dictionaries (`EXPECTED_COLUMNS` for both new
tables' exact column/type/nullability shape, `EXPECTED_FOREIGN_KEYS`,
`EXPECTED_INDEXES`, `EXPECTED_RELATIONSHIPS`, and the exported-model-name
list in `test_all_current_models_are_exported_from_app_models`). This
**strengthens** the test: it now also guards the two new tables against
future accidental drift, exactly as it already does for every other
table. All 5 tests in the file pass after the extension, on the first
attempt, with no further correction needed — independent confirmation
that the model's actual DB-level shape exactly matches what this report
describes above, not merely what its docstrings claim.

## `UnknownAirportCandidate` — exact model shape

Table `unknown_airport_candidates`. Columns: `id` (PK), `candidate_fingerprint`
(String(64), indexed, `UniqueConstraint`), `raw_name` (String(200), NOT
NULL), `raw_city`/`raw_state_region`/`raw_country` (String(100), nullable),
`raw_iata_code`/`raw_icao_code`/`raw_faa_lid`/`raw_runway_designation`
(String(20), nullable), `evidence_source_locator`/`evidence_artifact_identity`
(Text, nullable, audit-only), `resolved_airport_id` (nullable FK to
`airports.id`, indexed — **inert**, never read or written anywhere in
this slice's own code), `created_at` (DateTime(timezone=True)). No
`review_state` column (see "deliberate omission" below). Not a shadow
`Airport`: no relationship to `Runway`, `RunwayEnd`, `Installation`, or
`Signal` exists anywhere on this model.

## `UnknownAirportCandidateReview` — exact model shape

Table `unknown_airport_candidate_reviews`. Columns: `id` (PK),
`candidate_id` (FK to `unknown_airport_candidates.id`, NOT NULL, indexed),
`action` (String(30), NOT NULL, `CHECK` vocabulary-constrained), `reason`
(Text, NOT NULL), `reviewer` (String(100), NOT NULL, free text — no auth
infrastructure, matching `ReviewerAction.reviewer`), `matched_airport_id`
(nullable FK to `airports.id`, indexed, `CHECK`-paired with `action`),
`created_at` (DateTime(timezone=True)), `supersedes_review_id`
(self-referential nullable FK, indexed). Three `CheckConstraint`s:
action-vocabulary, `matched_airport_id` required iff
`MATCH_EXISTING_AIRPORT`, `matched_airport_id` forbidden otherwise —
directly mirrors `ReviewerAction`'s own paired-`CheckConstraint`
convention for `duplicate_of_signal_id`. Immutable: `before_update`/
`before_delete` ORM event listeners raise `ValueError`, matching
`ReviewerAction`/`InstallationAssertionLink` exactly.

## Review vocabulary

`UNKNOWN_AIRPORT_CANDIDATE_REVIEW_ACTIONS = ("MATCH_EXISTING_AIRPORT",
"CREATE_NEW_AIRPORT", "REJECT_CANDIDATE", "DEFER")` — confirmed against
the design document's §7, a deliberately distinct tuple/table from
`app.models.reviewer_action.REVIEWER_ACTIONS` (Signal-worthiness
decisions), never merged. `DEFER` legitimately appears in both
vocabularies (an ordinary word both independently governed decisions
use); the three airport-identity-specific actions
(`MATCH_EXISTING_AIRPORT`/`CREATE_NEW_AIRPORT`/`REJECT_CANDIDATE`) never
appear in `REVIEWER_ACTIONS` — proven by test.

## Fingerprint / convergence semantics

`compute_candidate_fingerprint(raw_name, raw_country=None)` — pure,
no I/O: `sha256(casefold(strip(raw_name)) + "|" + casefold(strip(raw_country
or "")))`. Exact convergence only: identical casefolded/stripped
`raw_name`+`raw_country` always produces the identical fingerprint and
therefore converges onto the same candidate row automatically; any other
difference (spelling, claimed city/codes, or the two evidence-provenance
fields) produces a different fingerprint and a separate row. No fuzzy or
similarity matching exists anywhere in this module.

**Uniqueness layer decision:** enforced at the **DB layer** — a
`UniqueConstraint("candidate_fingerprint")` on `unknown_airport_candidates`,
proven by a direct-insert test that bypasses the service entirely. This
follows the exact precedent `SourceAssertion`'s own DB-enforced
fragment-identity `UniqueConstraint` already sets, since the fingerprint
IS this table's entire identity the same way `(source_id, artifact_identity,
source_locator, raw_fragment_hash)` is `SourceAssertion`'s. The service
layer (`find_or_create_unknown_airport_candidate()`) performs a plain
select-then-create — the same convention `persist_discovery_fragment()`
already uses for `SourceAssertion` — and deliberately does **not** catch
or retry `IntegrityError` on a race (proven by test inspecting the
module's own source for the absence of `except IntegrityError`), matching
that same existing precedent's own no-retry posture.

On an exact match, the **existing** row is returned unchanged — a second
call's `raw_*`/`evidence_*` arguments never overwrite an already-existing
candidate, mirroring `persist_discovery_fragment()`'s "existing row
reused UNCHANGED" rule for `SourceAssertion`.

## Append-only behavior

`UnknownAirportCandidateReview` rows are never updated or deleted once
flushed — enforced by ORM event listeners, not merely by convention
(same mechanism as `ReviewerAction`). `get_latest_unknown_airport_candidate_review()`
determines "current" state purely by recency (`created_at` desc, `id`
desc tiebreak) over the full append-only history — it never walks
`supersedes_review_id`, mirroring `get_latest_reviewer_action()`'s own
documented reasoning exactly. A DEFER -> DEFER -> CREATE_NEW_AIRPORT
sequence (and a DEFER -> REJECT_CANDIDATE sequence) both leave every
prior row intact and unmodified — proven by test (fixture G).

`get_latest_unknown_airport_candidate_review()` was included in this
slice because it is a **plain recency query**, structurally identical to
the already-proven `get_latest_reviewer_action()` precedent — it
interprets nothing about an action's meaning, walks no chain, and never
touches `candidate.resolved_airport_id`. It is explicitly **not** the
heavier, not-yet-built resolution service (design doc §8:
`create_airport_from_approved_candidate()` / `link_candidate_to_existing_airport()`)
that will eventually consume its result to decide whether to act — that
remains entirely out of scope for UAC1.

## Canonical-side-effect proof

Six independent proofs, all in `TestNoCanonicalSideEffects`:

1. Runtime: after exercising every review action including
   `CREATE_NEW_AIRPORT` and `MATCH_EXISTING_AIRPORT`, `Airport` row count
   is unchanged.
2. Runtime: `Runway`/`RunwayEnd`/`Installation`/`Signal` row counts are
   all zero after every operation this module supports.
3. Runtime: `candidate.resolved_airport_id` remains `None` after a
   `MATCH_EXISTING_AIRPORT` review naming a real, existing `Airport`.
4. Runtime: a pre-existing `Airport` row's own fields are byte-identical
   before and after a `MATCH_EXISTING_AIRPORT` review points at it.
5. Static: no function in `unknown_airport_candidate_persistence.py`
   accepts a parameter named like a canonical-object FK
   (`runway_id`/`runway_end_id`/`installation_id`/`signal_id`/`signal`)
   or type-annotated as `Runway`/`RunwayEnd`/`Installation`/`Signal`.
6. Static (AST): neither the persistence module nor the model module
   contains an `ast.Call` node constructing `Airport(`, `Runway(`,
   `RunwayEnd(`, `Installation(`, or `Signal(` anywhere — the same
   AST-level proof technique this project's own D4D8D critical review
   already established as its `test_decision_comes_only_from_config_ast`
   precedent.

## SourceAssertion-change verdict

**Not changed, and fresh inspection confirmed this was unnecessary for
UAC1's own scope.** `SourceAssertion.airport_id` was already nullable
before this mission (confirmed independently in the preceding design
mission), so nothing about UAC1's candidate/review persistence required
touching it. UAC1 deliberately does not import, construct, or reference
`SourceAssertion` anywhere — the design's own additive
`SourceAssertion.unknown_airport_candidate_id` column (design doc §5) is
explicitly UAC2's integration seam, not this slice's. Provenance/evidence
traceability for UAC1 is instead carried on `UnknownAirportCandidate`
itself via two audit-only, non-FK text fields
(`evidence_source_locator`/`evidence_artifact_identity`), populated from
the same field vocabulary `CandidateFragment` already uses, without
creating any live relationship to `SourceAssertion` — deliberately
avoiding the "two evidence schemas" failure mode the design document's
§14 already rejected (Option 2).

## Deliberate design decision: no `review_state` column

Unlike `SourceAssertion.review_state`, `UnknownAirportCandidate` carries
no cached `review_state`/status field. RWI's own established convention
throughout this pipeline (`ReviewerAction`, `InstallationAssertionLink`)
is that "current" review status is always derived fresh by recency from
an append-only history table, never cached on the parent row — caching
it here would risk exactly the stale-field-drift class of defect this
project's D4D8 work repeatedly guarded against. "Unreviewed" is simply
"zero rows exist in `unknown_airport_candidate_reviews` for this
candidate," verifiable via `get_latest_unknown_airport_candidate_review()`
returning `None`.

## Tests

61 tests in `tests/test_unknown_airport_candidate_persistence.py`,
organized into: candidate persistence and convergence (fixtures A/B/C,
7 tests), malformed identity observations (3), fingerprint determinism
including order-independence and the evidence-provenance information
firewall (5), the DB-layer fingerprint-uniqueness backstop (2), review
recording (fixtures D/E/F/G, 9), invalid review input including
DB-layer `CHECK`-constraint bypass tests (11), immutability (3),
ordering/timestamp determinism (3), no-canonical-side-effects (6),
transaction rollback (3), missing schema (1), model-shape verification
independent of the module's own docstrings (7), and full-schema
integrity/migration-shaped smoke tests (2). All fixtures are entirely
fictional ("Foo Regional Airport," "Fictionland," etc.) — no real
airport, city, or evidence text appears anywhere in the test file.

### Adversarial defects found and corrections made

Five test-authoring defects were found and corrected during
implementation (all in the test file itself; **zero production-code
defects** were found in `unknown_airport_candidate.py` or
`unknown_airport_candidate_persistence.py`):

1. `test_reviewer_action_vocabulary_is_never_reused_here` originally
   asserted full vocabulary disjointness between the two review-action
   tuples; `"DEFER"` legitimately appears in both (an ordinary English
   word, not a governance leak). Corrected to assert the two tuples are
   distinct and that only the three airport-identity-specific actions
   are absent from `ReviewerAction`'s own vocabulary.
2. The identical-timestamp tiebreak test passed a raw ISO string as
   `created_at` instead of a `datetime` object, which SQLite's DateTime
   type rejects. Corrected to construct a real `datetime(..., tzinfo=UTC)`.
3. The "no Runway/Installation/Signal parameter" static-shape test
   originally used a bare substring match (`"runway" in param_name.lower()`),
   which false-positived on the legitimate claimed-evidence field
   `raw_runway_designation` (a plain string, never an object reference).
   Corrected to check exact FK-shaped parameter names and type
   annotations instead of substrings.
4. The "never commits" static-source test banned the substring
   `"SessionLocal"` outright, which false-positived on the module's own
   docstring prose explicitly explaining that it does *not* import
   `app.database.SessionLocal`. Corrected to check for the actual import
   statement.
5. The delete-cascade test expected a raw `IntegrityError` from the FK
   constraint. Investigation showed the actual mechanism fires one layer
   earlier: SQLAlchemy's default (no explicit cascade) behavior on
   deleting a parent with dependent rows is to first attempt to `NULL`
   the child's FK column, and `UnknownAirportCandidateReview`'s own
   immutability `before_update` listener rejects that `UPDATE` outright
   — the same `ValueError` any other attempted edit to a review row
   raises. This is a genuine, useful finding (documented here, not a
   defect): the immutability guard incidentally provides a *second*,
   earlier line of defense against orphaning review history, on top of
   the `NOT NULL` + FK constraint that would otherwise catch it. Test
   corrected to accept either exception type and documents this in its
   own docstring.

### Focused test result

`tests/test_unknown_airport_candidate_persistence.py`: **61 passed**, 0
failed.

Adjacent existing governance/evidence tests re-run for regression
confidence (`test_reviewer_action_persistence.py`,
`test_physical_installation_reconciliation.py`,
`test_governed_signal_creation.py`, `test_discovery_evidence_persistence.py`):
**108 passed**, 0 failed — proving UAC1's additive model registration in
`app/models/__init__.py` did not disturb any existing governance
behavior.

### Full pytest result

**2538 passed, 0 failed** (`pytest -q`, 260.53s). The first full run (before
`tests/test_model_contract.py` was extended) reported 3 failed / 2535
passed = 2538 collected — confirming the extension above changed zero
test outcomes for any pre-existing test and only fixed the 3 that
legitimately needed the two new tables added to their expectations.

### py_compile

`app/models/unknown_airport_candidate.py`, `app/models/__init__.py`,
`app/services/unknown_airport_candidate_persistence.py`,
`tests/test_unknown_airport_candidate_persistence.py`,
`tests/test_model_contract.py` — all compile cleanly.

### git diff --check

Clean (exit 0) on all five changed/created files, verified via
`git add -N` (intent-to-add, no content staged) followed by
`git diff --check` — no trailing whitespace or conflict markers.

## What was deliberately NOT implemented

- No `SourceAssertion` schema change (see verdict above).
- No candidate-selection integration — nothing in this slice decides
  *when* evidence qualifies as "no known Airport candidate matched";
  callers supply already-decided keyword arguments.
- No `CandidateFragment`/`EvidenceBag` import or dependency of any kind.
- No canonical creation services (`create_airport_from_approved_candidate()`,
  `link_candidate_to_existing_airport()`) — named in the design doc §8,
  not built here.
- No `add_runway_to_resolved_airport()`/`add_runway_end_to_runway()`.
- No human-review CLI.
- No near-duplicate/advisory-similarity query (design doc §9's
  advisory-only convergence aid).
- No migration script and no migration executed against any real
  database — every test uses an isolated in-memory or `tmp_path` SQLite
  database created via `Base.metadata.create_all()`.

## Migration verdict

No migration was written or run in UAC1, by deliberate choice — every
test's schema need is satisfied by `Base.metadata.create_all()` against
an isolated fixture database, so leaving migration to UAC2 was "cleanly
possible" per the mission's own preference.

**Exact UAC2 scope still to migrate — CORRECTED during the UAC1 critical
review** (see the addendum below): the original version of this section
claimed two separate migration scripts for reasons that did not hold up
under fresh inspection of actual repository precedent.
`scripts/migrate_evidence_identity_slice6c.py` (`TABLES =
("physical_installation_identities", "installation_assertion_links")`)
proves the established convention is **one script per tightly-coupled
slice**, not one script per table — it creates both
`PhysicalInstallationIdentity` (parent) and `InstallationAssertionLink`
(its own append-only review/link child) together in a single migration,
which is structurally the exact same shape as UAC1's own
`UnknownAirportCandidate` + `UnknownAirportCandidateReview` pair.

1. **One** migration script (e.g.
   `scripts/migrate_unknown_airport_candidate_uac1.py`, following
   `scripts/migrate_reviewer_action_slice9b.py`'s exact convention:
   `upgrade(database)`/`downgrade(database)`/`inspect(database)`/
   `main(argv)` gated behind `--allow-database-write`, `CreateTable`/
   `CreateIndex` compiled directly from `Base.metadata.tables[...]`, a
   required pre-write timestamped backup) creating **both**
   `unknown_airport_candidates` and `unknown_airport_candidate_reviews`
   together against the real `data/runway_safe.db` — matching the
   `migrate_evidence_identity_slice6c.py` precedent exactly.
2. A **separate**, later migration for `SourceAssertion.unknown_airport_candidate_id`
   (nullable FK + the mutual-exclusivity `CheckConstraint` against
   `airport_id`, design doc §5) — this remains its own script, not
   combined with (1), because it is a materially different kind of
   change: an additive column on an **existing, already-migrated,
   real-data-bearing** table, versus (1)'s creation of **brand-new**
   tables with no existing rows to preserve. This is the same
   risk/rollback distinction the `scripts/migrate_*` corpus already
   draws elsewhere (e.g. `migrate_governed_signal_creation_slice9c.py`
   and `migrate_reconciliation_confirmation_slice_r4b.py` are separate
   scripts from the `reviewer_actions` table's own original creation,
   even though both add columns/behavior in the same broader area) —
   sequencing/risk, not an arbitrary per-table count, is what should
   determine script boundaries.

## Real DB post-check

SHA-256 `d1c37dba82d99974627efb7006619cc7520bb10005605166c530df4fa24de856`,
size 1,822,720 bytes — **byte-identical** to the starting checkpoint.
`PRAGMA foreign_key_check` = `[]`, `PRAGMA integrity_check` = `ok`,
`signal_dispositions`=10, `signal_disposition_members`=25 — all
unchanged.

## git status

Working tree contains this slice's new/modified files plus the same
pre-existing untracked files already present at the start of this
mission (prior design docs, screenshots, `docs/research/`). Nothing was
staged for commit or committed; nothing was pushed.

## READY_FOR_UAC1_REVIEW_CHECKPOINT: yes

---

# Critical review addendum

Adversarial review performed against fresh reads of the design document,
both implementation files, both test files, and directly-verified
repository precedent (not merely trusting this report's own claims
above). Two genuine defects were found and corrected; everything else
below is either an independently-confirmed pass or an explicitly
documented, deliberate, non-defect limitation.

## Domain-boundary verdict

**Sound.** Fresh inspection confirms `UnknownAirportCandidate` has no
relationship to `Runway`/`RunwayEnd`/`Installation`/`Signal` anywhere in
the model; `resolved_airport_id` remains unwritten by every function in
the persistence module (re-verified by re-reading both files fresh, not
assumed from the prior report). The distinction ("candidate ≠ provisional
Airport ≠ hidden Airport ≠ auto-approved identity") is enforced
structurally — by the complete absence of any code path that constructs
or mutates a canonical row — not merely by docstring convention.

## UnknownAirportCandidate shape verdict

**Sound, with one field's role clarified.** Every `raw_*` field is a
genuine claimed-evidence value, correctly nullable except `raw_name`,
correctly excluded from any canonical validation. `evidence_source_locator`/
`evidence_artifact_identity` are confirmed audit-only snapshots of the
*first-observed* fragment only (see "provenance-model verdict" below) —
not a substitute for real multi-evidence linkage. `resolved_airport_id`
is a real FK, correctly nullable, and — after this review's correction —
is now the *only* field the model permits to change after creation (see
"candidate immutability verdict").

## Fingerprint safety verdict — GENUINE DEFECT FOUND AND CORRECTED

**The original two-field key (`sha256(casefold(name)+casefold(country))`)
was unsafe and has been corrected.** Attack case C (same generic name,
same country, different city) proved the defect directly: two evidence
claims for `raw_name="Municipal Airport"`, `raw_country="Exampland"` but
`raw_city="City A"` vs. `raw_city="City B"` — a realistic scenario, not a
contrived edge case, since generic airport names ("Municipal",
"Regional", "Executive") recur across many towns within one country —
would have silently converged onto a single `UnknownAirportCandidate`
row, discarding the fact that they concern two different real places.
This is exactly the "false automatic convergence" the design's own §9
forbids, even though the mechanism (a byte-exact hash, no similarity
scoring) is not "fuzzy" in the literal sense — the defect was that the
key's *chosen field set* was too narrow to be a safe exact-identity
proxy, not that the hashing technique itself was heuristic.

**Correction:** `compute_candidate_fingerprint()` now hashes
`raw_name + raw_city + raw_state_region + raw_country` (still casefolded,
stripped, still a pure deterministic function, still no similarity
scoring of any kind). This only ever makes convergence *stricter* —
proven by test (`test_same_name_city_state_country_still_converges_exactly`
confirms the fix doesn't over-correct into false separation of genuinely
identical claims). The corrected key is documented in both
`compute_candidate_fingerprint()`'s own docstring and
`docs/architecture/rwi-governed-new-airport-discovery-design.md` §9,
kept in sync. Claimed codes (IATA/ICAO/FAA LID) remain deliberately
excluded from the key — see "fingerprint normalization verdict."

Nine new tests in `TestFingerprintSafetyCriticalReviewCorrection` cover:
the exact false-merge case now prevented (city and, separately,
state/region variants), confirmation the fix doesn't block genuine exact
convergence, missing-vs-present optional fields failing closed rather
than guessing, codes deliberately excluded from the key (both the benign
case — same location, different code reported — and the documented,
accepted limitation — same location, *contradictory* codes still
converge), Unicode/accented-name convergence and non-convergence,
punctuation and internal-whitespace sensitivity (proving no hidden
heuristic normalization), and an authority-name near-variant that must
not fuzzy-converge.

## Fingerprint normalization verdict

**Sound, narrow, and now explicitly tested.** The only canonicalization
performed is `strip()` + `casefold()` — confirmed by fresh reading of
`_norm()`. No whitespace collapse, no punctuation removal, no accent
folding, no airport-word removal exists anywhere in the function — each
was independently attacked and proven absent by test (internal-whitespace
test, punctuation test, accented-vs-unaccented test). This is the correct
line: `casefold()` is deterministic normalization of a single already-
claimed string; anything beyond it would begin inferring identity rather
than reading it. Claimed codes remain out of the key by deliberate
design choice, documented in the function's own docstring: codes are
reported far more inconsistently per fragment than location fields
(one fragment may report only ICAO, another only IATA, for the same real
airport), so including them would fragment genuinely-identical claims
more than it would protect against false merges; a code disagreement
under an otherwise-matching location fingerprint is correctly classified
as a near-duplicate/human-review question (design doc §9), not a
convergence-key question, and is documented as a known, accepted,
non-defect limitation, proven by
`test_contradictory_codes_under_matching_location_still_converge_not_a_defect`.

## Candidate immutability verdict — GENUINE DEFECT FOUND AND CORRECTED

**Original implementation had no protection against direct-ORM mutation
of `UnknownAirportCandidate`'s own claim fields after creation — a real
gap, now closed.** Before this review, nothing stopped
`candidate.raw_name = "different name"; session.commit()` after a review
had already been recorded against the candidate as it originally stood —
silently corrupting the meaning of that review's own history (a reviewer
who approved `CREATE_NEW_AIRPORT` for one claimed name/location could
have that claim rewritten underneath the approval with no trace).

**Correction:** a new `before_update` event listener on
`UnknownAirportCandidate` rejects any change to any column *except*
`resolved_airport_id`, inspecting column-level history via SQLAlchemy's
`instance_state(target).get_history()` rather than rejecting every
`UPDATE` unconditionally — the row-level "reject everything" approach
`UnknownAirportCandidateReview` already uses was deliberately **not**
reused here, because `resolved_airport_id` must remain legitimately
settable by the not-yet-built governed resolution service (design doc
§8). This is the narrow, field-level distinction the review explicitly
asked for: "immutable source-derived candidate facts" vs. "fields
intentionally allowed to change through future governed resolution."
Thirteen new tests in `TestCandidateFieldImmutability` cover: every
individual claim field rejected on direct mutation (parametrized over
all eleven candidate fields other than `id`/`created_at`/
`resolved_airport_id`), `resolved_airport_id` itself confirmed still
settable (simulating the future service), and a mixed update (one
legitimate field change alongside one forbidden field change) confirmed
still rejected in full — partial enforcement would be worse than none.

## resolved_airport_id verdict

**Correctly inert for UAC1; cross-row consistency is correctly deferred,
not a UAC1 defect.** Nothing in UAC1's own code path can produce an
inconsistent state (e.g. `resolved_airport_id` disagreeing with the
latest review's `matched_airport_id`, or a `CREATE_NEW_AIRPORT` review
coexisting with a `resolved_airport_id` pointing at an unrelated
pre-existing `Airport`) — the field is never written by anything in this
slice, confirmed by the pre-existing `TestNoCanonicalSideEffects` tests
and re-verified fresh in this review. Validating consistency between
`resolved_airport_id` and review history at *set* time is correctly the
responsibility of the future governed resolution service (design doc
§8: `create_airport_from_approved_candidate()` /
`link_candidate_to_existing_airport()`) that will be the only code ever
legitimately writing to it — building that consistency check now, before
the writer exists, would require inventing resolution-service logic this
slice explicitly must not implement (mission §22: "Do not widen into
UAC2"). The one thing UAC1 *could* and now does add at its own layer is
the field-level immutability guard above, which ensures at least that
the *claim* `resolved_airport_id` might eventually resolve to cannot
silently drift out from under it.

## Review-vocabulary verdict

**Sound, and now exhaustively attacked.** `UNKNOWN_AIRPORT_CANDIDATE_REVIEW_ACTIONS`
is confirmed as its own distinct tuple from `ReviewerAction.REVIEWER_ACTIONS`
(fresh comparison of both source files) — "DEFER" legitimately appears in
both as an ordinary word; the three airport-identity-specific actions
never appear in `REVIEWER_ACTIONS`. Four new tests close the exact
vocabulary-bypass gaps the review's own attack list named: lowercase
(`"defer"`), padded (`" DEFER "`), `None`, and `bytes` (`b"DEFER"`) are
all correctly rejected — the exact-string-membership check
(`action not in UNKNOWN_AIRPORT_CANDIDATE_REVIEW_ACTIONS`) requires no
correction; it already rejected all four, but the coverage gap (untested,
even though correct) is now closed.

## Conditional-constraint verdict

**Sound at both layers, confirmed by fresh re-execution of the existing
DB-bypass tests plus one new one.** `MATCH_EXISTING_AIRPORT` requires
`matched_airport_id`; every other action forbids it — enforced by a
service-level check (re-read fresh) *and* by two paired `CheckConstraint`s
(re-read fresh from the model), each independently proven by a direct-ORM
test that bypasses the service. `CREATE_NEW_AIRPORT` carries no target of
any kind, by construction (the CHECK forbids `matched_airport_id` for any
non-`MATCH_EXISTING_AIRPORT` action) — there is no way for it to
"contradict" an existing match, since it structurally cannot carry one.
`REJECT_CANDIDATE`/`DEFER` correctly never touch `resolved_airport_id`
(proven by `TestNoCanonicalSideEffects`). One new test
(`test_direct_orm_insert_referencing_nonexistent_candidate_id_is_rejected_by_the_db`)
closes a real coverage gap: the service-level "candidate must exist"
check was already tested, but the raw FK constraint's own independent
enforcement (for a caller bypassing the service entirely) was not,
unlike the equivalent precedent in `test_reviewer_action_migration.py`.

## History/latest-semantics verdict

**Sound.** `get_latest_unknown_airport_candidate_review()` re-read fresh:
pure recency query (`created_at` desc, `id` desc tiebreak), never walks
`supersedes_review_id`, matching `get_latest_reviewer_action()` exactly.
DEFER→DEFER→CREATE_NEW_AIRPORT and DEFER→REJECT_CANDIDATE were already
tested; DEFER→MATCH_EXISTING_AIRPORT (explicitly named in the review's
own §10) was not and is now covered by
`test_defer_then_match_existing_airport_chain`, which also re-confirms
`resolved_airport_id` stays `None` through the chain. Omitting an
operational "current resolution" service in UAC1 is deliberate and
correct — recency alone is a data-access primitive, not a resolution
decision (see the original report's own reasoning, re-confirmed here).

## Supersession verdict

**Sound; two attack vectors newly confirmed, one pre-existing gap
explicitly documented as accepted (not fixed).** Cross-candidate
supersession is rejected (pre-existing test, re-verified). Nonexistent
supersession targets are rejected (pre-existing test, re-verified).
**Self-supersession/cycles via direct ORM bypass are not prevented by
any constraint** — deliberately left unfixed, because
`app.models.reviewer_action.ReviewerAction.supersedes_action_id` (the
exact structural precedent this table is modeled on, re-read fresh) has
the identical property: no cycle-prevention `CheckConstraint` exists
there either, and no code anywhere in this pipeline ever walks either
chain (`get_latest_*` is always recency-only). Adding cycle prevention
here that the precedent itself doesn't have would be scope creep beyond
what this slice's own reused pattern requires, not a correction of a
genuine defect. Competing independent roots (two reviews, both with
`supersedes_review_id=None`, for the same candidate) are already
exercised by the identical-timestamp tiebreak test.

## Append-only / direct-ORM-bypass verdict

**Sound.** Update and delete on `UnknownAirportCandidateReview` are both
blocked by `before_update`/`before_delete` listeners (re-verified fresh,
unchanged by this review). Malformed direct `INSERT`s (invalid action,
missing/extra `matched_airport_id`) are blocked by `CheckConstraint`s
(re-verified fresh). The one new gap closed: a direct `INSERT` naming a
nonexistent `candidate_id` is now proven rejected by the raw FK
constraint itself, not merely by the service-level guard.

## Candidate-delete/history-preservation verdict

**Sound, and the exact mechanism is now documented precisely.** Deleting
a candidate with review history is blocked — but, as the original report
already noted, by `UnknownAirportCandidateReview`'s own immutability
listener firing on SQLAlchemy's implicit "null the child FK" attempt
before the raw FK/`NOT NULL` constraint would ever be reached. This
remains true and unchanged after adding the new candidate-level
immutability listener (which only governs updates to *candidate* rows,
not the delete-cascade-attempt path, which touches the *review* row).

## Provenance-model verdict

**Not a blocking defect; documented explicitly as a known, deliberate,
temporary limitation rather than left implicit, per the review's own
instruction.** `evidence_source_locator`/`evidence_artifact_identity` are
single-snapshot audit fields capturing only the *first-observed*
fragment's identity — confirmed by test
(`test_fixture_b_same_exact_evidence_encountered_again_converges_not_duplicates`
already proved a second fragment's own provenance values are discarded,
not overwritten into the existing row). This does **not** paint UAC2 into
a corner: UAC2's own explicit job (design doc §5) is to add
`SourceAssertion.unknown_airport_candidate_id` — a genuine many-to-one
relational link from the real evidence table (`SourceAssertion`) to
`UnknownAirportCandidate` — which is the actual multi-evidence
traceability mechanism these two text fields were never meant to
replace. Nothing in UAC1's schema blocks or constrains that future
column in any way. The module's own docstring already described these
fields as "audit-only... without creating any live relationship to
SourceAssertion"; this review confirms that framing is accurate and
sufficiently explicit, and no code change was needed — only this
report's own clearer articulation of the limitation.

## Multi-evidence convergence verdict

**Sound, confirmed by existing test, no evidence is silently lost.**
Adapter A and Adapter B independently discovering the exact same
candidate: the second call returns the *existing* candidate
(`created=False`), never fails, never raises, never duplicates. Adapter
B's own evidence is not owned or destroyed by this module — it continues
to exist wherever Adapter B's own pipeline stage (a `SourceAssertion` row
today, or its UAC2 equivalent) already stores it; UAC1's own scope is
narrowly "does a second exact claim create a second row" (no), not
"where does every fragment's provenance ultimately live" (that answer is
UAC2's `SourceAssertion` link, per the provenance-model verdict above).

## Existing-airport recovery verdict

**Sound.** Re-confirmed by fresh test execution: candidate remains fully
queryable after a `MATCH_EXISTING_AIRPORT` review, the review itself is
fully auditable (immutable, timestamped, reviewer-attributed), the
pre-existing `Airport` row is byte-unchanged (field-by-field comparison,
pre-existing test), no duplicate `Airport` is created, and linkage
metadata (`matched_airport_id` on the review row) is internally
consistent with the target `Airport`'s continued existence (verified via
`session.get(Airport, matched_airport_id) is None` check in the service
itself).

## Canonical-side-effect verdict

**Sound, re-proven independently.** All six original proofs
(`TestNoCanonicalSideEffects`) re-run fresh after both corrections above
and still pass unchanged: zero `Airport`/`Runway`/`RunwayEnd`/
`Installation`/`Signal` rows created under any reviewed action including
`CREATE_NEW_AIRPORT`; `resolved_airport_id` still never set by any
function; a pre-existing `Airport`'s fields are still byte-unchanged
after `MATCH_EXISTING_AIRPORT`; static signature/AST scans still find
zero canonical-object parameters or constructor calls in either module.
The two corrections made during this review (fingerprint composite key,
candidate field immutability) touch neither canonical tables nor
canonical-object construction in any way.

## Transaction/failure-atomicity verdict

**Sound, unchanged by this review's corrections.** Both persistence
functions still validate before any `session.add()` where feasible, still
call `session.flush()` (never `session.commit()`, confirmed by static
source-scan test), still leave the caller owning the transaction
boundary. Uncommitted candidate/review creation is discarded cleanly on
rollback (pre-existing tests, re-verified). No partial canonical side
effect is possible since no canonical side effect exists anywhere in this
module.

## Model-registration/contract verdict

**Sound.** `app/models/__init__.py` diff re-inspected fresh: purely
additive (two new imports, two new `__all__` entries, nothing else
touched). `tests/test_model_contract.py` diff re-inspected fresh: every
addition is a genuine, verified expectation (columns/types/nullability/
FKs/indexes/relationships for both new tables, plus both new model names
in the exported-model-list test) — none of the pre-existing 2538 other
assertions were weakened, loosened, or removed; `configure_mappers()`
and a fresh `create_all()` both still succeed (all 5 tests in the file
pass). The two corrections made in this review (fingerprint composite
key, immutability listener) touch neither table's DDL shape, so no
further contract-test changes were required — confirmed by re-running
`test_model_contract.py` after both corrections with zero changes
needed.

## Source-neutral/international verdict

**Sound, and now explicitly tested rather than merely asserted.** Fresh
grep of both implementation files for source/vendor-specific terms
(MAC, Granicus, USAspending, FAA-as-a-data-source, n8n, any LLM/model
name) found zero references — the field name `raw_faa_lid` names a
*generic claimed code format* (parallel to `raw_iata_code`/`raw_icao_code`),
not a dependency on the FAA as a producer. New Unicode/accented-name
tests (`test_unicode_and_accented_names_converge_exactly_and_only_exactly`)
prove the fingerprint behaves correctly for non-English claims without
requiring any locale-specific logic, closing the review's own explicit
"use synthetic international/Unicode fixtures" instruction, which the
original test suite had not yet exercised.

## UAC2 migration-boundary verdict — CORRECTED

**The original report's "two separate migration scripts" claim was
imprecise and has been corrected in the Migration Verdict section above.**
`scripts/migrate_evidence_identity_slice6c.py` (`TABLES =
("physical_installation_identities", "installation_assertion_links")`,
confirmed by fresh direct inspection) proves the established convention
creates multiple tightly-coupled tables in **one** migration script when
they belong to one architectural slice — exactly UAC1's own two-table
shape. The corrected recommendation: **one** script for both new UAC1
tables, and a **separate**, later script for the `SourceAssertion`
column addition (a materially different, higher-risk change to an
existing, real-data-bearing table). This is a documentation-only
correction — no migration script exists in either version, so no test or
production-code change was required.

## Defects found

1. **Fingerprint composite key too narrow** (production defect, real
   risk of false-merging two distinct real airports under a shared
   generic name) — corrected.
2. **No field-level immutability on `UnknownAirportCandidate`**
   (production defect, real risk of a claim silently drifting out from
   under an already-recorded review) — corrected.
3. **Migration-boundary guidance in the original report was wrong**
   (documentation defect, not a code defect) — corrected.

No other genuine defects were found. Every other reviewed dimension
(domain boundary, canonical-side-effect isolation, transaction ownership,
append-only enforcement, review-vocabulary/constraint validation,
model-contract accuracy, source-neutrality) was independently
re-confirmed sound by fresh reading and fresh test execution, not merely
re-asserted from the original report.

## Corrections made

1. `compute_candidate_fingerprint()` widened to
   `raw_name + raw_city + raw_state_region + raw_country`; caller updated;
   design doc §9 updated to match.
2. New `before_update` event listener on `UnknownAirportCandidate`
   enforcing field-level immutability on every column except
   `resolved_airport_id`.
3. Migration-boundary guidance corrected in this report's own Migration
   Verdict section (documentation only).

## Regression tests added

29 new tests (90 total, up from 61): 9 in
`TestFingerprintSafetyCriticalReviewCorrection` (the primary attack and
its surrounding edge cases), 1 DEFER→MATCH_EXISTING_AIRPORT chain test,
4 review-vocabulary-bypass tests (lowercase/padded/None/bytes), 1
direct-ORM FK-violation test for a review row, 13 in
`TestCandidateFieldImmutability` (11 parametrized per-field cases + the
`resolved_airport_id`-remains-settable case + the mixed-update case),
plus 1 already implicitly present. Every new test targets a genuine gap
named explicitly in this review's own attack list — none are ceremony.

## Focused test result

`tests/test_unknown_airport_candidate_persistence.py`: **90 passed**, 0
failed (up from 61, after the two corrections and 29 new tests).
`tests/test_model_contract.py`,
`tests/test_reviewer_action_persistence.py`,
`tests/test_physical_installation_reconciliation.py`,
`tests/test_governed_signal_creation.py`,
`tests/test_discovery_evidence_persistence.py`: **113 passed**, 0 failed
(re-run after both corrections).

## Full pytest result

See the final chat report for the confirmed exact count.

## py_compile / git diff --check

Both re-run clean after the corrections; see the final chat report.

## Real DB before/after proof

Unchanged throughout this review: SHA-256
`d1c37dba82d99974627efb7006619cc7520bb10005605166c530df4fa24de856`,
1,822,720 bytes, FK check `[]`, integrity `ok`,
`signal_dispositions`=10, `signal_disposition_members`=25 — verified
fresh both before and after this review, byte-identical throughout.

RWI_UAC1_UNKNOWN_AIRPORT_CANDIDATE_PERSISTENCE_IMPLEMENTATION_COMPLETE
