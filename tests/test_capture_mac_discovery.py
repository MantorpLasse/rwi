"""Tests for scripts/capture_mac_discovery.py
(docs/product/msp-gated-discovery-capture-runner-dry-run.md).

Never touches the real database - every test builds its own isolated
temp-file SQLite database (migrated or not, as needed). Live network is
exercised only via httpx.MockTransport (small HTML snippets mirroring
the real MAC Granicus structure, same technique already established in
tests/test_mac_granicus_provider.py) - the one genuinely live run against
the real site happens separately, manually, for this task's own report,
never inside the ordinary test suite.
"""
from __future__ import annotations

import hashlib
import inspect
import shutil
import sqlite3
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app import models  # noqa: F401 - registers all metadata
from app.models import (
    Airport, Installation, PhysicalInstallationIdentity, Runway, RunwayEnd, Signal, Source, SourceAssertion,
)
from scripts.capture_mac_discovery import (
    CaptureConfig,
    CaptureRunnerError,
    FixtureDocument,
    build_engine,
    compute_plan_fingerprint,
    run_capture,
)
from scripts.migrate_discovery_governed_evidence_slice1 import upgrade as migrate_upgrade
from scripts.migrate_intelligence_review_persistence_slice4 import upgrade as migrate_upgrade_slice4
from scripts.migrate_promotion_policy_persistence_slice7 import upgrade as migrate_upgrade_slice7
from scripts.migrate_governed_signal_creation_slice9c import upgrade as migrate_upgrade_slice9c
from scripts.migrate_unknown_airport_candidates_uac2a import upgrade as migrate_upgrade_uac2a
from scripts.migrate_source_assertion_unknown_airport_uac2b import upgrade as migrate_upgrade_uac2b

FIXTURE_PDF = (Path(__file__).parent / "fixtures" / "mac_granicus_emas_procurement_memo_sample.pdf").read_bytes()
ARTIFACT_IDENTITY = "mac.granicus.document.4.2349.105406"
SOURCE_LOCATOR = "item-2.3.2"
DOCUMENT_URL = "https://metroairports.granicus.com/MetaViewer.php?view_id=4&clip_id=2349&meta_id=105406"
ITEM_TITLE = "2.3.2. Engineered Material Arresting Systems (EMAS) Procurement Advance Deposit"

MEETING_LIST_HTML = """
<table>
<tr><td class="listItem" headers="Name" id="Planning,-Development-and-Environment-Committee" scope="row">
Planning, Development and Environment Committee
</td>
<td class="listItem" headers="Date Planning,-Development-and-Environment-Committee">Sep  3, 2024</td>
<td class="listItem"> 00h&nbsp;49m</td>
<td class="listItem"><a href="//metroairports.granicus.com/AgendaViewer.php?view_id=4&clip_id=2349" target="_blank">Agenda</a></td>
</tr>
</table>
"""

AGENDA_HTML = f"""
<html><body>
<table>
<tr><td class = "numberspace">2.3.1.</td>
<td valign="top">Reliever Radio Purchase - Troy Tomlinson
<blockquote dir="ltr"><div><a href="https://metroairports.granicus.com/MetaViewer.php?view_id=4&clip_id=2349&meta_id=105404" name="document105404">2.3.1. Reliever Radio Purchase</a></div></blockquote>
</td></tr>
<tr><td class = "numberspace">2.3.2.</td>
<td valign="top">{ITEM_TITLE} - Angela Enroth
<blockquote dir="ltr"><div><a href="{DOCUMENT_URL}" name="document105406">2.3.2. EMAS Procurement Advance Deposit</a></div></blockquote>
</td></tr>
</table>
</body></html>
"""


def _fixture_document(**overrides) -> FixtureDocument:
    kwargs = dict(
        pdf_bytes=FIXTURE_PDF, artifact_identity=ARTIFACT_IDENTITY, source_locator=SOURCE_LOCATOR,
        item_title=ITEM_TITLE, document_url=DOCUMENT_URL,
    )
    kwargs.update(overrides)
    return FixtureDocument(**kwargs)


