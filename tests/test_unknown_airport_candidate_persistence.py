"""Tests for app/services/unknown_airport_candidate_persistence.py and
app/models/unknown_airport_candidate.py (UAC1,
docs/architecture/rwi-uac1-unknown-airport-candidate-persistence-report.md,
Slice 1 of docs/architecture/rwi-governed-new-airport-discovery-design.md).

Every test uses an isolated in-memory SQLite database - never the real
data/runway_safe.db. Fixtures are entirely fictional (per the UAC1 mission
brief §9): no real airport, city, or evidence text is used anywhere in
this file. Modeled on the already-proven pattern in
tests/test_reviewer_action_persistence.py.
"""
from __future__ import annotations

import ast
import inspect as inspect_module
import sqlite3
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

import app.services.unknown_airport_candidate_persistence as uac_persistence
from app.database import Base
from app.models import Airport, Installation, Runway, RunwayEnd, Signal
from app.models.unknown_airport_candidate import (
    UNKNOWN_AIRPORT_CANDIDATE_REVIEW_ACTIONS,
    UnknownAirportCandidate,
    UnknownAirportCandidateReview,
)
from app.services.unknown_airport_candidate_persistence import (
    UnknownAirportCandidateResult,
    compute_candidate_fingerprint,
    find_or_create_unknown_airport_candidate,
    get_latest_unknown_airport_candidate_review,
    record_unknown_airport_candidate_review,
)


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def make_session_with_foreign_keys_enforced():
    """Mirrors tests/test_reviewer_action_persistence.py's own helper -
    plain create_engine("sqlite:///:memory:") does NOT enforce SQLite
    foreign keys by default; this is for tests that specifically need
    DB-level FK/CHECK-constraint behavior, matching production
    (app/database.py's own connect-event listener)."""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    Base.metadata.create_all(engine)
    return engine, Session(engine)


def _foo_regional_kwargs(**overrides) -> dict:
    """Fixture A: a wholly fictional unknown airport, no canonical
    Airport row exists for it anywhere in this test file."""
    kwargs = dict(
        raw_name="Foo Regional Airport",
        raw_city="Fooville",
        raw_state_region="Fooland",
        raw_country="Fictionland",
        raw_iata_code="FOO",
        raw_icao_code="KFOO",
        raw_faa_lid="FOO",
        raw_runway_designation="18/36",
        evidence_source_locator="fixture://foo-regional/doc-1",
        evidence_artifact_identity="fixture-artifact-1",
    )
    kwargs.update(overrides)
    return kwargs


# ---------------------------------------------------------------------------
# Fixture A/B/C - candidate persistence, exact convergence, distinct identity
# ---------------------------------------------------------------------------


class TestCandidatePersistenceAndConvergence:
    def test_fixture_a_creates_one_candidate_row(self):
        _, session = make_session()
        result = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs())
        session.commit()

        assert isinstance(result, UnknownAirportCandidateResult)
        assert result.created is True
        assert result.candidate.id is not None
        assert result.candidate.raw_name == "Foo Regional Airport"
        assert result.candidate.raw_country == "Fictionland"
        assert result.candidate.resolved_airport_id is None
        assert session.query(UnknownAirportCandidate).count() == 1

    def test_fixture_b_same_exact_evidence_encountered_again_converges_not_duplicates(self):
        """Same claimed name + country, encountered via a second,
        different evidence fragment (different evidence_source_locator/
        evidence_artifact_identity) - must converge onto the SAME
        candidate row, not create a second one."""
        _, session = make_session()
        first = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs())
        session.commit()

        second = find_or_create_unknown_airport_candidate(
            session,
            **_foo_regional_kwargs(
                evidence_source_locator="fixture://foo-regional/doc-2-different-document",
                evidence_artifact_identity="fixture-artifact-2-different",
            ),
        )
        session.commit()

        assert second.created is False
        assert second.candidate.id == first.candidate.id
        assert session.query(UnknownAirportCandidate).count() == 1
        # The existing row is reused UNCHANGED - the second call's evidence_*
        # values do not overwrite the first-seen row's own provenance fields.
        assert second.candidate.evidence_source_locator == "fixture://foo-regional/doc-1"

    def test_fixture_c_similar_name_materially_different_identity_is_a_separate_candidate(self):
        """A superficially similar name with a different claimed country
        must NOT converge - no fuzzy matching anywhere in this module."""
        _, session = make_session()
        foo = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs())
        session.commit()

        bar = find_or_create_unknown_airport_candidate(
            session,
            **_foo_regional_kwargs(raw_name="Foo Regional Airport", raw_country="Otherland"),
        )
        session.commit()

        assert bar.created is True
        assert bar.candidate.id != foo.candidate.id
        assert bar.candidate.candidate_fingerprint != foo.candidate.candidate_fingerprint
        assert session.query(UnknownAirportCandidate).count() == 2

    def test_similar_but_not_identical_spelling_is_a_separate_candidate_no_fuzzy_merge(self):
        _, session = make_session()
        find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs(raw_name="Foo Regional Airport"))
        session.commit()
        second = find_or_create_unknown_airport_candidate(
            session, **_foo_regional_kwargs(raw_name="Foo Regional Arport")  # deliberate typo
        )
        session.commit()

        assert second.created is True
        assert session.query(UnknownAirportCandidate).count() == 2

    def test_missing_partial_identifying_observations_still_creates_a_valid_candidate(self):
        """Only raw_name is required - every other claimed field may be
        absent (early/partial evidence)."""
        _, session = make_session()
        result = find_or_create_unknown_airport_candidate(session, raw_name="Bar Municipal Airfield")
        session.commit()

        assert result.created is True
        assert result.candidate.raw_city is None
        assert result.candidate.raw_country is None
        assert result.candidate.raw_iata_code is None

    def test_repeated_ingestion_of_the_same_partial_evidence_shape_is_idempotent(self):
        _, session = make_session()
        first = find_or_create_unknown_airport_candidate(session, raw_name="Bar Municipal Airfield")
        session.commit()
        second = find_or_create_unknown_airport_candidate(session, raw_name="Bar Municipal Airfield")
        session.commit()

        assert second.created is False
        assert second.candidate.id == first.candidate.id
        assert session.query(UnknownAirportCandidate).count() == 1


