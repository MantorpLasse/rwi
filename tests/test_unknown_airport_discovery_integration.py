"""Tests for app/services/unknown_airport_discovery_integration.py (UAC3,
docs/architecture/rwi-uac3-unknown-airport-discovery-integration-report.md).

Isolated, in-memory (or tmp_path, for the real-migration-chain test)
SQLite databases only - never the real one. Fixtures are entirely
fictional (no real airport is used anywhere in this file) except where a
test deliberately reuses one of this project's already-committed
synthetic conventions.
"""
from __future__ import annotations

import ast
import inspect as inspect_module
import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app import models as _models  # noqa: F401 - registers all metadata
from app.models import Airport, Installation, PhysicalInstallationIdentity, Runway, RunwayEnd, Signal, Source, SourceAssertion
from app.models.unknown_airport_candidate import UnknownAirportCandidate, UnknownAirportCandidateReview
from app.services.discovery_candidate_fragment import CandidateFragment
from app.services.discovery_evidence_persistence import DiscoverySourceMetadata
from app.services.evidence_attachment_guard import AttachmentOutcome, CandidateAirport
from app.services.unknown_airport_candidate_persistence import find_or_create_unknown_airport_candidate
from app.services.unknown_airport_discovery_integration import (
    DiscoveryIdentityOutcome,
    DiscoveryIdentityResolutionResult,
    resolve_or_persist_discovery_identity,
)
import scripts.migrate_source_assertion_unknown_airport_uac2b as uac2b_migration
import scripts.migrate_unknown_airport_candidates_uac2a as uac2a_migration


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _seed_airport(session, *, faa_code=None, name, city=None, country="USA") -> Airport:
    airport = Airport(name=name, faa_code=faa_code, country=country, city=city)
    session.add(airport)
    session.flush()
    return airport


def _candidate(airport: Airport, **kwargs) -> CandidateAirport:
    return CandidateAirport(id=airport.id, name=airport.name, **kwargs)


def _artifact(name: str) -> str:
    return f"uac3-test-artifact:{name}"


def _meta(document_identity: str, title: str = "Test document", producer: str | None = None) -> DiscoverySourceMetadata:
    return DiscoverySourceMetadata(document_identity=document_identity, title=title, source_type=producer or "web_discovery")


# ---------------------------------------------------------------------------
# A. Known canonical match - existing path unchanged
# ---------------------------------------------------------------------------


class TestKnownCanonicalMatch:
    def test_known_match_persists_airport_id_no_candidate(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, faa_code="BOS", name="Boston Logan International Airport")
            candidate_airport = _candidate(airport, identifiers=frozenset({"BOS"}), canonical_runway_pairs=frozenset({"4L/22R"}))
            fragment = CandidateFragment(
                artifact_identity=_artifact("bos"), source_locator="p1", raw_text="BOS RWY 4L/22R EMAS work.",
                airport_identifiers=frozenset({"BOS"}), runway_pairs=frozenset({"4L/22R"}),
            )
            result = resolve_or_persist_discovery_identity(session, _meta("bos-doc"), fragment, [candidate_airport])

            assert isinstance(result, DiscoveryIdentityResolutionResult)
            assert result.outcome == DiscoveryIdentityOutcome.KNOWN_CANONICAL_ATTACHMENT
            assert result.attached_airport_id == airport.id
            assert result.unknown_airport_candidate_id is None

            assertion = session.get(SourceAssertion, result.source_assertion_id)
            assert assertion.airport_id == airport.id
            assert assertion.unknown_airport_candidate_id is None
            assert session.query(UnknownAirportCandidate).count() == 0

    def test_known_match_backward_compatibility_matches_direct_persist_discovery_fragment(self):
        """§9/T: the strongest available proof of "no behavior drift" -
        the SAME fragment, run through persist_discovery_fragment()
        directly and through the new orchestration, must produce the
        identical persisted outcome/airport_id."""
        from app.services.discovery_evidence_persistence import persist_discovery_fragment

        with Session(_engine()) as session:
            airport = _seed_airport(session, faa_code="ORH", name="Worcester Regional")
            candidate_airport = _candidate(airport, identifiers=frozenset({"ORH"}))
            fragment_direct = CandidateFragment(
                artifact_identity=_artifact("orh-direct"), source_locator="p1", raw_text="ORH work.",
                airport_identifiers=frozenset({"ORH"}),
            )
            fragment_orchestrated = CandidateFragment(
                artifact_identity=_artifact("orh-orchestrated"), source_locator="p1", raw_text="ORH work.",
                airport_identifiers=frozenset({"ORH"}),
            )
            direct = persist_discovery_fragment(session, _meta("orh-direct-doc"), fragment_direct, [candidate_airport])
            orchestrated = resolve_or_persist_discovery_identity(session, _meta("orh-orch-doc"), fragment_orchestrated, [candidate_airport])

            assert direct.outcome == orchestrated.attachment_outcome
            assert direct.attached_airport_id == orchestrated.attached_airport_id == airport.id

    def test_msp_shaped_known_airport_fixture_unchanged(self):
        """Review §5: reproduces the real, live-proven MSP/SFO cross-
        airport shape (see tests/test_discovery_evidence_persistence.py::test_case_A_sfo_msp_persists_for_msp_not_sfo,
        which mirrors SourceAssertion #222's own real, live-acquired
        fixture) through the new orchestration - MSP confirms, SFO is
        never chosen, despite a search context seeded toward SFO."""
        with Session(_engine()) as session:
            sfo_row = _seed_airport(session, faa_code="SFO", name="San Francisco International Airport", city="San Francisco")
            msp_row = _seed_airport(session, faa_code="MSP", name="Minneapolis-St. Paul International Airport", city="Minneapolis")
            sfo = _candidate(sfo_row, identifiers=frozenset({"SFO", "KSFO"}), known_issuers=frozenset({"San Francisco Airport Commission"}))
            msp = _candidate(
                msp_row, identifiers=frozenset({"MSP", "KMSP"}),
                canonical_runway_ends=frozenset({"30L"}), known_issuers=frozenset({"Metropolitan Airports Commission"}),
            )
            fragment = CandidateFragment(
                artifact_identity=_artifact("msp-memo"), source_locator="p1-p2",
                raw_text="Metropolitan Airports Commission. Runway 30L EMAS. Sole source procurement with Runway Safe.",
                issuers=frozenset({"Metropolitan Airports Commission"}), runway_ends=frozenset({"30L"}),
            )
            result = resolve_or_persist_discovery_identity(session, _meta("msp-memo-doc"), fragment, [sfo, msp])

            assert result.outcome == DiscoveryIdentityOutcome.KNOWN_CANONICAL_ATTACHMENT
            assert result.attached_airport_id == msp_row.id
            assert result.attached_airport_id != sfo_row.id
            assert result.unknown_airport_candidate_id is None
            assert session.query(UnknownAirportCandidate).count() == 0
            assertion = session.get(SourceAssertion, result.source_assertion_id)
            assert assertion.airport_id == msp_row.id
            assert assertion.identity_guard_decision == "ATTACH_CONFIRMED"


# ---------------------------------------------------------------------------
# B/E. Strong unknown identity, including "all known candidates conflict
# but the fragment itself carries a coherent identity"
# ---------------------------------------------------------------------------


