from dataclasses import FrozenInstanceError

import pytest

from app.intelligence.rules import (
    CurrentEmasRule,
    IntelligenceRuleRegistry,
    RuleResult,
)
from app.models import Fact, FactStatus


def fact(
    fact_id: int,
    *,
    fact_type_key: str = "airport.emas.product",
    subject_type: str = "airport",
    subject_identifier: str = "FAA:JFK",
    accepted_value: str = "EMASMAX",
) -> Fact:
    return Fact(
        id=fact_id,
        fact_type_key=fact_type_key,
        subject_type=subject_type,
        subject_identifier=subject_identifier,
        accepted_value=accepted_value,
        status=FactStatus.ACCEPTED,
    )


def test_rule_result_is_immutable_non_persistent_value_object():
    result = RuleResult(
        finding_type_key="CURRENT_EMAS",
        fact_ids=(1,),
        title="Current EMAS",
        summary="A deterministic finding.",
        rule_key="CURRENT_EMAS",
    )

    with pytest.raises(FrozenInstanceError):
        result.title = "Changed"
    assert not hasattr(result, "__table__")


def test_current_emas_rule_exposes_stable_metadata():
    rule = CurrentEmasRule()
    assert rule.key == "CURRENT_EMAS"
    assert rule.name == "Current EMAS"
    assert rule.description
    assert rule.finding_type_key == "CURRENT_EMAS"
    assert rule.required_fact_type_key == "airport.emas.product"


def test_registry_rejects_duplicates_orders_deterministically_and_handles_unknown():
    class LaterRule(CurrentEmasRule):
        key = "ZZZ_RULE"

    class EarlierRule(CurrentEmasRule):
        key = "AAA_RULE"

    earlier = EarlierRule()
    later = LaterRule()
    registry = IntelligenceRuleRegistry([later, earlier])

    assert registry.list() == [earlier, later]
    assert registry.get_by_key("AAA_RULE") is earlier
    assert registry.get_by_key("UNKNOWN") is None
    with pytest.raises(ValueError, match="Duplicate Intelligence rule key"):
        registry.register(EarlierRule())


def test_current_emas_produces_minimal_deterministic_result_without_mutation():
    rule = CurrentEmasRule()
    lower_id = fact(2)
    higher_id = fact(9, accepted_value="greenEMAS")
    before = [(item.id, item.accepted_value) for item in (lower_id, higher_id)]

    first = rule.evaluate([higher_id, lower_id])
    second = rule.evaluate([lower_id, higher_id])

    assert first == second
    assert len(first) == 1
    assert first[0].fact_ids == (2,)
    assert first[0].finding_type_key == "CURRENT_EMAS"
    assert first[0].rule_key == "CURRENT_EMAS"
    assert first[0].title == "Current EMAS established for airport FAA:JFK"
    assert first[0].summary == (
        "A current accepted airport.emas.product Fact establishes an EMAS "
        "installation for airport FAA:JFK."
    )
    assert [(item.id, item.accepted_value) for item in (lower_id, higher_id)] == before


def test_current_emas_groups_subjects_orders_results_and_ignores_unrelated_types():
    rule = CurrentEmasRule()
    bos = fact(4, subject_identifier="FAA:BOS")
    jfk = fact(3, subject_identifier="FAA:JFK")
    unrelated = fact(
        1,
        fact_type_key="airport.emas.system_count",
        accepted_value="2",
    )

    results = rule.evaluate([jfk, unrelated, bos, bos])

    assert [result.fact_ids for result in results] == [(4,), (3,)]
    assert [result.title for result in results] == [
        "Current EMAS established for airport FAA:BOS",
        "Current EMAS established for airport FAA:JFK",
    ]
    assert len(set(results)) == 2


def test_current_emas_returns_no_result_without_governed_product_fact():
    rule = CurrentEmasRule()
    assert rule.evaluate(
        [fact(1, fact_type_key="airport.emas.system_count", accepted_value="2")]
    ) == ()
