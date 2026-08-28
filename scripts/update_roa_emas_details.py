"""Governed, narrowly-scoped enrichment of Roanoke-Blacksburg Regional
Airport's (ROA) historical EMAS Installation record with independently
verified evidence that the system at Runway 16/34, end 34, was completely
replaced in 2024.

Reuses the exact existing one-off enrichment pattern
scripts/update_lex_emas_details.py and scripts/update_cgh_emas_details.py
already established (Installation.source_id repointed at the strongest new
evidence, old source row left in place - not deleted, just unlinked -
existing notes preserved and appended to via scripts.annotate_signal's own
append_note()) - this script introduces no new evidence model. It adds the
dry-run/--allow-database-write/backup/precondition/atomicity discipline this
mission's own safety requirements demand, which none of those four lighter
precedent scripts individually had, without changing what they write or how.

EVIDENCE CHAIN (docs/architecture/rwi-fresh-intelligence-sweep-1-2026-08.md
S3, independently re-verified fresh in this mission - see its own commander-
triage follow-up report for full source-by-source detail):

  - RWI's own pre-existing SourceAssertion 88 (FY2023 USAspending grant,
    $4.0M, published 2023-08-28) and SourceAssertion 98 (FY2024 USAspending
    grant, $359K, published 2024-08-13) already state, in the FAA's own
    grant-purpose text, that an EMAS "RECONSTRUCTS AN EXISTING ENGINEERED
    MATERIAL ARRESTING SYSTEM FOR RUNWAY 16/34, AT THE 34 END, THAT HAS BEEN
    DAMAGED" - confirms WHAT and WHERE, and that federal reconstruction
    funding was authorized across FY2023-FY2024, but NEITHER states a
    completion year on its own.
  - RWI's own pre-existing SourceAssertion 216 (FAA NASR CSV, effective
    2026-08-06) confirms an EMAS currently exists at ROA Runway 16/34 end 34
    - current STATE, not a year.
  - The completion YEAR itself is established by two independently-fetched,
    external sources (this mission, live network): WSLS ("How a Safety
    System Stops a Runaway United Plane at Roanoke Airport", published
    2025-09-25/26, journalist Omose Ighodaro), quoting airport spokesperson
    Alexa Briehl by name: "this new EMAS was installed in spring 2024" - "a
    $12 million investment" - "runway 16-34"; and Branch Group's own EMAS
    project page (branchgroup.com/emas-roanoke-blacksburg-airport/,
    referencing the Sept 24 2025 incident and its own ENR "Best Regional
    Project Award of Merit"), independently corroborated by the ENR article's
    own title, "Roanoke-Blacksburg Airport Runway 16-34 EMAS Replacement"
    (direct fetch blocked, HTTP 403 - cited by URL/title only, not treated as
    a verified full-text source), and a WebSearch summary of that same ENR
    article stating 4,708 Runway Safe EMAS blocks, installed April-May 2024,
    "completed at budget and ahead of schedule."

INSTALLATION IDENTITY (resolved fresh, not assumed): ROA has exactly two
Installation rows. Installation 69 (source_id=12, the generic bulk FAA
Tableau map) carries the correct current runway/runway_end linkage
(runway_id=41, runway_end='34') matching the live 2026-08-06 FAA NASR feed,
but no install_year or replacement_year at all. Installation 104
(source_id=57, a dated 2016 FAA Fact Sheet PDF) carries install_year=2004 but
no runway_id. FAA's own live NASR feed shows exactly ONE current EMAS record
for ROA - both rows almost certainly describe the SAME one physical bed via
two never-reconciled historical import passes, not two distinct physical
installations. This script deliberately enriches ONLY Installation 104 (the
row that already carries the historical install_year, whose own pre-existing
note already anticipated exactly this replacement_year value) and does NOT
touch, merge, or delete Installation 69 - no governed Installation-row-merge
mechanism exists in this repository, and inventing one is out of this
script's narrow scope. A future, separately-authorized mission should decide
whether/how to reconcile the two rows.

SAFETY MODEL: dry-run by default; a real write requires BOTH --apply and
--allow-database-write. Deterministic target selection is airport-code- and
structural-shape-anchored (ROA + install_year==2004 + runway_id IS NULL),
never a bare hardcoded row id alone - a mismatched --database (e.g. a
fixture where ids differ) is caught as a precondition failure. Fails closed
if ROA does not have exactly two installations or the target's current
values differ at all from the expected-before snapshot captured during this
mission's own investigation. A timestamped backup is taken before any real
write. The whole operation runs in one transaction; PRAGMA foreign_key_check
is verified clean immediately before commit. Safe to re-run: the two new
Source rows are looked up by URL before creating a duplicate, and the
note-append is guarded by checking whether this run's marker text already
appears in the installation's notes.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import create_engine, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Airport, Installation, Source
from scripts.add_brazil_expansion import get_or_create_source
from scripts.annotate_signal import append_note

DEFAULT_DATABASE = Path("data/runway_safe.db")
BACKUP_DIRECTORY = Path("data/backups")

EXPECTED_AIRPORT_CODE = "ROA"
REPLACEMENT_YEAR = 2024
CONFIRMED_VENDOR = "Runway Safe"

WSLS_URL = "https://www.wsls.com/news/local/2025/09/26/how-a-safety-system-stops-a-runaway-united-plane-at-roanoke-airport/"
BRANCH_URL = "https://www.branchgroup.com/emas-roanoke-blacksburg-airport/"
ENR_URL = "https://www.enr.com/articles/61847-award-of-merit-airport-transit-roanoke-blacksburg-airport-runway-16-34-emas-replacement"

NOTE_MARKER = "2024 replacement confirmed"

# The exact pre-write state this script was written against - re-verified
# fresh in this mission's own investigation. Any live deviation aborts
# before any write; this is never silently relaxed to "close enough."
# source_type (not a bare literal source_id) is the anchor for the old
# evidentiary source, matching the airport-code/structural-shape-anchored
# convention this script's own target selection already uses - a raw id
# would also make this precondition untestable against an isolated fixture
# database, whose autoincrement ids never match production's.
EXPECTED_BEFORE = {
    "source_type": "faa_fact_sheet",
    "install_year": 2004,
    "replacement_year": None,
    "runway_id": None,
}


class UnexpectedStateError(RuntimeError):
    """Raised whenever the live database does not exactly match this
    script's own recorded expected-before snapshot - fails closed rather
    than guessing or proceeding against unverified state."""


@dataclass(frozen=True)
class EnrichmentResult:
    installation_id: int
    updated: bool
    before: dict
    after: dict
    new_source_ids: "list[int]"


def _backup_name() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"runway_safe-pre-roa-2024-replacement-apply-{timestamp}.db"


def backup_database(database: Path, backup_directory: Path = BACKUP_DIRECTORY) -> Path:
    database = database.resolve()
    if not database.is_file():
        raise FileNotFoundError(f"Database not found: {database}")
    backup_directory.mkdir(parents=True, exist_ok=True)
    destination = backup_directory / _backup_name()
    shutil.copy2(database, destination)
    if destination.stat().st_size != database.stat().st_size:
        raise RuntimeError("Database backup size does not match the source database.")
    return destination


def _find_target_installation(session: Session) -> Installation:
    """Airport-code- and structural-shape-anchored, never a bare id alone.
    Purely structural (which row is "the historical ROA EMAS record") -
    never asserts its field VALUES are still exactly what this script
    expects; that is a separate, later check (_assert_expected_before),
    deliberately not run here so this function alone stays valid to call
    both before AND after a successful apply (idempotent re-run, and
    plan()'s own post-apply preview both depend on that)."""
    airport = session.scalar(
        select(Airport).where(
            or_(
                Airport.iata_code == EXPECTED_AIRPORT_CODE,
                Airport.icao_code == "K" + EXPECTED_AIRPORT_CODE,
                Airport.faa_code == EXPECTED_AIRPORT_CODE,
            )
        )
    )
    if airport is None:
        raise UnexpectedStateError(f"No airport found for code={EXPECTED_AIRPORT_CODE!r}.")

    installations = session.scalars(
        select(Installation).where(Installation.airport_id == airport.id)
    ).all()
    if len(installations) != 2:
        raise UnexpectedStateError(
            f"Expected exactly 2 Installation rows for {EXPECTED_AIRPORT_CODE}, found {len(installations)}: "
            f"{[i.id for i in installations]!r}. Refusing to guess which one to enrich."
        )

    candidates = [i for i in installations if i.install_year == 2004 and i.runway_id is None]
    if len(candidates) != 1:
        raise UnexpectedStateError(
            f"Expected exactly 1 installation with install_year=2004 and runway_id IS NULL for "
            f"{EXPECTED_AIRPORT_CODE}, found {len(candidates)}: {[i.id for i in candidates]!r}."
        )
    return candidates[0]


def _assert_expected_before(target: Installation) -> None:
    """The state precondition proper - checked only immediately before an
    actual, real (not-yet-applied) mutation. Deliberately NOT part of
    _find_target_installation() (see its own docstring): calling this after
    a successful apply would always fail, since source_type/replacement_year
    have legitimately changed - the idempotency short-circuit in apply()
    must run, and win, before this is ever reached on a second call."""
    actual_before = {
        "source_type": target.source.source_type if target.source is not None else None,
        "install_year": target.install_year,
        "replacement_year": target.replacement_year,
        "runway_id": target.runway_id,
    }
    if actual_before != EXPECTED_BEFORE:
        raise UnexpectedStateError(
            f"Installation {target.id}'s current state {actual_before!r} does not match this script's own "
            f"expected-before snapshot {EXPECTED_BEFORE!r}. Refusing to write against unverified state."
        )


def plan(session: Session) -> EnrichmentResult:
    """Read-only: resolves the target and reports what WOULD change, without
    adding, flushing, or committing anything."""
    target = _find_target_installation(session)
    before = {
        "source_id": target.source_id,
        "install_year": target.install_year,
        "replacement_year": target.replacement_year,
        "confirmed_vendor": target.confirmed_vendor,
        "notes": target.notes,
    }
    after = dict(before)
    after["replacement_year"] = REPLACEMENT_YEAR
    after["confirmed_vendor"] = CONFIRMED_VENDOR
    after["source_id"] = "<new WSLS Source id, to be created>"
    already_applied = NOTE_MARKER in (target.notes or "")
    return EnrichmentResult(
        installation_id=target.id, updated=not already_applied, before=before, after=after, new_source_ids=[],
    )


def apply(session: Session, *, today: "date | None" = None) -> EnrichmentResult:
    today = today or date.today()
    target = _find_target_installation(session)
    before = {
        "source_id": target.source_id,
        "install_year": target.install_year,
        "replacement_year": target.replacement_year,
        "confirmed_vendor": target.confirmed_vendor,
        "notes": target.notes,
    }

    if NOTE_MARKER in (target.notes or ""):
        # Idempotent: a second run recognizes the enrichment already
        # happened and changes nothing further. Checked BEFORE the state
        # precondition below - a second call's state (source_type=news,
        # replacement_year=2024) never matches EXPECTED_BEFORE by design,
        # and must short-circuit here rather than ever reach that check.
        return EnrichmentResult(
            installation_id=target.id, updated=False, before=before, after=before, new_source_ids=[],
        )

    _assert_expected_before(target)

    new_source_ids: "list[int]" = []

    wsls_source = get_or_create_source(
        session,
        url=WSLS_URL,
        title="How a Safety System Stops a Runaway United Plane at Roanoke Airport",
        source_type="news",
        publisher="WSLS 10 (Roanoke, VA)",
        published_date=date(2025, 9, 25),
        summary=(
            "WSLS-artikel (Omose Ighodaro, 2025-09-25/26), citerar flygplatsens egen talesperson Alexa Briehl "
            "direkt: 'this new EMAS was installed in spring 2024', ett $12 miljoner-projekt finansierat via "
            "federala, delstatliga, lokala medel samt passenger facility charge-medel, bana 16-34. Beskriver "
            "ocksa den 24 september 2025-incidenten dar systemet arresterade ett United Express-plan (Embraer "
            "EMB-145XR) - forsta gangen systemet nagonsin anvants."
        ),
    )
    if wsls_source.id not in new_source_ids and before["source_id"] != wsls_source.id:
        new_source_ids.append(wsls_source.id)

    branch_source = get_or_create_source(
        session,
        url=BRANCH_URL,
        title="Branch EMAS Stops Aircraft at Roanoke Airport, Wins ENR Award",
        source_type="news",
        publisher="Branch Group",
        published_date=date(2025, 9, 26),
        summary=(
            "Entreprenoren Branch Builds (Branch Group) egen projektsida: byggde/koordinerade installationen av "
            "4 708 Runway Safe EMAS-block, 'completed at budget and ahead of schedule', tilldelades Engineering "
            "News-Record (ENR) 'Best Regional Project Award of Merit'. Oberoende bekraftelse av ENR:s egen "
            f"artikeltitel 'Roanoke-Blacksburg Airport Runway 16-34 EMAS Replacement' ({ENR_URL} - direkthamtning "
            "blockerad, HTTP 403; citerad via URL/titel, aldrig som fullt verifierad brodtext)."
        ),
    )
    new_source_ids.append(branch_source.id)

    target.source_id = wsls_source.id
    target.replacement_year = REPLACEMENT_YEAR
    target.confirmed_vendor = CONFIRMED_VENDOR

    note = (
        f"[{NOTE_MARKER}] Fullstandigt ersatt varen 2024 (april-maj), $12M, finansierat via federala/delstatliga/"
        "lokala medel + passenger facility charge. Leverantor: Runway Safe (4 708 EMASMAX-block, via "
        "entreprenoren Branch Builds/Branch Group). Bekraftar och preciserar den egna tidigare noteringen nedan. "
        f"Oberoende kallor: WSLS ({WSLS_URL}), Branch Group ({BRANCH_URL}), ENR-artikeltitel ({ENR_URL}). "
        "RWI:s egna redan befintliga SourceAssertion 88 (FY2023-bidrag) och 98 (FY2024-bidrag) bekraftar redan "
        "syfte/plats (Runway 16/34, 34-anden) men anger inte sjalva ett slutfort-ar - det faststalls har av de "
        "externa kallorna ovan, inte av bidragstexterna ensamma."
    )
    target.notes = append_note(target.notes, note, on=today)

    violations = session.connection().exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"apply() would leave foreign-key violations: {violations}")

    session.commit()
    session.refresh(target)

    after = {
        "source_id": target.source_id,
        "install_year": target.install_year,
        "replacement_year": target.replacement_year,
        "confirmed_vendor": target.confirmed_vendor,
        "notes": target.notes,
    }
    return EnrichmentResult(
        installation_id=target.id, updated=True, before=before, after=after, new_source_ids=new_source_ids,
    )


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--apply", action="store_true", help="Perform the real write (still requires --allow-database-write).")
    parser.add_argument("--allow-database-write", action="store_true")
    parser.add_argument("--skip-backup", action="store_true", help="isolated/temp DBs only")
    args = parser.parse_args(argv)

    if args.apply and not args.allow_database_write:
        parser.error("--apply requires --allow-database-write")
    if args.allow_database_write and not args.apply:
        parser.error("--allow-database-write requires --apply")

    engine = create_engine(f"sqlite:///{args.database.resolve()}", connect_args={"check_same_thread": False}, future=True)

    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SessionFactory = sessionmaker(bind=engine)

    if not args.apply:
        with SessionFactory() as session:
            result = plan(session)
        print("DRY RUN (no write performed) - pass --apply --allow-database-write to write for real.")
        print(f"Installation {result.installation_id}: would_update={result.updated}")
        print(f"  before: {result.before}")
        print(f"  after:  {result.after}")
        return 0

    if not args.skip_backup:
        backup = backup_database(args.database)
        print("Backup created:", backup)

    with SessionFactory() as session:
        result = apply(session)

    print(f"Installation {result.installation_id}: updated={result.updated}")
    print(f"  before: {result.before}")
    print(f"  after:  {result.after}")
    print(f"  new_source_ids: {result.new_source_ids}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