class TestStrongUnknownIdentity:
    def test_strong_unknown_identity_no_known_candidates_supplied(self):
        with Session(_engine()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("foo"), source_locator="p1",
                raw_text="Foo Regional Airport is planning an EMAS feasibility study for Runway 18.",
                airport_names=frozenset({"Foo Regional Airport"}), runway_ends=frozenset({"18"}),
            )
            result = resolve_or_persist_discovery_identity(session, _meta("foo-doc"), fragment, [])

            assert result.outcome == DiscoveryIdentityOutcome.UNKNOWN_AIRPORT_CANDIDATE
            assert result.attached_airport_id is None
            assert result.unknown_airport_candidate_id is not None
            assert result.unknown_airport_candidate_created is True

            candidate = session.get(UnknownAirportCandidate, result.unknown_airport_candidate_id)
            assert candidate.raw_name == "Foo Regional Airport"
            assert candidate.raw_runway_designation == "18"
            assert candidate.resolved_airport_id is None

            assertion = session.get(SourceAssertion, result.source_assertion_id)
            assert assertion.airport_id is None
            assert assertion.unknown_airport_candidate_id == candidate.id
            assert assertion.raw_relevant_text == fragment.raw_text
            assert session.query(Airport).count() == 0

    def test_all_known_candidates_conflict_but_coherent_unknown_identity_present(self):
        """§5C: evidence positively conflicts with every supplied known
        candidate (REJECT_CROSS_AIRPORT), but the fragment itself names
        exactly one, distinct, coherent airport - forms a candidate."""
        with Session(_engine()) as session:
            known = _seed_airport(session, faa_code="BOS", name="Boston Logan International Airport")
            candidate_airport = _candidate(known, identifiers=frozenset({"BOS"}))
            fragment = CandidateFragment(
                artifact_identity=_artifact("conflict"), source_locator="p1",
                raw_text="Foo Regional Airport EMAS plan (not Boston Logan).",
                airport_names=frozenset({"Foo Regional Airport"}),
                contradicting_names=frozenset({"Foo Regional Airport"}),
            )
            result = resolve_or_persist_discovery_identity(session, _meta("conflict-doc"), fragment, [candidate_airport])

            assert result.attachment_outcome == AttachmentOutcome.REJECT_CROSS_AIRPORT
            assert result.outcome == DiscoveryIdentityOutcome.UNKNOWN_AIRPORT_CANDIDATE
            candidate = session.get(UnknownAirportCandidate, result.unknown_airport_candidate_id)
            assert candidate.raw_name == "Foo Regional Airport"

    def test_all_known_candidates_conflict_and_evidence_identity_is_weak_stays_unresolved(self):
        """Review §7 inverse: the fragment conflicts with the supplied
        known candidate, but carries NO usable identity of its own
        (zero claimed airport names) - must NOT form a candidate,
        distinguishing this from the "coherent different identity" case
        above purely on the fragment's own formability, never on the
        conflict itself."""
        with Session(_engine()) as session:
            known = _seed_airport(session, faa_code="BOS", name="Boston Logan International Airport")
            candidate_airport = _candidate(known, identifiers=frozenset({"BOS"}))
            fragment = CandidateFragment(
                artifact_identity=_artifact("conflict-weak"), source_locator="p1",
                raw_text="Not Boston Logan - some other place.",
                contradicting_names=frozenset({"Not Boston Logan"}),
            )
            result = resolve_or_persist_discovery_identity(session, _meta("conflict-weak-doc"), fragment, [candidate_airport])

            assert result.attachment_outcome == AttachmentOutcome.REJECT_CROSS_AIRPORT
            assert result.outcome == DiscoveryIdentityOutcome.UNRESOLVED_IDENTITY
            assert result.unknown_airport_candidate_id is None
            assert session.query(UnknownAirportCandidate).count() == 0


# ---------------------------------------------------------------------------
# C. Weak / insufficient identity
# ---------------------------------------------------------------------------


class TestCandidateFormabilityAdversarial:
    """Review §4: aggressive attack on 'exactly one claimed airport name'
    as the formability bar."""

    def test_punctuation_only_name_never_forms_a_candidate(self):
        """Critical-review correction: a raw name with zero alphabetic
        characters cannot be an airport name in any language."""
        with Session(_engine()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("punct"), source_locator="p1", raw_text="--- plans EMAS.",
                airport_names=frozenset({"---"}),
            )
            result = resolve_or_persist_discovery_identity(session, _meta("punct-doc"), fragment, [])
            assert result.outcome == DiscoveryIdentityOutcome.UNRESOLVED_IDENTITY
            assert session.query(UnknownAirportCandidate).count() == 0

    def test_numeric_only_name_never_forms_a_candidate(self):
        with Session(_engine()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("numeric"), source_locator="p1", raw_text="123 plans EMAS.",
                airport_names=frozenset({"123"}),
            )
            result = resolve_or_persist_discovery_identity(session, _meta("numeric-doc"), fragment, [])
            assert result.outcome == DiscoveryIdentityOutcome.UNRESOLVED_IDENTITY
            assert session.query(UnknownAirportCandidate).count() == 0

    def test_symbol_only_name_never_forms_a_candidate(self):
        with Session(_engine()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("symbol"), source_locator="p1", raw_text="*** plans EMAS.",
                airport_names=frozenset({"***"}),
            )
            result = resolve_or_persist_discovery_identity(session, _meta("symbol-doc"), fragment, [])
            assert result.outcome == DiscoveryIdentityOutcome.UNRESOLVED_IDENTITY

    def test_bare_generic_word_still_forms_a_candidate_deliberately(self):
        """A bare, generic-but-real word ('Airport') is deliberately NOT
        filtered - see the module's own docstring for why a genericness/
        semantic-quality blocklist is explicitly not added (it would
        require an inevitably incomplete, English-biased list; the
        human review layer this candidate feeds is the correct place to
        reject it, not this deterministic gate)."""
        with Session(_engine()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("bare"), source_locator="p1", raw_text="Airport plans EMAS.",
                airport_names=frozenset({"Airport"}),
            )
            result = resolve_or_persist_discovery_identity(session, _meta("bare-doc"), fragment, [])
            assert result.outcome == DiscoveryIdentityOutcome.UNKNOWN_AIRPORT_CANDIDATE
            candidate = session.get(UnknownAirportCandidate, result.unknown_airport_candidate_id)
            assert candidate.raw_name == "Airport"

    def test_generic_phrase_international_airport_still_forms_a_candidate_deliberately(self):
        with Session(_engine()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("generic-phrase"), source_locator="p1",
                raw_text="International Airport plans EMAS.", airport_names=frozenset({"International Airport"}),
            )
            result = resolve_or_persist_discovery_identity(session, _meta("generic-phrase-doc"), fragment, [])
            assert result.outcome == DiscoveryIdentityOutcome.UNKNOWN_AIRPORT_CANDIDATE

    def test_case_and_whitespace_variant_treated_as_two_distinct_names_fails_closed(self):
        """'One list element' is the literal bar, not 'one normalized
        identity' - two differently-cased/whitespaced strings for what a
        human might recognize as the same name are, correctly and
        conservatively, treated as an ambiguous multi-name fragment
        (fail-closed, matching the project's own 'prefer false
        separation over false automatic convergence' ethos elsewhere in
        this same architecture) rather than silently normalized/merged."""
        with Session(_engine()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("case-variant"), source_locator="p1", raw_text="x",
                airport_names=frozenset({"Foo Regional Airport", "foo regional airport"}),
            )
            assert len(fragment.airport_names) == 2  # sanity: genuinely two distinct set elements
            result = resolve_or_persist_discovery_identity(session, _meta("case-variant-doc"), fragment, [])
            assert result.outcome == DiscoveryIdentityOutcome.UNRESOLVED_IDENTITY
            assert session.query(UnknownAirportCandidate).count() == 0

    def test_exact_repeated_mention_collapses_via_frozenset_and_still_forms(self):
        """The SAME string mentioned multiple times in raw text already
        collapses to one frozenset element before this module ever sees
        it - not a special case this module itself handles, but worth
        proving explicitly."""
        with Session(_engine()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("exact-repeat"), source_locator="p1", raw_text="x",
                airport_names=frozenset({"Foo Regional Airport", "Foo Regional Airport"}),
            )
            assert len(fragment.airport_names) == 1
            result = resolve_or_persist_discovery_identity(session, _meta("exact-repeat-doc"), fragment, [])
            assert result.outcome == DiscoveryIdentityOutcome.UNKNOWN_AIRPORT_CANDIDATE

    def test_one_name_with_conflicting_location_claims_still_forms_ignoring_locations(self):
        """Locations are never populated onto the candidate at all (the
        module's own documented extraction-contract boundary) - their
        presence, even if internally conflicting, has no bearing on
        formability."""
        with Session(_engine()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("conflicting-loc"), source_locator="p1", raw_text="x",
                airport_names=frozenset({"Foo Regional Airport"}), locations=frozenset({"City A", "City B"}),
            )
            result = resolve_or_persist_discovery_identity(session, _meta("conflicting-loc-doc"), fragment, [])
            assert result.outcome == DiscoveryIdentityOutcome.UNKNOWN_AIRPORT_CANDIDATE
            candidate = session.get(UnknownAirportCandidate, result.unknown_airport_candidate_id)
            assert candidate.raw_city is None
            assert candidate.raw_state_region is None
            assert candidate.raw_country is None

    def test_one_name_with_multiple_runway_references_joins_them_audit_only(self):
        with Session(_engine()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("multi-runway"), source_locator="p1", raw_text="x",
                airport_names=frozenset({"Foo Regional Airport"}), runway_ends=frozenset({"18", "36"}),
            )
            result = resolve_or_persist_discovery_identity(session, _meta("multi-runway-doc"), fragment, [])
            candidate = session.get(UnknownAirportCandidate, result.unknown_airport_candidate_id)
            assert set(candidate.raw_runway_designation.split(", ")) == {"18", "36"}


