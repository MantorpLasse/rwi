"""RWI HQ "Official-Domain Document Discovery Pass" mission - offline tests
for app.services.official_domain_discovery. In-memory SQLite DB, matching
this repo's own established test convention (see e.g.
tests/test_update_ase_runway_relocation_note.py). No network access."""

from __future__ import annotations

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Signal, Source, SourceAssertion
from app.services.official_domain_discovery import (
    get_known_official_hostnames,
    rank_official_hostnames,
    select_preferred_official_hostname,
)


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


def _reviewed_sa(session, *, airport_id: int, source_id: int) -> SourceAssertion:
    n = next(_sa_counter)
    assertion = SourceAssertion(
        source_id=source_id, airport_id=airport_id, assertion_type="project_construction",
        raw_relevant_text="text", evidence_quality="unverified_candidate", review_state="reviewed",
        source_locator=f"page:1;chars:{n}-{n + 10}", raw_fragment_hash=f"hash{n}",
    )
    session.add(assertion)
    session.flush()
    return assertion


def _signal(session, *, airport_id: int, source_id: int, published: bool) -> Signal:
    signal = Signal(
        airport_id=airport_id, source_id=source_id, title="Test signal", category="new_installation",
        confidence="high", status="identified", published=published,
    )
    session.add(signal)
    session.flush()
    return signal


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


# --- Preferred-domain selection (RWI HQ "Official-Domain Discovery -
# Smarter Domain Selection" mission) -----------------------------------------
#
# Deliberately generic fixture domains ("authority.example.org",
# "regulator.gov", "airport.example.com") - the selector must never be
# tuned to any specific real hostname (mission's own explicit "SDF is a
# benchmark fixture, not a special case" instruction).


# 1. A domain backing a PUBLISHED Signal outranks one backing only a
#    reviewed SourceAssertion, even though the assertion-only domain has
#    more governed official Source rows.
def test_published_signal_backed_domain_outranks_assertion_only_domain():
    with Session(_engine()) as session:
        airport = _airport(session)

        published_source = _official_source(session, url="https://authority.example.org/a.pdf")
        _signal(session, airport_id=airport.id, source_id=published_source.id, published=True)

        for path in ("b.pdf", "c.pdf", "d.pdf"):
            s = _official_source(session, url=f"https://faa.gov/{path}")
            _reviewed_sa(session, airport_id=airport.id, source_id=s.id)

        preferred = select_preferred_official_hostname(session, airport.id)
        assert preferred == "authority.example.org"

        ranked = rank_official_hostnames(session, airport.id)
        assert ranked[0].hostname == "authority.example.org"
        assert ranked[0].reason == "source domain used by published Signal(s)"


# 2. A domain backing an UNPUBLISHED Signal still outranks a domain backing
#    only a reviewed SourceAssertion (tier 2 beats tier 3).
def test_unpublished_signal_backed_domain_outranks_assertion_only_domain():
    with Session(_engine()) as session:
        airport = _airport(session)

        signal_source = _official_source(session, url="https://authority.example.org/a.pdf")
        _signal(session, airport_id=airport.id, source_id=signal_source.id, published=False)

        assertion_source = _official_source(session, url="https://faa.gov/b.pdf")
        _reviewed_sa(session, airport_id=airport.id, source_id=assertion_source.id)

        preferred = select_preferred_official_hostname(session, airport.id)
        assert preferred == "authority.example.org"

        ranked = rank_official_hostnames(session, airport.id)
        assert ranked[0].reason == "source domain used by Signal(s) (not yet published)"


# 3. With no Signal or reviewed-SourceAssertion distinction at all, more
#    airport-linked official Source rows on one domain breaks the tie.
def test_higher_official_source_count_breaks_lower_tier_ties():
    with Session(_engine()) as session:
        airport = _airport(session)

        for path in ("a.pdf", "b.pdf", "c.pdf", "d.pdf", "e.pdf"):
            s = _official_source(session, url=f"https://regulator.gov/{path}")
            _unreviewed_sa(session, airport_id=airport.id, source_id=s.id)
        for path in ("a.pdf", "b.pdf"):
            s = _official_source(session, url=f"https://airport.example.com/{path}")
            _unreviewed_sa(session, airport_id=airport.id, source_id=s.id)

        preferred = select_preferred_official_hostname(session, airport.id)
        assert preferred == "regulator.gov"

        ranked = rank_official_hostnames(session, airport.id)
        assert ranked[0].official_source_count == 5
        assert ranked[0].reason == "most airport-linked official Source(s) on this domain"


# 4. Exact ties on every prior tier fall back to alphabetical order.
def test_exact_tie_falls_back_to_alphabetical_hostname():
    with Session(_engine()) as session:
        airport = _airport(session)
        s1 = _official_source(session, url="https://zzz-domain.example.com/a.pdf")
        s2 = _official_source(session, url="https://aaa-domain.example.com/a.pdf")
        _unreviewed_sa(session, airport_id=airport.id, source_id=s1.id)
        _unreviewed_sa(session, airport_id=airport.id, source_id=s2.id)

        preferred = select_preferred_official_hostname(session, airport.id)
        assert preferred == "aaa-domain.example.com"

        ranked = rank_official_hostnames(session, airport.id)
        assert ranked[0].reason == "alphabetical tiebreak (no distinguishing governed Signal/review found)"


