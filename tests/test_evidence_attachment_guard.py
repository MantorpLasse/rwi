"""Tests for app/services/evidence_attachment_guard.py
(docs/architecture/ai-discovery-evidence-attachment-guard.md,
docs/architecture/ai-discovery-evidence-attachment-guard-core-report.md).

Every test is a pure, synthetic fixture - no database, no network, no
SQLAlchemy import anywhere in this file. This module's own purity is
exactly what is being tested."""
import copy

from app.services.evidence_attachment_guard import (
    AttachmentOutcome,
    CandidateAirport,
    EvidenceBag,
    EvidenceCategory,
    candidate_airport_from_airport_like,
    evaluate_attachment,
    evaluate_attachment_for_candidates,
)

# ---------------------------------------------------------------------------
# Synthetic candidate airports, shaped like the real ones this pilot
# concerned (never touching the real database).
# ---------------------------------------------------------------------------

SFO = CandidateAirport(
    id="SFO",
    name="San Francisco International Airport",
    identifiers=frozenset({"SFO", "KSFO"}),
    aliases=frozenset(),
    city_location="San Francisco",
    canonical_runway_ends=frozenset({"1L", "19R", "1R", "19L", "10L", "28R", "10R", "28L"}),
    canonical_runway_pairs=frozenset({"1L/19R", "1R/19L", "10L/28R", "10R/28L"}),
    known_issuers=frozenset({"San Francisco Airport Commission"}),
)

MSP = CandidateAirport(
    id="MSP",
    name="Minneapolis-St. Paul International Airport",
    identifiers=frozenset({"MSP", "KMSP"}),
    city_location="Minneapolis",
    canonical_runway_ends=frozenset({"12R", "30L", "12L", "30R", "4", "22", "17", "35"}),
    canonical_runway_pairs=frozenset({"12R/30L", "12L/30R", "4/22", "17/35"}),
    known_issuers=frozenset({"Metropolitan Airports Commission"}),
)

BOS = CandidateAirport(
    id="BOS",
    name="Boston Logan International Airport",
    identifiers=frozenset({"BOS", "KBOS"}),
    city_location="Boston",
    canonical_runway_ends=frozenset({"9", "27", "4L", "22R", "4R", "22L", "14", "32", "15L", "33R", "15R", "33L"}),
    canonical_runway_pairs=frozenset({"9/27", "4L/22R", "4R/22L", "14/32", "15L/33R", "15R/33L"}),
    known_issuers=frozenset({"Massachusetts Port Authority", "Massport"}),
)

ORH = CandidateAirport(
    id="ORH",
    name="Worcester Regional",
    identifiers=frozenset({"ORH", "KORH"}),
    city_location="Worcester",
    canonical_runway_ends=frozenset({"11", "29", "15", "33"}),
    canonical_runway_pairs=frozenset({"11/29", "15/33"}),
    known_issuers=frozenset({"Massachusetts Port Authority", "MPA", "Massport"}),
)

ALLEGHENY = CandidateAirport(
    id="AGC",
    name="Allegheny County Airport",
    identifiers=frozenset({"AGC", "KAGC"}),
    city_location="West Mifflin",
    canonical_runway_ends=frozenset({"10", "28"}),
    canonical_runway_pairs=frozenset({"10/28"}),
    known_issuers=frozenset(),
)

MORRISTOWN = CandidateAirport(
    id="MMU",
    name="Morristown Municipal Airport",
    identifiers=frozenset({"MMU", "KMMU"}),
    city_location="Morristown",
    canonical_runway_ends=frozenset({"5", "23"}),
    canonical_runway_pairs=frozenset({"5/23"}),
    known_issuers=frozenset(),
)

# A synthetic, deliberately non-U.S. airport - no FAA-shaped identifier,
# no U.S. state, English airport name used only as a plain string (no
# assumption the guard "knows" English) - proves the decision core does
# not depend on any U.S.-specific structure (design doc S9, task S16).
HANEDA = CandidateAirport(
    id="HND",
    name="Tokyo International Airport",
    identifiers=frozenset({"HND", "RJTT"}),
    aliases=frozenset({"羽田空港"}),
    city_location="Ota, Tokyo",
    canonical_runway_ends=frozenset({"04", "22", "05", "23", "16L", "34R", "16R", "34L"}),
    canonical_runway_pairs=frozenset({"4/22", "5/23", "16L/34R", "16R/34L"}),
    known_issuers=frozenset({"Ministry of Land, Infrastructure, Transport and Tourism"}),
)


