"""Tests for the "RWI - Sacheon Evidence Surfacing - View-Model Slice"
mission: governed AirportAlias data in the Airport identity header, and a
public-safe governed evidence/claims block on Signal Detail.

Every test uses a synthetic, isolated in-memory SQLite database and a
synthetic Signal explicitly built with published=True - never the real
data/runway_safe.db and never the real Signal69 (which must remain
published=False throughout this repository).

Modeled directly on tests/test_static_export.py's own established
fixture/assertion pattern.
"""
from __future__ import annotations

import json
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, ReviewerAction, Signal, Source, SourceAssertion
from app.models.airport_alias import AirportAlias
from app.services.airport_alias import record_airport_alias
from app.services.manual_claim_evidence import record_manual_claim_evidence
from app.services.reviewer_action_persistence import record_reviewer_action
from app.static_export import build_site


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


_ALIAS_EXCERPT = "테스트공항(Test Airport) official."
_COUNCIL_EXCERPT = "테스트공항 EMAS project confirmed. 50m 90m constraint. 2025년 2026년 budget secured."


def _seed_airport_with_alias(session, *, alias="테스트공항", name="Test Airport") -> Airport:
    airport = Airport(name=name, country="Testland", iata_code="TST", icao_code="KTST")
    session.add(airport); session.commit()
    alias_source = Source(title="Registry", source_type="Authority", reliability_level="official")
    session.add(alias_source); session.commit()
    alias_assertion = SourceAssertion(
        source_id=alias_source.id, airport_id=airport.id, assertion_type="airport_inventory",
        raw_relevant_text=_ALIAS_EXCERPT, source_record_identifier="rec-alias", evidence_quality="direct_strong",
    )
    session.add(alias_assertion); session.commit()
    record_airport_alias(
        session, airport_id=airport.id, source_id=alias_source.id, source_assertion_id=alias_assertion.id,
        alias=alias, evidence_excerpt=_ALIAS_EXCERPT, analyst="human:tester",
    )
    session.commit()
    return airport


def _seed_governed_signal(session, airport, *, published=True, category="new_installation"):
    """A Signal linked (via SourceAssertion.signal_id) to a governed
    SourceAssertion carrying 2 ManualClaimEvidence rows - the SA235-shaped
    fixture, but fully generic (no real Sacheon/Korean value is asserted
    as meaningful; it is simply the literal text used to exercise
    original-language preservation, same as this repository's own existing
    tests already do for AirportAlias/ManualClaimEvidence)."""
    council_source = Source(
        title="Test Council Record", source_type="Authority", reliability_level="official",
        publisher="Test Council Committee", url="https://example.test/council-record",
        published_date=date(2025, 6, 11),
    )
    session.add(council_source); session.commit()
    sa = SourceAssertion(
        source_id=council_source.id, airport_id=airport.id, assertion_type="project_construction",
        raw_relevant_text=_COUNCIL_EXCERPT, source_record_identifier="rec-council", evidence_quality="direct_strong",
        identity_guard_decision="ATTACH_CONFIRMED",
    )
    session.add(sa); session.commit()

    record_manual_claim_evidence(
        session, source_assertion_id=sa.id, claim_category="explicit_document_fact",
        subject="EMAS installation", statement="EMAS installation is confirmed.",
        evidence_excerpt="테스트공항 EMAS project confirmed", analyst="human:tester",
    )
    record_manual_claim_evidence(
        session, source_assertion_id=sa.id, claim_category="temporal_statement",
        subject="Budget", statement="Budget secured across 2025-2026.",
        evidence_excerpt=_COUNCIL_EXCERPT, analyst="human:tester",
        temporal_qualifier="planned_future_action", temporal_year_tokens=("2025년", "2026년"),
    )
    session.commit()

    signal = Signal(
        airport_id=airport.id, source_id=council_source.id, title="Test Airport EMAS installation",
        category=category, confidence="medium", probability_score=6.0, status="identified",
        published=published,
    )
    session.add(signal); session.commit()
    sa.signal_id = signal.id
    session.commit()
    return signal, sa, council_source


# ---------------------------------------------------------------------------
# 1-3. AirportAlias public projection
# ---------------------------------------------------------------------------

