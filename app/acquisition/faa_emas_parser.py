from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Iterable


PARSER_VERSION = "faa-emas-tableau/0.1-boundary"
WORKBOOK = "EMASIncidentsandInstallations"
VIEW = "Main"


class FAAEmasParseErrorCode(str, Enum):
    EMPTY_PAYLOAD = "empty_payload"
    UNSUPPORTED_PAYLOAD = "unsupported_payload"
    MALFORMED_PAYLOAD = "malformed_payload"
    EXPECTED_TABLEAU_STRUCTURE_MISSING = "expected_tableau_structure_missing"
    REQUIRED_AIRPORT_IDENTIFIER_MISSING = "required_airport_identifier_missing"
    DUPLICATE_SOURCE_LOCATOR = "duplicate_source_locator"


class FAAEmasParseError(ValueError):
    def __init__(self, code: FAAEmasParseErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code.value


@dataclass(frozen=True)
class FAAEmasCandidate:
    airport_identifier_raw: str
    airport_name_raw: str | None
    city_raw: str | None
    state_raw: str | None
    installation_type_raw: str | None
    system_count_raw: str | None
    installation_year_display_raw: str | None
    incident_count_raw: str | None
    latitude_raw: str | None
    longitude_raw: str | None
    source_locator: str
    source_record_raw: bytes


@dataclass(frozen=True)
class FAAEmasParseReport:
    candidates: tuple[FAAEmasCandidate, ...]
    source_workbook: str
    source_view: str
    parser_version: str
    warnings: tuple[str, ...]


class FAAEmasSnapshotParser:
    """FAA-specific replay boundary for preserved Tableau response bytes.

    The current acquisition provider preserves the initial Tableau HTML response,
    not an observed VizQL data response. Until a valid response fixture establishes
    the exact installation-mark grammar, this parser intentionally fails closed.
    """

    def parse(
        self, snapshot_bytes: bytes, media_type: str | None = None
    ) -> FAAEmasParseReport:
        if not snapshot_bytes:
            raise FAAEmasParseError(
                FAAEmasParseErrorCode.EMPTY_PAYLOAD,
                "FAA EMAS Snapshot payload is empty.",
            )

        normalized_media_type = (
            media_type.partition(";")[0].strip().lower() if media_type else None
        )
        if normalized_media_type == "application/json":
            try:
                json.loads(snapshot_bytes)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise FAAEmasParseError(
                    FAAEmasParseErrorCode.MALFORMED_PAYLOAD,
                    "FAA EMAS JSON payload is malformed.",
                ) from exc
            raise FAAEmasParseError(
                FAAEmasParseErrorCode.UNSUPPORTED_PAYLOAD,
                "No observed FAA EMAS VizQL JSON structure is available.",
            )

        if normalized_media_type not in {None, "text/html", "text/plain"}:
            raise FAAEmasParseError(
                FAAEmasParseErrorCode.UNSUPPORTED_PAYLOAD,
                f"Unsupported FAA EMAS payload media type: {normalized_media_type}.",
            )

        try:
            text = snapshot_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FAAEmasParseError(
                FAAEmasParseErrorCode.MALFORMED_PAYLOAD,
                "FAA EMAS text payload is not valid UTF-8.",
            ) from exc

        lowered = text.lstrip().lower()
        if lowered.startswith("<html") or lowered.startswith("<!doctype html"):
            if "</html>" not in lowered:
                raise FAAEmasParseError(
                    FAAEmasParseErrorCode.MALFORMED_PAYLOAD,
                    "FAA EMAS HTML payload is incomplete.",
                )
            raise FAAEmasParseError(
                FAAEmasParseErrorCode.EXPECTED_TABLEAU_STRUCTURE_MISSING,
                "Snapshot contains a Tableau HTML shell, not installation data.",
            )

        raise FAAEmasParseError(
            FAAEmasParseErrorCode.UNSUPPORTED_PAYLOAD,
            "Snapshot is not a supported FAA EMAS Tableau payload.",
        )

    def _build_report(
        self,
        candidates: Iterable[FAAEmasCandidate],
        warnings: Iterable[str] = (),
    ) -> FAAEmasParseReport:
        """Validate future decoded records before exposing a parse result."""
        ordered = tuple(candidates)
        locators: set[str] = set()
        for candidate in ordered:
            if not candidate.airport_identifier_raw:
                raise FAAEmasParseError(
                    FAAEmasParseErrorCode.REQUIRED_AIRPORT_IDENTIFIER_MISSING,
                    "FAA EMAS candidate lacks its required raw airport identifier.",
                )
            if candidate.source_locator in locators:
                raise FAAEmasParseError(
                    FAAEmasParseErrorCode.DUPLICATE_SOURCE_LOCATOR,
                    f"Duplicate FAA EMAS source locator: {candidate.source_locator}.",
                )
            locators.add(candidate.source_locator)
        return FAAEmasParseReport(
            candidates=ordered,
            source_workbook=WORKBOOK,
            source_view=VIEW,
            parser_version=PARSER_VERSION,
            warnings=tuple(warnings),
        )
