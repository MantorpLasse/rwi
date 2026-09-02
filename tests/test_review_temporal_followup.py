"""RWI Mission #18C - offline tests for scripts/review_temporal_followup.py.
Fake SearchProvider only (mirrors tests/test_discovery_cli.py's own
_FakeProvider convention) - no network. Isolated temp-file SQLite DB for
SourceAssertion loading - never touches data/runway_safe.db."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import scripts.review_temporal_followup as cli
from app.database import Base
from app.discovery.query import SearchQuery
from app.discovery.search import SearchOutcome, SearchOutcomeStatus, SearchResult
from app.models import Source, SourceAssertion
from app.services.discovery_candidate_fragment import CandidateFragment
from app.services.discovery_temporal_followup import AirportSearchContext

_NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)

LCY_TEXT = (
    "process (ACP)[1].\nThe CAA reference is ACP-2022-090, the link to the CAA progress page is here.\n"
    "2. Brief Summary of this Proposal\nLondon City Airport is installing an Engineered Material Arrestor System "
    "(EMAS) which will provide an arrestor bed at both ends of its runway."
)
YTZ_TEXT = "EMAS was considered at Billy Bishop Toronto City Airport, but the landmass alternative was approved."

LCY_CONTEXT = AirportSearchContext(name="London City Airport", iata_code="LCY", icao_code="EGLC")


class _FakeProvider:
    name = "fake"

    def __init__(self, canned: "dict[str, SearchOutcome]"):
        self._canned = canned

    def search(self, query: SearchQuery) -> SearchOutcome:
        return self._canned.get(query.rendered, SearchOutcome(query=query, status=SearchOutcomeStatus.NO_RESULTS))


def _result(query: SearchQuery, url: str, *, title: str = "A result", snippet: str = "s") -> SearchResult:
    return SearchResult(query=query, rank=1, title=title, url=url, snippet=snippet, discovered_at=_NOW, provider="fake")


def _seed_source_assertion(db_path: str, *, text: str = LCY_TEXT) -> int:
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        source = Source(title="Test Source", source_type="web_discovery", reliability_level="unverified", external_id="discovery:test:1")
        session.add(source)
        session.flush()
        assertion = SourceAssertion(
            source_id=source.id, assertion_type="project_construction",
            raw_relevant_text=text, source_locator="page:3;chars:0-100",
            raw_fragment_hash="deadbeef", artifact_identity="artifact:test-1",
            evidence_quality="unverified_candidate", review_state="unreviewed",
        )
        session.add(assertion)
        session.commit()
        return assertion.id


# --- Input contract / DB read-only boundary ---


def test_load_fragment_for_review_reads_exact_fields(tmp_path):
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion(db_path)
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        fragment = cli.load_fragment_for_review(session, aid)
    assert isinstance(fragment, CandidateFragment)
    assert fragment.artifact_identity == "artifact:test-1"
    assert fragment.source_locator == "page:3;chars:0-100"
    assert fragment.raw_text == LCY_TEXT


def test_load_fragment_for_review_missing_assertion_raises(tmp_path):
    db_path = str(tmp_path / "test.db")
    _seed_source_assertion(db_path)
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        with pytest.raises(ValueError):
            cli.load_fragment_for_review(session, 9999)


def test_db_write_impossible_via_this_script(tmp_path):
    """No function in this module ever calls session.add/flush/commit -
    verified structurally by architectural-safety tests; here we verify
    the DB file itself is untouched after a full main() run."""
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion(db_path)
    before = open(db_path, "rb").read()
    cli.main([
        "--database", db_path, "--source-assertion-id", str(aid),
        "--identity-name", "London City Airport", "--identity-iata", "LCY", "--identity-icao", "EGLC",
        "--concept-term", "EMAS",
    ])
    after = open(db_path, "rb").read()
    assert before == after


# --- Trigger behavior ---


def test_zero_trigger_exits_cleanly(tmp_path, capsys):
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion(db_path, text=YTZ_TEXT)
    exit_code = cli.main([
        "--database", db_path, "--source-assertion-id", str(aid),
        "--identity-name", "Billy Bishop Toronto City Airport", "--identity-iata", "YTZ",
        "--concept-term", "EMAS",
    ])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "No temporal follow-up trigger matched" in out


# --- LCY fixture acceptance (Part Q) ---


def test_lcy_fixture_produces_one_trigger_and_four_queries(tmp_path):
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion(db_path)
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        fragment = cli.load_fragment_for_review(session, aid)
    from app.services.discovery_temporal_followup import detect_temporal_triggers
    triggers = detect_temporal_triggers(fragment, airport_context=LCY_CONTEXT, concept_term="EMAS")
    assert len(triggers) == 1
    plan, outcomes, deduped = cli.run(triggers, provider=None)
    assert len(plan) == 4
    assert outcomes == []
    assert deduped == []


def test_cross_query_dedup_preserves_multiple_found_by(tmp_path):
    """The key #18C behavior: the same URL discovered by 'completed' AND
    'operational' AND 'installed' queries collapses to ONE review item
    with all three queries preserved in found_by - never deduplicated
    per-query."""
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion(db_path)
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        fragment = cli.load_fragment_for_review(session, aid)
    from app.services.discovery_temporal_followup import detect_temporal_triggers
    triggers = detect_temporal_triggers(fragment, airport_context=LCY_CONTEXT, concept_term="EMAS")
    plan_preview, _, _ = cli.run(triggers, provider=None)
    by_concept = {q.template_id.rsplit("_", 1)[-1]: q for q in plan_preview}

    quantum_url = "https://www.quantumcls.com/projects/emas-london-city-airport"
    other_url = "https://runwaysafe.com/london-city-airport"

    canned = {
        by_concept["installed"].rendered: SearchOutcome(
            query=by_concept["installed"], status=SearchOutcomeStatus.OK,
            results=(_result(by_concept["installed"], quantum_url, title="QuantumCLS"),),
        ),
        by_concept["completed"].rendered: SearchOutcome(
            query=by_concept["completed"], status=SearchOutcomeStatus.OK,
            results=(
                _result(by_concept["completed"], quantum_url, title="QuantumCLS"),
                _result(by_concept["completed"], other_url, title="Runway Safe"),
            ),
        ),
        by_concept["commissioned"].rendered: SearchOutcome(
            query=by_concept["commissioned"], status=SearchOutcomeStatus.NO_RESULTS,
        ),
        by_concept["operational"].rendered: SearchOutcome(
            query=by_concept["operational"], status=SearchOutcomeStatus.OK,
            results=(_result(by_concept["operational"], quantum_url, title="QuantumCLS"),),
        ),
    }
    provider = _FakeProvider(canned)
    plan, outcomes, deduped = cli.run(triggers, provider=provider)

    assert len(plan) == 4
    assert len(outcomes) == 4
    # 2 unique URLs, not 4 raw hits for quantum_url collapsed separately.
    assert len(deduped) == 2
    quantum_item = next(d for d in deduped if d.result.url == quantum_url)
    assert len(quantum_item.found_by) == 3
    found_by_renders = {q.rendered for q in quantum_item.found_by}
    assert found_by_renders == {by_concept["installed"].rendered, by_concept["completed"].rendered, by_concept["operational"].rendered}


# --- YTZ negative acceptance (Part R) ---


def test_ytz_fixture_produces_zero_triggers_zero_search_calls(tmp_path):
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion(db_path, text=YTZ_TEXT)
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        fragment = cli.load_fragment_for_review(session, aid)
    from app.services.discovery_temporal_followup import detect_temporal_triggers
    triggers = detect_temporal_triggers(
        fragment, airport_context=AirportSearchContext(name="Billy Bishop Toronto City Airport", iata_code="YTZ"),
        concept_term="EMAS",
    )
    assert triggers == ()

    call_count = {"n": 0}

    class _CountingProvider:
        name = "counting"

        def search(self, query):
            call_count["n"] += 1
            return SearchOutcome(query=query, status=SearchOutcomeStatus.NO_RESULTS)

    plan, outcomes, deduped = cli.run(triggers, provider=_CountingProvider())
    assert plan == []
    assert outcomes == []
    assert deduped == []
    assert call_count["n"] == 0


# --- Empty / failure behavior ---


def test_provider_failure_reported_not_swallowed(tmp_path, capsys):
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion(db_path)
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        fragment = cli.load_fragment_for_review(session, aid)
    from app.services.discovery_temporal_followup import detect_temporal_triggers
    triggers = detect_temporal_triggers(fragment, airport_context=LCY_CONTEXT, concept_term="EMAS")

    class _FailingProvider:
        name = "failing"

        def search(self, query):
            return SearchOutcome(query=query, status=SearchOutcomeStatus.PROVIDER_FAILURE, error="boom")

    plan, outcomes, deduped = cli.run(triggers, provider=_FailingProvider())
    assert len(outcomes) == 4
    assert all(o.status == SearchOutcomeStatus.PROVIDER_FAILURE for o in outcomes)
    assert deduped == []


def test_zero_results_handled_cleanly(tmp_path):
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion(db_path)
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        fragment = cli.load_fragment_for_review(session, aid)
    from app.services.discovery_temporal_followup import detect_temporal_triggers
    triggers = detect_temporal_triggers(fragment, airport_context=LCY_CONTEXT, concept_term="EMAS")

    class _EmptyProvider:
        name = "empty"

        def search(self, query):
            return SearchOutcome(query=query, status=SearchOutcomeStatus.NO_RESULTS)

    plan, outcomes, deduped = cli.run(triggers, provider=_EmptyProvider())
    assert len(outcomes) == 4
    assert deduped == []


def test_malformed_source_assertion_missing_text_fails_closed(tmp_path):
    db_path = str(tmp_path / "test.db")
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        source = Source(title="t", source_type="web_discovery", reliability_level="unverified", external_id="discovery:test:2")
        session.add(source)
        session.flush()
        assertion = SourceAssertion(
            source_id=source.id, assertion_type="project_construction",
            raw_relevant_text=None, source_locator="page:1;chars:0-1",
            raw_fragment_hash="x", artifact_identity="artifact:missing-text",
            evidence_quality="unverified_candidate", review_state="unreviewed",
        )
        session.add(assertion)
        session.commit()
        aid = assertion.id
    with Session(engine) as session:
        with pytest.raises(ValueError):
            cli.load_fragment_for_review(session, aid)


def test_blank_identity_name_refused(tmp_path, capsys):
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion(db_path)
    exit_code = cli.main([
        "--database", db_path, "--source-assertion-id", str(aid),
        "--identity-name", "   ", "--concept-term", "EMAS",
    ])
    assert exit_code == 2
    assert "Refused" in capsys.readouterr().err


def test_blank_concept_term_refused(tmp_path, capsys):
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion(db_path)
    exit_code = cli.main([
        "--database", db_path, "--source-assertion-id", str(aid),
        "--identity-name", "London City Airport", "--concept-term", "  ",
    ])
    assert exit_code == 2
    assert "Refused" in capsys.readouterr().err


def test_unknown_provider_refused(tmp_path, capsys):
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion(db_path)
    exit_code = cli.main([
        "--database", db_path, "--source-assertion-id", str(aid),
        "--identity-name", "London City Airport", "--concept-term", "EMAS",
        "--provider", "not-a-real-provider",
    ])
    assert exit_code == 2
    assert "Unknown --provider" in capsys.readouterr().err


# --- Snippet safety ---


def test_snippet_never_reaches_candidate_fragment_or_evidence_bag(tmp_path):
    """The review CLI's own run()/main() never constructs a new
    CandidateFragment/EvidenceBag/SourceAssertion from a SearchResult -
    the only CandidateFragment anywhere is the one built once, up front,
    from the already-persisted SourceAssertion (see architectural-safety
    test file for the static import-boundary proof)."""
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion(db_path)
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        fragment = cli.load_fragment_for_review(session, aid)
    from app.services.discovery_temporal_followup import detect_temporal_triggers
    triggers = detect_temporal_triggers(fragment, airport_context=LCY_CONTEXT, concept_term="EMAS")
    plan_preview, _, _ = cli.run(triggers, provider=None)
    by_concept = {q.template_id.rsplit("_", 1)[-1]: q for q in plan_preview}
    canned = {
        by_concept["completed"].rendered: SearchOutcome(
            query=by_concept["completed"], status=SearchOutcomeStatus.OK,
            results=(_result(by_concept["completed"], "https://example.com/x", snippet="scheduled for completion mid-summer 2023"),),
        ),
    }
    plan, outcomes, deduped = cli.run(triggers, provider=_FakeProvider(canned))
    assert len(deduped) == 1
    # The snippet is present only on the SearchResult itself - never
    # copied into anything resembling raw_text/artifact_identity of a
    # NEW CandidateFragment.
    assert deduped[0].result.snippet == "scheduled for completion mid-summer 2023"
    assert deduped[0].result.snippet != fragment.raw_text


# --- Human output labeling ---


def test_human_output_without_provider_carries_disclaimer_only(tmp_path, capsys):
    """No --provider given: only the disclaimer/trigger/query-plan section
    prints; the fetch hint (which presupposes candidates exist) does not,
    and no network access occurs."""
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion(db_path)
    exit_code = cli.main([
        "--database", db_path, "--source-assertion-id", str(aid),
        "--identity-name", "London City Airport", "--concept-term", "EMAS",
    ])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "DISCOVERY / FETCH REVIEW" in out
    assert "NO SEARCH PROVIDER CONFIGURED" in out
    assert "fetch_discovered_url" not in out


def test_human_output_carries_disclaimer_and_fetch_hint(tmp_path, capsys):
    db_path = str(tmp_path / "test.db")
    aid = _seed_source_assertion(db_path)
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        fragment = cli.load_fragment_for_review(session, aid)
    from app.services.discovery_temporal_followup import detect_temporal_triggers
    triggers = detect_temporal_triggers(fragment, airport_context=LCY_CONTEXT, concept_term="EMAS")
    plan_preview, _, _ = cli.run(triggers, provider=None)
    by_concept = {q.template_id.rsplit("_", 1)[-1]: q for q in plan_preview}
    canned = {
        by_concept["completed"].rendered: SearchOutcome(
            query=by_concept["completed"], status=SearchOutcomeStatus.OK,
            results=(_result(by_concept["completed"], "https://example.com/x"),),
        ),
    }
    from app.discovery.triage import triage_results
    from app.discovery.identity import AirportIdentity

    plan, outcomes, deduped = cli.run(triggers, provider=_FakeProvider(canned))
    triaged = triage_results(deduped, identity=AirportIdentity(name="London City Airport", iata_code="LCY"))
    cli._print_human(triggers, plan, outcomes, triaged, "fake")

    out = capsys.readouterr().out
    assert "DISCOVERY / FETCH REVIEW" in out
    assert "not evidence" in out
    assert "fetch_discovered_url" in out
