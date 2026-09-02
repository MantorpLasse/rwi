"""Discovery Orchestration V1 - deterministic temporal follow-up planning
(Mission #17B, implementing the recon in Mission #17A).

    human-KEPT CandidateFragment
        -> deterministic temporal Trigger detection
        -> deterministic follow-up SearchQuery planning

An ADVISORY DISCOVERY capability only. Composes exactly two already-
committed, pure runtime types - app.services.discovery_candidate_fragment
.CandidateFragment and app.discovery.query.SearchQuery - and nothing else.
This module performs NO database access, NO network access, NO file
write, and NO ORM mutation of any kind. It does not conclude a later
state exists, does not create evidence, does not fetch documents, does
not persist anything, does not inspect or mutate governed intelligence
(Airport/Installation/Signal), and does not invoke IdentityGuard or any
persistence service - none of those types are imported here.

Mission #17A Part M's optional governed-Installation-state lookup is
DELIBERATELY NOT implemented here (Mission #17B Part D, explicit HQ
design refinement): CandidateFragment -> Trigger -> Query Plan remains
pure and fully independent of current governed-domain state. A future,
higher-level research coordinator may compare a query plan against
governed state; that composition is explicitly out of scope for this
module.

NEGATION SAFETY (Mission #17B Part H - read before adding phrases):
V1 does not implement general NLP negation handling. Instead, safety is
achieved entirely through a narrow POSITIVE phrase vocabulary, and each
candidate phrase was individually checked for the specific structural
vulnerability Part H warns about: a positive phrase that remains a
literal, unbroken substring inside an OBVIOUS, COMMON negated
construction. Concretely, of the 7 phrases named as candidates in the
mission brief, 5 were found and REJECTED for exactly this reason:

  - "installation underway" (bare, no internal auxiliary verb) is a
    literal substring of the extremely common construction "no
    installation underway" / "there is no installation underway" -
    REJECTED.
  - "work has begun" is a literal substring of the extremely common
    project-status phrasing "no work has begun (yet)" - REJECTED.
  - "selected for installation" is a literal substring of "was NOT
    selected for installation" (the auxiliary "was"/"not" sits entirely
    OUTSIDE the phrase, so natural negation never breaks the substring) -
    REJECTED.
  - "scheduled for installation" - same structural flaw as above -
    REJECTED.
  - "planned installation" - same structural flaw (a bare noun phrase,
    negated only from outside: "no planned installation exists") -
    REJECTED.

Only phrases whose OWN auxiliary verb sits immediately before the
distinguishing content word survive: "is installing" ("is not
installing"/"isn't installing" - the natural, common negation - breaks
the substring by inserting "not" between "is" and "installing") and
"installation is underway" (same property: "is not underway" breaks it).
This is a real, disclosed V1 limitation, not a complete solution: a rare,
clause-EXTERNAL negation ("it is not the case that ... is installing")
would still slip through undetected by any purely lexical phrase list -
this is accepted as out of scope for V1, exactly as Part H directs
("achieve safety through narrow positive phrase matching," not "invent a
complex negation engine").
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.discovery.query import SearchQuery
from app.services.discovery_candidate_fragment import CandidateFragment

__all__ = [
    "TemporalTriggerKind",
    "AirportSearchContext",
    "AirportSearchContextError",
    "DiscoveryTrigger",
    "detect_temporal_triggers",
    "plan_follow_up_queries",
]


class TemporalTriggerKind(str, Enum):
    """V1 implements exactly one kind - the minimum required for the
    LCY-class case (Mission #17B Part F). Every other kind found
    conceptually generalizable in Mission #17A's recon (replacement,
    funding, incident, option-considered, procurement, commissioning) is
    explicitly NOT implemented here - one real trigger kind first."""

    TEMPORAL_ACTIVITY_INSTALLING = "TEMPORAL_ACTIVITY_INSTALLING"


# Deliberately small, explicit, human-reviewed positive-phrase vocabulary
# (Mission #17B Part G) - forward/in-progress installation language only,
# narrowed from the mission's 7 candidates to the 2 that survive the
# negation-safety review above. Matching is case-insensitive; the STORED
# matched_text always preserves the original casing found in the fragment
# (Part M). Deliberately EXCLUDES bare words like "installed"/"completed"/
# "commissioned"/"operational" - those are FOLLOW-UP SEARCH CONCEPTS
# (_INSTALLING_FOLLOW_UP_CONCEPTS below), never trigger phrases - and
# deliberately excludes any "considered"/"under evaluation" phrasing
# (Mission #17A Part P's YTZ anti-confirmation-bias finding): no entry
# here can ever fire on a sentence that merely evaluated EMAS as an
# option among several.
_POSITIVE_TRIGGER_PHRASES: tuple[str, ...] = (
    "is installing",
    "installation is underway",
)

# Fixed conceptual follow-up set for TEMPORAL_ACTIVITY_INSTALLING (Mission
# #17B Part I) - SEARCH CONCEPTS ONLY. These are NOT equivalent states
# ("installed" != "operational"; "completed" may mean civil works only;
# "commissioned" has its own distinct technical meaning) and are never
# persisted as lifecycle state anywhere in this module or its output.
_INSTALLING_FOLLOW_UP_CONCEPTS: tuple[str, ...] = (
    "installed",
    "completed",
    "commissioned",
    "operational",
)


class AirportSearchContextError(ValueError):
    """Raised when the caller supplies an unusable AirportSearchContext."""


@dataclass(frozen=True)
class AirportSearchContext:
    """Explicit, caller-supplied SEARCH CONTEXT - never evidence identity
    (Mission #17B Part J). This module never queries an Airport ORM row
    and never derives this context from Search-seed context alone; the
    caller is responsible for supplying a value with real provenance
    (e.g. the same explicit candidate Airport used at persistence time, or
    an already-attached SourceAssertion's governed Airport name/codes).
    `name` is required and fails closed if blank/absent; `iata_code`/
    `icao_code` are optional enrichments only. Never allowed to alter any
    CandidateFragment evidence field - this type is consumed only by
    plan_follow_up_queries() below, never fed back into IdentityGuard or
    any evidence-bearing structure."""

    name: str
    iata_code: "str | None" = None
    icao_code: "str | None" = None

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise AirportSearchContextError(
                "AirportSearchContext.name is required and cannot be empty - "
                "search context must fail closed rather than silently proceed "
                "with no usable airport identity."
            )


@dataclass(frozen=True)
class DiscoveryTrigger:
    """One deterministic, runtime-only temporal follow-up trigger (Mission
    #17A Part G / Mission #17B Part E). No ORM, no table, no migration, no
    persistence - exactly like FragmentSelection/CandidateFragment
    (pre-KEEP) themselves. Carries no lifecycle state, no resolved state,
    no confidence/probability/freshness score, and no current-state
    conclusion of any kind: it means only "this evidence creates a reason
    to search for another state," never "the later state exists.\""""

    trigger_kind: TemporalTriggerKind
    artifact_identity: str
    source_locator: str
    matched_text: str
    airport_context: AirportSearchContext
    concept_term: str
    follow_up_concepts: "tuple[str, ...]"
    reason: str


def detect_temporal_triggers(
    fragment: CandidateFragment,
    *,
    airport_context: AirportSearchContext,
    concept_term: str,
    phrase_vocabulary: "tuple[str, ...] | None" = None,
) -> "tuple[DiscoveryTrigger, ...]":
    """Pure, deterministic: scans fragment.raw_text for a narrow, fixed
    positive-phrase vocabulary (case-insensitive match; original-casing
    matched_text preserved - Part M). Zero database, network, file, or
    ORM access - fragment/airport_context/concept_term/phrase_vocabulary
    are the only inputs, all already in the caller's hands.

    `phrase_vocabulary` is the multilingual seam (Mission #17A Part L /
    #17B Part N): defaults to the English V1 vocabulary
    (_POSITIVE_TRIGGER_PHRASES) when omitted, but a caller may supply an
    alternate tuple of lower-cased phrases (e.g. a future non-English
    vocabulary pack) WITHOUT modifying this function or any orchestration
    logic - only the phrase list changes, the detection algorithm and
    every field of DiscoveryTrigger stay identical regardless of which
    language the phrases came from. No non-English pack is shipped in V1.

    concept_term and airport_context are both required, explicit caller
    inputs (Part J/K) - never inferred from fragment text, never derived
    from Search-seed context alone.

    Returns one DiscoveryTrigger per matched phrase (each vocabulary
    phrase is visited at most once, since neither the default nor a
    caller-supplied vocabulary is expected to contain duplicates) - a
    fragment naming multiple distinct trigger phrases produces multiple
    Triggers, each honestly citing the specific literal text that fired
    it. Returns an empty tuple when no phrase matches - the expected,
    safe outcome for historical-only, negated, option-considered, or
    otherwise unresolved text (Mission #17A Part P / #17B Part H/Q). No
    special-casing for any particular airport or document exists
    anywhere in this function.
    """
    if not concept_term or not concept_term.strip():
        raise ValueError("concept_term is required and cannot be empty.")

    vocabulary = phrase_vocabulary if phrase_vocabulary is not None else _POSITIVE_TRIGGER_PHRASES
    text = fragment.raw_text
    lowered = text.lower()
    triggers: list[DiscoveryTrigger] = []

    for phrase in vocabulary:
        index = lowered.find(phrase)
        if index == -1:
            continue
        matched_text = text[index : index + len(phrase)]
        triggers.append(
            DiscoveryTrigger(
                trigger_kind=TemporalTriggerKind.TEMPORAL_ACTIVITY_INSTALLING,
                artifact_identity=fragment.artifact_identity,
                source_locator=fragment.source_locator,
                matched_text=matched_text,
                airport_context=airport_context,
                concept_term=concept_term,
                follow_up_concepts=_INSTALLING_FOLLOW_UP_CONCEPTS,
                reason=(
                    f"Literal historical evidence {matched_text!r} at "
                    f"{fragment.source_locator!r} (artifact {fragment.artifact_identity!r}) "
                    f"suggests an in-progress {concept_term} activity whose later "
                    "state (installed/completed/commissioned/operational) is unresolved."
                ),
            )
        )

    return tuple(triggers)


def plan_follow_up_queries(trigger: DiscoveryTrigger) -> "tuple[SearchQuery, ...]":
    """Pure, deterministic: one existing SearchQuery per follow_up_concept
    (Mission #17B Part L) - reuses app.discovery.query.SearchQuery exactly,
    never a competing query type. Every rendered query is fully explainable
    from the trigger's own fields alone: template_id encodes trigger_kind
    and the follow-up concept; identity_field/identity_value trace
    directly back to airport_context.name (the only field this V1 uses -
    IATA/ICAO enrichment is left for a future mission, matching the
    smallest-safe-scope instruction). No LLM, no randomization, no
    network, no clock.
    """
    name = trigger.airport_context.name
    rendered_name = f'"{name}"' if " " in name else name

    queries: list[SearchQuery] = []
    for concept in trigger.follow_up_concepts:
        rendered = f"{rendered_name} {trigger.concept_term} {concept}"
        queries.append(
            SearchQuery(
                rendered=rendered,
                template_id=f"temporal_followup_{trigger.trigger_kind.value.lower()}_{concept}",
                identity_field="name",
                identity_value=name,
            )
        )
    return tuple(queries)
