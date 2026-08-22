from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import configure_mappers
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    AcquisitionRun,
    AcquisitionSource,
    Airport,
    Incident,
    Installation,
    InstallationAssertionLink,
    PhysicalInstallationIdentity,
    PublishingSource,
    ReviewerAction,
    Runway,
    RunwayEnd,
    Signal,
    SignalDisposition,
    SignalDispositionMember,
    Source,
    SourceAssertion,
    Snapshot,
    UnknownAirportCandidate,
    UnknownAirportCandidateReview,
)


EXPECTED_COLUMNS = {
    "acquisition_sources": {
        "id": ("INTEGER", False, None, None),
        "publishing_source_id": ("INTEGER", False, None, None),
        "key": ("VARCHAR(150)", False, None, None),
        "display_name": ("VARCHAR(200)", False, None, None),
        "acquisition_type": ("VARCHAR(50)", False, None, None),
        "canonical_url": ("VARCHAR(1000)", False, None, None),
        "expected_media_type": ("VARCHAR(200)", True, None, None),
        "active": ("BOOLEAN", False, ("scalar", True), None),
        "created_at": ("DATETIME", False, ("callable",), None),
        "updated_at": ("DATETIME", False, ("callable",), None),
    },
    "acquisition_runs": {
        "id": ("INTEGER", False, None, None),
        "acquisition_source_id": ("INTEGER", False, None, None),
        "snapshot_id": ("INTEGER", True, None, None),
        "started_at": ("DATETIME", False, None, None),
        "completed_at": ("DATETIME", True, None, None),
        "status": ("VARCHAR(18)", False, None, None),
        "request_url": ("VARCHAR(1000)", False, None, None),
        "final_url": ("VARCHAR(1000)", True, None, None),
        "http_status": ("INTEGER", True, None, None),
        "content_type": ("VARCHAR(200)", True, None, None),
        "response_headers": ("TEXT", True, None, None),
        "provider_version": ("VARCHAR(100)", False, None, None),
        "duration_seconds": ("FLOAT", False, None, None),
        "error_category": ("VARCHAR(100)", True, None, None),
        "error_detail": ("TEXT", True, None, None),
        "is_new_snapshot": ("BOOLEAN", True, None, None),
    },
    "snapshots": {
        "id": ("INTEGER", False, None, None),
        "acquisition_source_id": ("INTEGER", False, None, None),
        "first_acquisition_run_id": ("INTEGER", False, None, None),
        "payload": ("BLOB", False, None, None),
        "sha256": ("VARCHAR(64)", False, None, None),
        "byte_size": ("INTEGER", False, None, None),
        "media_type": ("VARCHAR(200)", True, None, None),
        "retrieved_at": ("DATETIME", False, None, None),
    },
    "airports": {
        "id": ("INTEGER", False, None, None),
        "iata_code": ("VARCHAR(3)", True, None, None),
        "icao_code": ("VARCHAR(4)", True, None, None),
        "faa_code": ("VARCHAR(5)", True, None, None),
        "name": ("VARCHAR(200)", False, None, None),
        "city": ("VARCHAR(100)", True, None, None),
        "state_region": ("VARCHAR(100)", True, None, None),
        "country": ("VARCHAR(100)", False, None, None),
        "latitude": ("FLOAT", True, None, None),
        "longitude": ("FLOAT", True, None, None),
        "website_url": ("VARCHAR(500)", True, None, None),
        "notes": ("TEXT", True, None, None),
        "created_at": ("DATETIME", False, ("callable",), None),
        "updated_at": ("DATETIME", False, ("callable",), ("callable",)),
    },
    "runways": {
        "id": ("INTEGER", False, None, None),
        "airport_id": ("INTEGER", False, None, None),
        "designation": ("VARCHAR(20)", False, None, None),
        "length_m": ("INTEGER", True, None, None),
        "width_m": ("INTEGER", True, None, None),
        "surface": ("VARCHAR(50)", True, None, None),
        "notes": ("TEXT", True, None, None),
    },
    "runway_ends": {
        "id": ("INTEGER", False, None, None),
        "runway_id": ("INTEGER", False, None, None),
        "designation": ("VARCHAR(10)", False, None, None),
    },
    "publishing_sources": {
        "id": ("INTEGER", False, None, None),
        "name": ("VARCHAR(200)", False, None, None),
        "source_type": ("VARCHAR(50)", True, None, None),
        "homepage_url": ("VARCHAR(1000)", True, None, None),
        "country_code": ("VARCHAR(2)", True, None, None),
        "reliability_level": ("VARCHAR(30)", True, None, None),
        "notes": ("TEXT", True, None, None),
    },
    "sources": {
        "id": ("INTEGER", False, None, None),
        "title": ("VARCHAR(300)", False, None, None),
        "source_type": ("VARCHAR(50)", False, None, None),
        "publisher": ("VARCHAR(200)", True, None, None),
        "url": ("VARCHAR(1000)", True, None, None),
        "published_date": ("DATE", True, None, None),
        "retrieved_at": ("DATE", True, None, None),
        "document_reference": ("VARCHAR(200)", True, None, None),
        "page_number": ("VARCHAR(30)", True, None, None),
        "summary": ("TEXT", True, None, None),
        "reliability_level": ("VARCHAR(30)", False, ("scalar", "official"), None),
        "external_id": ("VARCHAR(200)", True, None, None),
        "created_at": ("DATETIME", True, ("callable",), None),
        "updated_at": ("DATETIME", True, ("callable",), ("callable",)),
    },
    "source_assertions": {
        "id": ("INTEGER", False, None, None),
        "source_id": ("INTEGER", False, None, None),
        "airport_id": ("INTEGER", True, None, None),
        "runway_id": ("INTEGER", True, None, None),
        "assertion_type": ("VARCHAR(30)", False, None, None),
        "runway_end": ("VARCHAR(20)", True, None, None),
        "raw_airport_identifier": ("VARCHAR(100)", True, None, None),
        "raw_airport_name": ("VARCHAR(300)", True, None, None),
        "raw_runway_value": ("VARCHAR(100)", True, None, None),
        "raw_runway_end_value": ("VARCHAR(100)", True, None, None),
        "raw_product_type": ("VARCHAR(200)", True, None, None),
        "raw_year_date_wording": ("VARCHAR(300)", True, None, None),
        "raw_vendor_manufacturer_wording": ("VARCHAR(300)", True, None, None),
        "raw_count": ("VARCHAR(100)", True, None, None),
        "raw_relevant_text": ("TEXT", True, None, None),
        "source_record_identifier": ("VARCHAR(300)", True, None, None),
        "source_locator": ("VARCHAR(500)", True, None, None),
        "raw_fragment_hash": ("VARCHAR(128)", True, None, None),
        "artifact_identity": ("VARCHAR(500)", True, None, None),
        "parser_identifier": ("VARCHAR(200)", True, None, None),
        "extracted_at": ("DATETIME", True, None, None),
        "evidence_quality": ("VARCHAR(30)", False, ("scalar", "unverified_candidate"), None),
        "review_state": ("VARCHAR(20)", False, ("scalar", "unreviewed"), None),
        "identity_guard_decision": ("VARCHAR(30)", True, None, None),
        "identity_guard_reason": ("TEXT", True, None, None),
        "intelligence_review_decision": ("VARCHAR(30)", True, None, None),
        "intelligence_review_reason": ("TEXT", True, None, None),
        "promotion_policy_decision": ("VARCHAR(30)", True, None, None),
        "promotion_policy_reason": ("TEXT", True, None, None),
        "signal_id": ("INTEGER", True, None, None),
        "created_at": ("DATETIME", False, ("callable",), None),
    },
    "physical_installation_identities": {
        "id": ("INTEGER", False, None, None),
        "airport_id": ("INTEGER", False, None, None),
        "runway_id": ("INTEGER", True, None, None),
        "runway_end": ("VARCHAR(20)", True, None, None),
        "runway_end_id": ("INTEGER", True, None, None),
        "created_at": ("DATETIME", False, ("callable",), None),
    },
    "installation_assertion_links": {
        "id": ("INTEGER", False, None, None),
        "assertion_id": ("INTEGER", False, None, None),
        "physical_installation_id": ("INTEGER", True, None, None),
        "outcome": ("VARCHAR(40)", False, None, None),
        "reason": ("TEXT", False, None, None),
        "actor": ("VARCHAR(100)", False, None, None),
        "reviewed_at": ("DATETIME", False, ("callable",), None),
        "supersedes_link_id": ("INTEGER", True, None, None),
    },
    "reviewer_actions": {
        "id": ("INTEGER", False, None, None),
        "source_assertion_id": ("INTEGER", False, None, None),
        "action": ("VARCHAR(30)", False, None, None),
        "reason": ("TEXT", False, None, None),
        "reviewer": ("VARCHAR(100)", False, None, None),
        "created_at": ("DATETIME", False, ("callable",), None),
        "supersedes_action_id": ("INTEGER", True, None, None),
        "duplicate_of_signal_id": ("INTEGER", True, None, None),
        "reconciliation_fingerprint": ("VARCHAR(64)", True, None, None),
    },
    "installations": {
        "id": ("INTEGER", False, None, None),
        "airport_id": ("INTEGER", False, None, None),
        "runway_id": ("INTEGER", True, None, None),
        "source_id": ("INTEGER", True, None, None),
        "runway_end": ("VARCHAR(20)", True, None, None),
        "type": ("VARCHAR(30)", True, None, None),
        "manufacturer": ("VARCHAR(150)", True, None, None),
        "product_name": ("VARCHAR(100)", True, None, None),
        "install_year": ("INTEGER", True, None, None),
        "replacement_year": ("INTEGER", True, None, None),
        "status": ("VARCHAR(30)", True, None, None),
        "length_m": ("FLOAT", True, None, None),
        "width_m": ("FLOAT", True, None, None),
        "faa_accepted": ("BOOLEAN", True, None, None),
        "notes": ("TEXT", True, None, None),
        "confirmed_vendor": ("VARCHAR(150)", True, None, None),
        "created_at": ("DATETIME", True, ("callable",), None),
        "updated_at": ("DATETIME", True, ("callable",), ("callable",)),
    },
    "incidents": {
        "id": ("INTEGER", False, None, None),
        "airport_id": ("INTEGER", False, None, None),
        "runway_id": ("INTEGER", True, None, None),
        "source_id": ("INTEGER", True, None, None),
        "incident_date": ("DATE", False, None, None),
        "aircraft_type": ("VARCHAR(100)", True, None, None),
        "operator": ("VARCHAR(150)", True, None, None),
        "incident_type": ("VARCHAR(100)", False, None, None),
        "emas_engaged": ("BOOLEAN", False, ("scalar", False), None),
        "injuries": ("VARCHAR(100)", True, None, None),
        "aircraft_damage": ("VARCHAR(100)", True, None, None),
        "summary": ("TEXT", True, None, None),
        "source_url": ("VARCHAR(1000)", True, None, None),
        "official_report_url": ("VARCHAR(1000)", True, None, None),
        "implies_replacement": ("BOOLEAN", False, ("scalar", True), None),
        "created_at": ("DATETIME", True, ("callable",), None),
        "updated_at": ("DATETIME", True, ("callable",), ("callable",)),
    },
    "signals": {
        "id": ("INTEGER", False, None, None),
        "airport_id": ("INTEGER", False, None, None),
        "runway_id": ("INTEGER", True, None, None),
        "source_id": ("INTEGER", True, None, None),
        "title": ("VARCHAR(250)", False, None, None),
        "category": ("VARCHAR(50)", False, None, None),
        "confidence": ("VARCHAR(30)", False, None, None),
        "target_year": ("INTEGER", True, None, None),
        "notes": ("TEXT", True, None, None),
        "status": ("VARCHAR(50)", True, None, None),
        "planning_year": ("INTEGER", True, None, None),
        "procurement_year": ("INTEGER", True, None, None),
        "construction_start": ("DATE", True, None, None),
        "completion_date": ("DATE", True, None, None),
        "estimated_total_value_usd": ("NUMERIC(14, 2)", True, None, None),
        "estimated_emas_value_usd": ("NUMERIC(14, 2)", True, None, None),
        "probability_score": ("FLOAT", True, None, None),
        "supplier": ("VARCHAR(150)", True, None, None),
        "likely_supplier": ("VARCHAR(150)", True, None, None),
        "supplier_reason": ("TEXT", True, None, None),
        "last_verified_at": ("DATE", True, None, None),
        "manual_year_estimate": ("INTEGER", True, None, None),
        "confirmed_vendor": ("VARCHAR(150)", True, None, None),
        "installation_id": ("INTEGER", True, None, None),
        "created_at": ("DATETIME", True, ("callable",), None),
        "updated_at": ("DATETIME", True, ("callable",), ("callable",)),
        "source_notes": ("TEXT", True, None, None),
        "published": ("BOOLEAN", False, ("scalar", True), None),
    },
    "signal_dispositions": {
        "id": ("INTEGER", False, None, None),
        "decision": ("VARCHAR(30)", False, None, None),
        "reason": ("TEXT", False, None, None),
        "reviewer": ("VARCHAR(100)", False, None, None),
        "created_at": ("DATETIME", False, ("callable",), None),
        "supersedes_id": ("INTEGER", True, None, None),
    },
    "signal_disposition_members": {
        "id": ("INTEGER", False, None, None),
        "disposition_id": ("INTEGER", False, None, None),
        "signal_id": ("INTEGER", False, None, None),
    },
    "unknown_airport_candidates": {
        "id": ("INTEGER", False, None, None),
        "candidate_fingerprint": ("VARCHAR(64)", False, None, None),
        "raw_name": ("VARCHAR(200)", False, None, None),
        "raw_city": ("VARCHAR(100)", True, None, None),
        "raw_state_region": ("VARCHAR(100)", True, None, None),
        "raw_country": ("VARCHAR(100)", True, None, None),
        "raw_iata_code": ("VARCHAR(20)", True, None, None),
        "raw_icao_code": ("VARCHAR(20)", True, None, None),
        "raw_faa_lid": ("VARCHAR(20)", True, None, None),
        "raw_runway_designation": ("VARCHAR(20)", True, None, None),
        "evidence_source_locator": ("TEXT", True, None, None),
        "evidence_artifact_identity": ("TEXT", True, None, None),
        "resolved_airport_id": ("INTEGER", True, None, None),
        "created_at": ("DATETIME", False, ("callable",), None),
    },
    "unknown_airport_candidate_reviews": {
        "id": ("INTEGER", False, None, None),
        "candidate_id": ("INTEGER", False, None, None),
        "action": ("VARCHAR(30)", False, None, None),
        "reason": ("TEXT", False, None, None),
        "reviewer": ("VARCHAR(100)", False, None, None),
        "matched_airport_id": ("INTEGER", True, None, None),
        "created_at": ("DATETIME", False, ("callable",), None),
        "supersedes_review_id": ("INTEGER", True, None, None),
    },
}

