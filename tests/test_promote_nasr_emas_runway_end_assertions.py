"""Isolated tests for scripts/promote_nasr_emas_runway_end_assertions.py
(docs/domain/nasr-emas-auto-resolvable-promotion-dry-run.md).

Never touches the real development database - builds isolated in-memory
databases per test. NEVER points a real --apply at data/runway_safe.db."""
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app import models  # noqa: F401 - registers all metadata
from app.models import (
    Airport,
    InstallationAssertionLink,
    PhysicalInstallationIdentity,
    Runway,
    RunwayEnd,
    Signal,
    Source,
    SourceAssertion,
)
from scripts.promote_nasr_emas_runway_end_assertions import (
    ALREADY_PROMOTED,
    WRITABLE,
    PromotionGuardError,
    apply,
    dry_run,
    plan,
)


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _seed_airport_with_runway(session, *, name, code, pair):
    airport = Airport(name=name, faa_code=code, country="USA")
    session.add(airport)
    session.flush()
    runway = Runway(airport_id=airport.id, designation=pair)
    session.add(runway)
    session.flush()
    end_a, end_b = pair.split("/")
    ra = RunwayEnd(runway_id=runway.id, designation=end_a)
    rb = RunwayEnd(runway_id=runway.id, designation=end_b)
    session.add_all([ra, rb])
    session.flush()
    return airport, runway, ra, rb


def _seed_nasr_assertion(session, *, airport_id, raw_pair, raw_end, runway_end=None, title="NASR test cycle"):
    source = Source(
        title=title, source_type="faa_nasr_apt_ars", url="https://example.test/nasr",
        external_id=f"faa_nasr:airport_csv:test:{raw_pair}:{raw_end}:{title}",
    )
    session.add(source)
    session.flush()
    assertion = SourceAssertion(
        source_id=source.id, airport_id=airport_id, assertion_type="runway_end",
        raw_airport_identifier="TST", raw_runway_value=raw_pair, raw_runway_end_value=raw_end,
        runway_end=runway_end, raw_relevant_text="{}",
        source_record_identifier=f"test:{raw_pair}:{raw_end}:{source.id}",
        evidence_quality="direct_strong", review_state="unreviewed",
    )
    session.add(assertion)
    session.commit()
    return assertion


@pytest.fixture
def loose_snapshot(monkeypatch):
    """Most tests use a tiny fixture population, not literally 97 rows -
    relax the frozen EXPECTED_SNAPSHOT guard to match whatever the test's
    own fixture produces, computed once from the classifier itself, so the
    snapshot-drift guard is exercised by dedicated tests only."""
    import scripts.promote_nasr_emas_runway_end_assertions as writer
    from scripts.analyze_nasr_emas_runway_end_resolution import classify_all, summarize

    original_check = writer._check_snapshot

    def _relaxed_check(summary):
        pass  # no-op: this fixture deliberately bypasses the frozen-count guard

    monkeypatch.setattr(writer, "_check_snapshot", _relaxed_check)
    yield
    monkeypatch.setattr(writer, "_check_snapshot", original_check)


def test_apply_writes_every_row_in_a_multi_row_batch_regardless_of_session_autoflush(loose_snapshot):
    """A single-row batch cannot expose an autoflush-related bug: only a
    session.get() call for a DIFFERENT primary key partway through the
    write loop triggers an implicit autoflush of earlier pending changes.
    This test uses a plain Session(engine) (autoflush=True, the default
    every other test in this project's suite also uses) with 3 writable
    rows specifically to prove apply()'s own session.no_autoflush guard
    makes it correct regardless of the caller's session configuration -
    the exact gap that let all single-row tests pass while the real
    97-row disposable-copy rehearsal exposed a real bug (see the design
    doc's incident note)."""
    with Session(_engine()) as session:
        airport, *_ = _seed_airport_with_runway(session, name="Test Field", code="TST", pair="9/27")
        other_airport, *_ = _seed_airport_with_runway(session, name="Other Field", code="OTH", pair="9/27")
        third_airport, *_ = _seed_airport_with_runway(session, name="Third Field", code="THD", pair="9/27")
        a1 = _seed_nasr_assertion(session, airport_id=airport.id, raw_pair="09/27", raw_end="09", title="c1")
        a2 = _seed_nasr_assertion(session, airport_id=other_airport.id, raw_pair="09/27", raw_end="27", title="c2")
        a3 = _seed_nasr_assertion(session, airport_id=third_airport.id, raw_pair="09/27", raw_end="09", title="c3")

        fingerprint = dry_run(session)["fingerprint"]
        result = apply(session, expected_fingerprint=fingerprint)

        assert result["rows_written"] == 3
        assert session.get(SourceAssertion, a1.id).runway_end == "9"
        assert session.get(SourceAssertion, a2.id).runway_end == "27"
        assert session.get(SourceAssertion, a3.id).runway_end == "9"


