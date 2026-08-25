"""Pure EMAS-relevance evaluation core (ERG1,
docs/architecture/rwi-emas-relevance-gate-design.md, first implementation
slice of that design's Option III architecture).

    tuple[EmasEvidenceObservation, ...] (already-classified evidence facts -
        this module never reads raw document text, never calls a
        source-specific extractor, never touches a CandidateFragment or
        EvidenceBag directly)
        + EmasRelevanceContext (explicit, currently-empty policy seam)
        -> evaluate_emas_relevance()
        -> EmasRelevanceDecision
        -> STOP (no persistence, no UnknownAirportCandidate/Airport/
           SourceAssertion read or write, no UAC4 gate wiring - all
           future, separately-authorized slices: ERG2 persistence, ERG3
           human relevance review, ERG4 canonical-admission gate)

Answers exactly one question, mirroring
docs/architecture/rwi-emas-relevance-gate-design.md's own governing
principle: "given evidence already known about one UnknownAirportCandidate,
is it EMAS_CONFIRMED, EMAS_STRONG_SIGNAL, EMAS_PLAUSIBLE_SIGNAL,
RUNWAY_ONLY_NOT_EMAS_RELEVANT, or INSUFFICIENT_EVIDENCE?" It never asks "is
this identity real" (that remains UAC1-5/Option 3's job, entirely untouched)
and never asks "should this become a canonical Airport" (that remains a
future, human-gated UAC4 precondition this module does not implement).

DIRECTLY MIRRORS app.services.promotion_policy_evaluation's own proven
shape (read fresh, in full, as this slice's own required precedent): a
pure, deterministic core; an Enum outcome vocabulary with no score/
probability member (design doc S5, and RWI's own pre-existing,
deliberately-chosen invariant - "candidate scoring/ranking: NOT_PRESENT
(deliberately)", docs/architecture/rwi-post-d4d8-strategic-orientation.md
capability #22); a frozen dataclass Context object as a forward-compatible
policy seam even where (as here, at this slice) it currently carries no
fields; deterministic, template-built reason strings, never LLM-generated;
zero database query, zero database write, zero network call, zero
filesystem access, and zero current-time read anywhere.

IDENTITY DISCOVERY != EMAS BUSINESS RELEVANCE != CANONICAL AIRPORT
ADMISSION (the design doc's own locked principle, restated here because
this module implements the middle term only): this module never creates,
reads, or mutates an UnknownAirportCandidate, SourceAssertion, Airport, or
Signal - importing app.models or app.database from this module would
already be a defect, not merely a style violation.

EVIDENCE CLASSES (design doc S4, "Section 3" in this repo's numbering):
seven classes A-G. Class G (generic runway work - pavement reconstruction,
resurfacing, lighting, PAPI, electrical vault) is STRUCTURALLY EXCLUDED
from ever contributing to a positive relevance outcome, on its own,
regardless of how much of it is present - the design doc's own derivation
(not this module's invention): every runway periodically needs exactly
this kind of maintenance, uncorrelated with EMAS need or opportunity, and
treating it as relevant would make RWI's watch queue converge toward
"every airport with a runway," which is the exact failure this gate exists
to prevent. The real Anoka County-Blaine Airport case (Runway 18-36
pavement reconstruction + electrical vault improvements, no EMAS/RSA/
arrestor/overrun language anywhere in the real evidence) is class G only
and is this module's own locked permanent regression
(tests/test_emas_relevance_evaluation.py::TestAnokaLockedRegression).

CLASS C'S OWN DUAL NATURE (design doc S4 row C): a planning/feasibility
signal is graded by what it plans/studies, not by its own class tag alone.
Class C co-occurring with class A (a feasibility study that itself NAMES
EMAS) contributes toward EMAS_STRONG_SIGNAL exactly as class A alone would;
class C occurring WITHOUT class A (a feasibility/alternatives study that
never names the EMAS product, e.g. a bare "Runway Safety Area Alternatives
Analysis") contributes only toward EMAS_PLAUSIBLE_SIGNAL, matching class B.
No special-cased "C+A" enum member is needed - the outcome derivation below
falls out of ordinary elif precedence over the matched class set.

TEMPORAL DISCOUNT (design doc S10/S16 item M - a historical article must
not read as a current opportunity): reuses
app.services.evidence_claim_semantics.TemporalQualifier VERBATIM (imported,
never reimplemented as a parallel enum) per the design doc's own explicit
instruction ("reusing the TemporalQualifier concept already proven in
promotion_policy_evaluation.py"). An observation explicitly tagged
HISTORICAL_FACT is excluded from contributing to the outcome UNLESS (a) its
own class is E_EXISTING_INSTALLATION (an installation's existence is a
present-tense structural fact about the airport regardless of its install
date - "installed in 2008" is still, today, a confirmed installation,
absent any explicit removal claim, which this module has no evidence
category for and does not invent), or (b) some OTHER positive observation
in the same evaluation is explicitly tagged with a temporality other than
HISTORICAL_FACT/UNKNOWN (a current follow-on corroborates the historical
one is still live). Missing/UNKNOWN temporality is NEVER discounted - only
an EXPLICIT HISTORICAL_FACT tag triggers the discount, so a caller that
simply has no temporal information yet never silently loses evidence
weight; this is a deliberate asymmetry, not an oversight (see
docs/architecture/rwi-erg1-emas-relevance-evaluator-report.md S19 for the
full derivation).

CONTRADICTION SEMANTICS (design doc S9 item C, S24 threat D/G - a genuinely
new, not-previously-specified case this slice had to derive): an
observation may be tagged ObservationPolarity.CONTRADICTING (e.g. "EMAS
feasibility was studied and explicitly rejected as unwarranted"). A
contradicting observation NEVER upgrades or downgrades the outcome computed
from positive-polarity evidence - allowing a document's own self-serving
negative claim to silently override structurally-matched positive evidence
would be exactly the kind of "free-text-only, untraceable" reasoning the
design doc's own S13 traceability requirement forbids. Instead, every
contradicting observation is always surfaced, never hidden, in
`contradicting_evidence_classes` and in `reason` - visible to whatever
human eventually reviews this candidate (design doc S12: relevance approval
is human-gated), never silently absorbed. This mirrors this codebase's own
repeated "advisory, never gating, never hidden" pattern (e.g.
ExistingSignalReconciliationDecision.advisory_candidate_signal_ids).

INVENTORY vs WATCH vs CANONICAL-ADMISSION-RELEVANT (ERG1.6,
docs/architecture/rwi-erg1-5-inventory-vs-opportunity-design.md - resolves
the open question this docstring used to flag as unfixed): RWI's own
already-shipped architecture (`app.models.Installation` = "what's installed
today," `app.models.Signal` = "something that could become a future EMAS
order," `scripts/graduate_signal_to_installation.py` = the real, working
pipeline turning one into the other) already draws exactly the distinction
this module now mirrors one layer earlier, pre-canonical:

`is_inventory_relevant` answers "does this evidence establish a confirmed
(current or historical) EMAS installation/incident fact that belongs in
RWI's EMAS world model" - driven by E_EXISTING_INSTALLATION/F_INCIDENT_DRIVEN
ALONE, regardless of temporality (an installation's/incident's existence is
a permanent historical fact, never erased by later evidence - see
CONTRADICTION SEMANTICS below). Mirrors `Installation`.

`is_watch_worthy` answers "is there current-or-future-shaped evidence of an
EMAS lifecycle event worth an operator's attention right now" - computed
with a DELIBERATE ASYMMETRY (ERG1.5's own locked resolution, re-derived and
confirmed, not merely re-applied):
  - A/B/C/D contribute UNLESS their own temporality is explicitly COMPLETED
    (a closed-out pipeline event, e.g. "RSA subsequently constructed,
    deficiency resolved" - see the CLOSED-EVENT NOTE below). These classes
    are inherently forward-looking by definition (a feasibility study, a
    funding action, a safety-area deficiency needing mitigation are, by
    definition, unresolved unless explicitly closed out) - so `UNKNOWN`
    temporality still counts, exactly as it always has for outcome
    computation (TEMPORAL DISCOUNT above, unchanged).
  - E/F contribute ONLY when EXPLICITLY tagged
    CURRENT_STATE_AS_OF_DOCUMENT_DATE/PLANNED_FUTURE_ACTION/
    REQUESTED_PENDING_APPROVAL (a repair/replacement genuinely in progress
    or planned) - `UNKNOWN`/`HISTORICAL_FACT`/`COMPLETED` never grant watch
    for these classes, since an installation's mere existence is, by
    default, an already-resolved fact requiring affirmative evidence to
    read as "something is happening now," not merely the absence of
    evidence to the contrary. Mirrors `Signal`.
This asymmetry is the direct, narrow fix for what used to be flagged here
as an open question: a dormant, decades-old confirmed installation with
zero corroborating current activity is now `is_inventory_relevant=True,
is_watch_worthy=False` - no longer identical to an active opportunity.

`is_canonical_admission_relevant` = `is_inventory_relevant OR is_watch_worthy`
- a DERIVED boolean, not a third independent primitive (ERG1.5 S1/S7,
re-confirmed: RWI's own `Installation`/`Signal` split already shows a
canonical Airport can legitimately exist for either reason alone). Field
name preserved unchanged from ERG1 - still means exactly "could a human
eventually approve CREATE_NEW_AIRPORT for this," a SEPARATE future
human-gated action this module does not implement or gate.

CLOSED-EVENT NOTE / MODELING GUIDANCE for future extraction-layer callers:
to represent "this pipeline event concluded" (a resolved deficiency, a
completed repair) in a way that correctly excludes it from
`is_watch_worthy`, tag the observation itself `COMPLETED` - do NOT rely on
a separate `CONTRADICTING` observation to achieve this. Contradiction
NEVER changes any of the three dimensions (see CONTRADICTION SEMANTICS
below, re-confirmed unchanged by ERG1.6, not silently redesigned) - a
contradicted-but-still-POSITIVE claim (e.g. "EMAS planned" + CONTRADICTING
"project cancelled") still contributes to `is_watch_worthy=True`,
deliberately, so a human reviewer sees both the original claim AND its
contradiction, rather than the system silently resolving the dispute.

KNOWN, FLAGGED, NOT-FULLY-SYMMETRIC OPEN NOTE (found during ERG1.6's own
implementation, not silently smoothed over): `is_inventory_relevant`
reuses the EXISTING, UNCHANGED `matched_classes` intersection with
E/F (`_CONFIRMED_CLASSES`) - which means it inherits the PRE-EXISTING,
already-locked asymmetry between E (temporal-discount-EXEMPT) and F (NOT
exempt - an incident's own newsworthiness genuinely fades with time,
unlike an installation's bare existence). A BARE, UNCORROBORATED
`HISTORICAL_FACT`-tagged F observation with nothing else is therefore
`is_inventory_relevant=False` (matches `outcome=INSUFFICIENT_EVIDENCE`,
since F alone is fully discounted there too) - NOT `True` as a naive
"E and F both establish inventory regardless of temporality" reading might
suggest. `UNKNOWN`-tagged F (never discounted, unlike explicit
`HISTORICAL_FACT`) and any F corroborated by another current-tagged
observation DO both set `is_inventory_relevant=True`, matching E. This
divergence is deliberately preserved, not resolved by inventing a new,
separately-permissive computation for F, per this slice's own
"reuse existing logic, do not touch outcome computation, STOP and report
conflicts rather than invent" instruction - see
docs/architecture/rwi-erg1-6-inventory-watch-refinement-report.md S11 for
the full derivation and the reasoning for why this is NOT a defect.

KNOWN, FLAGGED, NOT-FIXED-HERE OPEN QUESTION #2 (temporal discount
asymmetry, unchanged by ERG1.6): `UNKNOWN` temporality is never discounted
for OUTCOME computation (see TEMPORAL DISCOUNT above) - this means a
class-B/F observation whose caller simply failed to tag a genuinely
historical fact as `HISTORICAL_FACT` (leaving it at the `UNKNOWN` default)
is treated as undiscounted, exactly as if it were fresh. This is a
DELIBERATE, but NOT risk-free, choice: the alternative (discounting
`UNKNOWN` the same as `HISTORICAL_FACT`) would make the whole evaluator
fragile to ordinary extraction incompleteness, silently losing weight for
genuinely CURRENT evidence whose extractor simply never got around to
tagging temporality. The consequence is a hard, load-bearing requirement on
every future extraction-layer caller: temporality MUST be tagged
explicitly and accurately whenever it is determinable from the source
document (an explicit date/tense in the text), and `UNKNOWN` must be
reserved for genuinely indeterminate cases only - never used as a lazy
default for a fact the extractor could have determined was historical or
completed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.services.evidence_claim_semantics import TemporalQualifier

__all__ = [
    "EVALUATOR_VERSION",
    "EmasRelevanceInputError",
    "EvidenceClass",
    "ObservationPolarity",
    "RelevanceOutcome",
    "EmasEvidenceObservation",
    "EmasRelevanceContext",
    "EmasRelevanceDecision",
    "evaluate_emas_relevance",
]


# Bare string constant, no behavior implication - a future ERG2 persistence
# bridge's own seam (adversarial review addition, mission's own S22): a
# persisted assessment row can stamp which evaluator version classified it,
# without this module needing to know anything about persistence, rows, or
# schemas itself. Bump only on a classification-affecting change to this
# module's own logic.
EVALUATOR_VERSION = "1"


class EmasRelevanceInputError(ValueError):
    """Fail-closed, typed input-validation error - mirrors
    app.services.discovery_candidate_fragment.CandidateFragmentError's own
    "raise ValueError with a typed, inspectable reason" convention rather
    than allowing a malformed observation (e.g. a bare string where an Enum
    member is required) to silently misclassify."""


class EvidenceClass(str, Enum):
    """The seven evidence classes (design doc S4). Source-neutral concepts,
    never English strings or FAA/MAC-specific terminology - matching
    concept extraction to these class tags is entirely an EXTRACTION-LAYER
    responsibility (a future source adapter, mirroring
    app.acquisition.mac_granicus_extractor.py's own existing role), never
    this module's."""

    # NOTE (adversarial review finding, rwi-erg1-emas-relevance-evaluator-report.md
    # S9/S10): this class means the EMAS/engineered-materials-arresting-bed
    # aviation runway-safety technology specifically - it must never be
    # extraction-matched from unrelated "arresting" usage (military aircraft
    # arrestor cable/arresting-gear systems, "arresting the decline in
    # ridership," etc.). The evaluator has no raw text to police this
    # itself; getting this right is a hard EXTRACTION-LAYER requirement,
    # not something this class's own tag can express.
    A_EXPLICIT_EMAS = "A_EXPLICIT_EMAS"
    # NOTE (adversarial review finding, same report S9): this class means a
    # GENUINE runway safety area deficiency/need (RSA too short, RSA
    # improvement required, declared-distance reduction, arrestor
    # feasibility) - it must never be extraction-matched from ROUTINE/
    # ADMINISTRATIVE RSA activity that carries no deficiency/need signal at
    # all (RSA mowing contracts, RSA signage replacement, routine RSA
    # inspection, generic Part 139 compliance checklists that merely mention
    # "runway safety area" in passing). Routine RSA upkeep belongs with
    # class G's own "universal, uncorrelated with EMAS need" framing, never
    # with this class - again, a hard extraction-layer precision
    # requirement this evaluator cannot enforce from a bare class tag alone.
    B_RUNWAY_SAFETY_AREA_OR_ARRESTOR_NEED = "B_RUNWAY_SAFETY_AREA_OR_ARRESTOR_NEED"
    C_PLANNING_OR_FEASIBILITY = "C_PLANNING_OR_FEASIBILITY"
    D_FUNDING_OR_PROCUREMENT = "D_FUNDING_OR_PROCUREMENT"
    E_EXISTING_INSTALLATION = "E_EXISTING_INSTALLATION"
    F_INCIDENT_DRIVEN = "F_INCIDENT_DRIVEN"
    G_GENERIC_RUNWAY_WORK = "G_GENERIC_RUNWAY_WORK"


class ObservationPolarity(str, Enum):
    """POSITIVE: ordinary evidence for the named class. CONTRADICTING: an
    explicit claim that negates/rules out the named class (e.g. "EMAS was
    studied and rejected") - see module docstring's CONTRADICTION SEMANTICS
    section. Never inferred; always caller-supplied."""

    POSITIVE = "POSITIVE"
    CONTRADICTING = "CONTRADICTING"


class RelevanceOutcome(str, Enum):
    """The five-member deterministic vocabulary (design doc S5). No score,
    no probability, no sixth member - matching RWI's own pre-existing,
    deliberate "no candidate scoring" invariant."""

    EMAS_CONFIRMED = "EMAS_CONFIRMED"
    EMAS_STRONG_SIGNAL = "EMAS_STRONG_SIGNAL"
    EMAS_PLAUSIBLE_SIGNAL = "EMAS_PLAUSIBLE_SIGNAL"
    RUNWAY_ONLY_NOT_EMAS_RELEVANT = "RUNWAY_ONLY_NOT_EMAS_RELEVANT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


_CONFIRMED_CLASSES = frozenset({EvidenceClass.E_EXISTING_INSTALLATION, EvidenceClass.F_INCIDENT_DRIVEN})
_STRONG_CLASSES = frozenset({EvidenceClass.A_EXPLICIT_EMAS, EvidenceClass.D_FUNDING_OR_PROCUREMENT})
_PLAUSIBLE_CLASSES = frozenset({EvidenceClass.B_RUNWAY_SAFETY_AREA_OR_ARRESTOR_NEED, EvidenceClass.C_PLANNING_OR_FEASIBILITY})
_OPPORTUNITY_CLASSES = _STRONG_CLASSES | _PLAUSIBLE_CLASSES

# Only this class is exempt from the historical-fact temporal discount - an
# installation's existence is a present-tense structural fact regardless of
# its install date (module docstring, TEMPORAL DISCOUNT).
_TEMPORAL_DISCOUNT_EXEMPT_CLASSES = frozenset({EvidenceClass.E_EXISTING_INSTALLATION})

_NON_DISCOUNTING_TEMPORALITIES = frozenset({TemporalQualifier.HISTORICAL_FACT, TemporalQualifier.UNKNOWN})

# ERG1.6 (module docstring, INVENTORY vs WATCH vs CANONICAL-ADMISSION-RELEVANT):
# E/F only grant is_watch_worthy with one of these EXPLICIT temporalities -
# an installation's bare existence defaults to "already resolved," requiring
# affirmative evidence to read as "something is happening now."
_ACTIVE_TEMPORALITIES_FOR_INVENTORY_CLASSES = frozenset({
    TemporalQualifier.CURRENT_STATE_AS_OF_DOCUMENT_DATE,
    TemporalQualifier.PLANNED_FUTURE_ACTION,
    TemporalQualifier.REQUESTED_PENDING_APPROVAL,
})


@dataclass(frozen=True)
class EmasEvidenceObservation:
    """One already-classified evidence fact - never raw document text.
    Produced by a future, separate extraction-layer adapter (this module
    never extracts, matches regex, or reads raw_text itself); consumed here
    purely as a structured tag, exactly as
    app.services.promotion_policy_evaluation consumes already-typed `Claim`
    objects rather than raw text.

    `basis`: a short, deterministic, human-readable description of what was
    matched (e.g. "title phrase: EMAS Feasibility Study") - carried for
    explainability in `EmasRelevanceDecision.reason` only, never itself
    business logic (mirrors ExtractedMoney/ExtractedDate's own
    audit-only-context-label discipline in discovery_candidate_fragment.py).

    `temporality`: reuses TemporalQualifier verbatim (module docstring).
    Defaults to UNKNOWN - never discounted (see TEMPORAL DISCOUNT).

    `polarity`: defaults to POSITIVE. CONTRADICTING observations never
    change the computed outcome; they are only ever surfaced (module
    docstring, CONTRADICTION SEMANTICS).
    """

    evidence_class: EvidenceClass
    basis: str
    temporality: TemporalQualifier = TemporalQualifier.UNKNOWN
    polarity: ObservationPolarity = ObservationPolarity.POSITIVE

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_class, EvidenceClass):
            raise EmasRelevanceInputError(
                f"EmasEvidenceObservation.evidence_class must be an EvidenceClass member, got {self.evidence_class!r}"
            )
        if not isinstance(self.temporality, TemporalQualifier):
            raise EmasRelevanceInputError(
                f"EmasEvidenceObservation.temporality must be a TemporalQualifier member, got {self.temporality!r}"
            )
        if not isinstance(self.polarity, ObservationPolarity):
            raise EmasRelevanceInputError(
                f"EmasEvidenceObservation.polarity must be an ObservationPolarity member, got {self.polarity!r}"
            )
        if not self.basis or not self.basis.strip():
            raise EmasRelevanceInputError("EmasEvidenceObservation.basis is required and cannot be empty.")


