"""MAC Granicus claim extraction adapter
(docs/architecture/mac-granicus-claim-extraction-slice2-report.md,
Slice 2 of docs/architecture/evidence-to-signal-semantics-design.md).

    CandidateFragment (already built, app.acquisition.mac_granicus_extractor)
        -> extract_mac_claims()
        -> tuple[Claim, ...] (app.services.evidence_claim_semantics, unmodified)

Consumes an ALREADY-BUILT CandidateFragment - never fetches a document,
never resolves an airport, never runs the Evidence Attachment Guard,
never persists anything, never evaluates whether a claim is Signal-
worthy. Answers only "what does this fragment's own text say," reusing
CandidateFragment's already-extracted ExtractedMoney/ExtractedDate/
runway/issuer fields wherever they exist, and applying MAC-memo-specific
structural phrase detection only for the relationship/procedural
semantics CandidateFragment has no field for at all (it carries no
vendor/relationship concept - see its own docstring).

This module is deliberately MAC-Granicus-specific (regexes match real,
observed MAC memo boilerplate - "sole source procurement with X",
"Purchase Order to X", "under the oversight of X" - never a literal
"Runway Safe" search, matching the exact discipline already established
in app/acquisition/mac_granicus_extractor.py). The OUTPUT remains fully
generic Claim objects from the already-committed, unmodified claim core
- a future Massport/SFO/international extractor produces the identical
Claim shape from its own, separately-written phrase detection.

FAIL-CLOSED (task S12): if a required piece of wording is absent or
ambiguous, the corresponding claim is simply omitted - never fabricated,
never guessed from topical proximity alone. In particular: a dollar
amount with no resolvable semantic role produces NO FinancialFact-
bearing claim for that amount (an unlabeled amount is not evidence of
any specific role); a vendor name appearing near a dollar figure with no
explicit relationship-establishing phrase produces NO RelationshipFact
tying the two together.
"""
from __future__ import annotations

import re
from decimal import Decimal

from app.services.discovery_candidate_fragment import CandidateFragment
from app.services.evidence_claim_semantics import (
    Claim,
    ClaimCategory,
    ClaimProvenance,
    FinancialFact,
    RelationshipFact,
    TemporalContext,
    TemporalQualifier,
)

__all__ = ["extract_mac_claims"]

# Normalizes this extractor's own upstream context_label vocabulary
# (app.acquisition.mac_granicus_extractor._money_context_label) to the
# claim core's canonical semantic_role vocabulary. Deliberately NOT a
# re-inference of role from raw text (task S7: "do not re-infer a
# broader financial role if context_label already carries it") - this
# is a pure, explicit, one-to-one rename table. A context_label with no
# entry here passes through unchanged (FinancialFact.semantic_role is
# free-text by design, per the claim core's own docstring) - this table
# only exists to align two already-close vocabularies, not to invent
# new ones.
_CONTEXT_LABEL_TO_SEMANTIC_ROLE: dict[str, str] = {
    "advance_deposit": "advance_deposit_purchase_order",
    "cip_project_ceiling": "cip_project_ceiling",
}

# Which conflated readings a given resolved semantic_role does NOT, by
# itself, establish (docs/architecture/evidence-to-signal-semantics-
# design.md S7's negative-constraint requirement, task S7's own hard
# requirement for the CIP-ceiling claim specifically). Deliberately a
# small, explicit, reviewed table - never computed by inference.
_NOT_ESTABLISHED_BY_ROLE: dict[str, tuple[str, ...]] = {
    "cip_project_ceiling": ("contract_value", "confirmed_vendor_award_amount", "estimated_vendor_revenue"),
}

# --- Structural phrase detection (task S3: "the source-specific parser
# understands wording; the Claim model remains source-agnostic"). Every
# pattern below matches GENERIC MAC memo boilerplate shape, never a
# literal vendor name - the same patterns would find a different vendor
# in a differently-worded MAC memo using the same template (exactly the
# discipline already proven in mac_granicus_extractor.py). ---

_LIFECYCLE_REACHED = re.compile(r"has reached its life expectancy", re.IGNORECASE)
_REPLACEMENT_REQUIRED = re.compile(r"requires replacement|needs to be replaced", re.IGNORECASE)
_FOR_ACTION_MARKER = re.compile(r"\bFOR ACTION\b")

_SOLE_SOURCE_RELATIONSHIP = re.compile(
    r"sole source\s+(?:procurement|purchase order)\s+with\s+([A-Z][\w&,.'\-\s]*?)\s+for\b", re.IGNORECASE,
)
_ADVANCE_DEPOSIT_PO_REQUEST = re.compile(
    r"Purchase Order to\s+([A-Z][\w&,.'\-\s]*?)\s+in the amount of\s*\$\s*([\d,]+(?:\.\d{2})?)", re.IGNORECASE,
)
_CIP_APPROVAL = re.compile(
    r"[Cc]ommission approved the[^.]*?in the amount of\s*\$\s*([\d,]+(?:\.\d{2})?)",
)
_PLANNED_FUTURE_BID = re.compile(
    r"(?:A separate (?:contract|installation contract)[^.]*?will be bid in\s*(\d{4}))", re.IGNORECASE,
)
_INSTALLATION_OVERSIGHT = re.compile(
    r"installation[^.]*?by a separate contractor but under the oversight of\s+([A-Z][\w&,'\-\s]*?)\s*\.", re.IGNORECASE,
)