def test_dry_run_default_performs_zero_writes(loose_snapshot):
    with Session(_engine()) as session:
        airport, *_ = _seed_airport_with_runway(session, name="Test Field", code="TST", pair="9/27")
        _seed_nasr_assertion(session, airport_id=airport.id, raw_pair="09/27", raw_end="09")

        result = dry_run(session)

        assert result["writable_count"] == 1
        assert len(session.new) == 0 and len(session.dirty) == 0 and len(session.deleted) == 0
        assertion = session.scalar(select(SourceAssertion))
        assert assertion.runway_end is None


def test_only_auto_resolvable_rows_are_planned(loose_snapshot):
    with Session(_engine()) as session:
        airport, runway, end_9, end_27 = _seed_airport_with_runway(
            session, name="Test Field", code="TST", pair="9/27"
        )
        identity = PhysicalInstallationIdentity(airport_id=airport.id, runway_id=runway.id, runway_end_id=end_9.id)
        session.add(identity)
        session.flush()
        prior_source = Source(title="prior", source_type="faa_nasr_apt_ars", url="https://example.test/p")
        session.add(prior_source)
        session.flush()
        prior_assertion = SourceAssertion(
            source_id=prior_source.id, airport_id=airport.id, assertion_type="runway_end",
            raw_runway_end_value="09", source_record_identifier="prior-1",
            evidence_quality="direct_strong", review_state="reviewed",
        )
        session.add(prior_assertion)
        session.flush()
        session.add(InstallationAssertionLink(
            assertion_id=prior_assertion.id, physical_installation_id=identity.id,
            outcome="SAME_PHYSICAL_INSTALLATION", reason="test", actor="human:test",
        ))
        session.commit()
        already_linked_new = _seed_nasr_assertion(session, airport_id=airport.id, raw_pair="09/27", raw_end="09", title="c2")
        auto_resolvable = _seed_nasr_assertion(session, airport_id=airport.id, raw_pair="09/27", raw_end="27", title="c3")

        result = dry_run(session)

        writable_ids = {r.assertion_id for r in result["writable_rows"]}
        assert writable_ids == {auto_resolvable.id}
        assert already_linked_new.id not in writable_ids


def test_isolated_apply_writes_correct_physical_designation(loose_snapshot):
    with Session(_engine()) as session:
        airport, *_ = _seed_airport_with_runway(session, name="Test Field", code="TST", pair="4L/22R")
        assertion = _seed_nasr_assertion(session, airport_id=airport.id, raw_pair="04L/22R", raw_end="04L")

        plan_result = dry_run(session)
        fingerprint = plan_result["fingerprint"]

        apply(session, expected_fingerprint=fingerprint)

        refreshed = session.get(SourceAssertion, assertion.id)
        assert refreshed.runway_end == "4L"


def test_apply_never_writes_the_reciprocal_protected_designation(loose_snapshot):
    with Session(_engine()) as session:
        airport, *_ = _seed_airport_with_runway(session, name="Test Field", code="TST", pair="4L/22R")
        assertion = _seed_nasr_assertion(session, airport_id=airport.id, raw_pair="04L/22R", raw_end="04L")

        fingerprint = dry_run(session)["fingerprint"]
        apply(session, expected_fingerprint=fingerprint)

        refreshed = session.get(SourceAssertion, assertion.id)
        assert refreshed.runway_end == "4L"
        assert refreshed.runway_end != "22R"


