"""Backfill iata_code/icao_code for Airport rows that already have a
faa_code but no iata_code/icao_code.

scripts/import_usaspending_grants.py's resolve_airport() only ever sets
faa_code when creating a new Airport (see its Loc ID and beneficiary
branches) - never iata_code/icao_code, even for major hub airports whose
FAA LID literally is their public IATA code (ORD, EWR, LGA, PHL, MEM, OAK,
SAN, DCA, CLT, MSP, BUR, FLL, ...).

Only backfills airports on the VERIFIED_CODES allowlist below - each entry
was checked by hand against the airport's real-world public IATA/ICAO
identifiers, not derived by blindly assuming faa_code == iata_code for
every row. One row in this database is deliberately left off:

- Small GA fields (e.g. Cartersville/VPC, John Tune/JWN) whose FAA LID may
  never have been assigned a matching IATA code - not confident enough to
  assert either way.

"President Donald J. Trump International" (faa_code=DJT, West Palm Beach)
is Palm Beach International renamed - the same airport already tracked
elsewhere in this project via its incident-import data. IATA/ICAO codes
don't change on a rename (only the official name and, here, the FAA LID
did), so this one keeps its real iata_code/icao_code (PBI/KPBI) even
though faa_code no longer matches them - the one exception to the
"faa_code == iata_code" pattern every other entry below follows.

Alaska airports get the "PA" ICAO prefix (not "K") per the real FAA/ICAO
convention for that state; every other row here is in the contiguous US,
which uses "K" + the 3-letter code.

Safe to re-run: only touches rows where iata_code is still NULL.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Airport

# faa_code -> (iata_code, icao_code)
VERIFIED_CODES: dict[str, tuple[str, str]] = {
    "ADQ": ("ADQ", "PADQ"),  # Kodiak, AK
    "CDV": ("CDV", "PACV"),  # Cordova (Merle K Mudhole Smith), AK
    "OME": ("OME", "PAOM"),  # Nome, AK
    "LIT": ("LIT", "KLIT"),  # Bill and Hillary Clinton National, Little Rock, AR
    "ACV": ("ACV", "KACV"),  # Arcata-Eureka, CA
    "BUR": ("BUR", "KBUR"),  # Bob Hope / Hollywood Burbank, CA
    "MRY": ("MRY", "KMRY"),  # Monterey, CA
    "OAK": ("OAK", "KOAK"),  # Oakland International, CA
    "SAN": ("SAN", "KSAN"),  # San Diego International, CA
    "SBP": ("SBP", "KSBP"),  # San Luis Obispo County, CA
    "TEX": ("TEX", "KTEX"),  # Telluride Regional, CO
    "GON": ("GON", "KGON"),  # Groton-New London, CT
    "BDR": ("BDR", "KBDR"),  # Sikorsky Memorial, Bridgeport, CT
    "OXC": ("OXC", "KOXC"),  # Waterbury-Oxford, CT
    "ILG": ("ILG", "KILG"),  # New Castle County, Wilmington, DE
    "BCT": ("BCT", "KBCT"),  # Boca Raton, FL
    "FLL": ("FLL", "KFLL"),  # Fort Lauderdale-Hollywood International, FL
    "EYW": ("EYW", "KEYW"),  # Key West International, FL
    "SUA": ("SUA", "KSUA"),  # Martin County / Witham Field, Stuart, FL
    "VNC": ("VNC", "KVNC"),  # Venice Municipal, FL
    "PDK": ("PDK", "KPDK"),  # DeKalb-Peachtree, Atlanta, GA
    "PWK": ("PWK", "KPWK"),  # Chicago Executive, Wheeling, IL
    "ORD": ("ORD", "KORD"),  # Chicago O'Hare International, IL
    "LEX": ("LEX", "KLEX"),  # Blue Grass, Lexington, KY
    "SDF": ("SDF", "KSDF"),  # Louisville Muhammad Ali Intl (fka Standiford Field), KY
    "BTR": ("BTR", "KBTR"),  # Baton Rouge Metropolitan, LA
    "LFT": ("LFT", "KLFT"),  # Lafayette Regional, LA
    "AUG": ("AUG", "KAUG"),  # Augusta State, ME
    "ORH": ("ORH", "KORH"),  # Worcester Regional, MA
    "MSP": ("MSP", "KMSP"),  # Minneapolis-St Paul International, MN
    "EWR": ("EWR", "KEWR"),  # Newark Liberty International, NJ
    "TTN": ("TTN", "KTTN"),  # Trenton-Mercer, NJ
    "MMU": ("MMU", "KMMU"),  # Morristown Municipal, NJ
    "ELM": ("ELM", "KELM"),  # Elmira-Corning Regional, NY
    "ROC": ("ROC", "KROC"),  # Greater Rochester International, NY
    "LGA": ("LGA", "KLGA"),  # LaGuardia, NY
    "FRG": ("FRG", "KFRG"),  # Republic Airport, Farmingdale, NY
    "CLT": ("CLT", "KCLT"),  # Charlotte Douglas International, NC
    "EWN": ("EWN", "KEWN"),  # Coastal Carolina Regional, New Bern, NC
    "INT": ("INT", "KINT"),  # Smith Reynolds, Winston-Salem, NC
    "BKL": ("BKL", "KBKL"),  # Burke Lakefront, Cleveland, OH
    "CLE": ("CLE", "KCLE"),  # Cleveland-Hopkins International, OH
    "CGF": ("CGF", "KCGF"),  # Cuyahoga County, OH
    "ABE": ("ABE", "KABE"),  # Lehigh Valley International, Allentown, PA
    "PHL": ("PHL", "KPHL"),  # Philadelphia International, PA
    "RDG": ("RDG", "KRDG"),  # Reading Regional, PA
    "AVP": ("AVP", "KAVP"),  # Wilkes-Barre/Scranton International, PA
    "PVD": ("PVD", "KPVD"),  # Rhode Island T.F. Green International, RI
    "GMU": ("GMU", "KGMU"),  # Greenville Downtown, SC
    "HXD": ("HXD", "KHXD"),  # Hilton Head, SC
    "MEM": ("MEM", "KMEM"),  # Memphis International, TN
    "ADS": ("ADS", "KADS"),  # Addison, Dallas, TX
    "LRD": ("LRD", "KLRD"),  # Laredo International, TX
    "MFE": ("MFE", "KMFE"),  # McAllen Miller International, TX
    "RUT": ("RUT", "KRUT"),  # Rutland - Southern Vermont Regional, VT
    "DCA": ("DCA", "KDCA"),  # Ronald Reagan Washington National, VA
    "ROA": ("ROA", "KROA"),  # Roanoke-Blacksburg Regional, VA
    "CRW": ("CRW", "KCRW"),  # Charleston Yeager, WV
    "DJT": ("PBI", "KPBI"),  # Palm Beach International, renamed; FAA LID changed, IATA/ICAO didn't
}


def backfill(session: Session) -> dict:
    stats = {"updated": 0, "skipped_not_verified": []}
    candidates = session.scalars(
        select(Airport).where(Airport.iata_code.is_(None), Airport.faa_code.is_not(None))
    ).all()

    for airport in candidates:
        codes = VERIFIED_CODES.get(airport.faa_code)
        if codes is None:
            stats["skipped_not_verified"].append(f"{airport.faa_code} ({airport.name})")
            continue
        airport.iata_code, airport.icao_code = codes
        stats["updated"] += 1

    session.commit()
    return stats


def main() -> None:
    with SessionLocal() as session:
        stats = backfill(session)

    print(f"Backfilled iata_code/icao_code: {stats['updated']}")
    skipped = sorted(stats["skipped_not_verified"])
    if skipped:
        print(f"Not on the verified list, skipped ({len(skipped)}):")
        for entry in skipped:
            print(f"  {entry}")


if __name__ == "__main__":
    main()
