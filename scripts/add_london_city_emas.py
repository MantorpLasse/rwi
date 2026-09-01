"""Governed ingestion for London City Airport (LCY/EGLC), UK - RWI's first
new international historical-baseline airport ("RWI - Mission #8D -
Governed London City Historical Baseline Ingestion").

Evidence verified in Missions #8B/#8C: UK CAA ACP-2022-090 (regulatory,
15-16 March 2023), Runway Safe's own LCY EMAS announcement (vendor
primary, 21 Oct 2022), blu-3's own LCY EMAS contract page (contractor
primary). The secondary Dock/Lock/River blog is deliberately NOT ingested
(#8C: minimum sufficient evidence over evidence quantity).

PIPELINE USED (Mission #8C Part B/C revision - the real governed
machinery, not a bare one-off script, for the evidence-to-Airport-identity
transition):

  1. Source + SourceAssertion, candidate-linked (never airport-linked -
     LCY does not exist yet): app.services.discovery_evidence_persistence
     .persist_candidate_linked_source_assertion() (imported, never
     reimplemented), one CandidateFragment per source, all three linked to
     ONE UnknownAirportCandidate via
     app.services.unknown_airport_candidate_persistence
     .find_or_create_unknown_airport_candidate().
  2. IdentityGuard run for real (not skipped because the airport is
     "obviously" real): app.services.evidence_attachment_guard
     .evaluate_attachment_for_candidates() against every existing real
     Airport, confirmed to return no attachment before any candidate is
     created.
  3. ERG1/ERG2/ERG3/ERG4 (app.services.emas_relevance_evaluation
     .evaluate_emas_relevance(), .unknown_airport_candidate_relevance_
     persistence.persist_unknown_airport_candidate_relevance_assessment(),
     .unknown_airport_candidate_relevance_review_persistence
     .record_unknown_airport_candidate_relevance_review()) - a REQUIRED
     precondition for step 4 discovered during this mission's own
     implementation (create_airport_from_approved_candidate() calls
     _require_admission_eligible(), which fails closed without a CURRENT
     CONFIRM_EMAS_RELEVANT human review against a CURRENT automatic
     assessment). Not anticipated in this shape by Mission #8C's own
     preflight - see the mission report's own "deviations" section.
  4. Human review + execution: app.services.unknown_airport_candidate_
     persistence.record_unknown_airport_candidate_review() (action=
     CREATE_NEW_AIRPORT), then app.services.unknown_airport_candidate_
     resolution.create_airport_from_approved_candidate() - both imported,
     never reimplemented.
  5. Runway + Installation: NO dedicated governed creation service exists
     for this layer (confirmed in Mission #8C Part B) - plain, idempotent
     ORM inserts, matching scripts/add_brazil_expansion.py's and
     scripts/add_gadelius_greenemas_installations.py's own established
     get-or-create discipline exactly.

Zero Signal, zero PhysicalInstallationIdentity/RunwayEnd (Mission #8C
Part F/G: not needed - no reconciliation ambiguity exists for a brand-new,
uncontested airport).

DRY-RUN BY DEFAULT: `python -m scripts.add_london_city_emas --database
data/runway_safe.db` (or run_ingestion(..., allow_database_write=False))
performs every step against a real Session, prints a full preview, then
ROLLS BACK - no row survives. Only `--allow-database-write` commits.

IDEMPOTENT: re-running with --allow-database-write a second time finds the
existing Airport (by iata_code), Runway (by (airport_id, designation)),
and Installation rows ((airport_id, runway_end, type, install_year)) and
creates nothing new for those. The SourceAssertion/relevance-assessment/
review layers are append-only by the current architecture's own design
(SourceAssertion rows are get-or-created by fragment identity - a second
run finds the SAME fragment identity and reuses the existing row, per
persist_candidate_linked_source_assertion()'s own documented behavior -
but UnknownAirportCandidateReview and UnknownAirportCandidateRelevanceReview
are genuinely append-only audit trails; a second run's review-recording
step is skipped entirely once the candidate is already resolved, since
record_unknown_airport_candidate_review()/create_airport_from_approved_
candidate() would raise on an already-resolved candidate - this script
detects that first and skips straight to the idempotent Runway/Installation
layer).
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, UTC
from pathlib import Path

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Airport,
    Installation,
    Runway,
    Source,
    SourceAssertion,
    UnknownAirportCandidate,
)
from app.services.discovery_candidate_fragment import CandidateFragment, candidate_fragment_to_evidence_bag
from app.services.discovery_evidence_persistence import (
    DiscoverySourceMetadata,
    persist_candidate_linked_source_assertion,
)
from app.services.emas_relevance_evaluation import EmasEvidenceObservation, EvidenceClass
from app.services.evidence_attachment_guard import (
    AttachmentOutcome,
    candidate_airport_from_airport_like,
    evaluate_attachment_for_candidates,
)
from app.services.evidence_claim_semantics import TemporalQualifier
from app.services.unknown_airport_candidate_persistence import (
    find_or_create_unknown_airport_candidate,
    record_unknown_airport_candidate_review,
)
from app.services.unknown_airport_candidate_relevance_persistence import (
    get_latest_unknown_airport_candidate_relevance_assessment,
    persist_unknown_airport_candidate_relevance_assessment,
)
from app.services.unknown_airport_candidate_relevance_review_persistence import (
    record_unknown_airport_candidate_relevance_review,
)
from app.services.unknown_airport_candidate_resolution import create_airport_from_approved_candidate

REVIEWER = "human:lkarlsson@gmail.com"
LCY_IATA = "LCY"
LCY_ICAO = "EGLC"
LCY_NAME = "London City Airport"
LCY_COUNTRY = "United Kingdom"
LCY_CITY = "London"

_CAA_URL = "https://airspacechange.caa.co.uk/documents/download/5487"
_RUNWAY_SAFE_URL = "https://runwaysafe.com/london-city-airport-invests-in-safety-enhancing-technology-emas/"
_BLU3_URL = "https://www.blu-3.co.uk/blu-3-secures-landmark-aviation-contract-for-london-city-airport"

# Verbatim excerpts, exactly as fetched/read in Mission #8B (CAA PDF via
# pdftotext) and re-cited in #8C - never paraphrased, never re-translated.
_CAA_EXCERPT = (
    "London City Airport is installing an Engineered Material Arrestor System (EMAS) which will provide "
    "an arrestor bed at both ends of its runway, enhancing safety and reducing the risk to aircraft and "
    "passengers should an aircraft overrun or undershoot a runway. The EMAS will be placed in the "
    "existing RESAs and the future design will see changes to the threshold locations."
)
_RUNWAY_SAFE_EXCERPT = (
    "Runway Safe's EMASMAX solution was selected for installations at each end of London City Airport's "
    "runway in the beginning 2023. EMASEME AB, a joint venture company between Runway Safe and KIBAG "
    "that offers Runway Safe's EMAS solutions to support airports in the EMEA region, will supply the "
    "system."
)
_BLU3_EXCERPT = (
    "blu-3's scope is the installation of two Engineered Material Arresting Systems (EMAS) with carbon "
    "neutral construction for the duration of the project. blu-3's work will begin in October 2022 and "
    "is scheduled to complete in June 2023. Contract value: GBP 6 million."
)


def _build_fragments() -> "list[tuple[DiscoverySourceMetadata, CandidateFragment]]":
    return [
        (
            DiscoverySourceMetadata(
                document_identity=_CAA_URL,
                title="LCY EMAS ACP — ACP-2022-090 (Issue 1.1)",
                source_type="regulatory_document",
                publisher="UK Civil Aviation Authority",
                url=_CAA_URL,
                published_date=date(2023, 3, 16),
                reliability_level="official",
            ),
            CandidateFragment(
                artifact_identity=_CAA_URL,
                source_locator="section 2 / section 8.2 (Statement of Need)",
                raw_text=_CAA_EXCERPT,
                airport_names=frozenset({LCY_NAME}),
                runway_pairs=frozenset({"09/27"}),
                runway_ends=frozenset({"09", "27"}),
                issuers=frozenset({"UK Civil Aviation Authority"}),
                document_title="LCY EMAS ACP — ACP-2022-090",
                url=_CAA_URL,
                publication_date=date(2023, 3, 16),
                parser_identifier="manual-uk-caa-research-v1",
                extracted_at=datetime.now(UTC),
            ),
        ),
        (
            DiscoverySourceMetadata(
                document_identity=_RUNWAY_SAFE_URL,
                title="London City airport invests in safety enhancing technology EMAS",
                source_type="manufacturer_press",
                publisher="Runway Safe",
                url=_RUNWAY_SAFE_URL,
                published_date=date(2022, 10, 21),
                reliability_level="official",
            ),
            CandidateFragment(
                artifact_identity=_RUNWAY_SAFE_URL,
                source_locator="article body",
                raw_text=_RUNWAY_SAFE_EXCERPT,
                airport_names=frozenset({LCY_NAME}),
                issuers=frozenset({"Runway Safe"}),
                document_title="London City airport invests in safety enhancing technology EMAS",
                url=_RUNWAY_SAFE_URL,
                publication_date=date(2022, 10, 21),
                parser_identifier="manual-uk-vendor-research-v1",
                extracted_at=datetime.now(UTC),
            ),
        ),
        (
            DiscoverySourceMetadata(
                document_identity=_BLU3_URL,
                title="blu-3 secures landmark aviation contract for London City Airport",
                source_type="contractor_press",
                publisher="blu-3",
                url=_BLU3_URL,
                published_date=None,
                reliability_level="official",
            ),
            CandidateFragment(
                artifact_identity=_BLU3_URL,
                source_locator="article body",
                raw_text=_BLU3_EXCERPT,
                airport_names=frozenset({LCY_NAME}),
                issuers=frozenset({"blu-3"}),
                document_title="blu-3 secures landmark aviation contract for London City Airport",
                url=_BLU3_URL,
                parser_identifier="manual-uk-contractor-research-v1",
                extracted_at=datetime.now(UTC),
            ),
        ),
    ]


@dataclass
class IngestionReport:
    lines: "list[str]" = None

    def __post_init__(self):
        if self.lines is None:
            self.lines = []

    def add(self, line: str) -> None:
        self.lines.append(line)
        print(line)


def run_ingestion(database: Path, *, allow_database_write: bool = False) -> IngestionReport:
    report = IngestionReport()
    engine = create_engine(f"sqlite:///{database}", connect_args={"check_same_thread": False}, future=True)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    session = Session(engine)
    try:
        report.add(f"=== London City ingestion ({'WRITE' if allow_database_write else 'DRY-RUN'}) ===")

        # --- Step 0: fresh identity re-check (repository state overrides
        # any prior report - Mission #8D Part D's own instruction) ---
        existing_airport = session.scalar(
            select(Airport).where((Airport.iata_code == LCY_IATA) | (Airport.icao_code == LCY_ICAO))
        )
        if existing_airport is not None:
            report.add(f"STOP: Airport already exists (id={existing_airport.id}) matching LCY/EGLC.")
            return report

        # --- Step 1/2: IdentityGuard, run for real against every existing
        # Airport, using the CAA fragment (the identity/existence claim). ---
        airports = session.scalars(
            select(Airport).options(selectinload(Airport.runways).selectinload(Runway.runway_ends))
        ).all()
        candidates = [candidate_airport_from_airport_like(a) for a in airports]

        fragments = _build_fragments()
        caa_metadata, caa_fragment = fragments[0]
        caa_bag = candidate_fragment_to_evidence_bag(caa_fragment)
        decisions = evaluate_attachment_for_candidates(caa_bag, candidates)
        attach_outcomes = {
            aid: d.outcome for aid, d in decisions.items()
            if d.outcome in (AttachmentOutcome.ATTACH_CONFIRMED, AttachmentOutcome.ATTACH_PROVISIONAL)
        }
        report.add(
            f"IdentityGuard: evaluated against {len(candidates)} existing Airports; "
            f"ATTACH_CONFIRMED/ATTACH_PROVISIONAL matches = {attach_outcomes or 'NONE'}"
        )
        if attach_outcomes:
            report.add("STOP: IdentityGuard found a possible existing-airport match - refusing to proceed.")
            return report

        # --- Step 3: find-or-create the UnknownAirportCandidate ---
        candidate_result = find_or_create_unknown_airport_candidate(
            session,
            raw_name=LCY_NAME,
            raw_city=LCY_CITY,
            raw_country=LCY_COUNTRY,
            raw_iata_code=LCY_IATA,
            raw_icao_code=LCY_ICAO,
            raw_runway_designation="09/27",
            evidence_source_locator="section 2 / section 8.2 (Statement of Need)",
            evidence_artifact_identity=_CAA_URL,
        )
        candidate = candidate_result.candidate
        report.add(
            f"UnknownAirportCandidate id={candidate.id} created={candidate_result.created} "
            f"fingerprint={candidate.candidate_fingerprint[:16]}... resolved_airport_id={candidate.resolved_airport_id}"
        )

        already_resolved = candidate.resolved_airport_id is not None

        # --- Step 4: persist the 3 candidate-linked SourceAssertions ---
        source_assertion_ids: "list[int]" = []
        source_id_by_publisher: "dict[str, int]" = {}
        for metadata, fragment in fragments:
            result = persist_candidate_linked_source_assertion(
                session, metadata, fragment, unknown_airport_candidate_id=candidate.id,
            )
            source_assertion_ids.append(result.source_assertion_id)
            source_id_by_publisher[metadata.publisher] = result.source_id
            report.add(
                f"Source id={result.source_id} (created={result.source_created}) / "
                f"SourceAssertion id={result.source_assertion_id} (created={result.source_assertion_created}) "
                f"-> {metadata.publisher}"
            )
        # Installation.source_id can reference only one Source (Mission #8C
        # Part G) - the UK CAA regulator source is chosen as it best
        # supports EMAS existence/physical installation directly (§8.2
        # Statement of Need); the vendor/contractor sources remain linked
        # only via their own SourceAssertion rows above, not via
        # Installation.source_id - never a fabricated multi-source
        # relationship the schema does not support.
        installation_source_id = source_id_by_publisher["UK Civil Aviation Authority"]

        if not already_resolved:
            # --- Step 5: ERG1/ERG2 automatic relevance assessment ---
            observations = (
                EmasEvidenceObservation(
                    evidence_class=EvidenceClass.A_EXPLICIT_EMAS,
                    basis="CAA/Runway Safe/blu-3 all explicitly name EMAS/EMASMAX",
                    temporality=TemporalQualifier.HISTORICAL_FACT,
                ),
                EmasEvidenceObservation(
                    evidence_class=EvidenceClass.E_EXISTING_INSTALLATION,
                    basis="EMAS confirmed installed and operational at both runway ends since 2023",
                    temporality=TemporalQualifier.HISTORICAL_FACT,
                ),
            )
            assessment_result = persist_unknown_airport_candidate_relevance_assessment(
                session, candidate, observations=observations, source_assertion_ids=tuple(source_assertion_ids),
            )
            assessment = assessment_result.assessment
            report.add(
                f"ERG2 relevance assessment id={assessment.id} outcome={assessment.outcome} "
                f"is_inventory_relevant={assessment.is_inventory_relevant} "
                f"is_watch_worthy={assessment.is_watch_worthy}"
            )

            # --- Step 6: ERG3 human relevance review ---
            relevance_review = record_unknown_airport_candidate_relevance_review(
                session, candidate,
                basis_assessment_id=assessment.id,
                action="CONFIRM_EMAS_RELEVANT",
                reviewer=REVIEWER,
                reason=(
                    "Three independent primary sources (UK CAA regulator, Runway Safe vendor, blu-3 "
                    "contractor) confirm EMAS/EMASMAX physically installed and operational at both ends "
                    "of London City Airport's runway 09/27 since 2023 - E_EXISTING_INSTALLATION, "
                    "unambiguous, no contradicting evidence found (Mission #8B verification)."
                ),
            )
            report.add(f"ERG3 relevance review id={relevance_review.id} action={relevance_review.action}")

            # --- Step 7: human review recording CREATE_NEW_AIRPORT ---
            review = record_unknown_airport_candidate_review(
                session, candidate,
                action="CREATE_NEW_AIRPORT",
                reviewer=REVIEWER,
                reason=(
                    "London City Airport (LCY/EGLC) does not exist in RWI under any identity, alias, or "
                    "unresolved candidate (re-verified fresh, Mission #8D Part D). IdentityGuard confirmed "
                    "no ATTACH_CONFIRMED/ATTACH_PROVISIONAL match against any existing Airport. Evidence "
                    "is strong and cross-corroborated (Mission #8B/#8C)."
                ),
            )
            report.add(f"UnknownAirportCandidateReview id={review.id} action={review.action}")

            # --- Step 8: execute - create the real Airport ---
            create_result = create_airport_from_approved_candidate(
                session, candidate_id=candidate.id, review_id=review.id,
                name=LCY_NAME, country=LCY_COUNTRY, city=LCY_CITY,
                iata_code=LCY_IATA, icao_code=LCY_ICAO,
            )
            airport_id = create_result.created_airport_id
            report.add(
                f"Airport CREATED id={airport_id}; moved SourceAssertion ids="
                f"{create_result.moved_source_assertion_ids}"
            )
        else:
            airport_id = candidate.resolved_airport_id
            report.add(
                f"Candidate already resolved to Airport id={airport_id} - skipping review/execute "
                "(idempotent re-run: append-only review/assessment layers are not re-recorded)."
            )
            latest = get_latest_unknown_airport_candidate_relevance_assessment(session, candidate.id)
            report.add(f"(existing latest relevance assessment id={latest.id if latest else None})")

        airport = session.get(Airport, airport_id)

        # --- Step 9: Runway (plain idempotent insert - no dedicated
        # governed service exists for this layer, Mission #8C Part B/F) ---
        runway = session.scalar(
            select(Runway).where(Runway.airport_id == airport.id, Runway.designation == "09/27")
        )
        runway_created = False
        if runway is None:
            runway = Runway(airport_id=airport.id, designation="09/27")
            session.add(runway)
            session.flush()
            runway_created = True
        report.add(f"Runway id={runway.id} designation={runway.designation!r} created={runway_created}")

        # --- Step 10: Installations (plain idempotent insert, one per
        # runway end - Mission #8C Part F/G) ---
        notes_text = (
            "EMAS levererat via EMASEME AB (Runway Safe/KIBAG:s EMEA-joint venture). "
            "Civil works av entreprenoren blu-3 (kontraktsvarde ca GBP 6M). "
            "Bada banandar installerade under vintern 2022/23."
        )
        installation_ids: "list[int]" = []
        for runway_end in ("09", "27"):
            installation = session.scalar(
                select(Installation).where(
                    Installation.airport_id == airport.id,
                    Installation.runway_end == runway_end,
                    Installation.type == "EMASMAX",
                    Installation.install_year == 2023,
                )
            )
            created = False
            if installation is None:
                installation = Installation(
                    airport_id=airport.id,
                    runway_id=runway.id,
                    runway_end=runway_end,
                    type="EMASMAX",
                    install_year=2023,
                    replacement_year=None,
                    status="active",
                    confirmed_vendor="Runway Safe",
                    source_id=installation_source_id,
                    notes=notes_text,
                )
                session.add(installation)
                session.flush()
                created = True
            installation_ids.append(installation.id)
            report.add(f"Installation id={installation.id} runway_end={runway_end!r} created={created}")

        if allow_database_write:
            session.commit()
            report.add("=== COMMITTED ===")
        else:
            session.rollback()
            report.add("=== ROLLED BACK (dry-run; no row persisted) ===")

        return report
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--allow-database-write", action="store_true")
    args = parser.parse_args()
    run_ingestion(args.database, allow_database_write=args.allow_database_write)


if __name__ == "__main__":
    main()
