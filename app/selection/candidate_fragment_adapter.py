"""Human-KEEP-gated FragmentSelection -> CandidateFragment adapter (RWI
Mission #14B).

Pure, deterministic: no database, no network, no persistence import (no
Source/SourceAssertion write path is imported or callable from here), and
deliberately NO IdentityGuard import either (Mission #14B Part O) - see
app.selection.identity_guard_demo for the separate, optional,
read-only evaluation layer.

CANDIDATEFRAGMENT here means exactly what
app.services.discovery_candidate_fragment.CandidateFragment's own
docstring already says: a runtime extraction result, never accepted
evidence, never a confirmed airport, never a confirmed claim. This
adapter does not enforce the human KEEP decision itself - that gate is
app.selection.review.apply_keep_decisions(), the only place in this
package that constructs a CandidateFragment from an entire
DocumentSelection. This function may still be called directly (e.g. by
tests) for exactly one already-approved FragmentSelection.

No document_title/url/publication_date is ever fabricated: they are
included only if the caller supplies real, independently-known upstream
metadata. As of Mission #14B, neither Snapshot, AcquisitionRun,
ExtractedDocument, nor FragmentSelection carries a document title
anywhere in the current pipeline (Mission #12B deliberately excluded PDF
metadata as untrusted) - so in practice, called from the real pipeline
today, `document_title` is always None. This is stated honestly rather
than worked around.
"""

from __future__ import annotations

from app.selection.fragment_selection import FragmentSelection
from app.selection.structured_extraction import extract_identity
from app.services.discovery_candidate_fragment import CandidateFragment

ADAPTER_VERSION = "0.1"


def build_candidate_fragment(
    selection: FragmentSelection,
    *,
    document_title: str | None = None,
    url: str | None = None,
) -> CandidateFragment:
    """artifact_identity/source_locator/raw_text map directly and exactly
    from `selection` (Mission #14B Part H). airport_names/
    airport_identifiers come ONLY from independent, literal extraction
    over the fragment text (and document title, if supplied) - never
    from SelectionReason, never from any AirportIdentityContext the
    selection run may have used. Every other CandidateFragment
    structured field (issuers/locations/runway_ends/runway_pairs/
    contradicting_*/alternate_airport_*/project_identifiers/
    contract_identifiers/money_values/dates/terminology_hits/language)
    is left at CandidateFragment's own default (empty) - never populated
    merely because the dataclass has room for it.
    """
    identity = extract_identity(fragment_text=selection.text, document_title=document_title)
    return CandidateFragment(
        artifact_identity=selection.document_identity,
        source_locator=f"page:{selection.page_number};chars:{selection.start_offset}-{selection.end_offset}",
        raw_text=selection.text,
        airport_names=identity.airport_names,
        airport_identifiers=identity.airport_identifiers,
        document_title=document_title,
        url=url,
        parser_identifier=f"fragment-selection-adapter/{ADAPTER_VERSION}",
    )