class TestWeakIdentity:
    def test_no_reliable_identity_remains_unresolved(self):
        with Session(_engine()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("weak"), source_locator="p1",
                raw_text="the airport will install EMAS",
            )
            result = resolve_or_persist_discovery_identity(session, _meta("weak-doc"), fragment, [])

            assert result.outcome == DiscoveryIdentityOutcome.UNRESOLVED_IDENTITY
            assert result.unknown_airport_candidate_id is None
            assert session.query(UnknownAirportCandidate).count() == 0
            assertion = session.get(SourceAssertion, result.source_assertion_id)
            assert assertion.airport_id is None
            assert assertion.unknown_airport_candidate_id is None

    def test_weak_identity_against_known_candidates_still_unresolved_not_rejected_into_candidate(self):
        with Session(_engine()) as session:
            known = _seed_airport(session, faa_code="BOS", name="Boston Logan International Airport")
            candidate_airport = _candidate(known, identifiers=frozenset({"BOS"}))
            fragment = CandidateFragment(
                artifact_identity=_artifact("weak2"), source_locator="p1",
                raw_text="Arresting system grant awarded.",
            )
            result = resolve_or_persist_discovery_identity(session, _meta("weak2-doc"), fragment, [candidate_airport])
            assert result.outcome == DiscoveryIdentityOutcome.UNRESOLVED_IDENTITY
            assert session.query(UnknownAirportCandidate).count() == 0


# ---------------------------------------------------------------------------
# D. Ambiguous known match
# ---------------------------------------------------------------------------


class TestAmbiguousKnownMatch:
    def test_ambiguous_known_match_never_creates_a_candidate(self):
        with Session(_engine()) as session:
            bos = _seed_airport(session, faa_code="BOS", name="Boston Logan International Airport")
            orh = _seed_airport(session, faa_code="ORH", name="Worcester Regional")
            bos_c = _candidate(bos, known_issuers=frozenset({"Massport"}))
            orh_c = _candidate(orh, known_issuers=frozenset({"Massport"}))
            fragment = CandidateFragment(
                artifact_identity=_artifact("ambiguous"), source_locator="p1",
                raw_text="Massport capital bill covering Logan and Worcester Regional airfield safety work.",
                issuers=frozenset({"Massport"}),
            )
            result = resolve_or_persist_discovery_identity(session, _meta("ambig-doc"), fragment, [bos_c, orh_c])

            assert result.outcome == DiscoveryIdentityOutcome.AMBIGUOUS_KNOWN_IDENTITY
            assert result.attached_airport_id is None
            assert result.unknown_airport_candidate_id is None
            assert session.query(UnknownAirportCandidate).count() == 0
            assertion = session.get(SourceAssertion, result.source_assertion_id)
            assert assertion.airport_id is None
            assert assertion.unknown_airport_candidate_id is None


# ---------------------------------------------------------------------------
# Identity-precedence Option 3 (docs/architecture/rwi-uac3-identity-
# precedence-review.md S14/S16): an explicit, well-formed source-provided
# airport-name claim that no supplied candidate's own positive evidence
# corroborates must not let a merely coincidental runway-topology, issuer,
# or location match silently claim the evidence - routing must fall
# through to the existing, unmodified UnknownAirportCandidate path
# instead. Every test here reuses the guard's own already-computed
# positive_evidence facts (via resolve_or_persist_discovery_identity())-
# never a parallel or fuzzy name comparison.
# ---------------------------------------------------------------------------


