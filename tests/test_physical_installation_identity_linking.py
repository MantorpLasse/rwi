"""app.services.physical_installation_identity_linking tests - all against
isolated in-memory databases, never the real development database."""
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app import models  # noqa: F401 - registers all metadata
from app.models import Airport, PhysicalInstallationIdentity, Runway, RunwayEnd
from app.services.physical_installation_identity_linking import (
    ALREADY_LINKED_CORRECT,
    AMBIGUOUS,
    CONFLICTING_LINK,
    CROSS_AIRPORT,
    RESOLVED_NEW,
    UNRESOLVED,
    apply_identity_links,
    plan_identity_links,
    resolve_identity,
)


@pytest.fixture
def engine():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.commit()
    return engine


def _seed_mdw_cgf(session: Session) -> dict:
    mdw = Airport(name="Chicago Midway International Airport", faa_code="MDW", iata_code="MDW", icao_code="KMDW", country="USA")
    cgf = Airport(name="Cuyahoga", faa_code="CGF", country="USA")
    session.add_all([mdw, cgf])
    session.flush()

    mdw_r1 = Runway(airport=mdw, designation="4R/22L")
    mdw_r2 = Runway(airport=mdw, designation="13L/31R")
    mdw_r3 = Runway(airport=mdw, designation="4L/22R")
    mdw_r4 = Runway(airport=mdw, designation="13R/31L")
    cgf_r1 = Runway(airport=cgf, designation="6/24")
    session.add_all([mdw_r1, mdw_r2, mdw_r3, mdw_r4, cgf_r1])
    session.flush()

    ends = {
        "4R": RunwayEnd(runway_id=mdw_r1.id, designation="4R"),
        "22L": RunwayEnd(runway_id=mdw_r1.id, designation="22L"),
        "13L": RunwayEnd(runway_id=mdw_r2.id, designation="13L"),
        "31R": RunwayEnd(runway_id=mdw_r2.id, designation="31R"),
        "4L": RunwayEnd(runway_id=mdw_r3.id, designation="4L"),
        "22R": RunwayEnd(runway_id=mdw_r3.id, designation="22R"),
        "13R": RunwayEnd(runway_id=mdw_r4.id, designation="13R"),
        "31L": RunwayEnd(runway_id=mdw_r4.id, designation="31L"),
        "6": RunwayEnd(runway_id=cgf_r1.id, designation="6"),
        "24": RunwayEnd(runway_id=cgf_r1.id, designation="24"),
    }
    session.add_all(ends.values())
    session.flush()

    identities = {
        "mdw_04R": PhysicalInstallationIdentity(airport_id=mdw.id, runway_end="04R"),
        "mdw_22L": PhysicalInstallationIdentity(airport_id=mdw.id, runway_end="22L"),
        "mdw_13L": PhysicalInstallationIdentity(airport_id=mdw.id, runway_end="13L"),
        "mdw_31R": PhysicalInstallationIdentity(airport_id=mdw.id, runway_end="31R"),
        "cgf_06": PhysicalInstallationIdentity(airport_id=cgf.id, runway_end="06"),
        "cgf_24": PhysicalInstallationIdentity(airport_id=cgf.id, runway_end="24"),
    }
    session.add_all(identities.values())
    session.commit()

    return {"mdw": mdw, "cgf": cgf, "runways": {"mdw_r1": mdw_r1, "mdw_r2": mdw_r2, "cgf_r1": cgf_r1},
            "ends": ends, "identities": identities}


def test_all_six_mdw_cgf_mappings_resolve_correctly(engine):
    with Session(engine) as session:
        seed = _seed_mdw_cgf(session)
        plan = plan_identity_links(session, ("MDW", "CGF"))

        assert len(plan) == 6
        by_id = {r.identity_id: r for r in plan}

        expected = {
            seed["identities"]["mdw_04R"].id: ("4R/22L", "4R"),
            seed["identities"]["mdw_22L"].id: ("4R/22L", "22L"),
            seed["identities"]["mdw_13L"].id: ("13L/31R", "13L"),
            seed["identities"]["mdw_31R"].id: ("13L/31R", "31R"),
            seed["identities"]["cgf_06"].id: ("6/24", "6"),
            seed["identities"]["cgf_24"].id: ("6/24", "24"),
        }
        for identity_id, (runway_designation, end_designation) in expected.items():
            r = by_id[identity_id]
            assert r.status == RESOLVED_NEW
            assert r.target_runway_designation == runway_designation
            assert r.target_runway_end_designation == end_designation
            assert r.current_runway_end_id is None
            assert r.proposed_runway_end_id == r.target_runway_end_id


def test_leading_zero_normalization_04r_and_06(engine):
    """04R <-> 4R and 06 <-> 6 - stored designation keeps the leading zero,
    canonical RunwayEnd designation doesn't; resolution must bridge that."""
    with Session(engine) as session:
        seed = _seed_mdw_cgf(session)
        r = resolve_identity(session, seed["identities"]["mdw_04R"])
        assert r.normalized_designation == "4R"
        assert r.status == RESOLVED_NEW

        r = resolve_identity(session, seed["identities"]["cgf_06"])
        assert r.normalized_designation == "6"
        assert r.status == RESOLVED_NEW