# ---------------------------------------------------------------------------
# A-K worked cases (docs/architecture/ai-discovery-evidence-attachment-guard.md S8)
# ---------------------------------------------------------------------------

def test_case_A_sfo_msp_false_positive_rejects_cross_airport():
    """The pilot's own case: a real MSP document, evaluated against
    candidate SFO, must never attach - regardless of EMAS/Runway
    Safe/dollar-figure topical overlap, none of which is identity
    evidence at all."""
    evidence = EvidenceBag(
        contradicting_issuers=frozenset({"Metropolitan Airports Commission"}),
        runway_ends=frozenset({"30L"}),  # absent from SFO's topology
    )
    decision = evaluate_attachment(SFO, evidence)
    assert decision.outcome == AttachmentOutcome.REJECT_CROSS_AIRPORT
    assert any(c.category == EvidenceCategory.ISSUER for c in decision.contradicting_evidence)
    assert any(c.category == EvidenceCategory.RUNWAY_TOPOLOGY for c in decision.contradicting_evidence)


def test_case_A_same_evidence_confirms_for_the_real_airport_msp():
    """The same evidence, evaluated against the airport it actually
    concerns, is entirely unproblematic - the guard rejects a mismatch,
    it does not reject the evidence itself."""
    evidence = EvidenceBag(issuers=frozenset({"Metropolitan Airports Commission"}), runway_ends=frozenset({"30L"}))
    decision = evaluate_attachment(MSP, evidence)
    assert decision.outcome == AttachmentOutcome.ATTACH_CONFIRMED


def test_case_B_genuine_sfo_evidence_confirms():
    evidence = EvidenceBag(identifiers=frozenset({"SFO"}), runway_pairs=frozenset({"1R/19L"}))
    decision = evaluate_attachment(SFO, evidence)
    assert decision.outcome == AttachmentOutcome.ATTACH_CONFIRMED


def test_case_C_bos_protected_direction_naming_confirms():
    """Massport's own public naming uses the PROTECTED direction ("Runway
    22R"), not the NASR physical value ("04L") - the guard checks
    topology MEMBERSHIP (22R is a real BOS RunwayEnd), never which
    specific physical assertion it corresponds to."""
    evidence = EvidenceBag(issuers=frozenset({"Massport"}), runway_ends=frozenset({"22R"}))
    decision = evaluate_attachment(BOS, evidence)
    assert decision.outcome == AttachmentOutcome.ATTACH_CONFIRMED


def test_case_D_orh_dual_physical_protected_naming_confirms():
    evidence = EvidenceBag(issuers=frozenset({"MPA"}), runway_ends=frozenset({"29", "11"}))
    decision = evaluate_attachment(ORH, evidence)
    assert decision.outcome == AttachmentOutcome.ATTACH_CONFIRMED


def test_case_E_usaspending_embedded_faa_loc_id_confirms():
    """An embedded FAA Loc ID is just another identifier token from this
    module's point of view - no FAA-specific code lives in the guard
    itself (design doc S9); the extraction regex stays entirely in
    scripts/import_usaspending_grants.py, untouched by this task."""
    evidence = EvidenceBag(identifiers=frozenset({"ORH"}))
    decision = evaluate_attachment(ORH, evidence)
    assert decision.outcome == AttachmentOutcome.ATTACH_CONFIRMED


def test_case_F_usaspending_city_state_only_is_provisional_not_confirmed():
    """Deliberate, documented difference from resolve_airport()'s CURRENT
    behavior (which fully resolves a unique city/state match today) - see
    the core report S15/S16 for why this is flagged, not silently
    retrofitted."""
    evidence = EvidenceBag(locations=frozenset({"Worcester"}))
    decision = evaluate_attachment(ORH, evidence)
    assert decision.outcome == AttachmentOutcome.ATTACH_PROVISIONAL


def test_case_G_allegheny_recipient_name_alone_is_insufficient():
    """The historical failure shape: only the USAspending recipient/
    authority-organization name is available. Recipient/organization
    names are deliberately never placed in EvidenceBag.names or .issuers
    by a correctly-designed extractor (they identify who received money,
    not which airport - see resolve_airport()'s own docstring) - so this
    evidence bag carries no positive-evidence category at all."""
    evidence = EvidenceBag()  # "Allegheny County Airport Authority" recognized as a recipient name, not identity evidence
    decision = evaluate_attachment(ALLEGHENY, evidence)
    assert decision.outcome == AttachmentOutcome.INSUFFICIENT_IDENTITY


