# FH-D4 Signal Disposition — D4D8D Subgroup Review CLI Report

Implementation-only. No commit, no push, no real database write. A separate
D4D8D adversarial review mission will inspect this diff and, if accepted,
commit/push it.

## 1. Starting HEAD

`e2b25b71a88f773bb097b86cf71f06c8be3654ac` == origin/main — verified before
any work began.

## 2. DB checkpoint

SHA-256 `b183750889e8d6eed1f26bb7bbe26987306c87613663c292d59affae5690d405`,
size 1,822,720 bytes, mtime `1787347226.011277`. FK `[]`, integrity `ok`.
`signal_dispositions`=8, `signal_disposition_members`=19. Fresh D4D4: raw=12,
active=4, confirmed_distinct=7, confirmed_same_effort=1, ambiguous=0.

## 3. Files read fresh

D4D8/D4D8A/D4D8B/D4D8C docs (full), `scripts/review_signal_disposition.py`
(full, pre-change), `tests/test_review_signal_disposition.py` (full, all 29
pre-existing test classes and their helpers), `app/services/fh_d4_
disposition_resolution.py`, `app/services/signal_disposition_conflicts.py`,
`app/services/signal_disposition_persistence.py`, `app/services/signal_
disposition_resolution.py` (all four, already fully reviewed earlier this
session, unchanged since).

## 4. Files modified

- `scripts/review_signal_disposition.py`
- `tests/test_review_signal_disposition.py`

## 5. Files created

- `docs/architecture/fh-d4-signal-disposition-d4d8d-subgroup-cli-report.md`
  (this report)

No ORM/schema, migration, D4D8B/D4D8C production module, or persistence
service was modified — `record_signal_group_disposition()` is called
unchanged, from two call sites (whole-group's own pre-existing one, plus one
new subgroup call site, both passing `decision=config.decision` directly,
verified by an updated AST test).

## 6. CLI/API subgroup contract

Explicit opt-in only: `--parent-signal-id` (repeatable, config field
`parent_signal_ids`), non-empty if and only if the reviewer wants subgroup
mode. `--signal-id` is reused, meaning "target subgroup" instead of "whole
group" whenever `parent_signal_ids` is non-empty — never inferred from
`signal_ids` alone being smaller than some group. Chosen over a second,
parallel flag set to keep the vocabulary minimal and reuse `--signal-id`'s
already-established meaning ("the thing this action targets") uniformly
across both modes.

## 7. Parent-group validation

The parent must match, by exact member-set equality, a group specifically
in the current `active_findings` bucket (`_find_active_parent()`, new) — not
`_find_group()`'s all-four-bucket search whole-group mode uses. A parent
already resolved (`CONFIRMED_DISTINCT`/`CONFIRMED_SAME_REAL_WORLD_EFFORT`)
or `ambiguous_groups` is refused with the new
`PARENT_GROUP_NOT_CURRENT_ACTIVE_BLOCKER`, since D4D8B never computes
`resolved_subgroups`/`unresolved_remainder_signal_ids`/`subgroup_conflict`
for those buckets at all — subgroup review of an already-decided or
already-contested whole group is a usage error, not silently accommodated.

## 8. Target-subgroup validation

Pure input-shape validation in `_validate_config()`, before any database is
opened (mirrors the existing whole-group cardinality check exactly): target
must have ≥ 2 distinct ids and be a STRICT, PROPER subset of the parent as
given (`set(target) < set(parent)`) — never equal, never containing an id
outside the parent. Since this check is relative only to the ONE parent set
the reviewer explicitly named, a target can never "silently span multiple
live raw groups."

## 9. D4D8C conflict integration

Mandatory, no override: `find_signal_disposition_conflicts()` is called for
the target's proposed exact member set before any decision-bearing action
(dry-run's eligibility and write's pre-write re-check alike). A non-empty
result refuses unconditionally via `_SUBGROUP_CONFLICT_REFUSAL`. No
`--force`, no bypass field on `SignalDispositionReviewConfig` — verified by
a dedicated test inspecting both the argparse option strings and the
dataclass's own field names.

## 10. Exact-set subgroup history behavior

The target's own exact-set status is resolved via D3's `resolve_fh_d4_group_
status()` directly (not re-derived). The conflict scan's own default
(`exclude_exact_set=True`) structurally excludes the target's own history
from ever appearing as a conflict — verified by `TestSubgroupExactSetHistory
NotOverlap`. Idempotency/supersession then applies via the SAME
`_evaluate_eligibility()` whole-group mode uses, refactored to accept raw
values (`status`, `ambiguous_history`, `independent_root_count`,
`latest_disposition_id`) instead of an `FhD4OperationalGroup` — one shared
implementation, not duck typing or duplication.

