"""Tests for app/services/discovery_evidence_persistence.py
(docs/architecture/ai-discovery-governed-evidence-persistence-report.md).

Isolated, in-memory SQLite databases only - never the real one. Never
commits inside the service itself; every test controls its own
transaction explicitly, proving the service's own "caller owns the
transaction" contract along the way."""
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app import models  # noqa: F401 - registers all metadata
from app.models import Airport, Signal, Source, SourceAssertion
from app.services.discovery_candidate_fragment import CandidateFragment, DiscoveryContext
from app.services.discovery_evidence_persistence import (
    DiscoveryPersistenceResult,
    DiscoverySourceMetadata,
    persist_discovery_fragment,
)
from app.services.evidence_attachment_guard import AttachmentOutcome, CandidateAirport


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _seed_airport(session, *, faa_code, name, city=None) -> Airport:
    airport = Airport(name=name, faa_code=faa_code, country="USA", city=city)
    session.add(airport)
    session.flush()
    return airport


def _candidate(airport: Airport, **kwargs) -> CandidateAirport:
    return CandidateAirport(id=airport.id, name=airport.name, **kwargs)


def _artifact(name: str) -> str:
    return f"test-artifact:{name}"


def _meta(document_identity: str, title: str = "Test document") -> DiscoverySourceMetadata:
    return DiscoverySourceMetadata(document_identity=document_identity, title=title)


# ---------------------------------------------------------------------------
# A. SFO/MSP cross-airport false positive - the mandatory case
# ---------------------------------------------------------------------------

def test_case_A_sfo_msp_persists_for_msp_not_sfo():
    with Session(_engine()) as session:
        sfo_row = _seed_airport(session, faa_code="SFO", name="San Francisco International Airport", city="San Francisco")
        msp_row = _seed_airport(session, faa_code="MSP", name="Minneapolis-St. Paul International Airport", city="Minneapolis")
        sfo = _candidate(sfo_row, identifiers=frozenset({"SFO", "KSFO"}), known_issuers=frozenset({"San Francisco Airport Commission"}))
        msp = _candidate(
            msp_row, identifiers=frozenset({"MSP", "KMSP"}),
            canonical_runway_ends=frozenset({"30L"}), known_issuers=frozenset({"Metropolitan Airports Commission"}),
        )

        fragment = CandidateFragment(
            artifact_identity=_artifact("msp-memo"), source_locator="p1-p2",
            raw_text="Metropolitan Airports Commission. Runway 30L EMAS. Sole source procurement with Runway Safe.",
            issuers=frozenset({"Metropolitan Airports Commission"}),
            runway_ends=frozenset({"30L"}),
            discovery_context=DiscoveryContext(search_query="SFO EMAS Runway Safe 2026 contract", seed_airport="SFO"),
        )

        result = persist_discovery_fragment(session, _meta("msp-memo-doc"), fragment, [sfo, msp])

        assert isinstance(result, DiscoveryPersistenceResult)
        assert result.outcome == AttachmentOutcome.ATTACH_CONFIRMED
        assert result.attached_airport_id == msp_row.id
        assert result.attached_airport_id != sfo_row.id

        assertion = session.get(SourceAssertion, result.source_assertion_id)
        assert assertion.airport_id == msp_row.id
        assert assertion.identity_guard_decision == "ATTACH_CONFIRMED"
        assert assertion.raw_relevant_text == fragment.raw_text  # original evidence preserved verbatim

        # Exactly one SourceAssertion created - not one per candidate.
        assert session.scalar(select(SourceAssertion)).id == result.source_assertion_id
        assert len(session.scalars(select(SourceAssertion)).all()) == 1


# ---------------------------------------------------------------------------
# B-E. genuine SFO / BOS / ORH / embedded-identifier cases
# ---------------------------------------------------------------------------

def test_case_B_genuine_sfo_confirms_and_attaches():
    with Session(_engine()) as session:
        sfo_row = _seed_airport(session, faa_code="SFO", name="San Francisco International Airport")
        sfo = _candidate(sfo_row, identifiers=frozenset({"SFO"}), canonical_runway_pairs=frozenset({"1R/19L"}))
        fragment = CandidateFragment(
            artifact_identity=_artifact("sfo-page"), source_locator="body",
            raw_text="SFO RWY 1R/19L Rehabilitation. EMAS seam replacement.",
            airport_identifiers=frozenset({"SFO"}), runway_pairs=frozenset({"1R/19L"}),
        )
        result = persist_discovery_fragment(session, _meta("sfo-page-doc"), fragment, [sfo])
        assert result.outcome == AttachmentOutcome.ATTACH_CONFIRMED
        assert result.attached_airport_id == sfo_row.id