def _build_pre_migration_schema(database: Path) -> None:
    """Hand-builds source_assertions in its TRUE pre-migration shape (no
    identity_guard_decision/identity_guard_reason) - Base.metadata.create_all()
    cannot produce this, because the current, committed ORM model already
    declares those two columns unconditionally (exactly the same technique
    tests/test_discovery_governed_evidence_migration.py::_pre_migration_database()
    already uses, adapted here)."""
    engine = create_engine(f"sqlite:///{database}")
    tables = [t for name, t in Base.metadata.tables.items() if name != "source_assertions"]
    Base.metadata.create_all(engine, tables=tables)
    engine.dispose()

    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute(
        "CREATE TABLE source_assertions_old ("
        "id INTEGER NOT NULL, source_id INTEGER NOT NULL, airport_id INTEGER, runway_id INTEGER, "
        "assertion_type VARCHAR(30) NOT NULL, runway_end VARCHAR(20), "
        "raw_airport_identifier VARCHAR(100), raw_airport_name VARCHAR(300), "
        "raw_runway_value VARCHAR(100), raw_runway_end_value VARCHAR(100), "
        "raw_product_type VARCHAR(200), raw_year_date_wording VARCHAR(300), "
        "raw_vendor_manufacturer_wording VARCHAR(300), raw_count VARCHAR(100), raw_relevant_text TEXT, "
        "source_record_identifier VARCHAR(300), source_locator VARCHAR(500), "
        "raw_fragment_hash VARCHAR(128), artifact_identity VARCHAR(500), "
        "parser_identifier VARCHAR(200), extracted_at DATETIME, "
        "evidence_quality VARCHAR(30) NOT NULL, review_state VARCHAR(20) NOT NULL, "
        "created_at DATETIME NOT NULL, "
        "PRIMARY KEY (id), "
        "FOREIGN KEY(source_id) REFERENCES sources (id), "
        "FOREIGN KEY(airport_id) REFERENCES airports (id), "
        "FOREIGN KEY(runway_id) REFERENCES runways (id))"
    )
    connection.execute("ALTER TABLE source_assertions_old RENAME TO source_assertions")
    connection.execute("CREATE INDEX ix_source_assertions_source_id ON source_assertions(source_id)")
    connection.execute("CREATE INDEX ix_source_assertions_airport_id ON source_assertions(airport_id)")
    connection.execute("CREATE INDEX ix_source_assertions_runway_id ON source_assertions(runway_id)")
    connection.commit()
    connection.close()


def _seed_msp_sfo_data(database: Path) -> None:
    engine = create_engine(f"sqlite:///{database}")
    with Session(engine) as session:
        msp = Airport(name="Minneapolis St. Paul International", faa_code="MSP", iata_code="MSP", icao_code="KMSP", country="USA", city="Minneapolis")
        sfo = Airport(name="San Francisco International Airport", faa_code="SFO", iata_code="SFO", icao_code="KSFO", country="USA", city="San Francisco")
        session.add_all([msp, sfo])
        session.flush()
        for airport, runways in (
            (msp, {"12R/30L": ("12R", "30L"), "4/22": ("4", "22")}),
            (sfo, {"1R/19L": ("1R", "19L"), "1L/19R": ("1L", "19R")}),
        ):
            for designation, ends in runways.items():
                runway = Runway(airport_id=airport.id, designation=designation)
                session.add(runway)
                session.flush()
                for end in ends:
                    session.add(RunwayEnd(runway_id=runway.id, designation=end))
        session.commit()
    engine.dispose()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def unmigrated_db(tmp_path) -> Path:
    database = tmp_path / "unmigrated.db"
    _build_pre_migration_schema(database)
    _seed_msp_sfo_data(database)
    return database


