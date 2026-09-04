from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.acquisition.faa_aip_grants import (
    AipGrant,
    AipGrantsError,
    discover_grant_pdf_urls,
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


def test_import_year_stages_evidence_for_tracked_airport_creates_no_signal(session_factory):
    """1/2/6/9/10. Known Airport -> Source + one project_construction
    SourceAssertion only, zero Signal, raw evidence and provenance
    preserved."""
    with session_factory() as session:
        session.add(Airport(name="Ronald Reagan Washington National", faa_code="DCA", country="USA"))
        session.commit()

    pdf_url = "https://www.faa.gov/airports/aip/2026_aip_grants/AIP_FY26_1A.pdf"
    stats = import_year(
        2026, session_factory=session_factory, client=_client_serving(FIXTURE_PDF, pdf_url)
    )

    assert stats["pdfs"] == 1
    assert stats["grants"] == 2
    assert stats["evidence_staged_resolved"] == 1  # only DCA is tracked, not ORD
    assert stats["evidence_staged_unresolved"] == 1
    assert stats["unresolved_loc_ids"] == ["ORD"]

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
        assert "Construct Taxiway" in assertion.raw_relevant_text
        assert assertion.evidence_quality == "unverified_candidate"
        assert assertion.review_state == "unreviewed"
        check_lightweight_funding_path_eligibility(assertion)  # 4. Slice B guard compatibility


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
    """5. Unknown Airport -> unresolved evidence (airport_id=NULL), zero
    Airport created."""
    pdf_url = "https://www.faa.gov/airports/aip/2026_aip_grants/unknown.pdf"
    grant = AipGrant(
        state="TX", city="Nowhere", airport_name="Nowhere Municipal", loc_id="ZZZ",
        project_description="Construct Fence",
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
    """11. Identical replay stages nothing new."""
    with session_factory() as session:
        session.add(Airport(name="Ronald Reagan Washington National", faa_code="DCA", country="USA"))
        session.commit()

    pdf_url = "https://www.faa.gov/airports/aip/2026_aip_grants/AIP_FY26_1A.pdf"
    first = import_year(2026, session_factory=session_factory, client=_client_serving(FIXTURE_PDF, pdf_url))
    second = import_year(2026, session_factory=session_factory, client=_client_serving(FIXTURE_PDF, pdf_url))

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

    table = [
        ["ST", "City", "Airport", "Loc ID", "Project Description",
         "Entitlement Amt", "Non-comp Discretionary Amt", "Total AIP"],
        ["KY", "Louisville", "Louisville Muhammad Ali International", "SDF", SDF_DESCRIPTION,
         "$203,940.00", "$25,272,482.00", "$25,476,422.00"],
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
        assert "25272482" in assertion.raw_relevant_text.replace(",", "").replace(".00", "")
        assert "203940" in assertion.raw_relevant_text.replace(",", "").replace(".00", "")

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
