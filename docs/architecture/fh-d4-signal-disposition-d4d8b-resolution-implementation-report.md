# FH-D4 Signal Disposition — D4D8B Resolution Implementation Report

Implementation-only. No commit, no push, no real database write, no CLI
change, no schema/migration change. A separate D4D8B adversarial review
mission will inspect this diff and, if accepted, commit/push it.

## 1. Starting HEAD

`c4faffdb7f5e2211f2118de1f7a4326c216a7a1c` == origin/main — verified before
any work began.

## 2. DB pre-checkpoint

SHA-256 `b183750889e8d6eed1f26bb7bbe26987306c87613663c292d59affae5690d405`,
size 1,822,720 bytes, mtime `1787347226.011277`. FK `[]`, integrity `ok`.
`signal_dispositions`=8, `signal_disposition_members`=19. Fresh D4D4: raw=12,
active=4, confirmed_distinct=7, confirmed_same_effort=1, ambiguous=0. Active
groups exactly `{36,44}`, `{37,51,61}`, `{46,53,56,57,62}`, `{49,55,58,59,60}`.

## 3. Files read fresh

`docs/architecture/fh-d4-signal-disposition-d4d8-subgroup-semantics-design.md`
(authoritative — every implementation decision below traces to a specific
section of it), `fh-d4-signal-disposition-d4d8a-architecture-review-report.md`,
`app/services/fh_d4_disposition_resolution.py` (full, pre-change),
`app/services/signal_disposition_resolution.py` (full),
`app/services/signal_disposition_persistence.py` (full, from prior D4D8A
review context), `app/services/fleet_health_review_rules.py` (`evaluate_fh_d4`,
from prior D4D8A review context), `tests/test_fh_d4_disposition_resolution.py`
(full, all 30 existing test classes, including `TestAccountingInvariant`,
`TestBatchQueryBehavior`, `TestInformationFirewall`, `TestNoRealDatabaseAccess`).

## 4. Exact files modified

- `app/services/fh_d4_disposition_resolution.py`
- `tests/test_fh_d4_disposition_resolution.py`

## 5. Exact files created

- `docs/architecture/fh-d4-signal-disposition-d4d8b-resolution-implementation-report.md`
  (this report)

No ORM schema file, migration, persistence write module, D4D5 CLI, `Signal`
model, or `fleet_health_review_rules.py` was touched. No contradiction with
the committed D4D8A architecture was discovered — none of the "STOP if
contradiction" conditions applied.

## 6. Implementation shape

Everything lives in `app/services/fh_d4_disposition_resolution.py` (D4D4) —
`app/services/signal_disposition_resolution.py` (D3) was read but **not
modified**: it remains FH-D4-agnostic, exactly as the design doc's own
existing-contract reconstruction (§2 of the D4D8A review) required. A new
frozen dataclass `SubgroupDispositionSummary` and a new private batched
function `_batched_subgroup_discovery()` were added, mirroring the existing,
already-reviewed `_batched_related_history()`'s own pattern (one full
`SignalDispositionMember` scan, one batched header fetch) rather than sharing
state with it — see §18 for why the two were deliberately kept independent.
`FhD4OperationalGroup` gained three new, defaulted fields:
`resolved_subgroups: tuple[SubgroupDispositionSummary, ...] = ()`,
`unresolved_remainder_signal_ids: tuple[int, ...] = ()`,
`subgroup_conflict: bool = False`. `resolve_fh_d4_findings()` gained one new
line invoking `_batched_subgroup_discovery()` alongside the existing
`_batched_related_history()` call, and a small branch in its per-finding loop
attaching subgroup metadata only when the raw group's own exact-set status is
`UNREVIEWED`.

## 7. Subgroup discovery rule

For each raw FH-D4 group whose own exact-set status is `UNREVIEWED`: every
`SignalDisposition` whose member set is a **strict subset** (`members <
group_set`) of the raw group's member set is a candidate. Candidates sharing
an identical exact member set are deduplicated via D3's own latest-wins
tie-break (`created_at` DESC, `id` DESC) plus that subset's own
`independent_root_count` (`sum(supersedes_id is None)`) — the same algorithm
`resolve_fh_d4_group_statuses()` already uses, applied to a subset's member
set instead of the raw group's. No inference is performed anywhere — only
persisted exact-set rows are ever read.

