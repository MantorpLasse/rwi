"""Graduate a confirmed-built Signal into a real Installation row.

Per docs/utredning_2026-07-26.md point 1/2: nothing in this codebase ever
turns a Signal into an Installation - every existing Installation-creation
site (scripts/add_brazil_expansion.py, scripts/add_rw_shareholder_letter_signals.py,
scripts/import_faa_csv.py) is an original import, not a graduation. A Signal
whose source later confirms physical completion (e.g. an official airport
page, not just an order/contract) was left sitting as an open-looking
"new_installation" signal with no installation to show for it.

Deliberately one signal at a time via --signal-id, not a bulk scan that
guesses which signals are "done" - judging whether a source actually
confirms completion (vs. "order signed", "planned", "80% complete") is the
same kind of manual call PLAN_FORENKLING.md's rule 2 already keeps manual
for keyword-flagged signals.

Copies airport/runway/source/confirmed_vendor/notes onto a new Installation
(status="active"), since --type and --install-year usually aren't
mechanically derivable from a Signal's existing fields. Sets
Signal.status="completed" and Signal.installation_id to the new row - the
Signal itself is never deleted, matching this project's "nothing gets
deleted, only unlinked/marked" convention.

Usage:
    python -m scripts.graduate_signal_to_installation --signal-id 65 \\
        --type EMAS --install-year 2026

Safe to re-run only in the sense that re-running on an already-completed
signal refuses (raises) rather than creating a second Installation.
"""
from __future__ import annotations

import argparse

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.database import SessionLocal, engine
from app.models import Installation, Signal


def ensure_signal_installation_id_column(bind=engine) -> None:
    """Add signals.installation_id if an older database doesn't have it yet.

    Base.metadata.create_all() only creates missing tables, not missing
    columns on ones that already exist - idempotent, safe to call every run.
    """
    existing = {c["name"] for c in inspect(bind).get_columns("signals")}
    if "installation_id" not in existing:
        with bind.begin() as connection:
            connection.execute(text("ALTER TABLE signals ADD COLUMN installation_id INTEGER REFERENCES installations(id)"))


def graduate(
    session: Session,
    signal_id: int,
    *,
    install_type: str,
    install_year: int,
) -> Installation:
    signal = session.get(Signal, signal_id)
    if signal is None:
        raise SystemExit(f"No signal with id={signal_id}")
    if signal.status == "completed":
        raise SystemExit(
            f"Signal {signal_id} is already completed (installation_id={signal.installation_id})."
        )

    installation = Installation(
        airport_id=signal.airport_id,
        runway_id=signal.runway_id,
        source_id=signal.source_id,
        type=install_type,
        install_year=install_year,
        status="active",
        confirmed_vendor=signal.confirmed_vendor,
        notes=signal.notes,
    )
    session.add(installation)
    session.flush()

    signal.status = "completed"
    signal.installation = installation

    session.commit()
    session.refresh(installation)
    return installation


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--signal-id", type=int, required=True, dest="signal_id")
    parser.add_argument("--type", required=True, dest="install_type", help="e.g. EMAS, EMASMAX, greenEMAS")
    parser.add_argument("--install-year", type=int, required=True, dest="install_year")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    ensure_signal_installation_id_column(engine)

    with SessionLocal() as session:
        installation = graduate(
            session, args.signal_id, install_type=args.install_type, install_year=args.install_year
        )

    print(
        f"Signal {args.signal_id} marked completed -> Installation {installation.id} "
        f"(airport_id={installation.airport_id}, type={installation.type}, "
        f"install_year={installation.install_year}, source_id={installation.source_id})."
    )


if __name__ == "__main__":
    main()