## 11. Remainder semantics

`target_remainder_signal_ids` (parent minus target) is computed and
displayed; nothing in this module can construct a disposition for it — no
code path exists that would let a singleton or multi-member remainder
become a disposition member set.

## 12. Disjoint subgroup behavior

Confirmed permitted: `TestSubgroupDisjointAndOverlap::test_l` writes a first
subgroup `{a,b}` SAME, then successfully dry-runs and writes a fully disjoint
second subgroup `{c,d}` DISTINCT within the same parent — two independent
dispositions coexist, `resolved_subgroups` on the parent correctly shows
both afterward (D4D8B's own already-reviewed logic, unmodified).

## 13. Overlap block behavior

Confirmed blocked identically regardless of decision (same or different) and
regardless of relation (partial overlap, strict subset, strict superset) —
`test_m`/`test_n`.

## 14. Final re-read-before-write behavior

Re-verifies, immediately before persistence: (1) the parent's ENTIRE
`FhD4OperationalGroup` via `_find_active_parent()` + object equality
(catches shape/status/subgroup-metadata changes together, one comparison);
(2) a fresh global conflict scan (catches a new conflict invisible to the
parent-scoped view, e.g. one touching the target from an unrelated part of
the table); (3) the target's own exact-set eligibility fresh. Any divergence
refuses via the SAME `_STATE_CHANGED_REFUSAL` string the whole-group path
already uses.

## 15. Parent grow/shrink/disappear behavior

All three refuse, confirmed by dedicated tests: disappearance entirely
(`test_h`), growth (`test_i` — the mission's own explicit "REFUSE stale
parent" requirement: `{a,b}` remains a technically-valid subset of the new,
grown parent, but the ORIGINALLY-specified parent `{a,b,c}` is no longer
found via exact match at all, so the write refuses regardless), and shrink
to a different-but-still-active shape (`test_j`).

## 16. Transaction/commit result

Unchanged model: read-only engine for inspect/dry-run, writable session only
with `--allow-database-write`, exactly one `session.commit()` on success,
`session.rollback()` on every other return path and on exception (identical
`try/except Exception: session.rollback(); raise` pattern, reused verbatim).

## 17. Legacy D4D5 compatibility

Confirmed: all 63 pre-existing tests pass unmodified in behavior. One
existing test (`test_decision_comes_only_from_config_ast`) required an
intentional, honest update — from "exactly one call site" to "every call
site (now two: whole-group + subgroup) independently satisfies `decision ==
config.decision`" — a strengthening, not a weakening, of the property it
verifies. `TestSubgroupModeNeverInferredFromLegacyCall` explicitly proves
passing fewer `--signal-id` values without `--parent-signal-id` is ordinary
(possibly-not-found) whole-group mode, never silently reinterpreted.

## 18. Display/explainability verdict

Confirmed: `PARENT RAW FH-D4 GROUP` always precedes `TARGET SUBGROUP` in
rendered output, both explicitly labeled; `REMAINDER` and `CONFLICTS`
sections always present before any proposed-decision section; write mode
prints the identical plan text dry-run already showed
(`test_write_mode_prints_same_plan_before_committing`). Mode label always
prefixed `SUBGROUP ` — never confusable with whole-group mode's own labels.

## 19. Information-firewall verdict

Confirmed via the SAME forbidden-attribute AST list `TestNoAutoDecision`
already established (the subgroup path lives in the same module and module-
wide AST scans already cover it), plus a new behavioral test proving
eligibility for Signals with deliberately SECRET-laden titles/values is
identical to ordinary Signals and that no such content leaks into rendered
output.

## 20. Real-case synthetic replay

Roanoke shape (`{37,51,61}`-topology: `{51,61}` SAME subgroup, `{37}`
remainder) and Binghamton shape (`{49,55,58,59,60}`-topology: four-member
SAME subgroup, `{60}` remainder) both pass as fully synthetic fixtures —
never touching the real database.

## 21. Defects/ambiguities found

None in the design itself. One genuine implementation-quality issue caught
and fixed during my own review before finalizing: the initial
`test_no_force_flag_exists` test naively substring-searched the rendered
`--help` text, which legitimately contains the string `--force` inside the
module's own top-of-file docstring (explaining its deliberate absence) —
false-positive-failed. Fixed by inspecting argparse's own registered
`option_strings` instead of rendered help text.

## 22. Corrections made

The AST test above (widened from exactly-one-call-site to every-call-site,
an intentional strengthening); the `--force` test (narrowed from text search
to structural argparse inspection).

## 23. Focused tests

`tests/test_review_signal_disposition.py`: **95 passed** (63 pre-existing +
32 new). Combined with `test_signal_disposition_conflicts.py`, `test_fh_d4_
disposition_resolution.py`, `test_signal_disposition_resolution.py`,
`test_signal_disposition_persistence.py`, `test_signal_disposition_
migration.py`: **425 passed, 0 failed.**

## 24. Full pytest

Run in background; result recorded in this mission's own final report.

## 25. py_compile

`python -m py_compile scripts/review_signal_disposition.py tests/test_review_
signal_disposition.py` — clean, no errors.

## 26. git diff --check

Clean — only the two intended files show as modified (only cosmetic
LF→CRLF autocrlf notices from Git, unrelated to diff content).

## 27. Real DB before/after proof

SHA-256 `b183750889e8d6eed1f26bb7bbe26987306c87613663c292d59affae5690d405`,
1,822,720 bytes, mtime `1787347226.011277` — byte-identical throughout. FK
`[]`, integrity `ok`. `signal_dispositions`=8, `signal_disposition_
members`=19 — unchanged. No `--allow-database-write` was ever used against
the real database; every test uses an isolated `tmp_path`-scoped SQLite file.

## 28. git status

Exactly two modified tracked files
(`scripts/review_signal_disposition.py`, `tests/test_review_signal_
disposition.py`) plus one new untracked file (this report). All other
untracked entries predate this mission and belong to separate, unrelated
in-progress work.

## 29. READY_FOR_D4D8D_REVIEW_CHECKPOINT

yes

## 30. Exact recommended next step

A separate D4D8D adversarial review mission: independently reconstruct the
subgroup contract from the actual diff, attack the parent re-read-before-
write comparison and the conflict-guard integration specifically, verify
the full pytest count, and — if the implementation survives review — commit
and push exactly the files listed in §4-5.

## 31. D4D8D critical review addendum

A subsequent adversarial review pass (D4D8D Critical Review mission)
independently attacked the highest-priority design question first: whether
restricting subgroup-mode parent eligibility to `active_findings`
(excluding `confirmed_distinct`/`confirmed_same_effort`/`ambiguous_groups`)
silently excludes a legitimate parent universe the locked architecture
requires. Resolved, not guessed: proven empirically that a target subset of
ANY already-exact-set-dispositioned parent (resolved OR ambiguous) ALWAYS
triggers a D4D8C `STRICT_SUPERSET` conflict regardless of which bucket the
parent is found in — restricting to `active_findings` is therefore
functionally equivalent in ultimate outcome (no legitimate write capability
is lost), consistent with D4D8B's own already-locked exact-set precedence,
and gives an earlier, clearer refusal. **Not a material architecture
contradiction — confirmed correct, not merely trusted.**

Further live adversarial attacks (parent shrinking to exactly equal the
target; a target member itself, not a remainder member, leaving the parent;
a genuinely cross-airport global conflict; failure injection on the
subgroup write call site specifically; the schema gate combined with a
subgroup-mode request; parent already resolved SAME - not just DISTINCT;
parent with ambiguous whole-group history; parent with pre-existing
conflicting subgroup metadata still valid and honestly displayed) all
independently verified CORRECT behavior that had not been locked in as
permanent regression tests. **No production code defect was found.** Real-DB
read-only structural smoke tests for both the Roanoke (`{37,51,61}` →
`{51,61}`, remainder `{37}`) and Binghamton (`{49,55,58,59,60}` →
`{49,55,58,59}`, remainder `{60}`) shapes confirmed the CLI is structurally
ready for a future, separately-authorized real write, with the real database
verified byte-identical before and after. Query count measured independently
at 19 SELECTs, flat across raw-group sizes 3/6/12 — no N+1.

8 new regression tests added (`test_parent_shrinks_to_exactly_equal_target_
refuses`, `test_target_member_itself_leaves_parent_refuses`,
`test_cross_airport_global_conflict_still_refuses`,
`TestSubgroupFailureInjection`, `test_parent_already_resolved_same_at_
whole_group_level_refused`, `test_parent_with_ambiguous_whole_group_
history_refused`, `test_parent_with_conflicting_existing_subgroup_metadata_
still_valid_and_displayed`, `test_missing_schema_blocks_subgroup_mode_too`)
— 95 → 103 tests in this file. Full-suite count is verified fresh in this
mission's own final report.
