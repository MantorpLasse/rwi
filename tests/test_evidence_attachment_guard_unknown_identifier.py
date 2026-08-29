"""Tests for the UNKNOWN-vs-KNOWN-DIFFERENT identifier fix in
app/services/evidence_attachment_guard.py (docs/architecture,
"RWI - IdentityGuard Unknown Identifier Semantics - Narrow Fix" mission).

A source identifier must never be treated as contradicting a candidate
Airport merely because RWI has no identifier data for that candidate -
mirrors the existing, already-designed "absence != contradiction" rule
for topology (S6 rule 2), now applied identically to identifiers.

Every test is a pure, synthetic fixture - no database, no network, no
Korea-specific production branching anywhere in the module under test
(see test_no_korea_specific_branching_in_production_module below).
"""
import inspect

from app.services.evidence_attachment_guard import (
    AttachmentOutcome,
    CandidateAirport,
    EvidenceBag,
    candidate_airport_from_airport_like,
    evaluate_attachment,
)
import app.services.evidence_attachment_guard as guard_module

# --- Synthetic fixtures ---

# A candidate with NO known identifiers of any kind - the exact shape
# this fix targets. Deliberately generic (not Korea/Sacheon-specific) -
# real runway topology is included so the second call site
# (_topology_evidence()'s own other_airport_named computation) can be
# exercised too.
UNKNOWN_ID_AIRPORT = CandidateAirport(
    id="UNK",
    name="Generic Regional Airport",
    identifiers=frozenset(),
    aliases=frozenset({"제네릭공항"}),  # a synthetic native-script alias, never Sacheon's own
    city_location=None,
    canonical_runway_ends=frozenset({"9", "27"}),
    canonical_runway_pairs=frozenset({"9/27"}),
    known_issuers=frozenset(),
)

# A candidate WITH a known identifier, for regression-locking existing
# match/mismatch behavior.
KNOWN_ID_AIRPORT = CandidateAirport(
    id="KID",
    name="Known Identifier Airport",
    identifiers=frozenset({"KID", "KKID"}),
    city_location="Sometown",
    canonical_runway_ends=frozenset({"9", "27"}),
    canonical_runway_pairs=frozenset({"9/27"}),
    known_issuers=frozenset(),
)


# --- 1-2: zero candidate identifiers + source identifier(s) ---

class TestZeroCandidateIdentifiers:
    def test_zero_candidate_identifiers_one_source_identifier_no_positive_no_contradiction(self):
        decision = evaluate_attachment(UNKNOWN_ID_AIRPORT, EvidenceBag(identifiers=frozenset({"HIN"})))
        assert decision.contradicting_evidence == ()
        assert decision.positive_evidence == ()
        assert decision.outcome == AttachmentOutcome.INSUFFICIENT_IDENTITY

    def test_zero_candidate_identifiers_multiple_source_identifiers_no_positive_no_contradiction(self):
        """Multiple unknown identifiers must not become stronger merely
        by quantity - still exactly zero positive/contradicting evidence."""
        decision = evaluate_attachment(UNKNOWN_ID_AIRPORT, EvidenceBag(identifiers=frozenset({"HIN", "RKPS"})))
        assert decision.contradicting_evidence == ()
        assert decision.positive_evidence == ()
        assert decision.outcome == AttachmentOutcome.INSUFFICIENT_IDENTITY


# --- 3-4: existing match/mismatch behavior, regression-locked ---

class TestKnownIdentifierRegression:
    def test_known_identifier_match_still_confirms(self):
        decision = evaluate_attachment(KNOWN_ID_AIRPORT, EvidenceBag(identifiers=frozenset({"KID"})))
        assert decision.outcome == AttachmentOutcome.ATTACH_CONFIRMED

    def test_known_identifier_mismatch_still_contradicts(self):
        decision = evaluate_attachment(KNOWN_ID_AIRPORT, EvidenceBag(identifiers=frozenset({"XYZ"})))
        assert decision.outcome == AttachmentOutcome.REJECT_CROSS_AIRPORT
        assert any(item.value == "XYZ" for item in decision.contradicting_evidence)


# --- 5-6: NAME + unknown identifier / identifier-only unknown ---

class TestNamePlusUnknownIdentifier:
    def test_name_plus_unknown_identifier_reaches_attach_provisional(self):
        """The key regression shape: NAME positive, identifier
        absent/unknown, no contradiction - natural outcome is
        ATTACH_PROVISIONAL (one weak category), never ATTACH_CONFIRMED
        and never REJECT_CROSS_AIRPORT."""
        decision = evaluate_attachment(
            UNKNOWN_ID_AIRPORT,
            EvidenceBag(names=frozenset({"Generic Regional Airport"}), identifiers=frozenset({"HIN"})),
        )
        assert decision.contradicting_evidence == ()
        assert decision.outcome == AttachmentOutcome.ATTACH_PROVISIONAL

    def test_name_via_alias_plus_unknown_identifier_reaches_attach_provisional(self):
        decision = evaluate_attachment(
            UNKNOWN_ID_AIRPORT,
            EvidenceBag(names=frozenset({"제네릭공항"}), identifiers=frozenset({"RKPS"})),
        )
        assert decision.contradicting_evidence == ()
        assert decision.outcome == AttachmentOutcome.ATTACH_PROVISIONAL

    def test_identifier_only_unknown_case_is_not_confirmed_and_not_rejected(self):
        decision = evaluate_attachment(UNKNOWN_ID_AIRPORT, EvidenceBag(identifiers=frozenset({"HIN"})))
        assert decision.outcome not in (AttachmentOutcome.ATTACH_CONFIRMED, AttachmentOutcome.REJECT_CROSS_AIRPORT)
        assert decision.outcome == AttachmentOutcome.INSUFFICIENT_IDENTITY


