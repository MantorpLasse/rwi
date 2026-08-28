"""USAspending legacy grant claim extraction adapter
(docs/architecture/rwi-usaspending-legacy-claims-extractor-design.md, the
locked design this module implements).

    HumanReviewItem (already-persisted SourceAssertion + Source fields)
        -> extract_usaspending_grant_claims()
        -> tuple[Claim, ...] (app.services.evidence_claim_semantics, unmodified)

Consumes only already-persisted, already-governed fields - never fetches a
document, never resolves an airport, never runs the identity guard, never
persists anything, never evaluates whether a claim is Signal-worthy. Answers
only "what does this SourceAssertion's own preserved grant-description text
say," mirroring app.acquisition.mac_granicus_claims's own scope exactly, but
against a structurally different source family: the legacy USAspending grant
backfill's own "PURPOSE: ... ACTIVITIES TO BE PERFORMED/EXPECTED OUTCOMES:
..." boilerplate, which carries no vendor wording, almost never a resolvable
dollar figure, and no in-text date - unlike MAC memo text, whose own
extractor reads dates/money directly out of the document. This module
therefore anchors every claim's temporal fact to the cited Source's own
already-persisted `published_date` (the grant's federal award date) rather
than re-parsing one from raw text.

SOURCE-FAMILY SCOPE (design doc S3): this adapter is intentionally scoped to
`source_type == "usaspending_grant"` rows carrying the legacy backfill
importer's own `parser_identifier`. The design's own real-data audit found
that parser_identifier alone is NOT a safe dispatch key here: the legacy
backfill's shared parser_identifier also covers `iija_grant` and
`faa_construction_report` rows, whose text shapes (terse project-title
phrases; narrative construction-status prose) are structurally different
from USAspending's own grant-description boilerplate and were never verified
against these extraction rules. Dispatch enforcement itself lives in
app.services.human_review_claim_enrichment (Phase 4) - this module's own
`extract_usaspending_grant_claims()` additionally re-checks `source_type`
itself (never trusts the caller alone) so it fails closed even if called
directly.

RSA != EMAS (hard invariant, design doc S5/S6): "runway safety area," "RSA,"
"runway reconstruction," "pavement," and "lighting" are never, by
themselves, sufficient for an EMAS claim - only an explicit "ENGINEERED
MATERIAL(S) ARRESTING SYSTEM" (both real, observed spelling variants) or the
bare "(EMAS)" parenthetical acronym immediately following that expanded
phrase produces Claim A. A grant that funds pure runway/taxiway/pavement/
lighting work with no such wording produces zero claims, never a guessed or
partial EMAS claim.

FAIL-CLOSED (design doc S5/S11, mirroring mac_granicus_claims's own
discipline exactly): missing raw_relevant_text, missing artifact_identity,
missing source_locator, the wrong source_type, or text with no explicit EMAS
wording all produce an empty claim tuple - never a fabricated or guessed
claim. Grant-phase wording (Claim B) is produced only when explicitly
present in the text; no phase is ever invented. This slice deliberately
implements NO generalized dollar-amount extraction and NO relationship
claims (design doc S9): USAspending grant-description text almost never
carries a resolvable dollar figure or any vendor/contractor wording at all,
so building either here now would require guessing, not reading.

NEVER CLAIMED: current operational status, installation, completion,
contract award, procurement result, or a "current opportunity"/lifecycle
classification. A claim built here states only that the cited, dated grant
description explicitly funds/describes an EMAS project - a permanent
historical fact about that document, never a claim about 2026 or any other
wall-clock-relative "now" (this module reads no current time anywhere).
"""
from __future__ import annotations

import re
from datetime import date

from app.services.evidence_claim_semantics import (
    Claim,
    ClaimCategory,
    ClaimProvenance,
    TemporalContext,
    TemporalQualifier,
)

__all__ = ["extract_usaspending_grant_claims"]

_EXPECTED_SOURCE_TYPE = "usaspending_grant"

# Both real, observed spelling variants (design doc S6 - verified against
# actual persisted SourceAssertion text, e.g. #84's own "ENGINEERED
# MATERIALS ARRESTING SYSTEM (EMAS)"). Deliberately requires the full
# expanded phrase - the bare acronym "EMAS" alone, with no expansion nearby,
# is never treated as sufficient (design doc S7's own "ambiguous wording"
# fail-closed case): a bare acronym could be a typo, a different program's
# initialism, or copy-paste noise this module has no way to verify.
_EMAS_PATTERN = re.compile(
    r"ENGINEERED\s+MATERIALS?\s+ARRESTING\s+SYSTEM(?:\s*\(EMAS\))?", re.IGNORECASE,
)

