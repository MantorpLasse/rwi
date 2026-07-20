from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.acquisition.faa_emas_parser import (
    FAAEmasCandidate,
    FAAEmasParseError,
    FAAEmasSnapshotParser,
)
from app.database import Base
from app.models import (
    AcquisitionRun,
    AcquisitionRunStatus,
    AcquisitionSource,
    PublishingSource,
    Snapshot,
)


def candidate(**overrides) -> FAAEmasCandidate:
    values = {
        "airport_identifier_raw": "STP",
        "airport_name_raw": "St. Paul Downtown Airport",
        "city_raw": "St. Paul",
        "state_raw": "Minnesota",
        "installation_type_raw": "EMASMAX",
        "system_count_raw": "2",
        "installation_year_display_raw": "1999 (2008) (2025)",
        "incident_count_raw": None,
        "latitude_raw": None,
        "longitude_raw": None,
        "source_locator": "worksheet=Installations;mark=0",
        "source_record_raw": b"preserved source fragment",
    }
    values.update(overrides)
    return FAAEmasCandidate(**values)


def assert_code(code: str, payload: bytes, media_type: str | None = None) -> None:
    with pytest.raises(FAAEmasParseError) as caught:
        FAAEmasSnapshotParser().parse(payload, media_type)
    assert caught.value.code == code


def test_candidate_is_immutable_and_preserves_raw_values_exactly():
    item = candidate()
    assert item.installation_year_display_raw == "1999 (2008) (2025)"
    assert item.source_locator == "worksheet=Installations;mark=0"
    assert item.source_record_raw == b"preserved source fragment"
    assert item.latitude_raw is None
    with pytest.raises(FrozenInstanceError):
        item.system_count_raw = "3"


def test_candidate_has_no_runway_runway_end_or_manufacturer_fields():
    names = {field.name for field in fields(FAAEmasCandidate)}
    assert "runway" not in names
    assert "runway_end" not in names
    assert "manufacturer" not in names


def test_report_is_immutable_ordered_and_deterministic():
    parser = FAAEmasSnapshotParser()
    candidates = (
        candidate(source_locator="worksheet=Installations;mark=0"),
        candidate(source_locator="worksheet=Installations;mark=1", city_raw=None),
    )
    first = parser._build_report(candidates, ("airport-level-only evidence",))
    second = parser._build_report(candidates, ("airport-level-only evidence",))
    assert first == second
    assert first.candidates == candidates
    assert first.source_workbook == "EMASIncidentsandInstallations"
    assert first.source_view == "Main"
    with pytest.raises(FrozenInstanceError):
        first.source_view = "Other"


def test_required_airport_identifier_missing_fails_complete_report():
    parser = FAAEmasSnapshotParser()
    with pytest.raises(FAAEmasParseError) as caught:
        parser._build_report((candidate(airport_identifier_raw=""),))
    assert caught.value.code == "required_airport_identifier_missing"


def test_duplicate_source_locator_fails_complete_report():
    parser = FAAEmasSnapshotParser()
    with pytest.raises(FAAEmasParseError) as caught:
        parser._build_report((candidate(), candidate()))
    assert caught.value.code == "duplicate_source_locator"


def test_empty_payload():
    assert_code("empty_payload", b"")


def test_unsupported_payload_and_media_type():
    assert_code("unsupported_payload", b"not tableau")
    assert_code("unsupported_payload", b"%PDF", "application/pdf")


def test_malformed_payload():
    assert_code("malformed_payload", b"{", "application/json")
    assert_code("malformed_payload", b"\xff", "text/plain")


def test_current_tableau_html_shell_has_no_installation_structure():
    shell = (
        b"<!doctype html><html><body>"
        b"EMASIncidentsandInstallations Main"
        b"</body></html>"
    )
    assert_code("expected_tableau_structure_missing", shell, "text/html")


def test_replay_reads_exact_bytes_from_persisted_snapshot_without_http():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        publisher = PublishingSource(name="FAA")
        source = AcquisitionSource(
            publishing_source=publisher,
            key="faa.emas.replay-test",
            display_name="FAA EMAS replay test",
            acquisition_type="tableau",
            canonical_url="https://example.invalid/tableau",
            active=True,
        )
        run = AcquisitionRun(
            source=source,
            started_at=datetime.now(UTC),
            status=AcquisitionRunStatus.RUNNING,
            request_url=source.canonical_url,
            provider_version="fixture/1",
            duration_seconds=0,
        )
        session.add(run)
        session.flush()
        payload = b"not a supported Tableau data payload"
        snapshot = Snapshot(
            source=source,
            first_acquisition_run=run,
            payload=payload,
            sha256="0" * 64,
            byte_size=len(payload),
            media_type="application/octet-stream",
            retrieved_at=datetime.now(UTC),
        )
        session.add(snapshot)
        session.flush()
        run.snapshot = snapshot
        run.status = AcquisitionRunStatus.SUCCESS
        run.completed_at = datetime.now(UTC)
        session.commit()

        persisted = session.get(Snapshot, snapshot.id)
        assert persisted.payload == payload
        assert_code("unsupported_payload", persisted.payload, persisted.media_type)
