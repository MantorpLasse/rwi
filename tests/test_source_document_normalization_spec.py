from dataclasses import dataclass
from datetime import date
from typing import Optional
from unicodedata import normalize
from urllib.parse import urlparse


UNKNOWN_PUBLISHER = "Unknown Publisher"


@dataclass(frozen=True)
class LegacySourceRow:
    legacy_id: int
    project_id: int
    title: str
    source_type: str
    publisher: Optional[str]
    url: str
    published_date: date


@dataclass(frozen=True)
class DocumentSpec:
    source_name: str
    title: str
    document_type: str
    url: str
    published_date: date
    incomplete_metadata: bool


@dataclass(frozen=True)
class NormalizationPlan:
    sources: frozenset[str]
    documents: frozenset[DocumentSpec]
    project_document_links: frozenset[tuple[int, DocumentSpec]]
    unresolved_legacy_ids: tuple[int, ...]


LEGACY_ROWS = (
    LegacySourceRow(1, 1, "Resolution 025-2024 – Amended Common Ground Recommendation Airport Map", "ALP", "Pitkin County", "http://www.aspenairport.com/wp-content/uploads/2024/07/bocc.res_.025.2024-Amending-Res-105-2020.pdf", date(2024, 5, 16)),
    LegacySourceRow(2, 2, "Runway 6 Departure End EMAS Project", "Procurement", "Manchester-Boston Regional Airport", "https://www.flymanchester.com/", date(2026, 6, 1)),
    LegacySourceRow(3, 3, "FAA Airport Construction Impact Report", "FAA", "FAA", "https://www.faa.gov/", date(2026, 7, 1)),
    LegacySourceRow(4, 4, "FAA Airport Construction Impact Report", "FAA", "FAA", "https://www.faa.gov/", date(2026, 7, 1)),
    LegacySourceRow(5, 5, "Runway 8/26 Runway Safety Improvements Project", "Airport", "Fulton County", "https://www.fultoncountyga.gov/", date(2026, 5, 11)),
    LegacySourceRow(6, 6, "2026–2031 Capital Improvements Program", "CIP", "Broome County", "https://broomecountyny.gov/", date(2026, 1, 1)),
    LegacySourceRow(7, 7, "California Aeronautics Capital Improvement Plan", "CIP", "Caltrans", "https://dot.ca.gov/", date(2025, 6, 1)),
    LegacySourceRow(8, 8, "Metropolitan Airports Commission Capital Improvement Program", "CIP", "Metropolitan Airports Commission", "https://www.metroairports.org/", date(2025, 12, 1)),
    LegacySourceRow(9, 9, "Port Authority Board Agenda – EMAS planning authorization", "Authority", "Port Authority of New York and New Jersey", "https://www.panynj.gov/", date(2026, 3, 19)),
    LegacySourceRow(10, 10, "Final Environmental Assessment", "Environmental", "Cape Cod Gateway Airport", "https://flyhya.com/", date(2025, 11, 4)),
    LegacySourceRow(11, 11, "MKC Airport Master Plan – Existing Conditions", "Master Plan", "Kansas City Aviation Department", "https://mkc.airportstudy.net/", date(2026, 1, 7)),
    LegacySourceRow(12, 12, "Internal watch item", "Watchlist", "Runway Safe Intelligence", "https://www.flychicago.com/midway/", date(2026, 7, 17)),
)


def _publisher_name(value: Optional[str]) -> str:
    if value is None or not value.strip():
        return UNKNOWN_PUBLISHER
    return " ".join(normalize("NFC", value).split())


def _is_generic_homepage(url: str) -> bool:
    return urlparse(url).path in {"", "/"}


def _is_internal_watch_item(row: LegacySourceRow) -> bool:
    return row.source_type == "Watchlist" and row.publisher == "Runway Safe Intelligence"


