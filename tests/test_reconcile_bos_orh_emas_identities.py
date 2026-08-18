"""Isolated tests for scripts/reconcile_bos_orh_emas_identities.py
(docs/domain/bos-orh-emas-reconciliation-dry-run.md).

Never touches the real development database - builds isolated in-memory
or temp-file databases per test. NEVER points a real --apply at
data/runway_safe.db."""
import hashlib
import sqlite3

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
    Source,
    SourceAssertion,
)
from scripts.reconcile_bos_orh_emas_identities import (
    ALREADY_RECONCILED,
    WRITABLE,
    ReconciliationGuardError,
    apply,
    dry_run,
    plan,
)


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _seed_airport_with_runways(session, *, name, code, pairs):
    airport = Airport(name=name, faa_code=code, country="USA")
    session.add(airport)
    session.flush()
    ends = {}
    for pair in pairs:
        runway = Runway(airport_id=airport.id, designation=pair)
        session.add(runway)
        session.flush()
        a, b = pair.split("/")
        ra = RunwayEnd(runway_id=runway.id, designation=a)
        rb = RunwayEnd(runway_id=runway.id, designation=b)
        session.add_all([ra, rb])
        session.flush()
        ends[a], ends[b] = ra, rb
    return airport, ends


def _seed_assertion(session, *, id, airport_id, raw_pair, raw_end, evidence_quality="direct_strong",
                     assertion_type="runway_end", runway_end=None, title=None):
    source = Source(
        title=title or f"NASR test cycle {id}", source_type="faa_nasr_apt_ars", url="https://example.test/nasr",
        external_id=f"faa_nasr:airport_csv:test:{id}",
    )
    session.add(source)
    session.flush()
    assertion = SourceAssertion(
        id=id, source_id=source.id, airport_id=airport_id, assertion_type=assertion_type,
        raw_airport_identifier="TST", raw_runway_value=raw_pair, raw_runway_end_value=raw_end,
        runway_end=runway_end, raw_relevant_text="{}", source_record_identifier=f"test:{id}",
        evidence_quality=evidence_quality, review_state="unreviewed",
    )
    session.add(assertion)
    session.commit()
    return assertion


def _seed_full_fixture(session):
    """Exactly the 4-row BOS/ORH target shape, matching
    docs/domain/bos-orh-emas-reconciliation-investigation.md S16."""
    bos, bos_ends = _seed_airport_with_runways(session, name="Test Logan", code="BOS", pairs=["4L/22R", "15R/33L"])
    orh, orh_ends = _seed_airport_with_runways(session, name="Test Worcester", code="ORH", pairs=["11/29"])
    a161 = _seed_assertion(session, id=161, airport_id=bos.id, raw_pair="04L/22R", raw_end="04L")
    a162 = _seed_assertion(session, id=162, airport_id=bos.id, raw_pair="15R/33L", raw_end="15R")
    a164 = _seed_assertion(session, id=164, airport_id=orh.id, raw_pair="11/29", raw_end="11")
    a165 = _seed_assertion(session, id=165, airport_id=orh.id, raw_pair="11/29", raw_end="29")
    return {
        "bos": bos, "orh": orh, "bos_ends": bos_ends, "orh_ends": orh_ends,
        "a161": a161, "a162": a162, "a164": a164, "a165": a165,
    }


def _link_and_identity_for(session, assertion_id):
    link = session.scalar(select(InstallationAssertionLink).where(InstallationAssertionLink.assertion_id == assertion_id))
    identity = session.get(PhysicalInstallationIdentity, link.physical_installation_id) if link else None
    return link, identity


# ---------------------------------------------------------------------------
# 1-2: dry-run behavior
# ---------------------------------------------------------------------------

def test_dry_run_default_performs_zero_writes():
    with Session(_engine()) as session:
        _seed_full_fixture(session)

        result = dry_run(session)

        assert len(session.new) == 0 and len(session.dirty) == 0 and len(session.deleted) == 0
        assert session.scalars(select(PhysicalInstallationIdentity)).all() == []
        assert session.scalars(select(InstallationAssertionLink)).all() == []
        assert result["writable_count"] == 4


