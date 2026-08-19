# Human review queue — Slice 8 report

Implements Slice 8 of the roadmap in
[signal-promotion-policy-slice5-design.md](signal-promotion-policy-slice5-design.md)
§25, a read-only surfacing layer over the fully governed evidence chain
built by Slices 1/4/6/7
([promotion-policy-persistence-slice7-report.md](promotion-policy-persistence-slice7-report.md),
[promotion-policy-core-slice6-report.md](promotion-policy-core-slice6-report.md),
[intelligence-review-persistence-slice4-report.md](intelligence-review-persistence-slice4-report.md)).

## 1. Starting HEAD

`af4b00d457ce29c3f784414e4c1ea151c7885b82` ("Add promotion policy persistence"),
branch `main`, matched `origin/main`. Baseline full pytest: 1019 passed,
verified before implementation began.

## 2. Queue API

```python
def list_human_review_items(session: Session, *, limit: int | None = None) -> tuple[HumanReviewItem, ...]
```

in `app/services/human_review_queue.py`. `SELECT`-only — no `add()`,
`flush()`, or `commit()` anywhere in the module. Returns immutable
`HumanReviewItem` dataclasses, never live ORM instances (safe to hold/
compare/serialize after the session closes).

## 3. Filter semantics

`WHERE SourceAssertion.promotion_policy_decision == "HUMAN_REVIEW_REQUIRED"`
exactly (the literal `PromotionPolicyOutcome.HUMAN_REVIEW_REQUIRED.value`,
imported from the unmodified Slice 6 core rather than a magic string).
`AUTO_ELIGIBLE`, `DO_NOT_PROMOTE`, `NULL`, and any malformed/unrecognized
value are all excluded identically — proven by five dedicated tests
(`TestQueueFilter`), including a literal `"GARBAGE_VALUE"` case. No fuzzy
interpretation anywhere in the filter.

## 4. Queue item shape

`HumanReviewItem` carries every field task §5 named: airport id/name/code,
source id/title/publisher/url/document_reference, the existing coarse
`source_reliability_level_raw` (explicitly labeled, never conflated with
`SourceAuthorityTier` — §14 below), `artifact_identity`/`source_locator`/
`raw_fragment_hash`/`raw_relevant_text`/`parser_identifier`, and all three
governed decision/reason pairs verbatim. Plus `invariant_warnings: tuple[str,
...]` (§13). No internal identifier beyond what an audit trail needs is
exposed; `source_assertion_id`/`source_id`/`airport_id` are kept for
auditability, matching the task's own explicit allowance.

## 5. Claim enrichment boundary

**`app/services/human_review_queue.py` imports nothing from
`app.acquisition`** — verified both by direct inspection and by an AST test
(`test_queue_structure_has_no_us_specific_dependency`) walking every
`Import`/`ImportFrom` node and asserting none starts with `app.acquisition`.
Claim re-derivation lives entirely in the separate
`app/services/human_review_claim_enrichment.py`, a small, explicit registry
keyed by `HumanReviewItem.parser_identifier` (the same value
`mac_granicus_extractor.PARSER_VERSION` already writes onto every governed
row it produces, persisted since Slice 1):

```python
enrich_claims(item: HumanReviewItem) -> tuple[Claim, ...] | None
```

Returns `None` — never a fabricated or guessed claim set — for any
`parser_identifier` with no registered adapter. **Re-derivation reuses
`app.acquisition.mac_granicus_extractor._fragment_from_text()`** (the same
private, pure, text-only helper `extract_candidate_fragment()` itself calls
internally after its own PDF-to-text step) directly against the
already-persisted `raw_relevant_text` — `SourceAssertion` never stores the
original PDF bytes or the full `CandidateFragment` (only `raw_relevant_text`
and a handful of other fields), so this is the only way to re-derive claims
without re-fetching the source document. This mirrors Slice 7's own
established discipline of reusing a small private helper across two closely
related modules (there:
`intelligence_review_persistence._identity_decision_from_assertion()`; here:
the same pattern one slice family over) rather than a second, drifting
reimplementation.

## 6. Ordering

`ORDER BY SourceAssertion.created_at DESC, SourceAssertion.id DESC` —
newest governed evidence first, `id` as a deterministic tiebreaker for rows
sharing an identical timestamp. `created_at` is the one always-populated,
non-nullable timestamp every `SourceAssertion` row carries (unlike
`intelligence_review_reason`/`promotion_policy_reason`, which are only set
once each respective review pass runs) — chosen over an implicit/unordered
DB scan per the task's own explicit instruction. Proven deterministic by two
dedicated tests, including an explicit same-timestamp tiebreak case.

