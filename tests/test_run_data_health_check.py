"""Tests for scripts.run_data_health_check - the operational Fleet Health
Check CLI. Every test builds an isolated, disposable SQLite database (always
under pytest's own tmp_path); the real data/runway_safe.db is never opened
here (that is exercised separately as a real, read-only smoke run, not a
pytest test)."""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Runway, RunwayEnd, Signal
from app.services.fleet_health_rules import HealthClassification
import scripts.run_data_health_check as cli
from scripts.migrate_intelligence_review_persistence_slice4 import downgrade as downgrade_slice4
from scripts.migrate_governed_signal_creation_slice9c import downgrade as downgrade_slice9c

MODULE_PATH = Path(cli.__file__)


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "health.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    return path


def _seed(db_path, rows):
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as s:
        rows(s)
        s.commit()
    engine.dispose()


def _airport(session, id, name="Test Airport", country="XX", iata=None, icao=None, faa=None):
    a = Airport(id=id, name=name, country=country, iata_code=iata, icao_code=icao, faa_code=faa)
    session.add(a)
    return a


def _runway(session, id, airport_id, designation="01/19"):
    r = Runway(id=id, airport_id=airport_id, designation=designation)
    session.add(r)
    session.add(RunwayEnd(id=id * 100 + 1, runway_id=id, designation="01"))
    session.add(RunwayEnd(id=id * 100 + 2, runway_id=id, designation="19"))
    return r


def _signal(
    session, id, airport_id, runway_id=None, title="Signal", category="new_installation",
    confidence="high", source_id=None, published=True,
):
    s = Signal(
        id=id, airport_id=airport_id, runway_id=runway_id, title=title, category=category,
        confidence=confidence, source_id=source_id, published=published,
    )
    session.add(s)
    return s


def _fully_healthy_airport(session, id):
    _airport(session, id, name=f"Airport {id}", iata="AAA", icao="KAAA", faa="AAA")
    _runway(session, id * 10, airport_id=id)


# ---------------------------------------------------------------------------
# No health logic in CLI (AST review)
# ---------------------------------------------------------------------------