EXPECTED_PRIMARY_KEYS = {table_name: ("id",) for table_name in EXPECTED_COLUMNS}

EXPECTED_FOREIGN_KEYS = {
    "acquisition_sources": {("publishing_source_id", "publishing_sources.id")},
    "acquisition_runs": {
        ("acquisition_source_id", "acquisition_sources.id"),
        ("snapshot_id", "snapshots.id"),
    },
    "snapshots": {
        ("acquisition_source_id", "acquisition_sources.id"),
        ("first_acquisition_run_id", "acquisition_runs.id"),
    },
    "airports": set(),
    "runways": {("airport_id", "airports.id")},
    "runway_ends": {("runway_id", "runways.id")},
    "publishing_sources": set(),
    "sources": set(),
    "source_assertions": {
        ("source_id", "sources.id"),
        ("airport_id", "airports.id"),
        ("runway_id", "runways.id"),
        ("signal_id", "signals.id"),
    },
    "physical_installation_identities": {
        ("airport_id", "airports.id"), ("runway_id", "runways.id"), ("runway_end_id", "runway_ends.id"),
    },
    "installation_assertion_links": {
        ("assertion_id", "source_assertions.id"),
        ("physical_installation_id", "physical_installation_identities.id"),
        ("supersedes_link_id", "installation_assertion_links.id"),
    },
    "reviewer_actions": {
        ("source_assertion_id", "source_assertions.id"),
        ("supersedes_action_id", "reviewer_actions.id"),
        ("duplicate_of_signal_id", "signals.id"),
    },
    "installations": {
        ("airport_id", "airports.id"),
        ("runway_id", "runways.id"),
        ("source_id", "sources.id"),
    },
    "incidents": {
        ("airport_id", "airports.id"),
        ("runway_id", "runways.id"),
        ("source_id", "sources.id"),
    },
    "signals": {
        ("airport_id", "airports.id"),
        ("runway_id", "runways.id"),
        ("source_id", "sources.id"),
        ("installation_id", "installations.id"),
    },
    "signal_dispositions": {("supersedes_id", "signal_dispositions.id")},
    "signal_disposition_members": {
        ("disposition_id", "signal_dispositions.id"),
        ("signal_id", "signals.id"),
    },
    "unknown_airport_candidates": {("resolved_airport_id", "airports.id")},
    "unknown_airport_candidate_reviews": {
        ("candidate_id", "unknown_airport_candidates.id"),
        ("matched_airport_id", "airports.id"),
        ("supersedes_review_id", "unknown_airport_candidate_reviews.id"),
    },
}