@pytest.fixture
def migrated_db(tmp_path) -> Path:
    database = tmp_path / "migrated.db"
    engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(engine)
    engine.dispose()
    _seed_msp_sfo_data(database)
    return database


def _mock_client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "ViewPublisher.php" in url:
            return httpx.Response(200, text=MEETING_LIST_HTML, request=request)
        if "AgendaViewer.php" in url:
            return httpx.Response(200, text=AGENDA_HTML, request=request)
        if "MetaViewer.php" in url:
            return httpx.Response(200, content=FIXTURE_PDF, headers={"Content-Type": "application/pdf"}, request=request)
        return httpx.Response(404, request=request)

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


# --- 1. dry-run is default ---


def test_dry_run_is_default(migrated_db):
    report = run_capture(CaptureConfig(database=migrated_db, fixture_documents=(_fixture_document(),)))
    assert report["applied"] is False
    assert report["apply_requested"] is False


def test_dry_run_creates_zero_database_rows(migrated_db):
    before = _file_hash(migrated_db)
    run_capture(CaptureConfig(database=migrated_db, fixture_documents=(_fixture_document(),)))
    after = _file_hash(migrated_db)
    assert before == after  # byte-identical: zero durable mutation


def test_dry_run_live_network_creates_zero_database_rows(migrated_db):
    before = _file_hash(migrated_db)
    run_capture(CaptureConfig(database=migrated_db, allow_live_network=True), client=_mock_client())
    after = _file_hash(migrated_db)
    assert before == after


# --- 2. live network requires explicit flag ---


def test_live_network_requires_explicit_flag(migrated_db):
    with pytest.raises(CaptureRunnerError, match="NO_LIVE_NETWORK_AND_NO_FIXTURE_PROVIDED"):
        run_capture(CaptureConfig(database=migrated_db))


# --- 3. database apply requires both write flags ---


def test_apply_requires_allow_database_write(migrated_db):
    with pytest.raises(CaptureRunnerError, match="--apply requires --allow-database-write"):
        run_capture(CaptureConfig(database=migrated_db, fixture_documents=(_fixture_document(),), apply=True))


def test_allow_database_write_requires_apply(migrated_db):
    with pytest.raises(CaptureRunnerError, match="--allow-database-write requires --apply"):
        run_capture(CaptureConfig(database=migrated_db, fixture_documents=(_fixture_document(),), allow_database_write=True))


# --- 4. migration-required gate ---


def test_apply_refuses_when_schema_not_migrated(unmigrated_db):
    dry = run_capture(CaptureConfig(database=unmigrated_db, fixture_documents=(_fixture_document(),)))
    assert dry["schema_readiness"]["identity_guard_decision_column_exists"] is False

    before = _file_hash(unmigrated_db)
    applied = run_capture(CaptureConfig(
        database=unmigrated_db, fixture_documents=(_fixture_document(),),
        apply=True, allow_database_write=True, expected_fingerprint=dry["plan_fingerprint"], skip_backup=True,
    ))
    after = _file_hash(unmigrated_db)

    assert applied["applied"] is False
    assert "DISCOVERY_SCHEMA_MIGRATION_REQUIRED" in applied["blockers"]
    assert before == after


