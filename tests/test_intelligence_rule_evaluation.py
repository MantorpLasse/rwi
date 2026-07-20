from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.intelligence.rules import CurrentEmasRule, IntelligenceRuleRegistry, RuleResult
from app.models import (
    Document,
    Fact,
    FactStatus,
    FindingType,
    Intelligence,
    Observation,
    ObservationType,
    PublishingSource,
    Verification,
    VerificationStatus,
)
from app.repositories import FactRepository, IntelligenceRepository
from app.services import (
    IntelligenceDerivationError,
    IntelligenceDerivationService,
    IntelligenceRuleEvaluationService,
)


@pytest.fixture
def engine():
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    event.listen(
        test_engine,
        "connect",
        lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"),
    )
    Base.metadata.create_all(test_engine)
    yield test_engine
    test_engine.dispose()


def add_fact(
    session: Session,
    key="airport.emas.product",
    *,
    subject_identifier="FAA:JFK",
    value="EMASMAX",
) -> Fact:
    verification = Verification(
        observation=Observation(
            document=Document(
                source=PublishingSource(name=f"Publisher {key} {subject_identifier}"),
                title=f"Document {key} {subject_identifier}",
            ),
            observation_type=ObservationType(
                key=f"{key}.{subject_identifier.lower()}",
                display_label=key,
                description="Rule evaluation evidence",
                value_type="raw_text",
            ),
            raw_value=value,
        ),
        status=VerificationStatus.ACCEPTED,
    )
    item = Fact(
        fact_type_key=key,
        subject_type="airport",
        subject_identifier=subject_identifier,
        accepted_value=value,
        status=FactStatus.ACCEPTED,
        supporting_verifications=[verification],
    )
    session.add(item)
    session.commit()
    return item


def add_finding_type(session: Session) -> FindingType:
    finding_type = FindingType(
        key="CURRENT_EMAS",
        name="Current EMAS",
        description="Current EMAS established.",
        category="STATUS",
    )
    session.add(finding_type)
    session.commit()
    return finding_type


class NoResultRule:
    key = "NO_RESULT"
    name = "No result"
    description = "Produces no findings."
    finding_type_key = "CURRENT_EMAS"

    def evaluate(self, _facts):
        return ()


def test_evaluates_all_rules_by_default_and_selected_rules_only(engine):
    with Session(engine) as session:
        support = add_fact(session)
        registry = IntelligenceRuleRegistry([NoResultRule(), CurrentEmasRule()])
        service = IntelligenceRuleEvaluationService(session, registry)

        all_report = service.evaluate()
        selected_report = service.evaluate(["NO_RESULT"])

        assert all_report.rules_evaluated == ("CURRENT_EMAS", "NO_RESULT")
        assert len(all_report.results_produced) == 1
        assert all_report.results_produced[0].fact_ids == (support.id,)
        assert selected_report.rules_evaluated == ("NO_RESULT",)
        assert selected_report.results_produced == ()


def test_unknown_requested_rule_is_a_stable_validation_error(engine):
    with Session(engine) as session:
        report = IntelligenceRuleEvaluationService(session).evaluate(["UNKNOWN"])

        assert report.rules_evaluated == ()
        assert report.results_produced == ()
        assert report.validation_errors[0].code == "unknown_rule"
        assert report.validation_errors[0].rule_key == "UNKNOWN"


def test_dry_run_uses_current_repository_facts_without_creating_intelligence(engine, monkeypatch):
    with Session(engine) as session:
        current = add_fact(session)
        historical = add_fact(session, subject_identifier="FAA:BOS")
        replacement = Fact(
            fact_type_key=historical.fact_type_key,
            subject_type=historical.subject_type,
            subject_identifier=historical.subject_identifier,
            accepted_value="greenEMAS",
            status=FactStatus.ACCEPTED,
            supersedes=historical,
            supporting_verifications=list(historical.supporting_verifications),
        )
        session.add(replacement)
        session.commit()
        retired_original = add_fact(session, subject_identifier="FAA:ATL")
        retirement = Fact(
            fact_type_key=retired_original.fact_type_key,
            subject_type=retired_original.subject_type,
            subject_identifier=retired_original.subject_identifier,
            accepted_value=retired_original.accepted_value,
            status=FactStatus.RETIRED,
            supersedes=retired_original,
            supporting_verifications=list(retired_original.supporting_verifications),
        )
        session.add(retirement)
        session.commit()
        calls = []
        original_list_current = FactRepository.list_current

        def tracking_list_current(repository, *args, **kwargs):
            facts = original_list_current(repository, *args, **kwargs)
            calls.append(tuple(fact.id for fact in facts))
            return facts

        monkeypatch.setattr(FactRepository, "list_current", tracking_list_current)
        report = IntelligenceRuleEvaluationService(session).evaluate(persist=False)

        assert len(calls) == 1
        assert historical.id not in calls[0]
        assert retired_original.id not in calls[0]
        assert retirement.id not in calls[0]
        assert {result.fact_ids for result in report.results_produced} == {
            (current.id,),
            (replacement.id,),
        }
        assert report.intelligence_created == ()
        assert session.scalar(select(func.count(Intelligence.id))) == 0


