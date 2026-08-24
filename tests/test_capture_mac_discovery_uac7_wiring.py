"""UAC7 (docs/architecture/rwi-uac7-capture-mac-uac3-wiring-report.md):
proves scripts/capture_mac_discovery.py's apply phase is genuinely wired
to app.services.unknown_airport_discovery_integration.resolve_or_persist_discovery_identity(),
not just that the orchestrator itself works (tests/test_unknown_airport_discovery_integration.py
already proves the orchestrator's own routing/idempotency/atomicity rules
exhaustively at the service level - never re-proven here). Every test in
this file goes THROUGH run_capture()/the runner's own CLI-facing entry
point - that is UAC7's entire reason for existing (pre-UAC7, the runner
called persist_discovery_fragment() directly and had no code path to the
UnknownAirportCandidate branch at all).

Never touches the real database or the network - isolated temp-file
SQLite databases only, and the MAC extractor is monkeypatched (not the
routing/persistence layer - see _install_fake_extractor()'s own
docstring) so tests can supply deterministic CandidateFragment content
without needing a real PDF fixture per case.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app import models  # noqa: F401 - registers all metadata
from app.models import Airport, Runway, RunwayEnd, Signal, Source, SourceAssertion
from app.models.identity_guard_evaluation import IdentityGuardEvaluation
from app.models.unknown_airport_candidate import UnknownAirportCandidate
from app.services.discovery_candidate_fragment import CandidateFragment
import app.services.unknown_airport_discovery_integration as uac3_integration
import scripts.capture_mac_discovery as capture_module
from scripts.capture_mac_discovery import (
    CaptureConfig,
    FixtureDocument,
    build_engine,
    run_capture,
)


def _migrated_db(tmp_path: Path, name: str) -> Path:
    database = tmp_path / name
    engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(engine)
    engine.dispose()
    return database


def _seed_airport(database: Path, *, name: str, code: "str | None" = None, runway_pairs: dict = {}) -> int:
    engine = create_engine(f"sqlite:///{database}")
    with Session(engine) as session:
        airport = Airport(name=name, faa_code=code, iata_code=code, icao_code=(f"K{code}" if code else None), country="USA", city="Anytown")
        session.add(airport)
        session.flush()
        for designation, ends in runway_pairs.items():
            runway = Runway(airport_id=airport.id, designation=designation)
            session.add(runway)
            session.flush()
            for end in ends:
                session.add(RunwayEnd(runway_id=runway.id, designation=end))
        session.commit()
        airport_id = airport.id
    engine.dispose()
    return airport_id


def _install_fake_extractor(monkeypatch, fragments_by_artifact_identity: dict) -> None:
    """Replaces the MAC extractor call with a deterministic lookup keyed
    on artifact_identity, so each test can supply an exact CandidateFragment
    without depending on real PDF text extraction (extraction itself is
    untouched by UAC7 and already covered by its own tests). Patches the
    name INSIDE scripts.capture_mac_discovery's own module namespace
    (where run_capture() actually looks it up), never the routing or
    persistence layer - the whole point of this file is to exercise the
    REAL resolve_or_persist_discovery_identity() call, unmocked."""

    def _fake_extract(pdf_bytes, content_type, *, artifact_identity, source_locator, document_title=None, url=None):
        fragment = fragments_by_artifact_identity.get(artifact_identity)
        if fragment is None:
            return None
        return fragment, ()

    monkeypatch.setattr(capture_module, "extract_candidate_fragment", _fake_extract)


def _fixture_for(artifact_identity: str, source_locator: str = "loc-1") -> FixtureDocument:
    return FixtureDocument(pdf_bytes=b"placeholder", artifact_identity=artifact_identity, source_locator=source_locator)


def _counts(database: Path) -> dict:
    engine = build_engine(database)
    with Session(engine) as session:
        result = {
            "airports": session.query(Airport).count(),
            "source_assertions": session.query(SourceAssertion).count(),
            "unknown_airport_candidates": session.query(UnknownAirportCandidate).count(),
            "identity_guard_evaluations": session.query(IdentityGuardEvaluation).count(),
            "signals": session.query(Signal).count(),
        }
    engine.dispose()
    return result


# --- C. strong unknown candidate, through the real runner -----------------


def test_strong_unknown_candidate_routes_through_runner_to_uac3(tmp_path, monkeypatch):
    database = _migrated_db(tmp_path, "strong_unknown.db")
    fragment = CandidateFragment(
        artifact_identity="uac7-strong-unknown-1", source_locator="loc-1",
        raw_text="RWI Fictional Regional Airport is evaluating an EMAS installation.",
        airport_names=frozenset({"RWI Fictional Regional Airport"}),
    )
    _install_fake_extractor(monkeypatch, {"uac7-strong-unknown-1": fragment})

    dry = run_capture(CaptureConfig(database=database, fixture_documents=(_fixture_for("uac7-strong-unknown-1"),)))
    assert dry["planned_governed_evidence"][0]["would_form_unknown_airport_candidate"] is True

    applied = run_capture(CaptureConfig(
        database=database, fixture_documents=(_fixture_for("uac7-strong-unknown-1"),),
        apply=True, allow_database_write=True, expected_fingerprint=dry["plan_fingerprint"], skip_backup=True,
    ))
    assert applied["applied"] is True
    result = applied["apply_result"][0]
    assert result["routing_outcome"] == "UNKNOWN_AIRPORT_CANDIDATE"
    assert result["unknown_airport_candidate_id"] is not None
    assert result["unknown_airport_candidate_created"] is True
    assert result["attached_airport_id"] is None
    assert result["evidence_bag_snapshot_id"] is not None

    counts = _counts(database)
    assert counts["unknown_airport_candidates"] == 1
    assert counts["source_assertions"] == 1
    assert counts["airports"] == 0  # no canonical Airport created
    assert counts["identity_guard_evaluations"] == 0  # EB4 never auto-triggered
    assert counts["signals"] == 0

    from app.models.source_assertion_evidence_bag import SourceAssertionEvidenceBag

    engine = build_engine(database)
    with Session(engine) as session:
        assertion = session.scalar(select(SourceAssertion))
        assert assertion.airport_id is None
        assert assertion.unknown_airport_candidate_id is not None
        assert session.query(SourceAssertionEvidenceBag).filter_by(source_assertion_id=assertion.id).count() == 1
    engine.dispose()


# --- D. replay/convergence, through the real runner -----------------------


def test_unknown_candidate_replay_through_runner_converges_no_duplicates(tmp_path, monkeypatch):
    database = _migrated_db(tmp_path, "convergence.db")
    fragment = CandidateFragment(
        artifact_identity="uac7-convergence-1", source_locator="loc-1",
        raw_text="RWI Convergence Municipal Airport EMAS study.",
        airport_names=frozenset({"RWI Convergence Municipal Airport"}),
    )
    _install_fake_extractor(monkeypatch, {"uac7-convergence-1": fragment})

    dry = run_capture(CaptureConfig(database=database, fixture_documents=(_fixture_for("uac7-convergence-1"),)))
    fp = dry["plan_fingerprint"]
    first = run_capture(CaptureConfig(
        database=database, fixture_documents=(_fixture_for("uac7-convergence-1"),),
        apply=True, allow_database_write=True, expected_fingerprint=fp, skip_backup=True,
    ))
    second = run_capture(CaptureConfig(
        database=database, fixture_documents=(_fixture_for("uac7-convergence-1"),),
        apply=True, allow_database_write=True, expected_fingerprint=fp, skip_backup=True,
    ))

    assert first["apply_result"][0]["unknown_airport_candidate_created"] is True
    assert second["apply_result"][0]["unknown_airport_candidate_created"] is False
    assert first["apply_result"][0]["unknown_airport_candidate_id"] == second["apply_result"][0]["unknown_airport_candidate_id"]
    assert second["apply_result"][0]["source_assertion_created"] is False

    counts = _counts(database)
    assert counts["unknown_airport_candidates"] == 1
    assert counts["source_assertions"] == 1


# --- E. weak identity: no candidate manufactured, unresolved intact -------


def test_weak_identity_through_runner_stays_unresolved_no_candidate(tmp_path, monkeypatch):
    database = _migrated_db(tmp_path, "weak_identity.db")
    fragment = CandidateFragment(
        artifact_identity="uac7-weak-1", source_locator="loc-1",
        raw_text="An unspecified airport authority is considering EMAS installation.",
        # No airport_names at all - zero names = insufficient identity,
        # never manufactures a candidate from nothing.
    )
    _install_fake_extractor(monkeypatch, {"uac7-weak-1": fragment})

    dry = run_capture(CaptureConfig(database=database, fixture_documents=(_fixture_for("uac7-weak-1"),)))
    assert dry["planned_governed_evidence"][0]["would_form_unknown_airport_candidate"] is False

    applied = run_capture(CaptureConfig(
        database=database, fixture_documents=(_fixture_for("uac7-weak-1"),),
        apply=True, allow_database_write=True, expected_fingerprint=dry["plan_fingerprint"], skip_backup=True,
    ))
    result = applied["apply_result"][0]
    assert result["routing_outcome"] == "UNRESOLVED_IDENTITY"
    assert result["unknown_airport_candidate_id"] is None
    assert result["attached_airport_id"] is None

    counts = _counts(database)
    assert counts["unknown_airport_candidates"] == 0
    assert counts["source_assertions"] == 1  # evidence still preserved, just unresolved


# --- F. ambiguous known identity: runner must never manufacture a candidate


def test_ambiguous_known_identity_through_runner_never_forms_candidate(tmp_path, monkeypatch):
    """No explicit airport-name evidence at all - the pure "no name +
    ambiguous topology" case (identity-precedence review row G / mission
    S18 item F), which Option 3 leaves completely unaffected, since the
    override only ever engages when a formable name claim exists."""
    database = _migrated_db(tmp_path, "ambiguous.db")
    # Two known airports independently share the identical runway pair -
    # a single positive category (runway_topology) qualifies both, which
    # evaluate_attachment_for_candidates() downgrades to REVIEW_REQUIRED
    # for both (ambiguous among KNOWN candidates is a human identity-review
    # case, never routed to UnknownAirportCandidate formation).
    _seed_airport(database, name="Ambiguous North Field", code="ANF", runway_pairs={"4/22": ("4", "22")})
    _seed_airport(database, name="Ambiguous South Field", code="ASF", runway_pairs={"4/22": ("4", "22")})

    fragment = CandidateFragment(
        artifact_identity="uac7-ambiguous-1", source_locator="loc-1",
        raw_text="Runway 4/22 EMAS project.",
        runway_pairs=frozenset({"4/22"}),
        # No airport_names at all - deliberately distinct from the
        # uncorroborated-name test below.
    )
    _install_fake_extractor(monkeypatch, {"uac7-ambiguous-1": fragment})

    dry = run_capture(CaptureConfig(database=database, fixture_documents=(_fixture_for("uac7-ambiguous-1"),)))
    applied = run_capture(CaptureConfig(
        database=database, fixture_documents=(_fixture_for("uac7-ambiguous-1"),),
        apply=True, allow_database_write=True, expected_fingerprint=dry["plan_fingerprint"], skip_backup=True,
    ))
    result = applied["apply_result"][0]
    assert result["routing_outcome"] == "AMBIGUOUS_KNOWN_IDENTITY"
    assert result["attachment_outcome"] == "REVIEW_REQUIRED"
    assert result["unknown_airport_candidate_id"] is None
    assert result["attached_airport_id"] is None

    counts = _counts(database)
    assert counts["unknown_airport_candidates"] == 0

    engine = build_engine(database)
    with Session(engine) as session:
        assertion = session.scalar(select(SourceAssertion))
        assert assertion.airport_id is None
        assert assertion.unknown_airport_candidate_id is None
        assert assertion.identity_guard_decision == "REVIEW_REQUIRED"
    engine.dispose()


def test_ambiguous_topology_with_uncorroborated_explicit_name_forms_unknown_candidate_through_runner(tmp_path, monkeypatch):
    """UAC3 identity-precedence Option 3 (docs/architecture/rwi-uac3-
    identity-precedence-review.md), through the real runner: the exact
    real-world shape of the Anoka County-Blaine case - an explicit,
    well-formed source-provided airport name that matches NEITHER
    topology-ambiguous known candidate must route to
    UNKNOWN_AIRPORT_CANDIDATE, not AMBIGUOUS_KNOWN_IDENTITY."""
    database = _migrated_db(tmp_path, "ambiguous_named.db")
    _seed_airport(database, name="Ambiguous North Field", code="ANF", runway_pairs={"4/22": ("4", "22")})
    _seed_airport(database, name="Ambiguous South Field", code="ASF", runway_pairs={"4/22": ("4", "22")})

    fragment = CandidateFragment(
        artifact_identity="uac7-ambiguous-named-1", source_locator="loc-1",
        raw_text="Ambiguous Regional Airport Runway 4/22 EMAS project.",
        runway_pairs=frozenset({"4/22"}),
        airport_names=frozenset({"Ambiguous Regional Airport"}),  # matches neither seeded candidate
    )
    _install_fake_extractor(monkeypatch, {"uac7-ambiguous-named-1": fragment})

    dry = run_capture(CaptureConfig(database=database, fixture_documents=(_fixture_for("uac7-ambiguous-named-1"),)))
    assert dry["planned_governed_evidence"][0]["would_form_unknown_airport_candidate"] is True
    assert dry["planned_governed_evidence"][0]["attached_airport_id"] is None

    applied = run_capture(CaptureConfig(
        database=database, fixture_documents=(_fixture_for("uac7-ambiguous-named-1"),),
        apply=True, allow_database_write=True, expected_fingerprint=dry["plan_fingerprint"], skip_backup=True,
    ))
    result = applied["apply_result"][0]
    assert result["routing_outcome"] == "UNKNOWN_AIRPORT_CANDIDATE"
    assert result["attachment_outcome"] == "REVIEW_REQUIRED"
    assert result["unknown_airport_candidate_id"] is not None
    assert result["attached_airport_id"] is None

    counts = _counts(database)
    assert counts["unknown_airport_candidates"] == 1
    assert counts["airports"] == 2  # only the two pre-seeded known candidates; no new canonical Airport

    engine = build_engine(database)
    with Session(engine) as session:
        assertion = session.scalar(select(SourceAssertion))
        assert assertion.airport_id is None
        assert assertion.unknown_airport_candidate_id is not None
    engine.dispose()


def test_single_wrong_topology_candidate_through_runner_no_silent_attach(tmp_path, monkeypatch):
    """Identity-precedence Option 3, HIGH PRIORITY case, through the real
    runner: a single, non-ambiguous topology match with an explicit,
    unmatched name must no longer silently attach to the wrong airport -
    this is the more serious defect the design review found (worse than
    the ambiguous case, since it previously never reached human review at
    all)."""
    database = _migrated_db(tmp_path, "wrong_topology.db")
    _seed_airport(database, name="Different Existing Airport", code="DEA", runway_pairs={"18/36": ("18", "36")})

    fragment = CandidateFragment(
        artifact_identity="uac7-wrong-topology-1", source_locator="loc-1",
        raw_text="Example New Airport Runway 18-36 Reconstruction.",
        airport_names=frozenset({"Example New Airport"}), runway_pairs=frozenset({"18/36"}),
    )
    _install_fake_extractor(monkeypatch, {"uac7-wrong-topology-1": fragment})

    dry = run_capture(CaptureConfig(database=database, fixture_documents=(_fixture_for("uac7-wrong-topology-1"),)))
    assert dry["planned_governed_evidence"][0]["would_form_unknown_airport_candidate"] is True
    assert dry["planned_governed_evidence"][0]["attached_airport_id"] is None

    applied = run_capture(CaptureConfig(
        database=database, fixture_documents=(_fixture_for("uac7-wrong-topology-1"),),
        apply=True, allow_database_write=True, expected_fingerprint=dry["plan_fingerprint"], skip_backup=True,
    ))
    result = applied["apply_result"][0]
    assert result["routing_outcome"] == "UNKNOWN_AIRPORT_CANDIDATE"
    assert result["attachment_outcome"] == "ATTACH_PROVISIONAL"
    assert result["attached_airport_id"] is None
    assert result["unknown_airport_candidate_id"] is not None

    counts = _counts(database)
    assert counts["unknown_airport_candidates"] == 1
    assert counts["airports"] == 1  # only the pre-seeded "Different Existing Airport"; never attached to it

    engine = build_engine(database)
    with Session(engine) as session:
        assertion = session.scalar(select(SourceAssertion))
        assert assertion.airport_id is None
        assert assertion.unknown_airport_candidate_id is not None
    engine.dispose()


def test_fingerprint_changes_when_option3_flips_routing(tmp_path, monkeypatch):
    """S15 HIGH PRIORITY: the preview fingerprint must change when
    Option 3 flips a fragment's routing (proving the fingerprint's own
    would_form_unknown_airport_candidate/attached_airport_id material
    genuinely reflects the new rule, not a stale pre-Option-3 value) -
    and, separately, that applying the STALE fingerprint from before a
    canonical-state change is still correctly refused, exactly as UAC7's
    own TOCTOU protection already requires."""
    database = _migrated_db(tmp_path, "fingerprint_option3.db")
    fragment = CandidateFragment(
        artifact_identity="uac7-fp-option3-1", source_locator="loc-1",
        raw_text="Example New Airport Runway 18-36 Reconstruction.",
        airport_names=frozenset({"Example New Airport"}), runway_pairs=frozenset({"18/36"}),
    )
    fixture = _fixture_for("uac7-fp-option3-1")
    _install_fake_extractor(monkeypatch, {"uac7-fp-option3-1": fragment})

    # State A: no known candidate exists at all - zero supplied candidates
    # means the pre-existing INSUFFICIENT_IDENTITY path already forms a
    # candidate here (Option 3's own override never needs to engage for
    # this state). The interesting transition is state B below.
    dry_before = run_capture(CaptureConfig(database=database, fixture_documents=(fixture,)))
    assert dry_before["planned_governed_evidence"][0]["would_form_unknown_airport_candidate"] is True
    fingerprint_before = dry_before["plan_fingerprint"]

    # State B: a known candidate now exists whose NAME exactly matches the
    # fragment's own explicit name - Option 3's override must NOT apply
    # here (name corroborated), so routing flips to KNOWN_CANONICAL_ATTACHMENT.
    _seed_airport(database, name="Example New Airport", code="ENA", runway_pairs={"18/36": ("18", "36")})
    dry_after = run_capture(CaptureConfig(database=database, fixture_documents=(fixture,)))
    assert dry_after["planned_governed_evidence"][0]["would_form_unknown_airport_candidate"] is False
    assert dry_after["plan_fingerprint"] != fingerprint_before

    # The stale (state A) fingerprint must be refused against the new
    # (state B) canonical data - zero writes.
    before_counts = _counts(database)
    stale_applied = run_capture(CaptureConfig(
        database=database, fixture_documents=(fixture,),
        apply=True, allow_database_write=True, expected_fingerprint=fingerprint_before, skip_backup=True,
    ))
    assert stale_applied["applied"] is False
    assert any("FINGERPRINT_MISMATCH" in b for b in stale_applied["blockers"])
    assert _counts(database) == before_counts

    # Applying the fresh (state B) fingerprint succeeds and correctly
    # attaches to the now-matching known candidate.
    applied = run_capture(CaptureConfig(
        database=database, fixture_documents=(fixture,),
        apply=True, allow_database_write=True, expected_fingerprint=dry_after["plan_fingerprint"], skip_backup=True,
    ))
    assert applied["applied"] is True
    assert applied["apply_result"][0]["routing_outcome"] == "KNOWN_CANONICAL_ATTACHMENT"


# --- G. all known candidates conflict, coherent new identity present ------


def test_all_known_conflict_coherent_new_routes_to_unknown_candidate(tmp_path, monkeypatch):
    database = _migrated_db(tmp_path, "all_conflict.db")
    # Topology overlap is required for the runner's own candidate-selection
    # heuristic (select_candidate_airports() is topology-driven; a bare
    # identifier/code alone is never enough to be selected as a candidate
    # in the first place) - the runway match becomes positive evidence,
    # but the contradicting identifier vetoes it unconditionally anyway
    # (evaluate_attachment(): "contradiction always wins", regardless of
    # any positive evidence found for the same candidate).
    _seed_airport(database, name="Conflict Field", code="CFD", runway_pairs={"16/34": ("16", "34")})

    fragment = CandidateFragment(
        artifact_identity="uac7-all-conflict-1", source_locator="loc-1",
        raw_text="RWI New Coherent Airport identifier ZZZ, runway 16/34, EMAS study.",
        airport_names=frozenset({"RWI New Coherent Airport"}),
        runway_pairs=frozenset({"16/34"}),
        # An identifier present in the fragment that does NOT match the
        # only known candidate is self-evidently a contradiction (design
        # doc rule 1) - the known candidate REJECT_CROSS_AIRPORTs.
        airport_identifiers=frozenset({"ZZZ"}),
    )
    _install_fake_extractor(monkeypatch, {"uac7-all-conflict-1": fragment})

    dry = run_capture(CaptureConfig(database=database, fixture_documents=(_fixture_for("uac7-all-conflict-1"),)))
    assert dry["planned_governed_evidence"][0]["guard_outcome"] == "REJECT_CROSS_AIRPORT"
    assert dry["planned_governed_evidence"][0]["would_form_unknown_airport_candidate"] is True

    applied = run_capture(CaptureConfig(
        database=database, fixture_documents=(_fixture_for("uac7-all-conflict-1"),),
        apply=True, allow_database_write=True, expected_fingerprint=dry["plan_fingerprint"], skip_backup=True,
    ))
    result = applied["apply_result"][0]
    assert result["routing_outcome"] == "UNKNOWN_AIRPORT_CANDIDATE"
    assert result["attachment_outcome"] == "REJECT_CROSS_AIRPORT"
    assert result["unknown_airport_candidate_id"] is not None

    counts = _counts(database)
    assert counts["unknown_airport_candidates"] == 1
    assert counts["airports"] == 1  # only the pre-seeded known candidate; no new canonical Airport


# --- H. preview never writes, even when the plan says it would form a candidate


def test_preview_of_unknown_candidate_case_creates_zero_rows(tmp_path, monkeypatch):
    database = _migrated_db(tmp_path, "preview_only.db")
    fragment = CandidateFragment(
        artifact_identity="uac7-preview-1", source_locator="loc-1",
        raw_text="RWI Preview Only Airport EMAS study.",
        airport_names=frozenset({"RWI Preview Only Airport"}),
    )
    _install_fake_extractor(monkeypatch, {"uac7-preview-1": fragment})

    before = _counts(database)
    report = run_capture(CaptureConfig(database=database, fixture_documents=(_fixture_for("uac7-preview-1"),)))
    assert report["applied"] is False
    assert report["planned_governed_evidence"][0]["would_form_unknown_airport_candidate"] is True
    after = _counts(database)
    assert before == after == {"airports": 0, "source_assertions": 0, "unknown_airport_candidates": 0, "identity_guard_evaluations": 0, "signals": 0}


# --- L. HIGH PRIORITY: candidate-set change between preview and apply -----


def test_topology_change_between_preview_and_apply_is_detected_not_silently_applied(tmp_path, monkeypatch):
    """Attack (mission S17): preview with candidate-set state A (no known
    match -> would form an UnknownAirportCandidate), then mutate canonical
    DB state so the SAME fragment would now ATTACH_CONFIRMED to a real
    known airport, then apply using the STALE preview fingerprint. The
    routing decision must never be allowed to silently flip between
    preview and apply - the apply must refuse (FINGERPRINT_MISMATCH),
    never create a candidate under changed state undetected."""
    database = _migrated_db(tmp_path, "toctou.db")
    # Runway topology, not a bare identifier code, is what the runner's
    # own select_candidate_airports() actually keys candidate discovery
    # on - see all_conflict test above for the same constraint.
    fragment = CandidateFragment(
        artifact_identity="uac7-toctou-1", source_locator="loc-1",
        raw_text="RWI TOCTOU Field runway 16/34 EMAS study.",
        airport_names=frozenset({"RWI TOCTOU Field"}),
        runway_pairs=frozenset({"16/34"}),
    )
    _install_fake_extractor(monkeypatch, {"uac7-toctou-1": fragment})

    # State A: no matching known airport exists yet.
    dry = run_capture(CaptureConfig(database=database, fixture_documents=(_fixture_for("uac7-toctou-1"),)))
    assert dry["planned_governed_evidence"][0]["would_form_unknown_airport_candidate"] is True
    stale_fingerprint = dry["plan_fingerprint"]

    # State B: a real airport now carries both the name AND the runway
    # topology the fragment describes - two independent positive
    # categories, so the fragment would now ATTACH_CONFIRMED to it instead.
    _seed_airport(database, name="RWI TOCTOU Field", code="TCT", runway_pairs={"16/34": ("16", "34")})

    before = _counts(database)
    applied = run_capture(CaptureConfig(
        database=database, fixture_documents=(_fixture_for("uac7-toctou-1"),),
        apply=True, allow_database_write=True, expected_fingerprint=stale_fingerprint, skip_backup=True,
    ))
    after = _counts(database)

    assert applied["applied"] is False
    assert any("FINGERPRINT_MISMATCH" in b for b in applied["blockers"])
    assert before["unknown_airport_candidates"] == after["unknown_airport_candidates"] == 0
    assert before["source_assertions"] == after["source_assertions"] == 0

    # A fresh preview against the NEW state correctly reflects the flip,
    # and applying THAT (correct, current) fingerprint succeeds normally.
    dry2 = run_capture(CaptureConfig(database=database, fixture_documents=(_fixture_for("uac7-toctou-1"),)))
    assert dry2["planned_governed_evidence"][0]["would_form_unknown_airport_candidate"] is False
    assert dry2["plan_fingerprint"] != stale_fingerprint
    applied2 = run_capture(CaptureConfig(
        database=database, fixture_documents=(_fixture_for("uac7-toctou-1"),),
        apply=True, allow_database_write=True, expected_fingerprint=dry2["plan_fingerprint"], skip_backup=True,
    ))
    assert applied2["applied"] is True
    assert applied2["apply_result"][0]["routing_outcome"] == "KNOWN_CANONICAL_ATTACHMENT"


# --- EB3 replay-conflict semantics must survive intact through the runner -


def test_evidence_bag_replay_conflict_fails_loud_through_runner_not_swallowed(tmp_path, monkeypatch):
    """fragment_hash (part of the fingerprint and of SourceAssertion's own
    fragment-identity dedup) only covers raw_text, not the full structured
    CandidateFragment - so a misbehaving extractor could in principle
    return DIFFERENT structured content (different airport_names) for an
    IDENTICAL artifact_identity/source_locator/raw_text on replay. A real,
    pure extractor can never do this (same raw_text -> same deterministic
    parse), but this test proves the deeper, independent safety net still
    holds even if it somehow did: EB3's own replay-conflict detection
    (ConflictingEvidenceBagReplayError, app.services.discovery_evidence_persistence)
    must still fail loud through the runner - never silently swallowed,
    never followed by a commit, and never leaving a half-formed extra
    UnknownAirportCandidate behind (find_or_create_unknown_airport_candidate()
    flushes its own new candidate row BEFORE the conflict is detected one
    call later in persist_candidate_linked_source_assertion() - proving
    that partially-flushed work is rolled back too, not just simple
    single-step failures like the atomicity test above)."""
    from app.services.discovery_evidence_persistence import ConflictingEvidenceBagReplayError

    database = _migrated_db(tmp_path, "replay_conflict.db")
    same_identity = dict(artifact_identity="uac7-replay-conflict-1", source_locator="loc-1")
    fragment_first = CandidateFragment(
        raw_text="RWI Replay Conflict Airport identical raw text.",
        airport_names=frozenset({"RWI Replay Conflict Name One"}),
        **same_identity,
    )
    fragment_second = CandidateFragment(
        raw_text="RWI Replay Conflict Airport identical raw text.",  # identical -> identical fragment_hash
        airport_names=frozenset({"RWI Replay Conflict Name Two"}),  # different structured content
        **same_identity,
    )
    assert fragment_first.identity == fragment_second.identity  # same dedup key, different content

    fixture = _fixture_for("uac7-replay-conflict-1", "loc-1")
    _install_fake_extractor(monkeypatch, {"uac7-replay-conflict-1": fragment_first})
    dry = run_capture(CaptureConfig(database=database, fixture_documents=(fixture,)))
    first_apply = run_capture(CaptureConfig(
        database=database, fixture_documents=(fixture,),
        apply=True, allow_database_write=True, expected_fingerprint=dry["plan_fingerprint"], skip_backup=True,
    ))
    assert first_apply["applied"] is True
    before = _counts(database)

    # Same fingerprint applies both times (same fragment identity, same
    # guard_outcome bucket, same would_form_unknown_airport_candidate) -
    # the fingerprint handshake alone would NOT catch this replay; EB3's
    # own deeper check must.
    _install_fake_extractor(monkeypatch, {"uac7-replay-conflict-1": fragment_second})
    dry2 = run_capture(CaptureConfig(database=database, fixture_documents=(fixture,)))
    assert dry2["plan_fingerprint"] == dry["plan_fingerprint"]

    with pytest.raises(ConflictingEvidenceBagReplayError):
        run_capture(CaptureConfig(
            database=database, fixture_documents=(fixture,),
            apply=True, allow_database_write=True, expected_fingerprint=dry2["plan_fingerprint"], skip_backup=True,
        ))
    after = _counts(database)

    assert before == after  # no orphaned second candidate, no corrupted assertion, nothing committed
    assert after["unknown_airport_candidates"] == 1
    assert after["source_assertions"] == 1


# --- M. runner-owned transaction atomicity across a batch -----------------


def test_runner_commit_boundary_rolls_back_whole_batch_on_uac3_failure(tmp_path, monkeypatch):
    """Two fragments in one apply call; the SECOND fragment's call into
    resolve_or_persist_discovery_identity() is made to raise. The runner
    commits exactly once, after the whole loop - proving that failure
    rolls back the FIRST fragment's already-persisted work too, not just
    the second's (the runner's own commit-boundary guarantee, distinct
    from - and not a duplicate of - UAC3's own internal atomicity tests)."""
    database = _migrated_db(tmp_path, "atomicity.db")
    fragment_a = CandidateFragment(
        artifact_identity="uac7-atomic-a", source_locator="loc-a",
        raw_text="RWI Atomic Alpha Airport EMAS study.",
        airport_names=frozenset({"RWI Atomic Alpha Airport"}),
    )
    fragment_b = CandidateFragment(
        artifact_identity="uac7-atomic-b", source_locator="loc-b",
        raw_text="RWI Atomic Beta Airport EMAS study.",
        airport_names=frozenset({"RWI Atomic Beta Airport"}),
    )
    _install_fake_extractor(monkeypatch, {"uac7-atomic-a": fragment_a, "uac7-atomic-b": fragment_b})

    real_resolver = uac3_integration.resolve_or_persist_discovery_identity
    calls = {"n": 0}

    def _flaky_resolver(session, source_metadata, fragment, candidate_airports):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated failure on second fragment")
        return real_resolver(session, source_metadata, fragment, candidate_airports)

    fixtures = (_fixture_for("uac7-atomic-a", "loc-a"), _fixture_for("uac7-atomic-b", "loc-b"))
    dry = run_capture(CaptureConfig(database=database, fixture_documents=fixtures))

    monkeypatch.setattr(capture_module, "resolve_or_persist_discovery_identity", _flaky_resolver)
    before = _counts(database)
    with pytest.raises(RuntimeError, match="simulated failure"):
        run_capture(CaptureConfig(
            database=database, fixture_documents=fixtures,
            apply=True, allow_database_write=True, expected_fingerprint=dry["plan_fingerprint"], skip_backup=True,
        ))
    after = _counts(database)

    assert before == after  # the first fragment's work did not survive the second's failure
    assert after["unknown_airport_candidates"] == 0
    assert after["source_assertions"] == 0