def test_apply_succeeds_after_running_the_real_migration_script(unmigrated_db):
    """Full disposable rehearsal: an unmigrated DB refuses apply, then
    applying the actual, unmodified migration script(s) (never auto-run by
    the capture runner itself) unblocks it - proving the runner's gate
    and the real migration scripts agree on schema readiness.

    Runs ALL SIX additive source_assertions migrations, not just Slice 1's:
    the ORM model (app/models/source_assertion.py) now also declares
    Slice 4's intelligence_review_decision/intelligence_review_reason,
    Slice 7's promotion_policy_decision/promotion_policy_reason, Slice
    9C's signal_id, and (UAC2B) unknown_airport_candidate_id columns
    unconditionally, so any SELECT against SourceAssertion - including
    this runner's own idempotency check - requires the physical table to
    carry all six too, even though this capture runner's own write path
    never touches any of them. UAC2B's own migration additionally
    requires UAC2A (unknown_airport_candidates/unknown_airport_candidate_reviews)
    to already exist - the ordering below matters."""
    dry = run_capture(CaptureConfig(database=unmigrated_db, fixture_documents=(_fixture_document(),)))
    refused = run_capture(CaptureConfig(
        database=unmigrated_db, fixture_documents=(_fixture_document(),),
        apply=True, allow_database_write=True, expected_fingerprint=dry["plan_fingerprint"], skip_backup=True,
    ))
    assert refused["applied"] is False

    migrate_upgrade(unmigrated_db)
    migrate_upgrade_slice4(unmigrated_db)
    migrate_upgrade_slice7(unmigrated_db)
    migrate_upgrade_slice9c(unmigrated_db)
    migrate_upgrade_uac2a(unmigrated_db)
    migrate_upgrade_uac2b(unmigrated_db)

    dry2 = run_capture(CaptureConfig(database=unmigrated_db, fixture_documents=(_fixture_document(),)))
    assert dry2["schema_readiness"]["identity_guard_decision_column_exists"] is True
    applied = run_capture(CaptureConfig(
        database=unmigrated_db, fixture_documents=(_fixture_document(),),
        apply=True, allow_database_write=True, expected_fingerprint=dry2["plan_fingerprint"], skip_backup=True,
    ))
    assert applied["applied"] is True


# --- 5. explicit DB binding ---


def test_engine_binds_to_exact_resolved_database_path(migrated_db):
    engine = build_engine(migrated_db)
    with Session(engine) as session:
        count = session.query(Airport).count()
    assert count == 2  # MSP + SFO seeded only in THIS database
    engine.dispose()


def test_module_never_imports_process_global_session_local():
    """Checks the module's actual import statements, not its prose (the
    module's own docstring/comments legitimately mention "SessionLocal"
    to explain that it is deliberately NOT imported)."""
    import ast

    import scripts.capture_mac_discovery as module
    tree = ast.parse(inspect.getsource(module))
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
    assert "SessionLocal" not in imported_names
    assert "app.database" not in imported_names
    assert not any(name.startswith("app.database.") for name in imported_names)


# --- 6. wrong-DB isolation ---


def test_wrong_db_isolation_protected_untouched_target_written(migrated_db, tmp_path):
    protected = tmp_path / "protected.db"
    target = tmp_path / "target.db"
    shutil.copy2(migrated_db, protected)
    shutil.copy2(migrated_db, target)
    protected_before = _file_hash(protected)

    dry = run_capture(CaptureConfig(database=target, fixture_documents=(_fixture_document(),)))
    run_capture(CaptureConfig(
        database=target, fixture_documents=(_fixture_document(),),
        apply=True, allow_database_write=True, expected_fingerprint=dry["plan_fingerprint"], skip_backup=True,
    ))

    assert _file_hash(protected) == protected_before  # byte-identical, never touched

    protected_engine = build_engine(protected)
    with Session(protected_engine) as session:
        assert session.query(SourceAssertion).count() == 0
    protected_engine.dispose()

    target_engine = build_engine(target)
    with Session(target_engine) as session:
        assert session.query(SourceAssertion).count() == 1
    target_engine.dispose()


def test_default_app_database_cannot_override_explicit_target(migrated_db, tmp_path, monkeypatch):
    """Even if app.config.settings.database_url points elsewhere, writes
    still go only to the explicitly resolved --database path."""
    import app.config as config_module
    monkeypatch.setattr(config_module.settings, "database_url", f"sqlite:///{tmp_path / 'should_never_be_touched.db'}")

    dry = run_capture(CaptureConfig(database=migrated_db, fixture_documents=(_fixture_document(),)))
    run_capture(CaptureConfig(
        database=migrated_db, fixture_documents=(_fixture_document(),),
        apply=True, allow_database_write=True, expected_fingerprint=dry["plan_fingerprint"], skip_backup=True,
    ))

    assert not (tmp_path / "should_never_be_touched.db").exists()
    engine = build_engine(migrated_db)
    with Session(engine) as session:
        assert session.query(SourceAssertion).count() == 1
    engine.dispose()