## 7. CLI

`scripts/list_human_review_queue.py` —
`python -m scripts.list_human_review_queue --database data/runway_safe.db --limit 20`.
One function, `run_review_queue(config) -> ReviewQueueReport`, does the
actual work (schema check, then query); `main()` and every test both call
it directly — no parallel/duplicated code path between CLI output and
importable, testable behavior. `render_report()`/`render_item_report()` are
pure text-formatting functions over the same `ReviewQueueReport`/
`HumanReviewItem` data, adding no new information.

## 8. Schema gate

`check_schema_readiness()` reuses the existing migration scripts' own
read-only `inspect()` functions (Slice 4's and Slice 7's, imported directly
— never reimplemented) and refuses with `REVIEW_QUEUE_SCHEMA_MIGRATION_REQUIRED`
in `ReviewQueueReport.blockers` if either the `intelligence_review_*` or
`promotion_policy_*` columns are missing — before any ORM query runs. Proven
against three schema states built by *reusing the already-proven
`downgrade()` functions* (rather than hand-writing SQL a third time): full
schema (passes), Slice 7 columns downgraded away (blocks), and both Slice
4+7 columns downgraded away (blocks). No auto-migration — the CLI never
imports `upgrade`/`downgrade` from either migration script at all
(AST-verified, `test_module_never_imports_upgrade_or_downgrade`).

## 9. MSP golden result

Real chain (isolated DB): PDF fixture → `extract_candidate_fragment()` →
`extract_mac_claims()` → `persist_discovery_fragment()` →
`persist_intelligence_review()` → `persist_promotion_policy()` (Tier 1) →
`list_human_review_items()`: **exactly 1 item**, `promotion_policy_decision
== "HUMAN_REVIEW_REQUIRED"`. The rendered report (verified against the
actual real fixture text, reproduced verbatim in §12 of this report's own
development log) shows every section task §12 required: authoritative MAC
source, identity confirmed, intelligence material, human review required,
`1590000.00 USD — advance_deposit_purchase_order`,
`19000000.00 USD — cip_project_ceiling`, `planned_future_action` for the
2025 installation, both the sole-source and oversight relationships kept
structurally distinct, and **no fabricated `19,000,000 contract` language
anywhere** — proven directly by `test_no_fabricated_19m_contract_language`.

## 10. AUTO_ELIGIBLE exclusion

`test_auto_eligible_excluded`: a row with `promotion_policy_decision =
"AUTO_ELIGIBLE"` never appears in `list_human_review_items()`'s result.

## 11. DO_NOT_PROMOTE exclusion

`test_do_not_promote_excluded`: likewise excluded. Evidence remains fully
queryable via the underlying `SourceAssertion` table by any other means —
this queue simply does not surface it as needing human attention; nothing
is deleted or hidden at the data level.

## 12. NULL/legacy behavior

`test_null_excluded`: a `SourceAssertion` with `promotion_policy_decision
IS NULL` (the state of every row not yet run through Slice 7 — including,
today, every real row in `data/runway_safe.db`) never appears. No inference
of review need from missing data, per the task's own explicit instruction.

## 13. Invariant-warning behavior

`_invariant_warnings()` checks, for every queued row, that
`identity_guard_decision == "ATTACH_CONFIRMED"` and
`intelligence_review_decision == "REVIEW_REQUIRED"` — the two preconditions
`evaluate_promotion_policy()`'s own outcome mapping already assumes for any
`HUMAN_REVIEW_REQUIRED` result. A real violation (constructed directly in
`TestInvariantWarning`, e.g. `identity_guard_decision="ATTACH_PROVISIONAL"`)
produces a non-empty `invariant_warnings` tuple, surfaced in the rendered
report under a `!! INVARIANT WARNINGS !!` heading — never silently
normalized, never corrected, and `test_warning_does_not_modify_the_database`
confirms the underlying row is untouched.

## 14. Source-authority-gap handling