def test_case_C_bos_massport_confirms_and_attaches():
    with Session(_engine()) as session:
        bos_row = _seed_airport(session, faa_code="BOS", name="Boston Logan International Airport")
        bos = _candidate(bos_row, known_issuers=frozenset({"Massport"}), canonical_runway_ends=frozenset({"22R"}))
        fragment = CandidateFragment(
            artifact_identity=_artifact("bos-press"), source_locator="para-3",
            raw_text="Massport: Boston Logan has an EMAS system at Runway 22R.",
            issuers=frozenset({"Massport"}), runway_ends=frozenset({"22R"}),
        )
        result = persist_discovery_fragment(session, _meta("bos-press-doc"), fragment, [bos])
        assert result.outcome == AttachmentOutcome.ATTACH_CONFIRMED
        assert result.attached_airport_id == bos_row.id


def test_case_D_orh_mpa_confirms_and_attaches():
    with Session(_engine()) as session:
        orh_row = _seed_airport(session, faa_code="ORH", name="Worcester Regional")
        orh = _candidate(orh_row, known_issuers=frozenset({"MPA"}), canonical_runway_ends=frozenset({"29", "11"}))
        fragment = CandidateFragment(
            artifact_identity=_artifact("orh-w306"), source_locator="scope-1-2",
            raw_text="Replace Runway 29 Departure EMAS (R/W 11 End); Replace Runway 11 Departure EMAS (R/W 29 End).",
            issuers=frozenset({"MPA"}), runway_ends=frozenset({"29", "11"}),
            contract_identifiers=frozenset({"W306"}),
        )
        result = persist_discovery_fragment(session, _meta("orh-w306-doc"), fragment, [orh])
        assert result.outcome == AttachmentOutcome.ATTACH_CONFIRMED
        assert result.attached_airport_id == orh_row.id
        assertion = session.get(SourceAssertion, result.source_assertion_id)
        assert set(assertion.raw_runway_end_value.split(", ")) == {"11", "29"}


def test_case_E_embedded_identifier_confirms_alone():
    """Mirrors the existing USAspending embedded-FAA-Loc-ID pathway's own
    RESOLVED_EXISTING behavior, without retrofitting that importer -
    an identifier alone is sufficient (guard S7 step 3)."""
    with Session(_engine()) as session:
        orh_row = _seed_airport(session, faa_code="ORH", name="Worcester Regional")
        orh = _candidate(orh_row, identifiers=frozenset({"ORH"}))
        fragment = CandidateFragment(
            artifact_identity=_artifact("grant-1"), source_locator="record-1",
            raw_text="Airport (ORH), located in Worcester, Massachusetts.",
            airport_identifiers=frozenset({"ORH"}),
        )
        result = persist_discovery_fragment(session, _meta("grant-1-doc"), fragment, [orh])
        assert result.outcome == AttachmentOutcome.ATTACH_CONFIRMED
        assert result.attached_airport_id == orh_row.id


# ---------------------------------------------------------------------------
# F. city/state/provisional case
# ---------------------------------------------------------------------------

def test_case_F_provisional_attaches_airport_but_is_distinguishable_from_confirmed():
    with Session(_engine()) as session:
        orh_row = _seed_airport(session, faa_code="ORH", name="Worcester Regional", city="Worcester")
        orh = _candidate(orh_row, city_location="Worcester")
        fragment = CandidateFragment(
            artifact_identity=_artifact("city-state-only"), source_locator="p1",
            raw_text="A grant for an airport in Worcester, Massachusetts.",
            locations=frozenset({"Worcester"}),
        )
        result = persist_discovery_fragment(session, _meta("city-state-doc"), fragment, [orh])

        assert result.outcome == AttachmentOutcome.ATTACH_PROVISIONAL
        # Lifecycle design S15: provisional evidence DOES carry airport_id.
        assert result.attached_airport_id == orh_row.id

        assertion = session.get(SourceAssertion, result.source_assertion_id)
        assert assertion.identity_guard_decision == "ATTACH_PROVISIONAL"
        assert assertion.identity_guard_decision != "ATTACH_CONFIRMED"  # remains distinguishable


