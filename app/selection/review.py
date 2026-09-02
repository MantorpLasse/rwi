"""Human KEEP/INSPECT reviewer workflow for FragmentSelection (RWI
Mission #14B Part D).

KEEP means only "continue processing this fragment" - it never means
evidence accepted, airport confirmed, EMAS confirmed, project confirmed,
claim accepted, or SourceAssertion approved. SKIP means no
CandidateFragment is created for that fragment at all.

Decisions are NOT persisted anywhere in this mission - runtime-only,
exactly like FragmentSelection/DocumentSelection themselves (Mission #14B
Part I: after this runtime review ends, the reason a human kept a given
fragment is not durably recorded anywhere - an acknowledged, deliberate
V1 limitation, not solved here).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.selection.candidate_fragment_adapter import build_candidate_fragment
from app.selection.fragment_selection import DocumentSelection, FragmentSelection
from app.services.discovery_candidate_fragment import CandidateFragment


class ReviewDecision(str, Enum):
    KEEP = "KEEP"
    SKIP = "SKIP"


@dataclass(frozen=True)
class FragmentReview:
    """One reviewer decision for one FragmentSelection. `candidate_fragment`
    is populated only when `decision` is KEEP - a FragmentSelection never
    becomes a CandidateFragment merely because Selection produced it."""

    fragment: FragmentSelection
    decision: ReviewDecision
    candidate_fragment: CandidateFragment | None = None


def apply_keep_decisions(
    selection: DocumentSelection,
    *,
    keep_indices: frozenset[int],
    document_title: str | None = None,
    url: str | None = None,
) -> tuple[FragmentReview, ...]:
    """`keep_indices` are 1-based positions into `selection.fragments`,
    matching what a reviewer sees displayed (position 1 = first fragment
    shown). Every fragment is accounted for in the result - one
    FragmentReview per FragmentSelection, in the same order - so a caller
    can always see exactly what was skipped as well as what was kept,
    never a silently-shortened list.
    """
    reviews: list[FragmentReview] = []
    for index, fragment in enumerate(selection.fragments, start=1):
        if index in keep_indices:
            candidate = build_candidate_fragment(fragment, document_title=document_title, url=url)
            reviews.append(FragmentReview(fragment=fragment, decision=ReviewDecision.KEEP, candidate_fragment=candidate))
        else:
            reviews.append(FragmentReview(fragment=fragment, decision=ReviewDecision.SKIP, candidate_fragment=None))
    return tuple(reviews)
