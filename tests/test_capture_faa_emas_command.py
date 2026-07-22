from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.acquisition.faa_tableau import TableauSessionError
from app.database import Base
from app.models import AcquisitionRunStatus, AcquisitionSource, PublishingSource
from app.scripts.capture_faa_emas import run_capture


def factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


class Provider:
    source_url = "unused"

    def __init__(self, **kwargs):
        self.kwargs = kwargs


class CountingService:
    calls = 0

    def __init__(self, session, provider):
        self.session = session
        self.provider = provider

    def acquire(self, source):
        type(self).calls += 1
        snapshot = SimpleNamespace(
            id=7,
            sha256="a" * 64,
            byte_size=42,
            media_type="text/plain",
            retrieved_at=datetime(2026, 7, 20, tzinfo=UTC),
        )
        return SimpleNamespace(
            status=AcquisitionRunStatus.SUCCESS,
            snapshot=snapshot,
            request_url="https://dot.example/bootstrapSession/sessions/secret-session",
            final_url="https://dot.example/bootstrapSession/sessions/secret-session",
        )


def test_live_network_approval_is_required(capsys):
    CountingService.calls = 0
    result = run_capture([], session_factory=factory(), provider_factory=Provider)
    assert result == 2
    assert "--allow-live-network is required" in capsys.readouterr().err
    assert CountingService.calls == 0


def test_database_write_approval_is_required(capsys):
    CountingService.calls = 0
    result = run_capture(
        ["--allow-live-network"],
        session_factory=factory(),
        provider_factory=Provider,
    )
    assert result == 2
    assert "--allow-database-write is required" in capsys.readouterr().err
    assert CountingService.calls == 0


def test_target_is_displayed_before_refusal(capsys):
    result = run_capture([], session_factory=factory(), database_url="sqlite:///explicit.db")
    assert result == 2
    assert "Database target: sqlite:///explicit.db" in capsys.readouterr().out


def test_success_calls_service_once_resolves_sources_and_prints_safe_summary(capsys):
    CountingService.calls = 0
    sessions = factory()
    result = run_capture(
        ["--allow-live-network", "--allow-database-write"],
        session_factory=sessions,
        provider_factory=Provider,
        service_factory=CountingService,
        database_url="sqlite:///:memory:",
    )
    assert result == 0
    assert CountingService.calls == 1
    output = capsys.readouterr().out
    assert "Run status: success" in output
    assert "Snapshot ID: 7" in output
    assert "/sessions/[redacted]" in output
    assert "secret-session" not in output
    assert "cookie" not in output.lower()
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(PublishingSource)) == 1
        assert session.scalar(select(func.count()).select_from(AcquisitionSource)) == 1


class FailedService:
    calls = 0

    def __init__(self, session, provider):
        pass

    def acquire(self, source):
        type(self).calls += 1
        raise TableauSessionError("bootstrap failed")


def test_governed_failure_returns_nonzero_and_does_not_print_sensitive_data(capsys):
    FailedService.calls = 0
    result = run_capture(
        ["--allow-live-network", "--allow-database-write"],
        session_factory=factory(),
        provider_factory=Provider,
        service_factory=FailedService,
    )
    assert result == 1
    assert FailedService.calls == 1
    captured = capsys.readouterr()
    assert "tableau_session_error" in captured.err
    assert "cookie" not in (captured.out + captured.err).lower()


def test_diagnostic_mode_passes_explicit_local_directory():
    received = []

    def provider_factory(**kwargs):
        received.append(kwargs)
        return Provider(**kwargs)

    CountingService.calls = 0
    result = run_capture(
        [
            "--allow-live-network",
            "--allow-database-write",
            "--capture-diagnostic-html",
        ],
        session_factory=factory(),
        provider_factory=provider_factory,
        service_factory=CountingService,
    )
    assert result == 0
    assert CountingService.calls == 1
    assert str(received[0]["diagnostic_directory"]).replace("\\", "/") == (
        "data/diagnostics/faa_tableau"
    )