# ---------------------------------------------------------------------------
# G-H. Allegheny-like / Morristown-like insufficient-identity cases
# ---------------------------------------------------------------------------

def test_case_G_allegheny_like_insufficient_identity_preserved():
    with Session(_engine()) as session:
        agc_row = _seed_airport(session, faa_code="AGC", name="Allegheny County Airport", city="West Mifflin")
        agc = _candidate(agc_row, identifiers=frozenset({"AGC", "KAGC"}))
        fragment = CandidateFragment(
            artifact_identity=_artifact("allegheny-grant"), source_locator="p1",
            raw_text="Allegheny County Airport Authority. Runway safety area improvement grant.",
            # Recipient/organization name deliberately NOT placed in
            # airport_names (it identifies who received money, not the
            # airport - resolve_airport()'s own long-established rule).
        )
        result = persist_discovery_fragment(session, _meta("allegheny-grant-doc"), fragment, [agc])

        assert result.outcome == AttachmentOutcome.INSUFFICIENT_IDENTITY
        assert result.attached_airport_id is None

        assertion = session.get(SourceAssertion, result.source_assertion_id)
        assert assertion.airport_id is None
        assert assertion.raw_relevant_text == fragment.raw_text  # evidence still preserved
        assert assertion.identity_guard_decision == "INSUFFICIENT_IDENTITY"


def test_case_H_morristown_like_insufficient_identity_preserved():
    with Session(_engine()) as session:
        mmu_row = _seed_airport(session, faa_code="MMU", name="Morristown Municipal Airport", city="Morristown")
        mmu = _candidate(mmu_row, identifiers=frozenset({"MMU", "KMMU"}))
        fragment = CandidateFragment(
            artifact_identity=_artifact("morristown-grant"), source_locator="p1",
            raw_text="Morristown Municipal Airport Authority. Arresting system grant.",
        )
        result = persist_discovery_fragment(session, _meta("morristown-grant-doc"), fragment, [mmu])

        assert result.outcome == AttachmentOutcome.INSUFFICIENT_IDENTITY
        assert result.attached_airport_id is None
        assert session.get(SourceAssertion, result.source_assertion_id).airport_id is None


# ---------------------------------------------------------------------------
# I. multi-airport REVIEW_REQUIRED - never pick one candidate
# ---------------------------------------------------------------------------

def test_case_I_multi_airport_review_required_persists_once_with_no_airport_chosen():
    with Session(_engine()) as session:
        bos_row = _seed_airport(session, faa_code="BOS", name="Boston Logan International Airport")
        orh_row = _seed_airport(session, faa_code="ORH", name="Worcester Regional")
        bos = _candidate(bos_row, known_issuers=frozenset({"Massport"}))
        orh = _candidate(orh_row, known_issuers=frozenset({"Massport"}))

        fragment = CandidateFragment(
            artifact_identity=_artifact("massport-bill"), source_locator="para-7",
            raw_text="Massport capital improvement bill covering Logan and Worcester Regional airfield safety work.",
            issuers=frozenset({"Massport"}),
        )
        result = persist_discovery_fragment(session, _meta("massport-bill-doc"), fragment, [bos, orh])

        assert result.outcome == AttachmentOutcome.REVIEW_REQUIRED
        assert result.attached_airport_id is None

        # Exactly one SourceAssertion, not one per candidate.
        assert session.scalars(select(SourceAssertion)).all().__len__() == 1
        assertion = session.get(SourceAssertion, result.source_assertion_id)
        assert assertion.airport_id is None
        assert assertion.identity_guard_decision == "REVIEW_REQUIRED"
        # Both candidates' own reasoning preserved for later human review.
        assert str(bos_row.id) in assertion.identity_guard_reason
        assert str(orh_row.id) in assertion.identity_guard_reason


# ---------------------------------------------------------------------------
# J. international / native-language fragment
# ---------------------------------------------------------------------------