EXPECTED_INDEXES = {
    "acquisition_sources": {
        ("ix_acquisition_sources_publishing_source_id", ("publishing_source_id",), False),
    },
    "acquisition_runs": {
        ("ix_acquisition_runs_acquisition_source_id", ("acquisition_source_id",), False),
        ("ix_acquisition_runs_snapshot_id", ("snapshot_id",), False),
    },
    "snapshots": {
        ("ix_snapshots_acquisition_source_id", ("acquisition_source_id",), False),
        ("ix_snapshots_sha256", ("sha256",), False),
    },
    "airports": {
        ("ix_airports_country", ("country",), False),
        ("ix_airports_faa_code", ("faa_code",), False),
        ("ix_airports_iata_code", ("iata_code",), False),
        ("ix_airports_icao_code", ("icao_code",), False),
        ("ix_airports_name", ("name",), False),
    },
    "runways": {
        ("ix_runways_airport_id", ("airport_id",), False),
        ("ix_runways_designation", ("designation",), False),
    },
    "runway_ends": {
        ("ix_runway_ends_runway_id", ("runway_id",), False),
    },
    "publishing_sources": set(),
    "sources": {
        ("ix_sources_source_type", ("source_type",), False),
        ("uq_sources_external_id", ("external_id",), True),
    },
    "source_assertions": {
        ("ix_source_assertions_source_id", ("source_id",), False),
        ("ix_source_assertions_airport_id", ("airport_id",), False),
        ("ix_source_assertions_runway_id", ("runway_id",), False),
        ("ix_source_assertions_signal_id", ("signal_id",), False),
    },
    "physical_installation_identities": {
        ("ix_physical_installation_identities_airport_id", ("airport_id",), False),
        ("ix_physical_installation_identities_runway_id", ("runway_id",), False),
        ("ix_physical_installation_identities_runway_end_id", ("runway_end_id",), False),
    },
    "installation_assertion_links": {
        ("ix_installation_assertion_links_assertion_id", ("assertion_id",), False),
        ("ix_installation_assertion_links_physical_installation_id", ("physical_installation_id",), False),
        ("ix_installation_assertion_links_supersedes_link_id", ("supersedes_link_id",), False),
    },
    "reviewer_actions": {
        ("ix_reviewer_actions_source_assertion_id", ("source_assertion_id",), False),
        ("ix_reviewer_actions_supersedes_action_id", ("supersedes_action_id",), False),
        ("ix_reviewer_actions_duplicate_of_signal_id", ("duplicate_of_signal_id",), False),
    },
    "installations": {
        ("ix_installations_airport_id", ("airport_id",), False),
        ("ix_installations_runway_id", ("runway_id",), False),
        ("ix_installations_source_id", ("source_id",), False),
        ("ix_installations_type", ("type",), False),
        ("ix_installations_install_year", ("install_year",), False),
        ("ix_installations_status", ("status",), False),
    },
    "incidents": {
        ("ix_incidents_airport_id", ("airport_id",), False),
        ("ix_incidents_runway_id", ("runway_id",), False),
        ("ix_incidents_source_id", ("source_id",), False),
        ("ix_incidents_emas_engaged", ("emas_engaged",), False),
        ("ix_incidents_incident_date", ("incident_date",), False),
    },
    "signals": {
        ("ix_signals_airport_id", ("airport_id",), False),
        ("ix_signals_runway_id", ("runway_id",), False),
        ("ix_signals_source_id", ("source_id",), False),
        ("ix_signals_title", ("title",), False),
        ("ix_signals_category", ("category",), False),
        ("ix_signals_confidence", ("confidence",), False),
        ("ix_signals_target_year", ("target_year",), False),
        ("ix_signals_status", ("status",), False),
        ("ix_signals_planning_year", ("planning_year",), False),
        ("ix_signals_procurement_year", ("procurement_year",), False),
        ("ix_signals_installation_id", ("installation_id",), False),
    },
    "signal_dispositions": {
        ("ix_signal_dispositions_supersedes_id", ("supersedes_id",), False),
    },
    "signal_disposition_members": {
        ("ix_signal_disposition_members_disposition_id", ("disposition_id",), False),
        ("ix_signal_disposition_members_signal_id", ("signal_id",), False),
    },
    "unknown_airport_candidates": {
        ("ix_unknown_airport_candidates_candidate_fingerprint", ("candidate_fingerprint",), False),
        ("ix_unknown_airport_candidates_resolved_airport_id", ("resolved_airport_id",), False),
    },
    "unknown_airport_candidate_reviews": {
        ("ix_unknown_airport_candidate_reviews_candidate_id", ("candidate_id",), False),
        ("ix_unknown_airport_candidate_reviews_matched_airport_id", ("matched_airport_id",), False),
        ("ix_unknown_airport_candidate_reviews_supersedes_review_id", ("supersedes_review_id",), False),
    },
}