# RUNWAY 1 / RUNWAY 1/19 / RUNWAY 12R / RUNWAY 12R/30L - an optional single
# letter suffix (L/R/C) on either end, an optional "/END" pairing. Used only
# to label a claim's `subject`/excerpt - never to gate whether a claim is
# produced at all, and never inferred from anywhere but this exact text.
_RUNWAY_PATTERN = re.compile(r"RUNWAY\s+\d{1,2}[LRC]?(?:/\d{1,2}[LRC]?)?", re.IGNORECASE)

# "THIS GRANT FUNDS THE SECOND PHASE" / "... THE FINAL PHASE" / "... PHASE 2"
# - captures only the phase's own wording, verbatim, never a total-project
# budget or a phase number invented from context.
_GRANT_PHASE_PATTERN = re.compile(r"THIS GRANT FUNDS (?:THE\s+)?([A-Z0-9 ]+?PHASE)\b", re.IGNORECASE)


def _excerpt(text: str, match: "re.Match[str]", *, before: int = 0, after: int = 0) -> str:
    start = max(0, match.start() - before)
    end = min(len(text), match.end() + after)
    return text[start:end].strip()


def _runway_subject_fragment(text: str) -> str:
    match = _RUNWAY_PATTERN.search(text)
    return match.group(0).upper() if match else "runway unspecified"


def _provenance(
    *, artifact_identity: str, source_locator: str, fragment_hash: "str | None", excerpt: str,
) -> ClaimProvenance:
    return ClaimProvenance(
        artifact_identity=artifact_identity, source_locator=source_locator,
        fragment_hash=fragment_hash or "", raw_text_excerpt=excerpt,
    )


def extract_usaspending_grant_claims(
    *,
    source_type: "str | None",
    raw_relevant_text: "str | None",
    artifact_identity: "str | None",
    source_locator: "str | None",
    raw_fragment_hash: "str | None",
    published_date: "date | None",
) -> "tuple[Claim, ...]":
    """Pure, deterministic: the same inputs always produce the same tuple of
    Claims, in the same order. No network, no database, no filesystem, no
    current-time dependency - every temporal fact is anchored to
    `published_date`, the cited Source's own already-persisted date, never
    `datetime.now()`/`date.today()`.

    Fails closed (returns an empty tuple) when: `source_type` is not exactly
    "usaspending_grant"; `raw_relevant_text`, `artifact_identity`, or
    `source_locator` is missing/blank; or the text contains no explicit EMAS
    wording. Never raises for any of these - an unsupported or malformed
    input is simply "no claims," matching
    app.acquisition.mac_granicus_claims's own fail-closed discipline.
    """
    if source_type != _EXPECTED_SOURCE_TYPE:
        return ()
    if not raw_relevant_text or not raw_relevant_text.strip():
        return ()
    if not artifact_identity or not source_locator:
        return ()

    text = raw_relevant_text
    emas_match = _EMAS_PATTERN.search(text)
    if emas_match is None:
        return ()

    runway_fragment = _runway_subject_fragment(text)
    claims: list[Claim] = []

    # Claim A - explicit EMAS project fact.
    claims.append(Claim(
        category=ClaimCategory.EXPLICIT_DOCUMENT_FACT,
        subject=f"EMAS project, {runway_fragment}",
        statement=(
            "The cited federal grant explicitly funds/describes an Engineered Materials "
            "Arresting System (EMAS) project."
        ),
        provenance=_provenance(
            artifact_identity=artifact_identity, source_locator=source_locator,
            fragment_hash=raw_fragment_hash, excerpt=_excerpt(text, emas_match, before=60, after=10),
        ),
        temporal=TemporalContext(
            qualifier=TemporalQualifier.HISTORICAL_FACT, as_of_date=published_date,
            detail="grant description date, not a current-status claim",
        ),
    ))

    # Claim B - grant-phase funding obligation, only when explicitly present.
    phase_match = _GRANT_PHASE_PATTERN.search(text)
    if phase_match is not None:
        phase_wording = phase_match.group(1).strip().lower()
        claims.append(Claim(
            category=ClaimCategory.EXPLICIT_DOCUMENT_FACT,
            subject=f"federal grant funding obligation, {runway_fragment}",
            statement=f"Federal grant funding was obligated for {phase_wording} of this project.",
            provenance=_provenance(
                artifact_identity=artifact_identity, source_locator=source_locator,
                fragment_hash=raw_fragment_hash, excerpt=_excerpt(text, phase_match, before=5, after=5),
            ),
            temporal=TemporalContext(qualifier=TemporalQualifier.HISTORICAL_FACT, as_of_date=published_date),
        ))

    # Deterministic duplicate suppression via the claim core's own
    # structural equality, matching mac_granicus_claims's own convention.
    return tuple(dict.fromkeys(claims))