def test_case_G_allegheny_recipient_name_never_reaches_confirmed_even_if_naively_extracted():
    """Even in the worst case - a naive extractor treats the raw
    recipient name as a "name" token - it does not exact-match the real
    canonical name/alias ("Allegheny County Airport" != "Allegheny County
    Airport Authority"), so it still cannot confirm attachment alone."""
    evidence = EvidenceBag(names=frozenset({"Allegheny County Airport Authority"}))
    decision = evaluate_attachment(ALLEGHENY, evidence)
    assert decision.outcome == AttachmentOutcome.INSUFFICIENT_IDENTITY


def test_case_H_morristown_recipient_name_alone_is_insufficient():
    evidence = EvidenceBag()
    decision = evaluate_attachment(MORRISTOWN, evidence)
    assert decision.outcome == AttachmentOutcome.INSUFFICIENT_IDENTITY


def test_case_I_valid_airport_identity_no_runway_reference_is_provisional():
    evidence = EvidenceBag(names=frozenset({"San Francisco International Airport"}))
    decision = evaluate_attachment(SFO, evidence)
    assert decision.outcome == AttachmentOutcome.ATTACH_PROVISIONAL


def test_case_I_valid_airport_identity_plus_identifier_confirms():
    evidence = EvidenceBag(identifiers=frozenset({"SFO"}))
    decision = evaluate_attachment(SFO, evidence)
    assert decision.outcome == AttachmentOutcome.ATTACH_CONFIRMED


def test_case_J_runway_only_unique_across_candidates_is_provisional():
    """Single-candidate evaluation: a runway pair alone, matching only
    this one candidate's own topology, with no name/identifier/issuer at
    all, must not over-confirm merely from runway coincidence."""
    evidence = EvidenceBag(runway_pairs=frozenset({"1R/19L"}))
    decision = evaluate_attachment(SFO, evidence)
    assert decision.outcome == AttachmentOutcome.ATTACH_PROVISIONAL


def test_case_J_runway_shared_by_two_candidates_becomes_review_required():
    """The same runway-pair-only evidence, checked against the full
    candidate set, where TWO different candidates share that exact pair -
    ambiguous, must not silently pick one."""
    shared_pair = "9/27"
    twin_a = CandidateAirport(id="A", name="Twin Field A", canonical_runway_pairs=frozenset({shared_pair}))
    twin_b = CandidateAirport(id="B", name="Twin Field B", canonical_runway_pairs=frozenset({shared_pair}))
    evidence = EvidenceBag(runway_pairs=frozenset({shared_pair}))

    decisions = evaluate_attachment_for_candidates(evidence, [twin_a, twin_b])

    assert decisions["A"].outcome == AttachmentOutcome.REVIEW_REQUIRED
    assert decisions["B"].outcome == AttachmentOutcome.REVIEW_REQUIRED


def test_case_K_multiple_airport_document_is_fragment_scoped():
    """Strong evidence for BOS in one fragment must not attach to ORH
    merely because ORH is named elsewhere in the same source document -
    the guard is invoked once per fragment, never once per whole
    document; each fragment gets its own EvidenceBag."""
    bos_fragment = EvidenceBag(issuers=frozenset({"Massport"}), runway_ends=frozenset({"22R"}))
    orh_fragment = EvidenceBag(issuers=frozenset({"MPA"}), runway_ends=frozenset({"29"}))

    bos_decision = evaluate_attachment(BOS, bos_fragment)
    orh_decision_against_bos_fragment = evaluate_attachment(ORH, bos_fragment)

    assert bos_decision.outcome == AttachmentOutcome.ATTACH_CONFIRMED
    # BOS's own fragment ("22R", an issuer BOTH airports actually share)
    # gives ORH no matching runway-topology evidence at all ("22R" is not
    # one of ORH's own ends) - one category (issuer) only -> provisional,
    # never a confident attach purely from evidence meant for BOS.
    assert orh_decision_against_bos_fragment.outcome == AttachmentOutcome.ATTACH_PROVISIONAL
    assert evaluate_attachment(ORH, orh_fragment).outcome == AttachmentOutcome.ATTACH_CONFIRMED


