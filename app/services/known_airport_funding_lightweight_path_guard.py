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

This is a READ-ONLY, PURE function - no session parameter, no query, no
write of any kind. Every field it inspects lives directly on the
SourceAssertion instance already in hand; no relationship needs loading.

RESIDUAL RISK (documented, not solved): like every other field-shape gate in
this pipeline (see app.services.effective_identity_guard_decision's own
"only reachable via direct DB corruption/bypass" caveats), a row manufactured
by a HYPOTHETICAL future code path that happened to reproduce this exact
field shape without actually being known-Airport-staged funding evidence
would be admitted. No in-schema marker distinguishes "created by
known_airport_evidence_persistence.py" from "any other code that wrote the
same field values" - inventing one would be a new schema field, which this
mission's own governing instruction requires separate HQ design approval to
add. This module accepts that residual risk explicitly rather than papering
over it, exactly as EB5 already does for its own analogous edge cases.
"""
from __future__ import annotations

__all__ = [
    "NotLightweightFundingAssertionError",
    "check_lightweight_funding_path_eligibility",
]

# The one assertion_type this lightweight path ever admits. Extending this
# would be a separate, explicit, future decision - never inferred here.
LIGHTWEIGHT_FUNDING_ASSERTION_TYPE = "project_construction"

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


def check_lightweight_funding_path_eligibility(source_assertion) -> None:
    """The single source of truth for "is this SourceAssertion eligible for
    the lightweight known-Airport funding review/promotion path at all" -
    both app.services.known_airport_funding_reviewer_action and
    app.services.known_airport_funding_signal_creation call this exact
    function, so the two can never independently disagree about eligibility
    (mirrors check_legacy_attestation_eligibility()'s own identical
    discipline).

    Checks every field
    app.services.known_airport_evidence_persistence.apply_known_airport_evidence_persistence()
    guarantees for assertion_type="project_construction" - see that
    module's own docstring "FIELD SEMANTICS" section, reproduced here as
    seven checks (assertion_type, airport_id, unknown_airport_candidate_id,
    evidence_quality, review_state, identity_guard_decision,
    identity_guard_reason, intelligence_review_decision,
    intelligence_review_reason, promotion_policy_decision,
    promotion_policy_reason - eleven individual field checks in total).
    Raises on the FIRST violated field, in the order listed."""
    sa_id = source_assertion.id

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
