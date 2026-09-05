"""Lightweight-path eligibility guard for known-Airport-staged funding
evidence (RWI HQ "Funding Human Review Gate - Slice B", following the
recon mission "Funding Human Review & Promotion Contract Recon").

    SourceAssertion (assertion_type="project_construction", staged via
        app.services.known_airport_evidence_persistence)
        -> check_lightweight_funding_path_eligibility()
        -> pass, or NotLightweightFundingAssertionError

Answers exactly one narrow, read-only question: "does this SourceAssertion
have the EXACT field shape app.services.known_airport_evidence_persistence's
own apply_ function guarantees for assertion_type='project_construction'
(see that module's own 'FIELD SEMANTICS' docstring section) - i.e. is it
SAFE to admit into the lightweight funding human-review path, as opposed to
a heavy Discovery Engine row (which has real identity_guard_decision/
intelligence_review_decision/promotion_policy_decision state, or a real
evidence_quality other than the KAR/stage-only default) or a legacy,
pre-EvidenceBag-pipeline row (airport_id set, identity_guard_decision NULL,
but NOT otherwise shaped like a KAR row - see
app.services.source_assertion_legacy_identity_attestation's own existence as
proof such rows are real)."

WHY assertion_type == "project_construction" ALONE IS NOT SUFFICIENT (the
recon mission's own explicit warning, confirmed empirically against the real
production database before this module was written): a live read-only query
against data/runway_safe.db found 26 existing SourceAssertion rows with
assertion_type="project_construction" AND airport_id IS NOT NULL AND
identity_guard_decision IS NULL - a real, pre-existing, non-hypothetical
population that assertion_type + airport_id + identity_guard_decision alone
cannot distinguish from a genuine known-Airport-staged funding row. All 26 of
those rows were excluded by the FULL conjunction below: 25 have
evidence_quality="direct_strong" (never "unverified_candidate", the
KAR/stage-only default) and the remaining one (id=81) has
intelligence_review_decision="REVIEW_REQUIRED" (not NULL) - i.e. every field
this guard checks did real, load-bearing discriminating work against actual
data, not merely a defensive-but-untested list. Zero rows in the real
database satisfy every condition below with assertion_type="project_construction"
today; the 11 rows that DO satisfy every condition except assertion_type are
all assertion_type="airport_inventory" (SourceAssertion 244-254, Missions
#26D/#26E's own coordinate-evidence acceptance batch) - direct, real proof
the same seven-field conjunction already correctly separates KAR's two
existing use cases from each other.

This is a READ-ONLY, PURE function - still no session parameter, still no
query, still no write of any kind, and it still never touches
`source_assertion.source` or any other relationship itself. As of this
hardening it additionally requires the caller to pass `source_external_id`
explicitly - the caller already holds both `session` and `source_assertion`
(see app.services.known_airport_funding_reviewer_action /
known_airport_funding_signal_creation, the only two callers), so resolving
`source_assertion.source.external_id` there, exactly like every other
relationship access already made throughout this pipeline, is not a new
category of side effect; this function itself remains a pure comparison
over values already handed to it, matching this module's own "smallest
truthful API change" mandate rather than silently lazy-loading a
relationship inside a function whose docstring promises it never will.

RESIDUAL RISK - NOW PARTIALLY CLOSED (RWI HQ "Lightweight Funding Eligibility
Hardening", following the recon mission "Commander Lightweight Funding
Review CLI"): the paragraph above described a real, since-confirmed gap -
a live, read-only run of this exact function against the real production
database found that SourceAssertion 258 (Research Loop / Discovery
candidate evidence, persisted via
scripts.persist_selected_fragments's own --known-airport-id mode with
assertion_type="project_construction") passed every one of the
SourceAssertion-shape checks below, because
app.services.known_airport_evidence_persistence is a genuinely generic
seam shared by both funding importers and Selection/KEEP-derived
known-airport evidence - the two produce byte-identical SourceAssertion
field shapes. This module now ALSO requires the associated Source's own
`external_id` to carry one of the two REAL, already-evidenced funding
provenance namespaces this repository's own funding importers write
(scripts.import_faa_aip_grants's `"faa_aip:"` prefix,
scripts.import_usaspending_grants's `"usaspending:"` prefix) - explicitly
excluding the `"discovery:"` namespace
app.services.known_airport_evidence_persistence/
selection_source_metadata.build_discovery_source_metadata_for_snapshot
always uses. No new schema field was needed: `Source.external_id` already
exists and is already populated correctly by every real writer - this
closes the gap by finally reading a signal that was always there, not by
adding one. The residual risk this module explicitly accepted for the
SourceAssertion-shape checks alone (a hypothetical future code path
reproducing that exact shape) is narrowed, not eliminated, by the same
reasoning one level up: a hypothetical future writer that reused an
existing `"faa_aip:"`/`"usaspending:"` Source row for non-funding evidence
would still be admitted. No such writer exists today.
"""
from __future__ import annotations

