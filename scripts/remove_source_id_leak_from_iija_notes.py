"""One-off correction: three Signal.source_notes rows (BOS id 3, MHT id 45,
MMU id 47) end with an internal implementation note that leaked into
public, reader-facing text: "primär källa (source_id) lämnas oförändrad"
names a database column (source_id) and describes internal
source-linking bookkeeping that means nothing to a site visitor. All three
were written by the same IIJA cross-reference pass on 2026-07-25 and share
the exact same trailing sentence fragment.

Rewrites the fragment to plain, reader-facing text that keeps the
substantial fact (this is a separate, additional grant on top of the
AIP/USAspending grant(s) already linked to the signal) and drops the
internal detail.

Safe to re-run: guarded by checking the old fragment is still present -
a no-op once applied.
"""
from __future__ import annotations

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Signal

SIGNAL_IDS = (3, 45, 47)

OLD_FRAGMENT = (
    " - separat finansieringspott (IIJA) utöver ovanstående AIP/USAspending-bidrag; "
    "primär källa (source_id) lämnas oförändrad."
)
NEW_FRAGMENT = (
    ". Detta är ett separat, ytterligare bidrag utöver de AIP/USAspending-bidrag som "
    "redan är kopplade till den här signalen."
)


def fix_source_id_leak(session) -> list[int]:
    fixed: list[int] = []
    signals = session.scalars(select(Signal).where(Signal.id.in_(SIGNAL_IDS))).all()
    for signal in signals:
        if signal.source_notes and OLD_FRAGMENT in signal.source_notes:
            signal.source_notes = signal.source_notes.replace(OLD_FRAGMENT, NEW_FRAGMENT)
            fixed.append(signal.id)
    session.commit()
    return fixed


def main() -> None:
    with SessionLocal() as session:
        fixed = fix_source_id_leak(session)
    if fixed:
        print(f"Fixed source_notes for {len(fixed)} signals: {sorted(fixed)}")
    else:
        print("Nothing to do - already fixed.")


if __name__ == "__main__":
    main()