## 8. Exact-set precedence verdict

**Confirmed, tested (matrix B/C/J/K).** Subgroup discovery is computed only
inside the `status.status == UNREVIEWED` branch of `resolve_fh_d4_findings()`
— a group already `CONFIRMED_DISTINCT`, `CONFIRMED_SAME_REAL_WORLD_EFFORT`,
or already routed to `ambiguous_groups` never has `_batched_subgroup_
discovery()`'s result attached; its three new fields keep their empty/default
values. Test J explicitly constructs a raw `{a,b,c}` with BOTH a genuine
`{a,b}` subset disposition AND the raw group's own exact-set `DISTINCT`
disposition, and proves the group lands in `confirmed_distinct` with
`resolved_subgroups == ()` — exact-set semantics win outright, subgroup
metadata is never even computed for it.

## 9. Remainder semantics

`unresolved_remainder_signal_ids = raw_signal_ids - union(resolved_subgroups'
member sets)`, always, by construction — including the no-subgroup case
(remainder equals the full raw set, test A) and the conflict case (remainder
is forced to the full raw set regardless of what the conflicting candidates
would otherwise seem to cover, test H/I). A remainder Signal is never given
an invented disposition (no code path constructs a `SignalDisposition` or
infers a decision for it) — singleton remainders (tests D, F, N, Roanoke/
Binghamton fixtures) are represented purely as membership in this tuple.

## 10. Multiple-disjoint-subgroup verdict

**Confirmed, tested (matrix G).** `{1,2}` SAME and `{3,4}` DISTINCT inside a
six-member raw group both appear in `resolved_subgroups` (two entries, sorted
`(1,2)` then `(3,4)`), `subgroup_conflict` stays `False`, and remainder is
exactly `{5,6}`. No attempt is made to semantically combine or compare the
two decisions — they are independent facts about disjoint member sets.

## 11. Overlap/conflict behavior

**Confirmed, tested (matrix H/I).** `{1,2,3}` SAME and `{3,4}` SAME (same
decision) and `{1,2,3}` SAME / `{3,4}` DISTINCT (different decisions) both
produce identical, deterministic behavior: `subgroup_conflict=True`, both
candidates present unfiltered in `resolved_subgroups` (nothing dropped), no
winner picked, nothing unioned, no transitive equivalence derived, and
`unresolved_remainder_signal_ids` forced to the group's entire member set —
matching the design doc §10 "Correction 4" defensive read-side check exactly,
and matching it identically regardless of whether the two overlapping
decisions happen to agree (per the syntactic, not semantic, philosophy the
overlap policy is built on).

## 12. Global-scan verdict