# ---------------------------------------------------------------------------
# PRIMARY ATTACK (UAC1 critical review): fingerprint safety. name+country
# alone was found to be an UNSAFE exact-identity key during review - two
# genuinely distinct real airports can plausibly share a generic name in
# the same country (e.g. "Municipal Airport"). The fingerprint was
# corrected to include raw_city/raw_state_region. This class specifically
# proves the false-merge case the correction closes, and that the fix
# never over-corrects into fuzzy matching.
# ---------------------------------------------------------------------------


class TestFingerprintSafetyCriticalReviewCorrection:
    def test_same_generic_name_same_country_different_city_does_not_converge(self):
        """The exact scenario the review flagged: two DIFFERENT real
        airports plausibly sharing a generic name within one country must
        NOT be silently merged into one candidate."""
        _, session = make_session()
        city_a = find_or_create_unknown_airport_candidate(
            session, raw_name="Municipal Airport", raw_country="Exampland", raw_city="City A",
        )
        session.commit()
        city_b = find_or_create_unknown_airport_candidate(
            session, raw_name="Municipal Airport", raw_country="Exampland", raw_city="City B",
        )
        session.commit()

        assert city_b.created is True
        assert city_a.candidate.id != city_b.candidate.id
        assert city_a.candidate.candidate_fingerprint != city_b.candidate.candidate_fingerprint
        assert session.query(UnknownAirportCandidate).count() == 2

    def test_same_generic_name_same_country_different_state_region_does_not_converge(self):
        _, session = make_session()
        one = find_or_create_unknown_airport_candidate(
            session, raw_name="Regional Airport", raw_country="Exampland", raw_state_region="Province One",
        )
        session.commit()
        two = find_or_create_unknown_airport_candidate(
            session, raw_name="Regional Airport", raw_country="Exampland", raw_state_region="Province Two",
        )
        session.commit()
        assert two.created is True
        assert one.candidate.id != two.candidate.id

    def test_same_name_city_state_country_still_converges_exactly(self):
        """The correction only ever makes convergence STRICTER - it must
        not accidentally prevent genuine exact convergence when every
        location field agrees."""
        _, session = make_session()
        first = find_or_create_unknown_airport_candidate(
            session, raw_name="Regional Airport", raw_city="City A",
            raw_state_region="Province One", raw_country="Exampland",
        )
        session.commit()
        second = find_or_create_unknown_airport_candidate(
            session, raw_name="Regional Airport", raw_city="City A",
            raw_state_region="Province One", raw_country="Exampland",
        )
        session.commit()
        assert second.created is False
        assert second.candidate.id == first.candidate.id

    def test_missing_city_vs_present_city_does_not_converge_fails_closed(self):
        """One fragment omits city, another supplies it - the corrected
        key treats these as different claims (fail-closed: an avoidable
        near-duplicate candidate row, never a false merge) rather than
        guessing they describe the same place."""
        _, session = make_session()
        without_city = find_or_create_unknown_airport_candidate(
            session, raw_name="Regional Airport", raw_country="Exampland",
        )
        session.commit()
        with_city = find_or_create_unknown_airport_candidate(
            session, raw_name="Regional Airport", raw_country="Exampland", raw_city="City A",
        )
        session.commit()
        assert with_city.created is True
        assert without_city.candidate.id != with_city.candidate.id

    def test_claimed_codes_are_not_part_of_the_fingerprint(self):
        """Deliberate design decision (see compute_candidate_fingerprint's
        own docstring): codes are reported too inconsistently per
        fragment to be part of the exact-identity key. Two fragments
        agreeing on name/city/state/country but differing in which code
        they report still converge."""
        _, session = make_session()
        icao_only = find_or_create_unknown_airport_candidate(
            session, raw_name="Regional Airport", raw_city="City A", raw_country="Exampland",
            raw_icao_code="KEXR",
        )
        session.commit()
        iata_only = find_or_create_unknown_airport_candidate(
            session, raw_name="Regional Airport", raw_city="City A", raw_country="Exampland",
            raw_iata_code="EXR",
        )
        session.commit()
        assert iata_only.created is False
        assert iata_only.candidate.id == icao_only.candidate.id
        # First-seen row's own codes are preserved, not overwritten by the
        # second call's different code claim (matches the existing
        # "existing row reused UNCHANGED" convention).
        assert iata_only.candidate.raw_icao_code == "KEXR"
        assert iata_only.candidate.raw_iata_code is None

    def test_contradictory_codes_under_matching_location_still_converge_not_a_defect(self):
        """Documents, rather than silently accepts, the known limitation:
        genuinely CONTRADICTORY codes under an otherwise-matching
        location fingerprint still converge onto one candidate row in
        UAC1. This is an explicit, reviewed design decision (contradiction
        detection under a matching location is a near-duplicate/human-
        review question, design doc §9 - not something the convergence
        key itself is responsible for resolving) and not a defect."""
        _, session = make_session()
        first = find_or_create_unknown_airport_candidate(
            session, raw_name="Regional Airport", raw_city="City A", raw_country="Exampland",
            raw_icao_code="KEXR",
        )
        session.commit()
        second = find_or_create_unknown_airport_candidate(
            session, raw_name="Regional Airport", raw_city="City A", raw_country="Exampland",
            raw_icao_code="KZZZ",  # contradicts KEXR
        )
        session.commit()
        assert second.created is False
        assert second.candidate.id == first.candidate.id

    def test_unicode_and_accented_names_converge_exactly_and_only_exactly(self):
        """International/Unicode fixtures (UAC1 review §19): the
        fingerprint must be source/country-neutral. casefold() correctly
        normalizes case for accented characters, but an accented and
        unaccented spelling remain genuinely DIFFERENT claims - no accent
        folding is performed (accent folding would cross from
        deterministic normalization into heuristic identity, which this
        module deliberately never does)."""
        _, session = make_session()
        accented = find_or_create_unknown_airport_candidate(
            session, raw_name="Aéroport Régional Exemple", raw_city="Ville-Exemple", raw_country="Examplie",
        )
        session.commit()
        accented_again = find_or_create_unknown_airport_candidate(
            session, raw_name="AÉROPORT RÉGIONAL EXEMPLE", raw_city="ville-exemple", raw_country="EXAMPLIE",
        )
        session.commit()
        assert accented_again.created is False
        assert accented_again.candidate.id == accented.candidate.id

        unaccented = find_or_create_unknown_airport_candidate(
            session, raw_name="Aeroport Regional Exemple", raw_city="Ville-Exemple", raw_country="Examplie",
        )
        session.commit()
        assert unaccented.created is True
        assert unaccented.candidate.id != accented.candidate.id

    def test_punctuation_differences_do_not_converge_no_heuristic_normalization(self):
        """Punctuation removal would be a heuristic normalization step,
        not deterministic identity - deliberately not performed."""
        _, session = make_session()
        with_period = find_or_create_unknown_airport_candidate(
            session, raw_name="St. Example Regional Airport", raw_country="Exampland",
        )
        session.commit()
        without_period = find_or_create_unknown_airport_candidate(
            session, raw_name="St Example Regional Airport", raw_country="Exampland",
        )
        session.commit()
        assert without_period.created is True
        assert without_period.candidate.id != with_period.candidate.id

    def test_internal_whitespace_collapse_is_not_performed(self):
        """Multiple-internal-spaces normalization is NOT part of this
        module's canonicalization contract - only casefold + leading/
        trailing strip. Documents the exact, narrow behavior rather than
        silently allowing scope creep into whitespace-collapse
        heuristics."""
        one_space = compute_candidate_fingerprint("Example Regional Airport", "Exampland")
        two_spaces = compute_candidate_fingerprint("Example  Regional Airport", "Exampland")
        assert one_space != two_spaces

    def test_authority_like_name_variants_do_not_fuzzy_converge(self):
        """An organization/authority-style name near-variant must not
        silently converge with a similarly-named different entity - no
        similarity scoring exists anywhere in this function."""
        _, session = make_session()
        authority = find_or_create_unknown_airport_candidate(
            session, raw_name="Example Airport Authority", raw_country="Exampland",
        )
        session.commit()
        near_variant = find_or_create_unknown_airport_candidate(
            session, raw_name="Example Airports Authority", raw_country="Exampland",  # plural, one letter different
        )
        session.commit()
        assert near_variant.created is True
        assert near_variant.candidate.id != authority.candidate.id