DEFAULT_CASCADE = frozenset({"merge", "save-update"})
DELETE_ORPHAN_CASCADE = frozenset(
    {"delete", "delete-orphan", "expunge", "merge", "refresh-expire", "save-update"}
)

EXPECTED_RELATIONSHIPS = {
    "AcquisitionSource": {
        "publishing_source": ("PublishingSource", "acquisition_sources", DEFAULT_CASCADE),
        "runs": ("AcquisitionRun", "source", DEFAULT_CASCADE),
        "snapshots": ("Snapshot", "source", DEFAULT_CASCADE),
    },
    "AcquisitionRun": {
        "source": ("AcquisitionSource", "runs", DEFAULT_CASCADE),
        "snapshot": ("Snapshot", "runs", DEFAULT_CASCADE),
    },
    "Snapshot": {
        "source": ("AcquisitionSource", "snapshots", DEFAULT_CASCADE),
        "first_acquisition_run": ("AcquisitionRun", None, DEFAULT_CASCADE),
        "runs": ("AcquisitionRun", "snapshot", DEFAULT_CASCADE),
    },
    "Airport": {
        "runways": ("Runway", "airport", DELETE_ORPHAN_CASCADE),
        "signals": ("Signal", "airport", DELETE_ORPHAN_CASCADE),
        "installations": ("Installation", "airport", DELETE_ORPHAN_CASCADE),
        "incidents": ("Incident", "airport", DELETE_ORPHAN_CASCADE),
        "source_assertions": ("SourceAssertion", "airport", DEFAULT_CASCADE),
        "physical_installation_identities": ("PhysicalInstallationIdentity", "airport", DEFAULT_CASCADE),
    },
    "Runway": {
        "airport": ("Airport", "runways", DEFAULT_CASCADE),
        "signals": ("Signal", "runway", DEFAULT_CASCADE),
        "installations": ("Installation", "runway", DEFAULT_CASCADE),
        "incidents": ("Incident", "runway", DEFAULT_CASCADE),
        "source_assertions": ("SourceAssertion", "runway", DEFAULT_CASCADE),
        "physical_installation_identities": ("PhysicalInstallationIdentity", "runway", DEFAULT_CASCADE),
        "runway_ends": ("RunwayEnd", "runway", DELETE_ORPHAN_CASCADE),
    },
    "RunwayEnd": {
        "runway": ("Runway", "runway_ends", DEFAULT_CASCADE),
        "physical_installation_identities": ("PhysicalInstallationIdentity", "canonical_runway_end", DEFAULT_CASCADE),
    },
    "Signal": {
        "airport": ("Airport", "signals", DEFAULT_CASCADE),
        "runway": ("Runway", "signals", DEFAULT_CASCADE),
        "source": ("Source", None, DEFAULT_CASCADE),
        "installation": ("Installation", None, DEFAULT_CASCADE),
        "supporting_source_assertions": ("SourceAssertion", "signal", DEFAULT_CASCADE),
    },
    "PublishingSource": {
        "acquisition_sources": ("AcquisitionSource", "publishing_source", DEFAULT_CASCADE),
    },
    "Installation": {
        "airport": ("Airport", "installations", DEFAULT_CASCADE),
        "runway": ("Runway", "installations", DEFAULT_CASCADE),
        "source": ("Source", None, DEFAULT_CASCADE),
    },
    "Source": {"assertions": ("SourceAssertion", "source", DEFAULT_CASCADE)},
    "SourceAssertion": {
        "source": ("Source", "assertions", DEFAULT_CASCADE),
        "airport": ("Airport", "source_assertions", DEFAULT_CASCADE),
        "runway": ("Runway", "source_assertions", DEFAULT_CASCADE),
        "installation_assertion_links": ("InstallationAssertionLink", "assertion", DEFAULT_CASCADE),
        "reviewer_actions": ("ReviewerAction", "source_assertion", DEFAULT_CASCADE),
        "signal": ("Signal", "supporting_source_assertions", DEFAULT_CASCADE),
    },
    "ReviewerAction": {
        "source_assertion": ("SourceAssertion", "reviewer_actions", DEFAULT_CASCADE),
        "supersedes": ("ReviewerAction", None, DEFAULT_CASCADE),
        "duplicate_of_signal": ("Signal", None, DEFAULT_CASCADE),
    },
    "PhysicalInstallationIdentity": {
        "airport": ("Airport", "physical_installation_identities", DEFAULT_CASCADE),
        "runway": ("Runway", "physical_installation_identities", DEFAULT_CASCADE),
        "canonical_runway_end": ("RunwayEnd", "physical_installation_identities", DEFAULT_CASCADE),
        "assertion_links": ("InstallationAssertionLink", "physical_installation", DEFAULT_CASCADE),
    },
    "InstallationAssertionLink": {
        "assertion": ("SourceAssertion", "installation_assertion_links", DEFAULT_CASCADE),
        "physical_installation": ("PhysicalInstallationIdentity", "assertion_links", DEFAULT_CASCADE),
        "supersedes": ("InstallationAssertionLink", None, DEFAULT_CASCADE),
    },
    "Incident": {
        "airport": ("Airport", "incidents", DEFAULT_CASCADE),
        "runway": ("Runway", "incidents", DEFAULT_CASCADE),
        "source": ("Source", None, DEFAULT_CASCADE),
    },
    "SignalDisposition": {
        "supersedes": ("SignalDisposition", None, DEFAULT_CASCADE),
        "members": ("SignalDispositionMember", "disposition", DEFAULT_CASCADE),
    },
    "SignalDispositionMember": {
        "disposition": ("SignalDisposition", "members", DEFAULT_CASCADE),
        "signal": ("Signal", None, DEFAULT_CASCADE),
    },
    "UnknownAirportCandidate": {
        "reviews": ("UnknownAirportCandidateReview", "candidate", DEFAULT_CASCADE),
        "resolved_airport": ("Airport", None, DEFAULT_CASCADE),
    },
    "UnknownAirportCandidateReview": {
        "candidate": ("UnknownAirportCandidate", "reviews", DEFAULT_CASCADE),
        "matched_airport": ("Airport", None, DEFAULT_CASCADE),
        "supersedes": ("UnknownAirportCandidateReview", None, DEFAULT_CASCADE),
    },
}


