from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.acquisition.faa_aip_grants import (
    AipGrant,
    AipGrantsError,
    _parse_amount,
    discover_grant_pdf_urls,
    is_runway_safety_relevant,
    parse_grant_pdf,
)
from app.database import Base
from app.models import Airport, ReviewerAction, Signal, Source, SourceAssertion
from app.services.known_airport_funding_lightweight_path_guard import (
    check_lightweight_funding_path_eligibility,
)
from scripts.import_faa_aip_grants import import_year, main

FIXTURE_PDF = (Path(__file__).parent / "fixtures" / "aip_grant_sample.pdf").read_bytes()
LISTING_HTML = """
<html><body>
<a href="/airports/aip/2026_aip_grants/AIP_FY26_1.pdf">Announcement 1</a>
<a href="/airports/aip/2026_aip_grants/AIP_FY26_1A.pdf">Announcement 1A</a>
<a href="/airports/aip/2026_aip_grants/AIP_FY26_1.pdf">duplicate link</a>
</body></html>
"""


def test_discover_grant_pdf_urls_resolves_and_deduplicates_links():
    def handler(request):
        assert str(request.url) == "https://www.faa.gov/airports/aip/2026_aip_grants"
        return httpx.Response(200, content=LISTING_HTML, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    urls = discover_grant_pdf_urls(2026, client=client)

    assert urls == [
        "https://www.faa.gov/airports/aip/2026_aip_grants/AIP_FY26_1.pdf",
        "https://www.faa.gov/airports/aip/2026_aip_grants/AIP_FY26_1A.pdf",
    ]


def test_discover_grant_pdf_urls_fails_closed_on_no_pdfs():
    def handler(request):
        return httpx.Response(200, content=b"<html><body>no links here</body></html>", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(AipGrantsError):
        discover_grant_pdf_urls(2026, client=client)


def test_parse_grant_pdf_extracts_rows_and_skips_totals_row():
    grants = parse_grant_pdf(FIXTURE_PDF, source_pdf_url="https://faa.example/AIP_FY26_1A.pdf")

    assert len(grants) == 2
    dca, ord_ = grants
    assert dca == AipGrant(
        state="VA",
        city="Arlington",
        airport_name="Ronald Reagan Washington Ntl",
        loc_id="DCA",
        project_description="Construct Taxiway",
        entitlement_amt=Decimal("0.00"),
        discretionary_amt=Decimal("15000000.00"),
        total_aip_amt=Decimal("15000000.00"),
        source_pdf_url="https://faa.example/AIP_FY26_1A.pdf",
    )
    assert ord_.loc_id == "ORD"
    assert ord_.project_description == "Extend/Expand Runway"
    assert ord_.total_aip_amt == Decimal("20000000.00")
    # The trailing totals row (blank Loc ID) must not appear as a grant.
    assert all(g.loc_id for g in grants)


def test_parse_grant_pdf_fails_closed_on_unexpected_header():
    import pdfplumber

    class _FakePage:
        def extract_table(self):
            return [["Wrong", "Header"], ["a", "b"]]

    class _FakePdf:
        pages = [_FakePage()]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    original_open = pdfplumber.open
    pdfplumber.open = lambda _fp: _FakePdf()
    try:
        with pytest.raises(AipGrantsError):
            parse_grant_pdf(b"irrelevant", source_pdf_url="https://faa.example/x.pdf")
    finally:
        pdfplumber.open = original_open


def _fake_pdf_with_table(table):
    class _FakePage:
        def extract_table(self):
            return table

    class _FakePdf:
        pages = [_FakePage()]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return _FakePdf()


def _with_fake_pdfplumber(table, fn):
    import pdfplumber

    original_open = pdfplumber.open
    pdfplumber.open = lambda _fp: _fake_pdf_with_table(table)
    try:
        return fn()
    finally:
        pdfplumber.open = original_open


def test_parse_grant_pdf_accepts_current_fy2026_noncomp_discretionary_header():
    """2. RWI HQ 'FAA AIP Parser + Stage-Only Conversion': the current
    FY2026 header uses 'Non-comp Discretionary Amt' in place of the
    historical 'Discretionary Amt' - must parse, not fail closed."""
    table = [
        ["ST", "City", "Airport", "Loc ID", "Project Description",
         "Entitlement Amt", "Non-comp Discretionary Amt", "Total AIP"],
        ["KY", "Louisville", "Louisville Muhammad Ali International", "SDF",
         "Reconstruct Taxiway; Construct EMAS Safety Area",
         "$203,940.00", "$25,272,482.00", "$25,476,422.00"],
    ]
    grants = _with_fake_pdfplumber(
        table, lambda: parse_grant_pdf(b"irrelevant", source_pdf_url="https://faa.example/fy26.pdf")
    )
    assert len(grants) == 1
    grant = grants[0]
    assert grant.loc_id == "SDF"
    assert grant.entitlement_amt == Decimal("203940.00")
    assert grant.discretionary_amt == Decimal("25272482.00")
    assert grant.total_aip_amt == Decimal("25476422.00")


def test_parse_grant_pdf_fails_closed_on_unrecognized_discretionary_label():
    """3. A right-shaped, right-length header with an UNRECOGNIZED
    discretionary-column label must still fail closed - proves the header
    check is an explicit alias set, never a fuzzy/partial match that would
    silently accept a genuinely different table schema."""
    table = [
        ["ST", "City", "Airport", "Loc ID", "Project Description",
         "Entitlement Amt", "Some Other Discretionary Label", "Total AIP"],
        ["KY", "Louisville", "Louisville Muhammad Ali International", "SDF", "x", "$1.00", "$1.00", "$2.00"],
    ]
    with pytest.raises(AipGrantsError):
        _with_fake_pdfplumber(
            table, lambda: parse_grant_pdf(b"irrelevant", source_pdf_url="https://faa.example/x.pdf")
        )


# --- Part 1/2/9: amount parser (internal-whitespace artifact) --------------


def test_parse_amount_normal_form():
    assert _parse_amount("$25,272,482.00") == Decimal("25272482.00")


def test_parse_amount_spaced_form_matches_the_confirmed_live_artifact():
    """1. The exact real live-PDF shape found in AIP_FY26_5.pdf."""
    assert _parse_amount("$ 2 5,272,482.00") == Decimal("25272482.00")
    assert _parse_amount("$ 2 5,476,422.00") == Decimal("25476422.00")


def test_parse_amount_other_internal_whitespace_forms():
    """Deliberately supported: ANY internal whitespace run, anywhere in the
    cell, including a tab or non-breaking space, is removed the same way -
    uniform normalization, not a hardcoded single-position fix."""
    assert _parse_amount("$1 7,053,214.00") == Decimal("17053214.00")
    assert _parse_amount("$\t203,940.00") == Decimal("203940.00")
    assert _parse_amount("$203, 940.00") == Decimal("203940.00")  # non-breaking space


def test_parse_amount_blank_and_dash_remain_none():
    assert _parse_amount("") is None
    assert _parse_amount("   ") is None
    assert _parse_amount("-") is None
    assert _parse_amount("$ - ") is None
    assert _parse_amount(None) is None


def test_parse_amount_malformed_text_remains_none():
    """2. Malformed/non-numeric content stays fail-closed as None, even
    after whitespace normalization - proves this is not fuzzy digit
    extraction: a genuinely broken cell still fails Decimal(...) parsing."""
    assert _parse_amount("N O T   A V A I L A B L E") is None
    assert _parse_amount("TBD") is None
    assert _parse_amount("$25,272,482.00 $1,000.00") is None  # two numbers concatenated - still invalid


# --- Part 3/4/9: FAA AIP relevance gate -------------------------------------


def test_relevance_explicit_engineered_material_arresting_system_is_relevant():
    """4. Explicit 'Engineered Material Arresting System' names EMAS."""
    assert is_runway_safety_relevant("Construct Engineered Material Arresting System Safety Area") is True


def test_relevance_bare_emas_abbreviation_is_relevant():
    """5. The bare EMAS abbreviation, in realistic project-title text."""
    assert is_runway_safety_relevant("Reconstruct EMAS Runway 6") is True
    assert is_runway_safety_relevant("EMAS Replacement") is True


def test_relevance_bundled_sdf_shaped_description_is_relevant():
    """6. A bundled, multi-component description is relevant as long as ONE
    explicit component names EMAS/arresting system."""
    assert is_runway_safety_relevant(
        "Reconstruct Taxiway,Construct Engineered Material Arresting System Safety Area,"
        "Conduct Noise Compatibility Plan Study,Noise Mitigation Measures for Residences within 65-69 DNL"
    ) is True


def test_relevance_generic_runway_reconstruction_is_not_relevant():
    """7."""
    assert is_runway_safety_relevant("Reconstruct Runway") is False
    assert is_runway_safety_relevant("Extend/Expand Runway") is False
    assert is_runway_safety_relevant("Reseal/Resurface Runway") is False


def test_relevance_generic_non_runway_work_is_not_relevant():
    """8. Terminal/apron/taxiway/lighting/noise work, with no EMAS
    component, must never qualify merely for occurring at an Airport."""
    assert is_runway_safety_relevant("Construct Terminal") is False
    assert is_runway_safety_relevant("Expand Apron") is False
    assert is_runway_safety_relevant("Reconstruct Taxiway") is False
    assert is_runway_safety_relevant("Rehabilitate Runway Lighting") is False
    assert is_runway_safety_relevant("Airport Noise Study") is False
    assert is_runway_safety_relevant("Residential Sound Insulation Program") is False


def test_relevance_generic_runway_safety_area_or_rsa_does_not_automatically_qualify():
    """9. Per this mission's own explicit caution and the repository's own
    narrower scripts/import_faa_construction_report.py::_mentions_keyword()
    precedent (which also excludes RSA/'runway safety area'), a bare RSA
    mention with no explicit EMAS/arresting-system language does not
    qualify - it may describe ordinary safety-area work with no EMAS
    component at all."""
    assert is_runway_safety_relevant("Improve Runway Safety Area") is False
    assert is_runway_safety_relevant("RSA Improvement") is False
    assert is_runway_safety_relevant("Runway Safety Area Alternatives Analysis") is False


def test_relevance_word_boundary_avoids_false_positive_substring():
    """The EMAS check is word-boundary-anchored specifically so it never
    false-positives on an ordinary word that happens to CONTAIN 'emas' as a
    substring (e.g. 'SCHEMAS')."""
    assert is_runway_safety_relevant("Update Airport Layout Plan Schemas") is False


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _client_serving(pdf_bytes: bytes, pdf_url: str):
    def handler(request):
        if str(request.url).endswith("_aip_grants"):
            return httpx.Response(
                200,
                content=f'<a href="{pdf_url}">grants</a>'.encode(),
                request=request,
            )
        if str(request.url) == pdf_url:
            return httpx.Response(200, content=pdf_bytes, request=request)
        return httpx.Response(404, request=request)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _fake_parsed(module, grants_by_url):
    """Monkeypatch scripts.import_faa_aip_grants.parse_grant_pdf for one
    test, restoring the original afterward. grants_by_url: {pdf_url: [AipGrant, ...]}."""

    def fake_parse(pdf_bytes, *, source_pdf_url):
        return grants_by_url.get(source_pdf_url, [])

    original = module.parse_grant_pdf
    module.parse_grant_pdf = fake_parse
    return original


def test_import_year_skips_irrelevant_rows_creates_nothing(session_factory):
    """10. FIXTURE_PDF's own two real rows (DCA "Construct Taxiway", ORD
    "Extend/Expand Runway") are BOTH non-EMAS - under the relevance gate,
    neither is staged, even though DCA is a tracked Airport. Nothing at all
    is created for either row - not even for the tracked one."""
    with session_factory() as session:
        session.add(Airport(name="Ronald Reagan Washington National", faa_code="DCA", country="USA"))
        session.commit()

    pdf_url = "https://www.faa.gov/airports/aip/2026_aip_grants/AIP_FY26_1A.pdf"
    stats = import_year(
        2026, session_factory=session_factory, client=_client_serving(FIXTURE_PDF, pdf_url)
    )

    assert stats["pdfs"] == 1
    assert stats["grants"] == 2
    assert stats["irrelevant_rows_skipped"] == 2
    assert stats["evidence_staged_resolved"] == 0
    assert stats["evidence_staged_unresolved"] == 0
    assert stats["unresolved_loc_ids"] == []

    with session_factory() as session:
        assert session.scalars(select(Source)).all() == []
        assert session.scalars(select(SourceAssertion)).all() == []
        assert session.scalars(select(Signal)).all() == []
        assert session.scalars(select(ReviewerAction)).all() == []
        assert len(session.scalars(select(Airport)).all()) == 1  # only the pre-existing DCA row


def test_import_year_stages_evidence_for_tracked_airport_creates_no_signal(session_factory):
    """1/2/6/9/11. A relevant, known-Airport row -> Source + one
    project_construction SourceAssertion only, zero Signal, raw evidence
    and provenance preserved."""
    with session_factory() as session:
        session.add(Airport(name="Ronald Reagan Washington National", faa_code="DCA", country="USA"))
        session.commit()

    pdf_url = "https://www.faa.gov/airports/aip/2026_aip_grants/dca_emas.pdf"
    grant = AipGrant(
        state="VA", city="Arlington", airport_name="Ronald Reagan Washington Ntl", loc_id="DCA",
        project_description="Construct Engineered Material Arresting System Safety Area",
        entitlement_amt=Decimal("0.00"), discretionary_amt=Decimal("15000000.00"),
        total_aip_amt=Decimal("15000000.00"), source_pdf_url=pdf_url,
    )
    import scripts.import_faa_aip_grants as module
    original = _fake_parsed(module, {pdf_url: [grant]})
    try:
        stats = import_year(2026, session_factory=session_factory, client=_client_serving(b"fake-pdf-bytes", pdf_url))
    finally:
        module.parse_grant_pdf = original

    assert stats["evidence_staged_resolved"] == 1
    assert stats["irrelevant_rows_skipped"] == 0

    with session_factory() as session:
        assert session.scalars(select(Signal)).all() == []
        assert session.scalars(select(ReviewerAction)).all() == []

        source = session.scalar(select(Source).where(Source.source_type == "aip_grant", Source.document_reference == "DCA"))
        assert source is not None
        assert source.url == pdf_url
        assert source.external_id is not None and source.external_id.startswith("faa_aip:")

        assertion = session.scalar(select(SourceAssertion).where(SourceAssertion.source_id == source.id))
        assert assertion is not None
        assert assertion.airport_id is not None
        assert assertion.assertion_type == "project_construction"
        assert "Construct Engineered Material Arresting System Safety Area" in assertion.raw_relevant_text
        assert assertion.evidence_quality == "unverified_candidate"
        assert assertion.review_state == "unreviewed"
        check_lightweight_funding_path_eligibility(assertion)  # Slice B guard compatibility


def test_import_year_never_creates_signal_even_on_emas_keyword_match(session_factory):
    """6. A project description that WOULD have matched the retired
    keyword-flagging rule must still create zero Signal - the keyword
    concept no longer gates anything in this importer."""
    with session_factory() as session:
        session.add(Airport(name="Greater Binghamton", faa_code="BGM", country="USA"))
        session.commit()

    pdf_url = "https://www.faa.gov/airports/aip/2026_aip_grants/emas.pdf"
    grant = AipGrant(
        state="NY", city="Binghamton", airport_name="Greater Binghamton", loc_id="BGM",
        project_description="Reconstruct Engineered Material Arresting System Safety Area",
        entitlement_amt=Decimal("437081.00"), discretionary_amt=None,
        total_aip_amt=Decimal("437081.00"), source_pdf_url=pdf_url,
    )

    import scripts.import_faa_aip_grants as module
    original = _fake_parsed(module, {pdf_url: [grant]})
    try:
        stats = import_year(2026, session_factory=session_factory, client=_client_serving(b"fake-pdf-bytes", pdf_url))
    finally:
        module.parse_grant_pdf = original

    assert stats["evidence_staged_resolved"] == 1

    with session_factory() as session:
        assert session.scalars(select(Signal)).all() == []
        assert session.scalars(select(ReviewerAction)).all() == []
        assertion = session.scalar(select(SourceAssertion))
        assert "arresting system" in assertion.raw_relevant_text.lower()


def test_import_year_stages_unresolved_evidence_without_fabricating_airport(session_factory):
    """5/12. A RELEVANT row at an unknown Airport -> unresolved evidence
    (airport_id=NULL), zero Airport created. The description is
    EMAS-relevant so it actually reaches the unresolved-staging branch at
    all (an irrelevant row at an unknown airport would be skipped before
    even reaching Airport lookup - see
    test_import_year_skips_irrelevant_rows_creates_nothing)."""
    pdf_url = "https://www.faa.gov/airports/aip/2026_aip_grants/unknown.pdf"
    grant = AipGrant(
        state="TX", city="Nowhere", airport_name="Nowhere Municipal", loc_id="ZZZ",
        project_description="Construct Engineered Material Arresting System Safety Area",
        entitlement_amt=Decimal("10000.00"), discretionary_amt=Decimal("0.00"),
        total_aip_amt=Decimal("10000.00"), source_pdf_url=pdf_url,
    )
    import scripts.import_faa_aip_grants as module
    original = _fake_parsed(module, {pdf_url: [grant]})
    try:
        stats = import_year(2026, session_factory=session_factory, client=_client_serving(b"fake-pdf-bytes", pdf_url))
    finally:
        module.parse_grant_pdf = original

    assert stats["evidence_staged_unresolved"] == 1
    assert stats["unresolved_loc_ids"] == ["ZZZ"]

    with session_factory() as session:
        assert session.scalars(select(Airport)).all() == []  # no fabricated Airport
        assert session.scalars(select(Signal)).all() == []
        source = session.scalar(select(Source))
        assert source is not None
        assertion = session.scalar(select(SourceAssertion))
        assert assertion.airport_id is None
        assert assertion.raw_airport_identifier == "ZZZ"
        assert assertion.raw_airport_name == "Nowhere Municipal"


def test_import_year_never_maps_amounts_to_a_signal_field():
    """8. There is no structured amount column on SourceAssertion at all -
    since neither path in this importer creates a Signal, no amount can
    reach a Signal value field by construction."""
    from sqlalchemy import inspect as sa_inspect

    columns = {c.name for c in sa_inspect(SourceAssertion).columns}
    assert "estimated_total_value_usd" not in columns
    assert "estimated_emas_value_usd" not in columns


def test_import_year_replay_is_idempotent_no_duplicate_source_assertion(session_factory):
    """14. Identical replay of a relevant known-Airport row plus a relevant
    unresolved row stages nothing new the second time."""
    with session_factory() as session:
        session.add(Airport(name="Ronald Reagan Washington National", faa_code="DCA", country="USA"))
        session.commit()

    pdf_url = "https://www.faa.gov/airports/aip/2026_aip_grants/replay_emas.pdf"
    grants = [
        AipGrant(
            state="VA", city="Arlington", airport_name="Ronald Reagan Washington Ntl", loc_id="DCA",
            project_description="Construct Engineered Material Arresting System Safety Area",
            entitlement_amt=Decimal("0.00"), discretionary_amt=Decimal("15000000.00"),
            total_aip_amt=Decimal("15000000.00"), source_pdf_url=pdf_url,
        ),
        AipGrant(
            state="TX", city="Nowhere", airport_name="Nowhere Municipal", loc_id="ZZZ",
            project_description="Reconstruct EMAS Runway 9",
            entitlement_amt=Decimal("10000.00"), discretionary_amt=Decimal("0.00"),
            total_aip_amt=Decimal("10000.00"), source_pdf_url=pdf_url,
        ),
    ]
    import scripts.import_faa_aip_grants as module
    original = _fake_parsed(module, {pdf_url: grants})
    try:
        first = import_year(2026, session_factory=session_factory, client=_client_serving(b"fake-pdf-bytes", pdf_url))
        second = import_year(2026, session_factory=session_factory, client=_client_serving(b"fake-pdf-bytes", pdf_url))
    finally:
        module.parse_grant_pdf = original

    assert first["evidence_staged_resolved"] == 1
    assert first["evidence_staged_unresolved"] == 1
    assert second["evidence_staged_resolved"] == 0
    assert second["evidence_staged_unresolved"] == 0
    assert second["already_imported"] == 2

    with session_factory() as session:
        assert len(session.scalars(select(Source)).all()) == 2
        assert len(session.scalars(select(SourceAssertion)).all()) == 2
        assert session.scalars(select(Signal)).all() == []


def test_cli_write_and_network_gates_remain_intact(monkeypatch, capsys):
    """12. --allow-live-network / --allow-database-write gates are
    untouched by this mission - both are still required before any network
    call or import_year() invocation is attempted."""
    called = {"import_year": False}
    monkeypatch.setattr(
        "scripts.import_faa_aip_grants.import_year", lambda *a, **k: called.__setitem__("import_year", True),
    )

    assert main(["--year", "2026"]) == 2
    assert called["import_year"] is False
    assert "--allow-live-network is required" in capsys.readouterr().err

    assert main(["--year", "2026", "--allow-live-network"]) == 2
    assert called["import_year"] is False
    assert "--allow-database-write is required" in capsys.readouterr().err


# --- Part 6: SDF synthetic acceptance case -----------------------------------


SDF_DESCRIPTION = (
    "Reconstruct Taxiway A; Construct Engineered Material Arresting System Safety Area "
    "Runway 17/35; Airport Noise Study; Residential Sound Insulation Program"
)


def test_sdf_synthetic_acceptance_case(session_factory):
    """Part 6: a synthetic fixture faithfully representing the known
    FY2026 SDF (Louisville Muhammad Ali International) row - the CURRENT
    FY2026 'Non-comp Discretionary Amt' header, a bundled multi-component
    description, and real-shaped funding amounts. Uses only a synthetic,
    in-memory fixture DB - never the real production SDF record."""
    with session_factory() as session:
        airport = Airport(name="Louisville Muhammad Ali International Airport", faa_code="SDF", country="USA")
        session.add(airport)
        session.commit()
        airport_id = airport.id

    # Discretionary/Total cells use the CONFIRMED real live-PDF shape
    # (RWI HQ "FAA AIP Live Preview" mission's own finding, AIP_FY26_5.pdf):
    # a stray internal space between the first two digits - '$ 2 5,...'
    # rather than '$25,...'. This is a direct regression test for that
    # exact real artifact, not merely a clean synthetic amount.
    table = [
        ["ST", "City", "Airport", "Loc ID", "Project Description",
         "Entitlement Amt", "Non-comp Discretionary Amt", "Total AIP"],
        ["KY", "Louisville", "Louisville Muhammad Ali International", "SDF", SDF_DESCRIPTION,
         "$ 203,940.00", "$ 2 5,272,482.00", "$ 2 5,476,422.00"],
    ]
    pdf_url = "https://www.faa.gov/airports/aip/2026_aip_grants/AIP_FY26_5.pdf"

    def fake_parse(pdf_bytes, *, source_pdf_url):
        return _with_fake_pdfplumber(table, lambda: parse_grant_pdf(b"x", source_pdf_url=source_pdf_url))

    import scripts.import_faa_aip_grants as module
    original = module.parse_grant_pdf
    module.parse_grant_pdf = fake_parse
    try:
        stats = import_year(2026, session_factory=session_factory, client=_client_serving(b"fake-pdf-bytes", pdf_url))
    finally:
        module.parse_grant_pdf = original

    # current FY2026 header parses (no AipGrantsError raised, one grant staged)
    assert stats["grants"] == 1
    assert stats["evidence_staged_resolved"] == 1
    assert stats["evidence_staged_unresolved"] == 0

    with session_factory() as session:
        # airport identity resolves only because SDF already exists in the
        # fixture DB - exactly one Airport, the pre-existing one, none
        # fabricated.
        assert [a.id for a in session.scalars(select(Airport)).all()] == [airport_id]

        # exactly Source + one project_construction SourceAssertion staged
        sources = session.scalars(select(Source)).all()
        assert len(sources) == 1
        assertions = session.scalars(select(SourceAssertion)).all()
        assert len(assertions) == 1
        assertion = assertions[0]
        assert assertion.airport_id == airport_id
        assert assertion.assertion_type == "project_construction"

        # zero Signal, zero ReviewerAction
        assert session.scalars(select(Signal)).all() == []
        assert session.scalars(select(ReviewerAction)).all() == []

        # ~$25.5M is not promoted into any Signal value - trivially true
        # (no Signal exists at all), and no structured amount field exists
        # on SourceAssertion either.
        assert not hasattr(assertion, "estimated_total_value_usd")

        # bundled description preserved without pretending the whole
        # amount is EMAS - the raw text carries every component verbatim,
        # not a single-purpose EMAS-only label.
        assert "Reconstruct Taxiway A" in assertion.raw_relevant_text
        assert "Engineered Material Arresting System" in assertion.raw_relevant_text
        assert "Airport Noise Study" in assertion.raw_relevant_text
        assert "Residential Sound Insulation Program" in assertion.raw_relevant_text
        # 3/15. The stray-internal-space artifact is correctly repaired -
        # the exact, contiguous, correctly-parsed Decimal values appear,
        # never a "None" left where an amount should be.
        assert "Discretionary Amt: 25272482.00" in assertion.raw_relevant_text
        assert "Total AIP: 25476422.00" in assertion.raw_relevant_text
        assert "Entitlement Amt: 203940.00" in assertion.raw_relevant_text
        assert "None" not in assertion.raw_relevant_text

        # Slice B guard compatibility
        check_lightweight_funding_path_eligibility(assertion)

    # replay is idempotent
    def fake_parse_2(pdf_bytes, *, source_pdf_url):
        return _with_fake_pdfplumber(table, lambda: parse_grant_pdf(b"x", source_pdf_url=source_pdf_url))

    module.parse_grant_pdf = fake_parse_2
    try:
        second = import_year(2026, session_factory=session_factory, client=_client_serving(b"fake-pdf-bytes", pdf_url))
    finally:
        module.parse_grant_pdf = original

    assert second["evidence_staged_resolved"] == 0
    assert second["already_imported"] == 1
    with session_factory() as session:
        assert len(session.scalars(select(SourceAssertion)).all()) == 1