def test_admitted_alias_appears_in_airport_detail(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_airport_with_alias(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert "테스트공항" in html


def test_rejected_alias_does_not_appear(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_airport_with_alias(session)  # admits 테스트공항, the default
        alias_source = Source(title="Second registry", source_type="Authority", reliability_level="official")
        session.add(alias_source); session.commit()
        excerpt = "위험한별칭(Test Airport) official."
        alias_assertion = SourceAssertion(
            source_id=alias_source.id, airport_id=airport.id, assertion_type="airport_inventory",
            raw_relevant_text=excerpt, source_record_identifier="rec-alias-2", evidence_quality="direct_strong",
        )
        session.add(alias_assertion); session.commit()
        admitted = record_airport_alias(
            session, airport_id=airport.id, source_id=alias_source.id, source_assertion_id=alias_assertion.id,
            alias="위험한별칭", evidence_excerpt=excerpt, analyst="human:tester",
        )
        session.commit()
        record_airport_alias(
            session, airport_id=airport.id, source_id=alias_source.id, source_assertion_id=alias_assertion.id,
            alias="위험한별칭", evidence_excerpt=excerpt, analyst="human:tester",
            status="REJECTED", supersedes_alias_id=admitted.alias_id,
        )
        session.commit()
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert "위험한별칭" not in html
    assert "테스트공항" in html  # the still-ADMITTED alias remains visible


def test_duplicate_canonical_name_alias_is_suppressed(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        # Alias text identical (after normalization) to the canonical name.
        airport = _seed_airport_with_alias(session, alias="Test Airport", name="Test Airport")
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    # The canonical name appears once (in the <h1>/subtitle) but the
    # duplicate alias line must not add a second, redundant "local name" row.
    airport_view_marker = 'style="margin-top:-8px; color:var(--text-dim)"'
    assert airport_view_marker not in html


# ---------------------------------------------------------------------------
# 4-9. Public claim evidence block
# ---------------------------------------------------------------------------

def test_published_signal_with_claims_renders_evidence_block(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_airport_with_alias(session)
        signal, sa, source = _seed_governed_signal(session, airport)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "signals" / f"{signal.id}.html").read_text(encoding="utf-8")
    assert "Underlag" in html


def test_all_claim_statements_render(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_airport_with_alias(session)
        signal, sa, source = _seed_governed_signal(session, airport)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "signals" / f"{signal.id}.html").read_text(encoding="utf-8")
    assert "EMAS installation is confirmed." in html
    assert "Budget secured across 2025-2026." in html


def test_literal_excerpts_render_exactly(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_airport_with_alias(session)
        signal, sa, source = _seed_governed_signal(session, airport)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "signals" / f"{signal.id}.html").read_text(encoding="utf-8")
    assert "테스트공항 EMAS project confirmed" in html
    assert _COUNCIL_EXCERPT in html
    assert "2025년" in html and "2026년" in html


def test_source_publisher_renders(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_airport_with_alias(session)
        signal, sa, source = _seed_governed_signal(session, airport)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "signals" / f"{signal.id}.html").read_text(encoding="utf-8")
    assert "Test Council Committee" in html


def test_source_published_date_renders(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_airport_with_alias(session)
        signal, sa, source = _seed_governed_signal(session, airport)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "signals" / f"{signal.id}.html").read_text(encoding="utf-8")
    assert "2025-06-11" in html


def test_source_original_url_renders_as_navigable_link(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_airport_with_alias(session)
        signal, sa, source = _seed_governed_signal(session, airport)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "signals" / f"{signal.id}.html").read_text(encoding="utf-8")
    assert 'href="https://example.test/council-record"' in html


# ---------------------------------------------------------------------------
# 10-12. Safe/legacy rendering
# ---------------------------------------------------------------------------

def test_signal_without_claims_renders_normally(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = Airport(name="Plain Airport", country="Testland")
        session.add(airport); session.commit()
        signal = Signal(
            airport_id=airport.id, title="Plain Signal", category="replacement",
            confidence="high", status="identified", published=True,
        )
        session.add(signal); session.commit()
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "signals" / f"{signal.id}.html").read_text(encoding="utf-8")
    assert "Plain Signal" in html
    assert "Underlag" not in html


def test_legacy_signal_without_linked_source_assertion_renders_normally(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_airport_with_alias(session)
        legacy_signal = Signal(
            airport_id=airport.id, title="Legacy Signal", category="replacement",
            confidence="high", status="identified", published=True,
        )
        session.add(legacy_signal); session.commit()
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "signals" / f"{legacy_signal.id}.html").read_text(encoding="utf-8")
    assert "Legacy Signal" in html
    assert "Underlag" not in html


def test_unpublished_signal_produces_no_detail_page(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_airport_with_alias(session)
        signal, sa, source = _seed_governed_signal(session, airport, published=False)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    assert not (tmp_path / "site" / "signals" / f"{signal.id}.html").exists()


# ---------------------------------------------------------------------------
# 13-15. Public-leak regression
# ---------------------------------------------------------------------------

def test_raw_effective_identity_internals_not_rendered(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_airport_with_alias(session)
        signal, sa, source = _seed_governed_signal(session, airport)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "signals" / f"{signal.id}.html").read_text(encoding="utf-8")
    assert "ATTACH_PROVISIONAL" not in html
    assert "ATTACH_CONFIRMED" not in html
    assert "CROSS_SOURCE_ALIAS_ATTESTATION" not in html


def test_reviewer_action_analyst_and_reason_not_rendered(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_airport_with_alias(session)
        signal, sa, source = _seed_governed_signal(session, airport)
        sa.intelligence_review_decision = "REVIEW_REQUIRED"
        sa.promotion_policy_decision = "HUMAN_REVIEW_REQUIRED"
        session.commit()
        record_reviewer_action(
            session, sa, action="APPROVE_SIGNAL",
            reason="INTERNAL_ONLY_REASON_TEXT_MUST_NEVER_LEAK_PUBLICLY",
            reviewer="human:leaked-analyst@example.test",
        )
        session.commit()
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "signals" / f"{signal.id}.html").read_text(encoding="utf-8")
    assert "human:leaked-analyst@example.test" not in html
    assert "INTERNAL_ONLY_REASON_TEXT_MUST_NEVER_LEAK_PUBLICLY" not in html


def test_promotion_policy_internals_not_rendered(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_airport_with_alias(session)
        signal, sa, source = _seed_governed_signal(session, airport)
        sa.intelligence_review_decision = "REVIEW_REQUIRED"
        sa.promotion_policy_decision = "HUMAN_REVIEW_REQUIRED"
        sa.promotion_policy_reason = "INTERNAL_PROMOTION_POLICY_REASON_TEXT"
        session.commit()
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "signals" / f"{signal.id}.html").read_text(encoding="utf-8")
    assert "HUMAN_REVIEW_REQUIRED" not in html
    assert "INTERNAL_PROMOTION_POLICY_REASON_TEXT" not in html
    assert "TIER_2_OFFICIAL_GOVERNMENT" not in html


# ---------------------------------------------------------------------------
# 16-17. Confidence explanation, Unicode
# ---------------------------------------------------------------------------

def test_confidence_explanation_appears(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_airport_with_alias(session)
        signal, sa, source = _seed_governed_signal(session, airport)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "signals" / f"{signal.id}.html").read_text(encoding="utf-8")
    assert "inte sannolikheten att en viss leverantör vinner projektet" in html


def test_korean_unicode_survives_static_rendering(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_airport_with_alias(session)
        signal, sa, source = _seed_governed_signal(session, airport)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "signals" / f"{signal.id}.html").read_text(encoding="utf-8")
    assert "테스트공항" in html
    airport_html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert "테스트공항" in airport_html


# ---------------------------------------------------------------------------
# 18. data.json boundary
# ---------------------------------------------------------------------------

def test_data_json_excludes_literal_claim_evidence_but_includes_alias(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_airport_with_alias(session)
        signal, sa, source = _seed_governed_signal(session, airport)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    raw = (tmp_path / "site" / "data.json").read_text(encoding="utf-8")
    data = json.loads(raw)
    assert "테스트공항 EMAS project confirmed" not in raw  # literal claim excerpt not duplicated
    assert "테스트공항" in raw  # alias IS present (small string, useful for search)
    # No internal governance value anywhere in the payload.
    assert "ATTACH_PROVISIONAL" not in raw
    assert "ATTACH_CONFIRMED" not in raw
    assert "human:tester" not in raw
