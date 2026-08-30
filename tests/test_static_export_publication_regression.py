"""Static-export regression for "RWI - Signal Publication Governance -
Design + Implementation" (Phase 15): the exporter still reads
`Signal.published` directly (unchanged), a Signal published through the new
`app.services.signal_publication.publish_signal()` renders exactly like any
other public Signal (evidence, original source link, airport alias all
still present - the "RWI - Sacheon Evidence Surfacing" slice untouched by
this mission), and no publication-audit internal (reviewer identity, reason
text, action vocabulary, table/column names) ever reaches rendered HTML or
data.json.

Every test uses a synthetic, isolated in-memory SQLite database and a
synthetic Signal - never the real data/runway_safe.db and never the real
Signal69 (which must remain published=False throughout this repository).
"""
from __future__ import annotations

import json
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Signal, Source, SourceAssertion
from app.models.airport_alias import AirportAlias
from app.services.airport_alias import record_airport_alias
from app.services.cross_source_alias_attestation import record_cross_source_alias_attestation
from app.services.governed_signal_creation import create_signal_from_approved_review
from app.services.manual_claim_evidence import record_manual_claim_evidence
from app.services.reviewer_action_persistence import record_reviewer_action
from app.services.signal_publication import publish_signal
from app.static_export import build_site

_SECRET_REVIEWER = "human:should-never-leak@example.com"
_SECRET_REASON = "INTERNAL PUBLICATION REASON: reviewed council minutes, confirmed budget line item."
_ALIAS_EXCERPT = "테스트공항(Test Airport) official."
_COUNCIL_EXCERPT = "테스트공항 EMAS project confirmed. Budget secured 2025년 2026년."


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _build_and_publish_signal69_shaped(session) -> Signal:
    """Full real pipeline, exactly as create_signal_from_approved_review()
    requires it, then publish_signal() itself - not a shortcut
    `Signal(published=True)` construction - so this regression proves the
    ACTUAL governed publish path leaves no trace in rendered output, not
    just that an already-public Signal renders fine."""
    airport = Airport(name="Test Airport", country="Testland", iata_code="TST", icao_code="KTST")
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
        alias="테스트공항", evidence_excerpt=_ALIAS_EXCERPT, analyst="human:tester",
    )
    session.commit()
    alias = session.query(AirportAlias).filter_by(airport_id=airport.id).one()

    council_source = Source(
        title="Test Council Record", source_type="Authority", reliability_level="official",
        publisher="Test Council Committee", url="https://example.test/council-record",
        published_date=date(2025, 6, 11),
    )
    session.add(council_source); session.commit()
    sa = SourceAssertion(
        source_id=council_source.id, airport_id=airport.id, assertion_type="project_construction",
        raw_relevant_text=_COUNCIL_EXCERPT, source_record_identifier="rec-council", evidence_quality="direct_strong",
        identity_guard_decision="ATTACH_PROVISIONAL", intelligence_review_decision="REVIEW_REQUIRED",
        promotion_policy_decision="HUMAN_REVIEW_REQUIRED",
    )
    session.add(sa); session.commit()
    record_cross_source_alias_attestation(
        session, source_assertion_id=sa.id, matched_alias_id=alias.id, analyst="human:tester", reason="test",
    )
    session.commit()
    record_manual_claim_evidence(
        session, source_assertion_id=sa.id, claim_category="explicit_document_fact",
        subject="EMAS installation", statement="EMAS installation is confirmed.",
        evidence_excerpt="테스트공항 EMAS project confirmed", analyst="human:tester",
    )
    session.commit()
    record_reviewer_action(
        session, sa, action="APPROVE_SIGNAL", reason="Effectively confirmed identity, human-approved.",
        reviewer="human:tester",
    )
    session.commit()
    result = create_signal_from_approved_review(
        session, sa, title="Test Airport EMAS installation", category="new_installation",
        confidence="medium", status="identified",
    )
    session.commit()
    assert result.signal.published is False

    publish_signal(session, result.signal, reviewer=_SECRET_REVIEWER, reason=_SECRET_REASON)
    session.commit()
    assert result.signal.published is True
    return result.signal, airport


def test_published_governed_signal_gets_a_public_detail_page(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        signal, _airport = _build_and_publish_signal69_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    assert (tmp_path / "site" / "signals" / f"{signal.id}.html").exists()


def test_published_signal_still_renders_evidence_source_link_and_alias(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        signal, airport = _build_and_publish_signal69_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    signal_html = (tmp_path / "site" / "signals" / f"{signal.id}.html").read_text(encoding="utf-8")
    airport_html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert "EMAS installation is confirmed." in signal_html  # claim statement
    assert "테스트공항 EMAS project confirmed" in signal_html  # literal excerpt, untranslated
    assert 'href="https://example.test/council-record"' in signal_html  # original source link intact
    assert "테스트공항" in airport_html  # alias still surfaced


def test_reviewer_identity_never_leaks_into_signal_detail(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        signal, _airport = _build_and_publish_signal69_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "signals" / f"{signal.id}.html").read_text(encoding="utf-8")
    assert _SECRET_REVIEWER not in html
    assert _SECRET_REASON not in html


def test_reviewer_identity_never_leaks_anywhere_in_the_export(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        signal, _airport = _build_and_publish_signal69_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    for path in (tmp_path / "site").rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert _SECRET_REVIEWER not in text, f"leaked in {path}"
            assert _SECRET_REASON not in text, f"leaked in {path}"


def test_publication_action_vocabulary_and_table_name_never_leak(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        signal, _airport = _build_and_publish_signal69_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    html = (tmp_path / "site" / "signals" / f"{signal.id}.html").read_text(encoding="utf-8")
    for internal_token in ("signal_publication_actions", "SignalPublicationAction", "supersedes_action_id"):
        assert internal_token not in html


def test_data_json_excludes_publication_audit_fields(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        signal, _airport = _build_and_publish_signal69_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    data_json_text = (tmp_path / "site" / "data.json").read_text(encoding="utf-8")
    assert _SECRET_REVIEWER not in data_json_text
    assert _SECRET_REASON not in data_json_text
    payload = json.loads(data_json_text)
    matching = [s for s in payload["signals"] if s["id"] == signal.id]
    assert len(matching) == 1
    signal_json = matching[0]
    assert "reviewer" not in json.dumps(signal_json)
    assert "publication" not in json.dumps(signal_json).lower()


def test_static_export_still_reads_signal_published_directly():
    """The exporter's own publication predicate is unmodified by this
    mission - still a direct read of Signal.published, never a
    SignalPublicationAction history walk."""
    import inspect as _inspect
    from app.static_export.build import _is_public_signal
    source = _inspect.getsource(_is_public_signal)
    assert "signal.published" in source
    assert "SignalPublicationAction" not in source


def test_unpublished_signal_still_produces_no_detail_page(tmp_path):
    """publish_signal() is never called here - the Signal stays
    published=False, exactly the real Signal69 invariant this whole
    mission must preserve."""
    engine = _engine()
    with Session(engine) as session:
        airport = Airport(name="Untouched Airport", country="Testland")
        session.add(airport); session.commit()
        signal = Signal(
            airport_id=airport.id, title="Never published", category="new_installation",
            confidence="medium", status="identified", published=False,
        )
        session.add(signal); session.commit()
        signal_id = signal.id
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 30))
    assert not (tmp_path / "site" / "signals" / f"{signal_id}.html").exists()
