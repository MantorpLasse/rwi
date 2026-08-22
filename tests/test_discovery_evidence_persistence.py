"""Tests for app/services/discovery_evidence_persistence.py
(docs/architecture/ai-discovery-governed-evidence-persistence-report.md).

Isolated, in-memory SQLite databases only - never the real one. Never
commits inside the service itself; every test controls its own
transaction explicitly, proving the service's own "caller owns the
transaction" contract along the way."""
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import pytest

from app.database import Base
from app import models  # noqa: F401 - registers all metadata
from app.models import Airport, Installation, PhysicalInstallationIdentity, Runway, RunwayEnd, Signal, Source, SourceAssertion
from app.models.unknown_airport_candidate import UnknownAirportCandidate
from app.services.discovery_candidate_fragment import CandidateFragment, DiscoveryContext
from app.services.discovery_evidence_persistence import (
    DiscoveryPersistenceResult,
    DiscoverySourceMetadata,
    persist_candidate_linked_source_assertion,
    persist_discovery_fragment,
)
from app.services.evidence_attachment_guard import AttachmentOutcome, CandidateAirport
from app.services.unknown_airport_candidate_persistence import find_or_create_unknown_airport_candidate


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


# ---------------------------------------------------------------------------
# UAC2B: persist_candidate_linked_source_assertion()
# ---------------------------------------------------------------------------


def _foo_candidate_kwargs(**overrides):
    kwargs = dict(raw_name="Foo Regional Airport", raw_city="Fooville", raw_country="Fictionland")
    kwargs.update(overrides)
    return kwargs


def test_candidate_linked_evidence_persists_with_airport_id_null():
    """§11: the core UAC2B capability, end to end."""
    with Session(_engine()) as session:
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_candidate_kwargs()).candidate
        session.flush()
        airports_before = session.query(Airport).count()

        fragment = CandidateFragment(
            artifact_identity=_artifact("foo-regional-memo"), source_locator="p1",
            raw_text="Foo Regional Airport is planning an EMAS feasibility study.",
        )
        result = persist_candidate_linked_source_assertion(
            session, _meta("foo-regional-doc"), fragment, unknown_airport_candidate_id=candidate.id,
        )

        assert isinstance(result, DiscoveryPersistenceResult)
        assert result.attached_airport_id is None
        assert result.attached_unknown_airport_candidate_id == candidate.id
        assert result.outcome == AttachmentOutcome.INSUFFICIENT_IDENTITY

        assertion = session.get(SourceAssertion, result.source_assertion_id)
        assert assertion.airport_id is None
        assert assertion.unknown_airport_candidate_id == candidate.id
        assert assertion.raw_relevant_text == fragment.raw_text  # original evidence preserved

        # No canonical Airport created by this call.
        assert session.query(Airport).count() == airports_before
        # The candidate itself remains non-canonical - no relationship to
        # any canonical table exists on it at all.
        session.refresh(candidate)
        assert candidate.resolved_airport_id is None


def test_candidate_linked_evidence_queryable_and_auditable():
    with Session(_engine()) as session:
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_candidate_kwargs()).candidate
        session.flush()
        fragment = CandidateFragment(
            artifact_identity=_artifact("foo-audit"), source_locator="p1", raw_text="Foo Regional evidence.",
        )
        result = persist_candidate_linked_source_assertion(
            session, _meta("foo-audit-doc"), fragment, unknown_airport_candidate_id=candidate.id,
        )
        session.commit()

        reloaded = session.get(SourceAssertion, result.source_assertion_id)
        assert reloaded is not None
        assert reloaded.unknown_airport_candidate_id == candidate.id
        assert reloaded.source_id == result.source_id


def test_candidate_linked_evidence_rejects_nonexistent_candidate():
    with Session(_engine()) as session:
        fragment = CandidateFragment(
            artifact_identity=_artifact("ghost"), source_locator="p1", raw_text="Evidence for a ghost candidate.",
        )
        with pytest.raises(ValueError, match="does not reference an existing UnknownAirportCandidate"):
            persist_candidate_linked_source_assertion(
                session, _meta("ghost-doc"), fragment, unknown_airport_candidate_id=999999,
            )
        assert session.query(SourceAssertion).count() == 0


