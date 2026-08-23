# RWI EB1 — Lossless EvidenceBag Serialization + Immutable Persistence Models

Implementation report. Slice 1 of
`docs/architecture/rwi-full-evidencebag-persistence-design.md`. Synthetic
implementation only — not committed by this mission; a separate
adversarial EB1 review checkpoint commits/pushes if sound.

## 1. Scope

EB1 implements exactly three things: (A) deterministic, lossless
`EvidenceBag` ↔ JSON serialization; (B) the immutable
`SourceAssertionEvidenceBag` ORM model (one frozen snapshot per
`SourceAssertion`); (C) the immutable, append-only `IdentityGuardEvaluation`
ORM model (the persistence shape a future EB4 re-evaluation service will
write to — no such service exists yet). No migration, no discovery-
persistence wiring, no re-evaluation service, no CLI, no real database
change, no live internet access.

## 2. Files

**Created:**
- `app/services/evidence_bag_serialization.py`
- `app/models/source_assertion_evidence_bag.py`
- `app/models/identity_guard_evaluation.py`
- `tests/test_evidence_bag_persistence.py` (57 tests)
- This report.

**Modified:**
- `app/models/__init__.py` — additive imports/exports for the two new
  model classes only.
- `tests/test_model_contract.py` — additive entries for the two new
  tables' columns/FKs/indexes/relationships, and the two new class names
  added to the exported-models list. No existing assertion was weakened.

## 3. Files read fresh

`docs/architecture/rwi-full-evidencebag-persistence-design.md`,
`docs/architecture/rwi-uac4-unknown-airport-resolution-report.md`,
`docs/architecture/rwi-uac5-human-review-and-continuation-report.md`,
`app/services/evidence_attachment_guard.py`,
`app/services/discovery_candidate_fragment.py`, `app/models/source_assertion.py`,
`app/models/acquisition.py` (Snapshot), `app/models/reviewer_action.py`,
`app/models/unknown_airport_candidate.py` (UnknownAirportCandidateReview),
`app/models/physical_installation_identity.py`, `tests/test_model_contract.py`.

## 4. Complete EvidenceBag serialization contract

`serialize_evidence_bag(evidence_bag) -> str` / `deserialize_evidence_bag(payload: str) -> EvidenceBag`,
in `app/services/evidence_bag_serialization.py`. JSON, never `repr()`/
`pickle`. One JSON object per payload: `schema_version` (int) plus all
eleven `frozenset[str]` identity-relevant fields (each a `sorted()` JSON
array — canonicalization only, since `frozenset` has no order of its own)
plus the four audit-only scalar fields (plain string or `null`).
`json.dumps(..., sort_keys=True, ensure_ascii=False)` — deterministic key
order, Unicode stored as readable UTF-8 text rather than `\uXXXX` escapes.

## 5. Field-coverage verdict

**Complete, verified two ways, not merely asserted.** `_SET_FIELDS`/
`_SCALAR_FIELDS` were enumerated by re-reading `EvidenceBag`'s own
dataclass definition fresh (`evidence_attachment_guard.py` lines
150–227), not copied from the design document. A dedicated structural
test (`test_all_evidencebag_dataclass_fields_are_covered_by_serialization`)
compares `dataclasses.fields(EvidenceBag)`'s own actual field-name set
against the serialization module's own covered-field set at test time —
this would fail loudly if `EvidenceBag` ever gained or lost a field
without this module being updated, rather than silently under- or
over-serializing.

## 6. Determinism verdict

Sound, attacked directly (`TestDeterminism`): reversed frozenset
construction order produces byte-identical output (frozensets have no
order to begin with, but this proves the serializer doesn't accidentally
depend on incidental Python hash-seed iteration order); repeated
serialization of equal-but-distinct object instances is byte-identical;
duplicate values collapsed by `frozenset` construction itself never
resurface as a serialization artifact. No field or collection with
semantically meaningful order was sorted, because none exists — every
`EvidenceBag` field the guard reads for identity purposes is declared
`frozenset[str]`, confirmed by direct type inspection.

## 7. Schema-version verdict

`EVIDENCE_BAG_SCHEMA_VERSION = 1`. Deserialization requires an exact
match; any other value (missing, wrong number, wrong type) raises
`EvidenceBagSerializationError` before any field is read. No silent
best-effort coercion for any version, including future ones —
confirmed by `test_unsupported_future_schema_version_rejected`/
`test_missing_schema_version_rejected`.

## 8. Malformed-input verdict