# ---------------------------------------------------------------------------
# Malformed / empty identity observations
# ---------------------------------------------------------------------------


class TestMalformedIdentityObservations:
    def test_blank_raw_name_is_rejected(self):
        _, session = make_session()
        with pytest.raises(ValueError, match="raw_name is required"):
            find_or_create_unknown_airport_candidate(session, raw_name="")

    def test_whitespace_only_raw_name_is_rejected(self):
        _, session = make_session()
        with pytest.raises(ValueError, match="raw_name is required"):
            find_or_create_unknown_airport_candidate(session, raw_name="   ")

    def test_raw_name_is_stored_stripped(self):
        _, session = make_session()
        result = find_or_create_unknown_airport_candidate(session, raw_name="  Baz Regional  ")
        session.commit()
        assert result.candidate.raw_name == "Baz Regional"


# ---------------------------------------------------------------------------
# Fingerprint determinism / order-independence
# ---------------------------------------------------------------------------


class TestFingerprintDeterminism:
    def test_fingerprint_is_pure_and_deterministic(self):
        a = compute_candidate_fingerprint("Foo Regional Airport", "Fictionland")
        b = compute_candidate_fingerprint("Foo Regional Airport", "Fictionland")
        assert a == b
        assert len(a) == 64
        assert all(c in "0123456789abcdef" for c in a)

    def test_fingerprint_normalizes_case_and_surrounding_whitespace(self):
        a = compute_candidate_fingerprint("Foo Regional Airport", "Fictionland")
        b = compute_candidate_fingerprint("  foo REGIONAL airport  ", "  FICTIONLAND  ")
        assert a == b

    def test_fingerprint_treats_missing_country_deterministically(self):
        a = compute_candidate_fingerprint("Foo Regional Airport", None)
        b = compute_candidate_fingerprint("Foo Regional Airport", "")
        assert a == b

    def test_fingerprint_never_depends_on_evidence_provenance_fields(self):
        """Information-firewall equivalent for this module (mirrors the
        lifecycle design's own 'search context is never evidence
        identity' invariant, and CandidateFragment.discovery_context's
        own exclusion): evidence_source_locator/evidence_artifact_identity
        must never influence the fingerprint, and therefore must never
        affect convergence."""
        _, session = make_session()
        one = find_or_create_unknown_airport_candidate(
            session, **_foo_regional_kwargs(evidence_source_locator="fixture://a", evidence_artifact_identity="art-a")
        )
        session.commit()
        two = find_or_create_unknown_airport_candidate(
            session, **_foo_regional_kwargs(evidence_source_locator="fixture://totally-different", evidence_artifact_identity="art-z")
        )
        session.commit()
        assert one.candidate.candidate_fingerprint == two.candidate.candidate_fingerprint
        assert one.candidate.id == two.candidate.id

    def test_candidate_fingerprints_are_independent_of_creation_order(self):
        """Creating {Foo, Bar, Baz} in one order vs the reverse order, in
        two separate synthetic databases, must yield the identical set of
        fingerprints - each candidate's fingerprint depends only on its
        own inputs, never on insertion order or sibling rows."""
        names_and_countries = [
            ("Foo Regional Airport", "Fictionland"),
            ("Bar Municipal Airfield", "Otherland"),
            ("Baz International", "Thirdland"),
        ]

        _, session_forward = make_session()
        forward_fingerprints = set()
        for name, country in names_and_countries:
            result = find_or_create_unknown_airport_candidate(session_forward, raw_name=name, raw_country=country)
            forward_fingerprints.add(result.candidate.candidate_fingerprint)
        session_forward.commit()

        _, session_reverse = make_session()
        reverse_fingerprints = set()
        for name, country in reversed(names_and_countries):
            result = find_or_create_unknown_airport_candidate(session_reverse, raw_name=name, raw_country=country)
            reverse_fingerprints.add(result.candidate.candidate_fingerprint)
        session_reverse.commit()

        assert forward_fingerprints == reverse_fingerprints
        assert len(forward_fingerprints) == 3