# 5. Malformed/non-official Sources are ignored by the ranker exactly like
#    get_known_official_hostnames() itself already ignores them.
def test_ranking_ignores_malformed_and_non_official_sources():
    with Session(_engine()) as session:
        airport = _airport(session)
        good = _official_source(session, url="https://authority.example.org/a.pdf")
        _unreviewed_sa(session, airport_id=airport.id, source_id=good.id)

        unofficial = Source(
            title="Blog", source_type="web_discovery", reliability_level="unverified",
            url="https://unofficial-blog.example.com/x",
        )
        session.add(unofficial)
        session.flush()
        _unreviewed_sa(session, airport_id=airport.id, source_id=unofficial.id)

        blank = Source(title="No URL", source_type="Authority", reliability_level="official", url=None)
        session.add(blank)
        session.flush()
        _unreviewed_sa(session, airport_id=airport.id, source_id=blank.id)

        ranked = rank_official_hostnames(session, airport.id)
        assert [r.hostname for r in ranked] == ["authority.example.org"]


# 6. No governed official domain at all -> None, never an error.
def test_select_preferred_returns_none_when_no_official_domain():
    with Session(_engine()) as session:
        airport = _airport(session)
        assert select_preferred_official_hostname(session, airport.id) is None
        assert rank_official_hostnames(session, airport.id) == ()


# 7. No persistence occurs for the ranking/selection functions either.
def test_ranking_and_selection_do_not_persist():
    with Session(_engine()) as session:
        airport = _airport(session)
        source = _official_source(session, url="https://authority.example.org/a.pdf")
        _signal(session, airport_id=airport.id, source_id=source.id, published=True)
        session.commit()

        before = {
            "Source": session.scalar(select(func.count()).select_from(Source)),
            "SourceAssertion": session.scalar(select(func.count()).select_from(SourceAssertion)),
            "Signal": session.scalar(select(func.count()).select_from(Signal)),
        }
        rank_official_hostnames(session, airport.id)
        select_preferred_official_hostname(session, airport.id)
        after = {
            "Source": session.scalar(select(func.count()).select_from(Source)),
            "SourceAssertion": session.scalar(select(func.count()).select_from(SourceAssertion)),
            "Signal": session.scalar(select(func.count()).select_from(Signal)),
        }
        assert before == after


# 8. Genericity: the selector's outcome depends ONLY on governance shape
# (published Signal > Signal > reviewed SA > count > alphabetical), never
# on which literal hostname string is involved - proven by re-running the
# exact same governance shape with the hostnames' alphabetical order
# deliberately reversed relative to test 1 above, and getting the same
# governance-driven winner both times.
def test_selection_is_generic_not_hostname_text_driven():
    with Session(_engine()) as session:
        airport = _airport(session)
        # "aaa..." (alphabetically first) backs only a reviewed assertion;
        # "zzz..." (alphabetically last) backs a published Signal. If the
        # selector were secretly just alphabetical, "aaa..." would win -
        # it must not.
        assertion_only = _official_source(session, url="https://aaa-assertion-only.example.com/a.pdf")
        _reviewed_sa(session, airport_id=airport.id, source_id=assertion_only.id)
        published = _official_source(session, url="https://zzz-published-signal.example.com/b.pdf")
        _signal(session, airport_id=airport.id, source_id=published.id, published=True)

        preferred = select_preferred_official_hostname(session, airport.id)
        assert preferred == "zzz-published-signal.example.com"


# --- SDF-shaped benchmark fixture (generic domain names - see mission's
# own "SDF is a benchmark fixture, not a special case" instruction; no
# real airport_id/hostname is hardcoded anywhere in this test) -------------


def test_sdf_shaped_fixture_prefers_airport_operator_domain_via_signal_provenance():
    """Reproduces the REAL SDF governance shape that motivated this
    mission (three official domains; the FAA-style regulator domain backs
    several unreviewed/bundled assertions; the airport-operator-style
    domain backs the airport's own published, governed Signal) using
    entirely generic fixture domain names. Proves the generic rules alone
    - never a name/domain special-case - select the operator-style
    domain, because of its governed Signal provenance."""
    with Session(_engine()) as session:
        airport = _airport(session)

        # Regulator-style domain: several official Sources, only
        # unreviewed SourceAssertions, no Signal at all - matches the real
        # faa.gov/explore.dot.gov shape (bundled AIP-grant Sources with no
        # governed Signal yet).
        for path in ("grant1.pdf", "grant2.pdf"):
            s = _official_source(session, url=f"https://regulator-style.example.gov/{path}")
            _unreviewed_sa(session, airport_id=airport.id, source_id=s.id)

        # Airport-operator-style domain: one official Source, backing a
        # real, published, governed Signal - matches the real
        # flylouisville.com shape (Source 108 -> SA262 -> Signal 71,
        # published).
        operator_source = _official_source(session, url="https://operator-style.example.com/board-minutes.pdf")
        _unreviewed_sa(session, airport_id=airport.id, source_id=operator_source.id)
        _signal(session, airport_id=airport.id, source_id=operator_source.id, published=True)

        preferred = select_preferred_official_hostname(session, airport.id)
        assert preferred == "operator-style.example.com"

        ranked = rank_official_hostnames(session, airport.id)
        assert ranked[0].hostname == "operator-style.example.com"
        assert ranked[0].published_signal_count == 1
        assert ranked[1].hostname == "regulator-style.example.gov"
        assert ranked[1].published_signal_count == 0