def test_dry_run_produces_exactly_four_row_plan():
    with Session(_engine()) as session:
        _seed_full_fixture(session)

        result = dry_run(session)

        assert {r.assertion_id for r in result["writable_rows"]} == {161, 162, 164, 165}
        assert result["already_reconciled_count"] == 0


# ---------------------------------------------------------------------------
# 3-5: isolated apply creates exactly the right rows
# ---------------------------------------------------------------------------

def test_isolated_apply_creates_exactly_four_identities():
    with Session(_engine()) as session:
        _seed_full_fixture(session)
        fingerprint = dry_run(session)["fingerprint"]

        result = apply(session, expected_fingerprint=fingerprint)

        assert result["identities_created"] == 4
        assert len(session.scalars(select(PhysicalInstallationIdentity)).all()) == 4


def test_isolated_apply_creates_exactly_four_links_with_same_physical_outcome():
    with Session(_engine()) as session:
        _seed_full_fixture(session)
        fingerprint = dry_run(session)["fingerprint"]

        result = apply(session, expected_fingerprint=fingerprint)

        assert result["links_created"] == 4
        links = session.scalars(select(InstallationAssertionLink)).all()
        assert len(links) == 4
        assert {link.outcome for link in links} == {"SAME_PHYSICAL_INSTALLATION"}
        assert {link.assertion_id for link in links} == {161, 162, 164, 165}


def test_apply_never_writes_source_assertion_runway_end():
    with Session(_engine()) as session:
        fixture = _seed_full_fixture(session)
        fingerprint = dry_run(session)["fingerprint"]

        apply(session, expected_fingerprint=fingerprint)

        for assertion_id in (161, 162, 164, 165):
            assert session.get(SourceAssertion, assertion_id).runway_end is None


# ---------------------------------------------------------------------------
# 6-9: exact per-airport physical -> protected mappings
# ---------------------------------------------------------------------------

def test_bos_04l_maps_to_protected_22r():
    with Session(_engine()) as session:
        fixture = _seed_full_fixture(session)
        fingerprint = dry_run(session)["fingerprint"]
        apply(session, expected_fingerprint=fingerprint)

        link, identity = _link_and_identity_for(session, 161)
        assert identity.runway_end == "04L"
        assert identity.runway_end_id == fixture["bos_ends"]["4L"].id
        assert identity.runway_id == fixture["bos_ends"]["4L"].runway_id
        canonical_end = session.get(RunwayEnd, identity.runway_end_id)
        siblings = [e for e in canonical_end.runway.runway_ends if e.id != canonical_end.id]
        assert siblings[0].designation == "22R"


def test_bos_15r_maps_to_protected_33l():
    with Session(_engine()) as session:
        fixture = _seed_full_fixture(session)
        fingerprint = dry_run(session)["fingerprint"]
        apply(session, expected_fingerprint=fingerprint)

        link, identity = _link_and_identity_for(session, 162)
        assert identity.runway_end == "15R"
        assert identity.runway_end_id == fixture["bos_ends"]["15R"].id
        canonical_end = session.get(RunwayEnd, identity.runway_end_id)
        siblings = [e for e in canonical_end.runway.runway_ends if e.id != canonical_end.id]
        assert siblings[0].designation == "33L"


def test_orh_11_maps_to_protected_29():
    with Session(_engine()) as session:
        fixture = _seed_full_fixture(session)
        fingerprint = dry_run(session)["fingerprint"]
        apply(session, expected_fingerprint=fingerprint)

        link, identity = _link_and_identity_for(session, 164)
        assert identity.runway_end == "11"
        assert identity.runway_end_id == fixture["orh_ends"]["11"].id
        canonical_end = session.get(RunwayEnd, identity.runway_end_id)
        siblings = [e for e in canonical_end.runway.runway_ends if e.id != canonical_end.id]
        assert siblings[0].designation == "29"


