"""Human review claim enrichment — optional, source-family-specific
(docs/architecture/human-review-queue-slice8-report.md, Slice 8).

    HumanReviewItem (app.services.human_review_queue, generic)
        -> enrich_claims()
        -> tuple[Claim, ...] | None
        -> (None means: no supported adapter for this row's source family -
            the reviewer reads the raw governed text directly instead;
            never a fabricated or guessed claim set)

Deliberately isolated from app.services.human_review_queue - THAT module
must stay source-agnostic (design doc's own "the review query service must
not import a source-specific extractor" boundary, task S7). This module is
the one place in Slice 8 allowed to know about a specific source family's
own extractor, and it is a small, explicit registry keyed by
`HumanReviewItem.parser_identifier` (the same value
app.acquisition.mac_granicus_extractor.PARSER_VERSION already writes onto
every governed row it produces, via CandidateFragment.parser_identifier ->
SourceAssertion.parser_identifier, already persisted since Slice 1) - adding
a future source family means adding one more registry entry, never
modifying app.services.human_review_queue.

RE-DERIVATION, NOT PERSISTENCE: SourceAssertion never persists a full
CandidateFragment (only a handful of its fields, per
docs/architecture/ai-discovery-candidate-envelope-lifecycle.md's own "claims
are pure and re-derivable, never persisted" principle) - in particular, it
does not persist `money_values`/`dates`/`terminology_hits`, and the original
PDF bytes are never stored on the row either. Re-derivation here therefore
reuses `app.acquisition.mac_granicus_extractor._fragment_from_text()` - the
same pure, text-only (no PDF, no network, no filesystem) helper
`extract_candidate_fragment()` itself calls internally after its own
pdfplumber PDF-to-text step - directly against the already-persisted,
already-governed `raw_relevant_text`. This is a deliberate reuse of a
private helper across two closely related modules in the same slice family,
exactly like Slice 7's own reuse of
`intelligence_review_persistence._identity_decision_from_assertion()` - a
single source of truth for "how do MAC memo claims get derived from text,"
never a second, drifting reimplementation.

FAIL CLOSED, GRACEFULLY: an unsupported `parser_identifier`, missing raw
text, or text that no longer parses as relevant (should not happen for
already-governed rows, but never assumed) all return `None` - never an
empty tuple pretending to be "no claims found," and never a guessed
claim built from an unsupported format.
"""
from __future__ import annotations

from typing import Callable

from app.acquisition.mac_granicus_claims import extract_mac_claims
from app.acquisition.mac_granicus_extractor import PARSER_VERSION as _MAC_GRANICUS_PARSER_VERSION
from app.acquisition.mac_granicus_extractor import _fragment_from_text as _mac_granicus_fragment_from_text
from app.services.evidence_claim_semantics import Claim
from app.services.human_review_queue import HumanReviewItem

__all__ = ["enrich_claims"]


def _enrich_mac_granicus(item: HumanReviewItem) -> "tuple[Claim, ...] | None":
    if not item.raw_relevant_text or not item.artifact_identity or not item.source_locator:
        return None
    result = _mac_granicus_fragment_from_text(
        item.raw_relevant_text, artifact_identity=item.artifact_identity, source_locator=item.source_locator,
    )
    if result is None:
        return None
    fragment, _vendors = result
    return extract_mac_claims(fragment)


# Keyed by the exact parser_identifier value each source-specific extractor
# already writes onto governed rows - a data-driven dispatch, never a
# hardcoded "if source family looks like X" guess.
_ADAPTERS: "dict[str, Callable[[HumanReviewItem], tuple[Claim, ...] | None]]" = {
    _MAC_GRANICUS_PARSER_VERSION: _enrich_mac_granicus,
}


def enrich_claims(item: HumanReviewItem) -> "tuple[Claim, ...] | None":
    """Returns the re-derived claims for `item` if a source-specific adapter
    is registered for its `parser_identifier`, else `None` - never raises,
    never fabricates a claim set for an unsupported or unrecognized source
    family."""
    adapter = _ADAPTERS.get(item.parser_identifier or "")
    if adapter is None:
        return None
    return adapter(item)