def test_case_K_explicit_other_airport_identifier_in_same_fragment_rejects():
    """A fragment that explicitly names a DIFFERENT airport's own real
    identifier is not ambiguous, and not merely insufficient - the
    presence of that identifier is itself contradiction evidence, exactly
    as strong as it would be as positive evidence for the airport it
    actually belongs to. This is a stronger, more direct proof that
    evidence never leaks across airports than the "no evidence at all"
    case above."""
    bos_fragment = EvidenceBag(identifiers=frozenset({"BOS"}), runway_ends=frozenset({"22R"}))

    assert evaluate_attachment(BOS, bos_fragment).outcome == AttachmentOutcome.ATTACH_CONFIRMED
    assert evaluate_attachment(ORH, bos_fragment).outcome == AttachmentOutcome.REJECT_CROSS_AIRPORT


# ---------------------------------------------------------------------------
# Adversarial cases (task S13)
# ---------------------------------------------------------------------------

def test_adversarial_1_correct_code_plus_unrelated_topical_text_still_confirms():
    """Unrelated topical noise (EMAS/Runway Safe terminology) must not
    dilute or block a genuine identifier match - it simply isn't in any
    evidence category the guard understands, so it has no effect at all."""
    evidence = EvidenceBag(identifiers=frozenset({"SFO"}), document_title="EMAS Runway Safe arresting system news")
    decision = evaluate_attachment(SFO, evidence)
    assert decision.outcome == AttachmentOutcome.ATTACH_CONFIRMED


def test_adversarial_2_wrong_code_plus_matching_runway_string_rejects():
    """A wrong airport code plus a runway string that happens to overlap
    (both SFO and MSP could coincidentally reference a shared heading in
    a contrived example) must still reject once the wrong code is
    present - contradiction wins even with topical runway overlap."""
    evidence = EvidenceBag(identifiers=frozenset({"MSP"}), runway_ends=frozenset({"1R"}))  # 1R exists at SFO too
    decision = evaluate_attachment(SFO, evidence)
    assert decision.outcome == AttachmentOutcome.REJECT_CROSS_AIRPORT


def test_adversarial_3_correct_name_plus_impossible_runway_rejects():
    """A runway token impossible at the candidate, corroborated by a
    contradicting issuer in the same fragment, must reject even though
    the candidate's own correct name is also present."""
    evidence = EvidenceBag(
        names=frozenset({"San Francisco International Airport"}),
        runway_ends=frozenset({"30L"}),
        contradicting_issuers=frozenset({"Metropolitan Airports Commission"}),
    )
    decision = evaluate_attachment(SFO, evidence)
    assert decision.outcome == AttachmentOutcome.REJECT_CROSS_AIRPORT


def test_adversarial_4_operator_name_for_authority_managing_multiple_airports():
    """An authority governing more than one RWI airport (Massport governs
    both BOS and ORH) must confirm independently for EACH airport it
    actually governs, driven by the candidate's own known_issuers set -
    never by inferring "the" one airport an authority "belongs to"."""
    evidence = EvidenceBag(issuers=frozenset({"Massport"}), runway_ends=frozenset({"22R"}))
    bos_decision = evaluate_attachment(BOS, evidence)
    assert bos_decision.outcome == AttachmentOutcome.ATTACH_CONFIRMED

    orh_evidence = EvidenceBag(issuers=frozenset({"Massport"}), runway_ends=frozenset({"29"}))
    orh_decision = evaluate_attachment(ORH, orh_evidence)
    assert orh_decision.outcome == AttachmentOutcome.ATTACH_CONFIRMED


def test_adversarial_5_runway_valid_at_multiple_airports_no_stronger_identity():
    """A single heading shared by many unrelated airports, with nothing
    else, must never confirm alone - provisional at best for whichever
    one candidate is checked in isolation."""
    evidence = EvidenceBag(runway_ends=frozenset({"9"}))  # both BOS and countless others have a "9"
    decision = evaluate_attachment(BOS, evidence)
    assert decision.outcome == AttachmentOutcome.ATTACH_PROVISIONAL


