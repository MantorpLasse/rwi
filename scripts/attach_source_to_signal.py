"""Attach a Source to an existing Signal by ID.

Creates a new Source row and points signal.source_id at it. Signal only
holds a single source_id, so this replaces whatever source the signal
previously referenced - the old Source row itself is left in place, just no
longer linked from this signal.

Usage:
    python -m scripts.attach_source_to_signal --signal-id 5 \\
        --title "Fulton County Master Plan Technical Report (Draft, dec 2022)" \\
        --source-type master_plan \\
        --url "https://example.test/plan.pdf" \\
        [--publisher "Fulton County"] [--published-date 2022-12-16] [--summary "..."]
"""
from __future__ import annotations

import argparse
from datetime import date

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Signal, Source


def attach_source(
    session: Session,
    signal_id: int,
    *,
    title: str,
    source_type: str,
    url: str,
    publisher: str | None = None,
    published_date: date | None = None,
    summary: str | None = None,
) -> tuple[Signal, int | None]:
    """Returns the updated signal and whatever source_id it had before."""
    signal = session.get(Signal, signal_id)
    if signal is None:
        raise SystemExit(f"No signal with id={signal_id}")
    previous_source_id = signal.source_id

    source = Source(
        title=title,
        source_type=source_type,
        url=url,
        publisher=publisher,
        published_date=published_date,
        summary=summary,
    )
    session.add(source)
    session.flush()

    signal.source = source
    session.commit()
    session.refresh(signal)
    return signal, previous_source_id


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--signal-id", type=int, required=True, dest="signal_id")
    parser.add_argument("--title", required=True)
    parser.add_argument("--source-type", required=True, dest="source_type")
    parser.add_argument("--url", required=True)
    parser.add_argument("--publisher", default=None)
    parser.add_argument("--published-date", default=None, dest="published_date")
    parser.add_argument("--summary", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    published = date.fromisoformat(args.published_date) if args.published_date else None

    with SessionLocal() as session:
        signal, previous_source_id = attach_source(
            session,
            args.signal_id,
            title=args.title,
            source_type=args.source_type,
            url=args.url,
            publisher=args.publisher,
            published_date=published,
            summary=args.summary,
        )

    if previous_source_id:
        print(f"Signal {signal.id} ({signal.title!r}) source replaced: {previous_source_id} -> {signal.source_id}")
    else:
        print(f"Signal {signal.id} ({signal.title!r}) source set: {signal.source_id}")
    print(f"  {args.title}")
    print(f"  {args.url}")


if __name__ == "__main__":
    main()
