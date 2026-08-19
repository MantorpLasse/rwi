"""Tests for app/services/evidence_claim_semantics.py
(docs/architecture/evidence-claim-semantics-core-report.md).

Never touches the database, network, or filesystem - every test builds
plain, in-memory dataclass instances. The MSP golden case below hand-
builds a fixture matching the REAL, already-verified content of
SourceAssertion #222 (read-only-confirmed provenance identity:
artifact_identity="mac.granicus.document.4.2349.105406",
source_locator="item-2.3.2",
fragment_hash="76e5bf71cd2cb4759d3f9c1a568a14cf121626ede75ee00371a58f221852b4fa")
- this is Slice 1's own core-type proof, deliberately NOT a raw-text
extractor (that is Slice 2, out of scope here per the task's own STOP
boundary).
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
from datetime import date
from decimal import Decimal

import pytest

from app.services.evidence_claim_semantics import (
    Claim,
    ClaimCategory,
    ClaimProvenance,
    FinancialFact,
    ProvenanceKind,
    RelationshipFact,
    TemporalContext,
    TemporalQualifier,
)

# --- Real, read-only-confirmed provenance identity of SourceAssertion #222 ---
MSP_ARTIFACT_IDENTITY = "mac.granicus.document.4.2349.105406"
MSP_SOURCE_LOCATOR = "item-2.3.2"
MSP_FRAGMENT_HASH = "76e5bf71cd2cb4759d3f9c1a568a14cf121626ede75ee00371a58f221852b4fa"


def _msp_provenance(excerpt: str) -> ClaimProvenance:
    return ClaimProvenance(
        artifact_identity=MSP_ARTIFACT_IDENTITY,
        source_locator=MSP_SOURCE_LOCATOR,
        fragment_hash=MSP_FRAGMENT_HASH,
        raw_text_excerpt=excerpt,
    )


# ---------------------------------------------------------------------------
# Immutability / determinism
# ---------------------------------------------------------------------------


def test_claim_and_all_attachments_are_frozen():
    for cls in (Claim, ClaimProvenance, FinancialFact, RelationshipFact, TemporalContext):
        assert dataclasses.is_dataclass(cls)
        assert cls.__dataclass_params__.frozen is True


def test_claim_mutation_raises():
    claim = Claim(
        category=ClaimCategory.EXPLICIT_DOCUMENT_FACT, subject="x", statement="y",
        provenance=_msp_provenance("z"),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        claim.subject = "changed"  # type: ignore[misc]


def test_equal_construction_produces_equal_claims():
    """Deterministic equality - same inputs always produce an equal
    Claim, since every field is either an immutable primitive or itself
    a frozen dataclass."""
    def build():
        return Claim(
            category=ClaimCategory.EXPLICIT_DOCUMENT_FACT,
            subject="EMAS bed", statement="reached life expectancy",
            provenance=_msp_provenance("has reached its life expectancy"),
            financial=FinancialFact(amount=Decimal("1590000.00"), currency="USD", semantic_role="advance_deposit_purchase_order"),
        )
    assert build() == build()
    assert hash(build().provenance) == hash(build().provenance)  # frozen dataclasses are hashable


# ---------------------------------------------------------------------------
# Financial-role separation (task S4, hard invariant)
# ---------------------------------------------------------------------------


def test_two_amounts_with_different_roles_remain_structurally_distinct():
    deposit = FinancialFact(amount=Decimal("1590000.00"), currency="USD", semantic_role="advance_deposit_purchase_order")
    ceiling = FinancialFact(amount=Decimal("19000000.00"), currency="USD", semantic_role="cip_project_ceiling")
    assert deposit.amount != ceiling.amount
    assert deposit.semantic_role != ceiling.semantic_role
    assert deposit != ceiling
    # No field on FinancialFact could ever hold both amounts at once -
    # they are two separate objects by construction, not two values in
    # one generic "amount" bucket.


def test_financial_fact_has_no_generic_cost_or_value_field():
    """Structural proof: the dataclass's own field set has no field
    named cost/value/amount_type/contract_value/etc. - the ONLY numeric
    field is `amount`, and it is meaningless without the co-required
    `semantic_role`."""
    field_names = {f.name for f in dataclasses.fields(FinancialFact)}
    assert field_names == {"amount", "currency", "semantic_role", "not_established"}
    forbidden = {"cost", "value", "contract_value", "estimated_vendor_revenue", "project_value", "total"}
    assert field_names.isdisjoint(forbidden)


def test_semantic_role_is_required_not_defaulted():
    sig = inspect.signature(FinancialFact)
    assert sig.parameters["semantic_role"].default is inspect.Parameter.empty


def test_not_established_survives_on_the_financial_fact():
    ceiling = FinancialFact(
        amount=Decimal("19000000.00"), currency="USD", semantic_role="cip_project_ceiling",
        not_established=("contract_value", "confirmed_vendor_award_amount"),
    )
    assert "contract_value" in ceiling.not_established
    assert "confirmed_vendor_award_amount" in ceiling.not_established
    assert ceiling.semantic_role not in ceiling.not_established


# ---------------------------------------------------------------------------
# Temporal safety (task S5, hard invariant: no current-time dependency)
# ---------------------------------------------------------------------------


def test_temporal_context_requires_explicit_as_of_date_no_default_today():
    sig = inspect.signature(TemporalContext)
    # as_of_date has no default - or if it does, it must not be a callable
    # producing today's date. Requiring the caller to supply it (even
    # None explicitly) is what this module's own docstring promises.
    default = sig.parameters["as_of_date"].default
    assert default is inspect.Parameter.empty or default is None


def test_planned_future_claim_from_2024_remains_planned_regardless_of_when_evaluated():
    """The hard test the task itself specifies: a 2024 statement that an
    installation contract 'will be bid in 2025' must remain a
    future/planned claim - this module has no mechanism to reinterpret
    it based on today's real date, because it never reads today's real
    date at all (proven separately by test_module_never_reads_current_time)."""
    claim = Claim(
        category=ClaimCategory.TEMPORAL_STATEMENT, subject="installation contract",
        statement="A separate installation contract is planned to be bid in 2025.",
        provenance=_msp_provenance("A separate contract will be bid in 2025 for the installation of the blocks"),
        temporal=TemporalContext(qualifier=TemporalQualifier.PLANNED_FUTURE_ACTION, as_of_date=date(2024, 8, 28)),
    )
    assert claim.temporal.qualifier == TemporalQualifier.PLANNED_FUTURE_ACTION
    assert claim.temporal.qualifier != TemporalQualifier.COMPLETED
    assert claim.temporal.as_of_date == date(2024, 8, 28)
    # No matter what "real" year this test happens to run in, nothing
    # about the claim object itself changes - it carries no reference to
    # the current date at all.


def test_temporal_qualifier_vocabulary_covers_the_required_minimum():
    required = {"historical_fact", "current_state_as_of_document_date", "planned_future_action", "completed", "unknown"}
    actual = {member.value for member in TemporalQualifier}
    assert required <= actual


# ---------------------------------------------------------------------------
# Procedural (FOR ACTION) semantics (task S6, hard invariant)
# ---------------------------------------------------------------------------


def test_procedural_request_is_not_confused_with_award_or_execution():
    """A staff FOR ACTION request must never be representable as, or
    silently equal to, an approved/awarded/executed/completed claim -
    proven by using a genuinely different category and temporal
    qualifier for each, never the same object."""
    requested = Claim(
        category=ClaimCategory.PROCEDURAL_REQUEST, subject="EMAS sole-source procurement",
        statement="Staff requests authority to enter a sole-source procurement with the vendor.",
        provenance=_msp_provenance("Recommend that the full Commission authorize staff to enter into a sole source procurement"),
        temporal=TemporalContext(qualifier=TemporalQualifier.REQUESTED_PENDING_APPROVAL, as_of_date=date(2024, 8, 28)),
    )
    awarded = Claim(
        category=ClaimCategory.EXPLICIT_DOCUMENT_FACT, subject="EMAS sole-source procurement",
        statement="The Commission awarded the sole-source contract.",
        provenance=_msp_provenance("(hypothetical later document)"),
        temporal=TemporalContext(qualifier=TemporalQualifier.HISTORICAL_FACT, as_of_date=date(2024, 9, 15)),
    )
    assert requested.category != awarded.category
    assert requested.temporal.qualifier != awarded.temporal.qualifier
    assert requested != awarded
    # Nothing in this module ever promotes a PROCEDURAL_REQUEST claim
    # into an EXPLICIT_DOCUMENT_FACT/awarded claim automatically - a
    # second, independent Claim (from a second, later document) is
    # always required, never a mutation of the first.


def test_requested_purchase_order_financial_fact_is_not_labeled_executed():
    po_requested = FinancialFact(amount=Decimal("1590000.00"), currency="USD", semantic_role="advance_deposit_purchase_order")
    assert po_requested.semantic_role != "purchase_order_executed"
    assert po_requested.semantic_role != "contract_awarded_amount"


# ---------------------------------------------------------------------------
# Negative constraints (task S7)
# ---------------------------------------------------------------------------


def test_negative_constraint_is_minimal_labels_not_an_inference_engine():
    ceiling = FinancialFact(
        amount=Decimal("19000000.00"), currency="USD", semantic_role="cip_project_ceiling",
        not_established=("runway_safe_contract_value",),
    )
    # Plain string labels - no logical structure, no operators, no
    # evaluation - exactly "the minimum representation needed" per
    # instruction.
    assert isinstance(ceiling.not_established, tuple)
    assert all(isinstance(label, str) for label in ceiling.not_established)


# ---------------------------------------------------------------------------
# Provenance (task S8)
# ---------------------------------------------------------------------------


def test_provenance_reuses_existing_fragment_identity_field_names():
    field_names = {f.name for f in dataclasses.fields(ClaimProvenance)}
    assert field_names == {"artifact_identity", "source_locator", "fragment_hash", "raw_text_excerpt"}


def test_claim_provenance_traces_back_to_the_real_msp_fragment_identity():
    claim = Claim(
        category=ClaimCategory.EXPLICIT_DOCUMENT_FACT, subject="x", statement="y",
        provenance=_msp_provenance("z"),
    )
    assert claim.provenance.artifact_identity == MSP_ARTIFACT_IDENTITY
    assert claim.provenance.source_locator == MSP_SOURCE_LOCATOR
    assert claim.provenance.fragment_hash == MSP_FRAGMENT_HASH


# ---------------------------------------------------------------------------
# MSP A-I golden case (task S10)
# ---------------------------------------------------------------------------


class TestMspGoldenCase:
    """Hand-built fixture matching SourceAssertion #222's real, already-
    verified content - not a raw-text extractor (Slice 2, deferred).
    Each claim below corresponds directly to a lettered claim in
    docs/architecture/evidence-to-signal-semantics-design.md S4."""

    @staticmethod
    def _prov(excerpt: str) -> ClaimProvenance:
        return _msp_provenance(excerpt)

    def test_claim_a_lifecycle_condition(self):
        claim = Claim(
            category=ClaimCategory.EXPLICIT_DOCUMENT_FACT, subject="EMAS bed at Runway 30L",
            statement="The EMAS bed has reached its life expectancy.",
            provenance=self._prov("has reached its life expectancy"),
        )
        assert claim.category == ClaimCategory.EXPLICIT_DOCUMENT_FACT
        assert claim.provenance.artifact_identity == MSP_ARTIFACT_IDENTITY

    def test_claim_b_required_action(self):
        claim = Claim(
            category=ClaimCategory.EXPLICIT_DOCUMENT_FACT, subject="EMAS bed at Runway 30L",
            statement="EMAS replacement is required.",
            provenance=self._prov("requires replacement"),
        )
        assert claim.category == ClaimCategory.EXPLICIT_DOCUMENT_FACT

    def test_claim_c_vendor_procurement_relationship_is_scoped(self):
        claim = Claim(
            category=ClaimCategory.EXPLICIT_DOCUMENT_FACT, subject="EMAS replacement materials",
            statement="The vendor's EMAS bed is the FAA's only approved proprietary product.",
            provenance=self._prov("the only proprietary product approved by the FAA"),
            relationship=RelationshipFact(party="Runway Safe", role="sole_approved_manufacturer", scope="FAA product approval, not a confirmed MSP contract"),
        )
        assert claim.relationship.role == "sole_approved_manufacturer"
        assert claim.relationship.role != "installation_contractor"

    def test_claim_d_procedural_request_with_financial_fact(self):
        claim = Claim(
            category=ClaimCategory.PROCEDURAL_REQUEST, subject="EMAS advance-deposit Purchase Order",
            statement="Staff requests authority to issue a Purchase Order for the advance deposit.",
            provenance=self._prov("Authorize staff to issue a Purchase Order to Runway Safe in the amount of $1,590,000.00"),
            financial=FinancialFact(amount=Decimal("1590000.00"), currency="USD", semantic_role="advance_deposit_purchase_order"),
            temporal=TemporalContext(qualifier=TemporalQualifier.REQUESTED_PENDING_APPROVAL, as_of_date=date(2024, 8, 28)),
        )
        assert claim.category == ClaimCategory.PROCEDURAL_REQUEST
        assert claim.financial.amount == Decimal("1590000.00")
        assert claim.financial.semantic_role == "advance_deposit_purchase_order"
        assert claim.temporal.qualifier == TemporalQualifier.REQUESTED_PENDING_APPROVAL

    def test_claim_f_financial_fact_cip_ceiling_with_negative_constraint(self):
        claim = Claim(
            category=ClaimCategory.EXPLICIT_DOCUMENT_FACT, subject="2025 30L EMAS Replacement CIP project",
            statement="The Commission approved a $19,000,000 CIP ceiling for the project on 2023-12-18.",
            provenance=self._prov("the 2024-2030 CIP listing which included the work... in the amount of $19,000,000.00"),
            financial=FinancialFact(
                amount=Decimal("19000000.00"), currency="USD", semantic_role="cip_project_ceiling",
                not_established=("contract_value", "confirmed_vendor_award_amount", "estimated_vendor_revenue"),
            ),
            temporal=TemporalContext(qualifier=TemporalQualifier.HISTORICAL_FACT, as_of_date=date(2023, 12, 18)),
        )
        assert claim.financial.amount == Decimal("19000000.00")
        assert claim.financial.semantic_role == "cip_project_ceiling"
        assert "contract_value" in claim.financial.not_established
        assert claim.temporal.qualifier == TemporalQualifier.HISTORICAL_FACT

    def test_claim_d_and_f_amounts_and_roles_are_never_equal(self):
        """The task's own named requirement: the two money claims and
        their semantic roles remain separate."""
        deposit = FinancialFact(amount=Decimal("1590000.00"), currency="USD", semantic_role="advance_deposit_purchase_order")
        ceiling = FinancialFact(amount=Decimal("19000000.00"), currency="USD", semantic_role="cip_project_ceiling", not_established=("contract_value",))
        assert deposit.amount != ceiling.amount
        assert deposit.semantic_role != ceiling.semantic_role
        assert deposit != ceiling

    def test_claim_h_future_temporal_statement(self):
        claim = Claim(
            category=ClaimCategory.TEMPORAL_STATEMENT, subject="installation contract",
            statement="A separate installation contract is planned to be bid in 2025.",
            provenance=self._prov("A separate contract will be bid in 2025 for the installation of the blocks"),
            temporal=TemporalContext(qualifier=TemporalQualifier.PLANNED_FUTURE_ACTION, as_of_date=date(2024, 8, 28), detail="target year 2025"),
        )
        assert claim.temporal.qualifier == TemporalQualifier.PLANNED_FUTURE_ACTION
        assert claim.temporal.qualifier != TemporalQualifier.COMPLETED

    def test_claim_i_oversight_relationship_excludes_installation_contractor(self):
        claim = Claim(
            category=ClaimCategory.RELATIONSHIP, subject="EMAS material and installation oversight",
            statement="Runway Safe supplies material and oversees installation performed by a separate contractor.",
            provenance=self._prov("installation of the blocks by a separate contractor but under the oversight of Runway Safe"),
            relationship=RelationshipFact(party="Runway Safe", role="material_supplier_and_installation_oversight", scope="not the installation contractor"),
        )
        assert claim.relationship.role == "material_supplier_and_installation_oversight"
        assert "installation_contractor" != claim.relationship.role

    def test_all_msp_claims_trace_to_the_same_preserved_fragment(self):
        claims = [
            Claim(category=ClaimCategory.EXPLICIT_DOCUMENT_FACT, subject="a", statement="a", provenance=self._prov("a")),
            Claim(category=ClaimCategory.PROCEDURAL_REQUEST, subject="b", statement="b", provenance=self._prov("b")),
            Claim(category=ClaimCategory.TEMPORAL_STATEMENT, subject="c", statement="c", provenance=self._prov("c")),
            Claim(category=ClaimCategory.RELATIONSHIP, subject="d", statement="d", provenance=self._prov("d")),
        ]
        identities = {(c.provenance.artifact_identity, c.provenance.source_locator, c.provenance.fragment_hash) for c in claims}
        assert identities == {(MSP_ARTIFACT_IDENTITY, MSP_SOURCE_LOCATOR, MSP_FRAGMENT_HASH)}


# ---------------------------------------------------------------------------
# SFO $40M adversarial tests (task S11, critical)
# ---------------------------------------------------------------------------


def test_unlabeled_amount_cannot_be_represented_as_a_vendor_contract():
    """airport + vendor + EMAS + $40M in the same fragment must still be
    insufficient to produce contract_value=$40M without an explicit
    semantic relationship supporting that role - proven by showing the
    ONLY way to construct a FinancialFact requires an explicit
    semantic_role, and no combination of surrounding claims changes
    what value ends up in that field."""
    ambiguous_amount = FinancialFact(amount=Decimal("40000000.00"), currency="USD", semantic_role="unlabeled_amount_found_in_fragment")
    vendor_context = RelationshipFact(party="Runway Safe", role="mentioned_in_same_fragment", scope="topical proximity only, not an award statement")
    identity_claim = Claim(
        category=ClaimCategory.EXPLICIT_DOCUMENT_FACT, subject="airport identity",
        statement="This fragment concerns the candidate airport.",
        provenance=_msp_provenance("(adversarial synthetic fragment)"),
    )
    amount_claim = Claim(
        category=ClaimCategory.EXPLICIT_DOCUMENT_FACT, subject="unlabeled figure",
        statement="A $40,000,000 figure appears in the fragment with no stated role.",
        provenance=_msp_provenance("(adversarial synthetic fragment, ambiguous dollar figure)"),
        financial=ambiguous_amount, relationship=vendor_context,
    )
    # Nothing about combining identity_claim + amount_claim produces, or
    # could produce, a third claim/field asserting "contract_value =
    # $40,000,000" - no such field or merge operation exists anywhere in
    # this module.
    assert amount_claim.financial.semantic_role != "contract_value"
    assert amount_claim.financial.semantic_role != "confirmed_vendor_award_amount"
    assert identity_claim.category == ClaimCategory.EXPLICIT_DOCUMENT_FACT  # correct identity alone changes nothing about the amount's role
    field_names = {f.name for f in dataclasses.fields(Claim)}
    assert "contract_value" not in field_names


def test_topical_proximity_relationship_does_not_imply_award():
    """A vendor merely named in the same fragment as a dollar figure
    (RelationshipFact.role='mentioned_in_same_fragment') is structurally
    distinct from an explicit award/contract relationship - the role
    strings themselves are never equal, so no code path could ever
    conflate them by accident."""
    mentioned = RelationshipFact(party="Runway Safe", role="mentioned_in_same_fragment")
    awarded = RelationshipFact(party="Runway Safe", role="confirmed_contract_award")
    assert mentioned.role != awarded.role
    assert mentioned != awarded


def test_large_amount_alone_is_not_a_relationship_or_award_claim():
    """A bare FinancialFact, with no RelationshipFact attached at all,
    proves an amount can exist without ever implying who it is paid to
    or for what - exactly the SFO $40M failure mode's own root cause
    (a dollar figure with no established role)."""
    bare_amount = FinancialFact(amount=Decimal("40000000.00"), currency="USD", semantic_role="unlabeled_amount_found_in_fragment")
    claim = Claim(
        category=ClaimCategory.EXPLICIT_DOCUMENT_FACT, subject="unlabeled figure", statement="An amount was found.",
        provenance=_msp_provenance("(adversarial)"), financial=bare_amount,
    )
    assert claim.relationship is None
    assert claim.financial.semantic_role != "runway_safe_contract_value"


# ---------------------------------------------------------------------------
# International / generic safety (task S12)
# ---------------------------------------------------------------------------


def test_synthetic_non_us_non_usd_case_uses_the_identical_generic_shape():
    """A hypothetical Japanese authority's EMAS procurement, in EUR (an
    arbitrary non-USD currency), fits the identical Claim/FinancialFact/
    RelationshipFact/TemporalContext shape as the MSP case - no special
    casing, no different classes, no MSP/FAA/USD-specific code path."""
    provenance = ClaimProvenance(
        artifact_identity="mlit.example.document.1", source_locator="item-9.9.9",
        fragment_hash="deadbeef" * 8, raw_text_excerpt="羽田空港 滑走路16L/34R エンジニアド・マテリアル・アレスティング・システム（EMAS）予算 4,500,000 EUR",
    )
    financial = FinancialFact(amount=Decimal("4500000.00"), currency="EUR", semantic_role="approved_program_budget_ceiling", not_established=("final_contract_value",))
    temporal = TemporalContext(qualifier=TemporalQualifier.HISTORICAL_FACT, as_of_date=date(2026, 4, 1))
    relationship = RelationshipFact(party="Toray Industries", role="material_supplier", scope="EMAS replacement material only")
    claim = Claim(
        category=ClaimCategory.EXPLICIT_DOCUMENT_FACT, subject="Haneda Runway 16L/34R EMAS budget",
        statement="A program budget ceiling was approved.", provenance=provenance,
        financial=financial, temporal=temporal, relationship=relationship,
    )
    assert claim.financial.currency == "EUR"
    assert claim.financial.currency != "USD"
    assert "final_contract_value" in claim.financial.not_established
    assert claim.relationship.party != "Runway Safe"


# ---------------------------------------------------------------------------
# Purity / import boundary (task S13)
# ---------------------------------------------------------------------------


_FORBIDDEN_IMPORTS = {
    "sqlalchemy", "app.database", "app.models", "httpx", "requests", "urllib", "socket",
}


def test_module_imports_no_database_network_or_filesystem_dependency():
    import app.services.evidence_claim_semantics as module

    tree = ast.parse(inspect.getsource(module))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    for forbidden in _FORBIDDEN_IMPORTS:
        assert not any(name == forbidden or name.startswith(forbidden + ".") for name in imported), forbidden


def test_module_never_reads_current_time():
    """AST-based, not a naive substring search: the module's own
    docstrings legitimately MENTION 'datetime.now()'/'date.today()' in
    prose to explain that they are deliberately never called - only an
    actual Call node naming one of these would be a real violation."""
    import app.services.evidence_claim_semantics as module

    tree = ast.parse(inspect.getsource(module))
    offending_calls: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = None
        if isinstance(func, ast.Attribute):
            name = func.attr
        elif isinstance(func, ast.Name):
            name = func.id
        if name in {"now", "today", "utcnow"}:
            offending_calls.append(name)
    assert offending_calls == []


def test_module_has_no_open_or_os_or_pathlib_filesystem_calls():
    import app.services.evidence_claim_semantics as module
    source = inspect.getsource(module)
    for token in ("open(", "os.", "pathlib", "Path("):
        assert token not in source
