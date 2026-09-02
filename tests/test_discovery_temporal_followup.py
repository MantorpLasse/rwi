"""RWI Mission #17B - offline tests for
app.services.discovery_temporal_followup. Pure functions only: no
database, no network, no filesystem."""

from __future__ import annotations

import pytest

from app.discovery.query import SearchQuery
from app.services.discovery_candidate_fragment import CandidateFragment
from app.services.discovery_temporal_followup import (
    AirportSearchContext,
    AirportSearchContextError,
    DiscoveryTrigger,
    TemporalTriggerKind,
    detect_temporal_triggers,
    plan_follow_up_queries,
)

# Real preserved LCY fragment 1 raw_text, verbatim (Mission #16B
# SourceAssertion 239 / Snapshot 7 page:3;chars:147-941).
LCY_FRAGMENT_1_TEXT = (
    "process (ACP)[1].\nThe CAA reference is ACP-2022-090, the link to the CAA progress page is here.\n"
    "The intent of this document is to summarise and satisfy the requirements of CAP1616 Stages 1-4.\n"
    "2. Brief Summary of this Proposal\nLondon City Airport is installing an Engineered Material Arrestor System "
    "(EMAS) which will provide an arrestor\nbed at both ends of its runway, enhancing safety and reducing the risk "
    "to aircraft and passengers should an\naircraft overrun or undershoot a runway. The EMAS will be placed in the "
    "existing RESAs and the future design\nwill see changes to the threshold locations.\nProcedures will be "
    "introduced in two phases:\n1. pre-flight validation procedures will accommodate the new threshold locations "
    "but will not include\nthe revised Step Down Fix (SDF) locations or alt"
)

LCY_ARTIFACT_IDENTITY = (
    "generic_web:c649a3bfd2a89c7a665501d0a8c98955825a4663ecc344af6f418a69fbeb1e5e:"
    "a4d9d3f832cb619a3f8bd53874b5382e77969da1e90a889e08c8d3cd529428a3"
)
LCY_SOURCE_LOCATOR = "page:3;chars:147-941"

YTZ_CONTEXT = AirportSearchContext(name="Billy Bishop Toronto City Airport", iata_code="YTZ", icao_code="CYTZ")
LCY_CONTEXT = AirportSearchContext(name="London City Airport", iata_code="LCY", icao_code="EGLC")


def _fragment(text: str, *, artifact_identity: str = "artifact:test", source_locator: str = "page:1;chars:0-10") -> CandidateFragment:
    return CandidateFragment(artifact_identity=artifact_identity, source_locator=source_locator, raw_text=text)


# --- O. LCY acceptance test ---


def test_lcy_fragment_produces_exactly_one_installing_trigger():
    fragment = _fragment(LCY_FRAGMENT_1_TEXT, artifact_identity=LCY_ARTIFACT_IDENTITY, source_locator=LCY_SOURCE_LOCATOR)
    triggers = detect_temporal_triggers(fragment, airport_context=LCY_CONTEXT, concept_term="EMAS")

    assert len(triggers) == 1
    trigger = triggers[0]
    assert trigger.trigger_kind == TemporalTriggerKind.TEMPORAL_ACTIVITY_INSTALLING
    assert trigger.matched_text == "is installing"
    assert trigger.artifact_identity == LCY_ARTIFACT_IDENTITY
    assert trigger.source_locator == LCY_SOURCE_LOCATOR
    assert trigger.airport_context == LCY_CONTEXT
    assert trigger.airport_context.name == "London City Airport"
    assert trigger.airport_context.iata_code == "LCY"
    assert trigger.airport_context.icao_code == "EGLC"
    assert trigger.concept_term == "EMAS"
    assert trigger.follow_up_concepts == ("installed", "completed", "commissioned", "operational")


