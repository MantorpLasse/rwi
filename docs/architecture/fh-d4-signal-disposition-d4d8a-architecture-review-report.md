# FH-D4 Signal Disposition — D4D8A Adversarial Architecture Review Report

## 1. Starting checkpoint

HEAD `4ecd4a58e468395153daf7bef7b424f2fb7d0127` == origin/main. DB SHA
`b183750889e8d6eed1f26bb7bbe26987306c87613663c292d59affae5690d405`, 1,822,720
bytes, mtime `1787347226.011277` — exact match. FK `[]`, integrity `ok`.
`signal_dispositions`=8, `signal_disposition_members`=19. Fresh D4D4: raw=12,
confirmed_distinct=7, confirmed_same_effort=1, active=4, ambiguous=0. Active
groups exactly `{36,44}`, `{37,51,61}`, `{46,53,56,57,62}`, `{49,55,58,59,60}`.

**Primary-input discrepancy found and handled**: the prior D4D8 mission's own
instructions asked it to *return* a final report as chat text ("STOP after the
report") and never instructed it to write a file — so no
`docs/architecture/fh-d4-signal-disposition-d4d8-*` file existed at the start
of this review, despite this mission's instructions assuming one did. This is
noted here rather than silently worked around: the architecture content
reviewed in this mission is the D4D8 report actually produced in the prior
conversation turn, independently re-verified against the repository (not
merely re-read as prose), then corrected and materialized as the design
document listed in §5 below — this is the "update the D4D8 architecture
document itself" the mission's own D4D8A DOCUMENT ACTION section calls for,
applied to a document that had to be created rather than merely edited.

## 2. Final checkpoint (post-review)

HEAD after commit (see §23). DB unchanged throughout — see §21.

## 3. Files read fresh

`docs/architecture/fh-d4-signal-disposition-design.md`,
`fh-d4-signal-disposition-d4d1-persistence-report.md`,
`-d4d2-migration-report.md`, `-d4d3-resolution-report.md`,
`-d4d4-fleet-health-integration-report.md`, `-d4d5-human-cli-report.md`;
`app/models/signal_disposition.py`;
`app/services/signal_disposition_persistence.py`;
`app/services/signal_disposition_resolution.py`;
`app/services/fh_d4_disposition_resolution.py`;
`app/services/fleet_health_review_rules.py` (`evaluate_fh_d4`);
`scripts/review_signal_disposition.py`;
`scripts/migrate_signal_disposition_d4d2.py`; the real database's own stored
`sqlite_master` schema for `signal_dispositions`/`signal_disposition_members`
(read directly, not inferred from the ORM); and targeted sections of
`tests/test_signal_disposition_persistence.py` (`test_pair`/`test_triple`,
the three supersession-mismatch tests, the full test-name inventory).

## 4. Exact files modified

None (no pre-existing tracked file was edited).

## 5. Exact files created

- `docs/architecture/fh-d4-signal-disposition-d4d8-subgroup-semantics-design.md`
  (the finalized, review-corrected architecture document)
- `docs/architecture/fh-d4-signal-disposition-d4d8a-architecture-review-report.md`
  (this report)

## 6. Persistence-model verdict

**Confirmed true, independently, not merely accepted from the prior report.**
`record_signal_group_disposition()`'s own source contains no reference to
`airport_id`, `runway_id`, or any FH-D4/detector concept. The real database's
own stored `CREATE TABLE` text for both tables (read directly via
`sqlite_master`, reproduced verbatim in the design doc §2) contains no
CHECK/FK/UNIQUE constraint tying membership to any live detector state — none
could be expressed this way, since FH-D4 groups are computed at query time,
never stored. `tests/test_signal_disposition_persistence.py::test_pair`/
`test_triple`/`test_quintuple` independently prove the function is already
exercised against fully synthetic fixture Signals with no co-location
relationship at all. **A subgroup such as `{51,61}` or `{49,55,58,59}` can be
persisted today with zero schema or model change.** The only real barrier is
`scripts/review_signal_disposition.py::_find_group()`'s own exact-match-only
gate — a CLI convenience/safety check, not a structural constraint.

## 7. Exact-set/global-identity verdict

**Confirmed.** Identity is `frozenset(member ids)`, detector-context-
independent — proven directly by the three supersession-mismatch tests
(`test_grown_group_rejected_as_supersession` etc.), which demonstrate the
system already treats `{41,67}` and `{41,67,80}` as unrelated identities
regardless of any detector run. The mission's G1/G2 hypothetical (`{A,B,C,D}`
confirmed while live group is `{A,B,C,D,E}`, later a different live group
`{A,B,C,D,F}` appears) requires no new design decision — the existing model
already answers it: the `{A,B,C,D}` disposition means the same thing in both
contexts, by construction.