class TestIdentityPrecedenceOption3:
    def test_real_anoka_shape_multi_topology_forms_unknown_candidate(self):
        """The exact real-world motivating case (Controlled Live Pilot
        5E/5F): an explicit airport name matching NEITHER of two
        topology-ambiguous known candidates must route to
        UNKNOWN_AIRPORT_CANDIDATE, not AMBIGUOUS_KNOWN_IDENTITY."""
        with Session(_engine()) as session:
            clinton = _seed_airport(session, name="Bill and Hillary Clinton National")
            waterbury = _seed_airport(session, name="Waterbury-Oxford")
            clinton_c = _candidate(clinton, canonical_runway_pairs=frozenset({"18/36"}))
            waterbury_c = _candidate(waterbury, canonical_runway_pairs=frozenset({"18/36"}))
            fragment = CandidateFragment(
                artifact_identity=_artifact("anoka"), source_locator="item-3.1",
                raw_text="Anoka County-Blaine Airport Runway 18-36 Pavement Reconstruction.",
                airport_names=frozenset({"Anoka County-Blaine Airport"}),
                runway_ends=frozenset({"18", "36"}), runway_pairs=frozenset({"18/36"}),
                issuers=frozenset({"Metropolitan Airports Commission"}),
            )
            result = resolve_or_persist_discovery_identity(session, _meta("anoka-doc"), fragment, [clinton_c, waterbury_c])

            assert result.outcome == DiscoveryIdentityOutcome.UNKNOWN_AIRPORT_CANDIDATE
            assert result.attachment_outcome == AttachmentOutcome.REVIEW_REQUIRED
            assert result.attached_airport_id is None
            assert result.unknown_airport_candidate_id is not None
            candidate_row = session.get(UnknownAirportCandidate, result.unknown_airport_candidate_id)
            assert candidate_row.raw_name == "Anoka County-Blaine Airport"
            assert session.query(Airport).count() == 2  # only the two pre-seeded known airports

    def test_single_wrong_topology_candidate_no_longer_silently_attaches(self):
        """S11 HIGH PRIORITY: the more serious defect the design review
        found - a SINGLE, non-ambiguous topology match with an explicit,
        unmatched name previously reached ATTACH_PROVISIONAL and silently
        attached to the wrong airport, with no human review at all. Must
        now route to UNKNOWN_AIRPORT_CANDIDATE instead."""
        with Session(_engine()) as session:
            unrelated = _seed_airport(session, name="Different Existing Airport")
            unrelated_c = _candidate(unrelated, canonical_runway_pairs=frozenset({"18/36"}))
            fragment = CandidateFragment(
                artifact_identity=_artifact("wrong-topology"), source_locator="p1",
                raw_text="Example New Airport Runway 18-36 Reconstruction.",
                airport_names=frozenset({"Example New Airport"}), runway_pairs=frozenset({"18/36"}),
            )
            result = resolve_or_persist_discovery_identity(session, _meta("wrong-doc"), fragment, [unrelated_c])

            assert result.outcome == DiscoveryIdentityOutcome.UNKNOWN_AIRPORT_CANDIDATE
            assert result.attachment_outcome == AttachmentOutcome.ATTACH_PROVISIONAL
            assert result.attached_airport_id is None  # MUST NOT silently attach to `unrelated`
            assert result.unknown_airport_candidate_id is not None
            assertion = session.get(SourceAssertion, result.source_assertion_id)
            assert assertion.airport_id is None

    def test_exact_known_name_with_unrelated_topology_collisions_unaffected(self):
        """Non-regression (S6/S9 item D): an EXACT name match on candidate
        X, with topology also matching unrelated Y, is deliberately left
        unchanged by Option 3 - X's own positive evidence includes NAME,
        so the override never engages, and cross-candidate ambiguity
        resolution (a separate, un-touched layer) still downgrades both
        to REVIEW_REQUIRED exactly as before."""
        with Session(_engine()) as session:
            alpha = _seed_airport(session, name="Alpha Field")
            beta = _seed_airport(session, name="Beta Field")
            alpha_c = _candidate(alpha, canonical_runway_pairs=frozenset({"18/36"}))
            beta_c = _candidate(beta, canonical_runway_pairs=frozenset({"18/36"}))
            fragment = CandidateFragment(
                artifact_identity=_artifact("exact-name-collision"), source_locator="p1",
                raw_text="Alpha Field Runway 18-36 Reconstruction.",
                airport_names=frozenset({"Alpha Field"}), runway_pairs=frozenset({"18/36"}),
            )
            result = resolve_or_persist_discovery_identity(session, _meta("exact-doc"), fragment, [alpha_c, beta_c])

            assert result.outcome == DiscoveryIdentityOutcome.AMBIGUOUS_KNOWN_IDENTITY
            assert result.unknown_airport_candidate_id is None
            assert session.query(UnknownAirportCandidate).count() == 0

    def test_identifier_contradiction_veto_unaffected_by_uncorroborated_name(self):
        """S9: explicit unknown name + an identifier that CONTRADICTS the
        one supplied candidate + topology match - the identifier's
        existing, unconditional veto (REJECT_CROSS_AIRPORT) must fire
        exactly as before; Option 3's override never even gets a chance
        to engage, since REJECT_CROSS_AIRPORT was never in its trigger
        set. The fragment's own uncorroborated name still independently
        makes it formable, so it still reaches UNKNOWN_AIRPORT_CANDIDATE -
        via the pre-existing, unmodified REJECT_CROSS_AIRPORT path, not
        Option 3's new one."""
        with Session(_engine()) as session:
            known = _seed_airport(session, faa_code="ABC", name="Alpha Field")
            known_c = _candidate(known, identifiers=frozenset({"ABC"}), canonical_runway_pairs=frozenset({"18/36"}))
            fragment = CandidateFragment(
                artifact_identity=_artifact("identifier-contradiction"), source_locator="p1",
                raw_text="Example New Airport identifier XYZ runway 18-36.",
                airport_names=frozenset({"Example New Airport"}),
                airport_identifiers=frozenset({"XYZ"}), runway_pairs=frozenset({"18/36"}),
            )
            result = resolve_or_persist_discovery_identity(session, _meta("id-contra-doc"), fragment, [known_c])

            assert result.attachment_outcome == AttachmentOutcome.REJECT_CROSS_AIRPORT
            assert result.outcome == DiscoveryIdentityOutcome.UNKNOWN_AIRPORT_CANDIDATE
            assert result.attached_airport_id is None
            assert "identity-precedence Option 3" not in result.reason  # pre-existing path, not the new override

    def test_exact_identifier_known_match_with_unrelated_name_unaffected(self):
        """S9: an exact identifier match must remain untouchable by
        Option 3, even when the same fragment carries an unrelated,
        non-matching name - identifiers are the one category the design
        review's own verdict says needs no change (already correctly
        outranks name/issuer/location, including auto-veto on mismatch)."""
        with Session(_engine()) as session:
            alpha = _seed_airport(session, faa_code="ABC", name="Alpha Field")
            alpha_c = _candidate(alpha, identifiers=frozenset({"ABC"}))
            fragment = CandidateFragment(
                artifact_identity=_artifact("identifier-known-match"), source_locator="p1",
                raw_text="Gamma Field ABC identifier reference.",
                airport_identifiers=frozenset({"ABC"}), airport_names=frozenset({"Gamma Field"}),
            )
            result = resolve_or_persist_discovery_identity(session, _meta("id-known-doc"), fragment, [alpha_c])

            assert result.outcome == DiscoveryIdentityOutcome.KNOWN_CANONICAL_ATTACHMENT
            assert result.attached_airport_id == alpha.id
            assert result.unknown_airport_candidate_id is None

    def test_issuer_and_topology_confirmed_with_unrelated_name_no_longer_silently_attaches(self):
        """S9 item I: the deeper risk found while empirically testing the
        design review's own precedence matrix (row L) - issuer + topology
        alone can reach ATTACH_CONFIRMED, silently ignoring an unrelated,
        uncorroborated explicit name in the same evidence bag. Must now
        route to UNKNOWN_AIRPORT_CANDIDATE instead."""
        with Session(_engine()) as session:
            alpha = _seed_airport(session, name="Alpha Field")
            alpha_c = _candidate(alpha, canonical_runway_pairs=frozenset({"18/36"}), known_issuers=frozenset({"Metropolitan Airports Commission"}))
            fragment = CandidateFragment(
                artifact_identity=_artifact("issuer-topology-unrelated-name"), source_locator="p1",
                raw_text="Gamma Field runway 18-36 Metropolitan Airports Commission project.",
                issuers=frozenset({"Metropolitan Airports Commission"}), runway_pairs=frozenset({"18/36"}),
                airport_names=frozenset({"Gamma Field"}),
            )
            result = resolve_or_persist_discovery_identity(session, _meta("issuer-topo-doc"), fragment, [alpha_c])

            assert result.outcome == DiscoveryIdentityOutcome.UNKNOWN_AIRPORT_CANDIDATE
            assert result.attachment_outcome == AttachmentOutcome.ATTACH_CONFIRMED
            assert result.attached_airport_id is None
            assert "identity-precedence Option 3" in result.reason

    def test_location_only_with_topology_no_unintended_override(self):
        """S6 item J / mission's own location edge case: a location match
        plus topology reaching ATTACH_CONFIRMED, with NO competing name
        evidence at all in the fragment - the override must never engage
        merely because location (not name) was the second category, since
        there is no uncorroborated name claim to protect against here."""
        with Session(_engine()) as session:
            alpha = _seed_airport(session, name="Alpha Field", city="Springfield")
            alpha_c = _candidate(alpha, canonical_runway_pairs=frozenset({"18/36"}), city_location="Springfield")
            fragment = CandidateFragment(
                artifact_identity=_artifact("location-only"), source_locator="p1",
                raw_text="Springfield runway 18-36 improvement project.",
                locations=frozenset({"Springfield"}), runway_pairs=frozenset({"18/36"}),
            )
            result = resolve_or_persist_discovery_identity(session, _meta("location-doc"), fragment, [alpha_c])

            assert result.outcome == DiscoveryIdentityOutcome.KNOWN_CANONICAL_ATTACHMENT
            assert result.attached_airport_id == alpha.id
            assert result.unknown_airport_candidate_id is None

    def test_two_explicit_names_no_unknown_candidate_formed(self):
        """S8 multiple-name firewall: with no known candidate accepting
        the evidence at all (best_outcome already in the "no known match"
        bucket, so Option 3's override never needs to engage), UAC3's own
        pre-existing "exactly one name is formable" rule still fails
        closed for two names - Option 3 never widens that rule."""
        with Session(_engine()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("two-names"), source_locator="p1",
                raw_text="Example Regional Airport and Sample Municipal Airport runway 18-36.",
                airport_names=frozenset({"Example Regional Airport", "Sample Municipal Airport"}),
                runway_pairs=frozenset({"18/36"}),
            )
            result = resolve_or_persist_discovery_identity(session, _meta("two-names-doc"), fragment, [])

            assert result.outcome == DiscoveryIdentityOutcome.UNRESOLVED_IDENTITY
            assert result.unknown_airport_candidate_id is None
            assert session.query(UnknownAirportCandidate).count() == 0

    def test_two_explicit_names_with_topology_match_still_attaches_known_unaffected(self):
        """Companion case: when topology DOES genuinely match a supplied
        candidate, that known-match path proceeds exactly as before,
        completely independent of the fragment's own name multiplicity -
        multi-name ambiguity is a "no known match" branch concern only,
        never inspected on the known-match path either before or after
        Option 3."""
        with Session(_engine()) as session:
            unrelated = _seed_airport(session, name="Different Existing Airport")
            unrelated_c = _candidate(unrelated, canonical_runway_pairs=frozenset({"18/36"}))
            fragment = CandidateFragment(
                artifact_identity=_artifact("two-names-topology"), source_locator="p1",
                raw_text="Example Regional Airport and Sample Municipal Airport runway 18-36.",
                airport_names=frozenset({"Example Regional Airport", "Sample Municipal Airport"}),
                runway_pairs=frozenset({"18/36"}),
            )
            result = resolve_or_persist_discovery_identity(session, _meta("two-names-topo-doc"), fragment, [unrelated_c])

            assert result.outcome == DiscoveryIdentityOutcome.KNOWN_CANONICAL_ATTACHMENT
            assert result.attached_airport_id == unrelated.id
            assert result.unknown_airport_candidate_id is None

    def test_no_downstream_side_effects_from_override(self):
        """S12 EMAS relevance firewall / information firewall: the
        override, when it fires, still only ever produces the same
        UnknownAirportCandidate + candidate-linked SourceAssertion +
        EvidenceBag snapshot shape as any other UNKNOWN_AIRPORT_CANDIDATE
        outcome - no Airport, no Signal, no EB4/EB5, no intelligence
        review, no promotion, no publish, no invented relevance field."""
        with Session(_engine()) as session:
            unrelated = _seed_airport(session, name="Different Existing Airport")
            unrelated_c = _candidate(unrelated, canonical_runway_pairs=frozenset({"18/36"}))
            fragment = CandidateFragment(
                artifact_identity=_artifact("no-side-effects"), source_locator="p1",
                raw_text="Example New Airport Runway 18-36 Reconstruction.",
                airport_names=frozenset({"Example New Airport"}), runway_pairs=frozenset({"18/36"}),
            )
            result = resolve_or_persist_discovery_identity(session, _meta("no-side-effects-doc"), fragment, [unrelated_c])

            assert result.outcome == DiscoveryIdentityOutcome.UNKNOWN_AIRPORT_CANDIDATE
            assert session.query(Airport).count() == 1  # only the pre-seeded known airport
            assert session.query(Signal).count() == 0
            candidate_row = session.get(UnknownAirportCandidate, result.unknown_airport_candidate_id)
            assert not hasattr(candidate_row, "emas_relevant")