def test_review_required_assertion_remains_byte_for_byte_untouched(loose_snapshot):
    with Session(_engine()) as session:
        airport, *_ = _seed_airport_with_runway(session, name="Boston-Shaped Field", code="BST", pair="4L/22R")
        source = Source(title="grant", source_type="usaspending_grant", url="https://example.test/g")
        session.add(source)
        session.flush()
        session.add(Signal(
            airport_id=airport.id, source_id=source.id, title="Phase 2", category="new_installation",
            confidence="confirmed", status="under construction",
            source_notes="Boston-Shaped Field already has EMAS in operation: Runway 22R.",
        ))
        session.commit()
        review_required = _seed_nasr_assertion(session, airport_id=airport.id, raw_pair="04L/22R", raw_end="04L")
        before = (review_required.raw_runway_end_value, review_required.runway_end, review_required.evidence_quality,
                  review_required.review_state)

        fingerprint = dry_run(session)["fingerprint"]
        apply(session, expected_fingerprint=fingerprint)

        after_assertion = session.get(SourceAssertion, review_required.id)
        after = (after_assertion.raw_runway_end_value, after_assertion.runway_end, after_assertion.evidence_quality,
                 after_assertion.review_state)
        assert before == after
        assert after_assertion.runway_end is None


def test_already_linked_assertion_and_existing_identity_remain_untouched(loose_snapshot):
    with Session(_engine()) as session:
        airport, runway, end_6, end_24 = _seed_airport_with_runway(session, name="MDW-Shaped", code="MDS", pair="6/24")
        identity = PhysicalInstallationIdentity(airport_id=airport.id, runway_id=runway.id, runway_end_id=end_6.id)
        session.add(identity)
        session.flush()
        prior_source = Source(title="prior", source_type="faa_nasr_apt_ars", url="https://example.test/p")
        session.add(prior_source)
        session.flush()
        prior_assertion = SourceAssertion(
            source_id=prior_source.id, airport_id=airport.id, assertion_type="runway_end",
            raw_runway_end_value="06", source_record_identifier="prior-1",
            evidence_quality="direct_strong", review_state="reviewed",
        )
        session.add(prior_assertion)
        session.flush()
        link = InstallationAssertionLink(
            assertion_id=prior_assertion.id, physical_installation_id=identity.id,
            outcome="SAME_PHYSICAL_INSTALLATION", reason="test", actor="human:test",
        )
        session.add(link)
        session.commit()
        already_linked = _seed_nasr_assertion(session, airport_id=airport.id, raw_pair="06/24", raw_end="06", title="c2")

        fingerprint = dry_run(session)["fingerprint"]
        apply(session, expected_fingerprint=fingerprint)

        assert session.get(SourceAssertion, already_linked.id).runway_end is None
        assert session.get(PhysicalInstallationIdentity, identity.id).runway_end_id == end_6.id
        assert len(session.scalars(select(PhysicalInstallationIdentity)).all()) == 1
        assert len(session.scalars(select(InstallationAssertionLink)).all()) == 1


def test_snapshot_drift_fails_closed():
    """Without the loose_snapshot fixture, the real, frozen EXPECTED_SNAPSHOT
    (115/97/9/9/0/0/0) is enforced - any smaller fixture population must be
    rejected as drift, never silently accepted."""
    with Session(_engine()) as session:
        airport, *_ = _seed_airport_with_runway(session, name="Test Field", code="TST", pair="9/27")
        _seed_nasr_assertion(session, airport_id=airport.id, raw_pair="09/27", raw_end="09")

        with pytest.raises(PromotionGuardError, match="snapshot drift"):
            dry_run(session)


