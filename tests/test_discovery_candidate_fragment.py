"""Tests for app/services/discovery_candidate_fragment.py
(docs/architecture/ai-discovery-candidate-envelope-lifecycle.md,
docs/architecture/ai-discovery-candidate-fragment-core-report.md).

Every test is a pure, synthetic fixture - no database, no network, no
filesystem access anywhere in this file. This module's own purity is
exactly what is being tested, on top of the already-committed guard's own
purity (tests/test_evidence_attachment_guard.py)."""
import copy
import dataclasses
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.services.discovery_candidate_fragment import (
    CandidateFragment,
    CandidateFragmentError,
    DiscoveryContext,
    ExtractedDate,
    ExtractedMoney,
    candidate_fragment_to_evidence_bag,
)
from app.services.evidence_attachment_guard import (
    AttachmentOutcome,
    CandidateAirport,
    evaluate_attachment,
    evaluate_attachment_for_candidates,
)

# ---------------------------------------------------------------------------
# Synthetic candidate airports - identical in shape to
# tests/test_evidence_attachment_guard.py's own fixtures, never the real
# database.
# ---------------------------------------------------------------------------

SFO = CandidateAirport(
    id="SFO",
    name="San Francisco International Airport",
    identifiers=frozenset({"SFO", "KSFO"}),
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


def _artifact(name: str) -> str:
    return f"test-artifact:{name}"


# ---------------------------------------------------------------------------
# 1-3: fragment identity, hash, raw text preservation
# ---------------------------------------------------------------------------

def test_deterministic_fragment_hash():
    a = CandidateFragment(artifact_identity=_artifact("x"), source_locator="p1", raw_text="Runway 30L, Metropolitan Airports Commission")
    b = CandidateFragment(artifact_identity=_artifact("x"), source_locator="p1", raw_text="Runway 30L, Metropolitan Airports Commission")
    assert a.fragment_hash == b.fragment_hash
    assert len(a.fragment_hash) == 64  # sha256 hex digest length
    different = CandidateFragment(artifact_identity=_artifact("x"), source_locator="p1", raw_text="different text")
    assert different.fragment_hash != a.fragment_hash


def test_deterministic_fragment_identity():
    a = CandidateFragment(artifact_identity=_artifact("doc1"), source_locator="page-3", raw_text="EMAS procurement text")
    b = CandidateFragment(artifact_identity=_artifact("doc1"), source_locator="page-3", raw_text="EMAS procurement text")
    assert a.identity == b.identity
    assert a.identity == (_artifact("doc1"), "page-3", a.fragment_hash)
    # Different source_locator -> different identity, even with identical text.
    c = CandidateFragment(artifact_identity=_artifact("doc1"), source_locator="page-4", raw_text="EMAS procurement text")
    assert c.identity != a.identity


def test_raw_text_preserved_verbatim():
    text = "  Replace Runway 29 Departure EMAS (R/W 11 End) — MPA Contract W306.  "
    fragment = CandidateFragment(artifact_identity=_artifact("orh"), source_locator="scope-item-1", raw_text=text)
    assert fragment.raw_text == text


# ---------------------------------------------------------------------------
# 4: search-context firewall (the mandatory invariant)
# ---------------------------------------------------------------------------

def test_search_context_excluded_from_evidence_bag():
    fragment_with_context = CandidateFragment(
        artifact_identity=_artifact("msp-memo"), source_locator="p1",
        raw_text="Runway 30L EMAS procurement",
        issuers=frozenset({"Metropolitan Airports Commission"}),
        runway_ends=frozenset({"30L"}),
        discovery_context=DiscoveryContext(
            search_query="SFO EMAS Runway Safe 2026 contract",
            discovery_channel="web_search", seed_airport="SFO",
            discovered_at=datetime(2026, 8, 1, 12, 0, 0),
        ),
    )
    bag = candidate_fragment_to_evidence_bag(fragment_with_context)
    # No field on EvidenceBag contains "SFO" anywhere, despite the search
    # query and seed_airport both explicitly naming it.
    all_bag_text = " ".join(
        list(bag.identifiers) + list(bag.names) + list(bag.issuers) + list(bag.locations)
        + list(bag.runway_ends) + list(bag.runway_pairs)
        + [bag.document_title or "", bag.project_number or "", bag.contract_number or "", bag.url or ""]
    )
    assert "SFO" not in all_bag_text


def test_evidence_bag_identical_regardless_of_discovery_context():
    base_kwargs = dict(
        artifact_identity=_artifact("msp-memo"), source_locator="p1",
        raw_text="Runway 30L EMAS procurement",
        issuers=frozenset({"Metropolitan Airports Commission"}),
        runway_ends=frozenset({"30L"}),
    )
    no_context = CandidateFragment(**base_kwargs)
    with_context_a = CandidateFragment(
        **base_kwargs,
        discovery_context=DiscoveryContext(search_query="SFO EMAS Runway Safe 2026 contract", seed_airport="SFO"),
    )
    with_context_b = CandidateFragment(
        **base_kwargs,
        discovery_context=DiscoveryContext(search_query="something totally different", seed_airport="ORH", discovery_channel="n8n"),
    )

    bag_none = candidate_fragment_to_evidence_bag(no_context)
    bag_a = candidate_fragment_to_evidence_bag(with_context_a)
    bag_b = candidate_fragment_to_evidence_bag(with_context_b)

    assert bag_none == bag_a == bag_b


def test_adapter_never_reads_discovery_context_attribute():
    """Structural proof, not just behavioral: replacing discovery_context
    with an object that raises on any attribute access still lets the
    adapter succeed, proving no code path in the adapter ever touches it."""

    class _ExplodingContext:
        def __getattr__(self, name):
            raise AssertionError(f"candidate_fragment_to_evidence_bag() must never read discovery_context.{name}")

    fragment = CandidateFragment(
        artifact_identity=_artifact("x"), source_locator="p1", raw_text="text",
        discovery_context=_ExplodingContext(),  # type: ignore[arg-type]
    )
    candidate_fragment_to_evidence_bag(fragment)  # must not raise


# ---------------------------------------------------------------------------
# 5-9: extracted identity fields mapped correctly
# ---------------------------------------------------------------------------

def test_airport_identifiers_mapped():
    fragment = CandidateFragment(artifact_identity=_artifact("x"), source_locator="p1", raw_text="t", airport_identifiers=frozenset({"BOS"}))
    bag = candidate_fragment_to_evidence_bag(fragment)
    assert bag.identifiers == frozenset({"BOS"})


def test_names_mapped():
    fragment = CandidateFragment(artifact_identity=_artifact("x"), source_locator="p1", raw_text="t", airport_names=frozenset({"Boston Logan International Airport"}))
    bag = candidate_fragment_to_evidence_bag(fragment)
    assert bag.names == frozenset({"Boston Logan International Airport"})


def test_locations_mapped():
    fragment = CandidateFragment(artifact_identity=_artifact("x"), source_locator="p1", raw_text="t", locations=frozenset({"Worcester"}))
    bag = candidate_fragment_to_evidence_bag(fragment)
    assert bag.locations == frozenset({"Worcester"})


def test_runways_normalized_safely_via_guard_not_adapter():
    """The adapter itself passes runway tokens through RAW (un-normalized)
    - normalization is the guard's job (app/services/evidence_attachment_guard.py),
    never duplicated here. Proven end to end: "04L" survives the adapter
    unchanged, then the guard still recognizes it against a candidate
    whose canonical topology stores "4L"."""
    fragment = CandidateFragment(
        artifact_identity=_artifact("bos"), source_locator="p1", raw_text="Massport Runway 04L work",
        issuers=frozenset({"Massport"}), runway_ends=frozenset({"04L"}),
    )
    bag = candidate_fragment_to_evidence_bag(fragment)
    assert bag.runway_ends == frozenset({"04L"})  # raw, unmodified by the adapter
    decision = evaluate_attachment(BOS, bag)
    assert decision.outcome == AttachmentOutcome.ATTACH_CONFIRMED  # guard normalizes 04L -> 4L


def test_issuer_mapped():
    fragment = CandidateFragment(artifact_identity=_artifact("x"), source_locator="p1", raw_text="t", issuers=frozenset({"Massport"}))
    bag = candidate_fragment_to_evidence_bag(fragment)
    assert bag.issuers == frozenset({"Massport"})


# ---------------------------------------------------------------------------
# 10-13: project/contract identifiers, money, dates - extraction facts,
# never resolved to DB rows, never guard inputs.
# ---------------------------------------------------------------------------

def test_project_identifiers_preserved():
    fragment = CandidateFragment(
        artifact_identity=_artifact("bos"), source_locator="p1", raw_text="MPA Project No. L1633",
        project_identifiers=frozenset({"L1633"}),
    )
    assert fragment.project_identifiers == frozenset({"L1633"})
    # Not a guard input - EvidenceBag has no per-project-identifier category,
    # only an audit-only joined string.
    bag = candidate_fragment_to_evidence_bag(fragment)
    assert bag.project_number == "L1633"


def test_contract_identifiers_preserved():
    fragment = CandidateFragment(
        artifact_identity=_artifact("orh"), source_locator="p1", raw_text="MPA Contract No. W306",
        contract_identifiers=frozenset({"W306"}),
    )
    assert fragment.contract_identifiers == frozenset({"W306"})
    bag = candidate_fragment_to_evidence_bag(fragment)
    assert bag.contract_number == "W306"


def test_multiple_project_and_contract_identifiers_joined_for_audit_only():
    fragment = CandidateFragment(
        artifact_identity=_artifact("x"), source_locator="p1", raw_text="t",
        project_identifiers=frozenset({"L1633", "L1828"}),
        contract_identifiers=frozenset({"W269-C1", "W269-C2"}),
    )
    bag = candidate_fragment_to_evidence_bag(fragment)
    assert bag.project_number == "L1633, L1828"
    assert bag.contract_number == "W269-C1, W269-C2"


def test_money_extraction_shape_preserved_and_not_auto_interpreted():
    """The exact SFO 2026 $40M lesson: a dollar figure is preserved as a
    plain extraction fact; its semantic role stays None (unknown) unless
    extraction explicitly determined it - never assumed to be "the
    project budget" merely because it's the only number found."""
    money = ExtractedMoney(raw_text="$40 million", numeric_value=Decimal("40000000"), currency="USD", context_label=None)
    fragment = CandidateFragment(
        artifact_identity=_artifact("sfo"), source_locator="p1", raw_text="...up to $40 million...",
        money_values=(money,),
    )
    assert fragment.money_values == (money,)
    assert fragment.money_values[0].context_label is None  # role NOT guessed
    # Money is never mapped into EvidenceBag - the guard has no financial
    # reasoning at all.
    bag = candidate_fragment_to_evidence_bag(fragment)
    assert not hasattr(bag, "money_values")
    assert not hasattr(bag, "numeric_value")


def test_money_with_explicit_context_label_preserved():
    money = ExtractedMoney(raw_text="$1,590,000.00", numeric_value=Decimal("1590000.00"), currency="USD", context_label="advance_deposit")
    fragment = CandidateFragment(artifact_identity=_artifact("msp"), source_locator="p1", raw_text="advance deposit of $1,590,000.00", money_values=(money,))
    assert fragment.money_values[0].context_label == "advance_deposit"


def test_date_extraction_shape_preserved():
    d = ExtractedDate(raw_text="August 28, 2024", normalized_date=date(2024, 8, 28), semantic_role="memo_date")
    fragment = CandidateFragment(artifact_identity=_artifact("msp"), source_locator="p1", raw_text="DATE: August 28, 2024", dates=(d,))
    assert fragment.dates == (d,)
    assert fragment.dates[0].semantic_role == "memo_date"
    bag = candidate_fragment_to_evidence_bag(fragment)
    assert not hasattr(bag, "dates")


def test_date_with_unknown_semantic_role_preserved_as_none():
    d = ExtractedDate(raw_text="2026-08-06", normalized_date=date(2026, 8, 6))
    fragment = CandidateFragment(artifact_identity=_artifact("x"), source_locator="p1", raw_text="2026-08-06", dates=(d,))
    assert fragment.dates[0].semantic_role is None


# ---------------------------------------------------------------------------
# 14-15: SFO/MSP - the mandatory worked case
# ---------------------------------------------------------------------------

def test_sfo_msp_fragment_rejects_for_sfo():
    """The adapter never auto-classifies a found issuer as "contradicting"
    for a given candidate - free-text issuer/name/location strings are
    not self-verifying the way an identifier is (module docstring; guard
    core report S3). This fixture models what upstream orchestration
    would have ALREADY determined before evaluating against SFO
    specifically (an issuer/reference-table lookup resolved "Metropolitan
    Airports Commission" as belonging to a different, specific airport,
    not SFO) - hence contradicting_issuers, not issuers. Contrast with
    test_sfo_msp_fragment_confirms_for_msp below, which evaluates the
    same underlying facts against the airport they actually describe."""
    fragment = CandidateFragment(
        artifact_identity=_artifact("msp-memo"), source_locator="p1-p2",
        raw_text=(
            "Metropolitan Airports Commission. Engineered Material Arresting Systems (EMAS) "
            "Procurement Advance Deposit. The Runway 30L Engineered Material Arresting System "
            "has reached its life expectancy. Sole source procurement with Runway Safe. "
            "$1,590,000.00 advance deposit."
        ),
        contradicting_issuers=frozenset({"Metropolitan Airports Commission"}),
        runway_ends=frozenset({"30L"}),
        terminology_hits=frozenset({"EMAS", "Runway Safe", "sole source"}),
        money_values=(ExtractedMoney(raw_text="$1,590,000.00", numeric_value=Decimal("1590000.00"), currency="USD", context_label="advance_deposit"),),
        discovery_context=DiscoveryContext(search_query="SFO EMAS Runway Safe 2026 contract", seed_airport="SFO"),
    )
    bag = candidate_fragment_to_evidence_bag(fragment)
    decision = evaluate_attachment(SFO, bag)
    assert decision.outcome == AttachmentOutcome.REJECT_CROSS_AIRPORT


def test_sfo_msp_fragment_confirms_for_msp():
    """The SAME fragment (same raw_text, same extracted evidence),
    evaluated against the airport it actually concerns."""
    fragment = CandidateFragment(
        artifact_identity=_artifact("msp-memo"), source_locator="p1-p2",
        raw_text="Metropolitan Airports Commission. Runway 30L EMAS. Sole source procurement with Runway Safe.",
        issuers=frozenset({"Metropolitan Airports Commission"}),
        runway_ends=frozenset({"30L"}),
        discovery_context=DiscoveryContext(search_query="SFO EMAS Runway Safe 2026 contract", seed_airport="SFO"),
    )
    bag = candidate_fragment_to_evidence_bag(fragment)
    decision = evaluate_attachment(MSP, bag)
    assert decision.outcome == AttachmentOutcome.ATTACH_CONFIRMED


# ---------------------------------------------------------------------------
# 16: genuine SFO case
# ---------------------------------------------------------------------------

def test_genuine_sfo_fragment_confirms():
    fragment = CandidateFragment(
        artifact_identity=_artifact("sfo-project-page"), source_locator="body",
        raw_text="San Francisco International Airport (SFO). RWY 1R/19L Rehabilitation and TWY W. EMAS seam replacement.",
        airport_identifiers=frozenset({"SFO"}),
        runway_pairs=frozenset({"1R/19L"}),
        terminology_hits=frozenset({"EMAS", "rehabilitation"}),
    )
    bag = candidate_fragment_to_evidence_bag(fragment)
    decision = evaluate_attachment(SFO, bag)
    assert decision.outcome == AttachmentOutcome.ATTACH_CONFIRMED


# ---------------------------------------------------------------------------
# 17-18: BOS / ORH cases - physical vs. protected direction stays out of
# scope for the guard (topology membership only).
# ---------------------------------------------------------------------------

def test_bos_massport_runway_22r_fragment_confirms():
    fragment = CandidateFragment(
        artifact_identity=_artifact("bos-press-release"), source_locator="para-3",
        raw_text="Massport: Boston Logan currently has two other EMAS systems, one at Runway 22R.",
        issuers=frozenset({"Massport"}),
        runway_ends=frozenset({"22R"}),
    )
    bag = candidate_fragment_to_evidence_bag(fragment)
    decision = evaluate_attachment(BOS, bag)
    assert decision.outcome == AttachmentOutcome.ATTACH_CONFIRMED


def test_orh_mpa_dual_naming_fragment_confirms():
    fragment = CandidateFragment(
        artifact_identity=_artifact("orh-contract-w306"), source_locator="scope-1-2",
        raw_text="Replace Runway 29 Departure EMAS (R/W 11 End); Replace Runway 11 Departure EMAS (R/W 29 End).",
        issuers=frozenset({"MPA"}),
        runway_ends=frozenset({"29", "11"}),
        contract_identifiers=frozenset({"W306"}),
    )
    bag = candidate_fragment_to_evidence_bag(fragment)
    decision = evaluate_attachment(ORH, bag)
    assert decision.outcome == AttachmentOutcome.ATTACH_CONFIRMED


# ---------------------------------------------------------------------------
# 19: insufficient-identity preservation
# ---------------------------------------------------------------------------

def test_insufficient_identity_fragment_still_valid_and_preserved():
    """A useful fragment - real EMAS/procurement terminology, a contract
    number, a dollar figure - but no airport identifier/name/issuer/
    runway strong enough to resolve. The CandidateFragment itself
    construction succeeds and preserves everything; only the GUARD
    outcome is INSUFFICIENT_IDENTITY."""
    fragment = CandidateFragment(
        artifact_identity=_artifact("unknown-procurement"), source_locator="p1",
        raw_text="Engineered Material Arresting System procurement, Contract No. 2026-EMAS-014, $6,200,000.",
        terminology_hits=frozenset({"Engineered Material Arresting System"}),
        contract_identifiers=frozenset({"2026-EMAS-014"}),
        money_values=(ExtractedMoney(raw_text="$6,200,000", numeric_value=Decimal("6200000"), currency="USD"),),
    )
    # Construction succeeds - extraction does not require airport resolution.
    assert fragment.raw_text
    assert fragment.contract_identifiers == frozenset({"2026-EMAS-014"})

    bag = candidate_fragment_to_evidence_bag(fragment)
    decision = evaluate_attachment(SFO, bag)
    assert decision.outcome == AttachmentOutcome.INSUFFICIENT_IDENTITY
    # Nothing about the fragment itself was discarded because identity failed.
    assert fragment.money_values[0].numeric_value == Decimal("6200000")


# ---------------------------------------------------------------------------
# 20: multi-airport fragment
# ---------------------------------------------------------------------------

def test_multi_airport_fragment_does_not_pick_one_itself():
    """A fragment naming an authority that governs BOTH BOS and ORH, with
    runway evidence for only one of them. CandidateFragment/the adapter
    must not resolve this - evaluate_attachment_for_candidates() does,
    per-candidate, independently."""
    fragment = CandidateFragment(
        artifact_identity=_artifact("massport-capital-bill"), source_locator="para-7",
        raw_text="Massport capital improvement bill covering Logan and Worcester Regional airfield safety work.",
        issuers=frozenset({"Massport"}),
    )
    bag = candidate_fragment_to_evidence_bag(fragment)

    # The fragment/adapter itself contains no attachment decision at all.
    assert not hasattr(fragment, "airport_id")
    assert not hasattr(bag, "airport_id")

    decisions = evaluate_attachment_for_candidates(bag, [BOS, ORH])
    # Issuer alone (1 category) qualifies both independently as provisional,
    # and because both qualify for the same evidence, ambiguity resolution
    # correctly escalates both to REVIEW_REQUIRED rather than silently
    # picking one.
    assert decisions["BOS"].outcome == AttachmentOutcome.REVIEW_REQUIRED
    assert decisions["ORH"].outcome == AttachmentOutcome.REVIEW_REQUIRED


def test_multi_airport_fragment_with_distinguishing_runway_evidence_resolves_independently():
    """When each candidate additionally gets its OWN runway evidence
    (i.e. two separately-scoped EvidenceBags, exactly as real
    orchestration would build for two distinguishable sub-fragments),
    each resolves ATTACH_CONFIRMED independently - no merging."""
    bos_fragment = CandidateFragment(
        artifact_identity=_artifact("massport-capital-bill"), source_locator="para-7a",
        raw_text="...Logan Runway 22R work...", issuers=frozenset({"Massport"}), runway_ends=frozenset({"22R"}),
    )
    orh_fragment = CandidateFragment(
        artifact_identity=_artifact("massport-capital-bill"), source_locator="para-7b",
        raw_text="...Worcester Runway 29 work...", issuers=frozenset({"Massport"}), runway_ends=frozenset({"29"}),
    )
    assert evaluate_attachment(BOS, candidate_fragment_to_evidence_bag(bos_fragment)).outcome == AttachmentOutcome.ATTACH_CONFIRMED
    assert evaluate_attachment(ORH, candidate_fragment_to_evidence_bag(orh_fragment)).outcome == AttachmentOutcome.ATTACH_CONFIRMED


# ---------------------------------------------------------------------------
# 21: international / native-language fixture
# ---------------------------------------------------------------------------

def test_international_haneda_fragment_confirms_with_original_text_preserved():
    original_text = "羽田空港 滑走路16L/34R エンジニアド・マテリアル・アレスティング・システム（EMAS）"
    fragment = CandidateFragment(
        artifact_identity=_artifact("haneda-procurement"), source_locator="p1",
        raw_text=original_text,
        airport_identifiers=frozenset({"RJTT"}),
        airport_names=frozenset({"羽田空港"}),
        runway_pairs=frozenset({"16L/34R"}),
        issuers=frozenset({"Ministry of Land, Infrastructure, Transport and Tourism"}),
        language="ja",
    )
    assert fragment.raw_text == original_text  # original preserved, no translation applied
    bag = candidate_fragment_to_evidence_bag(fragment)
    decision = evaluate_attachment(HANEDA, bag)
    assert decision.outcome == AttachmentOutcome.ATTACH_CONFIRMED


# ---------------------------------------------------------------------------
# 22-23: no DB / no network imports (AST-based, robust)
# ---------------------------------------------------------------------------

def test_no_db_or_network_imports():
    import ast

    import app.services.discovery_candidate_fragment as mod

    forbidden_roots = {"sqlalchemy", "httpx", "requests", "app.database", "app.models"}
    tree = ast.parse(open(mod.__file__, encoding="utf-8").read(), filename=mod.__file__)
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
# 24-26: immutability, no input mutation, deterministic adapter result
# ---------------------------------------------------------------------------

def test_candidate_fragment_and_helpers_are_frozen():
    fragment = CandidateFragment(artifact_identity=_artifact("x"), source_locator="p1", raw_text="t")
    with pytest.raises(dataclasses.FrozenInstanceError):
        fragment.raw_text = "changed"  # type: ignore[misc]

    money = ExtractedMoney(raw_text="$1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        money.raw_text = "$2"  # type: ignore[misc]

    d = ExtractedDate(raw_text="2026-01-01")
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.raw_text = "2026-01-02"  # type: ignore[misc]

    ctx = DiscoveryContext(search_query="q")
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.search_query = "changed"  # type: ignore[misc]


def test_adapter_does_not_mutate_its_input():
    fragment = CandidateFragment(
        artifact_identity=_artifact("x"), source_locator="p1", raw_text="t",
        airport_identifiers=frozenset({"BOS"}), issuers=frozenset({"Massport"}),
    )
    before = copy.deepcopy(fragment)
    candidate_fragment_to_evidence_bag(fragment)
    assert fragment == before


def test_adapter_is_deterministic():
    fragment = CandidateFragment(
        artifact_identity=_artifact("x"), source_locator="p1", raw_text="t",
        airport_identifiers=frozenset({"ORH"}), issuers=frozenset({"MPA"}), runway_ends=frozenset({"11", "29"}),
    )
    bags = [candidate_fragment_to_evidence_bag(fragment) for _ in range(5)]
    assert all(b == bags[0] for b in bags)
    decisions = [evaluate_attachment(ORH, b) for b in bags]
    assert all(d.outcome == decisions[0].outcome for d in decisions)


# ---------------------------------------------------------------------------
# Fail-closed construction (mirrors FAAEmasSnapshotParser's own typed-error
# convention)
# ---------------------------------------------------------------------------

def test_missing_artifact_identity_fails_closed():
    with pytest.raises(CandidateFragmentError):
        CandidateFragment(artifact_identity="", source_locator="p1", raw_text="t")


def test_missing_source_locator_fails_closed():
    with pytest.raises(CandidateFragmentError):
        CandidateFragment(artifact_identity=_artifact("x"), source_locator="", raw_text="t")


def test_empty_raw_text_fails_closed():
    with pytest.raises(CandidateFragmentError):
        CandidateFragment(artifact_identity=_artifact("x"), source_locator="p1", raw_text="   ")
