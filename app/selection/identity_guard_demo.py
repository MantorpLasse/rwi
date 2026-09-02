"""Optional, read-only IdentityGuard evaluation for a CandidateFragment
(RWI Mission #14B Part C/J) - demonstration/testing only.

Deliberately kept separate from app.selection.candidate_fragment_adapter
(Mission #14B Part O: "IdentityGuard may be imported only in the optional
evaluation layer/CLI, not in the pure extraction function"). Nothing in
app.selection.structured_extraction or app.selection.candidate_fragment_adapter
imports this module or anything it depends on.

Uses the EXISTING, UNMODIFIED app.services.evidence_attachment_guard
machinery only - never weakened, never bypassed. Evaluates and returns a
real decision; never persists one anywhere.
"""

from __future__ import annotations

from app.services.discovery_candidate_fragment import CandidateFragment, candidate_fragment_to_evidence_bag
from app.services.evidence_attachment_guard import AttachmentDecision, CandidateAirport, evaluate_attachment_for_candidates


def evaluate_candidate_fragment_identity(
    fragment: CandidateFragment, candidates: list[CandidateAirport]
) -> "dict[object, AttachmentDecision]":
    """Pure, read-only, no I/O: builds an EvidenceBag from `fragment` via
    the existing, unmodified candidate_fragment_to_evidence_bag(), then
    evaluates it against `candidates` via the existing, unmodified
    evaluate_attachment_for_candidates() - both imported, never
    reimplemented or altered. Returns the real AttachmentDecision per
    candidate id; the caller decides what, if anything, to do with it -
    this function itself never persists, never mutates, never acts on
    the result."""
    bag = candidate_fragment_to_evidence_bag(fragment)
    return evaluate_attachment_for_candidates(bag, candidates)