Fails loud for every case the mission named, each with its own test:
invalid JSON, non-object top level (both a JSON array and a bare JSON
string), missing required field, **extra/unknown field** (never silently
dropped — deliberate, matches the module's own "strict, never
forward-compatible by silent drop" design choice), wrong-type set field
(a string instead of an array), wrong-type element within a set field
(a number/`null` mixed into a string array), a malformed nested/tuple-
shaped runway value (the mission's own explicitly-named attack), and a
wrong-type scalar field (an int where a string-or-null was required).

## 9. Hash verdict

`hash_serialized_evidence_bag(serialized: str) -> str` — SHA-256 hex of
the exact persisted string, never a second, independently-normalized
object representation (confirmed directly: appending a single trailing
space to an already-serialized payload changes its hash, proving the
hash is computed over the literal bytes, not a re-derived canonical
form). Deterministic (`test_hash_is_deterministic_and_survives_round_trip`):
serialize → hash, then deserialize → reserialize → hash, produces the
identical digest.

## 10. SourceAssertionEvidenceBag model shape

`source_assertion_evidence_bags`: `id` (PK), `source_assertion_id` (FK →
`source_assertions.id`, `unique=True` — no separate `index=True`, mirroring
`Snapshot.first_acquisition_run_id`'s own precedent exactly, since a
SQLite UNIQUE constraint already provides an efficient lookup path),
`evidence_bag_json` (Text), `evidence_bag_hash` (String(64), indexed,
mirroring `Snapshot.sha256`), `schema_version` (Integer), `created_at`
(DateTime(timezone=True), default `datetime.now(UTC)`). One-directional
`source_assertion` relationship (no `back_populates`) — `app/models/source_assertion.py`
is not modified (see §15).

## 11. Snapshot one-to-one verdict

Enforced at the DB layer, confirmed by direct `IntegrityError` attacks
(`TestSnapshotOneToOne`): a first insert succeeds; a second snapshot for
the same `source_assertion_id` raises `IntegrityError` matching `"UNIQUE"`;
a snapshot referencing a nonexistent `source_assertion_id` raises
`IntegrityError` matching `"FOREIGN KEY"` (with `PRAGMA foreign_keys=ON`
genuinely active for the attacking session — see §23 for a real bug this
mission's own attack list caught in the test fixture itself).

## 12. Snapshot immutability verdict

All six named attacks blocked, each via its own dedicated `pytest.raises`
test against a real, committed row (`TestSnapshotImmutability`): `payload`
(i.e. `evidence_bag_json`) update, `evidence_bag_hash` update,
`schema_version` update, `source_assertion_id` update, `created_at`
update, and delete — all raise `ValueError` from the `before_update`/
`before_delete` event listeners, matching `Snapshot`'s own established
precedent exactly.

## 13. IdentityGuardEvaluation model shape

`identity_guard_evaluations`: `id` (PK), `source_assertion_id` (FK →
`source_assertions.id`, indexed, **not** unique — append-only), 
`evidence_bag_snapshot_id` (FK → `source_assertion_evidence_bags.id`,
indexed — proves which exact immutable input this evaluation read),
`evaluated_against_airport_id` (FK → `airports.id`, indexed),
`triggering_review_id` (FK → `unknown_airport_candidate_reviews.id`,
nullable, indexed), `outcome` (String(30), CHECK-constrained), `reason`
(Text), `created_at`. Four one-directional relationships (no
`back_populates` on any), zero changes to `SourceAssertion`, `Airport`,
or `UnknownAirportCandidateReview`.

## 14. Decision-vocabulary verdict

Reused verbatim from `app.services.evidence_attachment_guard.AttachmentOutcome`
— the CHECK constraint SQL is built programmatically at import time
(`"outcome IN (...)"` from `tuple(o.value for o in AttachmentOutcome)`),
never a second, hand-typed tuple of outcome strings. Confirmed by a
dedicated structural test
(`test_no_second_hand_typed_vocabulary_exists`) that the model module's
own source contains no literal `"ATTACH_CONFIRMED"`-shaped string.
Attacked directly: an invalid outcome string raises `IntegrityError`
matching `"CHECK constraint failed"`; every one of the five real
`AttachmentOutcome` values is independently accepted
(`test_every_real_attachment_outcome_value_is_accepted`, parametrized
over the live enum, not a hand-typed list).

## 15. SourceAssertion relationships / model-touch verdict

**Zero changes to `app/models/source_assertion.py`.** Both new models
carry only one-directional `relationship()` attributes pointing outward
(no `back_populates`, no reciprocal collection required on
`SourceAssertion`/`Airport`/`UnknownAirportCandidateReview`) — mirroring
the already-established, already-reviewed precedent
`SourceAssertion.unknown_airport_candidate` set during UAC2B (also
one-directional, also zero target-model change), on the reasoning that
"which snapshot/evaluations belong to this assertion" is always answered
by a plain, narrow query (mirroring `get_latest_unknown_airport_candidate_review()`'s
own established "current state is derived by recency, never eagerly
loaded" convention), never an ORM collection walk. This directly answers
the mission's own §15 justification requirement: modifying
`SourceAssertion` was considered and explicitly rejected as unnecessary —
not because it was forbidden, but because the minimal, non-invasive
design achieves identical query capability without touching an
already-six-times-migrated, already-adversarially-reviewed model file.

## 16. Legacy-row verdict

No constraint anywhere requires a `SourceAssertion` to have a matching
snapshot — confirmed directly:
`test_source_assertion_without_snapshot_remains_valid` persists and
re-reads an ordinary `SourceAssertion` with zero rows in either new
table, proving this is a fully valid, unconstrained state. Presence/
absence of a linked `SourceAssertionEvidenceBag` row remains the sole
completeness signal, exactly as designed — no new enum/status column was
added anywhere.

## 17. Model-registration verdict

`app/models/__init__.py` updated additively (two new imports, two new
`__all__` entries, alphabetically consistent with the existing list's own
ordering convention). `tests/test_model_contract.py` extended additively
for both new tables' full column/PK/FK/index/relationship shape and the
exported-model-name list — every pre-existing assertion in that file is
untouched. Fresh `configure_mappers()` and `Base.metadata.create_all()`
both confirmed clean (`test_model_table_contract_is_unchanged`,
`test_model_relationship_contract_is_unchanged`,
`test_current_metadata_creates_cleanly_in_isolated_sqlite` — all pass).

## 18. Source-neutral / international verdict

Zero references to MAC/Granicus/USAspending/FAA-specific terms/n8n/any
LLM vendor/US-only geography anywhere in the three new production
modules (`TestSourceNeutralAndNoOrchestration`, term-list grep). Round-
trip fixtures cover Swedish (Åre, Luftfartsverket), Portuguese/Brazilian
(São Paulo, Brasília, ANAC), Japanese (羽田空港, 東京, 国土交通省), and
emoji (✈️🛫) content — all round-trip byte-for-byte, confirmed as raw
UTF-8 text in the serialized payload (not `\uXXXX` escapes).

## 19. Defects found

**One genuine test-fixture defect, found and fixed — not a production
code defect.** The test file's own `_engine()` helper originally
registered its `PRAGMA foreign_keys=ON` "connect" event listener AFTER
calling `Base.metadata.create_all(engine)`. A plain `sqlite:///:memory:`
engine defaults to SQLAlchemy's `SingletonThreadPool`, which caches and
reuses the single connection `create_all()` itself opened — meaning the
FK-enabling listener, registered too late, never actually applied to
that (only) connection, and every FK-attack test in the first draft
silently ran with FK enforcement OFF, passing for the wrong reason
(`IntegrityError` was never even attempted). This mission's own attack
list (item V, "FK delete blocking... verify with FK-enabled SQLite
fixture tests") caught this directly: a manual probe
(`PRAGMA foreign_keys` read back as `0`) proved the pragma was inactive.
Fixed by moving the listener registration before `create_all()`. No
production code was affected.

## 20. Corrections made

1. `_engine()` test fixture fixed per §19.
2. Two AST-scan tests (historical-decision-firewall,
   no-migration-wiring-imported) initially used naive substring search,
   which false-positived on this module's own legitimate prose (the
   docstrings explicitly discuss `identity_guard_decision`/
   `discovery_evidence_persistence.py` to explain WHY this module exists
   and what it must never touch). Corrected to AST-based checks (actual
   `Attribute`/`Name` nodes for the firewall test; actual `Import`/
   `ImportFrom` nodes for the wiring test) that distinguish real code
   references from prose.

## 21. Focused tests

`tests/test_evidence_bag_persistence.py`: **57 passed**, 0 failed.
`tests/test_model_contract.py`: **5 passed**, 0 failed. Combined broader
suite (EB1 + model-contract + UAC1/UAC2B/UAC3 persistence/migration/
discovery-integration): **329 passed**, 0 failed.

## 22. Full pytest

See the final chat report for the confirmed exact count.

## 23. py_compile / git diff --check

Both clean.

## 24. Real database safety

Verified before and after this mission: SHA-256
`d1c37dba82d99974627efb7006619cc7520bb10005605166c530df4fa24de856`, size
1,822,720 bytes, `signal_dispositions`=10, `signal_disposition_members`=25,
`PRAGMA foreign_key_check`=[], `PRAGMA integrity_check`=ok, UAC2A/UAC2B
schema and both new EB1 tables remain entirely absent. All fixtures in
this slice's tests use in-memory SQLite only. No internet access was used
or required.

## 25. Exact EB2 schema work required (documented, not implemented)

A single migration script, `scripts/migrate_evidence_bag_snapshot_ebN.py`,
following the exact `inspect()`/`backup_database()`/`upgrade()`/
`downgrade()`/typed-exception shape every prior UAC migration already
established:

- Create both `source_assertion_evidence_bags` and
  `identity_guard_evaluations` in one atomic migration (they have a hard
  dependency — `identity_guard_evaluations.evidence_bag_snapshot_id`
  requires the snapshot table to already exist — so, mirroring UAC2A's
  own "one migration, both new tables" precedent, there is no meaningful
  half-applied state between them).
- Both tables compiled fresh from `Base.metadata.tables[...]` for the
  upgrade direction (never hand-typed), matching every prior UAC
  migration's own discipline.
- No backfill of any kind — every pre-existing `SourceAssertion` row
  simply gets no corresponding snapshot row, by design (§16).
- `inspect()` must apply the identical strict, structural schema-
  comparison discipline (`_schema_mismatch_reasons()`-style, shared
  between `inspect()` and `upgrade()`) every prior UAC migration already
  established — partial/incompatible existing schema must refuse, never
  silently repair.
- Downgrade must refuse atomically if any `identity_guard_evaluations`
  row exists (mirrors UAC2B's own "refuse if linked rows exist"
  precedent) — an evaluation row is real, governed, historical fact once
  written. Downgrade when both tables are empty (or `source_assertion_evidence_bags`
  has rows but no evaluations reference them) may proceed, dropping both
  tables cleanly.
- No `ON DELETE CASCADE` anywhere, matching this repository's universal
  `NO ACTION`/`RESTRICT` convention — already the case in the ORM models
  themselves (§10/§13), so the migration's compiled-from-metadata DDL
  inherits this automatically.
- Real DB is never touched by EB2's own implementation mission itself
  (only fixture-tested against synthetic databases) — running it for
  real remains EB6's own, separately-authorized future mission, per the
  design document's own slice sequencing.

## 26. Commit policy (implementation phase)

Not committed, not pushed by the implementation phase. See the review
addendum below for the actual commit/push disposition.

---

# Critical review addendum

Adversarial review performed against fresh reads of the design doc, the
actual production code, and direct empirical attacks (not merely
re-trusting this report's own claims). Three genuine issues were found:
one production defect (fixed), one genuine architectural gap (fixed via
a narrow, schema-only correction), and one class of gap correctly
classified as deferred to a later slice, not silently accepted as solved.

## EvidenceBag field-coverage verdict

**Sound, re-confirmed independently.** `dataclasses.fields(EvidenceBag)`
was walked fresh against the live class, cross-checked against
`_SET_FIELDS`/`_SCALAR_FIELDS` — identical sets, confirmed by the
existing structural test
(`test_all_evidencebag_dataclass_fields_are_covered_by_serialization`),
independently re-run and re-verified to actually exercise
`dataclasses.fields()` rather than a hand-maintained list pretending to
be structural.

## Lossless-round-trip verdict

**Sound**, attacked independently with a fresh maximal fixture
(`_FULL_BAG_KWARGS`, all fifteen fields populated) beyond the existing
round-trip test. No field is omitted, reordered in a semantically
meaningful way (none of the eleven set fields have meaningful order —
confirmed by their own `frozenset[str]` type declaration), coerced, or
silently deduplicated beyond what `frozenset` construction itself already
does. No nested/tuple runway structure exists anywhere in `EvidenceBag`
to attack — `runway_ends`/`runway_pairs` are flat `frozenset[str]`,
identical in shape to every other set field; the mission's own "nested
runway structures" concern was verified moot by direct type inspection,
not merely assumed away.

## Deterministic-serialization verdict

**Sound**, re-attacked independently: reversed insertion order,
duplicate values before `frozenset` construction, and repeated
serialization of freshly-constructed-but-equal objects all produce
byte-identical output. Confirmed the determinism is explicit
(`sorted()` + `json.dumps(sort_keys=True)`), not incidental CPython hash-
seed behavior, by reasoning from the code directly (both are load-bearing,
explicit canonicalization calls, not implicit dict/set iteration).

## Schema-version verdict

**One genuine production defect found and fixed** — see the bool/type-
safety verdict below; this is the same finding. Beyond that: the module
correctly requires an exact match, refuses future/unknown versions with
no best-effort forward compatibility, and refuses a missing
`schema_version` key. **Consistency verdict (mission's own "HIGH-
PRIORITY" attack)**: `SourceAssertionEvidenceBag.schema_version` (the ORM
column) and `evidence_bag_json`'s own embedded `"schema_version"` key are
two independently-settable values today — nothing prevents them from
disagreeing (empirically proven: a row with column=`999` and
payload-embedded=`1` was successfully persisted). **Classification:
DEFERRED_TO_EB3_PERSISTENCE_SERVICE**, not a genuine EB1 defect — see the
payload/hash/schema-version consistency verdict below for the full
reasoning, which applies identically to this pair.

## Malformed-input verdict

**Sound**, re-attacked with the mission's own full named list: invalid
JSON, JSON array/scalar/`null` at the top level, missing field, extra
field, wrong scalar type, wrong collection type, a deliberately malformed
nested runway value (`[["09", "27"]]` instead of flat strings), duplicate
JSON object keys (documented — Python's own `json.loads()` silently keeps
only the last occurrence, standard library behavior this module does not
attempt to override, recorded via a new test rather than left
undiscovered). All fail loud with a specific, typed
`EvidenceBagSerializationError`; none silently coerce.

## Bool/type-safety verdict

**One genuine production defect found and fixed.** Python's `bool` is a
subclass of `int` (`True == 1`), and `1.0 == 1` is also `True` — the
original `if schema_version != EVIDENCE_BAG_SCHEMA_VERSION` comparison
silently accepted `schema_version: true` and `schema_version: 1.0` as
valid version `1`, confirmed by direct execution before any fix. **Fixed**
by adding a strict `type(schema_version) is not int` check ahead of the
value comparison — `type(True) is bool`, never `int`, even though `bool`
subclasses it, and `type(1.0) is float`, so both are now correctly
rejected. Verified: `TestSchemaVersionTypeStrictness`, parametrized over
`True`, `False`, `1.0`, `0`, `-1`, `None`, `"1"` (all rejected) plus the
literal integer `1` (still accepted).

## Hash-contract verdict

**Sound, re-verified independently.** SHA-256 of `evidence_bag_json`'s
own exact UTF-8-encoded bytes (`.encode("utf-8")`, confirmed by direct
code read — encoding is explicit, not implicit/platform-dependent).
Same bag → same hash; semantically different bags → different payload →
different hash; serialize → deserialize → reserialize → identical hash;
a single-character (even single-whitespace) difference in an otherwise
arbitrary JSON string changes the hash. **Can a caller supply an
arbitrary payload + an unrelated hash directly through the ORM? Yes,
confirmed empirically** — nothing at the model layer stops
`SourceAssertionEvidenceBag(evidence_bag_json=<A>, evidence_bag_hash=<hash of B>, ...)`.
**Classification: DEFERRED_TO_EB3_PERSISTENCE_SERVICE, not a GENUINE_EB1
DEFECT.** Justification: this repository has no existing precedent for
model-layer hash/payload consistency validation anywhere — `Snapshot`
(`app/models/acquisition.py`), the closest and most direct structural
precedent (also stores a payload alongside its own `sha256`/`byte_size`
fields), carries zero `@validates` hooks or any other cross-field
consistency check either; confirmed by direct source inspection (zero
matches for `validates`/`sha256` validation logic in that file beyond the
plain column declaration). Payload/hash consistency is, by this
repository's own established convention, the WRITER's responsibility
(the persistence service must always call `hash_serialized_evidence_bag()`
on the exact string it is about to store, never accept an independently-
supplied hash) — correctly EB3's future concern, not something EB1's own
"immutable persistence representation" scope should invent a new
validation pattern to solve. Documented explicitly, not silently
glossed over: `TestPayloadHashSchemaVersionConsistencyBoundary`.

## Snapshot-model verdict

**Sound, re-confirmed by direct schema inspection.** Genuinely a sibling
table, not a second `SourceAssertion` — no identity-guard outcome column,
no canonical Airport link, exactly the five fields the design specifies
plus the audit `created_at`. `unique=True` (no redundant explicit index)
on `source_assertion_id` re-confirmed to compile to the identical DDL
shape as `Snapshot.first_acquisition_run_id`'s own precedent (both
produce a UNIQUE constraint with no separate `Index` object in
`table.indexes`) — verified by direct schema introspection, not assumed.

## One-snapshot-per-assertion verdict

**Sound**, re-attacked via raw ORM/SQL: first snapshot succeeds; a second
for the identical `source_assertion_id` raises `IntegrityError` matching
`UNIQUE`; a snapshot for a nonexistent assertion raises `IntegrityError`
matching `FOREIGN KEY`; **same evidence content across two genuinely
different assertions is confirmed legal** (new test,
`test_same_evidence_content_across_different_assertions_remains_legal`) —
the uniqueness boundary is assertion identity, exactly as designed, never
evidence-hash identity.

## Snapshot-immutability verdict

**Sound, and the exact guarantee is stated honestly, not overclaimed.**
All six named field-update attacks plus delete are blocked — but strictly
via SQLAlchemy ORM `before_update`/`before_delete` event listeners,
which fire only for ORM-mediated mutation (`session.delete(obj)`,
attribute assignment + flush). A raw Core-level `Table.update()`/
`Table.delete()` call bypasses these listeners entirely and is NOT
blocked by anything at the database layer itself — SQLite has no
row-level immutability primitive, and this module adds no trigger to
simulate one (per this mission's own explicit "do not add triggers"
instruction). This exact bypass is demonstrated, not hidden, by
`test_deleting_referenced_snapshot_blocked_by_evaluation`'s own use of a
raw `Table.delete()` specifically to reach the FK layer underneath the
ORM guard — that test's own comment already documents this distinction.
The real, load-bearing guarantee against a raw-SQL bypass is the
project's own broader operational discipline (no direct, unreviewed raw
SQL against governed history tables) — identical in kind and strength to
every other "immutable" table in this repository (`Snapshot`,
`UnknownAirportCandidateReview`, `ReviewerAction` all share this exact
same boundary, none of them add DB triggers either).

## Payload/hash/schema-version consistency verdict

See the hash-contract and schema-version verdicts above.
**Classification: DEFERRED_TO_EB3_PERSISTENCE_SERVICE**, documented via
two new regression tests that prove (not merely assert) the current
boundary rather than silently living with an untested gap.

## IdentityGuardEvaluation-model verdict

**Sound after one genuine, critical correction.** The model can answer
every question the mission names: which `SourceAssertion` (`source_assertion_id`),
which exact snapshot (`evidence_bag_snapshot_id`), which canonical
Airport (`evaluated_against_airport_id`), what outcome (`outcome`), what
reason (`reason`), when (`created_at`) — none missing, none assumed from
the report without direct schema inspection.

## Decision-vocabulary verdict

**Sound, re-proven independently.** `_ATTACHMENT_OUTCOME_VALUES`/
`_ATTACHMENT_OUTCOME_CHECK_SQL` are built at import time directly from
`tuple(outcome.value for outcome in AttachmentOutcome)` — confirmed by
direct source read, and confirmed the model module's own source contains
no literal `"ATTACH_CONFIRMED"`-shaped string anywhere (grep-confirmed).
Attacked: every one of the five real values independently accepted
(parametrized over the live enum, not a hand-typed list); lowercase,
padded, and arbitrary-string values all rejected via
`IntegrityError`/`CHECK constraint failed`.

## Evaluation-append-only verdict

**Sound**, re-attacked: zero evaluations is a valid, unconstrained state;
one succeeds; multiple identical-outcome evaluations succeed (never
deduplicated); a later, changed-outcome evaluation succeeds; no
uniqueness constraint of any kind exists on `source_assertion_id` alone
for this table (confirmed by direct schema inspection — only the
composite FK, not a uniqueness constraint, touches that column here).
Direct update/delete of every meaningful field (`outcome`, `reason`)
blocked identically to the snapshot table's own immutability guarantee
(same ORM-event-listener-level caveat applies, stated honestly above).

## Evaluation snapshot/assertion causal-integrity verdict — CRITICAL FINDING, FIXED

**GENUINE_EB1_DEFECT, found and fixed — the single most important
finding of this review.** Confirmed empirically, before any fix: an
`IdentityGuardEvaluation` could be persisted with `source_assertion_id=A`
while `evidence_bag_snapshot_id` pointed at a snapshot that actually
belonged to a completely different `SourceAssertion B` — the two
independent, single-column foreign keys provided zero cross-validation.
Since this table's entire reason for existing is "prove a future
evaluation used the CORRECT, exact evidence," this gap directly
undermined its own central auditability claim.

**Fixed via a composite foreign key** — a purely schema-level correction,
no trigger, no new service, squarely within EB1's own "immutable
persistence representation" scope:
1. `SourceAssertionEvidenceBag` gained a `UniqueConstraint("id",
   "source_assertion_id")` (redundant with `id` alone already being the
   primary key, added solely so the pair can serve as a composite FK
   target).
2. `IdentityGuardEvaluation`'s `evidence_bag_snapshot_id` column lost its
   standalone `ForeignKey(...)`; a `ForeignKeyConstraint(["evidence_bag_snapshot_id",
   "source_assertion_id"], ["source_assertion_evidence_bags.id",
   "source_assertion_evidence_bags.source_assertion_id"])` was added to
   `__table_args__` instead, covering both columns together.

This makes the exact cross-assertion attack structurally impossible,
enforced by SQLite itself (confirmed: `IntegrityError` matching
`FOREIGN KEY`), not merely by a future service's own Python-level
discipline — a materially stronger guarantee than "EB4 will validate
this correctly," since it holds even against a raw, buggy, or malicious
direct-SQL writer. A `SAWarning` about the two overlapping relationships
sharing `source_assertion_id` (`source_assertion` and
`evidence_bag_snapshot`) was resolved cleanly via `overlaps="source_assertion"`
on the `evidence_bag_snapshot` relationship, exactly as SQLAlchemy's own
warning message recommends for this documented, intentional case —
confirmed zero warnings remain via a dedicated test using
`warnings.catch_warnings()`. Verified:
`TestEvaluationSnapshotCausalIntegrity` (attack blocked; correct pairing
still succeeds; no configuration warning).

## Airport causal-integrity boundary verdict

**Boundary identified and documented honestly, not overclaimed.** The
`evaluated_against_airport_id` FK proves only that the referenced
`Airport` row EXISTS — it cannot and does not prove that Airport is the
one this `SourceAssertion` was actually resolved to
(`SourceAssertion.airport_id`) or the one a candidate resolution
actually targeted. Confirmed empirically: an evaluation referencing a
genuinely unrelated, valid Airport succeeds at the DB layer without
error. **This is correctly a future EB4 service-level semantic
responsibility**, not a DB-schema invariant — mirroring how UAC4's own
governed functions (not the bare ORM) verify "the matched Airport must
exist AND be the one the review named." A composite-FK-style structural
fix analogous to the snapshot/assertion one above is NOT proposed here,
because there is no natural second column on `SourceAssertion` to anchor
it to (unlike the snapshot case, `SourceAssertion.airport_id` is a
plain, independently-mutable field with no 1:1 partner relationship to
compose a meaningful composite key from) — attempting one would be
exactly the "casual schema redesign" this mission's own instructions
warn against. Documented via a new, explicit test:
`TestAirportCausalIntegrityBoundary`.

## Historical-decision firewall verdict

**Sound, re-proven independently with exact before/after capture.**
`assertion.identity_guard_decision`/`identity_guard_reason` values were
captured before creating a snapshot AND before creating an evaluation
(with a reason text deliberately different from the original, to make
any accidental overwrite detectable), then re-read via session.get()
after — byte-identical. Grep-confirmed zero references to
`identity_guard_decision`/`identity_guard_reason` as actual code
expressions (AST-checked, not substring-matched, to avoid the original
draft's own false-positive on legitimate docstring prose) anywhere in
the three new production modules.

## FK/delete-safety verdict

**Sound**, re-attacked with `PRAGMA foreign_keys` genuinely confirmed
active on the actual connection under test (see the FK-test-fixture
verdict below) — not merely inferred from event-listener source code.
Deleting a `SourceAssertion` with a linked snapshot: blocked. Deleting an
`Airport` referenced by an evaluation: blocked. Deleting a snapshot
referenced by an evaluation, via a raw Core-level delete bypassing the
ORM immutability guard specifically to reach the FK layer underneath it:
blocked. An evaluation referencing a nonexistent Airport or a nonexistent
`SourceAssertion`: blocked. No cascade anywhere — every governed-history
delete attempt fails closed with `IntegrityError`, never silently
removing history.

## Legacy-row verdict

**Sound, re-confirmed.** A `SourceAssertion` with zero snapshot rows
remains fully valid, queryable, and unconstrained — no schema-level
requirement forces completeness. Presence/absence of a
`SourceAssertionEvidenceBag` row remains the sole signal a future EB4
service would use to fail closed.

## Model-registration/contract verdict

**Sound.** `app/models/__init__.py` diff re-confirmed purely additive
(two imports, two `__all__` entries). `tests/test_model_contract.py`
diff re-confirmed additive only — every pre-existing assertion in the
file is untouched; the composite-FK correction required one additional
`EXPECTED_FOREIGN_KEYS` entry (`source_assertion_id` now legitimately
targets two different tables), added and verified. Fresh
`configure_mappers()`/`Base.metadata.create_all()` both confirmed clean,
including a fresh, explicit zero-`SAWarning` check
(`test_no_sqlalchemy_relationship_configuration_warning`).

## Source-neutral/international verdict

**Sound, extended.** Zero MAC/Granicus/FAA/USAspending/n8n/LLM-vendor/
HTTP/currency-specific references anywhere in the three production
modules (re-confirmed). International round-trip coverage extended
beyond the implementation phase's own Swedish/Portuguese/Japanese/emoji
fixtures to include **Arabic (right-to-left script)** and a **Unicode
normalization-form attack** (NFC vs. NFD "café" — two canonically-
equivalent but code-point-distinct strings, proven to round-trip as the
two genuinely different strings they are, never silently collapsed to
one normalized form by this module).

## FK-test-fixture verdict

**Independently re-verified, not merely re-trusted.** A direct
`PRAGMA foreign_keys` read-back (`_assert_fk_actually_enabled()`) was
added and called at the start of the representative tests in both
FK-dependent test classes (`TestSnapshotOneToOne`,
`TestForeignKeyDeleteSafety`), confirming the value is genuinely `1` on
the actual connection each test uses — not inferred from the presence of
an event-listener registration in the fixture's own source code.

## Test-quality verdict

Read all 57 original tests plus reviewed every new addition against the
mission's own checklist. Found and closed: no payload/hash mismatch
attack (closed, 2 new tests + explicit classification); no schema-
version column/payload mismatch attack (same); no cross-assertion
evaluation/snapshot mismatch attack (closed — and led directly to the
critical composite-FK fix, not merely a test); no bool-as-int version
attack (closed — led directly to the type-safety fix); no raw-SQL-level
FK bypass proof beyond event-listener inspection (closed, direct pragma
assertions added). Confirmed already sound: no broad `except Exception`
anywhere in the test suite; no test recomputes through a duplicate,
independent implementation of the thing it's testing (hash tests use the
real `hash_serialized_evidence_bag()` helper, exactly as production code
would); migration-parity concerns are moot for EB1 (no migration exists
yet by design).

## Defects found

**Two genuine production defects, both found and fixed:**
1. Bool/float `schema_version` type-confusion (§"Bool/type-safety
   verdict") — `True`/`1.0` silently accepted as version `1`.
2. Cross-assertion evaluation/snapshot causal-integrity gap
   (§"Evaluation snapshot/assertion causal-integrity verdict") — the
   single most important finding of this review, fixed via a composite
   foreign key.

**Two genuine gaps correctly classified as deferred, not silently
accepted as already solved:** payload/hash consistency and
schema_version column/payload consistency, both DEFERRED_TO_EB3_PERSISTENCE_SERVICE,
matching this repository's own existing `Snapshot` precedent exactly.
**One boundary correctly identified as inherently a future service-level
concern, not a schema defect:** Airport causal integrity (which specific
Airport an evaluation "should" reference).

## Corrections made

1. `evidence_bag_serialization.py`: strict `type(schema_version) is not int`
   check added ahead of the value comparison.
2. `source_assertion_evidence_bag.py`: added
   `UniqueConstraint("id", "source_assertion_id")`.
3. `identity_guard_evaluation.py`: replaced the standalone
   `evidence_bag_snapshot_id` FK with a composite
   `ForeignKeyConstraint` covering `(evidence_bag_snapshot_id,
   source_assertion_id)`; added `overlaps="source_assertion"` to the
   `evidence_bag_snapshot` relationship to resolve the resulting
   `SAWarning` cleanly.
4. `tests/test_model_contract.py`: one additional `EXPECTED_FOREIGN_KEYS`
   entry for `identity_guard_evaluations` reflecting the composite FK.

## Regression tests added

23 new tests (80 total across the two touched test files, up from 62):
same-content-different-assertions legality; cross-assertion causal-
integrity attack + correct-pairing success + zero-warning proof (3);
Airport causal-integrity boundary (1); payload/hash and schema-
version/payload consistency documentation (2); bool/float/string/`None`/
negative/zero schema-version rejection, parametrized (7 cases) +
literal-`1`-still-accepted (1); duplicate-JSON-key documentation (1);
Arabic round-trip (1); Unicode normalization-form attack (1); direct
`PRAGMA foreign_keys` assertions wired into the existing FK-attack
classes (documented, not counted separately as new test functions).

## Focused tests

`tests/test_evidence_bag_persistence.py`: **75 passed**, 0 failed.
`tests/test_model_contract.py`: **5 passed**, 0 failed. Combined broader
suite (EB1 + model-contract + UAC1/UAC2B/UAC3 persistence/migration/
discovery-integration + UAC5 CLI): **394 passed**, 0 failed.

## Full pytest

See the final chat report for the confirmed exact count.

## py_compile / git diff --check

Both re-run clean after all corrections.

## Real DB before/after proof

Unchanged throughout this review: SHA-256
`d1c37dba82d99974627efb7006619cc7520bb10005605166c530df4fa24de856`,
1,822,720 bytes, FK check `[]`, integrity `ok`,
`signal_dispositions`=10, `signal_disposition_members`=25, UAC schema and
both new EB tables confirmed **absent** — verified fresh both before and
after this review.

RWI_EB1_EVIDENCEBAG_PERSISTENCE_FOUNDATION_REVIEWED_COMMITTED_AND_PUSHED
