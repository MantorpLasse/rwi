"""RWI HQ "Discovery Research Loop V1, Slice 2" - offline tests for
scripts/research_airport_clue.py. Fake SearchProvider only (mirrors
tests/test_review_temporal_followup.py's own _FakeProvider convention) -
no network. Isolated temp-file SQLite DB for SourceAssertion loading -
never touches data/runway_safe.db."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import scripts.research_airport_clue as cli
from app.database import Base
from app.discovery.query import SearchQuery
from app.discovery.search import SearchOutcome, SearchOutcomeStatus, SearchResult
from app.models import Airport, Source, SourceAssertion

_NOW = datetime(2026, 9, 4, tzinfo=timezone.utc)

SDF_TEXT = (
    "Reconstruct Taxiway,Construct Engineered Material Arresting System Safety Area,"
    "Conduct Noise Compatibility Plan Study,Noise Mitigation Measures for Residences "
    "within 65-69 DNL"
)


class _FakeProvider:
    name = "fake"

    def __init__(self, canned: "dict[str, SearchOutcome]"):
        self._canned = canned

    def search(self, query: SearchQuery) -> SearchOutcome:
        return self._canned.get(query.rendered, SearchOutcome(query=query, status=SearchOutcomeStatus.NO_RESULTS))


def _result(query: SearchQuery, url: str, *, title: str = "A result") -> SearchResult:
    return SearchResult(query=query, rank=1, title=title, url=url, snippet="", discovered_at=_NOW, provider="fake")


def _seed_source_assertion(db_path: str, *, text: str = SDF_TEXT) -> int:
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        source = Source(title="Test Source", source_type="aip_grant", reliability_level="official", external_id="faa_aip:test:1")
        session.add(source)
        session.flush()
        assertion = SourceAssertion(
            source_id=source.id, assertion_type="project_construction",
            raw_relevant_text=text, source_locator="page:1;chars:0-100",
            raw_fragment_hash="deadbeef", artifact_identity="artifact:test-1",
            evidence_quality="unverified_candidate", review_state="unreviewed",
        )
        session.add(assertion)
        session.commit()
        return assertion.id


_SDF_ARGS = [
    "--dimension", "runway_end", "--dimension", "installation_type", "--dimension",
    "project_phase", "--dimension", "timing", "--dimension", "supplier",
    "--search-name", "Louisville Muhammad Ali International Airport",
    "--search-iata", "SDF", "--search-icao", "KSDF",
]


# --- Input contract / DB read-only boundary ---------------------------------


def test_load_evidence_text_reads_exact_field(tmp_path):
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion(db_path)
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        text = cli.load_evidence_text(session, aid)
    assert text == SDF_TEXT


def test_missing_source_assertion_refused(tmp_path, capsys):
    db_path = str(tmp_path / "test.db")
    _seed_source_assertion(db_path)
    exit_code = cli.main(["--database", db_path, "--source-assertion-id", "9999", *_SDF_ARGS])
    assert exit_code == 2
    assert "Refused" in capsys.readouterr().err


def test_missing_dimension_refused(tmp_path, capsys):
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion(db_path)
    exit_code = cli.main([
        "--database", db_path, "--source-assertion-id", str(aid),
        "--search-name", "Louisville Muhammad Ali International Airport",
    ])
    assert exit_code == 2
    assert "dimension" in capsys.readouterr().err.lower()


def test_db_write_impossible_via_this_script(tmp_path):
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion(db_path)
    before = open(db_path, "rb").read()
    cli.main(["--database", db_path, "--source-assertion-id", str(aid), *_SDF_ARGS])
    after = open(db_path, "rb").read()
    assert before == after


# --- Plan-only mode (no --allow-live-network) --------------------------------


def test_plan_only_mode_makes_no_network_call(tmp_path, capsys, monkeypatch):
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion(db_path)

    class _ExplodingProvider:
        name = "brave"

        def search(self, query):
            raise AssertionError("must never be called without --allow-live-network")

    monkeypatch.setitem(cli.PROVIDER_REGISTRY, "brave", _ExplodingProvider())
    exit_code = cli.main(["--database", db_path, "--source-assertion-id", str(aid), *_SDF_ARGS])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "NO SEARCH PROVIDER EXECUTED" in out
    assert "RUNWAY_END" in out
    assert "EMAS" in out


# --- Executed run -------------------------------------------------------------


def test_allow_live_network_executes_against_registered_provider(tmp_path, capsys, monkeypatch):
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion(db_path)

    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        text = cli.load_evidence_text(session, aid)
    from app.services.discovery_temporal_followup import AirportSearchContext
    from app.services.research_question_planning import ResearchClue, ResearchDimension, plan_research_search_queries
    context = AirportSearchContext(name="Louisville Muhammad Ali International Airport", iata_code="SDF", icao_code="KSDF")
    clue = ResearchClue(
        evidence_text=text, airport_context=context,
        unresolved_dimensions=(
            ResearchDimension.RUNWAY_END, ResearchDimension.INSTALLATION_TYPE,
            ResearchDimension.PROJECT_PHASE, ResearchDimension.TIMING, ResearchDimension.SUPPLIER,
        ),
    )
    planned = plan_research_search_queries(clue)
    canned = {
        planned[0].search_query.rendered: SearchOutcome(
            query=planned[0].search_query, status=SearchOutcomeStatus.OK,
            results=(_result(planned[0].search_query, "https://faa.gov/sdf-doc", title="SDF EMAS project document"),),
        )
    }
    monkeypatch.setitem(cli.PROVIDER_REGISTRY, "brave", _FakeProvider(canned))

    exit_code = cli.main([
        "--database", db_path, "--source-assertion-id", str(aid), *_SDF_ARGS, "--allow-live-network",
    ])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "SEARCH SUMMARY" in out
    assert "7 queries" in out  # RUNWAY_END(1)+INSTALLATION_TYPE(2)+PROJECT_PHASE(2)+TIMING(1)+SUPPLIER(1)
    assert "https://faa.gov/sdf-doc" in out
    assert "STOP" in out
    # Slice 3's own honest-status vocabulary, present for every dimension
    assert "Search status: CANDIDATES_FOUND" in out or "Search status: NO_CANDIDATES_FOUND" in out
    assert "Research status: STILL UNRESOLVED" in out
    # the old, removed, false-resolution section must never reappear
    assert "UNRESOLVED / NO RESULT" not in out
    for banned in ("Search status: RESOLVED", "Search status: CONFIRMED", "resolved_dimensions"):
        assert banned not in out


def test_json_output_shape(tmp_path, capsys, monkeypatch):
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion(db_path)
    monkeypatch.setitem(cli.PROVIDER_REGISTRY, "brave", _FakeProvider({}))

    exit_code = cli.main([
        "--database", db_path, "--source-assertion-id", str(aid), *_SDF_ARGS, "--allow-live-network", "--json",
    ])
    import json
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert len(payload["questions"]) == 5
    assert len(payload["query_outcomes"]) == 7
    assert payload["triaged_candidates"] == []
    assert payload["network_used"] is True
    assert "unresolved_dimensions" not in payload  # the removed, defective field must never reappear
    for q in payload["questions"]:
        assert q["search_status"] == "NO_CANDIDATES_FOUND"
        assert q["research_status"] == "STILL_UNRESOLVED"
        assert len(q["queries"]) >= 1


def test_json_plan_only_mode_has_null_search_status(tmp_path, capsys):
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion(db_path)
    exit_code = cli.main(["--database", db_path, "--source-assertion-id", str(aid), *_SDF_ARGS, "--json"])
    import json
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["network_used"] is False
    assert payload["query_outcomes"] == []
    for q in payload["questions"]:
        assert q["search_status"] is None
        assert q["research_status"] is None


# --- Literal-anchor CLI opt-in (RWI HQ "Discovery Research Loop V1 - Slice 5F") ---

SA258_TEXT = (
    "On the airfield, reconstruction is expected on Taxiways B and D, phase 1 of the East "
    "Runway’s Engineered Materials Arresting System (EMAS) will be installed and electrical "
    "work will continue including the completion of the SDF MicroGrid."
)


def test_cli_without_flag_is_unchanged(tmp_path, capsys):
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion(db_path, text=SA258_TEXT)
    exit_code = cli.main(["--database", db_path, "--source-assertion-id", str(aid), *_SDF_ARGS])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Literal anchors: disabled" in out
    assert "Extracted literal anchors" not in out


def test_cli_with_flag_enables_anchor_aware_planning_and_shows_anchors(tmp_path, capsys):
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion(db_path, text=SA258_TEXT)
    exit_code = cli.main(["--database", db_path, "--source-assertion-id", str(aid), *_SDF_ARGS, "--use-literal-anchors"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Literal anchors: ENABLED" in out
    assert "'East Runway' [DIRECTIONAL_RUNWAY_NAME] -> RUNWAY_END" in out
    assert "'phase 1' [PHASE_LITERAL] -> PROJECT_PHASE" in out
    assert 'EMAS "East Runway"' in out
    assert 'EMAS "phase 1"' in out


def test_cli_flag_never_writes_to_database(tmp_path):
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion(db_path, text=SA258_TEXT)
    before = open(db_path, "rb").read()
    cli.main(["--database", db_path, "--source-assertion-id", str(aid), *_SDF_ARGS, "--use-literal-anchors"])
    after = open(db_path, "rb").read()
    assert before == after


def test_json_flag_reports_anchors_and_enabled_state(tmp_path, capsys, monkeypatch):
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion(db_path, text=SA258_TEXT)
    monkeypatch.setitem(cli.PROVIDER_REGISTRY, "brave", _FakeProvider({}))
    exit_code = cli.main([
        "--database", db_path, "--source-assertion-id", str(aid), *_SDF_ARGS,
        "--use-literal-anchors", "--allow-live-network", "--json",
    ])
    import json
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["literal_anchors_enabled"] is True
    anchors = {(a["text"], a["kind"], a["dimension_hint"]) for a in payload["literal_anchors"]}
    assert ("East Runway", "DIRECTIONAL_RUNWAY_NAME", "RUNWAY_END") in anchors
    assert ("phase 1", "PHASE_LITERAL", "PROJECT_PHASE") in anchors
    all_queries = {q for question in payload["questions"] for q in question["queries"]}
    assert len(all_queries) == 9


def test_json_flag_omitted_reports_disabled_and_empty_anchors(tmp_path, capsys):
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion(db_path, text=SA258_TEXT)
    exit_code = cli.main(["--database", db_path, "--source-assertion-id", str(aid), *_SDF_ARGS, "--json"])
    import json
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["literal_anchors_enabled"] is False
    assert payload["literal_anchors"] == []


def test_json_installation_type_and_project_phase_have_two_queries(tmp_path, capsys):
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion(db_path)
    exit_code = cli.main(["--database", db_path, "--source-assertion-id", str(aid), *_SDF_ARGS, "--json"])
    import json
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    by_dimension = {q["dimension"]: q["queries"] for q in payload["questions"]}
    assert len(by_dimension["INSTALLATION_TYPE"]) == 2
    assert len(by_dimension["PROJECT_PHASE"]) == 2
    assert len(by_dimension["RUNWAY_END"]) == 1
    assert len(by_dimension["TIMING"]) == 1
    assert len(by_dimension["SUPPLIER"]) == 1


# --- Official-domain discovery CLI opt-in (RWI HQ "Official-Domain Document
# Discovery Pass" mission - SDF/flylouisville.com benchmark) -----------------


def _seed_source_assertion_with_airport(db_path: str, *, text: str = SDF_TEXT, official_url: "str | None" = None) -> int:
    """Like _seed_source_assertion(), but also sets airport_id, and
    optionally links an additional official-reliability Source to that
    same airport via a second SourceAssertion (so airport-level official-
    domain derivation has something real to find)."""
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport = Airport(name="Louisville Muhammad Ali International Airport", iata_code="SDF", country="USA")
        session.add(airport)
        session.flush()

        source = Source(title="Test Source", source_type="aip_grant", reliability_level="official", external_id="faa_aip:test:1")
        session.add(source)
        session.flush()
        assertion = SourceAssertion(
            source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
            raw_relevant_text=text, source_locator="page:1;chars:0-100",
            raw_fragment_hash="deadbeef", artifact_identity="artifact:test-1",
            evidence_quality="unverified_candidate", review_state="unreviewed",
        )
        session.add(assertion)
        session.flush()

        if official_url:
            official_source = Source(
                title="LRAA board minutes", source_type="Authority", reliability_level="official", url=official_url,
            )
            session.add(official_source)
            session.flush()
            official_sa = SourceAssertion(
                source_id=official_source.id, airport_id=airport.id, assertion_type="project_construction",
                raw_relevant_text="official board minutes text", source_locator="page:1;chars:0-50",
                raw_fragment_hash="feedface", artifact_identity="artifact:test-2",
                evidence_quality="unverified_candidate", review_state="unreviewed",
            )
            session.add(official_sa)

        session.commit()
        return assertion.id


def test_flag_omitted_never_derives_or_reports_a_domain(tmp_path, capsys):
    """Existing behavior preserved when the flag is not given, even for a
    SourceAssertion whose airport DOES have a governed official domain."""
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion_with_airport(db_path, official_url="https://www.flylouisville.com/x.pdf")
    exit_code = cli.main(["--database", db_path, "--source-assertion-id", str(aid), *_SDF_ARGS])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Official-domain discovery: disabled" in out
    assert "flylouisville.com" not in out


def test_flag_with_known_official_domain_reports_it_and_adds_four_queries(tmp_path, capsys):
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion_with_airport(db_path, official_url="https://www.flylouisville.com/x.pdf")
    exit_code = cli.main([
        "--database", db_path, "--source-assertion-id", str(aid), *_SDF_ARGS, "--use-official-domain-discovery",
    ])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Official-domain discovery: ENABLED" in out
    assert "Known official hostname(s) for this airport: flylouisville.com" in out
    assert "Selected for the bounded document pass (max 1 per invocation): flylouisville.com" in out
    assert "site:flylouisville.com EMAS" in out
    assert "site:flylouisville.com EMAS presentation" in out
    assert "site:flylouisville.com EMAS capital program" in out
    assert "site:flylouisville.com EMAS board" in out


def test_flag_with_no_known_official_domain_is_a_documented_noop(tmp_path, capsys):
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion_with_airport(db_path)  # no official_url -> nothing governed yet
    exit_code = cli.main([
        "--database", db_path, "--source-assertion-id", str(aid), *_SDF_ARGS, "--use-official-domain-discovery",
    ])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Official-domain discovery: ENABLED" in out
    assert "none found - running the existing search plan unchanged" in out
    assert "site:" not in out


def test_flag_with_null_airport_id_is_a_documented_noop(tmp_path, capsys):
    """A SourceAssertion with no airport_id at all (a genuinely nullable
    column - e.g. an unresolved-identity candidate) must not crash; it is
    treated exactly like "no official domain known"."""
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion(db_path)  # airport_id left None
    exit_code = cli.main([
        "--database", db_path, "--source-assertion-id", str(aid), *_SDF_ARGS, "--use-official-domain-discovery",
    ])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "none found - running the existing search plan unchanged" in out


def test_official_domain_flag_never_writes_to_database(tmp_path):
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion_with_airport(db_path, official_url="https://www.flylouisville.com/x.pdf")
    before = open(db_path, "rb").read()
    cli.main([
        "--database", db_path, "--source-assertion-id", str(aid), *_SDF_ARGS, "--use-official-domain-discovery",
    ])
    after = open(db_path, "rb").read()
    assert before == after


def test_json_official_domain_fields_present_and_correct(tmp_path, capsys, monkeypatch):
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion_with_airport(db_path, official_url="https://www.flylouisville.com/x.pdf")
    monkeypatch.setitem(cli.PROVIDER_REGISTRY, "brave", _FakeProvider({}))
    exit_code = cli.main([
        "--database", db_path, "--source-assertion-id", str(aid), *_SDF_ARGS,
        "--use-official-domain-discovery", "--allow-live-network", "--json",
    ])
    import json
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["official_domain_discovery_enabled"] is True
    assert payload["known_official_hostnames"] == ["flylouisville.com"]
    assert payload["official_domain_selected"] == "flylouisville.com"
    assert payload["official_domain_queries"] == [
        "site:flylouisville.com EMAS",
        "site:flylouisville.com EMAS presentation",
        "site:flylouisville.com EMAS capital program",
        "site:flylouisville.com EMAS board",
    ]
    assert len(payload["official_domain_query_outcomes"]) == 4
    # baseline dimension query_outcomes count is completely unaffected.
    assert len(payload["query_outcomes"]) == 7


def test_json_official_domain_fields_empty_when_flag_omitted(tmp_path, capsys):
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion_with_airport(db_path, official_url="https://www.flylouisville.com/x.pdf")
    exit_code = cli.main(["--database", db_path, "--source-assertion-id", str(aid), *_SDF_ARGS, "--json"])
    import json
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["official_domain_discovery_enabled"] is False
    assert payload["known_official_hostnames"] == []
    assert payload["official_domain_selected"] is None
    assert payload["official_domain_queries"] == []


def test_multiple_known_domains_deterministically_picks_alphabetically_first(tmp_path, capsys):
    """Part CLI's own deterministic-cap requirement: with more than one
    governed official hostname, exactly one (alphabetically first) is
    selected - never both, never an unbounded per-domain multiplication."""
    db_path = str(tmp_path / "test.db")
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport = Airport(name="Louisville Muhammad Ali International Airport", iata_code="SDF", country="USA")
        session.add(airport)
        session.flush()
        source1 = Source(title="A", source_type="aip_grant", reliability_level="official", external_id="faa_aip:test:2")
        session.add(source1)
        session.flush()
        assertion = SourceAssertion(
            source_id=source1.id, airport_id=airport.id, assertion_type="project_construction",
            raw_relevant_text=SDF_TEXT, source_locator="page:1;chars:0-100",
            raw_fragment_hash="deadbeef", artifact_identity="artifact:test-3",
            evidence_quality="unverified_candidate", review_state="unreviewed",
        )
        session.add(assertion)
        session.flush()
        aid = assertion.id

        for i, url in enumerate((
            "https://www.zzz-later-domain.com/a.pdf",
            "https://www.flylouisville.com/b.pdf",
        )):
            official = Source(title=f"Official {i}", source_type="Authority", reliability_level="official", url=url)
            session.add(official)
            session.flush()
            sa = SourceAssertion(
                source_id=official.id, airport_id=airport.id, assertion_type="project_construction",
                raw_relevant_text="x", source_locator=f"page:1;chars:{i}-{i+1}",
                raw_fragment_hash=f"hash{i}", artifact_identity=f"artifact:test-multi-{i}",
                evidence_quality="unverified_candidate", review_state="unreviewed",
            )
            session.add(sa)
        session.commit()

    exit_code = cli.main([
        "--database", db_path, "--source-assertion-id", str(aid), *_SDF_ARGS, "--use-official-domain-discovery",
    ])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "flylouisville.com, zzz-later-domain.com" in out  # both reported...
    assert "Selected for the bounded document pass (max 1 per invocation): flylouisville.com" in out  # ...only one used
    assert out.count("site:") == 4  # exactly one domain's 4 queries, never 8
