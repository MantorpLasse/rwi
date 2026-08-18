"""Isolated, in-memory end-to-end proof for the MSP discovery-provider
pilot (docs/product/msp-authoritative-discovery-provider-pilot.md).

Chains the real, recorded MAC Granicus EMAS-procurement-memo fixture
through the full, already-committed pipeline:

    PDF bytes (real fixture)
        -> app.acquisition.mac_granicus_extractor.extract_candidate_fragment
        -> app.services.discovery_candidate_fragment.candidate_fragment_to_evidence_bag
        -> app.services.evidence_attachment_guard.evaluate_attachment_for_candidates
        -> app.services.discovery_evidence_persistence.persist_discovery_fragment

Never touches the real database - every test builds its own isolated
in-memory SQLite database. Task S13/S14/S15/S16/S20.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.acquisition.mac_granicus_extractor import extract_candidate_fragment
from app.database import Base
from app.models import Airport, Installation, PhysicalInstallationIdentity, Runway, RunwayEnd, Signal, Source, SourceAssertion
from app.services.discovery_candidate_fragment import CandidateFragment
from app.services.discovery_evidence_persistence import DiscoverySourceMetadata, persist_discovery_fragment
from app.services.evidence_attachment_guard import AttachmentOutcome, CandidateAirport

FIXTURE_PDF = (Path(__file__).parent / "fixtures" / "mac_granicus_emas_procurement_memo_sample.pdf").read_bytes()

ARTIFACT_IDENTITY = "mac.granicus.document.4.2349.105406"
SOURCE_LOCATOR = "pd&e-2024-09-03-item-2.3.2"
DOCUMENT_URL = "https://metroairports.granicus.com/MetaViewer.php?view_id=4&clip_id=2349&meta_id=105406"


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _seed_msp_and_sfo(session: Session) -> tuple[Airport, Airport]:
    msp = Airport(name="Minneapolis St. Paul International", faa_code="MSP", iata_code="MSP", icao_code="KMSP", country="USA", city="Minneapolis")
    sfo = Airport(name="San Francisco International Airport", faa_code="SFO", iata_code="SFO", icao_code="KSFO", country="USA", city="San Francisco")
    session.add_all([msp, sfo])
    session.flush()
    for airport, runways in (
        (msp, {"12R/30L": ("12R", "30L"), "4/22": ("4", "22"), "12L/30R": ("12L", "30R"), "17/35": ("17", "35")}),
        (sfo, {"1R/19L": ("1R", "19L"), "1L/19R": ("1L", "19R"), "10L/28R": ("10L", "28R"), "10R/28L": ("10R", "28L")}),
    ):
        for designation, ends in runways.items():
            runway = Runway(airport_id=airport.id, designation=designation)
            session.add(runway)
            session.flush()
            for end in ends:
                session.add(RunwayEnd(runway_id=runway.id, designation=end))
    session.flush()
    return msp, sfo


def _candidate(airport: Airport, *, known_issuers=frozenset()) -> CandidateAirport:
    ends = {e.designation for r in airport.runways for e in r.runway_ends}
    pairs = {r.designation for r in airport.runways}
    return CandidateAirport(
        id=airport.id, name=airport.name,
        identifiers=frozenset({c for c in (airport.iata_code, airport.icao_code, airport.faa_code) if c}),
        canonical_runway_ends=frozenset(ends), canonical_runway_pairs=frozenset(pairs),
        known_issuers=known_issuers,
    )


def _real_fragment(**overrides):
    kwargs = dict(
        artifact_identity=ARTIFACT_IDENTITY, source_locator=SOURCE_LOCATOR,
        document_title="2.3.2. Engineered Material Arresting Systems (EMAS) Procurement Advance Deposit",
        url=DOCUMENT_URL,
    )
    kwargs.update(overrides)
    result = extract_candidate_fragment(FIXTURE_PDF, "application/pdf", **kwargs)
    assert result is not None
    fragment, _vendors = result
    return fragment


def test_isolated_persistence_confirms_for_msp_not_sfo():
    with Session(_engine()) as session:
        msp_row, sfo_row = _seed_msp_and_sfo(session)
        msp = _candidate(msp_row, known_issuers=frozenset({"Metropolitan Airports Commission"}))
        sfo = _candidate(sfo_row, known_issuers=frozenset({"San Francisco Airport Commission"}))
        fragment = _real_fragment()
        meta = DiscoverySourceMetadata(document_identity=ARTIFACT_IDENTITY, title=fragment.document_title, publisher="Metropolitan Airports Commission", url=DOCUMENT_URL)

        result = persist_discovery_fragment(session, meta, fragment, [sfo, msp])

        assert result.outcome == AttachmentOutcome.ATTACH_CONFIRMED
        assert result.attached_airport_id == msp_row.id
        assert result.attached_airport_id != sfo_row.id

        assertion = session.get(SourceAssertion, result.source_assertion_id)
        assert assertion.identity_guard_decision == "ATTACH_CONFIRMED"
        assert assertion.airport_id == msp_row.id
        assert assertion.raw_relevant_text == fragment.raw_text
        assert len(session.scalars(select(SourceAssertion)).all()) == 1


def test_no_signal_or_canonical_fact_rows_created():
    with Session(_engine()) as session:
        msp_row, sfo_row = _seed_msp_and_sfo(session)
        msp = _candidate(msp_row, known_issuers=frozenset({"Metropolitan Airports Commission"}))
        fragment = _real_fragment()
        meta = DiscoverySourceMetadata(document_identity=ARTIFACT_IDENTITY, title="EMAS memo")

        before_runways = session.query(Runway).count()
        before_ends = session.query(RunwayEnd).count()
        persist_discovery_fragment(session, meta, fragment, [msp])

        assert session.query(Signal).count() == 0
        assert session.query(Installation).count() == 0
        assert session.query(PhysicalInstallationIdentity).count() == 0
        assert session.query(Runway).count() == before_runways  # only the test's own seeded runways
        assert session.query(RunwayEnd).count() == before_ends


def test_idempotent_persistence_across_rediscovery():
    with Session(_engine()) as session:
        msp_row, _sfo_row = _seed_msp_and_sfo(session)
        msp = _candidate(msp_row, known_issuers=frozenset({"Metropolitan Airports Commission"}))
        meta = DiscoverySourceMetadata(document_identity=ARTIFACT_IDENTITY, title="EMAS memo")

        first = persist_discovery_fragment(session, meta, _real_fragment(), [msp])
        # Rediscovered later, e.g. a fresh archive scan re-encountering the
        # same clip_id/meta_id - the document_identity is unchanged because
        # it is derived from the archive's own addressing, never a search.
        second = persist_discovery_fragment(session, meta, _real_fragment(), [msp])

        assert first.source_id == second.source_id
        assert first.source_assertion_id == second.source_assertion_id
        assert second.source_created is False
        assert second.source_assertion_created is False
        assert len(session.scalars(select(Source)).all()) == 1
        assert len(session.scalars(select(SourceAssertion)).all()) == 1


def test_changed_fragment_text_creates_a_new_assertion_but_reuses_source():
    with Session(_engine()) as session:
        msp_row, _sfo_row = _seed_msp_and_sfo(session)
        msp = _candidate(msp_row, known_issuers=frozenset({"Metropolitan Airports Commission"}))
        meta = DiscoverySourceMetadata(document_identity=ARTIFACT_IDENTITY, title="EMAS memo")

        original = _real_fragment()
        revised = CandidateFragment(
            artifact_identity=original.artifact_identity,
            source_locator=original.source_locator,
            raw_text=original.raw_text + "\n\nAddendum: schedule revised.",
            issuers=original.issuers, runway_ends=original.runway_ends, runway_pairs=original.runway_pairs,
        )
        assert revised.fragment_hash != original.fragment_hash

        first = persist_discovery_fragment(session, meta, original, [msp])
        second = persist_discovery_fragment(session, meta, revised, [msp])

        assert second.source_created is False
        assert second.source_assertion_created is True
        assert second.source_assertion_id != first.source_assertion_id
        assert len(session.scalars(select(SourceAssertion)).all()) == 2


def test_no_hidden_commit_rollback_undoes_everything():
    with Session(_engine()) as session:
        msp_row, _sfo_row = _seed_msp_and_sfo(session)
        session.commit()  # only the airport/runway seed is committed
        msp = _candidate(msp_row, known_issuers=frozenset({"Metropolitan Airports Commission"}))
        meta = DiscoverySourceMetadata(document_identity=ARTIFACT_IDENTITY, title="EMAS memo")

        persist_discovery_fragment(session, meta, _real_fragment(), [msp])
        session.rollback()

        assert session.scalars(select(Source)).all() == []
        assert session.scalars(select(SourceAssertion)).all() == []