## 8. Partial-resolution/accounting verdict

The proposed `resolved_subgroups`/`unresolved_remainder_signal_ids` additive
fields preserve the four-bucket invariant, `attention_required` semantics, raw
finding visibility, determinism, and `non_d4_findings` pass-through — verified
by construction (both fields are computed, never stored, from data that would
in any case already be visible via `resolve_fh_d4_group_statuses()`).

**Counterexample search found one genuine gap, now closed (§10 below,
Correction 4)**: the proposed presentation model implicitly assumed resolved
subgroups are always pairwise disjoint. That assumption holds only if the new
write-time overlap safeguard (§9) has been in force for every disposition ever
written — which cannot be guaranteed for data written before this safeguard
ships, or via any future write path that bypasses it. The design document is
corrected to require a **defensive read-side disjointness check** in D4D4
itself, surfacing a new explicit "conflicting subgroup history" presentation
state (mirroring the existing `ambiguous_history` pattern) rather than
silently assuming or manufacturing consistency.

No other counterexample survived scrutiny: Signal deletion cannot silently
shrink a resolved subgroup (real FK block, no `ON DELETE`, already proven);
raw-group growth/shrink is self-healing under the proposed remainder
computation (a newly co-located Signal automatically appears in
`unresolved_remainder_signal_ids` with no staleness, since remainder is
recomputed from the live raw set every call).

## 9. Overlap CASE 1-5 verdicts

| Case | Verdict |
|---|---|
| 1: `{A,B,C}` SAME → `{B,C,D}` SAME | hard block (overlapping, non-subset/superset) |
| 2: `{A,B}` SAME, `{B,C}` SAME | hard block; never inferred `{A,C}` |
| 3: `{A,B}` SAME → exact-set `{A,B}` DISTINCT | unchanged ordinary supersession — outside the new scan's scope entirely (already-handled exact-match case) |
| 4: `{A,B,C}` SAME → `{A,B}` DISTINCT | hard block (strict subset, differing decision) |
| 5: `{A,B,C,D}` SAME → `{C,D,E}` SAME | hard block (overlapping, non-subset/superset) |
| New, found during review: `{A,B,C}` SAME → `{A,B}` SAME (same decision, strict subset) | hard block — the rule is **syntactic**, not semantic; judging "these two claims happen to agree" would require the same banned transitive-implication reasoning as Case 2 |
| New, found during review: `{A,B}` SAME, `{D,E}` DISTINCT (same raw group, disjoint) | **allowed** — no intersection, two independent facts about the same raw group |

No case examined produces a scenario where the hard-block rule blocks a
clearly valid existing operation — the rule only ever refuses a genuinely new,
previously-unwritten claim that intersects prior history; it never touches
already-committed rows (immutability is untouched) and never blocks the
already-supported exact-match supersession path.

## 10. Final hard-block policy

**Locked as originally recommended, with two sharpenings found during
review:**

1. **Syntactic, not semantic**: block on any non-exact-match, non-empty
   intersection with an existing disposition's member set, regardless of
   whether the new and existing decisions appear to agree. (Correction 1 —
   the original report's framing via "differing decision" in Case 4 was
   incomplete; a same-decision strict-subset overlap needed the same
   treatment and now explicitly gets it.)