def test_lcy_trigger_produces_expected_search_queries():
    fragment = _fragment(LCY_FRAGMENT_1_TEXT, artifact_identity=LCY_ARTIFACT_IDENTITY, source_locator=LCY_SOURCE_LOCATOR)
    trigger = detect_temporal_triggers(fragment, airport_context=LCY_CONTEXT, concept_term="EMAS")[0]
    queries = plan_follow_up_queries(trigger)

    assert len(queries) == 4
    assert all(isinstance(q, SearchQuery) for q in queries)
    rendered = [q.rendered for q in queries]
    assert rendered == [
        '"London City Airport" EMAS installed',
        '"London City Airport" EMAS completed',
        '"London City Airport" EMAS commissioned',
        '"London City Airport" EMAS operational',
    ]
    for q in queries:
        assert q.identity_field == "name"
        assert q.identity_value == "London City Airport"
        assert q.template_id.startswith("temporal_followup_temporal_activity_installing_")


def test_lcy_fragment_matched_text_preserves_original_casing():
    fragment = _fragment("The EMAS Is Installing at the site.")
    triggers = detect_temporal_triggers(fragment, airport_context=LCY_CONTEXT, concept_term="EMAS")
    assert len(triggers) == 1
    assert triggers[0].matched_text == "Is Installing"


# --- P. YTZ negative test ---


def test_ytz_considered_sentence_produces_zero_triggers():
    fragment = _fragment(
        "EMAS was considered at Billy Bishop Toronto City Airport, but the landmass alternative was approved."
    )
    triggers = detect_temporal_triggers(fragment, airport_context=YTZ_CONTEXT, concept_term="EMAS")
    assert triggers == ()


# --- Q. Historical / negation negative tests ---


@pytest.mark.parametrize(
    "text",
    [
        "EMAS was installed in 2005.",
        "EMAS installation was not completed.",
        "EMAS was not installed.",
        "EMAS installation was cancelled.",
        "EMAS is operational.",
    ],
)
def test_historical_and_negative_sentences_produce_zero_triggers(text):
    fragment = _fragment(text)
    triggers = detect_temporal_triggers(fragment, airport_context=LCY_CONTEXT, concept_term="EMAS")
    assert triggers == ()


# --- H. Negation-safety design review: the 5 phrases dropped from the
# mission's 7 candidates because they remain literal substrings of a
# common negated construction. Each assertion below demonstrates the
# exact vulnerability that justified excluding that phrase from
# _POSITIVE_TRIGGER_PHRASES (module docstring has the full rationale).


@pytest.mark.parametrize(
    "text",
    [
        "There is no installation underway at this airport.",
        "No work has begun on the replacement yet.",
        "The airport was not selected for installation.",
        "The airport was not scheduled for installation this year.",
        "There is no planned installation at this time.",
    ],
)
def test_rejected_candidate_phrases_would_have_false_positived_on_negation(text):
    """Proves why these 5 mission-suggested phrases are absent from V1's
    vocabulary: each sentence here is a real, common negated construction
    that a naive phrase list would have matched. With the shipped,
    narrowed vocabulary, none of them produce a trigger."""
    fragment = _fragment(text)
    triggers = detect_temporal_triggers(fragment, airport_context=LCY_CONTEXT, concept_term="EMAS")
    assert triggers == ()


def test_is_installing_survives_its_own_natural_negation():
    fragment = _fragment("London City Airport is not installing EMAS at this time.")
    triggers = detect_temporal_triggers(fragment, airport_context=LCY_CONTEXT, concept_term="EMAS")
    assert triggers == ()


def test_installation_is_underway_survives_its_own_natural_negation():
    fragment = _fragment("The installation is not underway yet.")
    triggers = detect_temporal_triggers(fragment, airport_context=LCY_CONTEXT, concept_term="EMAS")
    assert triggers == ()


def test_installation_is_underway_positive_case_fires():
    fragment = _fragment("The EMAS installation is underway at both runway ends.")
    triggers = detect_temporal_triggers(fragment, airport_context=LCY_CONTEXT, concept_term="EMAS")
    assert len(triggers) == 1
    assert triggers[0].matched_text == "installation is underway"


# --- Multiple distinct phrases in one fragment ---


def test_multiple_distinct_phrases_produce_multiple_triggers():
    fragment = _fragment("EMAS is installing at the west end; installation is underway at the east end.")
    triggers = detect_temporal_triggers(fragment, airport_context=LCY_CONTEXT, concept_term="EMAS")
    assert len(triggers) == 2
    matched = {t.matched_text for t in triggers}
    assert matched == {"is installing", "installation is underway"}