def test_persist_delegates_each_result_to_derivation_service(engine, monkeypatch):
    with Session(engine) as session:
        first = add_fact(session, subject_identifier="FAA:BOS")
        second = add_fact(session, subject_identifier="FAA:JFK")
        calls = []

        def fake_derive(_service, finding_type_key, fact_ids, **values):
            calls.append((finding_type_key, tuple(fact_ids), values))
            return SimpleNamespace(id=len(calls))

        def forbidden_create(*_args, **_kwargs):
            raise AssertionError("evaluation service persisted Intelligence directly")

        monkeypatch.setattr(IntelligenceDerivationService, "derive", fake_derive)
        monkeypatch.setattr(IntelligenceRepository, "create", forbidden_create)
        report = IntelligenceRuleEvaluationService(session).evaluate(persist=True)

        assert [call[1] for call in calls] == [(first.id,), (second.id,)]
        assert len(report.results_produced) == 2
        assert len(report.intelligence_created) == 2
        assert report.validation_errors == ()


def test_real_persist_creates_multiple_traceable_intelligence_records(engine):
    with Session(engine) as session:
        add_finding_type(session)
        first = add_fact(session, subject_identifier="FAA:BOS")
        second = add_fact(session, subject_identifier="FAA:JFK")

        report = IntelligenceRuleEvaluationService(session).evaluate(persist=True)

        assert len(report.intelligence_created) == 2
        assert {
            tuple(fact.id for fact in intelligence.supporting_facts)
            for intelligence in report.intelligence_created
        } == {(first.id,), (second.id,)}
        assert session.scalar(select(func.count(Intelligence.id))) == 2


def test_governed_persistence_failure_is_reported_after_completed_results(engine, monkeypatch):
    with Session(engine) as session:
        add_fact(session, subject_identifier="FAA:BOS")
        add_fact(session, subject_identifier="FAA:JFK")
        completed = SimpleNamespace(id=1)
        calls = 0

        def partially_failing_derive(_service, _finding_type_key, _fact_ids, **_values):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise IntelligenceDerivationError(
                    "finding_type_inactive", "FindingType is inactive."
                )
            return completed

        monkeypatch.setattr(
            IntelligenceDerivationService, "derive", partially_failing_derive
        )
        report = IntelligenceRuleEvaluationService(session).evaluate(persist=True)

        assert report.intelligence_created == (completed,)
        assert len(report.results_produced) == 2
        assert report.validation_errors[0].code == "finding_type_inactive"
        assert report.validation_errors[0].rule_key == "CURRENT_EMAS"


def test_invalid_and_duplicate_rule_results_are_validated_and_deduplicated(engine):
    class InvalidRule(CurrentEmasRule):
        key = "INVALID"

        def evaluate(self, facts):
            valid = RuleResult(
                finding_type_key=self.finding_type_key,
                fact_ids=(facts[0].id,),
                title="Valid",
                summary="Valid summary",
                rule_key=self.key,
            )
            invalid = RuleResult(
                finding_type_key=self.finding_type_key,
                fact_ids=(999999,),
                title="Invalid",
                summary="Invalid support",
                rule_key=self.key,
            )
            return (valid, valid, invalid)

    with Session(engine) as session:
        add_fact(session)
        registry = IntelligenceRuleRegistry([InvalidRule()])
        report = IntelligenceRuleEvaluationService(session, registry).evaluate()

        assert len(report.results_produced) == 1
        assert report.results_produced[0].title == "Valid"
        assert [error.code for error in report.validation_errors] == [
            "non_current_fact"
        ]
