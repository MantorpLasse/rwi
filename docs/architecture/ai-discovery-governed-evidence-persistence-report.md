# AI-Discovery Governed Evidence Persistence — Slice Report

Implements the first governed persistence pathway connecting the
already-committed pure discovery components to real RWI evidence:

```
Acquisition/Snapshot
    ↓
CandidateFragment
    ↓
EvidenceBag
    ↓
Evidence Attachment Guard
    ↓
Source / SourceAssertion   <-- this slice
```

**This is a NEW discovery pathway only.** No retrofit of USAspending,
NASR, or existing FAA ingestion. No web crawler, no n8n, no Signal
creation, no Fact/Intelligence, no public UI, no deployment, no real-DB
migration, no commit, no push. Baseline: branch `main`, HEAD
`a3450aa268c839763d1049d5ef62410d021c4be6`.

## 1. Architecture basis

Built on three already-real precedents, extending none of them:

- `app/services/discovery_candidate_fragment.py` (committed `a3450aa`) —
  `CandidateFragment` and `candidate_fragment_to_evidence_bag()`, used
  unmodified.
- `app/services/evidence_attachment_guard.py` (committed `386bb88`) —
  `evaluate_attachment_for_candidates()`, used unmodified.
- `scripts/import_usaspending_grants.py::import_all()`'s own established
  pattern — create a `Source` regardless of identity resolution, preserve
  raw evidence via `SourceAssertion` with `airport_id = NULL` when
  identity can't be established — generalized here, not copied verbatim
  or modified in place.

## 2. Schema additions

Two new nullable columns on the existing `source_assertions` table
(`app/models/source_assertion.py`):

| Column | Type | Written by |
|---|---|---|
| `identity_guard_decision` | `VARCHAR(30)` | Only `app/services/discovery_evidence_persistence.py` — one of the five `AttachmentOutcome` values, verbatim (`.value`) |
| `identity_guard_reason` | `TEXT` | Only the same module — the guard's own deterministic `AttachmentDecision.reason`, never AI-regenerated or summarized |

Both fully nullable, additive, backward-compatible. No `DB`-level `CHECK`
constraint bounds `identity_guard_decision` to the five outcome values in
this slice — adding one would require the same full-table-rebuild
procedure `downgrade()` already needs for `DROP COLUMN` (SQLite cannot
add a `CHECK` constraint via a plain `ALTER TABLE ADD COLUMN` as safely/
simply as a bare nullable column), which is more than "smallest additive
migration" calls for. The persistence service is the **sole** writer of
this column and only ever writes a real `AttachmentOutcome.value` string,
enforced in Python (`outcome.value` on a typed enum), not the database.
Documented directly in the model's own new field comment, not just here.

No existing NASR/USAspending/FAA-acquisition ingestion path reads or
writes either column — every existing `SourceAssertion` row remains
exactly as it was, both columns `NULL`, forever, unless a future write
goes through this new module specifically.

## 3. Migration behavior

`scripts/migrate_discovery_governed_evidence_slice1.py`, modeled directly
on the already-proven `scripts/migrate_canonical_runway_runway_end_slice1.py`
pattern (same CLI shape, same backup-before-write discipline, same
`_drop_column_via_rebuild()` helper reused for `downgrade()` — see
`docs/domain/canonical-runway-migration-downgrade-fix-report.md` for why
a native `DROP COLUMN` cannot be trusted blindly).

- `upgrade()`: idempotent `ALTER TABLE source_assertions ADD COLUMN` for
  each of the two new columns, guarded by a `PRAGMA table_info()` check
  (safe to call on an already-upgraded database — a no-op).