def _normalization_plan(
    rows: tuple[LegacySourceRow, ...], *, include_unresolved: bool
) -> NormalizationPlan:
    sources: set[str] = set()
    documents: set[DocumentSpec] = set()
    links: set[tuple[int, DocumentSpec]] = set()
    unresolved: list[int] = []

    for row in rows:
        if _is_internal_watch_item(row) and not include_unresolved:
            unresolved.append(row.legacy_id)
            continue

        source_name = _publisher_name(row.publisher)
        document = DocumentSpec(
            source_name=source_name,
            title=row.title,
            document_type=row.source_type,
            url=row.url,
            published_date=row.published_date,
            incomplete_metadata=_is_generic_homepage(row.url),
        )
        sources.add(source_name)
        documents.add(document)
        links.add((row.project_id, document))

    return NormalizationPlan(
        sources=frozenset(sources),
        documents=frozenset(documents),
        project_document_links=frozenset(links),
        unresolved_legacy_ids=tuple(unresolved),
    )


def test_current_fixture_contains_twelve_legacy_project_owned_rows():
    assert len(LEGACY_ROWS) == 12
    assert {row.project_id for row in LEGACY_ROWS} == set(range(1, 13))


def test_complete_legacy_characterization_is_eleven_sources_documents_and_twelve_links():
    plan = _normalization_plan(LEGACY_ROWS, include_unresolved=True)

    assert len(plan.sources) == 11
    assert len(plan.documents) == 11
    assert len(plan.project_document_links) == 12


def test_one_faa_document_is_relevant_to_two_projects():
    plan = _normalization_plan(LEGACY_ROWS, include_unresolved=True)
    faa_document = next(
        document
        for document in plan.documents
        if document.title == "FAA Airport Construction Impact Report"
    )

    assert {project_id for project_id, document in plan.project_document_links if document == faa_document} == {3, 4}


def test_homepage_urls_are_retained_and_flagged_as_incomplete():
    plan = _normalization_plan(LEGACY_ROWS, include_unresolved=True)
    document = next(
        document
        for document in plan.documents
        if document.title == "Runway 6 Departure End EMAS Project"
    )

    assert document.url == "https://www.flymanchester.com/"
    assert document.incomplete_metadata is True


def test_publisher_matching_is_normalized_but_case_sensitive_and_has_no_aliases():
    rows = (
        LegacySourceRow(101, 101, "A", "Report", " FAA ", "https://example.test/a", date(2026, 1, 1)),
        LegacySourceRow(102, 102, "B", "Report", "FAA", "https://example.test/b", date(2026, 1, 1)),
        LegacySourceRow(103, 103, "C", "Report", "faa", "https://example.test/c", date(2026, 1, 1)),
        LegacySourceRow(104, 104, "D", "Report", "Federal Aviation Administration", "https://example.test/d", date(2026, 1, 1)),
    )

    plan = _normalization_plan(rows, include_unresolved=True)

    assert plan.sources == {"FAA", "faa", "Federal Aviation Administration"}


def test_unknown_publishers_are_explicit_and_never_null():
    rows = (
        LegacySourceRow(201, 201, "Unknown report", "Report", None, "https://example.test/a", date(2026, 1, 1)),
        LegacySourceRow(202, 202, "Unknown report", "Report", "   ", "https://example.test/a", date(2026, 1, 1)),
    )

    plan = _normalization_plan(rows, include_unresolved=True)

    assert plan.sources == {UNKNOWN_PUBLISHER}
    assert all(document.source_name == UNKNOWN_PUBLISHER for document in plan.documents)


def test_internal_watch_item_is_explicitly_unresolved_and_excluded_from_automatic_counts():
    plan = _normalization_plan(LEGACY_ROWS, include_unresolved=False)

    assert plan.unresolved_legacy_ids == (12,)
    assert len(plan.sources) == 10
    assert len(plan.documents) == 10
    assert len(plan.project_document_links) == 11
    assert all(document.title != "Internal watch item" for document in plan.documents)


def test_normalized_identity_does_not_depend_on_legacy_integer_ids():
    renumbered = tuple(
        LegacySourceRow(
            legacy_id=row.legacy_id + 10_000,
            project_id=row.project_id,
            title=row.title,
            source_type=row.source_type,
            publisher=row.publisher,
            url=row.url,
            published_date=row.published_date,
        )
        for row in LEGACY_ROWS
    )

    assert _normalization_plan(LEGACY_ROWS, include_unresolved=True) == _normalization_plan(
        renumbered, include_unresolved=True
    )