Not applicable to D4D8B in the sense the design doc's §7 (write-time overlap
hard-block, D4D8C's job) uses "global" — that concerns the *entire*
`signal_dispositions` table regardless of any particular raw group, at write
time. D4D8B's own read-side subgroup discovery is correctly scoped to
proper subsets of ONE currently-live raw group at a time (there is no other
meaningful scope for "what subgroups exist within this raw group"); each raw
group's candidate scan (`members < group_set`) is independently computed
against the FULL `signal_disposition_members` table (not filtered to
dispositions created "while this raw group existed" — there is no such
concept, since nothing is ever restricted to a detector-run context, per the
design doc's own §4 "subgroup is contextual, not a new persisted concept").
Test L explicitly proves a subset disposition recorded via the generic
persistence API, with no FH-D4 finding ever in view, is discovered correctly
the first time a raw group containing it as a proper subset is later
evaluated.

## 13. Transitivity verdict

**No transitive inference anywhere in the implementation.** `_batched_
subgroup_discovery()` never combines two different candidate member sets
into a third, never computes a union beyond the single `covered` set used
purely to derive the remainder (which is explicitly NOT a disposition or a
claim about any relationship — it is "what's left uncovered"), and contains
no graph, union-find, or equivalence-class structure of any kind. Verified
directly by reading the function's own body — it is a flat dict-of-lists
grouping plus a single latest-wins reduction, nothing more.

## 14. DISTINCT/remainder non-inference verdict

**Confirmed, tested (matrix E, Roanoke/Binghamton fixtures).** Test E
constructs `{a,b}` DISTINCT within a raw `{a,b,c}` and asserts only that `c`
appears in the remainder tuple — no assertion, and no code path, ever
constructs or would satisfy an assertion that `c` is "DISTINCT from a/b."
`resolve_fh_d4_group_statuses()` (unmodified) would still correctly report
`UNREVIEWED` for any set naming `c`, since no disposition naming `c` was ever
recorded.

## 15. Information-firewall verdict

**Unaffected — verified, not merely assumed.** `_batched_subgroup_discovery()`
reads only `SignalDispositionMember.disposition_id`/`.signal_id` and
`SignalDisposition.id`/`.decision`/`.reviewer`/`.reason`/`.created_at`/
`.supersedes_id` — the same field set D3's own resolution functions already
read, zero new Signal-column access. `TestInformationFirewall`'s existing AST
test (`_FORBIDDEN_SIGNAL_ATTRS`) and its behavioral leak test both ran
unmodified against the new code and passed without needing any change to the
forbidden-attribute list — no new attribute name introduced by this slice
collides with or requires exemption from that list.

## 16. Real-case fixture replay verdicts

All three synthetic fixtures (never touching the real database) pass:
- **Roanoke shape** (`{37,51,61}`-topology): `{51,61}` SAME subgroup
  discovered, `37` correctly isolated as the sole remainder, no conflict.
- **Binghamton shape** (`{49,55,58,59,60}`-topology): four-member `{49,55,58,59}`
  SAME subgroup discovered, `60` correctly isolated as the sole remainder.
- **Worcester shape** (`{46,53,56,57,62}`-topology): zero dispositions
  recorded (mirroring D4D7's own finding of no clean bipartition) — the raw
  group stays an ordinary active finding with `resolved_subgroups == ()`;
  nothing is manufactured from the mere shape of the group.

## 17. Backward-compatibility verdict

**Confirmed, tested (matrix R, full existing suite).** All 60 pre-existing
`test_fh_d4_disposition_resolution.py` tests pass unmodified in behavior
(two tests required an intentional, documented numeric update — see §19).
Every pre-existing `FhD4OperationalGroup` field (`status`, `latest_
disposition_id`, `decision`, `reviewer`, `reason`, `created_at`,
`independent_root_count`, `ambiguous_history`, `related_history`) behaves
identically to before this slice. The three new fields are additive with
safe defaults; no existing call site (`resolve_fh_d4_findings()` is the only
production constructor of `FhD4OperationalGroup`, confirmed by grep) required
any change beyond passing the three new values explicitly.

## 18. Defects/design contradictions found

None requiring a stop. One implementation judgment call, explicitly
documented in the module's own new top-of-file section ("QUERY COST"): the
design doc did not mandate whether subgroup discovery should share its
member-table scan with `_batched_related_history()`'s existing one. Sharing
state would reduce query count further but would entangle two independently
-reviewed pieces of logic in one diff; per this mission's own "avoid
unnecessary refactoring" instruction, `_batched_subgroup_discovery()` was
kept fully independent, accepting one additional bounded (not per-group)
table scan and one additional bounded header query as the cost — the same
accepted small-scale tradeoff `find_related_historical_dispositions()`'s own
docstring already documents for this table.

## 19. Corrections made

Two pre-existing tests in `TestBatchQueryBehavior` hardcoded the module's
prior query-count claim (5 / 6 SELECT statements) and were updated to the new,
correct, intentionally-larger bounds (6 / 8) with docstrings explaining the
new arithmetic — a necessary, honest reflection of the new bounded-but-larger
query budget documented in the module's own docstring, not a workaround.

## 20. Focused test results

`tests/test_fh_d4_disposition_resolution.py` (including the new
`TestSubgroupDiscovery` class, 21 tests) plus `tests/test_signal_disposition_
resolution.py`, `tests/test_signal_disposition_persistence.py`,
`tests/test_signal_disposition_migration.py`, `tests/test_review_signal_
disposition.py`: **338 passed, 0 failed.**

## 21. Full pytest result

**2382 passed, 0 failed** (up from the pre-change baseline of 2361 — exactly
the 21 new `TestSubgroupDiscovery` tests), 316.73s.

## 22. py_compile result

`python -m py_compile app/services/fh_d4_disposition_resolution.py
tests/test_fh_d4_disposition_resolution.py` — clean, no errors.

## 23. git diff --check

Clean — no whitespace/blank-line errors reported (only cosmetic LF→CRLF
autocrlf notices from Git, unrelated to diff content). The pre-existing
cosmetic trailing-EOF-blank-line issue in the D4D8A review report was **not**
touched by this mission (that file was not part of this slice's file set) and
was left exactly as-is, per this mission's own explicit instruction not to
modify it solely for that reason.

## 24. Real DB post-checkpoint / no-write proof

SHA-256 `b183750889e8d6eed1f26bb7bbe26987306c87613663c292d59affae5690d405`,
size 1,822,720 bytes, mtime `1787347226.011277` — byte-identical to §2. FK `[]`,
integrity `ok`. `signal_dispositions`=8, `signal_disposition_members`=19 —
unchanged. No `--allow-database-write` flag was ever used; no SQL statement
issued during this mission was anything other than `SELECT`/`PRAGMA` against
either the real database (verification only) or fully isolated in-memory
SQLite databases (all test/sanity-check work).

## 25. git status

Exactly two modified tracked files
(`app/services/fh_d4_disposition_resolution.py`,
`tests/test_fh_d4_disposition_resolution.py`) plus one new untracked file
(this report). All other untracked entries shown by `git status` predate this
mission and belong to separate, unrelated in-progress work in this
repository.

## 26. READY_FOR_D4D8B_REVIEW_CHECKPOINT

**yes**

## 27. Exact recommended next step

A separate D4D8B adversarial review mission: independently reconstruct the
implementation contract from the diff (not from this report's own claims),
attack the subgroup-discovery algorithm and the defensive conflict check for
correctness against the locked D4D8A architecture, verify the two updated
query-count tests are genuinely correct rather than merely adjusted to pass,
and — if the implementation survives review — commit and push exactly the
files listed in §4-5.

## 28. D4D8B critical review addendum

A subsequent adversarial review pass (D4D8B Critical Review mission)
independently re-verified every claim in this report against the actual diff
and live behavior, including several cases not originally covered:
subset-of-subset subgroup conflict (mission §7 case G — `{1,2}` SAME and
`{1,2,3}` SAME are themselves in a subset relationship; confirmed as a hard
conflict per the syntactic overlap policy), subgroup-level supersession
(confirmed exactly one current summary, not two), competing unsuperseded
roots for one subset (confirmed `ambiguous_history` on the summary, correctly
orthogonal to `subgroup_conflict`), a same-`created_at` tiebreak (confirmed
higher id wins), a three-stage detector growth/shrink transition (confirmed
no data rewrite, purely derived per call), and dual-raw-group context
independence (confirmed no parent-group identity is stored or assumed). A
hypothesized "zero-member disposition" defect was investigated and disproved
by direct construction — `member_sets` is built from member rows, so a
memberless disposition never becomes a candidate at all. **No production
code defect was found; the implementation was correct in every case
attacked.** Seven new regression tests (`TestSubgroupDiscoveryCriticalReview`)
plus one new fixture test (Greenville-shape) plus one strengthened existing
test (`test_k`, now including a coexisting legitimate subset disposition
alongside ambiguous exact-set history) were added to `tests/test_fh_d4_
disposition_resolution.py` to lock this already-correct behavior in
permanently — 8 new tests (89 total in this file, up from 81). Full-suite
count is verified fresh in this mission's own final report (§21).
