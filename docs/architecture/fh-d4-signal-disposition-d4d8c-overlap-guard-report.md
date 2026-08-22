# FH-D4 Signal Disposition — D4D8C Overlap/Contradiction Guard Report

Implementation-only. No commit, no push, no real database write, no write-
path wiring, no D4D5 CLI change, no migration. A separate D4D8C adversarial
review mission will inspect this diff and, if accepted, commit/push it.

## 1. Starting HEAD

`8f4447a4961d1331b0ab86a2ae28b827fdbfd463` == origin/main — verified before
any work began.

## 2. DB pre-checkpoint

SHA-256 `b183750889e8d6eed1f26bb7bbe26987306c87613663c292d59affae5690d405`,
size 1,822,720 bytes, mtime `1787347226.011277`. FK `[]`, integrity `ok`.
`signal_dispositions`=8, `signal_disposition_members`=19. Fresh D4D4: raw=12,
active=4, confirmed_distinct=7, confirmed_same_effort=1, ambiguous=0.

## 3. Files read fresh

`docs/architecture/fh-d4-signal-disposition-d4d8-subgroup-semantics-design.md`
(authoritative), `-d4d8a-architecture-review-report.md`,
`-d4d8b-resolution-implementation-report.md`,
`app/services/signal_disposition_persistence.py` (full),
`app/services/signal_disposition_resolution.py` (full, from this
conversation's own prior D4D8B work, unchanged since),
`app/services/fh_d4_disposition_resolution.py` (full, from this
conversation's own prior D4D8B work, unchanged since),
`app/models/signal_disposition.py` (full).

## 4. Files created

- `app/services/signal_disposition_conflicts.py`
- `tests/test_signal_disposition_conflicts.py`
- `docs/architecture/fh-d4-signal-disposition-d4d8c-overlap-guard-report.md`
  (this report)

## 5. Files modified

None. `record_signal_group_disposition()`, `resolve_fh_d4_group_status()`,
D4D4's subgroup discovery, and the D4D5 CLI are all completely untouched —
the locked architecture did not require wiring this guard into any of them
in this slice (§18 of the design doc explicitly separates D4D8C, the
standalone guard, from D4D8D, the future CLI wiring).

## 6. Helper/API shape

```python
def find_signal_disposition_conflicts(
    session: Session,
    *,
    signal_ids: Sequence[int],
    exclude_exact_set: bool = True,
) -> tuple[SignalDispositionConflict, ...]:
```

`SignalDispositionConflict` (frozen dataclass): `proposed_signal_ids`,
`conflicting_disposition_id`, `conflicting_signal_ids`,
`conflicting_decision`, `conflicting_reviewer`, `conflicting_reason`,
`conflicting_created_at`, `conflicting_supersedes_id`,
`overlap_signal_ids`, `relation` (one of `STRICT_SUBSET`/`STRICT_SUPERSET`/
`PARTIAL_OVERLAP`/`EXACT_MATCH`). No ranking, confidence, or severity field
exists anywhere — `relation` is a classification, not a priority, matching
the mission's own explicit "do not invent ranking or severity scoring."
`exclude_exact_set` (default `True`) matches the mission's own suggested
signature: when `True`, an exact match is never returned (owned entirely by
the existing exact-set re-review/supersession path); when `False`, it is
returned with `relation=EXACT_MATCH`, purely informational, never itself
grounds for refusal.

## 7. Normalization contract

Identical to `record_signal_group_disposition()`/`resolve_fh_d4_group_status()`:
dedupe + sort (`tuple(sorted(set(signal_ids)))`), minimum cardinality
enforced via the **imported** `MINIMUM_GROUP_CARDINALITY` constant from
`signal_disposition_persistence` (not re-declared — the two modules can
never silently drift). **Deliberately does not validate Signal existence** —
an explicit, documented, tested contract (§12 of the mission explicitly
sanctions this alternative): the intended caller (a future D4D8D write-time
gate) will already have resolved a live FH-D4 group or otherwise confirmed
existence before ever calling this helper, so re-validating here would be a
redundant query in the one workflow this module is built for. This module
never imports `Signal` and never queries the `signals` table.

## 8. Global-scan verdict

Confirmed by direct test (`TestGlobalScope`): a conflict is found between
Signals belonging to two *different* airports with no shared FH-D4 group,
detector run, or parent-group context of any kind — the guard reads only
`signal_dispositions`/`signal_disposition_members`, never `Signal.airport_id`
or any detector concept. No parent-group foreign key or contextual identity
was introduced anywhere.

## 9. Historical/latest-set policy

**This is the one genuine design decision this slice had to make beyond
directly-quotable design-doc text**, resolved by direct appeal to the
locked architecture's own uniformly-applied philosophy rather than guessed:
a proposed set is compared only against each existing exact member-set's
**current (latest-wins)** disposition — never against every raw historical
row independently. This mirrors, verbatim, the same latest-wins reduction
`resolve_fh_d4_group_statuses()` (D3) and `_batched_subgroup_discovery()`
(D4D8B) already apply before any downstream decision is made about a member
set. Verified directly by CASE 11 (§10 below): `{a,b}` SAME superseded by
`{a,b}` DISTINCT, proposing `{b,c}`, yields **exactly one** conflict — against
the current DISTINCT fact (`conflicting_disposition_id` == the superseding
row's own id, `conflicting_decision == "DISTINCT"`) — never two conflicts,
and never a conflict against the stale, superseded row alone. This directly
satisfies the mission's own explicit warning: "do not allow obsolete
historical rows to create permanent false blockers."

## 10. CASE 1-12 verdicts

All twelve required cases verified both by live, ad-hoc interactive testing
(before any test was written) and by a permanent test in
`tests/test_signal_disposition_conflicts.py::TestCaseMatrix`:

| Case | Result |
|---|---|
| 1: `{A,B,C}` SAME, propose `{B,C,D}` SAME | 1 conflict, `PARTIAL_OVERLAP` — hard block |
| 2: `{A,B}` SAME, propose `{B,C}` SAME | 1 conflict, `PARTIAL_OVERLAP` — hard block, no transitive inference |
| 3: `{A,B}` SAME, propose exact `{A,B}` DISTINCT | 0 conflicts — exact-set path owns it |
| 4: `{A,B,C}` SAME, propose `{A,B}` DISTINCT | 1 conflict, `STRICT_SUPERSET` — hard block |
| 5: `{A,B,C,D}` SAME, propose `{C,D,E}` SAME | 1 conflict, `PARTIAL_OVERLAP` — hard block |
| 6: `{A,B}` SAME, propose `{C,D}` DISTINCT | 0 conflicts — allowed |
| 7: `{A,B}` SAME, propose `{A,B,C}` SAME | 1 conflict, `STRICT_SUBSET` — hard block |
| 8: `{A,B,C}` SAME, propose `{A,B}` SAME (matching decision) | 1 conflict, `STRICT_SUPERSET` — hard block even though decisions match |
| 9: `{A,B}` SAME + `{C,D}` DISTINCT, propose `{E,F}` SAME | 0 conflicts |
| 10: `{A,B}` SAME→`{A,B}` DISTINCT (superseded), propose `{A,B}` | 0 conflicts — exact-set path owns it |
| 11: same history, propose `{B,C}` | exactly 1 conflict, against the **current** DISTINCT fact only |
| 12a: zero-member malformed row (raw SQL) | silently, correctly ignored — never becomes a candidate at all |
| 12b: one-member malformed row (raw SQL) | deterministically surfaced as `STRICT_SUBSET`, never crashes, never hidden |

## 11. Exact-match behavior

Never returned as a conflict by default (`exclude_exact_set=True`); returned
with `relation=EXACT_MATCH` only when the caller explicitly opts in
(`exclude_exact_set=False`) — verified by `TestExcludeExactSetToggle`.

## 12. Strict subset/superset behavior

Both directions classified relative to the *proposed* set, matching D3's own
`RelatedHistoricalDisposition.relation` directional convention exactly:
`STRICT_SUBSET` when the existing set is smaller (contained in the
proposed set), `STRICT_SUPERSET` when larger. Both are hard-blocked
unconditionally, including when the existing and proposed decisions happen
to match (CASE 8) — the policy is syntactic, not semantic, exactly as
locked.

## 13. Partial-overlap behavior

Any non-empty intersection that is neither exact, subset, nor superset is
`PARTIAL_OVERLAP` — hard-blocked (CASE 1, 2, 5).

## 14. Disjoint behavior

Zero-intersection existing sets are never returned at all — `TestCaseMatrix
::test_case_6_disjoint_is_allowed`/`test_case_9_...` confirm zero conflicts.

## 15. Transitivity verdict

**None, anywhere.** `TestNoTransitiveInference::test_no_third_fact_derived_
from_two_independent_conflicts` proves `{a,b}` SAME and `{b,c}` SAME, both
independently conflicting with a proposed `{b,d}`, are returned as **two
separate, independent** conflict entries — never combined into a claim about
`{a,c}` or `{a,b,c}`. A structural AST test confirms the module contains
exactly two function definitions (`_normalize`, `find_signal_disposition_
conflicts`) and imports no graph/union-find library — there is no hidden
helper capable of combining two existing sets with each other.

## 16. Information-firewall verdict

Confirmed three ways: an AST test proves zero references to any forbidden
`Signal` attribute (title/notes/source_notes/financial/vendor/category/
confidence/status/airport_id/runway_id/installation_id — a superset of
D4D4's own list, since this module could in principle have been tempted to
read `airport_id` for "scoping," and explicitly does not); an AST test
proves `Signal` is never imported; a behavioral test proves no Signal
title/financial content leaks into the result's own `repr()`.

## 17. Query-count result

Independently measured (not merely asserted): **1 query** when zero
dispositions anywhere touch any proposed id (the single candidate-id lookup
short-circuits immediately). **Exactly 3 queries** whenever at least one
candidate exists — candidate disposition ids (`signal_id IN proposed`) →
their complete member rows → their header rows — **verified constant at
1, 10, and 100 unrelated other dispositions in the table** (parametrized
test), never growing with table size, never one query per candidate.

## 18. No-autoflush/read-only verdict

Confirmed: `session.no_autoflush` wraps the entire function body; a pending,
uncommitted caller mutation is proven not flushed; zero INSERT/UPDATE/DELETE
statements are ever emitted (proven via SQL-statement capture); `session.new`/
`.dirty`/`.deleted` remain empty after a call; a missing schema propagates an
ordinary uncaught exception rather than silently returning "no conflicts."

## 19. Backward-compatibility verdict

Confirmed: `record_signal_group_disposition()` was not modified in any way
and still behaves identically (a direct regression test asserts this); no
other existing module was touched; the eight real dispositions are untouched
(never opened by any test in this file — see `TestNoRealDatabaseAccess`).

## 20. Defects/ambiguities found

One genuine ambiguity, resolved and documented (§9): the design doc's own
prose ("an existing disposition's member set") does not literally
distinguish "any historical row" from "the current fact for that member
set." Resolved by direct appeal to the architecture's own uniform "latest
wins determines current state" philosophy (already the algorithm D3 and
D4D8B both use), not guessed, and locked in by CASE 11's own explicit test.
No other design contradiction or implementation defect was found.

## 21. Corrections made

None required — the implementation matched the intended design on first
construction; all twelve required cases and every additional attack
(malformed rows, cross-airport global scope, exact-match toggle,
normalization edge cases) passed on first run once written.

## 22. Focused tests

`tests/test_signal_disposition_conflicts.py`: **41 passed.** Combined with
`test_signal_disposition_persistence.py`, `test_signal_disposition_
resolution.py`, `test_fh_d4_disposition_resolution.py`, `test_signal_
disposition_migration.py`, `test_review_signal_disposition.py`: **387
passed, 0 failed.**

## 23. Full pytest

Run in background; result recorded in this mission's own final report.

## 24. py_compile

`python -m py_compile app/services/signal_disposition_conflicts.py
tests/test_signal_disposition_conflicts.py` — clean, no errors.

## 25. git diff --check

Clean — the two new production/test files and this report are untracked
additions only; no existing tracked file was modified.

## 26. DB post-checkpoint

Verified immediately before this report was written: SHA-256
`b183750889e8d6eed1f26bb7bbe26987306c87613663c292d59affae5690d405`,
1,822,720 bytes, mtime `1787347226.011277` — byte-identical to §2. FK `[]`,
integrity `ok`. `signal_dispositions`=8, `signal_disposition_members`=19 —
unchanged. No `--allow-database-write` used; every SQL statement issued
during this mission was `SELECT`/`PRAGMA` against either the real database
(verification only) or fully isolated in-memory SQLite databases.

## 27. git status

Exactly three new untracked files added by this mission
(`app/services/signal_disposition_conflicts.py`,
`tests/test_signal_disposition_conflicts.py`, this report); no tracked file
modified. All other untracked entries predate this mission and belong to
separate, unrelated in-progress work.

## 28. READY_FOR_D4D8C_REVIEW_CHECKPOINT

yes

## 29. Exact recommended next step

A separate D4D8C adversarial review mission: independently reconstruct the
guard's contract from the actual diff, attack the historical/latest-set
policy decision specifically (§9 — the one genuine judgment call in this
slice), verify the query-count claims independently, and — if the
implementation survives review — commit and push exactly the three files
listed in §4.

## 30. D4D8C critical review addendum

A subsequent adversarial review pass (D4D8C Critical Review mission)
independently re-verified every claim in this report against the actual
implementation and live behavior, and found **one genuine production
defect**: `SignalDispositionConflict` exposed no `independent_root_count`/
`ambiguous_history` — unlike D3's `SignalDispositionStatus` and D4D8B's
`SubgroupDispositionSummary`, both of which expose this for every other
latest-wins-reduced fact in this pipeline. A conflict against a disposition
whose own history was itself contested (two independent, unlinked roots)
would have been indistinguishable from an ordinary, uncontested conflict —
a real opacity gap against the mission's own explicit "if a blocker would
be opaque to the reviewer, treat that as a genuine API defect" standard.
**Corrected** with the smallest possible change: two additive fields,
computed from data already fetched in the existing loop (no new query),
mirroring D3/D4D8B's identical `sum(supersedes_id is None)` computation.

The review also found that several attacks verified live during the
original implementation pass (H2 competing unsuperseded roots, H3 a
three-deep supersession chain, H4 same-timestamp tiebreak, H5/H6 a
malformed cross-member-set `supersedes_id`) had never been converted into
permanent regression tests, and that the mission's own exact §7
three-existing-set scenario and an explicit existing-DISTINCT
decision-independence case were likewise untested. All six were added as
permanent tests (`TestHistoricalLatestPolicyAttacks`,
`TestMissionExactScenarios`). One existing test's `pytest.raises(Exception)`
was narrowed to the specific `sqlalchemy.exc.OperationalError` the module's
own docstring promises. An independent cross-check against D4D8B's own
subgroup-conflict detection confirmed the two modules never disagree about
whether the same two exact member sets are in tension (§15 of the review) —
no shared production code was introduced, avoiding scope creep. Full suite
after this addendum: 47 tests in this file (41 + 6 new). Full-suite count is
verified fresh in this mission's own final report.