def test_orh_29_maps_to_protected_11():
    with Session(_engine()) as session:
        fixture = _seed_full_fixture(session)
        fingerprint = dry_run(session)["fingerprint"]
        apply(session, expected_fingerprint=fingerprint)

        link, identity = _link_and_identity_for(session, 165)
        assert identity.runway_end == "29"
        assert identity.runway_end_id == fixture["orh_ends"]["29"].id
        canonical_end = session.get(RunwayEnd, identity.runway_end_id)
        siblings = [e for e in canonical_end.runway.runway_ends if e.id != canonical_end.id]
        assert siblings[0].designation == "11"


# ---------------------------------------------------------------------------
# 10-11: topology, not designation arithmetic
# ---------------------------------------------------------------------------

def test_protected_direction_uses_topology_not_suffix_preserving_arithmetic():
    """A naive 'add 18 to the number, keep the same L/R/C suffix' formula
    would get BOS wrong: 04L + 18 = 22, suffix-preserving arithmetic would
    guess '22L', but the real reciprocal is '22R' (a suffix flip). This is
    exactly what _protected_direction() must get right via a pure
    Runway<->RunwayEnd relationship lookup, never string/number math."""
    from scripts.reconcile_bos_orh_emas_identities import _protected_direction

    with Session(_engine()) as session:
        airport, ends = _seed_airport_with_runways(session, name="Test Field", code="TST", pairs=["4L/22R"])

        protected = _protected_direction(ends["4L"])

        assert protected == "22R"
        assert protected != "22L"  # what naive suffix-preserving arithmetic would produce


def test_protected_direction_handles_asymmetric_suffix_pair_via_topology():
    """A pair where one end has a suffix and the other does not (e.g. a
    real 6L/24-shaped runway elsewhere in the dataset) - arithmetic on the
    numeric part alone cannot produce a correct suffixed/unsuffixed
    answer; only a topology lookup can."""
    from scripts.reconcile_bos_orh_emas_identities import _protected_direction

    with Session(_engine()) as session:
        airport, ends = _seed_airport_with_runways(session, name="Test Field", code="TST", pairs=["6L/24"])

        assert _protected_direction(ends["6L"]) == "24"
        assert _protected_direction(ends["24"]) == "6L"


# ---------------------------------------------------------------------------
# 12-13: strict scope - MDW/CGF and BGM/LEX/ELM must be structurally unreachable
# ---------------------------------------------------------------------------

def test_mdw_cgf_shaped_identities_and_links_untouched():
    with Session(_engine()) as session:
        fixture = _seed_full_fixture(session)
        mdw, mdw_ends = _seed_airport_with_runways(session, name="Test Midway", code="MDW", pairs=["4R/22L"])
        prior_assertion = _seed_assertion(session, id=145, airport_id=mdw.id, raw_pair="4R/22L", raw_end="04R", title="mdw-prior")
        mdw_identity = PhysicalInstallationIdentity(
            airport_id=mdw.id, runway_id=mdw_ends["4R"].runway_id, runway_end="04R", runway_end_id=mdw_ends["4R"].id,
        )
        session.add(mdw_identity)
        session.flush()
        session.add(InstallationAssertionLink(
            assertion_id=prior_assertion.id, physical_installation_id=mdw_identity.id,
            outcome="SAME_PHYSICAL_INSTALLATION", reason="pre-existing MDW reviewed row", actor="human:rwi-owner",
        ))
        session.commit()
        mdw_identity_before = (mdw_identity.id, mdw_identity.airport_id, mdw_identity.runway_end, mdw_identity.runway_end_id)

        fingerprint = dry_run(session)["fingerprint"]
        apply(session, expected_fingerprint=fingerprint)

        refreshed = session.get(PhysicalInstallationIdentity, mdw_identity.id)
        assert (refreshed.id, refreshed.airport_id, refreshed.runway_end, refreshed.runway_end_id) == mdw_identity_before
        assert len(session.scalars(select(InstallationAssertionLink).where(InstallationAssertionLink.assertion_id == 145)).all()) == 1
        # Exactly 5 identities total: the pre-existing MDW one + the 4 new BOS/ORH ones.
        assert len(session.scalars(select(PhysicalInstallationIdentity)).all()) == 5