# --- Identity: search seed alone cannot create evidence identity ---


def test_airport_search_context_requires_nonblank_name():
    with pytest.raises(AirportSearchContextError):
        AirportSearchContext(name="")
    with pytest.raises(AirportSearchContextError):
        AirportSearchContext(name="   ")


def test_concept_term_required():
    fragment = _fragment(LCY_FRAGMENT_1_TEXT)
    with pytest.raises(ValueError):
        detect_temporal_triggers(fragment, airport_context=LCY_CONTEXT, concept_term="")


def test_airport_context_is_plain_search_context_not_evidence():
    """DiscoveryTrigger carries airport_context verbatim but never folds it
    into anything resembling CandidateFragment's own evidence fields -
    this module never imports EvidenceBag/evaluate_attachment_for_candidates
    at all (see architectural-safety test file)."""
    fragment = _fragment(LCY_FRAGMENT_1_TEXT, artifact_identity=LCY_ARTIFACT_IDENTITY, source_locator=LCY_SOURCE_LOCATOR)
    trigger = detect_temporal_triggers(fragment, airport_context=LCY_CONTEXT, concept_term="EMAS")[0]
    assert trigger.airport_context is LCY_CONTEXT


# --- Provenance ---


def test_provenance_fields_preserved_exactly():
    fragment = _fragment(
        LCY_FRAGMENT_1_TEXT, artifact_identity="artifact:xyz", source_locator="page:9;chars:1-2"
    )
    trigger = detect_temporal_triggers(fragment, airport_context=LCY_CONTEXT, concept_term="EMAS")[0]
    assert trigger.artifact_identity == "artifact:xyz"
    assert trigger.source_locator == "page:9;chars:1-2"


def test_reason_is_human_readable_and_cites_matched_text():
    fragment = _fragment(LCY_FRAGMENT_1_TEXT, artifact_identity=LCY_ARTIFACT_IDENTITY, source_locator=LCY_SOURCE_LOCATOR)
    trigger = detect_temporal_triggers(fragment, airport_context=LCY_CONTEXT, concept_term="EMAS")[0]
    assert "is installing" in trigger.reason
    assert LCY_SOURCE_LOCATOR in trigger.reason
    assert LCY_ARTIFACT_IDENTITY in trigger.reason


# --- N. Multilingual seam ---


def test_phrase_vocabulary_is_injectable_without_changing_core_logic():
    fragment = _fragment("Instalando EMAS en el aeropuerto ahora mismo.")
    # No trigger with the default (English) vocabulary.
    assert detect_temporal_triggers(fragment, airport_context=LCY_CONTEXT, concept_term="EMAS") == ()

    # A caller-supplied alternate vocabulary pack (not shipped in V1)
    # changes detection without any change to this module's code.
    spanish_pack = ("instalando",)
    triggers = detect_temporal_triggers(
        fragment, airport_context=LCY_CONTEXT, concept_term="EMAS", phrase_vocabulary=spanish_pack
    )
    assert len(triggers) == 1
    assert triggers[0].matched_text == "Instalando"
    assert triggers[0].trigger_kind == TemporalTriggerKind.TEMPORAL_ACTIVITY_INSTALLING


# --- Determinism ---


def test_detection_and_planning_are_deterministic():
    fragment = _fragment(LCY_FRAGMENT_1_TEXT, artifact_identity=LCY_ARTIFACT_IDENTITY, source_locator=LCY_SOURCE_LOCATOR)
    first = detect_temporal_triggers(fragment, airport_context=LCY_CONTEXT, concept_term="EMAS")
    second = detect_temporal_triggers(fragment, airport_context=LCY_CONTEXT, concept_term="EMAS")
    assert first == second
    assert plan_follow_up_queries(first[0]) == plan_follow_up_queries(second[0])


def test_no_match_returns_empty_tuple_not_none():
    fragment = _fragment("Nothing relevant here at all.")
    result = detect_temporal_triggers(fragment, airport_context=LCY_CONTEXT, concept_term="EMAS")
    assert result == ()
    assert isinstance(result, tuple)