def test_multiple_source_assertions_can_link_to_the_same_candidate():
    """§12: convergence adds evidence links, never a one-to-one restriction."""
    with Session(_engine()) as session:
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_candidate_kwargs()).candidate
        session.flush()

        a = persist_candidate_linked_source_assertion(
            session, _meta("doc-a"),
            CandidateFragment(artifact_identity=_artifact("a"), source_locator="p1", raw_text="Evidence A."),
            unknown_airport_candidate_id=candidate.id,
        )
        b = persist_candidate_linked_source_assertion(
            session, _meta("doc-b"),
            CandidateFragment(artifact_identity=_artifact("b"), source_locator="p1", raw_text="Evidence B."),
            unknown_airport_candidate_id=candidate.id,
        )
        c = persist_candidate_linked_source_assertion(
            session, _meta("doc-c"),
            CandidateFragment(artifact_identity=_artifact("c"), source_locator="p1", raw_text="Evidence C."),
            unknown_airport_candidate_id=candidate.id,
        )

        linked = session.query(SourceAssertion).filter_by(unknown_airport_candidate_id=candidate.id).all()
        assert {row.id for row in linked} == {a.source_assertion_id, b.source_assertion_id, c.source_assertion_id}
        assert len(linked) == 3
        # No evidence overwrite - each has its own preserved raw text.
        texts = {row.raw_relevant_text for row in linked}
        assert texts == {"Evidence A.", "Evidence B.", "Evidence C."}
        # Exactly one candidate row throughout.
        assert session.query(UnknownAirportCandidate).count() == 1


def test_candidate_linking_does_not_mutate_candidate_claim_fields():
    """§14: linking new evidence must never rewrite the candidate's own
    immutable claim fields."""
    with Session(_engine()) as session:
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_candidate_kwargs()).candidate
        session.flush()
        before = (candidate.raw_name, candidate.raw_city, candidate.raw_country, candidate.candidate_fingerprint)

        persist_candidate_linked_source_assertion(
            session, _meta("doc-immut"),
            CandidateFragment(artifact_identity=_artifact("immut"), source_locator="p1", raw_text="More evidence."),
            unknown_airport_candidate_id=candidate.id,
        )
        session.commit()
        session.refresh(candidate)
        after = (candidate.raw_name, candidate.raw_city, candidate.raw_country, candidate.candidate_fingerprint)
        assert before == after


def test_rediscovered_candidate_linked_fragment_is_idempotent():
    with Session(_engine()) as session:
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_candidate_kwargs()).candidate
        session.flush()

        def _fragment():
            return CandidateFragment(
                artifact_identity=_artifact("foo-idem"), source_locator="p1", raw_text="Foo Regional evidence.",
            )

        first = persist_candidate_linked_source_assertion(
            session, _meta("foo-idem-doc"), _fragment(), unknown_airport_candidate_id=candidate.id,
        )
        second = persist_candidate_linked_source_assertion(
            session, _meta("foo-idem-doc"), _fragment(), unknown_airport_candidate_id=candidate.id,
        )
        assert first.source_assertion_created is True
        assert second.source_assertion_created is False
        assert first.source_assertion_id == second.source_assertion_id
        assert session.query(SourceAssertion).count() == 1