# --- 7. backup target correctness ---


def test_backup_corresponds_to_target_database(migrated_db, tmp_path):
    backup_dir = tmp_path / "backups"
    before_hash = _file_hash(migrated_db)

    dry = run_capture(CaptureConfig(database=migrated_db, fixture_documents=(_fixture_document(),)))
    applied = run_capture(CaptureConfig(
        database=migrated_db, fixture_documents=(_fixture_document(),),
        apply=True, allow_database_write=True, expected_fingerprint=dry["plan_fingerprint"], backup_directory=backup_dir,
    ))

    backup_path = Path(applied["backup_path"])
    assert backup_path.parent == backup_dir
    assert _file_hash(backup_path) == before_hash  # backup is the pre-write target content, byte for byte


def test_no_backup_created_in_ordinary_dry_run(migrated_db, tmp_path):
    backup_dir = tmp_path / "backups"
    run_capture(CaptureConfig(database=migrated_db, fixture_documents=(_fixture_document(),), backup_directory=backup_dir))
    assert not backup_dir.exists()


# --- 8. provider orchestration ---


def test_discover_relevant_fragments_uses_mac_provider_via_mock_transport(migrated_db):
    report = run_capture(CaptureConfig(database=migrated_db, allow_live_network=True), client=_mock_client())
    assert len(report["meetings_inspected"]) == 1
    assert report["meetings_inspected"][0]["clip_id"] == 2349
    assert report["agenda_items_inspected"] == 2  # both items on the mock agenda
    assert report["agenda_items_relevant"] == 1
    assert report["agenda_items_ignored"] == 1
    assert len(report["documents_fetched"]) == 1
    assert report["documents_fetched"][0]["http_status"] == 200


# --- 9. extractor orchestration ---


def test_run_capture_uses_mac_extractor_for_fixture_document(migrated_db):
    report = run_capture(CaptureConfig(database=migrated_db, fixture_documents=(_fixture_document(),)))
    fragment_report = report["candidate_fragments"][0]
    assert fragment_report["runway_ends"] == ["12R", "30L"]
    assert fragment_report["runway_pairs"] == ["12R/30L"]
    assert fragment_report["issuers"] == ["Metropolitan Airports Commission"]
    assert "Runway Safe" in fragment_report["vendors"]


# --- 10. topology enrichment ---


def test_run_capture_applies_enrichment_so_sfo_rejects_with_real_topology_reason(migrated_db):
    report = run_capture(CaptureConfig(database=migrated_db, fixture_documents=(_fixture_document(),)))
    decisions = report["candidate_fragments"][0]["decisions"]
    sfo_decision = next(d for cid, d in decisions.items() if d["outcome"] == "REJECT_CROSS_AIRPORT")
    assert "runway_topology" in sfo_decision["reason"]
    assert "30L" in sfo_decision["reason"]
    assert "provider" not in sfo_decision["reason"].lower()
    assert "mac" not in sfo_decision["reason"].lower()


# --- 11 / 12. MSP ATTACH_CONFIRMED, SFO REJECT_CROSS_AIRPORT ---


def test_msp_attach_confirmed_sfo_reject_cross_airport(migrated_db):
    report = run_capture(CaptureConfig(database=migrated_db, fixture_documents=(_fixture_document(),)))
    plan = report["planned_governed_evidence"][0]
    assert plan["guard_outcome"] == "ATTACH_CONFIRMED"
    assert plan["attached_airport_code"] == "MSP"

    decisions = report["candidate_fragments"][0]["decisions"]
    outcomes = {d["outcome"] for d in decisions.values()}
    assert "ATTACH_CONFIRMED" in outcomes
    assert "REJECT_CROSS_AIRPORT" in outcomes


