# FH-D4 Signal Disposition — D4D8 Subgroup Semantics Architecture Design

Design-only, adversarially reviewed and locked in D4D8A. No production code, migration,
or test in this document; no schema change anywhere in this repository or the real
database by this or any prior D4D8 task.

## 1. Problem statement

D4D7's real human review of all 12 real FH-D4 candidate groups left four groups
unresolved:

- `{36,44}` — genuinely insufficient evidence.
- `{37,51,61}` — evidence-supported mixed topology: `{51,61}` SAME-supported,
  `37` distinct-supported from both.
- `{46,53,56,57,62}` — abundant but genuinely conflicting evidence; no clean
  bipartition exists.
- `{49,55,58,59,60}` — evidence-supported mixed topology: `{49,55,58,59}`
  SAME-supported (all four independently, consistently name "Runway 34 end" in
  their own award text; a gapless design→procurement→final(62%+38%=100%) phase
  sequence), `60` distinct-supported (explicitly names the opposite "Runway 16
  end," restarts its own phase numbering at "Phase 1," arrives three years later).

The existing whole-group-only disposition vocabulary correctly refuses to force a
verdict on any of the four (this is working as designed) but has no way to durably
record the two genuine, evidence-backed partial conclusions (`{37,51,61}` and
`{49,55,58,59,60}`'s own sub-clusters) that D4D7 actually reached.

## 2. Existing contract (reconstructed fresh, independently verified from source)

- **Exact-set identity**: a disposition's identity is `frozenset(member Signal
  ids)` — no fingerprint. `{41,67}` and `{41,67,80}` are unrelated identities.
  Independently reconfirmed from `app/services/signal_disposition_resolution.py`'s
  own "EXACT-SET IDENTITY" docstring section and from
  `tests/test_signal_disposition_persistence.py`'s
  `test_grown_group_rejected_as_supersession`/`test_shrunk_group_rejected_as_supersession`/
  `test_different_same_size_group_rejected_as_supersession`.
- **Minimum cardinality**: `MINIMUM_GROUP_CARDINALITY = 2`, service-level only
  (no DB CHECK — confirmed against the real database's own stored `CREATE TABLE`
  text, which contains no cardinality constraint of any kind).
- **Persistence layer has zero FH-D4 awareness** — independently verified three
  ways: (1) `record_signal_group_disposition()`'s own source contains no
  reference to `airport_id`, `runway_id`, or any detector concept; (2) the real
  database's own stored schema (`sqlite_master`) contains no CHECK/FK referencing
  live detection state — none could even be expressed, since FH-D4 groups are
  computed at query time from live `Signal` rows, never stored; (3)
  `tests/test_signal_disposition_persistence.py`'s `test_pair`/`test_triple`/
  `test_quintuple` exercise the function against fully synthetic fixture Signals
  with no co-location/runway relationship at all. **A subgroup such as
  `{51,61}` or `{49,55,58,59}` can be persisted today, exactly as-is, with zero
  schema or model change.** The only barrier is `scripts/review_signal_disposition.py`'s
  own `_find_group()`, which requires `group.signal_ids == canonical_key`
  against a currently-live FH-D4 raw finding — a CLI-only gate, not a
  persistence-layer constraint.
- **Supersession semantics**: `supersedes_id`, when supplied, must reference a
  prior disposition with an **identical** member set (service-enforced) — never
  subset/superset. Confirmed: this check only fires when `supersedes_id` is
  explicitly supplied. **Independently found during D4D8A review**: nothing
  prevents a caller from recording a second, non-superseding disposition whose
  member set merely *overlaps* an existing disposition's member set — this gap
  is real, not hypothetical, and is exactly what §7 below closes.
- **FH-D4 detector**: fully dynamic, re-derived every call
  (`app/services/fleet_health_review_rules.py::evaluate_fh_d4`) — groups
  Signals sharing `airport_id` where `runway_id is None`, `len(group) >= 2`.
  Independently confirmed: any Signal whose `runway_id` is populated is skipped
  from candidacy entirely (`if fact.runway_id is not None: continue`) — not
  persisted state, recomputed fresh from live `Signal` rows every run. Also
  confirmed: grouping is keyed by `fact.signal_airport_id` — a raw FH-D4 group
  can never span more than one airport, by construction.
- **D4D4 accounting invariant**: every raw FH-D4 finding lands in exactly one of
  `active_findings`/`confirmed_distinct`/`confirmed_same_effort`/`ambiguous_groups`;
  `attention_required` is a derived, non-exclusive view.

## 3. Chosen direction: HYBRID (B + D)

- **B — exact-subgroup dispositions** using the *existing* exact-set persistence
  model, unchanged.
- **D — upstream runway/runway-end/installation identity governance**, pursued
  separately, independently justified, not a dependency of B.

Rejected: **A** (whole-group-only) discards two real, already-reached
conclusions with no durable record. **C** (explicit partition/topology tables,
transitive-closure inference, relationship rows) is unjustified by the real
data — exactly one clean bipartition exists per resolvable case, fully
expressible by B; building relationship/partition machinery for two real cases
is the "graph ontology the evidence doesn't require." **D alone** solves
detector noise, not governance recording, and can actively mask a conclusion
that was never recorded (§9).

## 4. "Subgroup" is contextual/derived, not a new persisted concept

A "subgroup disposition" is not a new kind of row. It means: an ordinary
exact-set `SignalDisposition` whose member set happens to be a **proper,
non-empty subset (≥2)** of some *currently-live* FH-D4 raw group's own member
set, as observed at read/write time. Nothing is stored to mark a disposition as
"a subgroup" — that relationship is always recomputed from the live raw group,
exactly the same way `find_related_historical_dispositions()` already computes
subset/superset relationships today. **Global exact-set identity is
unaffected**: `{A,B,C,D}` SAME means exactly the same thing whether the live
raw group is `{A,B,C,D,E}` or, later, `{A,B,C,D,F}` — the existing D4D1/D4D3
model was already detector-context-independent; subgroup semantics inherits
this for free and makes no new identity-scoping decision.

## 5. No transitive inference, ever

`{A,B}` SAME and `{B,C}` SAME never implies `{A,C}` SAME or `{A,B,C}` SAME.
No union-find, no equivalence-class engine, no derived closure — persisted or
computed — is introduced anywhere by this design. This matches the project's
consistent precedent (no governance table in this pipeline has ever computed a
derived fact) and is directly demonstrated by D4D7's own `{37,51,61}` finding:
a strong, structurally-confirmed pairwise SAME for `{51,61}` was correctly
**not** allowed to imply anything about `37`.

## 6. DISTINCT / remainder inference — locked, single statement

**A confirmed subgroup disposition asserts nothing whatsoever about any Signal
outside its own exact member set.** A raw-group member not covered by any
resolved subgroup remains `UNREVIEWED` — never inferred `DISTINCT`, never
inferred anything — regardless of how many other members of the same raw group
have been confirmed SAME. "Not proven SAME" must never collapse into "proven
DISTINCT." A singleton remainder (e.g., `{60}` alone, or `{37}` alone) is never
given its own disposition — a disposition requires ≥2 members being compared to
each other, and nothing needs to be asserted structurally about a lone,
unreviewed remainder Signal. A multi-signal remainder *may* independently be
dispositioned later as its own ordinary, separate exact-set decision.

## 7. Overlap / contradiction safety — locked policy

**Any new disposition whose member set has a non-empty intersection with an
existing disposition's member set, and is not an exact match, is a HARD,
write-time refusal — fail closed, unconditionally, syntactic not semantic.**

This must be **syntactic**, not semantic: the system does not attempt to judge
whether two overlapping claims "agree." `{A,B,C}` SAME followed by a proposed
`{A,B}` SAME (same decision, strict subset) is blocked exactly the same as
`{A,B,C}` SAME followed by a proposed `{A,B}` DISTINCT (differing decision,
strict subset) — judging "these two overlapping claims happen to agree" would
require exactly the transitive-implication reasoning §5 forbids computing. A
human seeing the refusal reviews both existing dispositions directly and
records one fresh, unified disposition for the correct set if a correction is
warranted.

Case-by-case:

| Case | Relationship | Outcome |
|---|---|---|
| `{A,B,C}` SAME, then `{B,C,D}` SAME | overlapping, non-subset/superset | **hard block** |
| `{A,B}` SAME, `{B,C}` SAME | overlapping, non-subset/superset | **hard block** (also: never inferred `{A,C}`, §5) |
| `{A,B}` SAME, then `{A,B}` DISTINCT | exact match | ordinary exact-set supersession (unchanged, requires explicit `supersedes_id`) — **not** subject to the new overlap scan |
| `{A,B,C}` SAME, then `{A,B}` DISTINCT | strict subset, differing decision | **hard block** |
| `{A,B,C}` SAME, then `{A,B}` SAME | strict subset, same decision | **hard block** (syntactic rule — redundancy is not exempted) |
| `{A,B,C,D}` SAME, then `{C,D,E}` SAME | overlapping, non-subset/superset | **hard block** |
| `{A,B}` SAME, then `{D,E}` DISTINCT (same raw group) | disjoint | **allowed** — two independent, non-conflicting subgroup facts about the same raw group |

The exact-match case is explicitly **excluded** from this new scan — it is
already, correctly handled by the pre-existing `independent_root_count`/
`ambiguous_history` mechanism (D4D3/D4D4); the new scan must not duplicate or
conflict with it.

**Scope**: the scan must be computed **globally**, across all
`signal_disposition_members` rows — not merely within the current raw group's
own scope. The persistence layer's identity model is fully general and not
airport-scoped; the CLI's own raw-group scoping (§10) is real but is not a
structural guarantee enforced by the persistence layer itself, so the safeguard
must not assume it.

## 8. Cross-airport dispositions — decision

**No persistence-level restriction is added.** The already-shipped,
already-tested `record_signal_group_disposition()` remains fully general (no
airport check) — adding one now would change already-committed, production-used
behavior for no real benefit, since no real case requires it. **The CLI-level
constraint is sufficient and already implicit**: subgroup-review mode (§10)
requires the target to be a proper subset of exactly one currently-live FH-D4
raw group, and FH-D4's own grouping is keyed by `signal_airport_id` — a raw
group can never span more than one airport by construction (independently
verified, §2). This makes cross-airport subgroup review impossible through the
sanctioned CLI write path without any new code. No change needed now, at either
layer.

## 9. Upstream-identity interaction — verdict and sequencing caution

Confirmed from source (§2): populating `Signal.runway_id` removes that Signal
from FH-D4 candidacy on the next detector run. Promoting D4D7's own reviewed
evidence (e.g., "#49/55/58/59 target Runway 34 end") into `Signal.runway_id`/
`installation_id`/`PhysicalInstallationIdentity` is independently valuable
data-quality work and, as a side effect, would silence FH-D4 for those Signals.
**This is not a substitute for recording the disposition**: doing so without
also recording the human conclusion trades a visible open question for an
invisible one, which is worse for auditability than doing nothing.

As a general architectural stance, upstream identity governance (D) and
subgroup disposition semantics (B) remain **parallel and independent** —
neither depends on the other, and both remain valid regardless of which is
built first or whether the other is ever built at all.

**Operational caution specific to the four real groups**: for `{37,51,61}` and
`{49,55,58,59,60}` specifically, any future upstream `runway_id`/installation-
identity promotion touching their members should not run silently ahead of, or
disconnected from, an opportunity to record the disposition B now supports —
otherwise the exact conclusion D4D7 already reached could be silently lost to
detector suppression before it is ever durably recorded anywhere.

## 10. Fleet Health (D4D4) operational semantics

The four-bucket accounting invariant is unchanged. A raw group with a
partially-resolved subgroup **stays in `active_findings`** — it is not closed.
`FhD4OperationalGroup` gains purely additive fields:

```
resolved_subgroups: tuple[SubgroupDispositionSummary, ...]
unresolved_remainder_signal_ids: tuple[int, ...]
```

**New invariant**: for every group, `resolved_subgroups`' member sets are
pairwise disjoint, and their union plus `unresolved_remainder_signal_ids`
exactly equals the raw group's own `signal_ids` — every raw member accounted
for in exactly one of "confirmed in a subgroup" or "remainder," never both,
never neither.

**Defensive read-side check (D4D8A strengthening)**: the write-time hard block
(§7) can only prevent *future* bad states — it cannot guarantee no
overlapping-subgroup condition exists from data written before this safeguard
shipped, or from any other future write path. D4D4's presentation layer must
therefore **independently re-verify** at read time that resolved subgroups for
a given raw group are pairwise disjoint, and if they are not, must surface a
new, explicit "conflicting subgroup history" presentation state — mirroring the
existing `ambiguous_history`/`independent_root_count > 1` pattern exactly —
rather than silently picking a subset of the conflicting subgroups to display
or manufacturing an inconsistent-but-quiet result. Fail-visible over
silently-manufactured consistency, applied at both write time and read time.

## 11. Human-review (D4D5) semantics

D4D5 gains an explicit subgroup-review mode. The reviewer targets a proper,
non-empty subset (≥2) of a currently-live FH-D4 raw group. The CLI must render,
loudly, which mode is active and that any un-targeted remainder will **not**
be dispositioned by this action. Existing/overlapping dispositions intersecting
the proposed set are displayed before any write and block it per §7. The
existing pre-write re-read is extended to also re-verify the target is still a
proper subset of the (possibly changed) live raw group. No singleton-remainder
disposition is ever offered (§6).

## 12. Persistence/schema

**No schema change.** `record_signal_group_disposition()` requires no
modification. The only new service-level code is the overlap/contradiction scan
(§7) — a new, narrow, read-only function, not a new table.

## 13. Backward compatibility

The eight existing real dispositions (7 DISTINCT, 1 SAME) remain valid,
unchanged, requiring no backfill or reinterpretation — under exact-set-global
semantics (§4) they already mean exactly what they will continue to mean.
Nothing is deprecated.

## 14. Real four-group replay (architecture test vectors, no writes)

- `{36,44}`: no positive anchor exists in D4D7's own evidence — no subgroup
  action justified; remains whole-group `UNREVIEWED`.
- `{37,51,61}`: eligible for a future, separately-authorized subgroup write
  recording `{51,61}` SAME. `37` remains `UNREVIEWED` (never inferred
  DISTINCT). Representable with a single exact-set disposition — no
  partition/graph machinery required.
- `{46,53,56,57,62}`: no clean bipartition exists even after full 10-pair/
  H1-H4 analysis — remains whole-group `UNREVIEWED`; this architecture offers
  nothing here and must not be used to manufacture a split the evidence does
  not support. Best future candidate for upstream-identity work instead.
- `{49,55,58,59,60}`: eligible for a future, separately-authorized subgroup
  write recording `{49,55,58,59}` SAME. `60` remains `UNREVIEWED`.
  Representable with a single exact-set disposition.

## 15. No-overengineering verdict

No real case requires a relationship table, partition table, graph model,
equivalence-class engine, transitive closure, canonical Signal, new disposition
decision vocabulary, or new database migration. Confirmed by direct replay of
all four real active groups (§14) — each resolvable case needs exactly one
ordinary exact-set disposition for its own subset; the two unresolvable cases
need nothing this architecture provides. The smaller architecture (B + a narrow
overlap safeguard, D pursued in parallel) is preserved.

## 16. "Evidence-reviewed but unresolved" — no new status

`{36,44}` and `{46,53,56,57,62}` have undergone substantial human evidence
review yet remain `UNREVIEWED` in disposition vocabulary. **No new persisted
status is added.** This is a presentation/workflow concern, not a governance-
fact concern — the disposition vocabulary answers "what did a human conclude
about this exact set," not "how much effort has been spent looking." Adding a
vocabulary value to encode review effort would be schema growth for workflow
cosmetics, against this project's own established restraint. If ever wanted, a
future, separate, presentation-only slice (e.g., an optional review-note
annotation, unrelated to the DISTINCT/SAME vocabulary) is the right shape —
not attempted here.

## 17. Implementation boundaries and sequence

- **D4D8A** — this architecture document + adversarial review report
  (committed, no code). Complete as of this document.
- **D4D8B** — D4D4 resolution extension: subgroup-within-raw-group detection,
  `resolved_subgroups`/`unresolved_remainder_signal_ids` fields, the new
  disjointness invariant, the defensive read-side conflict check (§10), tests.
- **D4D8C** — overlap/contradiction hard-refusal scan (§7): new, narrow,
  read-only, global-scope service function; tests explicitly covering every
  case in §7's table plus the disjoint/allowed case.
- **D4D8D** — D4D5 CLI subgroup-review mode (§11): explicit mode flag, wires in
  D4D8C's refusal, extends the pre-write re-read, tests.
- **D4D8E** — adversarial synthetic review: replay all four real groups' actual
  evidence shapes as fixtures (not real writes) proving correct refuse/allow
  behavior for each, plus every §7 case explicitly.
- **D4D8F** — (separately authorized, not part of this design) real human
  subgroup-review pass against the two real eligible cases (`{51,61}`,
  `{49,55,58,59}`), following the same evidence-review-then-explicit-write-
  authorization pattern every D4D7 mission already used.
- The upstream-identity-governance initiative (§9) is explicitly **not** part
  of this slice sequence — a separate, parallel workstream, not a dependency of
  D4D8B-F and not blocked by them, with the sequencing caution in §9 applying
  regardless of which sequence it runs in.
- Each of D4D8B-E follows the established implement → adversarial review
  checkpoint → commit/push pattern; D4D8F follows the established D4D7
  evidence-review → separate-authorization → write pattern.

## 18. Explicit non-goals (unchanged from D4D8, reaffirmed after review)

No transitive-closure inference, ever. No new schema/table. No
`canonical_signal_id`. No automatic Signal merge/delete/publication change. No
soft-warning overlap handling — hard refusal only (§7). No subgroup disposition
for a set that is not a proper subset of a currently-live FH-D4 raw group. No
singleton-remainder disposition. No change to the eight existing real
dispositions. No execution of any real write by this document or its review.

## 19. Open questions requiring human decision (unresolved, carried forward)

- Priority/sequencing of the upstream-identity-governance workstream relative
  to D4D8B-F — this design takes no position beyond noting they are
  independent (§9).
- Whether `{46,53,56,57,62}`'s and `{36,44}`'s continued whole-group-
  `UNREVIEWED` status should ever get a different Fleet Health presentation
  treatment distinguishing "evidence-reviewed, still unresolved" from "never
  looked at" — explicitly deferred (§16), not decided here.