def test_case_J_international_haneda_confirms():
    with Session(_engine()) as session:
        hnd_row = _seed_airport(session, faa_code=None, name="Tokyo International Airport")
        hnd_row.icao_code = "RJTT"
        session.flush()
        haneda = _candidate(
            hnd_row, identifiers=frozenset({"RJTT"}), aliases=frozenset({"羽田空港"}),
            canonical_runway_pairs=frozenset({"16L/34R"}), known_issuers=frozenset({"Ministry of Land, Infrastructure, Transport and Tourism"}),
        )
        original_text = "羽田空港 滑走路16L/34R エンジニアド・マテリアル・アレスティング・システム（EMAS）"
        fragment = CandidateFragment(
            artifact_identity=_artifact("haneda-procurement"), source_locator="p1",
            raw_text=original_text,
            airport_identifiers=frozenset({"RJTT"}), airport_names=frozenset({"羽田空港"}),
            runway_pairs=frozenset({"16L/34R"}),
            issuers=frozenset({"Ministry of Land, Infrastructure, Transport and Tourism"}),
            language="ja",
        )
        result = persist_discovery_fragment(session, _meta("haneda-doc"), fragment, [haneda])

        assert result.outcome == AttachmentOutcome.ATTACH_CONFIRMED
        assert result.attached_airport_id == hnd_row.id
        assertion = session.get(SourceAssertion, result.source_assertion_id)
        assert assertion.raw_relevant_text == original_text  # original, untranslated text preserved


# ---------------------------------------------------------------------------
# K. same fragment rediscovered through a different search query
# ---------------------------------------------------------------------------

def test_case_K_same_fragment_rediscovered_via_different_query_is_idempotent():
    with Session(_engine()) as session:
        bos_row = _seed_airport(session, faa_code="BOS", name="Boston Logan International Airport")
        bos = _candidate(bos_row, identifiers=frozenset({"BOS"}))

        def _fragment(query):
            return CandidateFragment(
                artifact_identity=_artifact("bos-doc"), source_locator="p1",
                raw_text="BOS EMAS work.", airport_identifiers=frozenset({"BOS"}),
                discovery_context=DiscoveryContext(search_query=query),
            )

        first = persist_discovery_fragment(session, _meta("bos-doc-id"), _fragment("BOS EMAS 2026"), [bos])
        second = persist_discovery_fragment(session, _meta("bos-doc-id"), _fragment("Boston Logan runway safety"), [bos])

        assert first.source_assertion_created is True
        assert second.source_assertion_created is False
        assert first.source_assertion_id == second.source_assertion_id
        assert first.source_created is True
        assert second.source_created is False
        assert first.source_id == second.source_id

        assert len(session.scalars(select(Source)).all()) == 1
        assert len(session.scalars(select(SourceAssertion)).all()) == 1


# ---------------------------------------------------------------------------
# L. changed fragment text
# ---------------------------------------------------------------------------

def test_case_L_changed_fragment_text_creates_a_new_assertion():
    with Session(_engine()) as session:
        bos_row = _seed_airport(session, faa_code="BOS", name="Boston Logan International Airport")
        bos = _candidate(bos_row, identifiers=frozenset({"BOS"}))

        fragment_v1 = CandidateFragment(
            artifact_identity=_artifact("bos-doc"), source_locator="p1",
            raw_text="BOS EMAS work, phase 1.", airport_identifiers=frozenset({"BOS"}),
        )
        fragment_v2 = CandidateFragment(
            artifact_identity=_artifact("bos-doc"), source_locator="p1",
            raw_text="BOS EMAS work, phase 1 - revised.", airport_identifiers=frozenset({"BOS"}),
        )
        assert fragment_v1.fragment_hash != fragment_v2.fragment_hash

        first = persist_discovery_fragment(session, _meta("bos-doc-id"), fragment_v1, [bos])
        second = persist_discovery_fragment(session, _meta("bos-doc-id"), fragment_v2, [bos])

        assert second.source_assertion_created is True
        assert second.source_assertion_id != first.source_assertion_id
        assert second.source_created is False  # same document, same Source reused
        assert len(session.scalars(select(SourceAssertion)).all()) == 2


# ---------------------------------------------------------------------------
# M. no Signal creation - mandatory regression
# ---------------------------------------------------------------------------