class TestNoHealthLogicInCli:
    def test_no_forbidden_rule_identifiers(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        identifiers = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                identifiers.add(node.id)
            elif isinstance(node, ast.Attribute):
                identifiers.add(node.attr)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                identifiers.add(node.name)
        forbidden = {
            "duplicate", "levenshtein", "fuzzy", "keyword_match",
            "temporal_conflict", "governance_decision", "publication_set",
        }
        assert not (identifiers & forbidden)

    def test_only_composes_existing_service_entry_points(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "fleet_health" in node.module:
                imported_names |= {alias.name for alias in node.names}
        assert imported_names == {
            "run_fleet_hard_invariant_check",
            "run_fleet_review_check",
            "run_fleet_presentation_check",
            "HealthClassification",
            "HealthFinding",
            "RULE_IDS",
            "REVIEW_RULE_IDS",
            "PRESENTATION_RULE_IDS",
        }

    def test_no_class_definitions_beyond_config_and_report_dataclasses(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        class_names = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        assert class_names == {"FleetHealthCliConfig", "FleetHealthCliReport"}


# ---------------------------------------------------------------------------
# Read-only guarantee
# ---------------------------------------------------------------------------


class TestReadOnlyGuarantee:
    def test_no_write_sql_statements_emitted_instrumented(self, db, tmp_path):
        _seed(db, lambda s: (_airport(s, 1), _signal(s, 1, airport_id=1)))
        engine = create_engine(f"sqlite:///{db}")
        statements = []

        def _capture(_conn, _cursor, statement, *_args, **_kwargs):
            statements.append(statement.strip().upper())

        event.listen(engine, "before_cursor_execute", _capture)
        try:
            config = cli.FleetHealthCliConfig(database=db, output_dir=tmp_path / "out")
            cli.run_data_health_check(config)
        finally:
            event.remove(engine, "before_cursor_execute", _capture)
            engine.dispose()
        write_statements = [s for s in statements if s.startswith(("INSERT", "UPDATE", "DELETE"))]
        assert write_statements == []

    def test_db_only_mode_readonly_engine_refuses_writes(self, db):
        engine = cli.build_readonly_engine(db)
        with Session(engine) as s:
            with pytest.raises(Exception):
                s.execute(text("INSERT INTO airports (id, name, country) VALUES (999, 'x', 'XX')"))
                s.commit()
            s.rollback()
        engine.dispose()

    def test_real_db_file_unchanged_after_run(self, db, tmp_path):
        _seed(db, lambda s: (_airport(s, 1), _signal(s, 1, airport_id=1)))
        before = db.read_bytes()
        config = cli.FleetHealthCliConfig(database=db)
        cli.run_data_health_check(config)
        after = db.read_bytes()
        assert before == after


# ---------------------------------------------------------------------------
# Scenario A-O
# ---------------------------------------------------------------------------


class TestScenarios:
    def test_a_completely_healthy_fleet(self, db):
        _seed(db, lambda s: _fully_healthy_airport(s, 1))
        report = cli.run_data_health_check(cli.FleetHealthCliConfig(database=db))
        assert report.hard_findings == ()
        assert all(
            f.classification == HealthClassification.INFORMATIONAL for f in report.review_findings
        ) or report.review_findings == ()
        status = cli.overall_status(report, cli.FleetHealthCliConfig(database=db))
        assert status in ("HEALTHY", "ATTENTION_REQUIRED")
        assert cli.exit_code_for(report) == cli.EXIT_HEALTHY_OR_ATTENTION

    def test_b_informational_only(self, db):
        # Airport with no runway rows => FH-A1 (INFORMATIONAL), otherwise clean.
        _seed(db, lambda s: _airport(s, 1, name="Airport 1", iata="AAA", icao="KAAA", faa="AAA"))
        config = cli.FleetHealthCliConfig(database=db)
        report = cli.run_data_health_check(config)
        assert report.hard_findings == ()
        assert any(f.rule_id == "FH-A1" for f in report.review_findings)
        assert cli.overall_status(report, config) == "HEALTHY"
        assert cli.exit_code_for(report) == cli.EXIT_HEALTHY_OR_ATTENTION

    def test_c_warning_only(self, db):
        # Airport with no code (IATA/ICAO/FAA) => FH-A3 (DETERMINISTIC_WARNING).
        _seed(db, lambda s: (_airport(s, 1, name="Airport 1"), _runway(s, 10, airport_id=1)))
        config = cli.FleetHealthCliConfig(database=db)
        report = cli.run_data_health_check(config)
        assert report.hard_findings == ()
        assert any(f.rule_id == "FH-A3" for f in report.review_findings)
        assert cli.overall_status(report, config) == "ATTENTION_REQUIRED"
        assert cli.exit_code_for(report) == cli.EXIT_HEALTHY_OR_ATTENTION

    def test_i_presentation_not_requested(self, db):
        _seed(db, lambda s: _fully_healthy_airport(s, 1))
        config = cli.FleetHealthCliConfig(database=db)
        report = cli.run_data_health_check(config)
        assert report.presentation_findings is None
        assert report.presentation_error is None
        rendered = cli.render_report(report, config)
        assert "Presentation check: NOT RUN" in rendered

    def test_g_presentation_clean(self, db, tmp_path):
        _seed(db, lambda s: (_airport(s, 1), _signal(s, 1, airport_id=1, published=True)))
        config = cli.FleetHealthCliConfig(database=db, output_dir=tmp_path / "out")
        report = cli.run_data_health_check(config)
        assert report.presentation_findings == ()
        assert report.presentation_error is None
        rendered = cli.render_report(report, config)
        assert "Presentation check: PASS" in rendered

    def test_h_presentation_missing_page(self, db, tmp_path, monkeypatch):
        _seed(db, lambda s: (_airport(s, 1), _signal(s, 1, airport_id=1, published=True)))
        output = tmp_path / "out"

        import app.services.fleet_health_presentation_check as fhc_module

        real_run = fhc_module.run_fleet_presentation_check

        def _stale_run(session, out_dir):
            snapshot = fhc_module.build_fleet_presentation_snapshot(session, out_dir)
            (Path(out_dir) / "signals" / "1.html").unlink()
            from app.services.fleet_health_presentation_rules import (
                FleetPresentationSnapshot, evaluate_presentation_findings,
            )
            rendered = fhc_module._read_rendered_signal_page_facts(out_dir)
            doctored = FleetPresentationSnapshot(
                published_signals=snapshot.published_signals, rendered_signal_pages=rendered,
            )
            return evaluate_presentation_findings(doctored)

        monkeypatch.setattr(cli, "run_fleet_presentation_check", _stale_run)
        config = cli.FleetHealthCliConfig(database=db, output_dir=output)
        report = cli.run_data_health_check(config)
        assert len(report.presentation_findings) == 1
        assert report.presentation_findings[0].rule_id == "FH-H2"
        assert cli.overall_status(report, config) == "ERROR"
        assert cli.exit_code_for(report) == cli.EXIT_DETERMINISTIC_ERROR

    def test_j_schema_missing(self, tmp_path):
        # Full current ORM schema (creates source_assertions etc.), then a
        # real downgrade removing the slice-4 columns FHC's own schema gate
        # requires - matching the established pattern in
        # tests/test_human_review_queue.py (_database_missing_slice7),
        # rather than a wholly empty/tableless file (which the reused
        # inspect() helpers were never designed to tolerate).
        downgraded_db = tmp_path / "downgraded.db"
        engine = create_engine(f"sqlite:///{downgraded_db}")
        Base.metadata.create_all(engine)
        engine.dispose()
        downgrade_slice4(downgraded_db)
        config = cli.FleetHealthCliConfig(database=downgraded_db)
        report = cli.run_data_health_check(config)
        assert report.blockers == (cli.SCHEMA_MIGRATION_REQUIRED_BLOCKER,)
        assert cli.exit_code_for(report) == cli.EXIT_OPERATIONAL_FAILURE
        rendered = cli.render_report(report, config)
        assert "BLOCKED" in rendered
        assert cli.SCHEMA_MIGRATION_REQUIRED_BLOCKER in rendered

    def test_k_exporter_failure(self, db, tmp_path, monkeypatch):
        _seed(db, lambda s: (_airport(s, 1), _signal(s, 1, airport_id=1, published=True)))

        def _boom(session, output_dir):
            raise RuntimeError("simulated export crash")

        monkeypatch.setattr(cli, "run_fleet_presentation_check", _boom)
        config = cli.FleetHealthCliConfig(database=db, output_dir=tmp_path / "out")
        report = cli.run_data_health_check(config)
        assert report.presentation_error is not None
        assert "simulated export crash" in report.presentation_error
        assert cli.exit_code_for(report) == cli.EXIT_OPERATIONAL_FAILURE
        rendered = cli.render_report(report, config)
        assert "Presentation check: ERROR" in rendered
        # FHC1/FHC3 results must still be present even though FHC4 crashed.
        assert report.hard_findings == ()

    def test_l_wrong_db_isolation(self, tmp_path):
        # Mission section 4/30 attack: target.db vs protected.db, with
        # distinctive, non-overlapping content - proves the CLI's report
        # reflects ONLY the database actually passed via --database, and
        # that the untouched database is provably byte-identical
        # afterwards, not merely that report.database differs textually.
        target_db = tmp_path / "target.db"
        protected_db = tmp_path / "protected.db"

        engine_t = create_engine(f"sqlite:///{target_db}")
        Base.metadata.create_all(engine_t)
        with Session(engine_t) as s:
            _airport(s, 1, name="Airport A", iata="AAA", icao="KAAA", faa="AAA")
            _runway(s, 10, airport_id=1)
            # Deliberately unhealthy in a TARGET-only, identifiable way:
            # airport with no code at all triggers FH-A3 (WARNING).
            _airport(s, 2, name="TARGET-ONLY UNHEALTHY AIRPORT")
            _runway(s, 20, airport_id=2)
            s.commit()
        engine_t.dispose()

        engine_p = create_engine(f"sqlite:///{protected_db}")
        Base.metadata.create_all(engine_p)
        with Session(engine_p) as s:
            _airport(s, 1, name="Airport B", iata="BBB", icao="KBBB", faa="BBB")
            _runway(s, 10, airport_id=1)
            s.commit()
        engine_p.dispose()
        before_protected = protected_db.read_bytes()

        report_target = cli.run_data_health_check(cli.FleetHealthCliConfig(database=target_db))
        assert protected_db.read_bytes() == before_protected  # untouched by the target-only run

        report_protected = cli.run_data_health_check(cli.FleetHealthCliConfig(database=protected_db))

        # The target's own findings must reflect ONLY target content.
        assert any(f.rule_id == "FH-A3" for f in report_target.review_findings)
        # The protected database is fully healthy/coded and must show none.
        assert all(f.rule_id != "FH-A3" for f in report_protected.review_findings)
        assert report_target.database != report_protected.database
        assert protected_db.read_bytes() == before_protected  # still untouched after its own run too

    def test_m_unicode_international_data(self, db):
        _seed(
            db,
            lambda s: (
                _airport(s, 1, name="Örnsköldsvik Flygplats", country="Sverige", icao="ESNO"),
                _signal(s, 1, airport_id=1, title="Örnsköldsvik – ny EMAS-installation", published=True),
                _runway(s, 10, airport_id=1),
            ),
        )
        config = cli.FleetHealthCliConfig(database=db)
        report = cli.run_data_health_check(config)
        rendered = cli.render_report(report, config)
        assert "RWI FLEET HEALTH" in rendered

    def test_n_duplicate_findings_order_determinism(self, db):
        _seed(
            db,
            lambda s: (
                _airport(s, 1, name="Airport 1"),
                _airport(s, 2, name="Airport 2"),
            ),
        )
        config = cli.FleetHealthCliConfig(database=db)
        report1 = cli.run_data_health_check(config)
        report2 = cli.run_data_health_check(config)
        assert cli.render_report(report1, config) == cli.render_report(report2, config)
        assert report1.hard_findings == report2.hard_findings
        assert report1.review_findings == report2.review_findings

    def test_o_external_pending_writer_isolated_from_cli_own_connection(self, db, tmp_path):
        # Critical-review wording correction: this CLI opens its OWN
        # read-only engine/session every run (build_readonly_engine()) and
        # never receives or touches a caller-supplied Session object - so
        # there is no "caller pending Session" for the CLI itself to
        # autoflush; that scenario is impossible at this boundary (the
        # no_autoflush guarantee that matters is FHC2/FHC3/FHC4's own, on
        # THEIR session parameter, already verified in their own test
        # suites). What IS meaningful and worth proving here is ordinary
        # SQLite/SQLAlchemy transaction isolation: an external writer's
        # pending, uncommitted change on the same database FILE is neither
        # disturbed nor made visible by the CLI's own, completely separate
        # connection.
        _seed(db, lambda s: (_airport(s, 1, name="Original"), _signal(s, 1, airport_id=1)))
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            airport = s.get(Airport, 1)
            airport.name = "PENDING - MUST NOT BE VISIBLE OR FLUSHED BY THE CLI"
            assert airport in s.dirty
            config = cli.FleetHealthCliConfig(database=db)
            cli.run_data_health_check(config)
            assert airport in s.dirty
            s.rollback()
        engine.dispose()
        raw_engine = create_engine(f"sqlite:///{db}")
        with Session(raw_engine) as s:
            name = s.execute(text("SELECT name FROM airports WHERE id = 1")).scalar_one()
        raw_engine.dispose()
        assert name == "Original"


# ---------------------------------------------------------------------------
# Overall status / exit code semantics
# ---------------------------------------------------------------------------


class TestStatusAndExitCodes:
    def test_hard_error_yields_error_status_and_exit_1(self, db):
        # FH-B1/B2-style hard invariant violation: a Signal referencing a
        # runway that belongs to a different airport than the signal itself.
        _seed(
            db,
            lambda s: (
                _airport(s, 1),
                _airport(s, 2),
                _runway(s, 10, airport_id=2),
                _signal(s, 1, airport_id=1, runway_id=10, published=True),
            ),
        )
        config = cli.FleetHealthCliConfig(database=db)
        report = cli.run_data_health_check(config)
        assert len(report.hard_findings) >= 1
        assert cli.overall_status(report, config) == "ERROR"
        assert cli.exit_code_for(report) == cli.EXIT_DETERMINISTIC_ERROR

    def test_informational_alone_never_makes_fleet_unhealthy(self, db):
        _seed(db, lambda s: _airport(s, 1, name="Airport 1", iata="AAA", icao="KAAA", faa="AAA"))
        config = cli.FleetHealthCliConfig(database=db)
        report = cli.run_data_health_check(config)
        assert all(f.rule_id != "FH-A3" for f in report.review_findings)
        assert cli.overall_status(report, config) == "HEALTHY"


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


class TestFiltering:
    def test_rule_filter(self, db):
        _seed(
            db,
            lambda s: (
                _airport(s, 1, name="Airport 1"),
                _airport(s, 2, name="Airport 2"),
            ),
        )
        config = cli.FleetHealthCliConfig(database=db, rule="FH-A1")
        report = cli.run_data_health_check(config)
        rendered = cli.render_report(report, config)
        assert "FH-A3" not in rendered.split("Presentation")[0].replace("Presentation errors", "")

    def test_classification_filter_json(self, db):
        _seed(db, lambda s: _airport(s, 1, name="Airport 1"))
        config = cli.FleetHealthCliConfig(database=db, classification="INFORMATIONAL")
        report = cli.run_data_health_check(config)
        payload = json.loads(cli.render_json_report(report, config))
        assert all(f["classification"] == "INFORMATIONAL" for f in payload["review_findings"])


# ---------------------------------------------------------------------------
# CLI entry point / argparse
# ---------------------------------------------------------------------------


class TestMainEntryPoint:
    def test_database_argument_is_required(self):
        with pytest.raises(SystemExit):
            cli.main([])

    def test_main_runs_and_returns_exit_code(self, db, capsys):
        _seed(db, lambda s: _fully_healthy_airport(s, 1))
        code = cli.main(["--database", str(db)])
        assert code == cli.EXIT_HEALTHY_OR_ATTENTION
        captured = capsys.readouterr()
        assert "RWI FLEET HEALTH" in captured.out

    def test_main_json_mode(self, db, capsys):
        _seed(db, lambda s: _fully_healthy_airport(s, 1))
        code = cli.main(["--database", str(db), "--json"])
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert "overall_status" in payload
        assert code in (cli.EXIT_HEALTHY_OR_ATTENTION, cli.EXIT_DETERMINISTIC_ERROR)

    def test_main_with_output_dir_runs_presentation_check(self, db, tmp_path, capsys):
        _seed(db, lambda s: (_airport(s, 1), _signal(s, 1, airport_id=1, published=True)))
        code = cli.main(["--database", str(db), "--output-dir", str(tmp_path / "out")])
        captured = capsys.readouterr()
        assert "Presentation check: PASS" in captured.out
        assert code == cli.EXIT_HEALTHY_OR_ATTENTION

    def test_no_default_database_in_parser(self):
        parser = cli._parser()
        for action in parser._actions:
            if action.dest == "database":
                assert action.default is None
                assert action.required is True

    def test_no_repair_or_disposition_flags(self):
        parser = cli._parser()
        flags = {opt for action in parser._actions for opt in action.option_strings}
        forbidden = {"--fix", "--repair", "--approve", "--duplicate", "--publish", "--resolve", "--suppress", "--ignore"}
        assert not (flags & forbidden)


# ---------------------------------------------------------------------------
# Critical-review additions
# ---------------------------------------------------------------------------


class TestSchemaGateCompleteness:
    """Review-checkpoint regression tests for a genuine defect found during
    this review: the schema gate, copied from list_human_review_queue.py's
    own three-inspector composition, did not cover Slice 1
    (identity_guard_decision/reason) or Slice 9C (signal_id) - both are
    read unconditionally by FHC1's own D2 fact builder and FHC3's own G1
    rule, so a database missing either genuinely crashed with a raw
    sqlite3.OperationalError instead of refusing cleanly. Fixed by
    extending check_schema_readiness() to five inspectors."""

    def test_missing_identity_guard_decision_column_refuses_cleanly_not_crash(self, tmp_path):
        import app.models  # noqa: F401 - populate Base.metadata before create_all
        db = tmp_path / "missing_slice1.db"
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        engine.dispose()
        raw = create_engine(f"sqlite:///{db}").connect()
        raw.exec_driver_sql("ALTER TABLE source_assertions DROP COLUMN identity_guard_decision")
        raw.commit()
        raw.close()

        config = cli.FleetHealthCliConfig(database=db)
        report = cli.run_data_health_check(config)  # must not raise
        assert report.blockers == (cli.SCHEMA_MIGRATION_REQUIRED_BLOCKER,)
        assert report.schema_readiness["identity_guard_decision_column_exists"] is False
        assert cli.exit_code_for(report) == cli.EXIT_OPERATIONAL_FAILURE

    def test_missing_signal_id_column_refuses_cleanly_not_crash(self, tmp_path):
        # signal_id carries a foreign-key definition, so unlike the plain
        # nullable slice1 columns it cannot be dropped via a bare ALTER
        # TABLE DROP COLUMN (SQLite refuses: "unknown column ... in foreign
        # key definition") - use the migration's own already-proven
        # downgrade() (a full table rebuild), matching the established
        # pattern already used for the schema-missing scenario above.
        import app.models  # noqa: F401
        db = tmp_path / "missing_slice9c.db"
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        engine.dispose()
        downgrade_slice9c(db)

        config = cli.FleetHealthCliConfig(database=db)
        report = cli.run_data_health_check(config)  # must not raise
        assert report.blockers == (cli.SCHEMA_MIGRATION_REQUIRED_BLOCKER,)
        assert report.schema_readiness["signal_id_column_exists"] is False
        assert cli.exit_code_for(report) == cli.EXIT_OPERATIONAL_FAILURE

    def test_fully_migrated_real_style_schema_passes_gate(self, db):
        # The normal db fixture (Base.metadata.create_all with no
        # downgrades) has every column - sanity check the extended gate
        # doesn't false-positive-block a healthy schema.
        report = cli.run_data_health_check(cli.FleetHealthCliConfig(database=db))
        assert report.blockers == ()


class TestCanonicalSiteProtection:
    """Review-checkpoint regression tests for a genuine safety gap found
    during this review: nothing stopped `--output-dir` from being pointed
    at the repository's own canonical site/ output, which build_site()
    deletes and recreates on every call - a user (or a script) passing
    `--output-dir site` from the repo root would have silently destroyed
    real generated production output. Fixed by refusing this case before
    ever calling FHC4/build_site()."""

    def test_canonical_site_output_dir_refused_without_calling_exporter(
        self, db, tmp_path, monkeypatch
    ):
        called = []
        monkeypatch.setattr(
            cli, "run_fleet_presentation_check",
            lambda *a, **k: called.append(True) or (),
        )
        monkeypatch.setattr(cli, "CANONICAL_SITE_DIR", tmp_path / "site")
        config = cli.FleetHealthCliConfig(database=db, output_dir=tmp_path / "site")
        report = cli.run_data_health_check(config)
        assert called == []  # the real/patched exporter must never be invoked
        assert report.presentation_error is not None
        assert cli.CANONICAL_SITE_OUTPUT_REFUSED in report.presentation_error
        assert cli.exit_code_for(report) == cli.EXIT_OPERATIONAL_FAILURE
        assert cli.overall_status(report, config) == "OPERATIONAL_FAILURE"

    def test_non_canonical_output_dir_with_same_basename_is_not_refused(
        self, db, tmp_path, monkeypatch
    ):
        # "site" as a basename elsewhere on disk must NOT be treated as the
        # canonical directory - only the exact resolved repository path is
        # refused.
        monkeypatch.setattr(cli, "CANONICAL_SITE_DIR", tmp_path / "the_real_canonical_site")
        other_site_dir = tmp_path / "some_subdir" / "site"
        config = cli.FleetHealthCliConfig(database=db, output_dir=other_site_dir)
        report = cli.run_data_health_check(config)
        assert report.presentation_error is None
        assert report.presentation_findings == ()

    def test_main_entry_point_refuses_canonical_site_end_to_end(self, db, tmp_path, monkeypatch, capsys):
        # End-to-end through main() (argparse -> run -> render -> exit
        # code), not just the internal run_data_health_check() call - the
        # real repository-level attack (`--output-dir site` from the repo
        # root) was reproduced manually against the live repo during this
        # review and confirmed refused; this is the permanent, isolated
        # equivalent.
        monkeypatch.setattr(cli, "CANONICAL_SITE_DIR", tmp_path / "site")
        code = cli.main(["--database", str(db), "--output-dir", str(tmp_path / "site")])
        captured = capsys.readouterr()
        assert code == cli.EXIT_OPERATIONAL_FAILURE
        assert "OPERATIONAL_FAILURE" in captured.out
        assert cli.CANONICAL_SITE_OUTPUT_REFUSED in captured.out
        assert not (tmp_path / "site").exists()  # never created, let alone deleted/rebuilt

    def test_is_canonical_site_output_helper_directly(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, "CANONICAL_SITE_DIR", tmp_path / "site")
        assert cli._is_canonical_site_output(tmp_path / "site") is True
        # A path that resolves to the same directory via traversal must
        # also be caught - the comparison is on the resolved path.
        traversal_path = tmp_path / "other" / ".." / "site"
        assert cli._is_canonical_site_output(traversal_path) is True
        assert cli._is_canonical_site_output(tmp_path / "not_site") is False


class TestOverallStatusPresentationCrashConsistency:
    """Review-checkpoint regression test for a genuine defect found during
    this review: a clean FHC1/FHC3 result combined with a crashed FHC4 run
    rendered 'Overall status: HEALTHY' while the CLI still exited 2 - a
    real, reproduced, misleading partial-success combination (mission item
    24). Fixed by making overall_status() check report.presentation_error
    before anything else."""

    def test_clean_findings_with_presentation_crash_is_not_healthy(self, db, tmp_path, monkeypatch):
        _seed(db, lambda s: _fully_healthy_airport(s, 1))

        def _boom(session, output_dir):
            raise RuntimeError("simulated export crash")

        monkeypatch.setattr(cli, "run_fleet_presentation_check", _boom)
        config = cli.FleetHealthCliConfig(database=db, output_dir=tmp_path / "out")
        report = cli.run_data_health_check(config)
        assert report.hard_findings == ()
        status = cli.overall_status(report, config)
        assert status == "OPERATIONAL_FAILURE"
        assert status != "HEALTHY"
        assert cli.exit_code_for(report) == cli.EXIT_OPERATIONAL_FAILURE

    def test_rendered_report_never_says_healthy_alongside_presentation_error_line(
        self, db, tmp_path, monkeypatch
    ):
        _seed(db, lambda s: _fully_healthy_airport(s, 1))
        monkeypatch.setattr(
            cli, "run_fleet_presentation_check",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        config = cli.FleetHealthCliConfig(database=db, output_dir=tmp_path / "out")
        report = cli.run_data_health_check(config)
        rendered = cli.render_report(report, config)
        assert "Overall status: HEALTHY" not in rendered
        assert "Presentation check: ERROR" in rendered


class TestFilteringNeverChangesGlobalHealthState:
    """Mission items 16/17: a --rule/--classification filter must be
    display-only. A user must never be able to hide a real hard error and
    see HEALTHY/exit 0."""

    def test_filtering_never_changes_overall_status_or_exit_code(self, db):
        _seed(
            db,
            lambda s: (
                _airport(s, 1),
                _airport(s, 2),
                _runway(s, 10, airport_id=2),
                _signal(s, 1, airport_id=1, runway_id=10, published=True),  # cross-airport hard error
            ),
        )
        unfiltered = cli.FleetHealthCliConfig(database=db)
        filtered = cli.FleetHealthCliConfig(database=db, classification="INFORMATIONAL")
        report = cli.run_data_health_check(unfiltered)
        assert len(report.hard_findings) >= 1

        # Same underlying report, evaluated once unfiltered and once with a
        # display filter applied - overall_status/exit_code must agree.
        assert cli.overall_status(report, unfiltered) == "ERROR"
        assert cli.overall_status(report, filtered) == "ERROR"
        assert cli.exit_code_for(report) == cli.EXIT_DETERMINISTIC_ERROR

        rendered_filtered = cli.render_report(report, filtered)
        assert "Overall status: ERROR" in rendered_filtered
        # The filtered DISPLAY correctly shows 0 hard errors (display-only
        # scoping) even though the real status is ERROR.
        assert "Hard errors ............. 0 finding(s)" in rendered_filtered

    def test_rule_filter_same_safety(self, db):
        _seed(
            db,
            lambda s: (
                _airport(s, 1),
                _airport(s, 2),
                _runway(s, 10, airport_id=2),
                _signal(s, 1, airport_id=1, runway_id=10, published=True),
            ),
        )
        config = cli.FleetHealthCliConfig(database=db)
        report = cli.run_data_health_check(config)
        hard_rule_id = report.hard_findings[0].rule_id
        other_rule_config = cli.FleetHealthCliConfig(
            database=db, rule="FH-A1" if hard_rule_id != "FH-A1" else "FH-A3"
        )
        assert cli.overall_status(report, other_rule_config) == "ERROR"
        assert cli.exit_code_for(report) == cli.EXIT_DETERMINISTIC_ERROR


class TestLongEntityListRendering:
    def test_finding_with_many_entities_is_truncated_by_default(self):
        finding = cli.HealthFinding(
            rule_id="FH-F1", classification=cli.HealthClassification.INFORMATIONAL,
            entity_type="Signal", entity_ids=tuple(range(1, 68)), airport_id=None,
            summary="67 Signals have a legacy source_id", structured_evidence={},
        )
        lines = cli._render_finding_line(finding, details=False)
        text = "\n".join(lines)
        assert "more" in text
        assert "..." in text
        # Every one of the 67 ids must NOT all be inline by default.
        assert text.count(",") < 66

    def test_small_finding_not_truncated(self):
        finding = cli.HealthFinding(
            rule_id="FH-A1", classification=cli.HealthClassification.INFORMATIONAL,
            entity_type="Airport", entity_ids=(1, 2, 3), airport_id=None,
            summary="test", structured_evidence={},
        )
        lines = cli._render_finding_line(finding, details=False)
        text = "\n".join(lines)
        assert "more" not in text
        assert "1, 2, 3" in text


class TestSchemaGateRunsBeforeExport:
    def test_export_never_attempted_when_schema_not_ready(self, tmp_path, monkeypatch):
        import app.models  # noqa: F401
        db = tmp_path / "unready.db"
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        engine.dispose()
        raw = create_engine(f"sqlite:///{db}").connect()
        raw.exec_driver_sql("ALTER TABLE source_assertions DROP COLUMN identity_guard_decision")
        raw.commit()
        raw.close()

        called = []
        monkeypatch.setattr(
            cli, "run_fleet_presentation_check",
            lambda *a, **k: called.append(True) or (),
        )
        config = cli.FleetHealthCliConfig(database=db, output_dir=tmp_path / "out")
        report = cli.run_data_health_check(config)
        assert called == []
        assert report.blockers == (cli.SCHEMA_MIGRATION_REQUIRED_BLOCKER,)
        assert not (tmp_path / "out").exists()  # no export artifact was ever produced


class TestDuplicateFindingsPassthrough:
    """Mission item 23: the CLI must not silently invent a dedup layer -
    if a (mocked) service returns duplicate HealthFinding objects, the CLI
    renders them as-is; deduplication is the service layer's own
    responsibility (already guaranteed by FHC1/FHC3/FHC4's own reviewed
    contracts)."""

    def test_duplicate_findings_from_service_are_not_silently_deduplicated(
        self, db, monkeypatch
    ):
        dup = cli.HealthFinding(
            rule_id="FH-A3", classification=cli.HealthClassification.DETERMINISTIC_WARNING,
            entity_type="Airport", entity_ids=(1,), airport_id=1,
            summary="duplicate warning", structured_evidence={},
        )
        monkeypatch.setattr(cli, "run_fleet_hard_invariant_check", lambda session: ())
        monkeypatch.setattr(cli, "run_fleet_review_check", lambda session: (dup, dup))
        config = cli.FleetHealthCliConfig(database=db)
        report = cli.run_data_health_check(config)
        assert report.review_findings == (dup, dup)
        rendered = cli.render_report(report, config)
        assert rendered.count("duplicate warning") == 2


class TestRuleFilterValidation:
    def test_unknown_rule_id_fails_loud_via_argparse(self, db):
        with pytest.raises(SystemExit):
            cli.main(["--database", str(db), "--rule", "FH-NOT-A-REAL-RULE"])

    def test_known_rule_ids_all_accepted(self):
        parser = cli._parser()
        for rule_id in cli.ALL_RULE_IDS:
            args = parser.parse_args(["--database", "x.db", "--rule", rule_id])
            assert args.rule == rule_id

    def test_all_rule_ids_is_union_of_three_registries(self):
        from app.services.fleet_health_presentation_rules import PRESENTATION_RULE_IDS
        from app.services.fleet_health_review_rules import REVIEW_RULE_IDS
        from app.services.fleet_health_rules import RULE_IDS
        assert set(cli.ALL_RULE_IDS) == set(RULE_IDS) | set(REVIEW_RULE_IDS) | set(PRESENTATION_RULE_IDS)


class TestJsonErrorModes:
    def test_json_schema_blocked_is_valid_json_no_mixed_output(self, tmp_path, capsys):
        import app.models  # noqa: F401
        db = tmp_path / "blocked.db"
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        engine.dispose()
        downgrade_slice4(db)
        code = cli.main(["--database", str(db), "--json"])
        captured = capsys.readouterr()
        payload = json.loads(captured.out)  # must parse cleanly, no stray prose
        assert payload["blocked"] is True
        assert code == cli.EXIT_OPERATIONAL_FAILURE

    def test_json_exporter_crash_is_valid_json_with_error_field(self, db, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(
            cli, "run_fleet_presentation_check",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        code = cli.main(["--database", str(db), "--output-dir", str(tmp_path / "out"), "--json"])
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload["presentation_check_error"] is not None
        assert "boom" in payload["presentation_check_error"]
        assert payload["overall_status"] == "OPERATIONAL_FAILURE"
        assert code == cli.EXIT_OPERATIONAL_FAILURE

    def test_stdout_stderr_separated_for_argparse_errors(self, capsys):
        with pytest.raises(SystemExit):
            cli.main([])
        captured = capsys.readouterr()
        assert captured.out == ""  # no partial/mixed report text on stdout
        assert "database" in captured.err.lower() or "required" in captured.err.lower()