__all__ = [
    "NotLightweightFundingAssertionError",
    "check_lightweight_funding_path_eligibility",
    "FUNDING_SOURCE_NAMESPACE_PREFIXES",
]

# The one assertion_type this lightweight path ever admits. Extending this
# would be a separate, explicit, future decision - never inferred here.
LIGHTWEIGHT_FUNDING_ASSERTION_TYPE = "project_construction"

# The only two REAL, already-evidenced funding-source provenance namespaces
# in this repository (RWI HQ "Lightweight Funding Eligibility Hardening"):
#   "faa_aip:"      - scripts.import_faa_aip_grants._external_id_for()
#   "usaspending:"  - scripts.import_usaspending_grants (external_id = f"usaspending:{grant.external_id}")
# Deliberately NOT including "discovery:" (app.services.known_airport_evidence_persistence's
# own Selection/KEEP-derived namespace, via
# app.services.selection_source_metadata.build_discovery_source_metadata_for_snapshot) -
# that is exactly the namespace the real, confirmed SA258 defect used to slip
# through. Case-sensitive, exact prefix match - neither writer normalizes
# case, so no normalization is invented here either. Extending this tuple to
# a third namespace is a separate, explicit, future decision requiring the
# same kind of real, evidenced justification these two already have - never
# inferred or broadened speculatively.
FUNDING_SOURCE_NAMESPACE_PREFIXES = ("faa_aip:", "usaspending:")

# The exact evidence_quality/review_state values
# app.services.known_airport_evidence_persistence.apply_known_airport_evidence_persistence()
# always writes, verbatim - see that module's own "FIELD SEMANTICS" docstring
# section.
_EXPECTED_EVIDENCE_QUALITY = "unverified_candidate"
_EXPECTED_REVIEW_STATE = "unreviewed"


class NotLightweightFundingAssertionError(ValueError):
    """Raised when a SourceAssertion does not have the exact field shape
    the known-Airport funding staging seam guarantees. Carries the specific
    field/expected/actual triple that first failed - never partially
    reports, matching
    app.services.source_assertion_legacy_identity_attestation.check_legacy_attestation_eligibility()'s
    own "raises the first violated precondition" convention."""

    def __init__(self, source_assertion_id: "int | None", *, field: str, expected: object, actual: object) -> None:
        self.source_assertion_id = source_assertion_id
        self.field = field
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"SourceAssertion {source_assertion_id!r} is not eligible for the lightweight known-Airport "
            f"funding review path: field {field!r} must be {expected!r}, got {actual!r}."
        )