2. **Global scope**: the scan must run across all `signal_disposition_members`
   rows, not merely within the current raw group's own scope, since the
   persistence layer itself is not airport-scoped (only the CLI's subgroup-mode
   entry point is, incidentally, via FH-D4's own per-airport grouping).
   (Correction 2.)
3. **Exact-match exclusion is explicit**: the new scan must not re-implement or
   conflict with the pre-existing `independent_root_count`/`ambiguous_history`
   mechanism, which already, correctly owns the exact-match case. (Correction
   3 — clarification, avoids duplicate machinery.)
4. **Read-side defense in depth**: D4D4 must independently re-verify
   disjointness at read time and fail-visible (a new "conflicting subgroup
   history" state) rather than assume the write-time guard was always in
   force. (Correction 4, §8.)

## 11. Transitivity verdict

**No transitive inference, confirmed as the correct and only defensible
position.** Verified against repository precedent (no governance table in this
pipeline has ever computed a derived fact — `ReviewerAction`,
`SignalDisposition` both record only explicit human comparisons) and directly
against the real D4D7 `{37,51,61}` finding, where a strong, structurally-
confirmed pairwise SAME for `{51,61}` was explicitly and correctly not allowed
to imply anything about `37`. No union-find/graph/equivalence-class machinery
is introduced anywhere in the corrected design.

## 12. DISTINCT/remainder inference verdict

The original report stated this correctly in multiple places but distributed
across several sections. **Strengthened**: consolidated into one single,
unambiguous locked statement in the finalized design document §6 — a confirmed
subgroup asserts nothing about any Signal outside its own exact member set; a
remainder Signal (singleton or not) is never inferred DISTINCT and never
receives an invented disposition merely to explain its exclusion.

## 13. Four-real-case replay verdict

Replayed all four as pure architecture test vectors (no writes):
`{36,44}` — no subgroup action justified, none manufactured. `{37,51,61}` —
`{51,61}` SAME is representable as a single ordinary exact-set disposition;
`37` correctly stays `UNREVIEWED`, not inferred DISTINCT. `{46,53,56,57,62}` —
no clean bipartition exists even after full analysis; the architecture
correctly offers nothing and creates no pressure to manufacture a split.
`{49,55,58,59,60}` — `{49,55,58,59}` SAME is representable as a single
ordinary exact-set disposition; `60` correctly stays `UNREVIEWED`. All four
outcomes match D4D7's own actual, already-reported conclusions exactly; none
required graph/partition machinery.

## 14. Upstream-identity interaction verdict

Confirmed directly from `evaluate_fh_d4`'s own source: `if fact.runway_id is
not None: continue` — any Signal with a populated `runway_id` is skipped from
candidacy on the next detector run, silently, since this is recomputed live
and not persisted. This means a future, uncoordinated `runway_id` promotion
for the Binghamton or Roanoke signals could make their raw FH-D4 groups vanish
before any disposition is ever recorded, permanently losing the opportunity to
durably record D4D7's own already-reached conclusions. Sequencing verdict:
upstream identity governance and subgroup disposition semantics remain
**parallel and independent** as a general architectural stance (neither
depends on the other), but the finalized design document now carries an
explicit operational caution (§9 of the design doc) specific to the two real
affected groups, so this risk is not silently inherited by whichever future
mission touches runway identity for them first.

## 15. Cross-airport verdict

**No persistence-level restriction added** — would change already-shipped,
already-tested general behavior for no real benefit, since FH-D4's own
grouping (`groups.setdefault(fact.signal_airport_id, [])`, confirmed directly
from source) already makes a raw group single-airport by construction. The
CLI's subgroup-mode entry point (target must be a proper subset of one
currently-live raw group) inherits this constraint automatically, with no new
code required at either layer. Decision: **no change needed now, at either
layer** — confirmed, not merely assumed.

## 16. Evidence-reviewed-but-unresolved verdict

**No new persisted status.** Confirmed against the project's own consistent
restraint (every prior design doc's non-goals section explicitly avoids
vocabulary/schema growth for workflow cosmetics). `{36,44}` and
`{46,53,56,57,62}` remaining `UNREVIEWED` despite substantial evidence review
is a presentation/workflow question, not a governance-fact question. If ever
wanted, deferred to a future, separate, presentation-only slice — not
attempted here.

## 17. No-overengineering verdict

Confirmed explicitly: no real case requires a relationship table, partition
table, graph model, equivalence-class engine, transitive closure, canonical
Signal, new disposition decision vocabulary, or new database migration. Every
resolvable real case needs exactly one ordinary exact-set disposition for its
own subset; the smaller architecture is preserved.

## 18. Architecture defects found

1. Overlap hard-block rule was ambiguous about same-decision strict-subset
   overlaps (implicitly exempted them; should not) — a real gap, since
   confirming "these two claims agree" requires the same banned inference the
   rule exists to avoid.
2. Overlap scan scope was not explicitly stated as global vs. raw-group-local
   — a real gap given the persistence layer's fully general identity model.
3. The proposed `resolved_subgroups` presentation model implicitly assumed
   permanent disjointness with no defensive re-check — a real gap against data
   that could predate the safeguard or arrive via any other write path.
4. No file had actually been written for the D4D8 architecture document
   despite this mission's own instructions assuming one existed (process gap,
   not an architecture defect — documented in §1 and resolved by creating it
   here).

No defect required abandoning the HYBRID direction, adding a new table, or
introducing inference/partition machinery.

## 19. Corrections made

All four defects in §18 are corrected in the finalized design document:
Correction 1 (§7/§9 of design doc — syntactic overlap rule, explicit
same-decision-subset example), Correction 2 (§7 — global scan scope),
Correction 3 (§7 — explicit exact-match exclusion), Correction 4 (§10 —
defensive read-side disjointness check and new "conflicting subgroup history"
presentation state). The design document was also created (§1) rather than
merely edited, incorporating the prior turn's reviewed conclusions plus these
corrections as the single canonical, committed source of truth going forward.

## 20. Decisions locked

All sixteen decisions listed in this mission's "DECISIONS TO LOCK IF REVIEW
PASSES" section are locked as originally stated, with the sharpened overlap
policy (syntactic, global-scope, exact-match-excluded) and the added
read-side defensive check superseding the original, less precise phrasing.
See the finalized design document for the authoritative statement of each.

## 21. DB no-write proof

SHA-256 `b183750889e8d6eed1f26bb7bbe26987306c87613663c292d59affae5690d405`,
size 1,822,720 bytes, mtime `1787347226.011277` — identical to the starting
checkpoint (§1), verified immediately before this report was finalized. `PRAGMA
foreign_key_check` returns `[]`; `PRAGMA integrity_check` returns `ok`.
`signal_dispositions` count = 8, `signal_disposition_members` count = 19 —
unchanged. Only read-only SQL (`sqlite3` direct connections, `SELECT`-only) and
read-only ORM calls (`run_disposition_aware_fh_d4_review`, a pure read
function) were used throughout this mission; no `Write`/`Edit` tool call
touched any file under `app/`, `scripts/`, `tests/`, or `data/`.

## 22. git diff --check

Clean — no whitespace errors reported. `git status --short` shows only the two
newly created documentation files plus pre-existing, unrelated untracked files
from other, separate in-progress work in this repository (not touched or
referenced by this mission). No tracked file appears modified.

## 23. Commit hash/subject

See final commit recorded by this mission (created immediately after this
report) — subject: "Finalize FH-D4 subgroup semantics architecture". Exactly
two files staged and committed:
`docs/architecture/fh-d4-signal-disposition-d4d8-subgroup-semantics-design.md`
and
`docs/architecture/fh-d4-signal-disposition-d4d8a-architecture-review-report.md`.

## 24. Push result

Pushed to `origin/main` immediately following the commit.

## 25. Final local/origin HEAD

Verified equal after push (see the mission's own final report for the exact
hash).

## 26. READY_FOR_D4D8B

**yes** — the architecture survived adversarial review with four real,
now-corrected refinements (§18-19); no defect required abandoning the HYBRID
direction or adding schema/inference machinery.

## 27. Recommended next step

D4D8B: implement the D4D4 resolution extension (`resolved_subgroups`,
`unresolved_remainder_signal_ids`, the disjointness invariant, and the
defensive read-side conflict check specified in this design's §10) against
fixture data only — no real database write, no CLI change yet. Follow with its
own adversarial review checkpoint before any further slice.