def test_bgm_lex_elm_shaped_assertions_untouched():
    with Session(_engine()) as session:
        fixture = _seed_full_fixture(session)
        bgm, _ = _seed_airport_with_runways(session, name="Test Binghamton", code="BGM", pairs=["16/34"])
        lex, _ = _seed_airport_with_runways(session, name="Test Blue Grass", code="LEX", pairs=["4/22"])
        elm, _ = _seed_airport_with_runways(session, name="Test Elmira", code="ELM", pairs=["6/24"])
        _seed_assertion(session, id=181, airport_id=bgm.id, raw_pair="16/34", raw_end="16", title="bgm-1")
        _seed_assertion(session, id=182, airport_id=bgm.id, raw_pair="16/34", raw_end="34", title="bgm-2")
        _seed_assertion(session, id=153, airport_id=lex.id, raw_pair="4/22", raw_end="4", title="lex-1")
        _seed_assertion(session, id=154, airport_id=lex.id, raw_pair="4/22", raw_end="22", title="lex-2")
        _seed_assertion(session, id=183, airport_id=elm.id, raw_pair="6/24", raw_end="6", title="elm-1")

        fingerprint = dry_run(session)["fingerprint"]
        apply(session, expected_fingerprint=fingerprint)

        for assertion_id in (181, 182, 153, 154, 183):
            assert session.get(SourceAssertion, assertion_id).runway_end is None
            assert session.scalar(
                select(InstallationAssertionLink).where(InstallationAssertionLink.assertion_id == assertion_id)
            ) is None
        # Exactly 4 identities total - none for BGM/LEX/ELM.
        identities = session.scalars(select(PhysicalInstallationIdentity)).all()
        assert len(identities) == 4
        assert {i.airport_id for i in identities} == {fixture["bos"].id, fixture["orh"].id}


# ---------------------------------------------------------------------------
# 14-19: fail-closed preconditions and guards
# ---------------------------------------------------------------------------

def test_duplicate_identity_at_same_physical_end_fails_closed():
    with Session(_engine()) as session:
        fixture = _seed_full_fixture(session)
        # A pre-existing identity at BOS 04L with no matching link - not the
        # clean WRITABLE or ALREADY_RECONCILED shape this writer accepts.
        rogue_identity = PhysicalInstallationIdentity(
            airport_id=fixture["bos"].id, runway_id=fixture["bos_ends"]["4L"].runway_id,
            runway_end="04L", runway_end_id=fixture["bos_ends"]["4L"].id,
        )
        session.add(rogue_identity)
        session.commit()

        with pytest.raises(ReconciliationGuardError, match="does not match a clean WRITABLE"):
            dry_run(session)


def test_existing_matching_link_and_identity_is_a_no_op():
    with Session(_engine()) as session:
        fixture = _seed_full_fixture(session)
        identity = PhysicalInstallationIdentity(
            airport_id=fixture["bos"].id, runway_id=fixture["bos_ends"]["4L"].runway_id,
            runway_end="04L", runway_end_id=fixture["bos_ends"]["4L"].id,
        )
        session.add(identity)
        session.flush()
        session.add(InstallationAssertionLink(
            assertion_id=161, physical_installation_id=identity.id,
            outcome="SAME_PHYSICAL_INSTALLATION", reason="already reconciled", actor="human:rwi-owner",
        ))
        session.commit()

        result = dry_run(session)

        assert result["writable_count"] == 3
        assert result["already_reconciled_count"] == 1
        assert 161 in {r.assertion_id for r in result["already_reconciled_rows"]}