@dataclass(frozen=True)
class EmasRelevanceContext:
    """Explicit, deliberately-empty policy seam at this slice (module
    docstring). Kept as its own object - rather than omitted entirely -
    because the design doc itself lists `EmasRelevanceContext` as an
    expected ERG1 artifact (rwi-emas-relevance-gate-design.md S20/S26 item
    26) and because this exact shape (a small, explicit, caller-supplied
    context object separate from the evidence tuple) is
    promotion_policy_evaluation.py's own proven pattern for future policy
    inputs that are not themselves evidence facts. No field is populated
    yet - no additional non-evidence-class policy input was justified
    during this slice's own contract derivation (see
    docs/architecture/rwi-erg1-emas-relevance-evaluator-report.md S3)."""


@dataclass(frozen=True)
class EmasRelevanceDecision:
    """An evaluation result - never an ORM object, never persisted by this
    module (ERG2's own future job). `reason` is a deterministic,
    template-built string - never LLM-generated. `evidence_classes_matched`
    names every POSITIVE-polarity class that actually contributed to
    `outcome` (temporally-discounted classes are excluded from this set,
    not merely from the outcome, so the two stay consistent).
    `contradicting_evidence_classes` names every CONTRADICTING-polarity
    class present, regardless of whether it affected `outcome` (it never
    does - see CONTRADICTION SEMANTICS). `is_inventory_relevant`,
    `is_watch_worthy`, and `is_canonical_admission_relevant` are three
    fields answering three deliberately separate questions - see module
    docstring's own INVENTORY vs WATCH vs CANONICAL-ADMISSION-RELEVANT
    section (ERG1.6)."""

    outcome: RelevanceOutcome
    reason: str
    evidence_classes_matched: "frozenset[EvidenceClass]" = field(default_factory=frozenset)
    contradicting_evidence_classes: "frozenset[EvidenceClass]" = field(default_factory=frozenset)
    is_inventory_relevant: bool = False
    is_watch_worthy: bool = False
    is_canonical_admission_relevant: bool = False