def _provenance(fragment: CandidateFragment, excerpt: str) -> ClaimProvenance:
    return ClaimProvenance(
        artifact_identity=fragment.artifact_identity,
        source_locator=fragment.source_locator,
        fragment_hash=fragment.fragment_hash,
        raw_text_excerpt=excerpt,
    )


def _normalize_vendor(raw: str) -> str:
    """Collapses PDF line-wrap whitespace (a name can be split across a
    page-width line break, e.g. "Runway\\nSafe") into single spaces -
    cosmetic normalization only, never a content change."""
    return " ".join(raw.split()).rstrip(",.")


def _excerpt(text: str, match: "re.Match[str]", *, before: int = 0, after: int = 0) -> str:
    start = max(0, match.start() - before)
    end = min(len(text), match.end() + after)
    return text[start:end].strip()


def _memo_date(fragment: CandidateFragment):
    return next((d.normalized_date for d in fragment.dates if d.semantic_role == "memo_date"), None)


def _prior_approval_date(fragment: CandidateFragment):
    return next((d.normalized_date for d in fragment.dates if d.semantic_role == "prior_approval_date"), None)


def _resolved_financial_facts(fragment: CandidateFragment) -> dict[Decimal, FinancialFact]:
    """Groups CandidateFragment.money_values by (amount, currency) and
    resolves ONE semantic_role per group from whatever context_label(s)
    the extractor already found - task S13's own duplicate-suppression
    requirement (the real $1,590,000 amount appears twice in the MSP
    memo; only one occurrence carries a context_label). Reuses
    context_label directly (mapped through _CONTEXT_LABEL_TO_SEMANTIC_ROLE),
    never re-infers a role from raw text itself.

    Fails closed on genuine ambiguity: if a single amount is found with
    more than one DIFFERENT non-None context_label in the same fragment,
    no FinancialFact is produced for it at all (a real conflict, not a
    role this module will guess between). An amount whose every
    occurrence is unlabeled produces no FinancialFact either - an
    unlabeled number is not evidence of any specific role (task S12,
    the SFO-$40M lesson applied directly to this extractor's own logic).
    """
    labels_by_amount: dict[Decimal, set[str]] = {}
    currency_by_amount: dict[Decimal, str] = {}
    for money in fragment.money_values:
        if money.numeric_value is None:
            continue
        currency_by_amount.setdefault(money.numeric_value, money.currency or "USD")
        if money.context_label:
            labels_by_amount.setdefault(money.numeric_value, set()).add(money.context_label)

    resolved: dict[Decimal, FinancialFact] = {}
    for amount, labels in labels_by_amount.items():
        if len(labels) != 1:
            continue  # ambiguous (0 handled by setdefault never firing; >1 is a real conflict) - omit, never guess
        (context_label,) = labels
        semantic_role = _CONTEXT_LABEL_TO_SEMANTIC_ROLE.get(context_label, context_label)
        resolved[amount] = FinancialFact(
            amount=amount, currency=currency_by_amount[amount], semantic_role=semantic_role,
            not_established=_NOT_ESTABLISHED_BY_ROLE.get(semantic_role, ()),
        )
    return resolved


def _subject_for_runway_context(fragment: CandidateFragment) -> str:
    """A short, free-text scope label distinguishing which physical
    runway context claims are about - reuses CandidateFragment's already-
    extracted runway_ends/runway_pairs verbatim, never re-parses raw text
    for this. Airport identity is deliberately NOT part of this (task
    S11 - identity was already handled upstream by the Guard)."""
    if fragment.runway_pairs:
        return f"EMAS bed, runway pair {', '.join(sorted(fragment.runway_pairs))}"
    if fragment.runway_ends:
        return f"EMAS bed, runway end {', '.join(sorted(fragment.runway_ends))}"
    return "EMAS bed"