# ---------------------------------------------------------------------------
# F/G. Repeated exact discovery / near-duplicate (no fuzzy merge)
# ---------------------------------------------------------------------------


class TestRepeatedAndNearDuplicateDiscovery:
    def test_repeated_exact_discovery_reuses_one_candidate(self):
        with Session(_engine()) as session:
            frag1 = CandidateFragment(
                artifact_identity=_artifact("foo-a"), source_locator="p1", raw_text="Foo Regional Airport memo A.",
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            frag2 = CandidateFragment(
                artifact_identity=_artifact("foo-b"), source_locator="p1", raw_text="Foo Regional Airport memo B.",
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            first = resolve_or_persist_discovery_identity(session, _meta("foo-doc-a", producer="adapter-a"), frag1, [])
            second = resolve_or_persist_discovery_identity(session, _meta("foo-doc-b", producer="adapter-b"), frag2, [])

            assert first.unknown_airport_candidate_created is True
            assert second.unknown_airport_candidate_created is False
            assert first.unknown_airport_candidate_id == second.unknown_airport_candidate_id
            assert session.query(UnknownAirportCandidate).count() == 1
            assert session.query(SourceAssertion).filter_by(unknown_airport_candidate_id=first.unknown_airport_candidate_id).count() == 2
            # Neither evidence row overwritten.
            texts = {
                a.raw_relevant_text for a in
                session.query(SourceAssertion).filter_by(unknown_airport_candidate_id=first.unknown_airport_candidate_id).all()
            }
            assert texts == {"Foo Regional Airport memo A.", "Foo Regional Airport memo B."}

    def test_near_duplicate_name_does_not_fuzzy_merge(self):
        with Session(_engine()) as session:
            frag1 = CandidateFragment(
                artifact_identity=_artifact("near-a"), source_locator="p1", raw_text="Foo Regional Airport memo.",
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            frag2 = CandidateFragment(
                artifact_identity=_artifact("near-b"), source_locator="p1", raw_text="Foo Regional Airport Authority memo.",
                airport_names=frozenset({"Foo Regional Airport Authority"}),
            )
            first = resolve_or_persist_discovery_identity(session, _meta("near-doc-a"), frag1, [])
            second = resolve_or_persist_discovery_identity(session, _meta("near-doc-b"), frag2, [])

            assert first.unknown_airport_candidate_id != second.unknown_airport_candidate_id
            assert second.unknown_airport_candidate_created is True
            assert session.query(UnknownAirportCandidate).count() == 2


# ---------------------------------------------------------------------------
# H. Existing-airport false negative
# ---------------------------------------------------------------------------


class TestExistingAirportFalseNegative:
    def test_real_airport_exists_but_not_supplied_as_candidate_routes_to_unknown_candidate(self):
        """The canonical Airport genuinely exists in the DB, but whoever
        assembled `candidate_airports` for this call didn't include it
        (a spelling/identity variant miss upstream of this module, which
        UAC3 does not itself search the whole catalogue to fix). Must not
        create a duplicate canonical Airport; a human can later resolve
        via MATCH_EXISTING_AIRPORT."""
        with Session(_engine()) as session:
            real_airport = _seed_airport(session, faa_code="XYZ", name="Foo Regional Airport")
            fragment = CandidateFragment(
                artifact_identity=_artifact("false-negative"), source_locator="p1",
                raw_text="Foo Regional Airport EMAS memo.", airport_names=frozenset({"Foo Regional Airport"}),
            )
            # Deliberately NOT including real_airport in the candidate list.
            result = resolve_or_persist_discovery_identity(session, _meta("fn-doc"), fragment, [])

            assert result.outcome == DiscoveryIdentityOutcome.UNKNOWN_AIRPORT_CANDIDATE
            assert session.query(Airport).count() == 1  # no duplicate created
            candidate = session.get(UnknownAirportCandidate, result.unknown_airport_candidate_id)
            assert candidate.resolved_airport_id is None
            assert candidate.raw_name == "Foo Regional Airport"

            # A human can still resolve this later via the existing,
            # unmodified UAC1 review mechanism.
            from app.services.unknown_airport_candidate_persistence import record_unknown_airport_candidate_review
            review = record_unknown_airport_candidate_review(
                session, candidate, action="MATCH_EXISTING_AIRPORT", reason="Same airport, name variant.",
                reviewer="human:test", matched_airport_id=real_airport.id,
            )
            assert review.matched_airport_id == real_airport.id
            assert session.query(Airport).count() == 1  # still no duplicate


# ---------------------------------------------------------------------------
# I. multiple evidence -> one candidate (already covered above; additional
# convergence-provider-neutrality case)
# ---------------------------------------------------------------------------


class TestMultipleEvidenceOneCandidate:
    def test_three_independent_producers_converge_on_one_candidate(self):
        with Session(_engine()) as session:
            for i, producer in enumerate(("adapter-alpha", "adapter-beta", "adapter-gamma")):
                fragment = CandidateFragment(
                    artifact_identity=_artifact(f"conv-{i}"), source_locator="p1",
                    raw_text=f"Foo Regional Airport evidence {i}.", airport_names=frozenset({"Foo Regional Airport"}),
                )
                resolve_or_persist_discovery_identity(session, _meta(f"conv-doc-{i}", producer=producer), fragment, [])

            assert session.query(UnknownAirportCandidate).count() == 1
            candidate = session.query(UnknownAirportCandidate).one()
            assert session.query(SourceAssertion).filter_by(unknown_airport_candidate_id=candidate.id).count() == 3


# ---------------------------------------------------------------------------
# J/K. Transaction ownership / rollback
# ---------------------------------------------------------------------------


class TestExactlyOnePersistencePath:
    """Review §9: instrumented, spy-based proof (not merely code-reading)
    that no execution path calls both persist_discovery_fragment() and
    persist_candidate_linked_source_assertion() for the same fragment."""

    def _spy(self, monkeypatch):
        import app.services.unknown_airport_discovery_integration as integration_module

        calls = {"persist_discovery_fragment": 0, "persist_candidate_linked_source_assertion": 0}
        original_a = integration_module.persist_discovery_fragment
        original_b = integration_module.persist_candidate_linked_source_assertion

        def spy_a(*args, **kwargs):
            calls["persist_discovery_fragment"] += 1
            return original_a(*args, **kwargs)

        def spy_b(*args, **kwargs):
            calls["persist_candidate_linked_source_assertion"] += 1
            return original_b(*args, **kwargs)

        monkeypatch.setattr(integration_module, "persist_discovery_fragment", spy_a)
        monkeypatch.setattr(integration_module, "persist_candidate_linked_source_assertion", spy_b)
        return calls

    def test_known_match_calls_only_persist_discovery_fragment(self, monkeypatch):
        calls = self._spy(monkeypatch)
        with Session(_engine()) as session:
            airport = _seed_airport(session, faa_code="BOS", name="Boston Logan International Airport")
            candidate_airport = _candidate(airport, identifiers=frozenset({"BOS"}))
            fragment = CandidateFragment(
                artifact_identity=_artifact("spy-known"), source_locator="p1", raw_text="BOS work.",
                airport_identifiers=frozenset({"BOS"}),
            )
            resolve_or_persist_discovery_identity(session, _meta("spy-known-doc"), fragment, [candidate_airport])
        assert calls == {"persist_discovery_fragment": 1, "persist_candidate_linked_source_assertion": 0}

    def test_unknown_candidate_calls_only_persist_candidate_linked(self, monkeypatch):
        calls = self._spy(monkeypatch)
        with Session(_engine()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("spy-unknown"), source_locator="p1", raw_text="Foo Regional Airport memo.",
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            resolve_or_persist_discovery_identity(session, _meta("spy-unknown-doc"), fragment, [])
        assert calls == {"persist_discovery_fragment": 0, "persist_candidate_linked_source_assertion": 1}

    def test_unresolved_calls_only_persist_discovery_fragment(self, monkeypatch):
        calls = self._spy(monkeypatch)
        with Session(_engine()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("spy-unresolved"), source_locator="p1", raw_text="Vague EMAS mention.",
            )
            resolve_or_persist_discovery_identity(session, _meta("spy-unresolved-doc"), fragment, [])
        assert calls == {"persist_discovery_fragment": 1, "persist_candidate_linked_source_assertion": 0}

    def test_ambiguous_known_calls_only_persist_discovery_fragment(self, monkeypatch):
        calls = self._spy(monkeypatch)
        with Session(_engine()) as session:
            bos = _seed_airport(session, faa_code="BOS", name="Boston Logan International Airport")
            orh = _seed_airport(session, faa_code="ORH", name="Worcester Regional")
            bos_c = _candidate(bos, known_issuers=frozenset({"Massport"}))
            orh_c = _candidate(orh, known_issuers=frozenset({"Massport"}))
            fragment = CandidateFragment(
                artifact_identity=_artifact("spy-ambiguous"), source_locator="p1",
                raw_text="Massport bill covering Logan and Worcester Regional.", issuers=frozenset({"Massport"}),
            )
            resolve_or_persist_discovery_identity(session, _meta("spy-ambiguous-doc"), fragment, [bos_c, orh_c])
        assert calls == {"persist_discovery_fragment": 1, "persist_candidate_linked_source_assertion": 0}

    def test_no_duplicate_source_assertion_produced_across_repeated_calls(self, monkeypatch):
        """Even under repeated calls for the identical fragment (replay),
        no duplicate SourceAssertion is ever produced because both
        branches ran - only ever one branch runs per call, and repeats
        idempotently reuse the same row."""
        calls = self._spy(monkeypatch)
        with Session(_engine()) as session:
            fragment_factory = lambda: CandidateFragment(
                artifact_identity=_artifact("spy-replay"), source_locator="p1", raw_text="Foo Regional Airport memo.",
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            resolve_or_persist_discovery_identity(session, _meta("spy-replay-doc"), fragment_factory(), [])
            resolve_or_persist_discovery_identity(session, _meta("spy-replay-doc"), fragment_factory(), [])
            assert session.query(SourceAssertion).count() == 1
        assert calls["persist_discovery_fragment"] == 0
        assert calls["persist_candidate_linked_source_assertion"] == 2


class TestTransactionOwnership:
    def test_no_hidden_commit_rollback_undoes_everything(self):
        with Session(_engine()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("rollback"), source_locator="p1", raw_text="Foo Regional Airport memo.",
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            resolve_or_persist_discovery_identity(session, _meta("rollback-doc"), fragment, [])
            session.rollback()

            assert session.query(UnknownAirportCandidate).count() == 0
            assert session.query(SourceAssertion).count() == 0
            assert session.query(Source).count() == 0

    def test_candidate_creation_and_assertion_persistence_share_one_atomic_unit(self):
        """§20: if candidate creation succeeds but assertion persistence
        somehow fails before commit, caller rollback must remove both -
        proven by injecting a failure between the two calls this
        orchestration makes and confirming a real rollback leaves no
        orphaned candidate."""
        with Session(_engine()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("partial"), source_locator="p1", raw_text="Foo Regional Airport memo.",
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            import app.services.unknown_airport_discovery_integration as integration_module

            original = integration_module.persist_candidate_linked_source_assertion

            def _crash(*args, **kwargs):
                raise RuntimeError("simulated failure after candidate creation")

            integration_module.persist_candidate_linked_source_assertion = _crash
            try:
                with pytest.raises(RuntimeError, match="simulated failure"):
                    resolve_or_persist_discovery_identity(session, _meta("partial-doc"), fragment, [])
            finally:
                integration_module.persist_candidate_linked_source_assertion = original
            session.rollback()

            assert session.query(UnknownAirportCandidate).count() == 0
            assert session.query(SourceAssertion).count() == 0

    def test_existing_candidate_unchanged_when_assertion_persistence_fails(self):
        """The converse: an ALREADY-committed candidate must survive a
        later, failed attempt to link new evidence to it."""
        with Session(_engine()) as session:
            frag1 = CandidateFragment(
                artifact_identity=_artifact("existing-a"), source_locator="p1", raw_text="Foo Regional Airport memo A.",
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            first = resolve_or_persist_discovery_identity(session, _meta("existing-doc-a"), frag1, [])
            session.commit()
            candidate_id = first.unknown_airport_candidate_id

            frag2 = CandidateFragment(
                artifact_identity=_artifact("existing-b"), source_locator="p1", raw_text="Foo Regional Airport memo B.",
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            import app.services.unknown_airport_discovery_integration as integration_module

            original = integration_module.persist_candidate_linked_source_assertion

            def _crash(*args, **kwargs):
                raise RuntimeError("simulated failure linking second assertion")

            integration_module.persist_candidate_linked_source_assertion = _crash
            try:
                with pytest.raises(RuntimeError, match="simulated failure"):
                    resolve_or_persist_discovery_identity(session, _meta("existing-doc-b"), frag2, [])
            finally:
                integration_module.persist_candidate_linked_source_assertion = original
            session.rollback()

            assert session.query(UnknownAirportCandidate).count() == 1
            surviving = session.get(UnknownAirportCandidate, candidate_id)
            assert surviving is not None
            assert surviving.raw_name == "Foo Regional Airport"
            assert session.query(SourceAssertion).filter_by(unknown_airport_candidate_id=candidate_id).count() == 1


# ---------------------------------------------------------------------------
# L. No canonical side effects
# ---------------------------------------------------------------------------


class TestNoCanonicalSideEffects:
    def test_unknown_candidate_route_creates_no_canonical_rows(self):
        with Session(_engine()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("canon"), source_locator="p1", raw_text="Foo Regional Airport memo.",
                airport_names=frozenset({"Foo Regional Airport"}), runway_ends=frozenset({"18"}),
            )
            resolve_or_persist_discovery_identity(session, _meta("canon-doc"), fragment, [])

            assert session.query(Airport).count() == 0
            assert session.query(Runway).count() == 0
            assert session.query(RunwayEnd).count() == 0
            assert session.query(Installation).count() == 0
            assert session.query(PhysicalInstallationIdentity).count() == 0
            assert session.query(Signal).count() == 0
            assert session.query(UnknownAirportCandidateReview).count() == 0

    def test_no_construction_of_canonical_orm_objects_in_module_source(self):
        import app.services.unknown_airport_discovery_integration as integration_module

        tree = ast.parse(inspect_module.getsource(integration_module))
        forbidden = {"Airport", "Runway", "RunwayEnd", "Installation", "Signal"}
        found = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in forbidden
        }
        assert found == set()


# ---------------------------------------------------------------------------
# M. Migration-chain parity - real migrations, not create_all()
# ---------------------------------------------------------------------------


class TestMigrationChainParity:
    def test_end_to_end_against_genuinely_migrated_schema(self, tmp_path):
        db = tmp_path / "uac3_parity.db"
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        engine.dispose()
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DROP TABLE unknown_airport_candidate_reviews")
        conn.execute("DROP TABLE unknown_airport_candidates")
        replacement = "source_assertions__presetup"
        conn.execute(uac2b_migration._pre_uac2b_create_table_sql(replacement))
        quoted = ", ".join(f'"{c}"' for c in uac2b_migration._PRE_UAC2B_COLUMNS)
        conn.execute(f'INSERT INTO "{replacement}" ({quoted}) SELECT {quoted} FROM source_assertions')
        conn.execute("DROP TABLE source_assertions")
        conn.execute(f'ALTER TABLE "{replacement}" RENAME TO source_assertions')
        conn.commit()
        conn.close()

        uac2a_migration.upgrade(db)
        uac2b_migration.upgrade(db)

        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("migrated"), source_locator="p1", raw_text="Foo Regional Airport memo.",
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            result = resolve_or_persist_discovery_identity(session, _meta("migrated-doc"), fragment, [])
            session.commit()

            assert result.outcome == DiscoveryIdentityOutcome.UNKNOWN_AIRPORT_CANDIDATE
            assert session.query(UnknownAirportCandidate).count() == 1
            assertion = session.get(SourceAssertion, result.source_assertion_id)
            assert assertion.unknown_airport_candidate_id == result.unknown_airport_candidate_id
        engine.dispose()


# ---------------------------------------------------------------------------
# N. Unicode / international identity
# ---------------------------------------------------------------------------


class TestInternationalIdentity:
    def test_unicode_airport_name_forms_a_candidate(self):
        with Session(_engine()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("intl"), source_locator="p1",
                raw_text="羽田空港 滑走路16L/34R エンジニアド・マテリアル・アレスティング・システム（EMAS）",
                airport_names=frozenset({"羽田空港"}), language="ja",
            )
            result = resolve_or_persist_discovery_identity(session, _meta("intl-doc"), fragment, [])
            assert result.outcome == DiscoveryIdentityOutcome.UNKNOWN_AIRPORT_CANDIDATE
            candidate = session.get(UnknownAirportCandidate, result.unknown_airport_candidate_id)
            assert candidate.raw_name == "羽田空港"

    def test_accented_name_and_generic_producer_label(self):
        with Session(_engine()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("accent"), source_locator="p1",
                raw_text="Aéroport Régional Exemple - EMAS study.", airport_names=frozenset({"Aéroport Régional Exemple"}),
            )
            result = resolve_or_persist_discovery_identity(
                session, _meta("accent-doc", producer="generic-acquisition-producer"), fragment, [],
            )
            assert result.outcome == DiscoveryIdentityOutcome.UNKNOWN_AIRPORT_CANDIDATE


# ---------------------------------------------------------------------------
# O. Multi-airport fragment safety (also covered by the smoke test above;
# additional explicit case)
# ---------------------------------------------------------------------------


class TestMultiAirportFragmentSafety:
    def test_two_names_in_one_fragment_never_blended_stays_unresolved(self):
        with Session(_engine()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("multi"), source_locator="p1",
                raw_text="Foo Regional Airport and Bar Municipal Airport both plan EMAS work.",
                airport_names=frozenset({"Foo Regional Airport", "Bar Municipal Airport"}),
            )
            result = resolve_or_persist_discovery_identity(session, _meta("multi-doc"), fragment, [])
            assert result.outcome == DiscoveryIdentityOutcome.UNRESOLVED_IDENTITY
            assert session.query(UnknownAirportCandidate).count() == 0

    def test_multiple_fragments_from_one_document_operate_independently(self):
        """If extraction already yields separate fragments (the existing,
        expected shape for a multi-airport document), UAC3 operates per
        fragment and may legitimately form two separate candidates."""
        with Session(_engine()) as session:
            meta = _meta("shared-multi-doc")
            frag_a = CandidateFragment(
                artifact_identity=_artifact("shared"), source_locator="section-1", raw_text="Foo Regional Airport EMAS plan.",
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            frag_b = CandidateFragment(
                artifact_identity=_artifact("shared"), source_locator="section-2", raw_text="Bar Municipal Airport EMAS plan.",
                airport_names=frozenset({"Bar Municipal Airport"}),
            )
            r_a = resolve_or_persist_discovery_identity(session, meta, frag_a, [])
            r_b = resolve_or_persist_discovery_identity(session, meta, frag_b, [])
            assert r_a.outcome == DiscoveryIdentityOutcome.UNKNOWN_AIRPORT_CANDIDATE
            assert r_b.outcome == DiscoveryIdentityOutcome.UNKNOWN_AIRPORT_CANDIDATE
            assert r_a.unknown_airport_candidate_id != r_b.unknown_airport_candidate_id
            assert session.query(UnknownAirportCandidate).count() == 2


# ---------------------------------------------------------------------------
# P/Q/R. FH-F2/FH-F3 candidate-linked integration (end-to-end, real
# SourceAssertion rows, not just the unit-level rule tests)
# ---------------------------------------------------------------------------


class TestFleetHealthFullSixCaseMatrix:
    """Review §14: independently reconstructs FH-F2/FH-F3's full 6-case
    matrix (canonical/candidate/truly-unattributed x reviewed/unreviewed)
    against real, persisted SourceAssertion rows and the actual query
    construction in _build_source_assertion_review_states() - not just
    synthetic SourceAssertionReviewStateFact literals."""

    def test_full_six_case_matrix_at_real_query_and_rule_level(self):
        from app.services.fleet_health_check import _build_source_assertion_review_states
        from app.services.fleet_health_review_rules import evaluate_fh_f2, evaluate_fh_f3

        with Session(_engine()) as session:
            airport = _seed_airport(session, faa_code="BOS", name="Boston Logan International Airport")
            source = Source(title="s", source_type="web_discovery")
            session.add(source)
            session.flush()
            candidate = find_or_create_unknown_airport_candidate(session, raw_name="Foo", raw_country="X").candidate
            session.flush()

            a = SourceAssertion(source_id=source.id, airport_id=airport.id, assertion_type="project_construction", source_record_identifier="A", review_state="reviewed")
            b = SourceAssertion(source_id=source.id, airport_id=airport.id, assertion_type="project_construction", source_record_identifier="B", review_state="unreviewed")
            c = SourceAssertion(source_id=source.id, unknown_airport_candidate_id=candidate.id, assertion_type="project_construction", source_record_identifier="C", review_state="reviewed")
            d = SourceAssertion(source_id=source.id, unknown_airport_candidate_id=candidate.id, assertion_type="project_construction", source_record_identifier="D", review_state="unreviewed")
            e = SourceAssertion(source_id=source.id, assertion_type="project_construction", source_record_identifier="E", review_state="reviewed")
            f = SourceAssertion(source_id=source.id, assertion_type="project_construction", source_record_identifier="F", review_state="unreviewed")
            session.add_all([a, b, c, d, e, f])
            session.commit()

            facts = _build_source_assertion_review_states(session)
            fact_ids = {fact.assertion_id for fact in facts}

            # A/B (canonical-attributed): must never even appear as facts,
            # regardless of review_state - unaffected by UAC3, pre-existing
            # behavior (airport_id IS NULL filter in the fact-builder).
            assert a.id not in fact_ids
            assert b.id not in fact_ids

            # C/D (candidate-attributed): appear as facts, but neither
            # rule fires for them.
            assert c.id in fact_ids
            assert d.id in fact_ids

            # E/F (truly unattributed): appear as facts, and fire exactly
            # as they always have.
            assert e.id in fact_ids
            assert f.id in fact_ids

            f2_findings = evaluate_fh_f2(facts)
            f3_findings = evaluate_fh_f3(facts)

            # Only F (unattributed + unreviewed) fires FH-F2.
            assert {finding.entity_ids[0] for finding in f2_findings} == {f.id}
            # Only E (unattributed + reviewed) fires FH-F3.
            assert {finding.entity_ids[0] for finding in f3_findings} == {e.id}


class TestFleetHealthCandidateLinkedIntegration:
    def test_fh_f2_skips_unreviewed_candidate_linked_row(self):
        from app.services.fleet_health_check import _build_source_assertion_review_states
        from app.services.fleet_health_review_rules import evaluate_fh_f2

        with Session(_engine()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("fh-f2"), source_locator="p1", raw_text="Foo Regional Airport memo.",
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            resolve_or_persist_discovery_identity(session, _meta("fh-f2-doc"), fragment, [])
            session.commit()

            facts = _build_source_assertion_review_states(session)
            findings = evaluate_fh_f2(facts)
            assert findings == ()

    def test_fh_f3_skips_reviewed_candidate_linked_row(self):
        from app.services.fleet_health_check import _build_source_assertion_review_states
        from app.services.fleet_health_review_rules import evaluate_fh_f3

        with Session(_engine()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("fh-f3"), source_locator="p1", raw_text="Foo Regional Airport memo.",
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            result = resolve_or_persist_discovery_identity(session, _meta("fh-f3-doc"), fragment, [])
            assertion = session.get(SourceAssertion, result.source_assertion_id)
            assertion.review_state = "reviewed"
            session.commit()

            facts = _build_source_assertion_review_states(session)
            findings = evaluate_fh_f3(facts)
            assert findings == ()

    def test_fh_f2_still_fires_for_truly_unattributed_row_alongside_candidate_linked_one(self):
        from app.services.fleet_health_check import _build_source_assertion_review_states
        from app.services.fleet_health_review_rules import evaluate_fh_f2

        with Session(_engine()) as session:
            # Candidate-linked row.
            fragment = CandidateFragment(
                artifact_identity=_artifact("fh-f2-mixed-a"), source_locator="p1", raw_text="Foo Regional Airport memo.",
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            resolve_or_persist_discovery_identity(session, _meta("fh-f2-mixed-doc-a"), fragment, [])
            # Genuinely unattributed row.
            weak_fragment = CandidateFragment(
                artifact_identity=_artifact("fh-f2-mixed-b"), source_locator="p1", raw_text="Vague EMAS mention.",
            )
            unattributed = resolve_or_persist_discovery_identity(session, _meta("fh-f2-mixed-doc-b"), weak_fragment, [])
            session.commit()

            facts = _build_source_assertion_review_states(session)
            findings = evaluate_fh_f2(facts)
            assert len(findings) == 1
            assert findings[0].entity_ids == (unattributed.source_assertion_id,)


# ---------------------------------------------------------------------------
# S. Promotion / governance firewall
# ---------------------------------------------------------------------------


class TestPromotionGovernanceFirewall:
    def test_candidate_linked_assertion_never_satisfies_attach_confirmed_gate(self):
        """The entire downstream governed chain
        (intelligence_review_persistence -> promotion_policy_persistence
        -> governed_signal_creation) requires
        identity_guard_decision == 'ATTACH_CONFIRMED' before it will ever
        touch a row - candidate-linked rows are always INSUFFICIENT_IDENTITY,
        structurally excluded by construction, unmodified by UAC3."""
        with Session(_engine()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("firewall"), source_locator="p1", raw_text="Foo Regional Airport memo.",
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            result = resolve_or_persist_discovery_identity(session, _meta("firewall-doc"), fragment, [])
            assertion = session.get(SourceAssertion, result.source_assertion_id)
            assert assertion.identity_guard_decision == "INSUFFICIENT_IDENTITY"
            assert assertion.identity_guard_decision != "ATTACH_CONFIRMED"
            assert assertion.intelligence_review_decision is None
            assert assertion.promotion_policy_decision is None
            assert assertion.signal_id is None

    def test_no_signal_ever_created_by_this_module(self):
        with Session(_engine()) as session:
            for i in range(3):
                fragment = CandidateFragment(
                    artifact_identity=_artifact(f"nosignal-{i}"), source_locator="p1",
                    raw_text=f"Foo Regional Airport memo {i}.", airport_names=frozenset({"Foo Regional Airport"}),
                )
                resolve_or_persist_discovery_identity(session, _meta(f"nosignal-doc-{i}"), fragment, [])
            assert session.query(Signal).count() == 0

    def test_strongest_adversarial_evidence_still_cannot_bypass_governed_signal_creation_gate(self):
        """Review §15: construct the strongest-looking possible
        candidate-linked evidence (a confirmed contract award, a dollar
        figure, an awarded-contractor claim - exactly the shape that
        WOULD sail through promotion policy for a KNOWN-airport row) and
        attempt to call create_signal_from_approved_review() directly
        against it, bypassing every normal review step. Must still fail
        closed - this is not a UAC3 guarantee, it is a structural
        guarantee of governed_signal_creation.py's own pre-existing,
        unmodified gate, proven directly rather than merely inferred."""
        from app.services.governed_signal_creation import create_signal_from_approved_review

        with Session(_engine()) as session:
            fragment = CandidateFragment(
                artifact_identity=_artifact("firewall-strong"), source_locator="p1",
                raw_text="Foo Regional Airport confirmed EMAS contract award, $5,000,000, awarded to Runway Safe.",
                airport_names=frozenset({"Foo Regional Airport"}),
            )
            result = resolve_or_persist_discovery_identity(session, _meta("firewall-strong-doc"), fragment, [])
            session.commit()
            assertion = session.get(SourceAssertion, result.source_assertion_id)

            with pytest.raises(ValueError, match="requires identity_guard_decision == 'ATTACH_CONFIRMED'"):
                create_signal_from_approved_review(
                    session, assertion, title="Fake Signal", category="replacement", confidence="high",
                )
            assert session.query(Signal).count() == 0


# ---------------------------------------------------------------------------
# Result contract shape
# ---------------------------------------------------------------------------


class TestResultContract:
    def test_outcome_is_narrow_enum_not_boolean(self):
        assert set(DiscoveryIdentityOutcome) == {
            DiscoveryIdentityOutcome.KNOWN_CANONICAL_ATTACHMENT,
            DiscoveryIdentityOutcome.UNKNOWN_AIRPORT_CANDIDATE,
            DiscoveryIdentityOutcome.AMBIGUOUS_KNOWN_IDENTITY,
            DiscoveryIdentityOutcome.UNRESOLVED_IDENTITY,
        }

    def test_unknown_airport_candidate_created_is_none_for_non_candidate_outcomes(self):
        with Session(_engine()) as session:
            airport = _seed_airport(session, faa_code="BOS", name="Boston Logan International Airport")
            candidate_airport = _candidate(airport, identifiers=frozenset({"BOS"}))
            fragment = CandidateFragment(
                artifact_identity=_artifact("contract"), source_locator="p1", raw_text="BOS work.",
                airport_identifiers=frozenset({"BOS"}),
            )
            result = resolve_or_persist_discovery_identity(session, _meta("contract-doc"), fragment, [candidate_airport])
            assert result.unknown_airport_candidate_created is None


# ---------------------------------------------------------------------------
# U. Real DB no-access
# ---------------------------------------------------------------------------


class TestNoRealDatabaseAccess:
    def test_no_reference_to_the_real_database_path_in_module(self):
        import app.services.unknown_airport_discovery_integration as integration_module

        source = inspect_module.getsource(integration_module)
        assert "runway_safe.db" not in source
        assert "SessionLocal" not in source