# ---------------------------------------------------------------------------
# DB-layer fingerprint uniqueness backstop
# ---------------------------------------------------------------------------


class TestFingerprintUniquenessBackstop:
    def test_direct_duplicate_fingerprint_insert_bypassing_the_service_is_rejected_by_the_db(self):
        """Proves candidate_fingerprint uniqueness is enforced at the DB
        layer, not merely by the service's own select-then-create
        convention - a defensive backstop mirroring
        SourceAssertion's own DB-enforced fragment-identity
        UniqueConstraint."""
        _, session = make_session()
        fingerprint = compute_candidate_fingerprint("Foo Regional Airport", "Fictionland")
        session.add(UnknownAirportCandidate(candidate_fingerprint=fingerprint, raw_name="Foo Regional Airport"))
        session.commit()

        session.add(UnknownAirportCandidate(candidate_fingerprint=fingerprint, raw_name="Foo Regional Airport (dup)"))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_service_never_catches_or_retries_the_integrity_error(self):
        """Documents the deliberate design decision (UAC1 report): a
        genuine race is not caught/retried here, matching
        persist_discovery_fragment's identical select-then-create,
        no-retry convention. Verified by inspecting that no
        `except IntegrityError` appears in the module's own source."""
        source = inspect_module.getsource(uac_persistence)
        assert "IntegrityError" not in source


# ---------------------------------------------------------------------------
# Fixture D/E/F/G - review recording
# ---------------------------------------------------------------------------


class TestReviewRecording:
    def test_review_vocabulary_is_exactly_the_four_approved_actions(self):
        assert UNKNOWN_AIRPORT_CANDIDATE_REVIEW_ACTIONS == (
            "MATCH_EXISTING_AIRPORT", "CREATE_NEW_AIRPORT", "REJECT_CANDIDATE", "DEFER",
        )

    def test_fixture_d_match_existing_airport_requires_and_stores_matched_airport_id(self):
        _, session = make_session()
        airport = Airport(name="Existing Fictional Airport", country="Fictionland")
        session.add(airport)
        session.commit()

        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()

        review = record_unknown_airport_candidate_review(
            session, candidate,
            action="MATCH_EXISTING_AIRPORT",
            reason="Fixture: claimed name/city/country match this existing fictional Airport row exactly.",
            reviewer="human:fixture-reviewer",
            matched_airport_id=airport.id,
        )
        session.commit()

        assert review.matched_airport_id == airport.id
        assert review.action == "MATCH_EXISTING_AIRPORT"
        # Recording the review alone never resolves the candidate.
        assert candidate.resolved_airport_id is None

    def test_match_existing_airport_without_matched_airport_id_is_rejected(self):
        _, session = make_session()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()

        with pytest.raises(ValueError, match="requires matched_airport_id"):
            record_unknown_airport_candidate_review(
                session, candidate, action="MATCH_EXISTING_AIRPORT", reason="x", reviewer="human:a",
            )

    def test_match_existing_airport_with_nonexistent_airport_id_is_rejected(self):
        _, session = make_session()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()

        with pytest.raises(ValueError, match="does not exist"):
            record_unknown_airport_candidate_review(
                session, candidate, action="MATCH_EXISTING_AIRPORT", reason="x", reviewer="human:a",
                matched_airport_id=999999,
            )

    def test_matched_airport_id_on_a_non_match_action_is_rejected(self):
        _, session = make_session()
        airport = Airport(name="Existing Fictional Airport", country="Fictionland")
        session.add(airport)
        session.commit()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()

        with pytest.raises(ValueError, match="only valid when action"):
            record_unknown_airport_candidate_review(
                session, candidate, action="DEFER", reason="x", reviewer="human:a",
                matched_airport_id=airport.id,
            )

    def test_fixture_e_create_new_airport_review_never_creates_an_airport(self):
        _, session = make_session()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()
        airports_before = session.query(Airport).count()

        review = record_unknown_airport_candidate_review(
            session, candidate,
            action="CREATE_NEW_AIRPORT",
            reason="Fixture: sufficient independent evidence this is a genuinely new airport.",
            reviewer="human:fixture-reviewer",
        )
        session.commit()

        assert review.action == "CREATE_NEW_AIRPORT"
        assert review.matched_airport_id is None
        assert candidate.resolved_airport_id is None
        assert session.query(Airport).count() == airports_before

    def test_fixture_f_reject_candidate(self):
        _, session = make_session()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()

        review = record_unknown_airport_candidate_review(
            session, candidate, action="REJECT_CANDIDATE",
            reason="Fixture: hallucinated - no corroborating evidence found.", reviewer="human:fixture-reviewer",
        )
        session.commit()
        assert review.action == "REJECT_CANDIDATE"

    def test_fixture_g_defer_then_defer_then_create_new_airport(self):
        _, session = make_session()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()

        first = record_unknown_airport_candidate_review(
            session, candidate, action="DEFER", reason="Need more evidence.", reviewer="human:a",
        )
        session.commit()
        second = record_unknown_airport_candidate_review(
            session, candidate, action="DEFER", reason="Still not enough.", reviewer="human:b",
            supersedes_review_id=first.id,
        )
        session.commit()
        third = record_unknown_airport_candidate_review(
            session, candidate, action="CREATE_NEW_AIRPORT", reason="Now sufficient.", reviewer="human:c",
            supersedes_review_id=second.id,
        )
        session.commit()

        assert session.query(UnknownAirportCandidateReview).filter_by(candidate_id=candidate.id).count() == 3
        latest = get_latest_unknown_airport_candidate_review(session, candidate.id)
        assert latest.id == third.id
        assert latest.action == "CREATE_NEW_AIRPORT"
        # History is never overwritten or collapsed - all three rows persist.
        first_reloaded = session.get(UnknownAirportCandidateReview, first.id)
        assert first_reloaded.action == "DEFER"

    def test_fixture_g_variant_defer_then_reject_candidate(self):
        _, session = make_session()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()

        first = record_unknown_airport_candidate_review(
            session, candidate, action="DEFER", reason="Need more evidence.", reviewer="human:a",
        )
        session.commit()
        second = record_unknown_airport_candidate_review(
            session, candidate, action="REJECT_CANDIDATE", reason="Turned out hallucinated.", reviewer="human:b",
            supersedes_review_id=first.id,
        )
        session.commit()

        latest = get_latest_unknown_airport_candidate_review(session, candidate.id)
        assert latest.id == second.id
        assert latest.action == "REJECT_CANDIDATE"

    def test_defer_then_match_existing_airport_chain(self):
        """UAC1 review §10: DEFER -> MATCH_EXISTING_AIRPORT explicitly
        required. Both rows persist; the match target is stored only on
        the second row; candidate.resolved_airport_id is still never
        touched by recording the review."""
        _, session = make_session()
        airport = Airport(name="Existing Fictional Airport", country="Fictionland")
        session.add(airport)
        session.commit()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()

        first = record_unknown_airport_candidate_review(
            session, candidate, action="DEFER", reason="Need more evidence.", reviewer="human:a",
        )
        session.commit()
        second = record_unknown_airport_candidate_review(
            session, candidate, action="MATCH_EXISTING_AIRPORT", reason="Now confirmed as this existing airport.",
            reviewer="human:b", matched_airport_id=airport.id, supersedes_review_id=first.id,
        )
        session.commit()

        assert session.query(UnknownAirportCandidateReview).filter_by(candidate_id=candidate.id).count() == 2
        latest = get_latest_unknown_airport_candidate_review(session, candidate.id)
        assert latest.id == second.id
        assert latest.action == "MATCH_EXISTING_AIRPORT"
        assert latest.matched_airport_id == airport.id
        first_reloaded = session.get(UnknownAirportCandidateReview, first.id)
        assert first_reloaded.matched_airport_id is None
        session.refresh(candidate)
        assert candidate.resolved_airport_id is None


