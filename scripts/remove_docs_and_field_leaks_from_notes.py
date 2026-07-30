"""One-off correction: 6 Signal.source_notes rows and 20 Installation.notes
rows contain internal implementation details that leaked into public,
reader-facing text - found by grepping the live export for "docs/",
"scripts/", "source_id", "install_year=" after the first source_id leak
fix (scripts/remove_source_id_leak_from_iija_notes.py) turned out to have
missed everything except three IIJA rows.

Each row is fixed by one or more small string replacements (same
old-fragment/new-fragment style as remove_source_id_leak_from_iija_notes.py),
applied against the same five categories of leak found:

- "Se docs/X.md" sentences/parentheticals pointing at internal
  investigation docs - dropped entirely.
- Field names (install_year=, source_id) - reworded to plain language,
  the underlying value/fact is kept.
- Script paths (scripts/X.py) - dropped, the factual content next to them
  is kept.
- Internal row IDs (e.g. "id 59, 49, 55, 58, 60", "(id 22)") - replaced
  with the distinguishing fact readers actually care about (amount, year,
  fiscal-year code), the same substitution already used for the BGM
  signal note.
- "Installation-rad"/"databasen" implementation language - reworded to
  plain reader language ("post", "registrerad") instead of table/row talk.

Safe to re-run: each replacement is guarded by checking its old fragment is
still present - a no-op once applied.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Installation, Signal

# (signal id, [(old fragment, new fragment), ...])
SIGNAL_FIXES: dict[int, list[tuple[str, str]]] = {
    6: [
        (
            "(id 59, 49, 55, 58, 60)",
            "(Design FY2021, tre Construction Phase I-poster FY2023, "
            "Construction Phase II FY2026)",
        ),
        ("kvar i databasen, men avlänkad", "kvar registrerad, men inte längre länkad"),
    ],
    49: [("Korsreferens (docs/utredning_svaga_poster.md):", "Korsreferens:")],
    55: [
        ("Korsreferens (docs/utredning_svaga_poster.md):", "Korsreferens:"),
        (
            "del av samma FY2023-tranche som id 49 och 58 (tillsammans $8,0M, nära Phase I:s totalkostnad)",
            "del av samma FY2023-tranche som de två andra posterna (tillsammans $8,0M, nära Phase I:s totalkostnad)",
        ),
    ],
    58: [
        ("Korsreferens (docs/utredning_svaga_poster.md):", "Korsreferens:"),
        (
            "del av samma FY2023-tranche som id 49 och 55 (tillsammans $8,0M, nära Phase I:s totalkostnad)",
            "del av samma FY2023-tranche som de två andra posterna (tillsammans $8,0M, nära Phase I:s totalkostnad)",
        ),
    ],
    59: [("Korsreferens (docs/utredning_svaga_poster.md):", "Korsreferens:")],
    60: [("Korsreferens (docs/utredning_svaga_poster.md):", "Korsreferens:")],
}

# (installation id, [(old fragment, new fragment), ...])
INSTALLATION_FIXES: dict[int, list[tuple[str, str]]] = {
    17: [
        (" (docs/utreding_status_flygplatser.md)", ""),
        ("Ingen ny Installation-rad skapad har.", "Ingen ny post har skapats för detta."),
    ],
    22: [
        (
            " - se docs/utreding_status_flygplatser.md's 'Ny kandidat'-avsnitt "
            "(som antog VNC saknade Installation/Signal helt - det stammer inte, "
            "en generisk FAA-post fanns redan).",
            ".",
        ),
        (
            "Ingen ny Installation-rad skapad har (inget bekraftat installationsar att satta)",
            "Ingen ny post har skapats for detta (inget bekraftat installationsar att satta)",
        ),
    ],
    23: [
        ("Uppföljning (docs/utredning_svaga_poster.md):", "Uppföljning:"),
        ("(install_year ej satt)", "(inget installationsår är registrerat för denna post)"),
    ],
    24: [
        (" (docs/utreding_status_flygplatser.md)", ""),
        ("Ingen ny Installation-rad skapad har -", "Ingen ny post har skapats for detta -"),
    ],
    43: [
        ("Kompletterande källa (docs/utredning_svaga_poster.md, bifynd):", "Kompletterande källa (bifynd):"),
        ("men install_year sätts inte utifrån detta ensamt", "men installationsåret sätts inte utifrån detta ensamt"),
        ("install_year satt till 2012", "Installationsåret satt till 2012"),
        (
            "loser den tidigare oppna fragan i scripts/attach_elm_fy2011_grant_source.py om att",
            "loser den tidigare oppna fragan om att",
        ),
        (
            "Ingen andring av source_id (fortfarande FAA-kartan, som ar mest specifik om var bädden ligger).",
            "Ingen andring av kalla (fortfarande FAA-kartan, som ar mest specifik om var bädden ligger).",
        ),
    ],
    71: [("install_year=2022", "installationsåret 2022")],
    83: [(", se docs/utredning_svaga_poster.md)", ")")],
    84: [(" (docs/utreding_status_flygplatser.md)", "")],
    98: [
        (
            "Ny flygplats, fanns inte i databasen tidigare.",
            "Ny flygplats - fanns inte med i sammanstallningen tidigare.",
        )
    ],
    100: [
        (
            "Loser den tidigare oppna fragan i docs/utredning_svaga_poster.md om okant installationsar for bana 12R.",
            "Loser den tidigare oppna fragan om okant installationsar for bana 12R.",
        )
    ],
    109: [(", se scripts/add_gadelius_greenemas_installations.py)", ")")],
    133: [("install_year=2015", "installationsaret satt till 2015")],
    142: [
        (
            "Flygplatsnamnet i var databas ar rattat till 'Standiford' "
            "(scripts/rename_sandiford_to_standiford.py) - docs/utredning_faa_factsheet_resten.md "
            "flaggade detta ursprungligen.",
            "Flygplatsnamnet ar rattat till 'Standiford' har.",
        )
    ],
    143: [(" Se docs/utreding_faa_tableau.md.", "")],
    144: [(" Se docs/utreding_faa_tableau.md.", "")],
    145: [
        ("(se den raden for projektdetaljer)", "(se posten for bana 6 for projektdetaljer)"),
        (" Se docs/utreding_faa_tableau.md.", ""),
    ],
    146: [(" Se docs/utreding_faa_tableau.md.", "")],
    147: [
        ("(id 43, $8,5M FY2024)", "pa $8,5M FY2024"),
        (" Se docs/utreding_status_flygplatser.md och docs/utreding_faa_tableau.md.", ""),
    ],
    148: [
        ("(id 22)", "for 2012 ars Beechcraft-incident"),
        (" Se docs/utreding_status_flygplatser.md och docs/utreding_faa_tableau.md.", ""),
    ],
    149: [
        ("2016 anvant som install_year", "2016 anvant som installationsar"),
        (" Se docs/utreding_faa_tableau.md.", ""),
    ],
}


def _apply_fixes(text: str, fixes: list[tuple[str, str]]) -> tuple[str, bool]:
    changed = False
    for old, new in fixes:
        if old in text:
            text = text.replace(old, new)
            changed = True
    return text, changed


def fix_leaks(session: Session) -> tuple[list[int], list[int]]:
    fixed_signals: list[int] = []
    signals = session.scalars(select(Signal).where(Signal.id.in_(SIGNAL_FIXES.keys()))).all()
    for signal in signals:
        if not signal.source_notes:
            continue
        new_text, changed = _apply_fixes(signal.source_notes, SIGNAL_FIXES[signal.id])
        if changed:
            signal.source_notes = new_text
            fixed_signals.append(signal.id)

    fixed_installations: list[int] = []
    installations = session.scalars(
        select(Installation).where(Installation.id.in_(INSTALLATION_FIXES.keys()))
    ).all()
    for installation in installations:
        if not installation.notes:
            continue
        new_text, changed = _apply_fixes(installation.notes, INSTALLATION_FIXES[installation.id])
        if changed:
            installation.notes = new_text
            fixed_installations.append(installation.id)

    session.commit()
    return fixed_signals, fixed_installations


def main() -> None:
    with SessionLocal() as session:
        fixed_signals, fixed_installations = fix_leaks(session)
    if fixed_signals or fixed_installations:
        print(f"Fixed source_notes for {len(fixed_signals)} signals: {sorted(fixed_signals)}")
        print(f"Fixed notes for {len(fixed_installations)} installations: {sorted(fixed_installations)}")
    else:
        print("Nothing to do - already fixed.")


if __name__ == "__main__":
    main()