def check_lightweight_funding_path_eligibility(source_assertion, *, source_external_id: "str | None") -> None:
    """The single source of truth for "is this SourceAssertion eligible for
    the lightweight known-Airport funding review/promotion path at all" -
    both app.services.known_airport_funding_reviewer_action and
    app.services.known_airport_funding_signal_creation call this exact
    function with the exact same arguments, so the two can never
    independently disagree about eligibility (mirrors
    check_legacy_attestation_eligibility()'s own identical discipline).

    Checks the associated Source's funding-provenance namespace FIRST (RWI
    HQ "Lightweight Funding Eligibility Hardening" - see
    FUNDING_SOURCE_NAMESPACE_PREFIXES's own docstring for exactly why:
    without this check, SourceAssertion 258, real Research Loop / Discovery
    evidence, passed every check below), then every field
    app.services.known_airport_evidence_persistence.apply_known_airport_evidence_persistence()
    guarantees for assertion_type="project_construction" - see that
    module's own docstring "FIELD SEMANTICS" section, reproduced here as
    seven checks (assertion_type, airport_id, unknown_airport_candidate_id,
    evidence_quality, review_state, identity_guard_decision,
    identity_guard_reason, intelligence_review_decision,
    intelligence_review_reason, promotion_policy_decision,
    promotion_policy_reason - eleven individual field checks in total, now
    twelve with the namespace check). Raises on the FIRST violated
    condition, in the order listed.

    `source_external_id` is the caller's already-resolved
    `source_assertion.source.external_id` (see module docstring "READ-ONLY,
    PURE function" section for why this function itself never resolves the
    relationship). A missing Source, a missing/empty external_id, and any
    namespace not in FUNDING_SOURCE_NAMESPACE_PREFIXES (including
    "discovery:") are all rejected identically - fail closed, never a
    default-permits interpretation of an absent value."""
    sa_id = source_assertion.id

    if not source_external_id or not source_external_id.strip():
        raise NotLightweightFundingAssertionError(
            sa_id, field="source.external_id",
            expected=f"a non-empty value starting with one of {FUNDING_SOURCE_NAMESPACE_PREFIXES!r}",
            actual=source_external_id,
        )
    if not source_external_id.startswith(FUNDING_SOURCE_NAMESPACE_PREFIXES):
        raise NotLightweightFundingAssertionError(
            sa_id, field="source.external_id",
            expected=f"a value starting with one of {FUNDING_SOURCE_NAMESPACE_PREFIXES!r}",
            actual=source_external_id,
        )
    if source_assertion.assertion_type != LIGHTWEIGHT_FUNDING_ASSERTION_TYPE:
        raise NotLightweightFundingAssertionError(
            sa_id, field="assertion_type", expected=LIGHTWEIGHT_FUNDING_ASSERTION_TYPE,
            actual=source_assertion.assertion_type,
        )
    if source_assertion.airport_id is None:
        raise NotLightweightFundingAssertionError(sa_id, field="airport_id", expected="NOT NULL", actual=None)
    if source_assertion.unknown_airport_candidate_id is not None:
        raise NotLightweightFundingAssertionError(
            sa_id, field="unknown_airport_candidate_id", expected=None,
            actual=source_assertion.unknown_airport_candidate_id,
        )
    if source_assertion.evidence_quality != _EXPECTED_EVIDENCE_QUALITY:
        raise NotLightweightFundingAssertionError(
            sa_id, field="evidence_quality", expected=_EXPECTED_EVIDENCE_QUALITY,
            actual=source_assertion.evidence_quality,
        )
    if source_assertion.review_state != _EXPECTED_REVIEW_STATE:
        raise NotLightweightFundingAssertionError(
            sa_id, field="review_state", expected=_EXPECTED_REVIEW_STATE, actual=source_assertion.review_state,
        )
    for field in (
        "identity_guard_decision", "identity_guard_reason",
        "intelligence_review_decision", "intelligence_review_reason",
        "promotion_policy_decision", "promotion_policy_reason",
    ):
        value = getattr(source_assertion, field)
        if value is not None:
            raise NotLightweightFundingAssertionError(sa_id, field=field, expected=None, actual=value)