def _default_contract(default):
    if default is None:
        return None
    if default.is_callable:
        return ("callable",)
    if default.is_scalar:
        return ("scalar", default.arg)
    return ("sql", str(default.arg))


def test_all_current_models_are_exported_from_app_models():
    assert [
        Airport.__name__,
        AcquisitionRun.__name__,
        AcquisitionSource.__name__,
        Incident.__name__,
        Installation.__name__,
        InstallationAssertionLink.__name__,
        PhysicalInstallationIdentity.__name__,
        PublishingSource.__name__,
        ReviewerAction.__name__,
        Runway.__name__,
        RunwayEnd.__name__,
        Signal.__name__,
        SignalDisposition.__name__,
        SignalDispositionMember.__name__,
        Source.__name__,
        SourceAssertion.__name__,
        Snapshot.__name__,
        UnknownAirportCandidate.__name__,
        UnknownAirportCandidateReview.__name__,
    ] == [
        "Airport",
        "AcquisitionRun",
        "AcquisitionSource",
        "Incident",
        "Installation",
        "InstallationAssertionLink",
        "PhysicalInstallationIdentity",
        "PublishingSource",
        "ReviewerAction",
        "Runway",
        "RunwayEnd",
        "Signal",
        "SignalDisposition",
        "SignalDispositionMember",
        "Source",
        "SourceAssertion",
        "Snapshot",
        "UnknownAirportCandidate",
        "UnknownAirportCandidateReview",
    ]