# --- 13 / 14. planned persistence fingerprint + stability ---


def test_plan_fingerprint_is_a_stable_hex_digest(migrated_db):
    report = run_capture(CaptureConfig(database=migrated_db, fixture_documents=(_fixture_document(),)))
    fingerprint = report["plan_fingerprint"]
    assert isinstance(fingerprint, str)
    assert len(fingerprint) == 64
    int(fingerprint, 16)  # valid hex


def test_fingerprint_stable_across_repeated_dry_runs(migrated_db):
    first = run_capture(CaptureConfig(database=migrated_db, fixture_documents=(_fixture_document(),)))
    second = run_capture(CaptureConfig(database=migrated_db, fixture_documents=(_fixture_document(),)))
    assert first["plan_fingerprint"] == second["plan_fingerprint"]


def test_fingerprint_independent_of_target_database_state(migrated_db, tmp_path):
    """Same upstream content fingerprints identically whether or not the
    target database already contains the resulting Source/SourceAssertion."""
    other_db = tmp_path / "other_migrated.db"
    engine = create_engine(f"sqlite:///{other_db}")
    Base.metadata.create_all(engine)
    engine.dispose()
    _seed_msp_sfo_data(other_db)

    report_a = run_capture(CaptureConfig(database=migrated_db, fixture_documents=(_fixture_document(),)))
    dry = run_capture(CaptureConfig(database=other_db, fixture_documents=(_fixture_document(),)))
    run_capture(CaptureConfig(
        database=other_db, fixture_documents=(_fixture_document(),),
        apply=True, allow_database_write=True, expected_fingerprint=dry["plan_fingerprint"], skip_backup=True,
    ))
    report_b = run_capture(CaptureConfig(database=other_db, fixture_documents=(_fixture_document(),)))

    assert report_a["plan_fingerprint"] == report_b["plan_fingerprint"]


# --- 15. stale fingerprint fails closed ---


def test_apply_refuses_on_fingerprint_mismatch(migrated_db):
    before = _file_hash(migrated_db)
    applied = run_capture(CaptureConfig(
        database=migrated_db, fixture_documents=(_fixture_document(),),
        apply=True, allow_database_write=True, expected_fingerprint="0" * 64, skip_backup=True,
    ))
    after = _file_hash(migrated_db)
    assert applied["applied"] is False
    assert any("FINGERPRINT_MISMATCH" in b for b in applied["blockers"])
    assert before == after


def test_apply_refuses_when_no_fingerprint_supplied(migrated_db):
    applied = run_capture(CaptureConfig(
        database=migrated_db, fixture_documents=(_fixture_document(),),
        apply=True, allow_database_write=True, skip_backup=True,
    ))
    assert applied["applied"] is False
    assert any("FINGERPRINT_MISMATCH" in b for b in applied["blockers"])


# --- 16. disposable apply creates Source/SourceAssertion only ---


def test_apply_creates_source_and_source_assertion_only(migrated_db):
    dry = run_capture(CaptureConfig(database=migrated_db, fixture_documents=(_fixture_document(),)))
    applied = run_capture(CaptureConfig(
        database=migrated_db, fixture_documents=(_fixture_document(),),
        apply=True, allow_database_write=True, expected_fingerprint=dry["plan_fingerprint"], skip_backup=True,
    ))
    assert applied["applied"] is True
    assert applied["apply_result"][0]["outcome"] == "ATTACH_CONFIRMED"

    engine = build_engine(migrated_db)
    with Session(engine) as session:
        assert session.query(Source).count() == 1
        assert session.query(SourceAssertion).count() == 1
        assertion = session.scalar(select(SourceAssertion))
        assert assertion.identity_guard_decision == "ATTACH_CONFIRMED"
        assert assertion.identity_guard_reason
        assert assertion.airport_id is not None
    engine.dispose()


# --- 17. no Signal ---


