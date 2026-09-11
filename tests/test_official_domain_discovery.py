"""RWI HQ "Official-Domain Document Discovery Pass" mission - offline tests
for app.services.official_domain_discovery. In-memory SQLite DB, matching
this repo's own established test convention (see e.g.
tests/test_update_ase_runway_relocation_note.py). No network access."""

from __future__ import annotations

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Signal, Source, SourceAssertion
from app.services.official_domain_discovery import get_known_official_hostnames


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _airport(session, *, iata: str = "SDF") -> Airport:
    airport = Airport(name=f"Test Airport {iata}", iata_code=iata, country="USA")
    session.add(airport)
    session.flush()
    return airport


def _official_source(session, *, url: str) -> Source:
    source = Source(title="Official doc", source_type="Authority", reliability_level="official", url=url)
    session.add(source)
    session.flush()
    return source


_sa_counter = iter(range(1, 100_000))


def _unreviewed_sa(session, *, airport_id: int, source_id: int) -> SourceAssertion:
    n = next(_sa_counter)
    assertion = SourceAssertion(
        source_id=source_id, airport_id=airport_id, assertion_type="project_construction",
        raw_relevant_text="text", evidence_quality="unverified_candidate", review_state="unreviewed",
        source_locator=f"page:1;chars:{n}-{n + 10}", raw_fragment_hash=f"hash{n}",
    )
    session.add(assertion)
    session.flush()
    return assertion


# 1. Official Source connected via SourceAssertion.airport_id yields hostname.
def test_official_source_via_source_assertion_yields_hostname():
    with Session(_engine()) as session:
        airport = _airport(session)
        source = _official_source(session, url="https://www.flylouisville.com/wp-content/uploads/x.pdf")
        _unreviewed_sa(session, airport_id=airport.id, source_id=source.id)

        hostnames = get_known_official_hostnames(session, airport.id)
        assert hostnames == ("flylouisville.com",)


# Official Source connected via Signal.airport_id (not just SourceAssertion) also counts.
def test_official_source_via_signal_yields_hostname():
    with Session(_engine()) as session:
        airport = _airport(session)
        source = _official_source(session, url="https://www.flylouisville.com/board-minutes.pdf")
        signal = Signal(
            airport_id=airport.id, source_id=source.id, title="Test signal",
            category="new_installation", confidence="high", status="identified", published=True,
        )
        session.add(signal)
        session.commit()

        hostnames = get_known_official_hostnames(session, airport.id)
        assert hostnames == ("flylouisville.com",)


# 2. "www." normalization.
def test_www_prefix_is_stripped():
    with Session(_engine()) as session:
        airport = _airport(session)
        source = _official_source(session, url="https://WWW.FlyLouisville.com/x.pdf")
        _unreviewed_sa(session, airport_id=airport.id, source_id=source.id)

        hostnames = get_known_official_hostnames(session, airport.id)
        assert hostnames == ("flylouisville.com",)


# 3. Duplicate hostnames collapse.
def test_duplicate_hostnames_collapse_to_one():
    with Session(_engine()) as session:
        airport = _airport(session)
        source1 = _official_source(session, url="https://www.flylouisville.com/a.pdf")
        source2 = _official_source(session, url="https://flylouisville.com/b.pdf")
        _unreviewed_sa(session, airport_id=airport.id, source_id=source1.id)
        _unreviewed_sa(session, airport_id=airport.id, source_id=source2.id)

        hostnames = get_known_official_hostnames(session, airport.id)
        assert hostnames == ("flylouisville.com",)


# 4. Malformed/missing URLs are ignored, never raise.
def test_malformed_or_missing_urls_are_ignored():
    with Session(_engine()) as session:
        airport = _airport(session)
        good = _official_source(session, url="https://www.flylouisville.com/a.pdf")
        blank = Source(title="No URL", source_type="Authority", reliability_level="official", url=None)
        session.add(blank)
        session.flush()
        _unreviewed_sa(session, airport_id=airport.id, source_id=good.id)
        _unreviewed_sa(session, airport_id=airport.id, source_id=blank.id)

        hostnames = get_known_official_hostnames(session, airport.id)
        assert hostnames == ("flylouisville.com",)


# 5. Non-official Sources never contribute.
def test_non_official_reliability_source_does_not_contribute():
    with Session(_engine()) as session:
        airport = _airport(session)
        unofficial = Source(
            title="Unverified blog", source_type="web_discovery",
            reliability_level="unverified", url="https://airport-world.com/article",
        )
        session.add(unofficial)
        session.flush()
        _unreviewed_sa(session, airport_id=airport.id, source_id=unofficial.id)

        hostnames = get_known_official_hostnames(session, airport.id)
        assert hostnames == ()


# 6. A Source belonging to a different airport does not contribute.
def test_source_from_another_airport_does_not_contribute():
    with Session(_engine()) as session:
        sdf = _airport(session, iata="SDF")
        other = _airport(session, iata="XXX")
        source = _official_source(session, url="https://www.flylouisville.com/a.pdf")
        _unreviewed_sa(session, airport_id=other.id, source_id=source.id)

        hostnames = get_known_official_hostnames(session, sdf.id)
        assert hostnames == ()


# 7. Multiple official Sources on the same domain still produce one hostname
#    (explicit variant with three sources, different URL paths).
def test_multiple_official_sources_same_domain_produce_one_hostname():
    with Session(_engine()) as session:
        airport = _airport(session)
        for i, path in enumerate(("a.pdf", "b.pdf", "c.pdf")):
            source = _official_source(session, url=f"https://www.flylouisville.com/{path}")
            _unreviewed_sa(session, airport_id=airport.id, source_id=source.id)

        hostnames = get_known_official_hostnames(session, airport.id)
        assert hostnames == ("flylouisville.com",)


# More than one genuinely distinct official hostname is returned, sorted.
def test_multiple_distinct_official_hostnames_returned_sorted():
    with Session(_engine()) as session:
        airport = _airport(session)
        s1 = _official_source(session, url="https://www.flylouisville.com/a.pdf")
        s2 = _official_source(session, url="https://www.faa.gov/b.pdf")
        _unreviewed_sa(session, airport_id=airport.id, source_id=s1.id)
        _unreviewed_sa(session, airport_id=airport.id, source_id=s2.id)

        hostnames = get_known_official_hostnames(session, airport.id)
        assert hostnames == ("faa.gov", "flylouisville.com")


# An airport with zero governed evidence at all returns empty, not an error.
def test_airport_with_no_evidence_returns_empty_tuple():
    with Session(_engine()) as session:
        airport = _airport(session)
        hostnames = get_known_official_hostnames(session, airport.id)
        assert hostnames == ()


# 8. No persistence occurs - row counts are identical before and after.
def test_no_persistence_occurs():
    with Session(_engine()) as session:
        airport = _airport(session)
        source = _official_source(session, url="https://www.flylouisville.com/a.pdf")
        _unreviewed_sa(session, airport_id=airport.id, source_id=source.id)
        session.commit()

        before = {
            "Source": session.scalar(select(func.count()).select_from(Source)),
            "SourceAssertion": session.scalar(select(func.count()).select_from(SourceAssertion)),
            "Signal": session.scalar(select(func.count()).select_from(Signal)),
        }
        get_known_official_hostnames(session, airport.id)
        after = {
            "Source": session.scalar(select(func.count()).select_from(Source)),
            "SourceAssertion": session.scalar(select(func.count()).select_from(SourceAssertion)),
            "Signal": session.scalar(select(func.count()).select_from(Signal)),
        }
        assert before == after