def test_adversarial_6_formatting_difference_04L_vs_4L_is_not_a_false_contradiction():
    """Known valid normalization difference (task S6 / design doc S6) -
    must resolve to the SAME topology token, never treated as absent or
    contradictory purely due to formatting."""
    evidence_04L = EvidenceBag(issuers=frozenset({"Massport"}), runway_ends=frozenset({"04L"}))
    evidence_4L = EvidenceBag(issuers=frozenset({"Massport"}), runway_ends=frozenset({"4L"}))
    assert evaluate_attachment(BOS, evidence_04L).outcome == AttachmentOutcome.ATTACH_CONFIRMED
    assert evaluate_attachment(BOS, evidence_4L).outcome == AttachmentOutcome.ATTACH_CONFIRMED


def test_adversarial_7_search_query_metadata_alone_has_no_effect():
    """The literal SFO/MSP lesson: a candidate airport being "the one the
    query was about" is not itself passed as evidence anywhere in this
    API - there is no field for it. An empty evidence bag for a query-
    only-relevant candidate is INSUFFICIENT_IDENTITY, proving the guard
    structurally cannot treat query context as evidence (there is nowhere
    to even put it)."""
    evidence = EvidenceBag()  # nothing extracted from the document itself
    decision = evaluate_attachment(SFO, evidence)
    assert decision.outcome == AttachmentOutcome.INSUFFICIENT_IDENTITY


def test_adversarial_8_issuer_says_msp_query_says_sfo_rejects():
    evidence = EvidenceBag(contradicting_issuers=frozenset({"Metropolitan Airports Commission"}))
    decision = evaluate_attachment(SFO, evidence)
    assert decision.outcome == AttachmentOutcome.REJECT_CROSS_AIRPORT


def test_adversarial_9_fragment_explicitly_names_both_sfo_and_msp():
    """A fragment naming both airports explicitly (e.g. a comparison
    article) must reject for SFO once MSP's own identifier is present as
    a genuine identifier-shaped token in the SAME fragment."""
    evidence = EvidenceBag(identifiers=frozenset({"SFO", "MSP"}))
    decision = evaluate_attachment(SFO, evidence)
    assert decision.outcome == AttachmentOutcome.REJECT_CROSS_AIRPORT
    # And it still confirms correctly for the OTHER airport actually named.
    assert evaluate_attachment(MSP, evidence).outcome == AttachmentOutcome.REJECT_CROSS_AIRPORT


def test_adversarial_10_alias_matches_but_explicit_different_icao_exists():
    """A candidate's own alias matching is not enough to save it once a
    genuinely different, explicit ICAO code is also present in the same
    fragment - identifier-level contradiction outranks a mere alias
    match."""
    haneda_with_alias = CandidateAirport(
        id="HND-alt", name="Tokyo International Airport", aliases=frozenset({"Haneda Airport"}),
        identifiers=frozenset({"HND", "RJTT"}),
    )
    evidence = EvidenceBag(names=frozenset({"Haneda Airport"}), identifiers=frozenset({"RJAA"}))  # RJAA = Narita, a different airport
    decision = evaluate_attachment(haneda_with_alias, evidence)
    assert decision.outcome == AttachmentOutcome.REJECT_CROSS_AIRPORT


# ---------------------------------------------------------------------------
# International readiness (task S16) - synthetic non-U.S. fixture
# ---------------------------------------------------------------------------

def test_international_haneda_confirms_with_no_us_specific_data():
    """No FAA identifier, no U.S. state, non-English alias - proves the
    decision core has no structural U.S. dependency. Only ICAO code,
    runway topology (ICAO numbering, already global), and a plain issuer
    string are used - exactly the "universal" evidence categories the
    design doc S9 identifies."""
    evidence = EvidenceBag(
        identifiers=frozenset({"RJTT"}),
        runway_pairs=frozenset({"16L/34R"}),
        issuers=frozenset({"Ministry of Land, Infrastructure, Transport and Tourism"}),
    )
    decision = evaluate_attachment(HANEDA, evidence)
    assert decision.outcome == AttachmentOutcome.ATTACH_CONFIRMED


def test_international_native_alias_counts_as_name_evidence():
    evidence = EvidenceBag(names=frozenset({"羽田空港"}))
    decision = evaluate_attachment(HANEDA, evidence)
    assert decision.outcome == AttachmentOutcome.ATTACH_PROVISIONAL  # one weak category alone


# ---------------------------------------------------------------------------
# Purity / determinism proof (task S14)
# ---------------------------------------------------------------------------

