from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from collections.abc import Callable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.acquisition.faa_tableau import (
    FAATableauAcquisitionProvider,
    TableauAcquisitionError,
)
from app.config import settings
from app.database import SessionLocal
from app.models import AcquisitionSource, PublishingSource
from app.services.acquisition import AcquisitionService


SOURCE_KEY = "faa.emas.installations.tableau"
PUBLISHER_NAME = "Federal Aviation Administration"
_SESSION_PATH = re.compile(r"(/sessions/)[^/?#]+", re.IGNORECASE)


def _safe_url(value: str | None) -> str:
    if value is None:
        return "—"
    return _SESSION_PATH.sub(r"\1[redacted]", value)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Perform exactly one controlled FAA EMAS Tableau acquisition."
    )
    parser.add_argument("--allow-live-network", action="store_true")
    parser.add_argument("--allow-database-write", action="store_true")
    parser.add_argument("--capture-diagnostic-html", action="store_true")
    return parser


def _resolve_source(session: Session) -> AcquisitionSource:
    publisher = session.scalar(
        select(PublishingSource).where(PublishingSource.name == PUBLISHER_NAME)
    )
    if publisher is None:
        publisher = PublishingSource(
            name=PUBLISHER_NAME,
            source_type="government",
            homepage_url="https://www.faa.gov/",
            country_code="US",
            reliability_level="official",
        )
        session.add(publisher)
        session.flush()

    source = session.scalar(
        select(AcquisitionSource).where(AcquisitionSource.key == SOURCE_KEY)
    )
    if source is None:
        source = AcquisitionSource(
            publishing_source=publisher,
            key=SOURCE_KEY,
            display_name="FAA EMAS Tableau installations",
            acquisition_type="tableau",
            canonical_url=settings.faa_emas_article_url,
            expected_media_type=None,
            active=True,
        )
        session.add(source)
    elif source.publishing_source_id != publisher.id:
        raise RuntimeError("Configured FAA AcquisitionSource belongs to another publisher.")
    elif source.canonical_url != settings.faa_emas_article_url:
        raise RuntimeError("Configured FAA AcquisitionSource URL does not match settings.")
    session.commit()
    return source


def run_capture(
    argv: Sequence[str] | None = None,
    *,
    session_factory: Callable[[], Session] = SessionLocal,
    provider_factory: Callable[[], FAATableauAcquisitionProvider] = FAATableauAcquisitionProvider,
    service_factory: Callable[[Session, FAATableauAcquisitionProvider], AcquisitionService] = AcquisitionService,
    database_url: str | None = None,
) -> int:
    args = _parser().parse_args(argv)
    target = database_url or settings.database_url
    print(f"Database target: {target}")
    print(f"FAA article URL: {settings.faa_emas_article_url}")
    print(f"Tableau view URL: {settings.faa_emas_tableau_view_url or 'discover from article'}")

    if not args.allow_live_network:
        print("Refusing capture: --allow-live-network is required.", file=sys.stderr)
        return 2
    if not args.allow_database_write:
        print("Refusing capture: --allow-database-write is required.", file=sys.stderr)
        return 2

    with session_factory() as session:
        try:
            source = _resolve_source(session)
            if args.capture_diagnostic_html:
                provider = provider_factory(
                    diagnostic_directory=Path("data/diagnostics/faa_tableau")
                )
            else:
                provider = provider_factory()
            run = service_factory(session, provider).acquire(source)
        except TableauAcquisitionError as exc:
            print(f"Acquisition failed [{exc.code}]: {exc}", file=sys.stderr)
            if exc.diagnostic is not None:
                diagnostic = exc.diagnostic
                print(f"Diagnostic file: {diagnostic.path}")
                print(f"Diagnostic HTTP status: {diagnostic.http_status}")
                print(f"Diagnostic final URL: {_safe_url(diagnostic.final_url)}")
                print(f"Diagnostic Content-Type: {diagnostic.content_type or '—'}")
                print(f"Diagnostic response byte size: {diagnostic.response_byte_size}")
            return 1
        except Exception as exc:
            print(f"Acquisition failed [{type(exc).__name__}]: {exc}", file=sys.stderr)
            return 1

        snapshot = run.snapshot
        if snapshot is None:
            print("Acquisition failed: completed run has no Snapshot.", file=sys.stderr)
            return 1
        print(f"Run status: {run.status.value}")
        print(f"Snapshot ID: {snapshot.id}")
        print(f"SHA-256: {snapshot.sha256}")
        print(f"Byte size: {snapshot.byte_size}")
        print(f"Media type: {snapshot.media_type or '—'}")
        print(f"Request URL: {_safe_url(run.request_url)}")
        print(f"Final URL: {_safe_url(run.final_url)}")
        print(f"Retrieved at: {snapshot.retrieved_at.isoformat()}")
        return 0


def main() -> None:
    raise SystemExit(run_capture())


if __name__ == "__main__":
    main()
