from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.acquisition.faa import FAAAcquisitionProvider
from app.models import AcquisitionRun, AcquisitionRunStatus, AcquisitionSource, Snapshot


logger = logging.getLogger(__name__)


class AcquisitionService:
    def __init__(self, session: Session, provider: FAAAcquisitionProvider) -> None:
        self.session = session
        self.provider = provider

    def acquire(self, source: AcquisitionSource) -> AcquisitionRun:
        if not source.active:
            raise ValueError("AcquisitionSource is inactive")
        if source.canonical_url != self.provider.source_url:
            raise ValueError("Provider URL does not match AcquisitionSource")

        started_at = datetime.now(UTC)
        run = AcquisitionRun(
            source=source,
            started_at=started_at,
            status=AcquisitionRunStatus.RUNNING,
            request_url=self.provider.source_url,
            provider_version=self.provider.version,
            duration_seconds=0.0,
        )
        self.session.add(run)
        self.session.flush()

        try:
            result = self.provider.retrieve()
            if source.expected_media_type and (
                result.content_type is None
                or result.content_type.partition(";")[0].strip().lower()
                != source.expected_media_type.lower()
            ):
                raise ValueError(
                    "FAA response Content-Type does not match AcquisitionSource"
                )
            digest = hashlib.sha256(result.content).hexdigest()
            snapshot = self.session.scalar(
                select(Snapshot).where(
                    Snapshot.acquisition_source_id == source.id,
                    Snapshot.sha256 == digest,
                    Snapshot.byte_size == len(result.content),
                )
            )
            is_new = snapshot is None
            if snapshot is None:
                snapshot = Snapshot(
                    source=source,
                    first_acquisition_run=run,
                    payload=result.content,
                    sha256=digest,
                    byte_size=len(result.content),
                    media_type=result.content_type,
                    retrieved_at=datetime.now(UTC),
                )
                self.session.add(snapshot)
                self.session.flush()

            run.snapshot = snapshot
            run.status = (
                AcquisitionRunStatus.SUCCESS if is_new else AcquisitionRunStatus.NO_CHANGE
            )
            run.completed_at = datetime.now(UTC)
            run.final_url = result.final_url
            run.http_status = result.http_status
            run.content_type = result.content_type
            run.response_headers = json.dumps(
                result.retrieved_headers, sort_keys=True, separators=(",", ":")
            )
            run.provider_version = result.provider_version
            run.duration_seconds = result.duration_seconds
            run.is_new_snapshot = is_new
            self.session.commit()
            self.session.refresh(run)
            logger.info("FAA acquisition run %s completed with %s", run.id, run.status.value)
            return run
        except Exception as exc:
            self.session.rollback()
            failed_run = AcquisitionRun(
                acquisition_source_id=source.id,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                status=self._failure_status(exc),
                request_url=self.provider.source_url,
                provider_version=self.provider.version,
                duration_seconds=0.0,
                error_category=type(exc).__name__,
                error_detail=str(exc),
                is_new_snapshot=False,
            )
            self.session.add(failed_run)
            self.session.commit()
            logger.warning("FAA acquisition run %s failed: %s", failed_run.id, exc)
            raise

    @staticmethod
    def _failure_status(exc: Exception) -> AcquisitionRunStatus:
        if isinstance(exc, httpx.TimeoutException):
            return AcquisitionRunStatus.UNAVAILABLE
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
            return AcquisitionRunStatus.RATE_LIMITED
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {401, 403}:
            return AcquisitionRunStatus.PERMISSION_FAILURE
        if isinstance(exc, httpx.HTTPStatusError):
            return AcquisitionRunStatus.FAILED
        if isinstance(exc, ValueError):
            return AcquisitionRunStatus.INVALID_RESPONSE
        return AcquisitionRunStatus.FAILED
