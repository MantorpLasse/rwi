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

WATCH-WORTHY vs CANONICAL-ADMISSION-RELEVANT (design doc S6/S7 - two
genuinely different questions, kept as two separate output fields even
though this slice's own derivation computes them from the identical
three-outcome set): `is_watch_worthy` answers "should this surface in an
automatic operator queue" (S7, no human step); `is_canonical_admission_relevant`
answers "could a human eventually approve CREATE_NEW_AIRPORT for this"
(S6, still requires a SEPARATE future human approval action this module
does not implement or gate). They are computed identically today
(EMAS_CONFIRMED/EMAS_STRONG_SIGNAL/EMAS_PLAUSIBLE_SIGNAL) but are kept as
two named fields, per the design doc's own explicit distinction, so a
future slice may diverge them (e.g. a stricter admission bar) without a
breaking rename.

KNOWN, FLAGGED, NOT-FIXED-HERE OPEN QUESTION (found during adversarial
review, docs/architecture/rwi-erg1-emas-relevance-evaluator-report.md S16):
EMAS_CONFIRMED currently means both "RWI inventory relevance" (this airport
demonstrably has/had EMAS infrastructure) AND "operator watch/admission
relevance" (`is_watch_worthy`/`is_canonical_admission_relevant` both True)
identically - including for a purely HISTORICAL_FACT-tagged installation
with zero corroborating current activity (e.g. "installed in 2008," no
other evidence). The parent design doc's own Section 7 defines
watch-worthiness as EMAS_CONFIRMED/STRONG/PLAUSIBLE unconditionally, with
no carve-out for a dormant, decades-old, no-longer-active installation -
this module implements that locked definition exactly as written, and does
NOT unilaterally add a "dormant" sub-state or a third boolean, since doing
so would require new vocabulary the parent design doc never specified
(this slice's own correction policy: STOP before widening, don't invent).
Flagged explicitly for a future design-level decision before ERG4 (the
UAC4 canonical-admission gate) is built, since "should a dormant, 15-year-old
confirmed installation with zero current activity be admission-eligible on
the same terms as an active EMAS_STRONG_SIGNAL opportunity" is a genuine,
unresolved policy question, not an implementation defect in this module.

KNOWN, FLAGGED, NOT-FIXED-HERE OPEN QUESTION #2 (temporal discount
asymmetry): `UNKNOWN` temporality is never discounted (see TEMPORAL
DISCOUNT above) - this means a class-B/F observation whose caller simply
failed to tag a genuinely historical fact as `HISTORICAL_FACT` (leaving it
at the `UNKNOWN` default) is treated as undiscounted, exactly as if it were
fresh. This is a DELIBERATE, but NOT risk-free, choice: the alternative
(discounting `UNKNOWN` the same as `HISTORICAL_FACT`) would make the whole
evaluator fragile to ordinary extraction incompleteness, silently losing
weight for genuinely CURRENT evidence whose extractor simply never got
around to tagging temporality. The consequence is a hard, load-bearing
requirement on every future extraction-layer caller: temporality MUST be
tagged explicitly and accurately whenever it is determinable from the
source document (an explicit date/tense in the text), and `UNKNOWN` must be
reserved for genuinely indeterminate cases only - never used as a lazy
default for a fact the extractor could have determined was historical.
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


_WATCH_AND_ADMISSION_RELEVANT_OUTCOMES = frozenset({
    RelevanceOutcome.EMAS_CONFIRMED,
    RelevanceOutcome.EMAS_STRONG_SIGNAL,
    RelevanceOutcome.EMAS_PLAUSIBLE_SIGNAL,
})

_CONFIRMED_CLASSES = frozenset({EvidenceClass.E_EXISTING_INSTALLATION, EvidenceClass.F_INCIDENT_DRIVEN})
_STRONG_CLASSES = frozenset({EvidenceClass.A_EXPLICIT_EMAS, EvidenceClass.D_FUNDING_OR_PROCUREMENT})
_PLAUSIBLE_CLASSES = frozenset({EvidenceClass.B_RUNWAY_SAFETY_AREA_OR_ARRESTOR_NEED, EvidenceClass.C_PLANNING_OR_FEASIBILITY})

# Only this class is exempt from the historical-fact temporal discount - an
# installation's existence is a present-tense structural fact regardless of
# its install date (module docstring, TEMPORAL DISCOUNT).
_TEMPORAL_DISCOUNT_EXEMPT_CLASSES = frozenset({EvidenceClass.E_EXISTING_INSTALLATION})

_NON_DISCOUNTING_TEMPORALITIES = frozenset({TemporalQualifier.HISTORICAL_FACT, TemporalQualifier.UNKNOWN})


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
    does - see CONTRADICTION SEMANTICS). `is_watch_worthy` and
    `is_canonical_admission_relevant` are two deliberately separate
    booleans - see module docstring's own WATCH-WORTHY vs
    CANONICAL-ADMISSION-RELEVANT section."""

    outcome: RelevanceOutcome
    reason: str
    evidence_classes_matched: "frozenset[EvidenceClass]" = field(default_factory=frozenset)
    contradicting_evidence_classes: "frozenset[EvidenceClass]" = field(default_factory=frozenset)
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

    watch_and_admission = outcome in _WATCH_AND_ADMISSION_RELEVANT_OUTCOMES
    return EmasRelevanceDecision(
        outcome=outcome,
        reason=reason.strip(),
        evidence_classes_matched=matched_classes,
        contradicting_evidence_classes=contradicting_classes,
        is_watch_worthy=watch_and_admission,
        is_canonical_admission_relevant=watch_and_admission,
    )