def test_fingerprint_drift_fails_closed_even_with_same_count(loose_snapshot):
    with Session(_engine()) as session:
        airport, *_ = _seed_airport_with_runway(session, name="Test Field", code="TST", pair="9/27")
        _seed_nasr_assertion(session, airport_id=airport.id, raw_pair="09/27", raw_end="09")
        stale_fingerprint = dry_run(session)["fingerprint"]

    with Session(_engine()) as session:
        # A structurally different (but same-count) population - a
        # different airport/assertion entirely - must not silently pass
        # under a fingerprint approved for the FIRST population.
        airport, *_ = _seed_airport_with_runway(session, name="Different Field", code="DIF", pair="9/27")
        _seed_nasr_assertion(session, airport_id=airport.id, raw_pair="09/27", raw_end="27")

        with pytest.raises(PromotionGuardError, match="Fingerprint drift"):
            apply(session, expected_fingerprint=stale_fingerprint)


def test_missing_raw_value_fails_closed(loose_snapshot):
    with Session(_engine()) as session:
        airport, *_ = _seed_airport_with_runway(session, name="Test Field", code="TST", pair="9/27")
        source = Source(title="broken", source_type="faa_nasr_apt_ars", url="https://example.test/b")
        session.add(source)
        session.flush()
        session.add(SourceAssertion(
            source_id=source.id, airport_id=airport.id, assertion_type="runway_end",
            raw_runway_value="9/27", raw_runway_end_value=None, source_record_identifier="broken-1",
            evidence_quality="direct_strong", review_state="unreviewed",
        ))
        session.commit()

        result = dry_run(session)
        # No raw value -> classifier itself returns INSUFFICIENT_EVIDENCE,
        # never reaches the writer's own writable set at all.
        assert result["writable_count"] == 0


def test_existing_non_null_runway_end_is_not_overwritten_and_counts_as_already_promoted(loose_snapshot):
    with Session(_engine()) as session:
        airport, *_ = _seed_airport_with_runway(session, name="Test Field", code="TST", pair="9/27")
        assertion = _seed_nasr_assertion(session, airport_id=airport.id, raw_pair="09/27", raw_end="09", runway_end="9")

        result = dry_run(session)

        assert result["writable_count"] == 0
        assert result["already_promoted_count"] == 1
        assert result["already_promoted_rows"][0].assertion_id == assertion.id


def test_drift_between_raw_value_and_existing_runway_end_fails_closed(loose_snapshot):
    with Session(_engine()) as session:
        airport, *_ = _seed_airport_with_runway(session, name="Test Field", code="TST", pair="9/27")
        _seed_nasr_assertion(session, airport_id=airport.id, raw_pair="09/27", raw_end="09", runway_end="27")

        with pytest.raises(PromotionGuardError, match="Drift detected"):
            dry_run(session)


