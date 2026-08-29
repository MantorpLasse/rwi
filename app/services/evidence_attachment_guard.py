"""AI-discovery evidence-attachment guard (docs/architecture/ai-discovery-
evidence-attachment-guard.md, core-implementation slice - see
docs/architecture/ai-discovery-evidence-attachment-guard-core-report.md).

Answers exactly one question, for one evidence fragment and one candidate
airport: "may this fragment become airport-bound evidence for this
airport?" This module knows nothing about where a fragment came from -
web search, n8n, USAspending, a procurement crawler, a translated
foreign-language document - and performs NO database query, NO database
write, and NO network call anywhere. Every input is a plain, immutable
dataclass; every output is a plain, immutable dataclass. Extraction
(finding candidate names/codes/runway tokens in raw text, resolving which
organization issued a document, translating a foreign term) is entirely
the caller's responsibility and happens before this module is ever
called.

Reuses app.services.runway_identity (normalize_end/normalize_pair/
is_two_ended_pair_shape) for all runway-designation normalization -
deliberately never reimplemented here, matching the same rule this
module's design doc states for canonical-runway-runway-end-design.md
elsewhere in the repository.

Deliberately does NOT return REVIEW_REQUIRED from evaluate_attachment()
itself: a single candidate, evaluated alone, cannot know whether some
OTHER candidate airport would explain the same evidence equally well -
that comparison needs the full candidate set, provided by
evaluate_attachment_for_candidates() below. See the core report S6/S7 for
the full reasoning; this is a deliberate, documented refinement of the
design doc's decision-contract wording, not an invented shortcut.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.services.runway_identity import AmbiguousRunwayDesignationError, normalize_end, normalize_pair

__all__ = [
    "EvidenceCategory",
    "AttachmentOutcome",
    "CandidateAirport",
    "EvidenceBag",
    "EvidenceItem",
    "ContradictionItem",
    "AttachmentDecision",
    "evaluate_attachment",
    "evaluate_attachment_for_candidates",
    "candidate_airport_from_airport_like",
]


class EvidenceCategory(str, Enum):
    """The five kinds of identity evidence this guard understands
    (docs/architecture/ai-discovery-evidence-attachment-guard.md S4).
    Each category counts at most once per decision, regardless of how
    many individual tokens within it matched - restating one fact five
    times is still one category, not five (design doc S7 step 2 / S10)."""

    IDENTIFIER = "identifier"
    NAME = "name"
    RUNWAY_TOPOLOGY = "runway_topology"
    ISSUER = "issuer"
    LOCATION = "location"


class AttachmentOutcome(str, Enum):
    """docs/architecture/ai-discovery-evidence-attachment-guard.md S7.
    str+Enum matches the one existing typed-outcome precedent in this
    repository (app.models.acquisition.AcquisitionRunStatus) rather than
    the plain-string-constant convention used by the read-only
    classifier/planner modules - chosen here because these five values
    are exactly the kind of persisted-status-shaped concept that
    precedent exists for, even though nothing is persisted in this slice
    (docs/architecture/ai-discovery-evidence-attachment-guard.md S11)."""

    ATTACH_CONFIRMED = "ATTACH_CONFIRMED"
    ATTACH_PROVISIONAL = "ATTACH_PROVISIONAL"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REJECT_CROSS_AIRPORT = "REJECT_CROSS_AIRPORT"
    INSUFFICIENT_IDENTITY = "INSUFFICIENT_IDENTITY"


def _norm_text(value: str) -> str:
    return value.strip().casefold()


def _norm_text_set(values: frozenset[str]) -> frozenset[str]:
    return frozenset(_norm_text(v) for v in values if v and v.strip())


def _normalize_end_safe(token: str) -> str | None:
    try:
        return normalize_end(token)
    except AmbiguousRunwayDesignationError:
        return None


def _normalize_pair_safe(token: str) -> str | None:
    try:
        return normalize_pair(token)
    except AmbiguousRunwayDesignationError:
        return None


@dataclass(frozen=True)
class CandidateAirport:
    """A small, DB-free view of one airport for the guard to evaluate
    evidence against (design doc S5 "candidate airport view"). Build one
    from a live ORM Airport via candidate_airport_from_airport_like()
    below, or construct directly (e.g. in tests) - this dataclass itself
    never touches a database.

    `id` is opaque to this module - never inspected, never dereferenced,
    only threaded through to the returned decision so a caller can match
    a decision back to the airport it concerns.
    """

    id: object
    name: str
    identifiers: frozenset[str] = field(default_factory=frozenset)
    aliases: frozenset[str] = field(default_factory=frozenset)
    city_location: str | None = None
    canonical_runway_ends: frozenset[str] = field(default_factory=frozenset)
    canonical_runway_pairs: frozenset[str] = field(default_factory=frozenset)
    known_issuers: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        # Normalize once at construction so every comparison downstream is
        # cheap and never re-derives this - canonical topology is
        # re-normalized defensively even though app.models.RunwayEnd rows
        # are already stored normalized, exactly the same defensive
        # posture physical_installation_identity_linking.py already takes
        # rather than trusting caller discipline.
        object.__setattr__(self, "_identifiers_norm", _norm_text_set(self.identifiers))
        object.__setattr__(self, "_names_norm", _norm_text_set(self.aliases | {self.name}))
        object.__setattr__(
            self, "_ends_norm",
            frozenset(n for n in (_normalize_end_safe(t) for t in self.canonical_runway_ends) if n),
        )
        object.__setattr__(
            self, "_pairs_norm",
            frozenset(n for n in (_normalize_pair_safe(t) for t in self.canonical_runway_pairs) if n),
        )
        object.__setattr__(self, "_issuers_norm", _norm_text_set(self.known_issuers))
        object.__setattr__(
            self, "_location_norm", _norm_text(self.city_location) if self.city_location else None,
        )


@dataclass(frozen=True)
class EvidenceBag:
    """Normalized identity evidence extracted from one document fragment
    (design doc S4). Source-agnostic: this shape says nothing about
    whether it came from a regex, an LLM, or a human. Every field is
    optional - a fragment need not contain every evidence type.

    `identifiers`/`runway_ends`/`runway_pairs` are compared directly
    against the candidate's own topology/identifiers by this module (a
    non-matching identifier or a runway token independently confirmed
    elsewhere is self-evidently NOT the candidate's own - no external
    reference table is needed to know two different code strings are
    different). `names`/`issuers`/`locations` are positive-only against
    the *matching* candidate for the same reason resolve_airport() never
    creates identity from a recipient/organization name alone: free-text
    identity strings are not self-verifying the way a coded identifier or
    a topology token is. Confirmed contradiction on those three
    categories is therefore only ever recognized when the caller has
    already resolved it (contradicting_names/contradicting_issuers/
    contradicting_locations) - this module never guesses that a found
    name belongs to some other, unspecified airport.
    """

    identifiers: frozenset[str] = field(default_factory=frozenset)
    names: frozenset[str] = field(default_factory=frozenset)
    runway_ends: frozenset[str] = field(default_factory=frozenset)
    runway_pairs: frozenset[str] = field(default_factory=frozenset)
    issuers: frozenset[str] = field(default_factory=frozenset)
    locations: frozenset[str] = field(default_factory=frozenset)

    # Pre-classified by the caller (an extractor, a reference-table
    # lookup, a human) as known to identify a DIFFERENT, specific
    # airport than whichever candidate this bag is evaluated against.
    # Never populated by this module itself.
    contradicting_names: frozenset[str] = field(default_factory=frozenset)
    contradicting_issuers: frozenset[str] = field(default_factory=frozenset)
    contradicting_locations: frozenset[str] = field(default_factory=frozenset)

    # Optional, strongest form of runway-elsewhere corroboration: the
    # caller has already resolved WHICH other airport this evidence
    # belongs to and supplies that airport's own canonical topology, so a
    # runway token absent from the candidate's own topology can be
    # confirmed (not merely suspected) to belong elsewhere.
    alternate_airport_runway_ends: frozenset[str] = field(default_factory=frozenset)
    alternate_airport_runway_pairs: frozenset[str] = field(default_factory=frozenset)

    # Carried through into the decision's reason text only - never used
    # in the decision algorithm itself.
    document_title: str | None = None
    project_number: str | None = None
    contract_number: str | None = None
    url: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "_identifiers_norm", _norm_text_set(self.identifiers))
        object.__setattr__(self, "_names_norm", _norm_text_set(self.names))
        object.__setattr__(
            self, "_ends_norm",
            frozenset(n for n in (_normalize_end_safe(t) for t in self.runway_ends) if n),
        )
        object.__setattr__(
            self, "_pairs_norm",
            frozenset(n for n in (_normalize_pair_safe(t) for t in self.runway_pairs) if n),
        )
        object.__setattr__(self, "_issuers_norm", _norm_text_set(self.issuers))
        object.__setattr__(self, "_locations_norm", _norm_text_set(self.locations))
        object.__setattr__(self, "_contradicting_names_norm", _norm_text_set(self.contradicting_names))
        object.__setattr__(self, "_contradicting_issuers_norm", _norm_text_set(self.contradicting_issuers))
        object.__setattr__(self, "_contradicting_locations_norm", _norm_text_set(self.contradicting_locations))
        object.__setattr__(
            self, "_alt_ends_norm",
            frozenset(n for n in (_normalize_end_safe(t) for t in self.alternate_airport_runway_ends) if n),
        )
        object.__setattr__(
            self, "_alt_pairs_norm",
            frozenset(n for n in (_normalize_pair_safe(t) for t in self.alternate_airport_runway_pairs) if n),
        )


@dataclass(frozen=True)
class EvidenceItem:
    category: EvidenceCategory
    value: str
    detail: str


@dataclass(frozen=True)
class ContradictionItem:
    category: EvidenceCategory
    value: str
    detail: str


@dataclass(frozen=True)
class AttachmentDecision:
    outcome: AttachmentOutcome
    reason: str
    positive_evidence: tuple[EvidenceItem, ...] = field(default_factory=tuple)
    contradicting_evidence: tuple[ContradictionItem, ...] = field(default_factory=tuple)


def _identifier_evidence(candidate: CandidateAirport, bag: EvidenceBag) -> tuple[list[EvidenceItem], list[ContradictionItem]]:
    """UNKNOWN-vs-KNOWN-DIFFERENT identifier asymmetry (mirrors the
    existing, already-designed "absence != contradiction" topology rule
    in _topology_evidence() below, S6 rule 2 - the identical principle,
    now applied to identifiers): a stated identifier can only be
    CONTRADICTING evidence against `candidate` when `candidate` actually
    HAS at least one known identifier of its own for the token to
    genuinely differ from. When `candidate.identifiers` is empty (RWI
    simply has no canonical code on file for this airport at all - a
    data-completeness gap, not a fact about the real world), a stated
    identifier is neither a match nor a contradiction - exactly the same
    "RWI's own inventory being incomplete must never be conflated with
    'this document is about a different airport'" reasoning the design
    doc already applies to topology, now applied here for the identical
    reason. This never makes an unknown identifier positive evidence -
    only removes it from being *punished* as if it were known-wrong."""
    positive: list[EvidenceItem] = []
    contradicting: list[ContradictionItem] = []
    candidate_has_known_identifiers = bool(candidate._identifiers_norm)
    for raw in bag.identifiers:
        normalized = _norm_text(raw)
        if normalized in candidate._identifiers_norm:
            positive.append(EvidenceItem(
                EvidenceCategory.IDENTIFIER, raw, f"matches candidate identifier {raw!r}",
            ))
        elif candidate_has_known_identifiers:
            # A different, structurally valid airport-identifier-shaped
            # token found in the SAME fragment, genuinely differing from
            # one of the candidate's own KNOWN identifiers, is
            # self-evidently not the candidate's own - no external
            # reference lookup is required to know two different
            # identifier strings identify different airports (design doc
            # S5, rule 1).
            contradicting.append(ContradictionItem(
                EvidenceCategory.IDENTIFIER, raw, f"identifier {raw!r} does not match candidate {candidate.name!r}",
            ))
        # else: candidate has NO known identifier of any kind - absence,
        # never contradiction (see docstring above).
    return positive, contradicting


def _name_evidence(candidate: CandidateAirport, bag: EvidenceBag) -> tuple[list[EvidenceItem], list[ContradictionItem]]:
    positive: list[EvidenceItem] = []
    for raw in bag.names:
        if _norm_text(raw) in candidate._names_norm:
            positive.append(EvidenceItem(EvidenceCategory.NAME, raw, f"matches candidate name/alias {raw!r}"))
    contradicting = [
        ContradictionItem(EvidenceCategory.NAME, raw, f"name {raw!r} pre-classified as identifying a different airport")
        for raw in bag.contradicting_names
    ]
    return positive, contradicting


def _issuer_evidence(candidate: CandidateAirport, bag: EvidenceBag) -> tuple[list[EvidenceItem], list[ContradictionItem]]:
    positive: list[EvidenceItem] = []
    for raw in bag.issuers:
        if _norm_text(raw) in candidate._issuers_norm:
            positive.append(EvidenceItem(EvidenceCategory.ISSUER, raw, f"matches known candidate issuer/authority {raw!r}"))
    contradicting = [
        ContradictionItem(EvidenceCategory.ISSUER, raw, f"issuer {raw!r} pre-classified as belonging to a different airport")
        for raw in bag.contradicting_issuers
    ]
    return positive, contradicting


def _location_evidence(candidate: CandidateAirport, bag: EvidenceBag) -> tuple[list[EvidenceItem], list[ContradictionItem]]:
    positive: list[EvidenceItem] = []
    for raw in bag.locations:
        if candidate._location_norm is not None and _norm_text(raw) == candidate._location_norm:
            positive.append(EvidenceItem(EvidenceCategory.LOCATION, raw, f"matches candidate location {raw!r}"))
    contradicting = [
        ContradictionItem(EvidenceCategory.LOCATION, raw, f"location {raw!r} pre-classified as conflicting with candidate")
        for raw in bag.contradicting_locations
    ]
    return positive, contradicting


def _topology_evidence(candidate: CandidateAirport, bag: EvidenceBag) -> tuple[list[EvidenceItem], list[ContradictionItem]]:
    positive: list[EvidenceItem] = []
    contradicting: list[ContradictionItem] = []

    # "The same fragment separately identifies a different, specific
    # airport" (design doc S6, rule 3) - computed once, reused for every
    # non-matching runway token below, never re-derived per token.
    #
    # The identifier term below applies the identical UNKNOWN-vs-KNOWN-
    # DIFFERENT asymmetry _identifier_evidence() itself now uses (see that
    # function's own docstring): a stated identifier only counts toward
    # "another airport is named in this fragment" when `candidate` has at
    # least one known identifier of its own for the token to genuinely
    # differ from. Without this guard, a candidate with NO known
    # identifier on file (e.g. a newly-admitted international airport
    # whose codes are simply not yet entered) would have every one of its
    # OWN matching topology tokens vetoed by an unrelated, merely-unknown
    # identifier - exactly the same "RWI's own data incompleteness must
    # never be conflated with contradiction" bug this mission's own fix
    # closes, manifesting a second time in this independent computation.
    other_airport_named = bool(
        bag._contradicting_names_norm or bag._contradicting_issuers_norm or bag._contradicting_locations_norm
        or (
            bool(candidate._identifiers_norm)
            and any(_norm_text(v) not in candidate._identifiers_norm for v in bag.identifiers)
        )
    )

    for raw in bag.runway_ends:
        normalized = _normalize_end_safe(raw)
        if normalized is None:
            continue
        if normalized in candidate._ends_norm:
            positive.append(EvidenceItem(
                EvidenceCategory.RUNWAY_TOPOLOGY, raw, f"runway end {raw!r} exists in candidate's own topology",
            ))
        elif normalized in bag._alt_ends_norm or other_airport_named:
            contradicting.append(ContradictionItem(
                EvidenceCategory.RUNWAY_TOPOLOGY, raw,
                f"runway end {raw!r} is absent from candidate's topology and a different airport is identified in the same fragment",
            ))
        # else: absent from candidate's topology, nothing else identifies
        # another airport - absence only, never contradiction (S6 rule 2).

    for raw in bag.runway_pairs:
        normalized = _normalize_pair_safe(raw)
        if normalized is None:
            continue
        if normalized in candidate._pairs_norm:
            positive.append(EvidenceItem(
                EvidenceCategory.RUNWAY_TOPOLOGY, raw, f"runway pair {raw!r} exists in candidate's own topology",
            ))
        elif normalized in bag._alt_pairs_norm or other_airport_named:
            contradicting.append(ContradictionItem(
                EvidenceCategory.RUNWAY_TOPOLOGY, raw,
                f"runway pair {raw!r} is absent from candidate's topology and a different airport is identified in the same fragment",
            ))

    return positive, contradicting


def evaluate_attachment(candidate: CandidateAirport, evidence: EvidenceBag) -> AttachmentDecision:
    """Pure, deterministic, side-effect-free. Performs no I/O of any kind.

    Evaluates ONE evidence fragment against ONE candidate airport, in
    isolation - never returns AttachmentOutcome.REVIEW_REQUIRED, which by
    definition requires comparing more than one candidate (use
    evaluate_attachment_for_candidates() for that). See the core
    implementation report S6 for why this split exists.

    Algorithm (docs/architecture/ai-discovery-evidence-attachment-guard.md
    S7, refined - see the core report for the exact refinement and why):
    1. Contradiction check, unconditional, first. Any match -> REJECT_CROSS_AIRPORT.
    2. Count independent positive categories that matched.
    3. An IDENTIFIER match alone is definitive (no other real airport
       shares a given identifier) -> ATTACH_CONFIRMED.
    4. >=2 independent positive categories, none contradicted -> ATTACH_CONFIRMED.
    5. Exactly 1 positive category, not IDENTIFIER -> ATTACH_PROVISIONAL.
    6. No positive evidence, no contradiction -> INSUFFICIENT_IDENTITY.
    """
    id_pos, id_neg = _identifier_evidence(candidate, evidence)
    name_pos, name_neg = _name_evidence(candidate, evidence)
    issuer_pos, issuer_neg = _issuer_evidence(candidate, evidence)
    loc_pos, loc_neg = _location_evidence(candidate, evidence)
    topo_pos, topo_neg = _topology_evidence(candidate, evidence)

    positive = tuple(id_pos + name_pos + issuer_pos + loc_pos + topo_pos)
    contradicting = tuple(id_neg + name_neg + issuer_neg + loc_neg + topo_neg)

    if contradicting:
        found = ", ".join(f"{c.category.value}={c.value!r}" for c in contradicting)
        return AttachmentDecision(
            outcome=AttachmentOutcome.REJECT_CROSS_AIRPORT,
            reason=(
                f"Contradicting identity evidence found against candidate {candidate.name!r}: {found}. "
                "Contradiction always vetoes positive evidence, regardless of topical overlap."
            ),
            positive_evidence=positive,
            contradicting_evidence=contradicting,
        )

    categories_matched = {item.category for item in positive}

    if EvidenceCategory.IDENTIFIER in categories_matched:
        return AttachmentDecision(
            outcome=AttachmentOutcome.ATTACH_CONFIRMED,
            reason=f"An explicit airport identifier matching {candidate.name!r} was found; identifier evidence is definitive alone.",
            positive_evidence=positive,
            contradicting_evidence=(),
        )

    if len(categories_matched) >= 2:
        names = ", ".join(sorted(c.value for c in categories_matched))
        return AttachmentDecision(
            outcome=AttachmentOutcome.ATTACH_CONFIRMED,
            reason=f"{len(categories_matched)} independent positive evidence categories ({names}) agree on {candidate.name!r}, none contradicted.",
            positive_evidence=positive,
            contradicting_evidence=(),
        )

    if len(categories_matched) == 1:
        (only,) = categories_matched
        return AttachmentDecision(
            outcome=AttachmentOutcome.ATTACH_PROVISIONAL,
            reason=(
                f"Exactly one positive evidence category ({only.value}) supports {candidate.name!r}, "
                "not enough alone for confirmed attachment; insufficient for governed reconciliation."
            ),
            positive_evidence=positive,
            contradicting_evidence=(),
        )

    return AttachmentDecision(
        outcome=AttachmentOutcome.INSUFFICIENT_IDENTITY,
        reason=f"No positive or contradicting identity evidence found for candidate {candidate.name!r}.",
        positive_evidence=(),
        contradicting_evidence=(),
    )


def evaluate_attachment_for_candidates(
    evidence: EvidenceBag, candidates: "list[CandidateAirport]",
) -> "dict[object, AttachmentDecision]":
    """Thin, deterministic orchestration over evaluate_attachment(): runs
    the pure per-candidate evaluation against every supplied candidate,
    then resolves cross-candidate ambiguity that no single-candidate call
    can see on its own. If more than one candidate independently reaches
    ATTACH_CONFIRMED or ATTACH_PROVISIONAL for the SAME evidence bag (e.g.
    a runway pair genuinely shared by two different candidate airports),
    none of them can be safely and uniquely attached - every such
    candidate's outcome is downgraded to REVIEW_REQUIRED. REJECT_CROSS_AIRPORT
    and INSUFFICIENT_IDENTITY results are never touched - a candidate that
    was independently contradicted, or found no evidence, is unaffected by
    what any other candidate found.

    Still performs no I/O - candidates is a plain, caller-supplied list.
    """
    decisions = {candidate.id: evaluate_attachment(candidate, evidence) for candidate in candidates}

    qualifying = [
        cid for cid, decision in decisions.items()
        if decision.outcome in (AttachmentOutcome.ATTACH_CONFIRMED, AttachmentOutcome.ATTACH_PROVISIONAL)
    ]
    if len(qualifying) <= 1:
        return decisions

    other_ids = {cid for cid in qualifying}
    result: "dict[object, AttachmentDecision]" = {}
    for cid, decision in decisions.items():
        if cid in qualifying:
            others = sorted(str(o) for o in other_ids if o != cid)
            result[cid] = AttachmentDecision(
                outcome=AttachmentOutcome.REVIEW_REQUIRED,
                reason=(
                    f"{decision.reason} However, the same evidence also independently qualifies "
                    f"candidate(s) {others} - ambiguous across multiple airports, requires human review."
                ),
                positive_evidence=decision.positive_evidence,
                contradicting_evidence=decision.contradicting_evidence,
            )
        else:
            result[cid] = decision
    return result


def candidate_airport_from_airport_like(
    airport_like, *, aliases: "frozenset[str]" = frozenset(), known_issuers: "frozenset[str]" = frozenset(),
) -> CandidateAirport:
    """Convenience builder from an ORM-shaped object (duck-typed, not
    imported from app.models - this module has zero database dependency;
    see the module docstring). Expects `airport_like` to expose the same
    attributes app.models.Airport already does: .id, .name, .iata_code,
    .icao_code, .faa_code, .city, .state_region, and .runways where each
    runway exposes .runway_ends with each end exposing .designation.

    This helper itself performs no query - but if `airport_like` is a
    live, lazily-loaded SQLAlchemy object whose .runways/.runway_ends
    were not already eager-loaded (e.g. via selectinload, the convention
    already used throughout app/static_export/build.py), accessing them
    here will trigger one, exactly like every other consumer of these
    relationships elsewhere in this repository. evaluate_attachment()
    itself never calls this function and never touches airport_like.
    """
    ends: set[str] = set()
    pairs: set[str] = set()
    for runway in getattr(airport_like, "runways", []) or []:
        designation = getattr(runway, "designation", None)
        if designation:
            pairs.add(designation)
        for end in getattr(runway, "runway_ends", []) or []:
            end_designation = getattr(end, "designation", None)
            if end_designation:
                ends.add(end_designation)

    identifiers = {
        code for code in (
            getattr(airport_like, "iata_code", None),
            getattr(airport_like, "icao_code", None),
            getattr(airport_like, "faa_code", None),
        ) if code
    }

    return CandidateAirport(
        id=airport_like.id,
        name=airport_like.name,
        identifiers=frozenset(identifiers),
        aliases=aliases,
        city_location=getattr(airport_like, "city", None),
        canonical_runway_ends=frozenset(ends),
        canonical_runway_pairs=frozenset(pairs),
        known_issuers=known_issuers,
    )
