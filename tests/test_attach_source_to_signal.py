from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Airport, Signal, Source
from scripts.attach_source_to_signal import attach_source, parse_args


def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _make_signal(session, **kwargs):
    airport = Airport(name="Fulton County Executive Airport", faa_code="FTY", country="USA")
    signal = Signal(
        airport=airport,
        title="Runway 8/26 EMAS safety improvements",
        category="new_installation",
        confidence="programmed",
        **kwargs,
    )
    session.add(signal)
    session.commit()
    return signal.id


def test_attach_source_creates_source_and_links_signal():
    Session = session_factory()
    with Session() as session:
        signal_id = _make_signal(session)

        signal, previous_source_id = attach_source(
            session,
            signal_id,
            title="Fulton County Master Plan Technical Report (Draft, dec 2022)",
            source_type="master_plan",
            url="https://www.fultoncountyga.gov/-/media/Departments/Public-Works/FTY-Master-Plan-Technical-Report-Draft-121622.pdf",
            publisher="Fulton County",
            published_date=date(2022, 12, 16),
        )

        assert previous_source_id is None
        assert signal.source is not None
        assert signal.source.title == "Fulton County Master Plan Technical Report (Draft, dec 2022)"
        assert signal.source.source_type == "master_plan"
        assert signal.source.publisher == "Fulton County"
        assert signal.source.published_date == date(2022, 12, 16)

        sources = session.scalars(select(Source)).all()
        assert len(sources) == 1


def test_attach_source_replaces_an_existing_link_without_deleting_the_old_source():
    Session = session_factory()
    with Session() as session:
        old_source = Source(title="Old homepage link", source_type="Airport", url="https://old.test")
        session.add(old_source)
        session.flush()
        signal_id = _make_signal(session, source=old_source)

        signal, previous_source_id = attach_source(
            session,
            signal_id,
            title="New primary source",
            source_type="master_plan",
            url="https://new.test/plan.pdf",
        )

        assert previous_source_id == old_source.id
        assert signal.source_id != old_source.id
        assert signal.source.title == "New primary source"
        # The old source row still exists, just unlinked from this signal.
        assert session.get(Source, old_source.id) is not None


def test_attach_source_raises_for_unknown_signal_id():
    Session = session_factory()
    with Session() as session:
        try:
            attach_source(session, 999, title="x", source_type="master_plan", url="https://x.test")
            assert False, "expected SystemExit"
        except SystemExit:
            pass


def test_parse_args_requires_title_source_type_and_url():
    try:
        parse_args(["--signal-id", "1"])
        assert False, "expected SystemExit"
    except SystemExit:
        pass


def test_parse_args_accepts_required_flags():
    args = parse_args(
        [
            "--signal-id", "5",
            "--title", "t",
            "--source-type", "master_plan",
            "--url", "https://x.test",
        ]
    )
    assert args.signal_id == 5
    assert args.title == "t"
    assert args.source_type == "master_plan"
    assert args.url == "https://x.test"
    assert args.publisher is None
    assert args.published_date is None