def extract_mac_claims(fragment: CandidateFragment) -> tuple[Claim, ...]:
    """Pure, deterministic: same CandidateFragment always produces the
    same tuple of Claims, in the same order. No network, no database, no
    filesystem, no current-time dependency - every temporal fact is
    anchored to a date CandidateFragment.dates already extracted from the
    cited document itself."""
    text = fragment.raw_text
    subject = _subject_for_runway_context(fragment)
    financial_by_amount = _resolved_financial_facts(fragment)
    memo_date = _memo_date(fragment)

    claims: list[Claim] = []

    # Claim A - lifecycle condition.
    match = _LIFECYCLE_REACHED.search(text)
    if match:
        claims.append(Claim(
            category=ClaimCategory.EXPLICIT_DOCUMENT_FACT, subject=subject,
            statement="The EMAS bed has reached its life expectancy.",
            provenance=_provenance(fragment, _excerpt(text, match, before=40, after=10)),
        ))

    # Claim B - required action.
    match = _REPLACEMENT_REQUIRED.search(text)
    if match:
        claims.append(Claim(
            category=ClaimCategory.EXPLICIT_DOCUMENT_FACT, subject=subject,
            statement="EMAS replacement is required.",
            provenance=_provenance(fragment, _excerpt(text, match, before=40, after=10)),
        ))

    # Claim C - sole-source vendor relationship (requested, not yet
    # approved - the memo's own "Recommend that the full Commission
    # authorize..." framing, never read as a confirmed award).
    match = _SOLE_SOURCE_RELATIONSHIP.search(text)
    if match:
        vendor = _normalize_vendor(match.group(1))
        claims.append(Claim(
            category=ClaimCategory.RELATIONSHIP, subject=f"EMAS material procurement, {subject}",
            statement=f"{vendor} is named as the requested sole-source vendor for this procurement.",
            provenance=_provenance(fragment, _excerpt(text, match, before=20, after=60)),
            relationship=RelationshipFact(
                party=vendor, role="requested_sole_source_vendor",
                scope="staff recommendation pending Commission authorization, not a confirmed award",
            ),
        ))

    # Claim D - procedural request for the advance-deposit Purchase
    # Order, with its financial fact attached (task's own claim E is
    # this attachment, not a separate Claim - see the report).
    match = _ADVANCE_DEPOSIT_PO_REQUEST.search(text)
    if match:
        vendor = _normalize_vendor(match.group(1))
        amount = Decimal(match.group(2).replace(",", ""))
        financial = financial_by_amount.get(amount)
        is_for_action = bool(_FOR_ACTION_MARKER.search(text))
        claims.append(Claim(
            category=ClaimCategory.PROCEDURAL_REQUEST, subject=f"advance-deposit Purchase Order, {subject}",
            statement=f"Staff requests authority to issue a Purchase Order to {vendor} for the advance deposit.",
            provenance=_provenance(fragment, _excerpt(text, match, before=20, after=40)),
            financial=financial,
            temporal=TemporalContext(
                qualifier=TemporalQualifier.REQUESTED_PENDING_APPROVAL, as_of_date=memo_date,
                detail="marked FOR ACTION - a staff request, not a recorded Commission decision" if is_for_action else None,
            ),
        ))

    # Claim F - the historical, already-approved CIP ceiling fact. The
    # historical action itself is true regardless of whether the dollar
    # figure's semantic role happens to resolve (see claim D, which has
    # the same financial=None-is-acceptable shape) - a failed/ambiguous
    # financial resolution must never silently delete an otherwise-true
    # EXPLICIT_DOCUMENT_FACT, only withhold the unresolved amount.
    match = _CIP_APPROVAL.search(text)
    if match:
        amount = Decimal(match.group(1).replace(",", ""))
        financial = financial_by_amount.get(amount)
        claims.append(Claim(
            category=ClaimCategory.EXPLICIT_DOCUMENT_FACT, subject=f"CIP project, {subject}",
            statement="The Commission approved a CIP ceiling for the project.",
            provenance=_provenance(fragment, _excerpt(text, match, before=20, after=10)),
            financial=financial,
            temporal=TemporalContext(qualifier=TemporalQualifier.HISTORICAL_FACT, as_of_date=_prior_approval_date(fragment)),
        ))

    # Claim H - planned future installation contract.
    match = _PLANNED_FUTURE_BID.search(text)
    if match:
        target_year = match.group(1)
        claims.append(Claim(
            category=ClaimCategory.TEMPORAL_STATEMENT, subject=f"installation contract, {subject}",
            statement=f"A separate installation contract is planned to be bid in {target_year}.",
            provenance=_provenance(fragment, _excerpt(text, match, before=10, after=10)),
            temporal=TemporalContext(
                qualifier=TemporalQualifier.PLANNED_FUTURE_ACTION, as_of_date=memo_date,
                detail=f"target year {target_year}",
            ),
        ))

    # Claim I - installation oversight relationship, explicitly distinct
    # from an installation-contractor award.
    match = _INSTALLATION_OVERSIGHT.search(text)
    if match:
        vendor = _normalize_vendor(match.group(1))
        claims.append(Claim(
            category=ClaimCategory.RELATIONSHIP, subject=f"installation, {subject}",
            statement=f"Installation is described as being under {vendor}'s oversight, performed by a separate contractor.",
            provenance=_provenance(fragment, _excerpt(text, match, before=40, after=10)),
            relationship=RelationshipFact(
                party=vendor, role="installation_oversight",
                scope="not the installation contractor - a separate contractor performs the installation work",
            ),
        ))

    # Deterministic duplicate suppression via the claim core's own
    # structural equality (task S13) - never an invented claim id.
    return tuple(dict.fromkeys(claims))