def test_reciprocal_runway_parent_is_verified_not_just_any_matching_end(engine):
    """The resolved end must belong to the SPECIFIC runway whose pair the
    reviewed end names - not merely any end with a matching designation
    anywhere at the airport."""
    with Session(engine) as session:
        seed = _seed_mdw_cgf(session)
        r = resolve_identity(session, seed["identities"]["mdw_13L"])
        assert r.target_runway_id == seed["runways"]["mdw_r2"].id
        assert r.target_runway_designation == "13L/31R"
        # NOT mdw_r1 (4R/22L) or any other runway
        assert r.target_runway_id != seed["runways"]["mdw_r1"].id


def test_cross_airport_match_is_rejected(engine):
    """A RunwayEnd belonging to a different airport must never be proposed,
    even if constructed directly (defense-in-depth, not just query scoping)."""
    with Session(engine) as session:
        seed = _seed_mdw_cgf(session)
        cgf_end = seed["ends"]["6"]
        mdw_identity = seed["identities"]["mdw_04R"]
        # Force a lookup table that (incorrectly) contains another airport's end.
        bogus_lookup = {"4R": [cgf_end]}
        r = resolve_identity(session, mdw_identity, ends_by_designation=bogus_lookup)
        assert r.status == CROSS_AIRPORT


def test_missing_runway_end_is_rejected(engine):
    with Session(engine) as session:
        seed = _seed_mdw_cgf(session)
        orphan = PhysicalInstallationIdentity(airport_id=seed["mdw"].id, runway_end="09")
        session.add(orphan)
        session.commit()
        r = resolve_identity(session, orphan)
        assert r.status == UNRESOLVED


def test_ambiguous_match_is_rejected(engine):
    with Session(engine) as session:
        seed = _seed_mdw_cgf(session)
        # A second, different runway that coincidentally has an end also
        # designated "4R" - genuinely ambiguous which one a bare "4R" means.
        dup_runway = Runway(airport=seed["mdw"], designation="4R/22L-alt")
        session.add(dup_runway)
        session.flush()
        session.add(RunwayEnd(runway_id=dup_runway.id, designation="4R"))
        session.commit()

        r = resolve_identity(session, seed["identities"]["mdw_04R"])
        assert r.status == AMBIGUOUS


def test_existing_correct_link_is_idempotent(engine):
    with Session(engine) as session:
        seed = _seed_mdw_cgf(session)
        identity = seed["identities"]["mdw_04R"]
        identity.runway_end_id = seed["ends"]["4R"].id
        session.commit()

        r = resolve_identity(session, identity)
        assert r.status == ALREADY_LINKED_CORRECT
        assert r.proposed_runway_end_id == seed["ends"]["4R"].id


def test_existing_conflicting_link_aborts(engine):
    with Session(engine) as session:
        seed = _seed_mdw_cgf(session)
        identity = seed["identities"]["mdw_04R"]
        identity.runway_end_id = seed["ends"]["13L"].id  # wrong end, on purpose
        session.commit()

        r = resolve_identity(session, identity)
        assert r.status == CONFLICTING_LINK

        with pytest.raises(ValueError, match="not safe to apply"):
            apply_identity_links(session, ("MDW", "CGF"))
        session.rollback()
        # nothing was written
        assert session.get(PhysicalInstallationIdentity, identity.id).runway_end_id == seed["ends"]["13L"].id


def test_dry_run_performs_zero_writes(engine):
    with Session(engine) as session:
        seed = _seed_mdw_cgf(session)
        plan_identity_links(session, ("MDW", "CGF"))
        for identity in session.scalars(select(PhysicalInstallationIdentity)).all():
            assert identity.runway_end_id is None


def test_apply_changes_only_runway_end_id(engine):
    with Session(engine) as session:
        seed = _seed_mdw_cgf(session)
        before = {
            (i.id, i.airport_id, i.runway_id, i.runway_end, i.created_at)
            for i in session.scalars(select(PhysicalInstallationIdentity)).all()
        }
        apply_identity_links(session, ("MDW", "CGF"))
        session.commit()
        after = {
            (i.id, i.airport_id, i.runway_id, i.runway_end, i.created_at)
            for i in session.scalars(select(PhysicalInstallationIdentity)).all()
        }
        assert before == after  # every other column untouched
        assert all(i.runway_end_id is not None for i in session.scalars(select(PhysicalInstallationIdentity)).all())

        # Runway/RunwayEnd rows themselves are untouched (no new rows, no attribute changes)
        assert len(session.scalars(select(Runway)).all()) == 5
        assert len(session.scalars(select(RunwayEnd)).all()) == 10


def test_all_or_nothing_one_bad_identity_blocks_the_whole_batch(engine):
    with Session(engine) as session:
        seed = _seed_mdw_cgf(session)
        # Make exactly one of the six unresolvable.
        seed["identities"]["cgf_24"].runway_end = "99"
        session.commit()

        with pytest.raises(ValueError):
            apply_identity_links(session, ("MDW", "CGF"))
        session.rollback()

        # None of the other five were linked either - true all-or-nothing.
        for identity in session.scalars(select(PhysicalInstallationIdentity)).all():
            assert identity.runway_end_id is None


def test_repeat_apply_is_idempotent(engine):
    with Session(engine) as session:
        seed = _seed_mdw_cgf(session)
        apply_identity_links(session, ("MDW", "CGF"))
        session.commit()

    with Session(engine) as session:
        second = apply_identity_links(session, ("MDW", "CGF"))
        session.commit()
        assert all(r.status == ALREADY_LINKED_CORRECT for r in second)
        assert len(second) == 6