# ---------------------------------------------------------------------------
# Invalid review vocabulary / malformed review input
# ---------------------------------------------------------------------------


class TestInvalidReviewInput:
    def test_invalid_action_string_is_rejected(self):
        _, session = make_session()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()

        with pytest.raises(ValueError, match="action must be one of"):
            record_unknown_airport_candidate_review(
                session, candidate, action="APPROVE_SIGNAL", reason="x", reviewer="human:a",
            )

    def test_lowercase_action_is_rejected_not_silently_normalized(self):
        _, session = make_session()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()
        with pytest.raises(ValueError, match="action must be one of"):
            record_unknown_airport_candidate_review(session, candidate, action="defer", reason="x", reviewer="human:a")

    def test_padded_action_is_rejected_not_silently_stripped(self):
        _, session = make_session()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()
        with pytest.raises(ValueError, match="action must be one of"):
            record_unknown_airport_candidate_review(session, candidate, action=" DEFER ", reason="x", reviewer="human:a")

    def test_none_action_is_rejected(self):
        _, session = make_session()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()
        with pytest.raises(ValueError, match="action must be one of"):
            record_unknown_airport_candidate_review(session, candidate, action=None, reason="x", reviewer="human:a")

    def test_bytes_action_is_rejected(self):
        _, session = make_session()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()
        with pytest.raises(ValueError, match="action must be one of"):
            record_unknown_airport_candidate_review(session, candidate, action=b"DEFER", reason="x", reviewer="human:a")

    def test_reviewer_action_vocabulary_is_never_reused_here(self):
        """The design explicitly separates this vocabulary from
        app.models.reviewer_action.REVIEWER_ACTIONS as its own distinct
        tuple/table - "DEFER" legitimately appears in both (an ordinary
        English word both independently governed decisions use), so the
        separation this test proves is "two distinct vocabularies," not
        "zero shared English words." The airport-identity-specific
        actions (MATCH_EXISTING_AIRPORT, CREATE_NEW_AIRPORT,
        REJECT_CANDIDATE) must never appear in ReviewerAction's own
        Signal-scoped vocabulary."""
        from app.models.reviewer_action import REVIEWER_ACTIONS
        assert UNKNOWN_AIRPORT_CANDIDATE_REVIEW_ACTIONS != REVIEWER_ACTIONS
        airport_identity_specific = {"MATCH_EXISTING_AIRPORT", "CREATE_NEW_AIRPORT", "REJECT_CANDIDATE"}
        assert airport_identity_specific.isdisjoint(set(REVIEWER_ACTIONS))

    def test_blank_reason_is_rejected(self):
        _, session = make_session()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()
        with pytest.raises(ValueError, match="reason is required"):
            record_unknown_airport_candidate_review(session, candidate, action="DEFER", reason="   ", reviewer="human:a")

    def test_blank_reviewer_is_rejected(self):
        _, session = make_session()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()
        with pytest.raises(ValueError, match="reviewer is required"):
            record_unknown_airport_candidate_review(session, candidate, action="DEFER", reason="x", reviewer="   ")

    def test_review_referencing_unpersisted_candidate_is_rejected(self):
        _, session = make_session()
        unpersisted = UnknownAirportCandidate(candidate_fingerprint="deadbeef", raw_name="Not Yet Saved")
        with pytest.raises(ValueError, match="already be persisted"):
            record_unknown_airport_candidate_review(session, unpersisted, action="DEFER", reason="x", reviewer="human:a")

    def test_review_referencing_deleted_candidate_id_is_rejected(self):
        _, session = make_session()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()
        real_id = candidate.id
        session.delete(candidate)
        session.commit()

        ghost = UnknownAirportCandidate(id=real_id, candidate_fingerprint="irrelevant", raw_name="Ghost")
        # Do not add/persist `ghost` - it only carries the stale id.
        with pytest.raises(ValueError, match="does not exist"):
            record_unknown_airport_candidate_review(session, ghost, action="DEFER", reason="x", reviewer="human:a")

    def test_supersedes_review_id_for_different_candidate_is_rejected(self):
        _, session = make_session()
        candidate_one = find_or_create_unknown_airport_candidate(session, raw_name="Foo Regional Airport").candidate
        candidate_two = find_or_create_unknown_airport_candidate(session, raw_name="Totally Different Airfield").candidate
        session.commit()

        review_one = record_unknown_airport_candidate_review(
            session, candidate_one, action="DEFER", reason="x", reviewer="human:a",
        )
        session.commit()

        with pytest.raises(ValueError, match="same candidate"):
            record_unknown_airport_candidate_review(
                session, candidate_two, action="DEFER", reason="y", reviewer="human:b",
                supersedes_review_id=review_one.id,
            )

    def test_supersedes_nonexistent_review_id_is_rejected(self):
        _, session = make_session()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()
        with pytest.raises(ValueError, match="superseded review must exist"):
            record_unknown_airport_candidate_review(
                session, candidate, action="DEFER", reason="x", reviewer="human:a", supersedes_review_id=999999,
            )

    def test_db_level_check_constraint_rejects_invalid_action_bypassing_the_service(self):
        _, session = make_session_with_foreign_keys_enforced()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()

        session.add(UnknownAirportCandidateReview(
            candidate_id=candidate.id, action="NOT_A_REAL_ACTION", reason="x", reviewer="human:a",
        ))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_db_level_check_constraint_rejects_match_action_without_target_bypassing_the_service(self):
        _, session = make_session_with_foreign_keys_enforced()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()

        session.add(UnknownAirportCandidateReview(
            candidate_id=candidate.id, action="MATCH_EXISTING_AIRPORT", reason="x", reviewer="human:a",
        ))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_db_level_check_constraint_rejects_target_on_non_match_action_bypassing_the_service(self):
        _, session = make_session_with_foreign_keys_enforced()
        airport = Airport(name="Existing Fictional Airport", country="Fictionland")
        session.add(airport)
        session.commit()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()

        session.add(UnknownAirportCandidateReview(
            candidate_id=candidate.id, action="DEFER", reason="x", reviewer="human:a",
            matched_airport_id=airport.id,
        ))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_direct_orm_insert_referencing_nonexistent_candidate_id_is_rejected_by_the_db(self):
        """Defense-in-depth (UAC1 review §12/§21): the service-level
        equivalent (test_review_referencing_unpersisted_candidate_is_rejected)
        already blocks this through the public API; this proves the raw
        FK constraint itself also rejects it for a caller that bypasses
        the service entirely, matching
        tests/test_reviewer_action_migration.py::test_invalid_foreign_key_reference_is_rejected's
        own precedent."""
        _, session = make_session_with_foreign_keys_enforced()
        session.add(UnknownAirportCandidateReview(
            candidate_id=999999, action="DEFER", reason="x", reviewer="human:a",
        ))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