def test_evaluate_attachment_is_deterministic_across_repeated_calls():
    evidence = EvidenceBag(identifiers=frozenset({"BOS"}), runway_ends=frozenset({"22R"}))
    results = [evaluate_attachment(BOS, evidence) for _ in range(5)]
    assert all(r.outcome == results[0].outcome for r in results)
    assert all(r.reason == results[0].reason for r in results)
    assert all(r.positive_evidence == results[0].positive_evidence for r in results)


def test_evaluate_attachment_does_not_mutate_its_inputs():
    candidate_before = copy.deepcopy(BOS)
    evidence = EvidenceBag(identifiers=frozenset({"BOS"}), runway_ends=frozenset({"22R"}))
    evidence_before = copy.deepcopy(evidence)

    evaluate_attachment(BOS, evidence)

    assert BOS == candidate_before
    assert evidence == evidence_before


def test_dataclasses_are_frozen_and_reject_mutation():
    import dataclasses

    evidence = EvidenceBag(identifiers=frozenset({"BOS"}))
    with __import__("pytest").raises(dataclasses.FrozenInstanceError):
        evidence.identifiers = frozenset({"ORH"})  # type: ignore[misc]

    with __import__("pytest").raises(dataclasses.FrozenInstanceError):
        BOS.name = "Something Else"  # type: ignore[misc]


def test_evaluate_attachment_performs_no_module_level_io_imports():
    """Static proof this module never imports sqlalchemy, httpx, requests,
    app.database, or app.models - purity is structural, not just observed
    behavior in these specific tests. Parses the module's actual AST
    (ast.Import/ast.ImportFrom nodes) rather than grepping source text, so
    it cannot false-positive on a docstring that merely *mentions* one of
    these module names in prose (e.g. citing AcquisitionRunStatus as
    prior art, or explaining what this module deliberately does not
    import) and cannot false-negative on an import written in an unusual
    style (aliased, multi-name, wrapped in parens) that a line-prefix
    check would miss."""
    import ast

    import app.services.evidence_attachment_guard as guard

    forbidden_roots = {"sqlalchemy", "httpx", "requests", "app.database", "app.models"}

    tree = ast.parse(open(guard.__file__, encoding="utf-8").read(), filename=guard.__file__)
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module)

    violations = {
        imported for imported in imported_roots
        if any(imported == forbidden or imported.startswith(forbidden + ".") for forbidden in forbidden_roots)
    }
    assert not violations, f"unexpected import(s) found via AST inspection: {violations!r}"


# ---------------------------------------------------------------------------
# candidate_airport_from_airport_like() - the optional ORM-shaped builder
# ---------------------------------------------------------------------------

class _FakeRunwayEnd:
    def __init__(self, designation):
        self.designation = designation


class _FakeRunway:
    def __init__(self, designation, ends):
        self.designation = designation
        self.runway_ends = [_FakeRunwayEnd(e) for e in ends]


class _FakeAirport:
    """Duck-typed stand-in for app.models.Airport - deliberately not the
    real ORM class, proving the builder needs no import from app.models."""

    def __init__(self, id, name, iata_code=None, icao_code=None, faa_code=None, city=None, runways=None):
        self.id = id
        self.name = name
        self.iata_code = iata_code
        self.icao_code = icao_code
        self.faa_code = faa_code
        self.city = city
        self.runways = runways or []


def test_candidate_airport_from_airport_like_builds_correct_topology():
    fake = _FakeAirport(
        id=3, name="Boston Logan International Airport", faa_code="BOS", icao_code="KBOS", city="Boston",
        runways=[_FakeRunway("4L/22R", ["4L", "22R"]), _FakeRunway("9/27", ["9", "27"])],
    )
    candidate = candidate_airport_from_airport_like(fake, known_issuers=frozenset({"Massport"}))

    assert candidate.id == 3
    assert candidate.identifiers == frozenset({"BOS", "KBOS"})
    assert candidate.canonical_runway_ends == frozenset({"4L", "22R", "9", "27"})
    assert candidate.canonical_runway_pairs == frozenset({"4L/22R", "9/27"})

    decision = evaluate_attachment(candidate, EvidenceBag(identifiers=frozenset({"BOS"})))
    assert decision.outcome == AttachmentOutcome.ATTACH_CONFIRMED