- `downgrade()`: rebuilds `source_assertions` via SQLite's documented
  12-step procedure (new table under a temp name, copy surviving
  columns/data, drop original, rename into place, recreate surviving
  indexes), preserving every surviving column's own constraints
  (including the table's own incoming foreign-key target — see §4) and
  every surviving index. `PRAGMA foreign_key_check` is verified clean
  before commit; the whole transaction rolls back if not.
- Both functions build their own `sqlite3` connection from the resolved
  `--database` argument — no `SessionLocal` import anywhere in this
  script.

**Not run against the real database in this task** (explicitly
prohibited) — every proof below used isolated, throwaway temp databases.

## 4. Migration upgrade/downgrade test result

`tests/test_discovery_governed_evidence_migration.py` — **4 passed**,
including the realistic full-schema regression this repository's own
prior incident specifically calls for: `source_assertions` **is** the
target of a real incoming foreign key
(`installation_assertion_links.assertion_id`), and a fresh
`Base.metadata.create_all()` database declares that FK as a table-level
clause — exactly the shape that previously broke a native `DROP COLUMN`
elsewhere in this repository. Result:

| Step | Result |
|---|---|
| `upgrade()` on the true pre-migration shape | Succeeds; both columns present, `NULL` on the existing row; `foreign_key_check` clean |
| `upgrade()` run twice | Fully idempotent — identical `inspect()` output both times |
| `downgrade()` reversibility (minimal schema) | Both columns removed; row data and full `sqlite_master` schema text byte-identical to pre-migration |
| `downgrade()` against the full realistic schema, with a live `InstallationAssertionLink` pointing into the altered `SourceAssertion` row | **Succeeds**; both columns removed; the assertion row and the incoming-FK link row both preserved with original values; every unrelated table's row count unchanged; both surviving indexes preserved; `foreign_key_check` → `[]`, `integrity_check` → `ok` |
| Re-`upgrade()` after `downgrade()` | Succeeds again, data still intact |

## 5. Persistence service API

`app/services/discovery_evidence_persistence.py`:

```python
persist_discovery_fragment(
    session: Session,
    source_metadata: DiscoverySourceMetadata,
    fragment: CandidateFragment,
    candidate_airports: Sequence[CandidateAirport],
) -> DiscoveryPersistenceResult
```

`DiscoverySourceMetadata` — `document_identity` (the **only** field used
to build `Source.external_id`, namespaced `"discovery:{document_identity}"`
— matches the existing `"usaspending:..."`/`"faa_nasr:..."` convention
exactly; must be derived from acquired-resource identity, never a search
query), `title`, `source_type` (default `"web_discovery"`), `publisher`,
`url`, `published_date`, `reliability_level` (default `"unverified"`).

`DiscoveryPersistenceResult` — `source_id`, `source_created`,
`source_assertion_id`, `source_assertion_created`, `outcome`
(`AttachmentOutcome`), `reason`, `attached_airport_id`,
`evaluated_candidate_ids` — no ORM instance ever exposed.

The service performs **no** web search, fetch, parsing, AI extraction,
database-wide airport resolution, or `Signal` creation — it receives an
already-built `CandidateFragment` and already-resolved candidate
airports, exactly as scoped.

## 6. Source boundary

Reuses `Source` exactly as designed (no parallel document table). Created
(or reused, keyed by `external_id`) unconditionally on every call — the
service assumes the **caller** has already judged the fragment topically
relevant enough to reach this point (the lifecycle design's own §11
boundary: "the persistence service may assume topical relevance has
already been established by the caller"). Idempotency is keyed **only**
by `document_identity` — never by search query, seed airport, or crawler
name (proven by `test_case_K_same_fragment_rediscovered_via_different_query_is_idempotent`,
which varies the search query across two calls for the identical document
and asserts the same `Source` row is reused).

## 7. SourceAssertion boundary

**Exactly one** `SourceAssertion` is created per fragment, regardless of
how many candidate airports were evaluated (task's own explicit
requirement). Idempotency reuses `SourceAssertion`'s own existing
fragment-identity fields and the exact tuple its `UniqueConstraint`
already enforces: `(source_id, artifact_identity, source_locator,
raw_fragment_hash)`. An already-existing row for the same fragment
identity is returned **unchanged** — never overwritten by a possibly-
different guard result on a later call, treating persisted
`SourceAssertion` rows as append-only evidence (matching, though not as
strictly DB-enforced as, `InstallationAssertionLink`'s own immutability
elsewhere in this repository). `raw_relevant_text` always holds the
fragment's original, untranslated text verbatim.

## 8. Outcome → `airport_id` contract

| Outcome | `airport_id` | Rationale |
|---|---|---|
| `ATTACH_CONFIRMED` | Set, to the one confirmed candidate | Strong enough evidence-level attachment |
| `ATTACH_PROVISIONAL` | **Set** | Per the lifecycle design's own §15: provisional evidence *does* carry `airport_id` — it is weak, single-category evidence, but it *is* attached, not merely candidate. Distinguished from confirmed entirely via `identity_guard_decision`, never by `airport_id` presence/absence |
| `REVIEW_REQUIRED` | `NULL` | Ambiguous across ≥2 candidates — never silently picks one |
| `REJECT_CROSS_AIRPORT` | `NULL` | Contradicted for every candidate that reached this outcome |
| `INSUFFICIENT_IDENTITY` | `NULL` | No positive evidence for any candidate, or no candidates supplied at all |

When more than one candidate is evaluated, the service selects **exactly
one** outcome to persist via a fixed priority
(`ATTACH_CONFIRMED` > `ATTACH_PROVISIONAL` > `REVIEW_REQUIRED` >
`REJECT_CROSS_AIRPORT` > `INSUFFICIENT_IDENTITY`). This is safe and
non-arbitrary because `evaluate_attachment_for_candidates()` itself
already guarantees at most one candidate can carry `ATTACH_CONFIRMED` or
`ATTACH_PROVISIONAL` in its returned decisions — any genuine ambiguity
between two-or-more qualifying candidates is already converted to
`REVIEW_REQUIRED` for *all* of them before this module ever sees the
result. The service never breaks a tie among several `ATTACH_CONFIRMED`
candidates, because that state cannot exist by construction.

## 9. Guard decision persistence

`identity_guard_reason` is always the guard's own `AttachmentDecision.reason`
string, verbatim — never regenerated, summarized, or paraphrased by AI at
this layer. For `REVIEW_REQUIRED`/`REJECT_CROSS_AIRPORT`/
`INSUFFICIENT_IDENTITY` outcomes reached by **multiple** candidates in
one call, the reasons are joined deterministically
(`"[candidate {id}] {reason}; ..."`, sorted by candidate id) into the
single persisted row — no speculative JSON column was added for this in
this slice (per instruction). What remains reconstructable purely from
`raw_relevant_text` + `identity_guard_reason` for a future review-queue
slice: the original evidence text, every category the guard matched or
contradicted (named explicitly inside the reason string), and — for
multi-candidate cases — every candidate id that was evaluated and its own
individual reasoning. What is **not** separately queryable without
re-parsing the reason text: a structured, per-candidate breakdown (e.g.
"show me every fragment ambiguous between exactly BOS and ORH") — this is
the exact limitation the lifecycle design's §22 already anticipated and
explicitly deferred to a future dedicated table, not solved here.

## 10. Fragment identity / idempotency

Reuses `CandidateFragment.artifact_identity`/`.source_locator`/
`.fragment_hash` directly as `SourceAssertion.artifact_identity`/
`.source_locator`/`.raw_fragment_hash` — no separate identity scheme.
Proven by test:

- Same fragment, rediscovered via a different search query →
  `source_assertion_created=False` on the second call, same id.
- Changed `raw_text` (different `fragment_hash`) → new, independent
  `SourceAssertion` (`source_assertion_created=True`, different id),
  same reused `Source`.
- Two distinct fragments (different `source_locator`) from the same
  document → one `Source`, two `SourceAssertion` rows.

## 11. SFO/MSP behavior

`test_case_A_sfo_msp_persists_for_msp_not_sfo`: SFO and MSP both
evaluated in one call; SFO independently reaches `REJECT_CROSS_AIRPORT`,
MSP independently reaches `ATTACH_CONFIRMED`. **Exactly one**
`SourceAssertion` is created, `airport_id` = MSP's real id, `identity_guard_decision
= "ATTACH_CONFIRMED"`. The document itself was never rejected — nothing
about `Source`/`SourceAssertion` creation depended on which candidate
"won."

## 12. Insufficient identity

`test_case_G_allegheny_like_insufficient_identity_preserved` /
`test_case_H_morristown_like_insufficient_identity_preserved`: a useful,
real-shaped fragment (recipient/organization name only, deliberately
never placed in `airport_names` — matching `resolve_airport()`'s own
long-standing rule that a recipient is not necessarily the airport) still
produces a `Source` + `SourceAssertion` with `raw_relevant_text` fully
preserved, `airport_id = NULL`, `identity_guard_decision =
"INSUFFICIENT_IDENTITY"` — mirroring the existing USAspending
fail-closed principle exactly, without modifying USAspending itself.

## 13. Provisional

`test_case_F_provisional_attaches_airport_but_is_distinguishable_from_confirmed`:
city/state-only evidence reaches `ATTACH_PROVISIONAL`; `airport_id` **is**
populated (per §8); the row remains fully distinguishable from a
confirmed one purely via `identity_guard_decision` (`"ATTACH_PROVISIONAL"`
≠ `"ATTACH_CONFIRMED"`). No `Signal` is created for it (§16), and no
public-facing pathway in this repository reads `identity_guard_decision`
at all yet, so provisional evidence has no route to public exposure in
this slice by construction, not merely by convention.

## 14. Multi-candidate limitation

`test_case_I_multi_airport_review_required_persists_once_with_no_airport_chosen`:
two candidates sharing one issuer, neither with distinguishing runway
evidence, both independently qualify → both escalated to
`REVIEW_REQUIRED` by the guard's own ambiguity resolution → **one**
`SourceAssertion` persisted, `airport_id = NULL`, `identity_guard_reason`
containing both candidates' own reasoning (by id). No broad multi-
candidate schema was added — exactly the deferral the lifecycle design's
§22 already called for, respected here without modification.

## 15. No-Signal invariant

`test_case_M_no_signal_created_for_any_outcome`: exercises
`ATTACH_CONFIRMED`, a mixed `REJECT_CROSS_AIRPORT`/`ATTACH_CONFIRMED`
multi-candidate call, and `INSUFFICIENT_IDENTITY` in sequence, then
asserts `Signal` count is zero. The module itself never imports the
`Signal` model at all — structurally, not just behaviorally, incapable of
creating one.

## 16. Transaction model

The service never calls `session.commit()` anywhere — only
`session.add()`/`session.flush()` (to obtain row ids for the result
object). No `app.database.SessionLocal` import anywhere in the module.
Proven directly by `test_no_hidden_commit_rollback_undoes_everything`:
calls the service, then rolls back the caller's own session, and asserts
**nothing** was persisted — proof the service itself never committed
anything, not merely that it "usually" doesn't.

## 17. Tests

- `tests/test_discovery_governed_evidence_migration.py` — 4 passed.
- `tests/test_discovery_evidence_persistence.py` — **16 passed**, covering
  all 13 required worked cases (A–M) plus 3 additional tests (no hidden
  commit, `Source` reuse across multiple fragments of one document, no
  canonical fact rows created).

## 18. Backward compatibility

`tests/test_model_contract.py::test_model_table_contract_is_unchanged` is
a deliberate schema-snapshot whitelist test and, as designed, failed the
first time the full suite ran after the two new columns were added — it
caught exactly the change this slice intentionally made. Fixed by adding
`identity_guard_decision`/`identity_guard_reason` to its
`EXPECTED_COLUMNS["source_assertions"]` entry (nullable,
`VARCHAR(30)`/`TEXT`, no default) — the same edit any additive model
change in this repository requires. No other assertion in that file
(primary keys, foreign keys, indexes, relationships) needed to change,
since neither new column carries a constraint or relationship.

Combined run across every potentially-affected existing suite —
`test_discovery_evidence_persistence.py`, `test_discovery_governed_evidence_migration.py`,
`test_discovery_candidate_fragment.py`, `test_evidence_attachment_guard.py`,
`test_source_assertions.py`, `test_import_usaspending_grants.py`,
`test_usaspending_grants.py`, `test_nasr_apt_ars_evidence.py`,
`test_nasr_apt_csv_acquisition.py`, `test_nasr_apt_rwy_evidence.py`,
`test_nasr_source_provenance.py`, `test_physical_installation_reconciliation.py`,
`test_physical_installation_identity_linking.py`,
`test_reconcile_bos_orh_emas_identities.py`, `test_static_export.py` —
**241 passed**, zero regressions (after the `test_model_contract.py`
whitelist update above). Full-suite run: **729 passed** (baseline 709 +
16 persistence + 4 migration tests, `test_model_contract.py` fixed in
place rather than counted as a new test). No existing NASR/USAspending/
reconciliation/static-export ingestion script was modified in this task.
Every pre-existing `SourceAssertion` row remains valid with both new
columns `NULL` by construction (they are never touched by any code path
except this new module).

## 19. Real DB untouched

| | Value |
|---|---|
| Path | `C:\Runwaysafe\runway-safe-intelligence\data\runway_safe.db` |
| Size | 667648 bytes (unchanged) |
| mtime | unchanged from the last recorded checkpoint |
| `source_assertions` row count | 221 (unchanged) |
| `identity_guard_decision` column present | **No** — confirmed absent by direct `PRAGMA table_info()` read |
| `identity_guard_reason` column present | **No** — confirmed absent |

No migration, upgrade, downgrade, or any write of any kind was ever
executed against this file in this task — every proof in §4/§17 used
isolated, in-memory or temp-file SQLite databases only.

## 20. Exact future integration direction

Per the lifecycle design's own §24/§25 ordering, now with slices 1
(candidate fragment) and this slice (governed persistence pathway)
complete:

1. **One controlled, narrowly-scoped discovery adapter** — a single new
   `AcquisitionProvider` for one specific, real, already-identified
   source (e.g., one airport authority's agenda page), reusing
   `AcquisitionService`/`AcquisitionSource`/`Snapshot` exactly as they
   exist today.
2. **Wire that one adapter's own extraction step** to build a real
   `CandidateFragment` from real acquired content (today, every fixture
   here is synthetic, by design), then call `persist_discovery_fragment()`
   for that one real source only.
3. Only after a real end-to-end run against one real source is reviewed:
   apply `scripts/migrate_discovery_governed_evidence_slice1.py --upgrade`
   against the real database, as its own explicit, separately-approved
   step.
4. **Review workflow** (conceptual queue query against
   `SourceAssertion.identity_guard_decision IN ('REVIEW_REQUIRED', ...)`
   — no UI) — validated against whatever slice 1–3 actually produce.
5. **Signal promotion rule**, as its own explicit, separately-reviewed
   rule set (lifecycle design §13) — this slice creates zero Signals by
   design and should continue to until that rule is deliberately built.