def test_apply_requires_both_cli_write_flags(tmp_path, monkeypatch, loose_snapshot):
    """--apply without --allow-database-write (and vice versa for the write
    path) must never reach backup_database()/a write. --allow-database-write
    alone, with no --apply, is a no-op dry run by design (it only widens
    what --apply would be allowed to do). Routed at an isolated, real
    temp-file DB via --database (never the real repository database) - see
    test_backup_happens_before_mutation_in_isolated_apply's docstring for
    why an isolated file, not an in-memory engine or a SessionLocal
    monkeypatch, is required here: main() builds its own engine directly
    from --database (docs/domain/nasr-emas-auto-resolvable-promotion-dry-run.md
    §incident) and no longer reads app.database.SessionLocal at all."""
    import scripts.promote_nasr_emas_runway_end_assertions as writer
    from sqlalchemy import create_engine as _ce

    called = {"backup": False}
    monkeypatch.setattr(writer, "backup_database", lambda *a, **k: called.__setitem__("backup", True))

    with pytest.raises(SystemExit):
        writer.main(["--apply"])
    assert called["backup"] is False

    db_path = tmp_path / "isolated.db"
    engine = _ce(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport, *_ = _seed_airport_with_runway(session, name="Test Field", code="TST", pair="9/27")
        _seed_nasr_assertion(session, airport_id=airport.id, raw_pair="09/27", raw_end="09")

    writer.main(["--allow-database-write", "--database", str(db_path)])  # no --apply -> dry run, not an error
    assert called["backup"] is False

    with pytest.raises(SystemExit):
        writer.main(["--apply", "--allow-database-write", "--database", str(db_path)])  # missing --expected-fingerprint
    assert called["backup"] is False


def test_backup_happens_before_mutation_in_isolated_apply(tmp_path, monkeypatch, loose_snapshot):
    import scripts.promote_nasr_emas_runway_end_assertions as writer
    from sqlalchemy import create_engine as _ce

    db_path = tmp_path / "isolated.db"
    engine = _ce(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport, *_ = _seed_airport_with_runway(session, name="Test Field", code="TST", pair="9/27")
        _seed_nasr_assertion(session, airport_id=airport.id, raw_pair="09/27", raw_end="09")

    backup_dir = tmp_path / "backups"
    backups_created = []
    real_backup = writer.backup_database

    def _tracking_backup(database=writer.DEFAULT_DATABASE, backup_directory=writer.BACKUP_DIRECTORY):
        path = real_backup(database=database, backup_directory=backup_dir)
        backups_created.append(path)
        return path

    monkeypatch.setattr(writer, "backup_database", _tracking_backup)

    with Session(engine) as session:
        fingerprint = dry_run(session)["fingerprint"]

    writer.main(["--apply", "--allow-database-write", "--database", str(db_path), "--expected-fingerprint", fingerprint])

    assert len(backups_created) == 1
    assert backups_created[0].parent == backup_dir
    backup_content_at_creation = backups_created[0].read_bytes()
    # The backup must reflect PRE-write state: re-reading the (now-written)
    # live db must differ from the backup's own bytes (the write happened
    # after the backup was taken).
    assert backup_content_at_creation != db_path.read_bytes()


def test_idempotent_rerun_after_successful_isolated_apply_produces_zero_further_writes(loose_snapshot):
    with Session(_engine()) as session:
        airport, *_ = _seed_airport_with_runway(session, name="Test Field", code="TST", pair="9/27")
        _seed_nasr_assertion(session, airport_id=airport.id, raw_pair="09/27", raw_end="09")

        fingerprint = dry_run(session)["fingerprint"]
        apply(session, expected_fingerprint=fingerprint)

        second = dry_run(session)
        assert second["writable_count"] == 0
        assert second["already_promoted_count"] == 1


def test_apply_creates_no_physical_installation_identity_or_link(loose_snapshot):
    with Session(_engine()) as session:
        airport, *_ = _seed_airport_with_runway(session, name="Test Field", code="TST", pair="9/27")
        _seed_nasr_assertion(session, airport_id=airport.id, raw_pair="09/27", raw_end="09")

        fingerprint = dry_run(session)["fingerprint"]
        apply(session, expected_fingerprint=fingerprint)

        assert session.scalars(select(PhysicalInstallationIdentity)).all() == []
        assert session.scalars(select(InstallationAssertionLink)).all() == []


def test_apply_leaves_runway_and_runway_end_rows_unchanged(loose_snapshot):
    with Session(_engine()) as session:
        airport, runway, end_9, end_27 = _seed_airport_with_runway(session, name="Test Field", code="TST", pair="9/27")
        before_runways = {(r.id, r.designation) for r in session.scalars(select(Runway)).all()}
        before_ends = {(e.id, e.designation) for e in session.scalars(select(RunwayEnd)).all()}
        _seed_nasr_assertion(session, airport_id=airport.id, raw_pair="09/27", raw_end="09")

        fingerprint = dry_run(session)["fingerprint"]
        apply(session, expected_fingerprint=fingerprint)

        after_runways = {(r.id, r.designation) for r in session.scalars(select(Runway)).all()}
        after_ends = {(e.id, e.designation) for e in session.scalars(select(RunwayEnd)).all()}
        assert after_runways == before_runways
        assert after_ends == before_ends


def test_apply_leaves_raw_evidence_fields_unchanged(loose_snapshot):
    with Session(_engine()) as session:
        airport, *_ = _seed_airport_with_runway(session, name="Test Field", code="TST", pair="9/27")
        assertion = _seed_nasr_assertion(session, airport_id=airport.id, raw_pair="09/27", raw_end="09")
        before = (
            assertion.raw_runway_end_value, assertion.raw_runway_value, assertion.evidence_quality,
            assertion.review_state, assertion.airport_id, assertion.source_id, assertion.assertion_type,
        )

        fingerprint = dry_run(session)["fingerprint"]
        apply(session, expected_fingerprint=fingerprint)

        refreshed = session.get(SourceAssertion, assertion.id)
        after = (
            refreshed.raw_runway_end_value, refreshed.raw_runway_value, refreshed.evidence_quality,
            refreshed.review_state, refreshed.airport_id, refreshed.source_id, refreshed.assertion_type,
        )
        assert before == after


# ---------------------------------------------------------------------------
# Wrong-database isolation regression suite
# (docs/domain/nasr-emas-auto-resolvable-promotion-dry-run.md incident).
#
# The real incident this task's final safety review responds to: main()
# built its backup from the correct --database path but ran the actual
# read/write session through the shared, import-time-bound
# app.database.SessionLocal, silently ignoring --database entirely and
# writing to the real database instead of the intended disposable copy.
# Every test below builds two REAL, ON-DISK, file-based temp databases -
# "protected.db" (standing in for the real database) and "target.db" (the
# intended write target) - and proves protected.db is never touched, no
# matter what the module's own DEFAULT_DATABASE points at, by invoking
# main() itself (the actual incident-prone entry point), not just the
# already-covered apply()/plan() functions directly.
# ---------------------------------------------------------------------------


def _make_seeded_db(path):
    from sqlalchemy import create_engine as _ce

    engine = _ce(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        airport, *_ = _seed_airport_with_runway(session, name="Test Field", code="TST", pair="9/27")
        _seed_nasr_assertion(session, airport_id=airport.id, raw_pair="09/27", raw_end="09")
    return path


def _promoted_count(path) -> int:
    import sqlite3

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute(
            "SELECT count(*) FROM source_assertions WHERE runway_end IS NOT NULL"
        ).fetchone()[0]
    finally:
        conn.close()


def test_apply_against_target_leaves_a_second_protected_db_byte_for_byte_unchanged(tmp_path, monkeypatch, loose_snapshot):
    """A. Two temp DBs. Apply runs against target.db only. protected.db
    (seeded identically, standing in for the real database) must remain
    byte-for-byte unchanged - proven by content hash, not just row counts,
    so even a metadata-only touch would be caught."""
    import hashlib
    import scripts.promote_nasr_emas_runway_end_assertions as writer

    protected_path = tmp_path / "protected.db"
    target_path = tmp_path / "target.db"
    _make_seeded_db(protected_path)
    _make_seeded_db(target_path)

    def _sha256(path):
        h = hashlib.sha256()
        with open(path, "rb") as f:
            h.update(f.read())
        return h.hexdigest()

    protected_hash_before = _sha256(protected_path)

    with Session(create_engine(f"sqlite:///{target_path}")) as session:
        fingerprint = dry_run(session)["fingerprint"]

    monkeypatch.setattr(writer, "backup_database", lambda *a, **k: tmp_path / "unused-backup.db")
    writer.main(["--apply", "--allow-database-write", "--database", str(target_path), "--expected-fingerprint", fingerprint])

    assert _promoted_count(target_path) == 1
    assert _sha256(protected_path) == protected_hash_before
    assert _promoted_count(protected_path) == 0


def test_explicit_target_wins_even_when_default_database_points_at_protected_db(tmp_path, monkeypatch, loose_snapshot):
    """B. Point the module's own DEFAULT_DATABASE at protected.db (simulating
    "the production DB"), while explicitly supplying --database target.db.
    The explicit target must win: target.db is written, protected.db is
    completely untouched - proving main() never silently falls back to
    DEFAULT_DATABASE (or any other implicit default) when an explicit
    --database is given, which is exactly the shape of the real incident."""
    import scripts.promote_nasr_emas_runway_end_assertions as writer

    protected_path = tmp_path / "protected.db"
    target_path = tmp_path / "target.db"
    _make_seeded_db(protected_path)
    _make_seeded_db(target_path)

    monkeypatch.setattr(writer, "DEFAULT_DATABASE", protected_path)
    monkeypatch.setattr(writer, "backup_database", lambda *a, **k: tmp_path / "unused-backup.db")

    with Session(create_engine(f"sqlite:///{target_path}")) as session:
        fingerprint = dry_run(session)["fingerprint"]

    writer.main(["--apply", "--allow-database-write", "--database", str(target_path), "--expected-fingerprint", fingerprint])

    assert _promoted_count(target_path) == 1
    assert _promoted_count(protected_path) == 0


def test_backup_corresponds_to_target_db_not_protected_db(tmp_path, monkeypatch, loose_snapshot):
    """C. The pre-write backup content must match target.db (the file
    actually about to be written), never protected.db - even when
    DEFAULT_DATABASE points at protected.db."""
    import scripts.promote_nasr_emas_runway_end_assertions as writer

    protected_path = tmp_path / "protected.db"
    target_path = tmp_path / "target.db"
    _make_seeded_db(protected_path)
    _make_seeded_db(target_path)
    monkeypatch.setattr(writer, "DEFAULT_DATABASE", protected_path)

    backup_dir = tmp_path / "backups"
    real_backup = writer.backup_database
    backups = []

    def _tracking_backup(database=writer.DEFAULT_DATABASE, backup_directory=writer.BACKUP_DIRECTORY):
        path = real_backup(database=database, backup_directory=backup_dir)
        backups.append(path)
        return path

    monkeypatch.setattr(writer, "backup_database", _tracking_backup)

    with Session(create_engine(f"sqlite:///{target_path}")) as session:
        fingerprint = dry_run(session)["fingerprint"]

    writer.main(["--apply", "--allow-database-write", "--database", str(target_path), "--expected-fingerprint", fingerprint])

    assert len(backups) == 1
    # The backup was taken from target.db BEFORE its write - it must match
    # target.db's ORIGINAL (pre-write, still-unpromoted) content, not
    # protected.db's content (both start identically seeded, so this
    # assertion alone wouldn't distinguish them - the row-count check
    # below does: only target.db's live file should show the write).
    assert _promoted_count(backups[0]) == 0
    assert _promoted_count(target_path) == 1  # live target.db now differs from its own backup, as expected
    assert _promoted_count(protected_path) == 0  # protected.db untouched throughout


def test_post_write_validation_reads_target_db(tmp_path, monkeypatch, loose_snapshot):
    """D. apply()'s own post-write verification re-reads through the same
    session it wrote with - which main() binds to target.db. A successful
    return value is itself proof the verification read target.db (had it
    somehow read protected.db instead, the unpromoted row there would have
    failed the post-write "expected exactly {...}" check and raised)."""
    import scripts.promote_nasr_emas_runway_end_assertions as writer

    protected_path = tmp_path / "protected.db"
    target_path = tmp_path / "target.db"
    _make_seeded_db(protected_path)
    _make_seeded_db(target_path)
    monkeypatch.setattr(writer, "DEFAULT_DATABASE", protected_path)
    monkeypatch.setattr(writer, "backup_database", lambda *a, **k: tmp_path / "unused-backup.db")

    with Session(create_engine(f"sqlite:///{target_path}")) as session:
        fingerprint = dry_run(session)["fingerprint"]

    # main() itself would raise PromotionGuardError if post-write
    # verification read the wrong (still-unpromoted) database - reaching
    # this line at all is the proof.
    writer.main(["--apply", "--allow-database-write", "--database", str(target_path), "--expected-fingerprint", fingerprint])
    assert _promoted_count(target_path) == 1


def test_dry_run_against_target_leaves_both_dbs_unchanged(tmp_path, monkeypatch, loose_snapshot):
    """E. Dry-run (no --apply) against target.db, with DEFAULT_DATABASE
    pointed at protected.db, must leave BOTH files completely unchanged."""
    import hashlib
    import scripts.promote_nasr_emas_runway_end_assertions as writer

    protected_path = tmp_path / "protected.db"
    target_path = tmp_path / "target.db"
    _make_seeded_db(protected_path)
    _make_seeded_db(target_path)
    monkeypatch.setattr(writer, "DEFAULT_DATABASE", protected_path)

    def _sha256(path):
        h = hashlib.sha256()
        with open(path, "rb") as f:
            h.update(f.read())
        return h.hexdigest()

    protected_before, target_before = _sha256(protected_path), _sha256(target_path)
    called = {"backup": False}
    monkeypatch.setattr(writer, "backup_database", lambda *a, **k: called.__setitem__("backup", True))

    writer.main(["--database", str(target_path)])  # no --apply

    assert called["backup"] is False
    assert _sha256(protected_path) == protected_before
    assert _sha256(target_path) == target_before


def test_inconsistent_fingerprint_fails_closed_without_writing_either_db(tmp_path, monkeypatch, loose_snapshot):
    """F. A deliberately wrong/stale --expected-fingerprint (the kind of
    human/procedural error that could accompany a mismatched session/path
    combination) must fail closed before any write, on target.db AND
    leave protected.db untouched - never a partial or misdirected write."""
    import scripts.promote_nasr_emas_runway_end_assertions as writer

    protected_path = tmp_path / "protected.db"
    target_path = tmp_path / "target.db"
    _make_seeded_db(protected_path)
    _make_seeded_db(target_path)
    monkeypatch.setattr(writer, "DEFAULT_DATABASE", protected_path)
    called = {"backup": False}
    monkeypatch.setattr(writer, "backup_database", lambda *a, **k: called.__setitem__("backup", True))

    from scripts.promote_nasr_emas_runway_end_assertions import PromotionGuardError as _PGE

    with pytest.raises(_PGE, match="Fingerprint drift"):
        writer.main([
            "--apply", "--allow-database-write", "--database", str(target_path),
            "--expected-fingerprint", "0" * 64,
        ])

    assert _promoted_count(target_path) == 0
    assert _promoted_count(protected_path) == 0
    assert called["backup"] is False  # validated BEFORE backup, per main()'s ordering


def test_malformed_fingerprint_format_fails_before_backup(tmp_path, monkeypatch, loose_snapshot):
    """An --expected-fingerprint that isn't even a plausible SHA-256 hex
    string must still fail closed cleanly (as a plain mismatch, not a
    crash) and never reach backup_database()."""
    import scripts.promote_nasr_emas_runway_end_assertions as writer
    from scripts.promote_nasr_emas_runway_end_assertions import PromotionGuardError as _PGE

    target_path = tmp_path / "target.db"
    _make_seeded_db(target_path)
    called = {"backup": False}
    monkeypatch.setattr(writer, "backup_database", lambda *a, **k: called.__setitem__("backup", True))

    with pytest.raises(_PGE, match="Fingerprint drift"):
        writer.main([
            "--apply", "--allow-database-write", "--database", str(target_path),
            "--expected-fingerprint", "not-a-real-fingerprint",
        ])

    assert called["backup"] is False
    assert _promoted_count(target_path) == 0


def test_classifier_snapshot_drift_fails_before_backup_via_main(tmp_path, monkeypatch):
    """A target.db shaped nothing like the approved 115/97/9/9/0/0/0
    snapshot (the strict, non-relaxed EXPECTED_SNAPSHOT - no loose_snapshot
    fixture here) must fail closed on classifier drift and never reach
    backup_database(), proving count/classification drift is caught before
    any backup or write - not merely that the write itself is refused."""
    import scripts.promote_nasr_emas_runway_end_assertions as writer
    from scripts.promote_nasr_emas_runway_end_assertions import PromotionGuardError as _PGE

    target_path = tmp_path / "target.db"
    _make_seeded_db(target_path)  # a 1-assertion population, nowhere near 115
    called = {"backup": False}
    monkeypatch.setattr(writer, "backup_database", lambda *a, **k: called.__setitem__("backup", True))

    with pytest.raises(_PGE, match="snapshot drift"):
        writer.main([
            "--apply", "--allow-database-write", "--database", str(target_path),
            "--expected-fingerprint", "05d76227c3fe863c30aa8adbcbaeb8a92590e5f8f687ca4a103b3b59f7d38d42",
        ])

    assert called["backup"] is False
    assert _promoted_count(target_path) == 0
