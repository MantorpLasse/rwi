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
from app.models import Source, SourceAssertion

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
    from app.services.research_question_planning import ResearchClue, ResearchDimension, plan_research_questions
    context = AirportSearchContext(name="Louisville Muhammad Ali International Airport", iata_code="SDF", icao_code="KSDF")
    clue = ResearchClue(
        evidence_text=text, airport_context=context,
        unresolved_dimensions=(
            ResearchDimension.RUNWAY_END, ResearchDimension.INSTALLATION_TYPE,
            ResearchDimension.PROJECT_PHASE, ResearchDimension.TIMING, ResearchDimension.SUPPLIER,
        ),
    )
    questions = plan_research_questions(clue)
    canned = {
        questions[0].search_query.rendered: SearchOutcome(
            query=questions[0].search_query, status=SearchOutcomeStatus.OK,
            results=(_result(questions[0].search_query, "https://faa.gov/sdf-doc", title="SDF EMAS project document"),),
        )
    }
    monkeypatch.setitem(cli.PROVIDER_REGISTRY, "brave", _FakeProvider(canned))

    exit_code = cli.main([
        "--database", db_path, "--source-assertion-id", str(aid), *_SDF_ARGS, "--allow-live-network",
    ])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "SEARCH SUMMARY" in out
    assert "5 queries" in out
    assert "HIGH" in out or "MEDIUM" in out or "LOW" in out
    assert "https://faa.gov/sdf-doc" in out
    assert "UNRESOLVED / NO RESULT" in out
    assert "STOP" in out


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
    assert len(payload["question_outcomes"]) == 5
    assert payload["triaged_candidates"] == []
    assert payload["network_used"] is True
    assert set(payload["unresolved_dimensions"]) == {
        "RUNWAY_END", "INSTALLATION_TYPE", "PROJECT_PHASE", "TIMING", "SUPPLIER",
    }


def test_json_plan_only_mode_has_null_unresolved_dimensions(tmp_path, capsys):
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion(db_path)
    exit_code = cli.main(["--database", db_path, "--source-assertion-id", str(aid), *_SDF_ARGS, "--json"])
    import json
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["network_used"] is False
    assert payload["unresolved_dimensions"] is None
    assert payload["question_outcomes"] == []