def test_already_airport_linked_assertion_is_never_rewritten_to_candidate_linked():
    """The identical fragment identity was already resolved to a KNOWN
    airport by persist_discovery_fragment() - a later call to
    persist_candidate_linked_source_assertion() for the SAME fragment
    identity must return that row exactly as-is, never rewrite its
    identity, and never create a dual-identity state."""
    with Session(_engine()) as session:
        airport = Airport(name="Known Airport", country="XX")
        session.add(airport)
        session.flush()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_candidate_kwargs()).candidate
        session.flush()
        known_candidate_airport = _candidate(airport, identifiers=frozenset({"KNOWN"}))

        def _fragment():
            return CandidateFragment(
                artifact_identity=_artifact("shared"), source_locator="p1", raw_text="Shared fragment identity.",
                airport_identifiers=frozenset({"KNOWN"}),
            )

        first = persist_discovery_fragment(session, _meta("shared-doc"), _fragment(), [known_candidate_airport])
        assert first.attached_airport_id == airport.id

        second = persist_candidate_linked_source_assertion(
            session, _meta("shared-doc"), _fragment(), unknown_airport_candidate_id=candidate.id,
        )
        assert second.source_assertion_created is False
        assert second.source_assertion_id == first.source_assertion_id
        assert second.attached_airport_id == airport.id  # unchanged - never rewritten
        assert second.attached_unknown_airport_candidate_id is None  # never set on this row

        reloaded = session.get(SourceAssertion, first.source_assertion_id)
        assert reloaded.airport_id == airport.id
        assert reloaded.unknown_airport_candidate_id is None


def test_candidate_linked_evidence_creates_no_canonical_rows():
    with Session(_engine()) as session:
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_candidate_kwargs()).candidate
        session.flush()
        airports_before = session.query(Airport).count()

        persist_candidate_linked_source_assertion(
            session, _meta("doc-canon"),
            CandidateFragment(artifact_identity=_artifact("canon"), source_locator="p1", raw_text="Evidence."),
            unknown_airport_candidate_id=candidate.id,
        )

        assert session.query(Airport).count() == airports_before
        assert session.query(Runway).count() == 0
        assert session.query(RunwayEnd).count() == 0
        assert session.query(Installation).count() == 0
        assert session.query(PhysicalInstallationIdentity).count() == 0
        assert session.query(Signal).count() == 0


def test_candidate_linked_evidence_no_hidden_commit():
    with Session(_engine()) as session:
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_candidate_kwargs()).candidate
        session.commit()

        persist_candidate_linked_source_assertion(
            session, _meta("doc-rollback"),
            CandidateFragment(artifact_identity=_artifact("rollback"), source_locator="p1", raw_text="Evidence."),
            unknown_airport_candidate_id=candidate.id,
        )
        session.rollback()

        assert session.query(SourceAssertion).count() == 0


def test_dual_identity_rejected_at_db_layer_direct_orm_construction():
    """§3/§21: the forbidden dual-identity state must fail at the DATABASE
    level, not merely because no service function ever constructs it -
    proven by direct ORM construction bypassing both persistence
    functions entirely."""
    from sqlalchemy.exc import IntegrityError

    with Session(_engine()) as session:
        airport = Airport(name="Known Airport", country="XX")
        session.add(airport)
        session.flush()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_candidate_kwargs()).candidate
        session.flush()
        source = Source(title="x", source_type="web_discovery")
        session.add(source)
        session.flush()

        session.add(SourceAssertion(
            source_id=source.id, airport_id=airport.id, unknown_airport_candidate_id=candidate.id,
            assertion_type="project_construction", source_record_identifier="dual-1",
        ))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_dual_identity_rejected_on_update_known_to_both_raw_sql():
    """UAC2B review §10: the forbidden dual state must fail via UPDATE,
    not merely INSERT - proven by a raw SQL UPDATE (via SQLAlchemy Core
    text(), bypassing the ORM's own attribute-assignment path entirely)
    against a valid, already-committed known-airport row."""
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    engine = _engine()
    with Session(engine) as session:
        airport = Airport(name="Known Airport", country="XX")
        session.add(airport)
        session.flush()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_candidate_kwargs()).candidate
        session.flush()
        source = Source(title="x", source_type="web_discovery")
        session.add(source)
        session.flush()
        assertion = SourceAssertion(
            source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
            source_record_identifier="update-known-raw-1",
        )
        session.add(assertion)
        session.commit()

        with pytest.raises(IntegrityError, match="CHECK constraint failed"):
            session.execute(
                text("UPDATE source_assertions SET unknown_airport_candidate_id=:cid WHERE id=:aid"),
                {"cid": candidate.id, "aid": assertion.id},
            )
        session.rollback()


