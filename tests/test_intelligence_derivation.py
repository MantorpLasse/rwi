from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    Document,
    Fact,
    FactStatus,
    FindingType,
    Intelligence,
    IntelligenceStatus,
    Observation,
    ObservationType,
    PublishingSource,
    Verification,
    VerificationStatus,
)
from app.repositories import IntelligenceRepository
from app.services import IntelligenceDerivationError, IntelligenceDerivationService


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


def add_finding_type(
    session: Session, *, key: str = "CURRENT_EMAS", active: bool = True
) -> FindingType:
    finding_type = FindingType(
        key=key,
        name=key.replace("_", " ").title(),
        description="Governed derivation test type",
        category="STATUS",
        is_active=active,
    )
    session.add(finding_type)
    session.commit()
    return finding_type


def add_fact(session: Session, key: str = "airport.emas.product") -> Fact:
    verification = Verification(
        observation=Observation(
            document=Document(
                source=PublishingSource(name=f"Publisher {key}"),
                title=f"Document {key}",
            ),
            observation_type=ObservationType(
                key=key,
                display_label=key,
                description="Derivation test evidence",
                value_type="raw_text",
            ),
            raw_value="EMASMAX",
        ),
        status=VerificationStatus.ACCEPTED,
    )
    fact = Fact(
        fact_type_key=key,
        subject_type="airport",
        subject_identifier="FAA:JFK",
        accepted_value="EMASMAX",
        status=FactStatus.ACCEPTED,
        supporting_verifications=[verification],
    )
    session.add(fact)
    session.commit()
    return fact


def derive(service: IntelligenceDerivationService, fact_ids, **overrides):
    values = {
        "finding_type_key": "CURRENT_EMAS",
        "title": "Current EMAS established",
        "summary": "Current accepted Facts establish an EMAS installation.",
    }
    values.update(overrides)
    return service.derive(fact_ids=fact_ids, **values)


def intelligence_count(session: Session) -> int:
    return session.scalar(select(func.count(Intelligence.id))) or 0


def test_successful_derivation_creates_one_immutable_intelligence(engine):
    with Session(engine) as session:
        finding_type = add_finding_type(session)
        fact = add_fact(session)
        original_value = fact.accepted_value

        intelligence = derive(IntelligenceDerivationService(session), [fact.id])

        assert intelligence.id is not None
        assert intelligence.finding_type is finding_type
        assert intelligence.title == "Current EMAS established"
        assert intelligence.status is IntelligenceStatus.ACTIVE
        assert intelligence.supporting_facts == [fact]
        assert intelligence.derived_at is not None
        assert intelligence_count(session) == 1
        assert fact.accepted_value == original_value

        intelligence.summary = "Changed"
        with pytest.raises(ValueError, match="immutable"):
            session.commit()


def test_multiple_current_accepted_facts_support_one_intelligence(engine):
    with Session(engine) as session:
        add_finding_type(session)
        first = add_fact(session)
        second = add_fact(session, "airport.emas.system_count")

        intelligence = derive(
            IntelligenceDerivationService(session), [first.id, second.id]
        )

        assert intelligence.supporting_facts == [first, second]
        assert intelligence_count(session) == 1


def test_empty_and_duplicate_fact_ids_are_rejected(engine):
    with Session(engine) as session:
        add_finding_type(session)
        fact = add_fact(session)
        service = IntelligenceDerivationService(session)

        with pytest.raises(IntelligenceDerivationError) as empty:
            derive(service, [])
        assert empty.value.code == "fact_ids_required"

        with pytest.raises(IntelligenceDerivationError) as duplicate:
            derive(service, [fact.id, fact.id])
        assert duplicate.value.code == "duplicate_fact_ids"
        assert intelligence_count(session) == 0


def test_missing_fact_is_rejected(engine):
    with Session(engine) as session:
        add_finding_type(session)
        with pytest.raises(IntelligenceDerivationError) as error:
            derive(IntelligenceDerivationService(session), [999999])
        assert error.value.code == "fact_not_found"