def test_existing_link_without_matching_identity_fails_closed():
    with Session(_engine()) as session:
        fixture = _seed_full_fixture(session)
        # A link pointing at an identity for a DIFFERENT physical end - a
        # real conflict, never silently treated as already-reconciled.
        other_identity = PhysicalInstallationIdentity(
            airport_id=fixture["bos"].id, runway_id=fixture["bos_ends"]["15R"].runway_id,
            runway_end="15R", runway_end_id=fixture["bos_ends"]["15R"].id,
        )
        session.add(other_identity)
        session.flush()
        session.add(InstallationAssertionLink(
            assertion_id=161, physical_installation_id=other_identity.id,
            outcome="SAME_PHYSICAL_INSTALLATION", reason="mismatched prior link", actor="human:rwi-owner",
        ))
        session.commit()

        with pytest.raises(ReconciliationGuardError, match="does not match a clean WRITABLE"):
            dry_run(session)


def test_evidence_quality_drift_fails_closed():
    with Session(_engine()) as session:
        bos, bos_ends = _seed_airport_with_runways(session, name="Test Logan", code="BOS", pairs=["4L/22R", "15R/33L"])
        orh, orh_ends = _seed_airport_with_runways(session, name="Test Worcester", code="ORH", pairs=["11/29"])
        _seed_assertion(session, id=161, airport_id=bos.id, raw_pair="04L/22R", raw_end="04L", evidence_quality="corroborated")
        _seed_assertion(session, id=162, airport_id=bos.id, raw_pair="15R/33L", raw_end="15R")
        _seed_assertion(session, id=164, airport_id=orh.id, raw_pair="11/29", raw_end="11")
        _seed_assertion(session, id=165, airport_id=orh.id, raw_pair="11/29", raw_end="29")

        with pytest.raises(ReconciliationGuardError, match="evidence_quality"):
            dry_run(session)


def test_topology_drift_fails_closed():
    """If the canonical Runway's reciprocal end doesn't match the
    investigation-approved protected direction (e.g. real-world topology
    somehow differs from what was reviewed), the writer must refuse, not
    silently use whatever it finds."""
    with Session(_engine()) as session:
        bos, bos_ends = _seed_airport_with_runways(session, name="Test Logan", code="BOS", pairs=["4L/20R", "15R/33L"])
        orh, orh_ends = _seed_airport_with_runways(session, name="Test Worcester", code="ORH", pairs=["11/29"])
        _seed_assertion(session, id=161, airport_id=bos.id, raw_pair="04L/20R", raw_end="04L")
        _seed_assertion(session, id=162, airport_id=bos.id, raw_pair="15R/33L", raw_end="15R")
        _seed_assertion(session, id=164, airport_id=orh.id, raw_pair="11/29", raw_end="11")
        _seed_assertion(session, id=165, airport_id=orh.id, raw_pair="11/29", raw_end="29")

        with pytest.raises(ReconciliationGuardError, match="derived protected direction"):
            dry_run(session)


def test_snapshot_drift_fails_closed_when_expected_snapshot_disagrees_with_target_rows(monkeypatch):
    import scripts.reconcile_bos_orh_emas_identities as writer

    monkeypatch.setattr(writer, "EXPECTED_SNAPSHOT", (("bogus",),))

    with Session(_engine()) as session:
        _seed_full_fixture(session)

        with pytest.raises(ReconciliationGuardError, match="Snapshot drift"):
            dry_run(session)


def test_fingerprint_drift_fails_closed():
    with Session(_engine()) as session:
        _seed_full_fixture(session)

        with pytest.raises(ReconciliationGuardError, match="Fingerprint drift"):
            apply(session, expected_fingerprint="0" * 64)


# ---------------------------------------------------------------------------
# 20-22: CLI flag gating
# ---------------------------------------------------------------------------