def test_dual_identity_rejected_on_update_known_to_both_via_orm():
    from sqlalchemy.exc import IntegrityError

    engine = _engine()
    with Session(engine) as session:
        airport = Airport(name="Known Airport", country="XX")
        session.add(airport)
        session.flush()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_candidate_kwargs()).candidate
        session.flush()
        source = Source(title="x", source_type="web_discovery")
        session.add(source)
        session.flush()
        assertion = SourceAssertion(
            source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
            source_record_identifier="update-known-2",
        )
        session.add(assertion)
        session.commit()

        assertion.unknown_airport_candidate_id = candidate.id
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_dual_identity_rejected_on_update_candidate_to_both_via_orm():
    from sqlalchemy.exc import IntegrityError

    engine = _engine()
    with Session(engine) as session:
        airport = Airport(name="Known Airport", country="XX")
        session.add(airport)
        session.flush()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_candidate_kwargs()).candidate
        session.flush()
        source = Source(title="x", source_type="web_discovery")
        session.add(source)
        session.flush()
        assertion = SourceAssertion(
            source_id=source.id, unknown_airport_candidate_id=candidate.id, assertion_type="project_construction",
            source_record_identifier="update-candidate-1",
        )
        session.add(assertion)
        session.commit()

        assertion.airport_id = airport.id
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_known_airport_only_state_still_allowed():
    """Truth-table case: airport_id set, unknown_airport_candidate_id NULL
    - the existing, unchanged behavior."""
    with Session(_engine()) as session:
        airport = Airport(name="Known Airport", country="XX")
        session.add(airport)
        session.flush()
        source = Source(title="x", source_type="web_discovery")
        session.add(source)
        session.flush()
        assertion = SourceAssertion(
            source_id=source.id, airport_id=airport.id, unknown_airport_candidate_id=None,
            assertion_type="project_construction", source_record_identifier="known-1",
        )
        session.add(assertion)
        session.flush()  # must not raise
        assert assertion.airport_id == airport.id
        assert assertion.unknown_airport_candidate_id is None


def test_unresolved_state_both_null_still_allowed():
    """Truth-table case: both NULL - the pre-existing 'unresolved
    evidence' state (e.g. REJECT_CROSS_AIRPORT/INSUFFICIENT_IDENTITY with
    no candidate link either) must remain valid. This is NOT an XOR
    requiring exactly one of the two to be set."""
    with Session(_engine()) as session:
        source = Source(title="x", source_type="web_discovery")
        session.add(source)
        session.flush()
        assertion = SourceAssertion(
            source_id=source.id, airport_id=None, unknown_airport_candidate_id=None,
            assertion_type="project_construction", source_record_identifier="unresolved-1",
        )
        session.add(assertion)
        session.flush()  # must not raise
        assert assertion.airport_id is None
        assert assertion.unknown_airport_candidate_id is None


def test_candidate_only_state_still_allowed():
    """Truth-table case: unknown_airport_candidate_id set, airport_id
    NULL - the new UAC2B state."""
    with Session(_engine()) as session:
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_candidate_kwargs()).candidate
        session.flush()
        source = Source(title="x", source_type="web_discovery")
        session.add(source)
        session.flush()
        assertion = SourceAssertion(
            source_id=source.id, airport_id=None, unknown_airport_candidate_id=candidate.id,
            assertion_type="project_construction", source_record_identifier="candidate-1",
        )
        session.add(assertion)
        session.flush()  # must not raise
        assert assertion.airport_id is None
        assert assertion.unknown_airport_candidate_id == candidate.id


def test_deleting_referenced_unknown_airport_candidate_is_blocked_by_fk():
    """§6: governed evidence must not silently lose its identity linkage
    through cascade deletion - no ON DELETE override, matching every
    other FK in this model."""
    from sqlalchemy import event
    from sqlalchemy.exc import IntegrityError

    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_candidate_kwargs()).candidate
        session.commit()
        persist_candidate_linked_source_assertion(
            session, _meta("doc-delete"),
            CandidateFragment(artifact_identity=_artifact("delete"), source_locator="p1", raw_text="Evidence."),
            unknown_airport_candidate_id=candidate.id,
        )
        session.commit()

        session.delete(candidate)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        assert session.query(SourceAssertion).count() == 1