def test_archived_fact_is_rejected(engine):
    with Session(engine) as session:
        add_finding_type(session)
        original = add_fact(session)
        archived = Fact(
            fact_type_key=original.fact_type_key,
            subject_type=original.subject_type,
            subject_identifier=original.subject_identifier,
            accepted_value=original.accepted_value,
            status=FactStatus.RETIRED,
            supersedes=original,
            supporting_verifications=list(original.supporting_verifications),
        )
        session.add(archived)
        session.commit()

        with pytest.raises(IntelligenceDerivationError) as error:
            derive(IntelligenceDerivationService(session), [archived.id])
        assert error.value.code == "fact_not_accepted"


def test_superseded_fact_is_rejected(engine):
    with Session(engine) as session:
        add_finding_type(session)
        original = add_fact(session)
        replacement = Fact(
            fact_type_key=original.fact_type_key,
            subject_type=original.subject_type,
            subject_identifier=original.subject_identifier,
            accepted_value="greenEMAS",
            status=FactStatus.ACCEPTED,
            supersedes=original,
            supporting_verifications=list(original.supporting_verifications),
        )
        session.add(replacement)
        session.commit()

        with pytest.raises(IntelligenceDerivationError) as error:
            derive(IntelligenceDerivationService(session), [original.id])
        assert error.value.code == "fact_not_current"
        assert intelligence_count(session) == 0


def test_inactive_and_unknown_finding_types_are_rejected(engine):
    with Session(engine) as session:
        add_finding_type(session, active=False)
        fact = add_fact(session)
        service = IntelligenceDerivationService(session)

        with pytest.raises(IntelligenceDerivationError) as inactive:
            derive(service, [fact.id])
        assert inactive.value.code == "finding_type_inactive"

        with pytest.raises(IntelligenceDerivationError) as unknown:
            derive(service, [fact.id], finding_type_key="UNKNOWN_TYPE")
        assert unknown.value.code == "finding_type_not_found"
        assert intelligence_count(session) == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [("title", ""), ("title", "  "), ("summary", ""), ("summary", "\n")],
)
def test_title_and_summary_must_contain_text(engine, field, value):
    with Session(engine) as session:
        add_finding_type(session)
        fact = add_fact(session)
        with pytest.raises(IntelligenceDerivationError) as error:
            derive(
                IntelligenceDerivationService(session),
                [fact.id],
                **{field: value},
            )
        assert error.value.code == f"{field}_required"


def test_repeated_derivation_creates_new_rows_through_repository(engine, monkeypatch):
    with Session(engine) as session:
        add_finding_type(session)
        fact = add_fact(session)
        calls = []
        original_create = IntelligenceRepository.create

        def tracking_create(repository, intelligence):
            calls.append(intelligence)
            return original_create(repository, intelligence)

        monkeypatch.setattr(IntelligenceRepository, "create", tracking_create)
        service = IntelligenceDerivationService(session)
        first = derive(service, [fact.id])
        second = derive(service, [fact.id])

        assert first.id != second.id
        assert len(calls) == 2
        assert intelligence_count(session) == 2


def test_repository_failure_rolls_back_without_modifying_evidence(engine, monkeypatch):
    with Session(engine) as session:
        add_finding_type(session)
        fact = add_fact(session)
        verification = fact.supporting_verifications[0]
        observation = verification.observation
        document = observation.document
        source = document.source
        original = (
            fact.accepted_value,
            verification.status,
            observation.raw_value,
            document.title,
            source.name,
        )

        def fail_create(_repository, _intelligence):
            raise RuntimeError("persistence failure")

        monkeypatch.setattr(IntelligenceRepository, "create", fail_create)
        with pytest.raises(RuntimeError, match="persistence failure"):
            derive(IntelligenceDerivationService(session), [fact.id])

        assert intelligence_count(session) == 0
        assert (
            session.get(Fact, fact.id).accepted_value,
            session.get(Verification, verification.id).status,
            session.get(Observation, observation.id).raw_value,
            session.get(Document, document.id).title,
            session.get(PublishingSource, source.id).name,
        ) == original