def test_apply_requires_both_cli_write_flags_and_fingerprint(tmp_path, monkeypatch):
    import scripts.reconcile_bos_orh_emas_identities as writer
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
        _seed_full_fixture(session)

    writer.main(["--allow-database-write", "--database", str(db_path)])  # no --apply -> dry run, not an error
    assert called["backup"] is False

    with pytest.raises(SystemExit):
        writer.main(["--apply", "--allow-database-write", "--database", str(db_path)])  # missing --expected-fingerprint
    assert called["backup"] is False


# ---------------------------------------------------------------------------
# 23: failed post-write verification rolls back (never partially commits)
# ---------------------------------------------------------------------------

def test_failed_post_write_verification_never_commits(monkeypatch):
    import scripts.reconcile_bos_orh_emas_identities as writer

    def _broken_create(session, *, airport_id, runway_id, runway_end, runway_end_id):
        # Deliberately creates an identity at the WRONG runway_end - the
        # post-write verification loop must catch this mismatch and raise
        # before any commit.
        from app.services.physical_installation_reconciliation import create_physical_installation_identity as real
        return real(session, airport_id=airport_id, runway_id=runway_id, runway_end="WRONG-VALUE", runway_end_id=runway_end_id)

    monkeypatch.setattr(writer, "create_physical_installation_identity", _broken_create)

    with Session(_engine()) as session:
        _seed_full_fixture(session)
        fingerprint = dry_run(session)["fingerprint"]

        with pytest.raises(ReconciliationGuardError, match="Post-write verification failed"):
            apply(session, expected_fingerprint=fingerprint)

        session.rollback()
        assert session.scalars(select(PhysicalInstallationIdentity)).all() == []
        assert session.scalars(select(InstallationAssertionLink)).all() == []


# ---------------------------------------------------------------------------
# 24: idempotent rerun
# ---------------------------------------------------------------------------

def test_idempotent_rerun_after_successful_isolated_apply_produces_zero_further_creates():
    with Session(_engine()) as session:
        _seed_full_fixture(session)
        fingerprint = dry_run(session)["fingerprint"]
        first = apply(session, expected_fingerprint=fingerprint)
        assert first["identities_created"] == 4

        second_plan = dry_run(session)
        assert second_plan["writable_count"] == 0
        assert second_plan["already_reconciled_count"] == 4

        second_fingerprint = second_plan["fingerprint"]  # empty-writable-set fingerprint
        second_apply = apply(session, expected_fingerprint=second_fingerprint)
        assert second_apply["identities_created"] == 0
        assert second_apply["links_created"] == 0
        assert len(session.scalars(select(PhysicalInstallationIdentity)).all()) == 4
        assert len(session.scalars(select(InstallationAssertionLink)).all()) == 4


# ---------------------------------------------------------------------------
# 25: no unrelated tables change
# ---------------------------------------------------------------------------

def test_no_unrelated_tables_change():
    with Session(_engine()) as session:
        fixture = _seed_full_fixture(session)
        before_airports = {(a.id, a.name, a.faa_code) for a in session.scalars(select(Airport)).all()}
        before_runways = {(r.id, r.designation, r.airport_id) for r in session.scalars(select(Runway)).all()}
        before_ends = {(e.id, e.designation, e.runway_id) for e in session.scalars(select(RunwayEnd)).all()}
        before_sources = {(s.id, s.external_id) for s in session.scalars(select(Source)).all()}
        before_assertions = {
            (a.id, a.raw_runway_end_value, a.runway_end, a.evidence_quality, a.review_state)
            for a in session.scalars(select(SourceAssertion)).all()
        }

        fingerprint = dry_run(session)["fingerprint"]
        apply(session, expected_fingerprint=fingerprint)

        assert {(a.id, a.name, a.faa_code) for a in session.scalars(select(Airport)).all()} == before_airports
        assert {(r.id, r.designation, r.airport_id) for r in session.scalars(select(Runway)).all()} == before_runways
        assert {(e.id, e.designation, e.runway_id) for e in session.scalars(select(RunwayEnd)).all()} == before_ends
        assert {(s.id, s.external_id) for s in session.scalars(select(Source)).all()} == before_sources
        assert {
            (a.id, a.raw_runway_end_value, a.runway_end, a.evidence_quality, a.review_state)
            for a in session.scalars(select(SourceAssertion)).all()
        } == before_assertions