def test_case_M_no_signal_created_for_any_outcome():
    with Session(_engine()) as session:
        sfo_row = _seed_airport(session, faa_code="SFO", name="San Francisco International Airport")
        msp_row = _seed_airport(session, faa_code="MSP", name="Minneapolis-St. Paul International Airport")
        agc_row = _seed_airport(session, faa_code="AGC", name="Allegheny County Airport")
        sfo = _candidate(sfo_row, identifiers=frozenset({"SFO"}))
        msp = _candidate(msp_row, identifiers=frozenset({"MSP"}))
        agc = _candidate(agc_row)

        # ATTACH_CONFIRMED
        persist_discovery_fragment(
            session, _meta("doc-1"),
            CandidateFragment(artifact_identity=_artifact("doc1"), source_locator="p1", raw_text="SFO work", airport_identifiers=frozenset({"SFO"})),
            [sfo],
        )
        # REJECT_CROSS_AIRPORT (for sfo) / ATTACH_CONFIRMED (for msp) - via the priority selector
        persist_discovery_fragment(
            session, _meta("doc-2"),
            CandidateFragment(
                artifact_identity=_artifact("doc2"), source_locator="p1", raw_text="MSP work",
                airport_identifiers=frozenset({"MSP"}),
            ),
            [sfo, msp],
        )
        # INSUFFICIENT_IDENTITY
        persist_discovery_fragment(
            session, _meta("doc-3"),
            CandidateFragment(artifact_identity=_artifact("doc3"), source_locator="p1", raw_text="Vague arresting-system mention."),
            [agc],
        )

        assert session.scalars(select(Signal)).all() == []
        assert session.query(Signal).count() == 0


# ---------------------------------------------------------------------------
# Additional: no canonical fact creation, no hidden commit, source reuse
# across distinct fragments of the same document
# ---------------------------------------------------------------------------

def test_no_hidden_commit_rollback_undoes_everything():
    with Session(_engine()) as session:
        bos_row = _seed_airport(session, faa_code="BOS", name="Boston Logan International Airport")
        session.commit()  # only the airport seed is committed
        bos = _candidate(bos_row, identifiers=frozenset({"BOS"}))

        persist_discovery_fragment(
            session, _meta("doc-x"),
            CandidateFragment(artifact_identity=_artifact("x"), source_locator="p1", raw_text="BOS EMAS", airport_identifiers=frozenset({"BOS"})),
            [bos],
        )
        session.rollback()  # the service itself must never have committed

        assert session.scalars(select(Source)).all() == []
        assert session.scalars(select(SourceAssertion)).all() == []


def test_same_document_multiple_fragments_reuses_one_source():
    with Session(_engine()) as session:
        bos_row = _seed_airport(session, faa_code="BOS", name="Boston Logan International Airport")
        bos = _candidate(bos_row, identifiers=frozenset({"BOS"}))
        meta = _meta("bos-multi-fragment-doc")

        r1 = persist_discovery_fragment(
            session, meta,
            CandidateFragment(artifact_identity=_artifact("bosdoc"), source_locator="p1", raw_text="BOS fragment one", airport_identifiers=frozenset({"BOS"})),
            [bos],
        )
        r2 = persist_discovery_fragment(
            session, meta,
            CandidateFragment(artifact_identity=_artifact("bosdoc"), source_locator="p2", raw_text="BOS fragment two", airport_identifiers=frozenset({"BOS"})),
            [bos],
        )

        assert r1.source_id == r2.source_id
        assert r1.source_assertion_id != r2.source_assertion_id
        assert len(session.scalars(select(Source)).all()) == 1
        assert len(session.scalars(select(SourceAssertion)).all()) == 2


def test_no_canonical_fact_rows_created():
    from app.models import Installation, PhysicalInstallationIdentity, Runway, RunwayEnd

    with Session(_engine()) as session:
        before_airports = session.query(Airport).count()
        bos_row = _seed_airport(session, faa_code="BOS", name="Boston Logan International Airport")
        bos = _candidate(bos_row, identifiers=frozenset({"BOS"}), canonical_runway_ends=frozenset({"22R"}))

        persist_discovery_fragment(
            session, _meta("doc-y"),
            CandidateFragment(artifact_identity=_artifact("y"), source_locator="p1", raw_text="BOS Runway 22R EMAS", airport_identifiers=frozenset({"BOS"}), runway_ends=frozenset({"22R"})),
            [bos],
        )

        assert session.query(Airport).count() == before_airports + 1  # only the test's own seed
        assert session.query(Runway).count() == 0
        assert session.query(RunwayEnd).count() == 0
        assert session.query(Installation).count() == 0
        assert session.query(PhysicalInstallationIdentity).count() == 0