def test_no_signal_created(migrated_db):
    dry = run_capture(CaptureConfig(database=migrated_db, fixture_documents=(_fixture_document(),)))
    run_capture(CaptureConfig(
        database=migrated_db, fixture_documents=(_fixture_document(),),
        apply=True, allow_database_write=True, expected_fingerprint=dry["plan_fingerprint"], skip_backup=True,
    ))
    engine = build_engine(migrated_db)
    with Session(engine) as session:
        assert session.query(Signal).count() == 0
    engine.dispose()


def test_module_never_imports_signal_model():
    import scripts.capture_mac_discovery as module
    source = inspect.getsource(module)
    assert "Signal" not in source


# --- 18. idempotent second capture ---


def test_idempotent_second_apply_no_duplicates(migrated_db):
    dry = run_capture(CaptureConfig(database=migrated_db, fixture_documents=(_fixture_document(),)))
    fp = dry["plan_fingerprint"]
    first = run_capture(CaptureConfig(
        database=migrated_db, fixture_documents=(_fixture_document(),),
        apply=True, allow_database_write=True, expected_fingerprint=fp, skip_backup=True,
    ))
    second = run_capture(CaptureConfig(
        database=migrated_db, fixture_documents=(_fixture_document(),),
        apply=True, allow_database_write=True, expected_fingerprint=fp, skip_backup=True,
    ))
    assert first["apply_result"][0]["source_created"] is True
    assert second["apply_result"][0]["source_created"] is False
    assert second["apply_result"][0]["source_assertion_created"] is False

    engine = build_engine(migrated_db)
    with Session(engine) as session:
        assert session.query(Source).count() == 1
        assert session.query(SourceAssertion).count() == 1
    engine.dispose()


# --- 19. changed fragment behavior ---


def test_distinct_fragment_identity_creates_new_assertion_reuses_source(migrated_db):
    """A distinct fragment (different source_locator - e.g. a different
    item on the same document) reuses the Source but creates its own
    SourceAssertion. See
    test_plan_governed_persistence_reflects_genuinely_changed_raw_content
    below for the literal "same locator, changed raw text" case, proven
    at the planning level this task actually adds."""
    dry1 = run_capture(CaptureConfig(database=migrated_db, fixture_documents=(_fixture_document(),)))
    run_capture(CaptureConfig(
        database=migrated_db, fixture_documents=(_fixture_document(),),
        apply=True, allow_database_write=True, expected_fingerprint=dry1["plan_fingerprint"], skip_backup=True,
    ))

    changed_fixture = _fixture_document(source_locator="item-2.3.2-revised")
    dry2 = run_capture(CaptureConfig(database=migrated_db, fixture_documents=(changed_fixture,)))
    applied2 = run_capture(CaptureConfig(
        database=migrated_db, fixture_documents=(changed_fixture,),
        apply=True, allow_database_write=True, expected_fingerprint=dry2["plan_fingerprint"], skip_backup=True,
    ))

    assert applied2["apply_result"][0]["source_created"] is False
    assert applied2["apply_result"][0]["source_assertion_created"] is True

    engine = build_engine(migrated_db)
    with Session(engine) as session:
        assert session.query(Source).count() == 1
        assert session.query(SourceAssertion).count() == 2
    engine.dispose()


