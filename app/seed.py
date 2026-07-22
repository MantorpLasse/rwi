from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.models import Airport, Runway, Signal, Source


def get_or_create_airport(db, **kwargs):
    airport = db.scalar(select(Airport).where(Airport.iata_code == kwargs.get("iata_code")))
    if airport:
        return airport
    airport = Airport(**kwargs)
    db.add(airport)
    db.flush()
    return airport


def add_signal(db, airport, runway, source, **kwargs):
    existing = db.scalar(
        select(Signal).where(
            Signal.airport_id == airport.id,
            Signal.title == kwargs["title"],
        )
    )
    if existing:
        return existing
    signal = Signal(airport=airport, runway=runway, source=source, **kwargs)
    db.add(signal)
    db.flush()
    return signal


def seed():
    Base.metadata.create_all(engine)
    db = SessionLocal()

    try:
        airports = {}

        airport_rows = [
            dict(iata_code="ASE", icao_code="KASE", faa_code="ASE", name="Aspen/Pitkin County Airport",
                 city="Aspen", state_region="Colorado", country="USA", website_url="https://www.aspenairport.com"),
            dict(iata_code="MHT", icao_code="KMHT", faa_code="MHT", name="Manchester-Boston Regional Airport",
                 city="Manchester", state_region="New Hampshire", country="USA", website_url="https://www.flymanchester.com"),
            dict(iata_code="BOS", icao_code="KBOS", faa_code="BOS", name="Boston Logan International Airport",
                 city="Boston", state_region="Massachusetts", country="USA"),
            dict(iata_code="SFO", icao_code="KSFO", faa_code="SFO", name="San Francisco International Airport",
                 city="San Francisco", state_region="California", country="USA"),
            dict(iata_code="FTY", icao_code="KFTY", faa_code="FTY", name="Fulton County Executive Airport",
                 city="Atlanta", state_region="Georgia", country="USA"),
            dict(iata_code="BGM", icao_code="KBGM", faa_code="BGM", name="Greater Binghamton Airport",
                 city="Binghamton", state_region="New York", country="USA"),
            dict(iata_code="CRQ", icao_code="KCRQ", faa_code="CRQ", name="McClellan-Palomar Airport",
                 city="Carlsbad", state_region="California", country="USA"),
            dict(iata_code="STP", icao_code="KSTP", faa_code="STP", name="St. Paul Downtown Airport",
                 city="St. Paul", state_region="Minnesota", country="USA"),
            dict(iata_code="JFK", icao_code="KJFK", faa_code="JFK", name="John F. Kennedy International Airport",
                 city="New York", state_region="New York", country="USA"),
            dict(iata_code="HYA", icao_code="KHYA", faa_code="HYA", name="Cape Cod Gateway Airport",
                 city="Hyannis", state_region="Massachusetts", country="USA"),
            dict(iata_code="MKC", icao_code="KMKC", faa_code="MKC", name="Charles B. Wheeler Downtown Airport",
                 city="Kansas City", state_region="Missouri", country="USA"),
            dict(iata_code="MDW", icao_code="KMDW", faa_code="MDW", name="Chicago Midway International Airport",
                 city="Chicago", state_region="Illinois", country="USA"),
            dict(iata_code="TEB", icao_code="KTEB", faa_code="TEB", name="Teterboro Airport",
                 city="Teterboro", state_region="New Jersey", country="USA"),
        ]

        for row in airport_rows:
            airports[row["iata_code"]] = get_or_create_airport(db, **row)

        runway_specs = {
            "ASE": ("15/33", 2440, 30),
            "MHT": ("6/24", 2926, 46),
            "BOS": ("9/27", 2134, 46),
            "SFO": ("1R/19L", 2637, 61),
            "FTY": ("8/26", 1768, 30),
            "BGM": ("16/34", 2226, 46),
            "CRQ": ("6/24", 1493, 46),
            "STP": ("14/32", 1981, 46),
            "JFK": ("4R/22L", 2560, 61),
            "HYA": ("6/24", 1658, 46),
            "MKC": ("1/19", 2073, 46),
            "MDW": ("13C/31C", 1988, 46),
            "TEB": ("6/24", 1833, 46),
        }

        runways = {}
        for code, (designation, length_m, width_m) in runway_specs.items():
            runway = db.scalar(
                select(Runway).where(
                    Runway.airport_id == airports[code].id,
                    Runway.designation == designation,
                )
            )
            if not runway:
                runway = Runway(
                    airport=airports[code],
                    designation=designation,
                    length_m=length_m,
                    width_m=width_m,
                    surface="Asphalt/Concrete",
                )
                db.add(runway)
                db.flush()
            runways[code] = runway

        signal_rows = [
            dict(code="ASE", title="Runway 15/33 future EMAS at both runway ends", category="new_installation",
                 status="alp", confidence="planned", planning_year=2027, procurement_year=None,
                 estimated_total_value_usd=None, estimated_emas_value_usd=None, probability_score=8.5,
                 likely_supplier="Runway Safe", supplier_reason="EMAS shown at both runway ends in adopted planning map; supplier not awarded.",
                 notes="Two EMAS beds are shown in Aspen's adopted future airport layout concept.",
                 source_title="Resolution 025-2024 – Amended Common Ground Recommendation Airport Map",
                 source_url="http://www.aspenairport.com/wp-content/uploads/2024/07/bocc.res_.025.2024-Amending-Res-105-2020.pdf",
                 source_type="ALP", publisher="Pitkin County", source_date=date(2024, 5, 16)),
            dict(code="MHT", title="Runway 6 departure-end EMAS replacement", category="replacement",
                 status="procurement", confidence="confirmed", planning_year=2026, procurement_year=2026,
                 estimated_total_value_usd=Decimal("2655000"), estimated_emas_value_usd=Decimal("660000"),
                 probability_score=10.0, likely_supplier="Runway Safe",
                 supplier_reason="Project procurement explicitly includes removal and installation of EMAS.",
                 notes="Replacement of existing EMAS, pavement and related airfield work.",
                 source_title="Runway 6 Departure End EMAS Project", source_url="https://www.flymanchester.com/",
                 source_type="Procurement", publisher="Manchester-Boston Regional Airport", source_date=date(2026, 6, 1)),
            dict(code="BOS", title="Runway 9/27 RSA and EMAS phase 2", category="new_installation",
                 status="construction", confidence="confirmed", planning_year=2026, procurement_year=2025,
                 estimated_total_value_usd=None, estimated_emas_value_usd=None, probability_score=10.0,
                 likely_supplier="Runway Safe", supplier_reason="FAA construction program identifies EMAS phase 2.",
                 notes="Continuation of runway safety area and EMAS construction.",
                 source_title="FAA Airport Construction Impact Report", source_url="https://www.faa.gov/",
                 source_type="FAA", publisher="FAA", source_date=date(2026, 7, 1)),
            dict(code="SFO", title="Runway 1R/19L EMAS seam replacement", category="maintenance",
                 status="construction", confidence="confirmed", planning_year=2026, procurement_year=2026,
                 estimated_total_value_usd=None, estimated_emas_value_usd=None, probability_score=10.0,
                 likely_supplier=None, supplier_reason=None,
                 notes="Seam replacement and maintenance in existing EMAS system.",
                 source_title="FAA Airport Construction Impact Report", source_url="https://www.faa.gov/",
                 source_type="FAA", publisher="FAA", source_date=date(2026, 7, 1)),
            dict(code="FTY", title="Runway 8/26 EMAS safety improvements", category="new_installation",
                 status="design", confidence="programmed", planning_year=2026, procurement_year=2026,
                 estimated_total_value_usd=Decimal("13400000"), estimated_emas_value_usd=None, probability_score=9.0,
                 likely_supplier="Runway Safe", supplier_reason="Official county safety project identifies EMAS at both runway ends.",
                 notes="Runway safety improvements with planned EMAS at both ends.",
                 source_title="Runway 8/26 Runway Safety Improvements Project", source_url="https://www.fultoncountyga.gov/",
                 source_type="Airport", publisher="Fulton County", source_date=date(2026, 5, 11)),
            dict(code="BGM", title="Runway 16 departure EMAS project", category="new_installation",
                 status="funded", confidence="programmed", planning_year=2026, procurement_year=None,
                 estimated_total_value_usd=None, estimated_emas_value_usd=None, probability_score=8.5,
                 likely_supplier="Runway Safe", supplier_reason="County capital plan includes EMAS-related project phases.",
                 notes="Capital program includes EMAS work and final flight-check phase.",
                 source_title="2026–2031 Capital Improvements Program", source_url="https://broomecountyny.gov/",
                 source_type="CIP", publisher="Broome County", source_date=date(2026, 1, 1)),
            dict(code="CRQ", title="Runway 24 EMAS improvement", category="new_installation",
                 status="cip", confidence="programmed", planning_year=2027, procurement_year=None,
                 estimated_total_value_usd=Decimal("25000000"), estimated_emas_value_usd=None, probability_score=8.0,
                 likely_supplier="Runway Safe", supplier_reason="Caltrans CIP identifies EMAS project; not yet awarded.",
                 notes="Programmed EMAS project for Runway 24.",
                 source_title="California Aeronautics Capital Improvement Plan", source_url="https://dot.ca.gov/",
                 source_type="CIP", publisher="Caltrans", source_date=date(2025, 6, 1)),
            dict(code="STP", title="Runway 14/32 EMAS replacement at both ends", category="replacement",
                 status="cip", confidence="programmed", planning_year=2027, procurement_year=None,
                 estimated_total_value_usd=Decimal("20000000"), estimated_emas_value_usd=None, probability_score=8.0,
                 likely_supplier="Runway Safe", supplier_reason="Airport capital plan identifies replacement at both runway ends.",
                 notes="Planned replacement of two existing EMAS beds.",
                 source_title="Metropolitan Airports Commission Capital Improvement Program", source_url="https://www.metroairports.org/",
                 source_type="CIP", publisher="Metropolitan Airports Commission", source_date=date(2025, 12, 1)),
            dict(code="JFK", title="Runway 22L departure-end EMAS replacement", category="replacement",
                 status="design", confidence="programmed", planning_year=2028, procurement_year=None,
                 estimated_total_value_usd=None, estimated_emas_value_usd=Decimal("2500000"), probability_score=8.0,
                 likely_supplier="Runway Safe", supplier_reason="Port Authority approved planning and preliminary design.",
                 notes="Replacement planning for EMAS installed in 2007.",
                 source_title="Port Authority Board Agenda – EMAS planning authorization", source_url="https://www.panynj.gov/",
                 source_type="Authority", publisher="Port Authority of New York and New Jersey", source_date=date(2026, 3, 19)),
            dict(code="HYA", title="EMAS reconstruction", category="replacement",
                 status="environmental_review", confidence="planned", planning_year=2027, procurement_year=None,
                 estimated_total_value_usd=None, estimated_emas_value_usd=None, probability_score=7.0,
                 likely_supplier="Runway Safe", supplier_reason="Environmental and master-planning documents identify reconstruction.",
                 notes="Future EMAS reconstruction described in environmental documentation.",
                 source_title="Final Environmental Assessment", source_url="https://flyhya.com/",
                 source_type="Environmental", publisher="Cape Cod Gateway Airport", source_date=date(2025, 11, 4)),
            dict(code="MKC", title="Existing EMAS lifecycle and replacement study", category="study",
                 status="master_plan", confidence="planned", planning_year=2026, procurement_year=None,
                 estimated_total_value_usd=None, estimated_emas_value_usd=None, probability_score=6.5,
                 likely_supplier="Runway Safe", supplier_reason="Master plan evaluates condition and replacement timing.",
                 notes="Lifecycle study for repair or replacement of existing EMAS.",
                 source_title="MKC Airport Master Plan – Existing Conditions", source_url="https://mkc.airportstudy.net/",
                 source_type="Master Plan", publisher="Kansas City Aviation Department", source_date=date(2026, 1, 7)),
            dict(code="MDW", title="Future EMAS lifecycle watch", category="replacement_watch",
                 status="identified", confidence="speculative", planning_year=None, procurement_year=None,
                 estimated_total_value_usd=None, estimated_emas_value_usd=None, probability_score=4.5,
                 likely_supplier="Runway Safe", supplier_reason="Existing EMAS airport with constrained runway environment; no current confirmed project.",
                 notes="Watch item only. No verified current procurement or capital project.",
                 source_title="Internal watch item", source_url="https://www.flychicago.com/midway/",
                 source_type="Watchlist", publisher="Runway Safe Intelligence", source_date=date(2026, 7, 17)),
        ]

        for row in signal_rows:
            code = row.pop("code")
            source_title = row.pop("source_title")
            source_url = row.pop("source_url")
            source_type = row.pop("source_type")
            publisher = row.pop("publisher")
            source_date = row.pop("source_date")

            source = db.scalar(select(Source).where(Source.url == source_url))
            if not source:
                source = Source(
                    title=source_title,
                    source_type=source_type,
                    publisher=publisher,
                    url=source_url,
                    published_date=source_date,
                    retrieved_at=date(2026, 7, 17),
                    reliability_level="official" if source_type != "Watchlist" else "internal",
                )
                db.add(source)
                db.flush()

            add_signal(db, airports[code], runways[code], source, **row)

        db.commit()
        print("Seed complete.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