# ---------------------------------------------------------------------------
# Immutability / append-only
# ---------------------------------------------------------------------------


class TestImmutability:
    def test_review_update_is_blocked(self):
        _, session = make_session()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()
        review = record_unknown_airport_candidate_review(session, candidate, action="DEFER", reason="x", reviewer="human:a")
        session.commit()

        review.reason = "silently changed"
        with pytest.raises(ValueError, match="immutable"):
            session.commit()
        session.rollback()

    def test_review_delete_is_blocked(self):
        _, session = make_session()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()
        review = record_unknown_airport_candidate_review(session, candidate, action="DEFER", reason="x", reviewer="human:a")
        session.commit()

        session.delete(review)
        with pytest.raises(ValueError, match="auditable and cannot be deleted"):
            session.commit()
        session.rollback()

    def test_deleting_a_candidate_with_reviews_is_blocked(self):
        """No cascade is defined anywhere in this module - a candidate
        with review history cannot simply vanish out from under its own
        audit trail. In practice this is blocked one layer earlier than
        the raw FK constraint: SQLAlchemy's default (no explicit cascade)
        behavior on deleting a parent is to try to NULL the child's FK
        first, and UnknownAirportCandidateReview's own immutability
        event listener (before_update) rejects that UPDATE outright -
        the same ValueError any other attempted edit to a review row
        raises. Belt-and-suspenders: even if that listener did not
        exist, the NOT NULL candidate_id column plus the FK constraint
        would still stop it at the database layer."""
        _, session = make_session_with_foreign_keys_enforced()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()
        record_unknown_airport_candidate_review(session, candidate, action="DEFER", reason="x", reviewer="human:a")
        session.commit()

        session.delete(candidate)
        with pytest.raises((IntegrityError, ValueError)):
            session.commit()
        session.rollback()


# ---------------------------------------------------------------------------
# Candidate field-level immutability (UAC1 review §7): source-derived
# claim fields must never be silently rewritten after creation, but
# resolved_airport_id must remain assignable for a future governed
# resolution service.
# ---------------------------------------------------------------------------


