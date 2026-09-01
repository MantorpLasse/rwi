"""Tests for "RWI - Mission #8F" (Airport Evidence Presentation: Surface
All Governed Sources): `_airport_evidence_view()` now also walks
`Airport.source_assertions` directly (a real, pre-existing relationship),
so an airport whose Installation rows can only ever cite ONE Source
(`Installation.source_id` is a single FK) still truthfully shows every
governed, narrative-evidence-shaped SourceAssertion linked to it - the
exact London City shape (#8D/#8E): 3 governed Sources/SourceAssertions,
2 Installations both citing only the first Source, 0 Signals, 0
ManualClaimEvidence.

No Claim is created here, no Signal is created, no current-EMAS/
PhysicalInstallationIdentity semantics are touched. Every test uses a
synthetic, isolated in-memory SQLite database - never the real
data/runway_safe.db.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Installation, Runway, Signal, Source, SourceAssertion
from app.services.manual_claim_evidence import record_manual_claim_evidence
from app.static_export import build_site


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _underlag_section(html: str) -> str:
    """Scopes to the 'Underlag' evidence card specifically - the same
    source title legitimately also appears once per Installation row in
    the unrelated, earlier 'EMAS-installation' section (airport_detail.html
    renders it BEFORE Underlag - moved further up the page and renamed from
    'Historisk EMAS-kontext' by "RWI - Mission #8H"), so a whole-page
    substring count is not a safe way to test deduplication. 'Underlag' is
    the last real content section before the closing footer/scripts, so
    scoping to everything from its own id onward is sufficient."""
    return html[html.index('id="underlag"'):]


def _seed_lcy_shaped(session) -> Airport:
    """Mirrors the real LCY shape exactly (#8D/#8E): 3 governed Sources +
    SourceAssertions (project_construction, airport-linked, no Signal), 2
    Installations both citing only the first Source, 0 Signals, 0
    ManualClaimEvidence - never asserting anything about the real rows
    themselves."""
    airport = Airport(name="Test London City Airport", iata_code="TLC", icao_code="EGTC", country="United Kingdom", city="London")
    session.add(airport); session.commit()

    caa_source = Source(
        title="Test LCY EMAS ACP", source_type="regulatory_document", publisher="Test Civil Aviation Authority",
        url="https://example.test/caa-acp", published_date=date(2023, 3, 16),
    )
    vendor_source = Source(
        title="Test LCY vendor announcement", source_type="manufacturer_press", publisher="Test Runway Safe",
        url="https://example.test/vendor-announcement", published_date=date(2022, 10, 21),
    )
    contractor_source = Source(
        title="Test LCY contractor announcement", source_type="contractor_press", publisher="Test blu-3",
        url="https://example.test/contractor-announcement", published_date=None,
    )
    session.add_all([caa_source, vendor_source, contractor_source]); session.commit()

    caa_assertion = SourceAssertion(
        source_id=caa_source.id, airport_id=airport.id, assertion_type="project_construction",
        raw_relevant_text="Test London City Airport is installing EMAS at both ends of its runway.",
        source_record_identifier="rec-caa", evidence_quality="unverified_candidate",
    )
    vendor_assertion = SourceAssertion(
        source_id=vendor_source.id, airport_id=airport.id, assertion_type="project_construction",
        raw_relevant_text="Test vendor's EMASMAX solution was selected for this airport.",
        source_record_identifier="rec-vendor", evidence_quality="unverified_candidate",
    )
    contractor_assertion = SourceAssertion(
        source_id=contractor_source.id, airport_id=airport.id, assertion_type="project_construction",
        raw_relevant_text="Test contractor's scope is the installation of two EMAS systems.",
        source_record_identifier="rec-contractor", evidence_quality="unverified_candidate",
    )
    session.add_all([caa_assertion, vendor_assertion, contractor_assertion]); session.commit()

    runway = Runway(airport_id=airport.id, designation="09/27")
    session.add(runway); session.commit()

    for end in ("09", "27"):
        session.add(Installation(
            airport_id=airport.id, runway_id=runway.id, source_id=caa_source.id, runway_end=end,
            type="EMASMAX", install_year=2023, status="active", confirmed_vendor="Test Runway Safe",
        ))
    session.commit()
    return airport


def test_lcy_shaped_evidence_renders_all_three_sources(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_lcy_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert "Test LCY EMAS ACP" in html
    assert "Test LCY vendor announcement" in html
    assert "Test LCY contractor announcement" in html


def test_source_count_is_three(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_lcy_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert "3 källor" in html


def test_caa_source_appears_exactly_once(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_lcy_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert _underlag_section(html).count("Test LCY EMAS ACP") == 1


def test_vendor_source_appears_exactly_once(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_lcy_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert _underlag_section(html).count("Test LCY vendor announcement") == 1


def test_contractor_source_appears_exactly_once(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_lcy_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert _underlag_section(html).count("Test LCY contractor announcement") == 1


def test_two_installations_same_caa_source_do_not_duplicate_it(tmp_path):
    """Both Installation rows cite the SAME caa_source - must still be
    exactly one evidence card, not two."""
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_lcy_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert _underlag_section(html).count("Test LCY EMAS ACP") == 1
    assert "3 källor" in html  # not 4 - the Installation-derived duplicate is absorbed


def test_zero_reviewed_claims_render(tmp_path):
    """claim_count=0 - the summary heading shows only the source count
    (never a fabricated "0 sakuppgifter" count), and each source honestly
    states no reviewed claim has been transcribed yet - Part D's own
    explicit acceptance criterion, not a regression to hide."""
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_lcy_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert "3 källor" in html
    assert "granskad sakuppgift har transkriberats ännu" in html  # honest, not fabricated
    assert "1 granskad sakuppgift" not in html
    assert "granskade sakuppgifter" not in html  # no plural claim-COUNT phrase anywhere


def test_raw_source_assertion_text_never_shown_as_fact(tmp_path):
    """No governed Claim exists yet - the raw_relevant_text itself must
    never be printed as if it were a reviewed public fact."""
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_lcy_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert "Test London City Airport is installing EMAS at both ends of its runway." not in html
    assert "Test vendor's EMASMAX solution was selected for this airport." not in html
    assert "Test contractor's scope is the installation of two EMAS systems." not in html


def test_future_claim_on_airport_linked_assertion_renders_automatically(tmp_path):
    """The critical #8F acceptance criterion: once a real Claim is
    recorded against one of these Airport-linked (not Signal-linked)
    SourceAssertions, it must appear with NO further presentation
    change - proving the claims lookup, not just the source citation,
    was wired in."""
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_lcy_shaped(session)
        caa_assertion = session.query(SourceAssertion).filter(
            SourceAssertion.source_record_identifier == "rec-caa"
        ).one()
        # record_manual_claim_evidence() requires EFFECTIVE identity
        # confirmation (resolve_effective_identity_guard_decision()) - the
        # real LCY assertions carry INSUFFICIENT_IDENTITY today (#8E's own
        # finding), so a real future promotion mission would need to
        # correct this first; this fixture sets it directly to isolate
        # THIS mission's own concern (does a Claim, once eligible and
        # recorded, render?) from that separate, already-flagged gap.
        caa_assertion.identity_guard_decision = "ATTACH_CONFIRMED"
        session.commit()
        record_manual_claim_evidence(
            session, source_assertion_id=caa_assertion.id, claim_category="explicit_document_fact",
            subject="Test EMAS installation", statement="Test EMAS installation is confirmed at both ends.",
            evidence_excerpt="Test London City Airport is installing EMAS at both ends of its runway.",
            analyst="human:tester",
        )
        session.commit()
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert "Test EMAS installation is confirmed at both ends." in html
    assert "1 granskad sakuppgift" in html


def test_existing_signal_linked_evidence_still_renders(tmp_path):
    """Regression guard: a Signal's own evidence/claims must continue to
    render exactly as before this mission."""
    engine = _engine()
    with Session(engine) as session:
        airport = Airport(name="Test Signal Airport", country="Testland")
        session.add(airport); session.commit()
        source = Source(
            title="Test signal source", source_type="Authority", publisher="Test Publisher",
            url="https://example.test/signal-source", published_date=date(2025, 1, 1),
        )
        session.add(source); session.commit()
        sa = SourceAssertion(
            source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
            raw_relevant_text="Test Signal Airport: signal-linked evidence text.", source_record_identifier="rec-signal",
            evidence_quality="direct_strong", identity_guard_decision="ATTACH_CONFIRMED",
        )
        session.add(sa); session.commit()
        record_manual_claim_evidence(
            session, source_assertion_id=sa.id, claim_category="explicit_document_fact",
            subject="Test claim", statement="Test signal claim statement.",
            evidence_excerpt="Test Signal Airport: signal-linked evidence text.", analyst="human:tester",
        )
        signal = Signal(
            airport_id=airport.id, source_id=source.id, title="Test Signal With Evidence",
            category="new_installation", confidence="high", status="identified", published=True,
        )
        session.add(signal); session.commit()
        sa.signal_id = signal.id
        session.commit()
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    airport_html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    signal_html = (tmp_path / "site" / "signals" / f"{signal.id}.html").read_text(encoding="utf-8")
    assert "Test signal claim statement." in airport_html
    assert "Test signal claim statement." in signal_html
    assert airport_html.count("Test signal source") == 1  # not duplicated by the new source_assertions loop


def test_no_internal_governance_fields_leak(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_lcy_shaped(session)
        caa_assertion = session.query(SourceAssertion).filter(
            SourceAssertion.source_record_identifier == "rec-caa"
        ).one()
        caa_assertion.identity_guard_decision = "INSUFFICIENT_IDENTITY"
        caa_assertion.identity_guard_reason = "INTERNAL_IDENTITY_GUARD_REASON_MUST_NOT_LEAK"
        caa_assertion.intelligence_review_decision = "REVIEW_REQUIRED"
        caa_assertion.promotion_policy_decision = "HUMAN_REVIEW_REQUIRED"
        caa_assertion.promotion_policy_reason = "INTERNAL_PROMOTION_REASON_MUST_NOT_LEAK"
        session.commit()
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    for forbidden in (
        "INSUFFICIENT_IDENTITY", "INTERNAL_IDENTITY_GUARD_REASON_MUST_NOT_LEAK",
        "REVIEW_REQUIRED", "HUMAN_REVIEW_REQUIRED", "INTERNAL_PROMOTION_REASON_MUST_NOT_LEAK",
        "rec-caa", "human:tester",
    ):
        assert forbidden not in html, f"forbidden token {forbidden!r} leaked"


def test_lcy_signal_count_remains_zero(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_lcy_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    with Session(engine) as session:
        assert session.query(Signal).filter(Signal.airport_id == airport.id).count() == 0


def test_lcy_shaped_airport_absent_from_marknadslage(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_lcy_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    market_html = (tmp_path / "site" / "marknadslage.html").read_text(encoding="utf-8")
    assert "Test London City Airport" not in market_html


def test_current_emas_semantics_unchanged(tmp_path):
    """No PhysicalInstallationIdentity/InstallationAssertionLink exists for
    this fixture - the underlying _current_emas_views() logic/data source is
    unchanged (empty for LCY). Its presentation was reframed twice: "RWI -
    Mission #8H" renamed the panel from "EMAS idag" to "Verifierad
    förekomst"; "RWI - Mission #8I.1" then made the top card lead with a
    POSITIVE "Dokumenterad EMAS" summary whenever installations exist (LCY's
    own real shape), demoting the verification note to a restrained,
    secondary line - honest wording that never implies the documented
    installation is missing, doubtful, or inactive."""
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_lcy_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert ">Dokumenterad EMAS<" in html
    assert "Ingen separat senare verifiering registrerad." in html
    assert "EMAS-installation" in html  # cross-referenced by name, not a dead pointer
    assert "FAA-cykelbaserad evidens" not in html  # internal-sounding jargon removed from the prominent empty state
    for forbidden in ("saknar EMAS", "tveksam", "borttagen", "inaktiv"):
        assert forbidden not in html.lower()


def test_project_and_timeline_empty_states_unchanged(tmp_path):
    engine = _engine()
    with Session(engine) as session:
        airport = _seed_lcy_shaped(session)
        build_site(tmp_path / "site", session=session, today=date(2026, 8, 31))
    html = (tmp_path / "site" / "airports" / f"{airport.id}.html").read_text(encoding="utf-8")
    assert "Inga offentliga projekt- eller bevakningsuppgifter registrerade." in html
    assert "Inga daterade eller odaterade projekt- eller finansieringshändelser registrerade." in html