`HumanReviewItem.source_reliability_level_raw` carries `Source.reliability_level`
verbatim; the rendered report labels it explicitly:
`"reliability_level (existing coarse field, NOT a PromotionPolicy
SourceAuthorityTier)"`. `human_review_queue.py` never imports
`SourceAuthorityTier` at all (AST-verified) — the gap identified in the
design doc (§12/§15) and re-confirmed in Slices 6/7 is not solved here,
only kept from being silently conflated with the unrelated existing field.
No `Source.reliability_level` schema change, no tier persistence.

## 15. Wrong-DB/read-only safety

`build_readonly_engine()` opens the target database via SQLite's own
`mode=ro` URI (`sqlite:///file:{path}?mode=ro&uri=true`) — a **hard**
guarantee, not merely application discipline: `test_readonly_engine_refuses_writes`
proves a write attempt through this engine raises at the driver level.
`test_only_the_target_database_is_queried` builds two separate disposable
databases (`target.db`, `protected.db`), runs the full CLI path against
`target.db` only, and confirms both files' SHA-256 hashes are unchanged
afterward — `target.db` because the connection is read-only, `protected.db`
because it was never opened at all. `test_no_backup_file_created` confirms
no new file appears in the working directory after a run.

## 16. No-Signal proof

`app/services/human_review_queue.py`, `app/services/human_review_claim_enrichment.py`,
and `scripts/list_human_review_queue.py` import no name called `Signal`
anywhere (AST-verified across all three,
`test_queue_service_no_signal_import`). `test_signal_count_unchanged_across_a_full_queue_read`
proves the `Signal` table is empty before and after a full MSP-chain queue
read.

## 17. International readiness

`TestInternationalAndUnsupportedFamily`: a synthetic row with
`parser_identifier="haneda-authority-v1"` (no registered adapter) renders
without error and without crashing on the missing claim enrichment;
`enrich_claims()` correctly returns `None`; the rendered report shows "No
source-specific claim extraction available for parser_identifier=..." rather
than a fabricated claim set. `human_review_queue.py` itself contains no
`app.acquisition` import at all (§5), so this holds structurally, not merely
by the one test case exercised.

## 18. Real DB read-only result

```
BEFORE: sha256=6be9c6f16b6e84fd67ccba7da3d7ac33bfd72c8d5479ea0dca046b9560771de0
        size=1,761,280  mtime=1787086520.3335173
```

Running `python -m scripts.list_human_review_queue --database data/runway_safe.db --limit 20`
against the real database:

```
Database: C:\Runwaysafe\runway-safe-intelligence\data\runway_safe.db
BLOCKED: REVIEW_QUEUE_SCHEMA_MIGRATION_REQUIRED
schema_readiness: {'intelligence_review_decision_column_exists': False,
    'intelligence_review_reason_column_exists': False,
    'promotion_policy_decision_column_exists': False,
    'promotion_policy_reason_column_exists': False,
    'source_assertions_count': 222, 'ready': False}
```