def test_configure_mappers_succeeds():
    configure_mappers()


def test_model_table_contract_is_unchanged():
    assert set(Base.metadata.tables) == set(EXPECTED_COLUMNS)

    for table_name, expected_columns in EXPECTED_COLUMNS.items():
        table = Base.metadata.tables[table_name]
        actual_columns = {
            column.name: (
                str(column.type),
                column.nullable,
                _default_contract(column.default),
                _default_contract(column.onupdate),
            )
            for column in table.columns
        }
        actual_primary_key = tuple(column.name for column in table.primary_key.columns)
        actual_foreign_keys = {
            (column.name, foreign_key.target_fullname)
            for column in table.columns
            for foreign_key in column.foreign_keys
        }
        actual_indexes = {
            (index.name, tuple(column.name for column in index.columns), index.unique)
            for index in table.indexes
        }

        assert actual_columns == expected_columns
        assert actual_primary_key == EXPECTED_PRIMARY_KEYS[table_name]
        assert actual_foreign_keys == EXPECTED_FOREIGN_KEYS[table_name]
        assert actual_indexes == EXPECTED_INDEXES[table_name]


def test_model_relationship_contract_is_unchanged():
    configure_mappers()
    actual = {
        mapper.class_.__name__: {
            relationship.key: (
                relationship.mapper.class_.__name__,
                relationship.back_populates,
                frozenset(relationship.cascade),
            )
            for relationship in mapper.relationships
        }
        for mapper in Base.registry.mappers
    }

    assert actual == EXPECTED_RELATIONSHIPS


def test_current_metadata_creates_cleanly_in_isolated_sqlite():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        with engine.connect() as connection:
            connection.execute(text("PRAGMA foreign_keys=ON"))
            Base.metadata.create_all(connection)
            inspector = inspect(connection)

            assert set(inspector.get_table_names()) == set(EXPECTED_COLUMNS)
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    finally:
        engine.dispose()