def test_plan_governed_persistence_reflects_genuinely_changed_raw_content(migrated_db):
    """Simulated (fixture) changed content only - never manipulates the
    real remote source (task S13). Same artifact_identity/source_locator,
    genuinely different raw_text -> different fragment_hash -> the
    planning function (this task's own new code) correctly reports it as
    a fresh SourceAssertion it would create, never confusing it with the
    unrelated, already-persisted original."""
    from app.services.discovery_candidate_fragment import CandidateFragment
    from scripts.capture_mac_discovery import plan_governed_persistence

    original = CandidateFragment(
        artifact_identity=ARTIFACT_IDENTITY, source_locator=SOURCE_LOCATOR,
        raw_text="Metropolitan Airports Commission. Runway 30L EMAS advance deposit.",
        issuers=frozenset({"Metropolitan Airports Commission"}), runway_ends=frozenset({"30L"}),
    )
    revised = CandidateFragment(
        artifact_identity=ARTIFACT_IDENTITY, source_locator=SOURCE_LOCATOR,
        raw_text="Metropolitan Airports Commission. Runway 30L EMAS advance deposit - SCHEDULE REVISED.",
        issuers=frozenset({"Metropolitan Airports Commission"}), runway_ends=frozenset({"30L"}),
    )
    assert original.fragment_hash != revised.fragment_hash

    engine = build_engine(migrated_db)
    with Session(engine) as session:
        plan_before = plan_governed_persistence(session, ARTIFACT_IDENTITY, original, {})
        assert plan_before.source_assertion_would_be_created is True

        # Persist the original for real (isolated, in this same disposable DB).
        from app.services.discovery_evidence_persistence import DiscoverySourceMetadata, persist_discovery_fragment
        meta = DiscoverySourceMetadata(document_identity=ARTIFACT_IDENTITY, title="EMAS memo")
        persist_discovery_fragment(session, meta, original, [])
        session.commit()

        plan_original_again = plan_governed_persistence(session, ARTIFACT_IDENTITY, original, {})
        assert plan_original_again.source_assertion_would_be_created is False  # already exists

        plan_revised = plan_governed_persistence(session, ARTIFACT_IDENTITY, revised, {})
        assert plan_revised.source_assertion_would_be_created is True  # genuinely new fragment identity
        assert plan_revised.source_would_be_created is False  # same document, Source already exists
    engine.dispose()


# --- 20. no canonical-entity creation ---


def test_no_canonical_entity_creation(migrated_db):
    engine = build_engine(migrated_db)
    with Session(engine) as session:
        before_runways = session.query(Runway).count()
        before_ends = session.query(RunwayEnd).count()
        before_airports = session.query(Airport).count()
    engine.dispose()

    dry = run_capture(CaptureConfig(database=migrated_db, fixture_documents=(_fixture_document(),)))
    run_capture(CaptureConfig(
        database=migrated_db, fixture_documents=(_fixture_document(),),
        apply=True, allow_database_write=True, expected_fingerprint=dry["plan_fingerprint"], skip_backup=True,
    ))

    engine = build_engine(migrated_db)
    with Session(engine) as session:
        assert session.query(Runway).count() == before_runways
        assert session.query(RunwayEnd).count() == before_ends
        assert session.query(Airport).count() == before_airports
        assert session.query(Installation).count() == 0
        assert session.query(PhysicalInstallationIdentity).count() == 0
    engine.dispose()


# --- additional: fingerprint helper unit behavior ---


def test_compute_plan_fingerprint_is_order_independent():
    from scripts.capture_mac_discovery import PlannedGovernedEvidence

    a = PlannedGovernedEvidence(
        document_identity="doc-a", fragment_identity=("doc-a", "loc-a", "hash-a"),
        guard_outcome="ATTACH_CONFIRMED", guard_reason="r", attached_airport_id=1, attached_airport_code="MSP",
        source_external_id="discovery:doc-a", source_would_be_created=True, source_id_if_existing=None,
        source_assertion_would_be_created=True, source_assertion_id_if_existing=None,
    )
    b = PlannedGovernedEvidence(
        document_identity="doc-b", fragment_identity=("doc-b", "loc-b", "hash-b"),
        guard_outcome="INSUFFICIENT_IDENTITY", guard_reason="r2", attached_airport_id=None, attached_airport_code=None,
        source_external_id="discovery:doc-b", source_would_be_created=True, source_id_if_existing=None,
        source_assertion_would_be_created=True, source_assertion_id_if_existing=None,
    )
    assert compute_plan_fingerprint([a, b]) == compute_plan_fingerprint([b, a])