def _is_temporally_discounted(
    observation: EmasEvidenceObservation, positive_observations: "tuple[EmasEvidenceObservation, ...]",
) -> bool:
    """True only for an explicitly HISTORICAL_FACT-tagged observation whose
    class is not exempt (E) and which no OTHER positive observation in the
    same evaluation corroborates as still current (module docstring,
    TEMPORAL DISCOUNT). UNKNOWN is never discounted - only an explicit
    historical tag triggers this."""
    if observation.evidence_class in _TEMPORAL_DISCOUNT_EXEMPT_CLASSES:
        return False
    if observation.temporality != TemporalQualifier.HISTORICAL_FACT:
        return False
    return not any(
        other is not observation and other.temporality not in _NON_DISCOUNTING_TEMPORALITIES
        for other in positive_observations
    )


def evaluate_emas_relevance(
    observations: "tuple[EmasEvidenceObservation, ...]", context: EmasRelevanceContext = EmasRelevanceContext(),
) -> EmasRelevanceDecision:
    """Pure, deterministic: the same `observations` tuple - regardless of
    its ordering, and regardless of duplicate entries within it - always
    produces the same EmasRelevanceDecision (design doc S16/mission S16:
    determinism, order-independence, duplicate-safety). No probabilistic
    accumulation: the outcome is a structural set-membership decision over
    which classes are present, never a count or a weighted score (design
    doc S5's own "no scores" invariant, and mission S11 item D's own
    "multiple weak signals do NOT magically combine into certainty"
    instruction).
    """
    unique_observations = tuple(dict.fromkeys(observations))
    positive = tuple(o for o in unique_observations if o.polarity == ObservationPolarity.POSITIVE)
    contradicting = tuple(o for o in unique_observations if o.polarity == ObservationPolarity.CONTRADICTING)

    contradicting_classes = frozenset(o.evidence_class for o in contradicting)

    contributing = tuple(o for o in positive if not _is_temporally_discounted(o, positive))
    discounted = tuple(o for o in positive if _is_temporally_discounted(o, positive))
    matched_classes = frozenset(o.evidence_class for o in contributing)

    contradiction_note = ""
    if contradicting_classes:
        contradiction_note = (
            f" CONTRADICTING evidence present for class(es) "
            f"{', '.join(sorted(c.value for c in contradicting_classes))} - never auto-suppresses or "
            f"auto-confirms matched evidence; surfaced for mandatory human review."
        )
    discount_note = ""
    if discounted:
        discounted_classes = sorted({o.evidence_class.value for o in discounted})
        discount_note = (
            f" Excluded as temporally-discounted (explicitly historical, uncorroborated by any current "
            f"evidence): {', '.join(discounted_classes)}."
        )

    if matched_classes & _CONFIRMED_CLASSES:
        outcome = RelevanceOutcome.EMAS_CONFIRMED
        reason = (
            f"EMAS_CONFIRMED: existing-installation or incident-driven evidence present "
            f"({', '.join(sorted(c.value for c in matched_classes & _CONFIRMED_CLASSES))})."
            + discount_note + contradiction_note
        )
    elif matched_classes & _STRONG_CLASSES:
        outcome = RelevanceOutcome.EMAS_STRONG_SIGNAL
        reason = (
            f"EMAS_STRONG_SIGNAL: explicit EMAS/funding-or-procurement evidence present, no confirmed "
            f"installation yet ({', '.join(sorted(c.value for c in matched_classes & _STRONG_CLASSES))})."
            + discount_note + contradiction_note
        )
    elif matched_classes & _PLAUSIBLE_CLASSES:
        outcome = RelevanceOutcome.EMAS_PLAUSIBLE_SIGNAL
        reason = (
            f"EMAS_PLAUSIBLE_SIGNAL: runway-safety-area/arrestor-need or planning/feasibility evidence "
            f"present without naming EMAS explicitly "
            f"({', '.join(sorted(c.value for c in matched_classes & _PLAUSIBLE_CLASSES))})."
            + discount_note + contradiction_note
        )
    elif EvidenceClass.G_GENERIC_RUNWAY_WORK in matched_classes:
        outcome = RelevanceOutcome.RUNWAY_ONLY_NOT_EMAS_RELEVANT
        reason = (
            "RUNWAY_ONLY_NOT_EMAS_RELEVANT: only generic runway work evidence present (class G) - "
            "structurally excluded from ever contributing to a positive relevance outcome on its own; "
            "universal airport maintenance, uncorrelated with EMAS need or opportunity."
            + discount_note + contradiction_note
        )
    else:
        outcome = RelevanceOutcome.INSUFFICIENT_EVIDENCE
        reason = (
            "INSUFFICIENT_EVIDENCE: no runway-safety-shaped evidence class contributes to a relevance "
            "determination." + discount_note + contradiction_note
        )

    is_inventory_relevant = bool(matched_classes & _CONFIRMED_CLASSES)

    opportunity_watch = any(
        o.evidence_class in _OPPORTUNITY_CLASSES and o.temporality != TemporalQualifier.COMPLETED
        for o in contributing
    )
    inventory_watch = any(
        o.evidence_class in _CONFIRMED_CLASSES and o.temporality in _ACTIVE_TEMPORALITIES_FOR_INVENTORY_CLASSES
        for o in positive
    )
    is_watch_worthy = opportunity_watch or inventory_watch

    is_canonical_admission_relevant = is_inventory_relevant or is_watch_worthy

    return EmasRelevanceDecision(
        outcome=outcome,
        reason=reason.strip(),
        evidence_classes_matched=matched_classes,
        contradicting_evidence_classes=contradicting_classes,
        is_inventory_relevant=is_inventory_relevant,
        is_watch_worthy=is_watch_worthy,
        is_canonical_admission_relevant=is_canonical_admission_relevant,
    )