# ---------------------------------------------------------------------------
# Database-target isolation suite (A-H) - the same wrong-DB incident class
# documented in docs/domain/nasr-emas-auto-resolvable-promotion-dry-run.md.
# Two REAL, on-disk temp databases - "protected.db" (stands in for the real
# database) and "target.db" (the intended write target) - proving
# protected.db is never touched no matter what the module's own
# DEFAULT_DATABASE, or even app.database.SessionLocal, point at. Exercises
# the actual main() CLI entry point, not just apply()/plan() directly.
# ---------------------------------------------------------------------------


def _make_seeded_db(path):
    from sqlalchemy import create_engine as _ce

    engine = _ce(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _seed_full_fixture(session)
    return path


def _identity_count(path) -> int:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT count(*) FROM physical_installation_identities").fetchone()[0]
    finally:
        conn.close()


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def test_apply_against_target_leaves_a_second_protected_db_byte_for_byte_unchanged(tmp_path, monkeypatch):
    """A + B. Apply runs against target.db only; protected.db (seeded
    identically) stays byte-for-byte unchanged."""
    import scripts.reconcile_bos_orh_emas_identities as writer

    protected_path = tmp_path / "protected.db"
    target_path = tmp_path / "target.db"
    _make_seeded_db(protected_path)
    _make_seeded_db(target_path)
    protected_hash_before = _sha256(protected_path)

    with Session(create_engine(f"sqlite:///{target_path}")) as session:
        fingerprint = dry_run(session)["fingerprint"]

    monkeypatch.setattr(writer, "backup_database", lambda *a, **k: tmp_path / "unused-backup.db")
    writer.main(["--apply", "--allow-database-write", "--database", str(target_path), "--expected-fingerprint", fingerprint])

    assert _identity_count(target_path) == 4
    assert _sha256(protected_path) == protected_hash_before
    assert _identity_count(protected_path) == 0


def test_explicit_target_wins_even_when_default_database_points_at_protected_db(tmp_path, monkeypatch):
    """C. Explicit --database must win over DEFAULT_DATABASE."""
    import scripts.reconcile_bos_orh_emas_identities as writer

    protected_path = tmp_path / "protected.db"
    target_path = tmp_path / "target.db"
    _make_seeded_db(protected_path)
    _make_seeded_db(target_path)
    monkeypatch.setattr(writer, "DEFAULT_DATABASE", protected_path)
    monkeypatch.setattr(writer, "backup_database", lambda *a, **k: tmp_path / "unused-backup.db")

    with Session(create_engine(f"sqlite:///{target_path}")) as session:
        fingerprint = dry_run(session)["fingerprint"]

    writer.main(["--apply", "--allow-database-write", "--database", str(target_path), "--expected-fingerprint", fingerprint])

    assert _identity_count(target_path) == 4
    assert _identity_count(protected_path) == 0


def test_backup_corresponds_to_target_db_not_protected_db(tmp_path, monkeypatch):
    """D. The pre-write backup content must reflect target.db's pre-write
    state, never protected.db - even when DEFAULT_DATABASE points at
    protected.db."""
    import scripts.reconcile_bos_orh_emas_identities as writer

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
    assert _identity_count(backups[0]) == 0  # pre-write snapshot of target.db
    assert _identity_count(target_path) == 4  # live target.db now differs from its own backup
    assert _identity_count(protected_path) == 0


def test_post_write_validation_reads_target_db(tmp_path, monkeypatch):
    """E. apply()'s own post-write verification re-reads through the same
    session main() bound to target.db - a successful return is itself
    proof it read target.db (protected.db's unpromoted row would have
    failed the check, had it somehow been read instead)."""
    import scripts.reconcile_bos_orh_emas_identities as writer

    protected_path = tmp_path / "protected.db"
    target_path = tmp_path / "target.db"
    _make_seeded_db(protected_path)
    _make_seeded_db(target_path)
    monkeypatch.setattr(writer, "DEFAULT_DATABASE", protected_path)
    monkeypatch.setattr(writer, "backup_database", lambda *a, **k: tmp_path / "unused-backup.db")

    with Session(create_engine(f"sqlite:///{target_path}")) as session:
        fingerprint = dry_run(session)["fingerprint"]

    writer.main(["--apply", "--allow-database-write", "--database", str(target_path), "--expected-fingerprint", fingerprint])
    assert _identity_count(target_path) == 4


def test_dry_run_against_target_leaves_both_dbs_unchanged(tmp_path, monkeypatch):
    """F. Dry-run (no --apply) must leave both files completely unchanged."""
    import scripts.reconcile_bos_orh_emas_identities as writer

    protected_path = tmp_path / "protected.db"
    target_path = tmp_path / "target.db"
    _make_seeded_db(protected_path)
    _make_seeded_db(target_path)
    monkeypatch.setattr(writer, "DEFAULT_DATABASE", protected_path)

    protected_before, target_before = _sha256(protected_path), _sha256(target_path)
    called = {"backup": False}
    monkeypatch.setattr(writer, "backup_database", lambda *a, **k: called.__setitem__("backup", True))

    writer.main(["--database", str(target_path)])  # no --apply

    assert called["backup"] is False
    assert _sha256(protected_path) == protected_before
    assert _sha256(target_path) == target_before


def test_inconsistent_fingerprint_fails_closed_without_writing_either_db(tmp_path, monkeypatch):
    """G. A wrong/stale --expected-fingerprint must fail closed before any
    write, on target.db AND leave protected.db untouched."""
    import scripts.reconcile_bos_orh_emas_identities as writer

    protected_path = tmp_path / "protected.db"
    target_path = tmp_path / "target.db"
    _make_seeded_db(protected_path)
    _make_seeded_db(target_path)
    monkeypatch.setattr(writer, "DEFAULT_DATABASE", protected_path)
    called = {"backup": False}
    monkeypatch.setattr(writer, "backup_database", lambda *a, **k: called.__setitem__("backup", True))

    with pytest.raises(ReconciliationGuardError, match="Fingerprint drift"):
        writer.main([
            "--apply", "--allow-database-write", "--database", str(target_path),
            "--expected-fingerprint", "0" * 64,
        ])

    assert _identity_count(target_path) == 0
    assert _identity_count(protected_path) == 0
    assert called["backup"] is False  # validated BEFORE backup


def test_no_process_global_session_local_can_override_explicit_target_path(tmp_path, monkeypatch):
    """H. Even if app.database.SessionLocal is bound to protected.db, main()
    with an explicit --database target.db must still only ever write
    target.db - proving this module never reads SessionLocal at all (it
    isn't even imported), unlike the writer involved in the real incident
    this test class exists to guard against."""
    import app.database as app_database
    import scripts.reconcile_bos_orh_emas_identities as writer
    from sqlalchemy.orm import sessionmaker

    protected_path = tmp_path / "protected.db"
    target_path = tmp_path / "target.db"
    _make_seeded_db(protected_path)
    _make_seeded_db(target_path)

    rogue_session_local = sessionmaker(bind=create_engine(f"sqlite:///{protected_path}"))
    monkeypatch.setattr(app_database, "SessionLocal", rogue_session_local)
    monkeypatch.setattr(writer, "backup_database", lambda *a, **k: tmp_path / "unused-backup.db")

    with Session(create_engine(f"sqlite:///{target_path}")) as session:
        fingerprint = dry_run(session)["fingerprint"]

    writer.main(["--apply", "--allow-database-write", "--database", str(target_path), "--expected-fingerprint", fingerprint])

    assert _identity_count(target_path) == 4
    assert _identity_count(protected_path) == 0