# --- 7: existing native alias remains NAME-only (pre-existing precedent, re-asserted here) ---

class TestAliasRemainsNameOnly:
    def test_native_alias_alone_is_still_only_attach_provisional(self):
        decision = evaluate_attachment(
            UNKNOWN_ID_AIRPORT, EvidenceBag(names=frozenset({"제네릭공항"})),
        )
        assert decision.outcome == AttachmentOutcome.ATTACH_PROVISIONAL


# --- 8: topology behavior unchanged (the second call site this fix touches) ---

class TestTopologyUnaffectedByUnknownIdentifier:
    def test_matching_topology_not_vetoed_by_unknown_identifier(self):
        """Before this fix, a stated-but-unknown identifier made
        _topology_evidence()'s own `other_airport_named` true, which
        would have wrongly vetoed an otherwise-matching runway token into
        a contradiction (REJECT_CROSS_AIRPORT). Proves the second call
        site is fixed too: the topology match is preserved as ordinary
        positive evidence, never vetoed - RUNWAY_TOPOLOGY is exactly one
        category here (only IDENTIFIER is ever "strong enough alone" in
        the real, implemented algorithm - see evaluate_attachment()'s own
        docstring), so the natural outcome is ATTACH_PROVISIONAL, not a
        contradiction."""
        decision = evaluate_attachment(
            UNKNOWN_ID_AIRPORT,
            EvidenceBag(runway_pairs=frozenset({"9/27"}), identifiers=frozenset({"HIN"})),
        )
        assert decision.contradicting_evidence == ()
        assert any(item.value == "9/27" for item in decision.positive_evidence)
        assert decision.outcome == AttachmentOutcome.ATTACH_PROVISIONAL

    def test_matching_topology_plus_name_reaches_confirmed_two_categories(self):
        """Two independent positive categories (topology + name), neither
        vetoed by the unknown identifier, naturally reach ATTACH_CONFIRMED
        via the >=2-categories rule - proving the topology fix combines
        correctly with the rest of the algorithm, unmodified."""
        decision = evaluate_attachment(
            UNKNOWN_ID_AIRPORT,
            EvidenceBag(
                runway_pairs=frozenset({"9/27"}), names=frozenset({"Generic Regional Airport"}),
                identifiers=frozenset({"HIN"}),
            ),
        )
        assert decision.contradicting_evidence == ()
        assert decision.outcome == AttachmentOutcome.ATTACH_CONFIRMED

    def test_topology_absence_still_not_contradiction_without_identifier_involved(self):
        """Regression: topology's own pre-existing absence != contradiction
        rule (S6 rule 2), completely independent of this fix's identifier
        change, remains intact."""
        decision = evaluate_attachment(UNKNOWN_ID_AIRPORT, EvidenceBag(runway_pairs=frozenset({"14/32"})))
        assert decision.contradicting_evidence == ()
        assert decision.outcome == AttachmentOutcome.INSUFFICIENT_IDENTITY


# --- 9: real contradiction (unrelated to identifiers) still vetoes ---

class TestRealContradictionStillVetoes:
    def test_contradicting_name_still_vetoes_topology_for_unknown_identifier_candidate(self):
        """A REAL contradiction signal (a name pre-classified as
        belonging to a different airport) must still make
        other_airport_named true and veto a non-matching runway token -
        this fix must not weaken that OR-clause's other branches."""
        decision = evaluate_attachment(
            UNKNOWN_ID_AIRPORT,
            EvidenceBag(
                runway_pairs=frozenset({"14/32"}),  # absent from UNKNOWN_ID_AIRPORT's own topology
                contradicting_names=frozenset({"Some Other Airport"}),
            ),
        )
        assert any(item.value == "14/32" for item in decision.contradicting_evidence)
        assert decision.outcome == AttachmentOutcome.REJECT_CROSS_AIRPORT

    def test_known_identifier_candidate_contradiction_still_vetoes_matching_name(self):
        """Contradiction always vetoes positive evidence, regardless of
        topical overlap (S5) - unchanged by this fix."""
        decision = evaluate_attachment(
            KNOWN_ID_AIRPORT,
            EvidenceBag(names=frozenset({"Known Identifier Airport"}), identifiers=frozenset({"WRONG"})),
        )
        assert decision.outcome == AttachmentOutcome.REJECT_CROSS_AIRPORT


# --- 10: no Korea-specific production branching ---

class TestNoKoreaSpecificBranching:
    def test_production_module_contains_no_korea_or_sacheon_specific_tokens(self):
        source = inspect.getsource(guard_module)
        forbidden_substrings = ("Sacheon", "sacheon", "Korea", "korea", "HIN", "RKPS", "사천")
        for token in forbidden_substrings:
            assert token not in source, f"unexpected Korea/Sacheon-specific token {token!r} in production module"

    def test_identifier_evidence_signature_takes_no_country_or_type_parameter(self):
        import inspect as _inspect
        sig = _inspect.signature(guard_module._identifier_evidence)
        assert list(sig.parameters) == ["candidate", "bag"]