class TestCandidateFieldImmutability:
    @pytest.mark.parametrize(
        "field,value",
        [
            ("raw_name", "Changed Name"),
            ("raw_city", "Changed City"),
            ("raw_state_region", "Changed Region"),
            ("raw_country", "Changed Country"),
            ("raw_iata_code", "ZZZ"),
            ("raw_icao_code", "KZZZ"),
            ("raw_faa_lid", "ZZZ"),
            ("raw_runway_designation", "99/17"),
            ("evidence_source_locator", "fixture://changed"),
            ("evidence_artifact_identity", "changed-artifact"),
            ("candidate_fingerprint", "0" * 64),
        ],
    )
    def test_source_derived_claim_fields_are_immutable_after_creation(self, field, value):
        _, session = make_session()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()

        setattr(candidate, field, value)
        with pytest.raises(ValueError, match="immutable after creation"):
            session.commit()
        session.rollback()

    def test_resolved_airport_id_remains_settable_for_a_future_governed_service(self):
        """Must NOT be blocked by the immutability guard - it is the one
        field intentionally reserved for a future, not-yet-built governed
        resolution service (design doc §8) to set. UAC1's own code never
        does this itself (see TestNoCanonicalSideEffects), but the model
        must permit it."""
        _, session = make_session()
        airport = Airport(name="Existing Fictional Airport", country="Fictionland")
        session.add(airport)
        session.commit()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()

        candidate.resolved_airport_id = airport.id
        session.commit()  # must not raise
        session.refresh(candidate)
        assert candidate.resolved_airport_id == airport.id

    def test_setting_resolved_airport_id_alongside_a_claim_field_change_is_still_blocked(self):
        """A mixed UPDATE (one legitimate field, one forbidden field)
        must still be rejected in full - partial immutability enforcement
        would be worse than none."""
        _, session = make_session()
        airport = Airport(name="Existing Fictional Airport", country="Fictionland")
        session.add(airport)
        session.commit()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()

        candidate.resolved_airport_id = airport.id
        candidate.raw_name = "Changed Name"
        with pytest.raises(ValueError, match="immutable after creation"):
            session.commit()
        session.rollback()


# ---------------------------------------------------------------------------
# Timestamp / ordering determinism
# ---------------------------------------------------------------------------


class TestOrderingDeterminism:
    def test_get_latest_tiebreaks_by_id_on_identical_timestamps(self):
        """Mirrors tests/test_fh_d4_disposition_resolution.py's own
        identical-timestamp tiebreak coverage: get_latest_reviewer_action's
        (id.desc()) tiebreak convention, reused verbatim here."""
        _, session = make_session()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()

        fixed_time = datetime(2026, 8, 19, 12, 0, 0, tzinfo=UTC)
        first = UnknownAirportCandidateReview(
            candidate_id=candidate.id, action="DEFER", reason="a", reviewer="human:a", created_at=fixed_time,
        )
        second = UnknownAirportCandidateReview(
            candidate_id=candidate.id, action="DEFER", reason="b", reviewer="human:b", created_at=fixed_time,
        )
        session.add_all([first, second])
        session.commit()

        latest = get_latest_unknown_airport_candidate_review(session, candidate.id)
        assert latest.id == max(first.id, second.id)

    def test_get_latest_returns_none_when_no_review_recorded(self):
        _, session = make_session()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()
        assert get_latest_unknown_airport_candidate_review(session, candidate.id) is None

    def test_get_latest_for_nonexistent_candidate_id_returns_none(self):
        _, session = make_session()
        assert get_latest_unknown_airport_candidate_review(session, 999999) is None


# ---------------------------------------------------------------------------
# No canonical side effects (§8 of the UAC1 mission brief)
# ---------------------------------------------------------------------------


class TestNoCanonicalSideEffects:
    def test_no_airport_row_ever_created_by_any_module_function(self):
        _, session = make_session()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()
        for action in ("DEFER", "REJECT_CANDIDATE", "CREATE_NEW_AIRPORT"):
            record_unknown_airport_candidate_review(session, candidate, action=action, reason="x", reviewer="human:a")
            session.commit()
        assert session.query(Airport).count() == 0

    def test_no_runway_runway_end_installation_or_signal_row_ever_created(self):
        _, session = make_session()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()
        record_unknown_airport_candidate_review(session, candidate, action="CREATE_NEW_AIRPORT", reason="x", reviewer="human:a")
        session.commit()

        assert session.query(Runway).count() == 0
        assert session.query(RunwayEnd).count() == 0
        assert session.query(Installation).count() == 0
        assert session.query(Signal).count() == 0

    def test_resolved_airport_id_is_never_set_by_any_module_function(self):
        _, session = make_session()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()
        airport = Airport(name="Existing Fictional Airport", country="Fictionland")
        session.add(airport)
        session.commit()

        record_unknown_airport_candidate_review(
            session, candidate, action="MATCH_EXISTING_AIRPORT", reason="x", reviewer="human:a",
            matched_airport_id=airport.id,
        )
        session.commit()
        session.refresh(candidate)
        assert candidate.resolved_airport_id is None

    def test_pre_existing_airport_row_is_byte_unchanged_after_match_review(self):
        _, session = make_session()
        airport = Airport(name="Existing Fictional Airport", country="Fictionland", iata_code="EFA")
        session.add(airport)
        session.commit()
        before = (airport.name, airport.country, airport.iata_code, airport.icao_code, airport.faa_code, airport.city)

        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()
        record_unknown_airport_candidate_review(
            session, candidate, action="MATCH_EXISTING_AIRPORT", reason="x", reviewer="human:a",
            matched_airport_id=airport.id,
        )
        session.commit()
        session.refresh(airport)
        after = (airport.name, airport.country, airport.iata_code, airport.icao_code, airport.faa_code, airport.city)
        assert before == after

    def test_no_function_signature_accepts_a_runway_runway_end_installation_or_signal_object_parameter(self):
        """Checks for a parameter that would accept a live ORM
        Runway/RunwayEnd/Installation/Signal reference (an *_id FK-shaped
        name, or a type annotation naming one of those classes) - NOT a
        bare substring match, which would false-positive on legitimate
        claimed-evidence fields like raw_runway_designation (a plain
        string, never an object reference)."""
        forbidden_id_names = {"runway_id", "runway_end_id", "installation_id", "signal_id", "signal"}
        forbidden_types = {"Runway", "RunwayEnd", "Installation", "Signal"}
        for name, func in inspect_module.getmembers(uac_persistence, inspect_module.isfunction):
            if func.__module__ != uac_persistence.__name__:
                continue
            for param_name, param in inspect_module.signature(func).parameters.items():
                assert param_name.lower() not in forbidden_id_names, (
                    f"{name}({param_name}) unexpectedly accepts a canonical-object-id-shaped parameter"
                )
                annotation = param.annotation
                annotation_name = getattr(annotation, "__name__", str(annotation))
                assert annotation_name not in forbidden_types, (
                    f"{name}({param_name}: {annotation_name}) unexpectedly accepts a canonical ORM object"
                )

    def test_module_source_never_constructs_airport_runway_runwayend_installation_or_signal(self):
        """AST-level proof (mirrors D4D8's own
        test_decision_comes_only_from_config_ast precedent): scans every
        ast.Call node in the module for a constructor call to any of the
        five canonical/investor-facing classes this slice must never
        touch."""
        source = inspect_module.getsource(uac_persistence)
        tree = ast.parse(source)
        forbidden = {"Airport", "Runway", "RunwayEnd", "Installation", "Signal"}
        found_constructors = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in forbidden:
                found_constructors.add(node.func.id)
        assert found_constructors == set()

    def test_model_module_source_never_constructs_airport_runway_runwayend_installation_or_signal(self):
        import app.models.unknown_airport_candidate as uac_models
        source = inspect_module.getsource(uac_models)
        tree = ast.parse(source)
        forbidden = {"Airport", "Runway", "RunwayEnd", "Installation", "Signal"}
        found_constructors = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in forbidden:
                found_constructors.add(node.func.id)
        assert found_constructors == set()


