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

DISPATCH SAFETY - (source_type, parser_identifier), NOT parser_identifier
ALONE (docs/architecture/rwi-usaspending-legacy-claims-extractor-design.md
S3/S4): the legacy USAspending governance work's own real-data audit found
that `parser_identifier` is not always source-family-unique - the legacy
backfill importer wrote the SAME `parser_identifier` ("legacy-source-
backfill-v1") onto `usaspending_grant`, `iija_grant`, and
`faa_construction_report` rows alike, even though their text shapes are
structurally different and were never verified against the USAspending
adapter's own extraction rules. Registering the USAspending adapter under
`parser_identifier` alone would therefore have silently misrouted 5 real
`iija_grant`/`faa_construction_report` rows into an extractor never designed
for their text.

Every existing single-parser-identifier registration (MAC/Granicus's own
`PARSER_VERSION`, already unique to that one source family) is preserved
EXACTLY as before - `_PARSER_ONLY_ADAPTERS` is untouched in shape and
behavior. Only a NEW, additive, MORE SPECIFIC registry
(`_SOURCE_TYPE_SCOPED_ADAPTERS`, keyed by the `(source_type,
parser_identifier)` pair) is introduced, checked first so a composite match
takes precedence, but never required for - and never able to change the
outcome for - a row whose parser_identifier already unambiguously identifies
its own source family.
"""
from __future__ import annotations

from typing import Callable

from app.acquisition.mac_granicus_claims import extract_mac_claims
from app.acquisition.mac_granicus_extractor import PARSER_VERSION as _MAC_GRANICUS_PARSER_VERSION
from app.acquisition.mac_granicus_extractor import _fragment_from_text as _mac_granicus_fragment_from_text
from app.acquisition.usaspending_grant_claims import extract_usaspending_grant_claims
from app.services.evidence_claim_semantics import Claim
from app.services.human_review_queue import HumanReviewItem

__all__ = ["enrich_claims"]

# The legacy backfill importer's own shared parser_identifier - carried here
# (not imported from a migration/import script, which is acquisition-time-
# only tooling) purely as a documented, testable constant for the composite
# registry key below. Not itself claimed to be USAspending-specific; the
# composite key is what actually enforces the source_type scope.
_LEGACY_SOURCE_BACKFILL_PARSER_VERSION = "legacy-source-backfill-v1"
_USASPENDING_SOURCE_TYPE = "usaspending_grant"


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


def _enrich_usaspending_grant(item: HumanReviewItem) -> "tuple[Claim, ...] | None":
    claims = extract_usaspending_grant_claims(
        source_type=item.source_type,
        raw_relevant_text=item.raw_relevant_text,
        artifact_identity=item.artifact_identity,
        source_locator=item.source_locator,
        raw_fragment_hash=item.raw_fragment_hash,
        published_date=item.source_published_date,
    )
    return claims or None


# Keyed by the exact parser_identifier value each source-specific extractor
# already writes onto governed rows, when that value is already unique to
# one source family - a data-driven dispatch, never a hardcoded "if source
# family looks like X" guess. Unchanged since before this slice.
_PARSER_ONLY_ADAPTERS: "dict[str, Callable[[HumanReviewItem], tuple[Claim, ...] | None]]" = {
    _MAC_GRANICUS_PARSER_VERSION: _enrich_mac_granicus,
}

# Keyed by (source_type, parser_identifier) - required whenever
# parser_identifier alone is not source-family-unique (see module docstring
# "DISPATCH SAFETY" above). Checked first; a composite match here always
# takes precedence over a parser_identifier-only match for the SAME
# parser_identifier value, but no parser_identifier is currently registered
# in both maps at once.
_SOURCE_TYPE_SCOPED_ADAPTERS: "dict[tuple[str, str], Callable[[HumanReviewItem], tuple[Claim, ...] | None]]" = {
    (_USASPENDING_SOURCE_TYPE, _LEGACY_SOURCE_BACKFILL_PARSER_VERSION): _enrich_usaspending_grant,
}


def enrich_claims(item: HumanReviewItem) -> "tuple[Claim, ...] | None":
    """Returns the re-derived claims for `item` if a source-specific adapter
    is registered for its (source_type, parser_identifier) pair or, failing
    that, its parser_identifier alone, else `None` - never raises, never
    fabricates a claim set for an unsupported or unrecognized source
    family."""
    composite_adapter = _SOURCE_TYPE_SCOPED_ADAPTERS.get((item.source_type or "", item.parser_identifier or ""))
    if composite_adapter is not None:
        return composite_adapter(item)
    adapter = _PARSER_ONLY_ADAPTERS.get(item.parser_identifier or "")
    if adapter is None:
        return None
    return adapter(item)