exit code 1 — exactly the expected result, since neither the Slice 4 nor
the Slice 7 migration has been applied to the real database (confirmed by
inspection, not assumed, per this task's own explicit instruction; Slice
1's `identity_guard_*` columns remain the only additive pair present).

```
AFTER:  sha256=6be9c6f16b6e84fd67ccba7da3d7ac33bfd72c8d5479ea0dca046b9560771de0
        size=1,761,280  mtime=1787086520.3335173
```

Byte-identical to the pre-run state. No new file appeared in `data/backups/`.

## 19. Focused tests

```
python -m pytest tests/test_human_review_queue.py tests/test_promotion_policy_persistence.py \
    tests/test_promotion_policy_persistence_migration.py tests/test_promotion_policy_evaluation.py \
    tests/test_intelligence_review_persistence.py tests/test_intelligence_review_persistence_migration.py \
    tests/test_signal_candidate_evaluation.py tests/test_mac_granicus_claims.py \
    tests/test_discovery_evidence_persistence.py tests/test_model_contract.py -q
```

Result: **226 passed** (updated during the checkpoint review — see §25a; one
regression test was added; the checkpoint run also swapped in
`tests/test_capture_mac_discovery.py` in place of a narrower set, still 0
failed).

## 20. Full pytest

```
python -m pytest -q
```

Result: **1060 passed** (baseline 1019 + 41 tests in
`tests/test_human_review_queue.py`), 0 failed, 0 skipped. (Originally
reported as 1059/40 before the checkpoint review added one regression test.)

## 21. py_compile

`python -m py_compile` on all four changed/new Python files — no output,
exit 0.

## 22. git diff --check

No output, exit 0.

## 23. Exact files changed

**New:**
- `app/services/human_review_queue.py`
- `app/services/human_review_claim_enrichment.py`
- `scripts/list_human_review_queue.py`
- `tests/test_human_review_queue.py`
- `docs/architecture/human-review-queue-slice8-report.md` (this file)

No existing file was modified — confirmed via `git status --porcelain`
against `app/services/evidence_claim_semantics.py`,
`app/services/signal_candidate_evaluation.py`,
`app/services/promotion_policy_evaluation.py`,
`app/services/intelligence_review_persistence.py`,
`app/services/promotion_policy_persistence.py`,
`app/acquisition/mac_granicus_claims.py`,
`app/acquisition/mac_granicus_extractor.py`, and
`app/models/source_assertion.py` (all empty output — untouched). This slice
required no schema change, so `app/models/source_assertion.py` was read for
grounding only.

## 24. git status

```
?? app/services/human_review_queue.py
?? app/services/human_review_claim_enrichment.py
?? scripts/list_human_review_queue.py
?? tests/test_human_review_queue.py
?? docs/architecture/human-review-queue-slice8-report.md
```
plus the same pre-existing, unrelated untracked items already present at
task start. Nothing staged, nothing committed, nothing pushed.

## 25. Design corrections discovered

No defect was found in any previously-committed file. One implementation-time
self-correction, caught before it shipped: an early draft of
`scripts/list_human_review_queue.py` had two parallel code paths — a
JSON-dict-oriented `run_review_queue()` and a separately-duplicated inline
implementation inside `main()` that didn't call it. Refactored to the single
`run_review_queue() -> ReviewQueueReport` shape documented in §7 before any
test was written against it, so no test or behavior needed to change as a
result — noted here for transparency since it was a real design smell caught
during this task's own development, not merely a stylistic choice made
up-front.

## 25a. Checkpoint review correction

No defect was found in `human_review_queue.py`, `human_review_claim_enrichment.py`,
or `list_human_review_queue.py` during the commit/push checkpoint review.
The reuse of `mac_granicus_extractor._fragment_from_text()` (§5/§9's own
"critical review" item) was traced through in full: it depends only on the
already-persisted `raw_relevant_text` plus identity metadata that
`extract_mac_claims()` never reads, so re-derivation cannot silently diverge
from the original ingestion-time extraction. The existing test suite only
verified this indirectly, via a matching claim *count* (7). Strengthened
with `test_re_derived_claims_are_structurally_identical_to_original_extraction`,
which asserts full dataclass equality (`Claim` is frozen/hashable with
structural `__eq__`) between the claims re-derived from the persisted text
and the claims originally extracted directly from the real PDF at
persistence time — a direct proof, not an inference from a matching count.
No code change was needed; only the test suite grew.

## 26. Whether ready for real Slice-4/Slice-7 migration

Yes, mechanically — both migration scripts were already proven correct in
their own slices' checkpoints, and this slice's own read-only pilot run
against the real database confirms the CLI correctly identifies and reports
the exact missing-column state without touching anything. Whether/when to
actually apply either migration to `data/runway_safe.db` remains its own
separate, explicitly-approved decision (matching every prior slice's own
discipline) — not part of this task, and not performed here.

## 27. Recommended next operational slice

Not a new pure-core or persistence slice — the remaining architecture (claim
core, promotion policy, persistence, and now read-only surfacing) is
complete end-to-end. The next genuinely useful step is **operational, not
architectural**: apply the Slice 4 and Slice 7 migrations to the real
database (their own small, already-approved, backed-up operations, each
already proven idempotent and reversible), then run a real intelligence-review
+ promotion-policy pass over the 222 real, already-governed `SourceAssertion`
rows (starting with #222, the real MSP row, whose expected result —
`HUMAN_REVIEW_REQUIRED` — this report and every prior slice's own testing
already established) so the human review queue built here has real data to
surface. Only after that should any further slice (reviewer actions, Signal
promotion, the `CREATE_SIGNAL`/`PUBLISH_SIGNAL` separation, or eventual
`AUTO_ELIGIBLE` automation) be scoped — each remains its own explicitly-
authorized step, per the design doc's own roadmap.

---

`RWI_HUMAN_REVIEW_QUEUE_SLICE8_COMPLETE`
