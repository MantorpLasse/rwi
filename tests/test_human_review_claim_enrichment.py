"""Tests for app/services/human_review_claim_enrichment.py's dispatch layer
(docs/architecture/rwi-usaspending-legacy-claims-extractor-design.md, Phase
4: (source_type, parser_identifier) dispatch safety).

No database, no session - HumanReviewItem is constructed directly as a
plain, ORM-free dataclass, matching this module's own "immutable snapshot"
design.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.acquisition.mac_granicus_extractor import PARSER_VERSION as MAC_PARSER_VERSION
from app.database import Base
from app.models import Airport, Source, SourceAssertion
from app.services.evidence_attachment_guard import AttachmentOutcome
from app.services.human_review_claim_enrichment import enrich_claims
from app.services.human_review_queue import HumanReviewItem, _to_item
from app.services.signal_candidate_evaluation import SignalCandidateContext, SignalCandidateOutcome, evaluate_signal_candidate

_LEGACY_PARSER = "legacy-source-backfill-v1"

_MAC_TEXT = "The EMAS bed has reached its life expectancy and requires replacement."
_USASPENDING_TEXT = (
    "PURPOSE: CONSTRUCT/EXTEND SAFETY AREA. THIS PROJECT CONSTRUCTS AN ENGINEERED MATERIAL "
    "ARRESTING SYSTEM AT RUNWAY 22. THIS GRANT FUNDS THE SECOND PHASE."
)


def _item(**overrides) -> HumanReviewItem:
    base = dict(
        source_assertion_id=1, airport_id=1, airport_name="Test Airport", airport_code="TST",
        source_id=1, source_type=None, source_title=None, source_publisher=None, source_url=None,
        source_document_reference=None, source_reliability_level_raw=None, source_published_date=None,
        artifact_identity="https://example.test/artifact", source_locator="loc:1",
        raw_fragment_hash="hash1", raw_relevant_text=None, parser_identifier=None,
        identity_guard_decision=None, identity_guard_reason=None,
        intelligence_review_decision=None, intelligence_review_reason=None,
        promotion_policy_decision="HUMAN_REVIEW_REQUIRED", promotion_policy_reason="x",
        latest_reviewer_action=None, linked_signal_id=None, review_workflow_state="ACTIVE_REVIEW",
    )
    base.update(overrides)
    return HumanReviewItem(**base)


class TestMacGranicusRegressionUnchanged:
    def test_mac_dispatch_still_fires_by_parser_identifier_alone(self):
        item = _item(parser_identifier=MAC_PARSER_VERSION, raw_relevant_text=_MAC_TEXT, source_type=None)
        claims = enrich_claims(item)
        assert claims is not None
        assert any("life expectancy" in c.statement for c in claims)

    def test_mac_dispatch_unaffected_by_unrelated_source_type_value(self):
        """MAC's own registration is parser_identifier-only and must behave
        identically regardless of source_type - preserved exactly."""
        item = _item(parser_identifier=MAC_PARSER_VERSION, raw_relevant_text=_MAC_TEXT, source_type="web_discovery")
        claims = enrich_claims(item)
        assert claims is not None
        assert any("life expectancy" in c.statement for c in claims)

    def test_mac_unrelated_text_still_fails_closed(self):
        item = _item(parser_identifier=MAC_PARSER_VERSION, raw_relevant_text="Approve the parking lot budget.")
        assert enrich_claims(item) is None


class TestUsaspendingDispatch:
    def test_usaspending_grant_dispatches_to_usaspending_adapter(self):
        item = _item(
            source_type="usaspending_grant", parser_identifier=_LEGACY_PARSER,
            raw_relevant_text=_USASPENDING_TEXT, source_published_date=date(2022, 9, 13),
        )
        claims = enrich_claims(item)
        assert claims is not None
        assert any("federal grant explicitly funds" in c.statement for c in claims)

    def test_usaspending_dispatch_returns_none_when_no_emas_wording(self):
        item = _item(
            source_type="usaspending_grant", parser_identifier=_LEGACY_PARSER,
            raw_relevant_text="Construct/Extend Safety Area",
        )
        assert enrich_claims(item) is None


class TestCrossFamilyIsolation:
    def test_iija_grant_sharing_parser_identifier_does_not_dispatch(self):
        item = _item(
            source_type="iija_grant", parser_identifier=_LEGACY_PARSER,
            raw_relevant_text=_USASPENDING_TEXT, source_published_date=date(2022, 9, 13),
        )
        assert enrich_claims(item) is None

    def test_faa_construction_report_sharing_parser_identifier_does_not_dispatch(self):
        item = _item(
            source_type="faa_construction_report", parser_identifier=_LEGACY_PARSER,
            raw_relevant_text=_USASPENDING_TEXT, source_published_date=date(2022, 9, 13),
        )
        assert enrich_claims(item) is None

    def test_iija_grant_with_explicit_emas_wording_still_does_not_dispatch(self):
        """Even if an iija_grant/faa_construction_report row DID happen to
        contain EMAS wording, it must never reach the USAspending adapter -
        the source_type gate is absolute, not merely a heuristic."""
        item = _item(
            source_type="iija_grant", parser_identifier=_LEGACY_PARSER,
            raw_relevant_text="Reconstruct Engineered Material Arresting System Safety Area",
        )
        assert enrich_claims(item) is None


class TestSA81ReadOnlyControl:
    """SA81's own exact real text/provenance shape, seeded into an isolated
    in-memory DB (repository policy: tests never open data/runway_safe.db
    directly - see test_human_review_queue.py's own identical discipline).
    Proves the full HumanReviewItem -> enrich_claims ->
    evaluate_signal_candidate() pipeline end to end, matching the real,
    read-only rehearsal already run against the actual SA81 row."""

    def _seed(self):
        from datetime import date

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session = Session(engine)
        airport = Airport(name="Greenville Downtown", iata_code="GMU", icao_code="KGMU", country="USA")
        session.add(airport)
        session.flush()
        source = Source(
            title="USAspending grant: Greenville Airport Commission", source_type="usaspending_grant",
            publisher="Department of Transportation", published_date=date(2022, 9, 13),
        )
        session.add(source)
        session.flush()
        assertion = SourceAssertion(
            source_id=source.id, airport_id=airport.id, assertion_type="project_construction",
            evidence_quality="direct_strong", identity_guard_decision=None,
            artifact_identity="https://www.usaspending.gov/award/ASST_NON_34500260362022_069",
            source_locator="source.external_id=usaspending:ASST_NON_34500260362022_069",
            raw_fragment_hash="dfe036a554bbb2bd06b9d8d3f2ffc0225aa04aaa8b429babb4e03a12a347e716",
            parser_identifier="legacy-source-backfill-v1",
            raw_relevant_text=(
                "PURPOSE: CONSTRUCT/EXTEND/IMPROVE SAFETY AREA. THIS GRANT IS FUNDED BY THE CORONAVIRUS AID, "
                "RELIEF, AND ECONOMIC SECURITY ACT TO INCREASE THE FEDERAL SHARE TO 100 PERCENT FOR THE AIRPORT "
                "IMPROVEMENT PROGRAM (AIP). ACTIVITIES TO BE PERFORMED/EXPECTED OUTCOMES: THIS PROJECT EXTENDS "
                "THE RUNWAY 1/19 SAFETY AREAS TO 600 FEET TO MEET FEDERAL AVIATION ADMINISTRATION DESIGN "
                "STANDARDS. THIS IMPROVEMENT WILL ENHANCE SAFETY AT THE AIRPORT. THIS GRANT FUNDS THE SECOND "
                "PHASE, WHICH CONSISTS OF DESIGN AND CONSTRUCTION OF THE RUNWAY 1 ENGINEERED MATERIAL ARRESTING "
                "SYSTEM (EMAS). INTENDED BENEFICIARY: THIS GRANT WILL PROVIDE FEDERAL FUNDING FOR AIRPORTS "
                "ASSOCIATED WITH GREENVILLE, SOUTH CAROLINA."
            ),
        )
        session.add(assertion)
        session.flush()
        return session, assertion

    def test_sa81_shaped_row_produces_two_claims_and_review_required(self):
        session, assertion = self._seed()
        item = _to_item(assertion, None)
        claims = enrich_claims(item)

        assert claims is not None
        assert len(claims) == 2
        assert any("RUNWAY 1/19" in c.subject for c in claims)
        assert all(c.temporal.as_of_date.isoformat() == "2022-09-13" for c in claims)

        decision = evaluate_signal_candidate(
            claims, SignalCandidateContext(identity_decision=AttachmentOutcome.ATTACH_CONFIRMED),
        )
        assert decision.outcome == SignalCandidateOutcome.REVIEW_REQUIRED

        # read-only: nothing staged or persisted.
        assert not session.dirty
        assert not session.new
        session.close()


class TestUnsupportedCombinations:
    def test_unknown_parser_identifier_returns_none(self):
        item = _item(source_type="usaspending_grant", parser_identifier="some-other-parser", raw_relevant_text=_USASPENDING_TEXT)
        assert enrich_claims(item) is None

    def test_no_parser_identifier_returns_none(self):
        item = _item(source_type="usaspending_grant", parser_identifier=None, raw_relevant_text=_USASPENDING_TEXT)
        assert enrich_claims(item) is None