# ---------------------------------------------------------------------------
# Transaction rollback
# ---------------------------------------------------------------------------


class TestTransactionRollback:
    def test_uncommitted_candidate_creation_is_discarded_on_rollback(self):
        _, session = make_session()
        find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs())
        # Never committed.
        session.rollback()
        assert session.query(UnknownAirportCandidate).count() == 0

    def test_uncommitted_review_is_discarded_on_rollback(self):
        _, session = make_session()
        candidate = find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs()).candidate
        session.commit()

        record_unknown_airport_candidate_review(session, candidate, action="DEFER", reason="x", reviewer="human:a")
        # Never committed.
        session.rollback()
        assert session.query(UnknownAirportCandidateReview).count() == 0

    def test_this_module_never_commits_by_itself(self):
        """Static proof, matching every sibling persistence module's own
        established convention (persist_discovery_fragment,
        record_reviewer_action): no call to session.commit() anywhere in
        this module's source."""
        source = inspect_module.getsource(uac_persistence)
        assert ".commit(" not in source
        assert "import SessionLocal" not in source


# ---------------------------------------------------------------------------
# Missing schema
# ---------------------------------------------------------------------------


class TestMissingSchema:
    def test_operating_against_a_database_missing_the_tables_fails_loudly_not_silently(self):
        engine = create_engine("sqlite:///:memory:")
        # Deliberately no Base.metadata.create_all(engine).
        session = Session(engine)
        with pytest.raises(OperationalError):
            find_or_create_unknown_airport_candidate(session, **_foo_regional_kwargs())
        session.rollback()


# ---------------------------------------------------------------------------
# Model-shape checks (columns, constraints) - independent proof the model
# matches the approved design, not merely trusting the module docstring.
# ---------------------------------------------------------------------------


class TestModelShape:
    def test_unknown_airport_candidates_table_columns(self):
        table = UnknownAirportCandidate.__table__
        expected = {
            "id", "candidate_fingerprint", "raw_name", "raw_city", "raw_state_region", "raw_country",
            "raw_iata_code", "raw_icao_code", "raw_faa_lid", "raw_runway_designation",
            "evidence_source_locator", "evidence_artifact_identity", "resolved_airport_id", "created_at",
        }
        assert set(table.columns.keys()) == expected

    def test_unknown_airport_candidates_fingerprint_is_unique(self):
        table = UnknownAirportCandidate.__table__
        unique_constraint_columns = {
            tuple(c.name for c in constraint.columns)
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert ("candidate_fingerprint",) in unique_constraint_columns

    def test_unknown_airport_candidates_raw_name_is_required_at_db_layer(self):
        columns = {c.name: c for c in UnknownAirportCandidate.__table__.columns}
        assert columns["raw_name"].nullable is False
        assert columns["raw_city"].nullable is True
        assert columns["raw_country"].nullable is True
        assert columns["resolved_airport_id"].nullable is True

    def test_unknown_airport_candidate_reviews_table_columns(self):
        table = UnknownAirportCandidateReview.__table__
        expected = {
            "id", "candidate_id", "action", "reason", "reviewer",
            "matched_airport_id", "created_at", "supersedes_review_id",
        }
        assert set(table.columns.keys()) == expected

    def test_unknown_airport_candidate_reviews_required_columns(self):
        columns = {c.name: c for c in UnknownAirportCandidateReview.__table__.columns}
        assert columns["candidate_id"].nullable is False
        assert columns["action"].nullable is False
        assert columns["reason"].nullable is False
        assert columns["reviewer"].nullable is False
        assert columns["matched_airport_id"].nullable is True
        assert columns["supersedes_review_id"].nullable is True

    def test_no_review_state_column_exists_on_the_candidate(self):
        """Deliberate design decision (module docstring): current review
        status is always derived by recency, never cached."""
        assert "review_state" not in UnknownAirportCandidate.__table__.columns.keys()


# ---------------------------------------------------------------------------
# Full-database migration-shaped smoke test: create every table via
# Base.metadata (as any real migration/test fixture already does) with
# foreign keys enforced, and prove foreign_key_check/integrity_check are
# clean - the same standard scripts/migrate_reviewer_action_slice9b.py's
# own migration tests hold their table to.
# ---------------------------------------------------------------------------


class TestFullSchemaIntegrity:
    def test_full_schema_creates_cleanly_with_foreign_keys_enforced(self, tmp_path):
        database = tmp_path / "uac1_fixture.db"
        engine = create_engine(f"sqlite:///{database}")
        Base.metadata.create_all(engine)
        engine.dispose()

        connection = sqlite3.connect(database)
        connection.execute("PRAGMA foreign_keys=ON")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        connection.close()

    def test_unknown_airport_candidate_tables_appear_in_full_schema(self, tmp_path):
        database = tmp_path / "uac1_fixture2.db"
        engine = create_engine(f"sqlite:///{database}")
        Base.metadata.create_all(engine)
        engine.dispose()

        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        connection.close()
        assert "unknown_airport_candidates" in tables
        assert "unknown_airport_candidate_reviews" in tables
