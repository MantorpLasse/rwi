"""RWI Mission #11B Part M - offline tests for
app.services.generic_web_fetch (PublishingSource/AcquisitionSource
get-or-create, robots.txt check, and the fetch_discovered_url()
orchestration). Synthetic in-memory SQLite DB, matching this repo's own
established test convention. No real network access."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import AcquisitionRun, AcquisitionRunStatus, AcquisitionSource, PublishingSource, Snapshot
from app.services.generic_web_fetch import (
    RobotsDisallowedError,
    check_robots_txt_allows,
    fetch_discovered_url,
    get_or_create_acquisition_source_for_url,
    get_or_create_publishing_source_for_hostname,
)


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


# --- Fakes (combined .get() + .stream() client) -------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, *, headers: dict | None = None, content: bytes = b"", text: str = "", url: str = "https://example.com/"):
        self.status_code = status_code
        self.headers = httpx.Headers(headers or {})
        self._content = content
        self.text = text
        self.url = httpx.URL(url)

    @property
    def is_redirect(self) -> bool:
        return self.status_code in (301, 302, 303, 307, 308)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=httpx.Request("GET", str(self.url)), response=self)

    def iter_bytes(self):
        chunk = 4096
        for i in range(0, len(self._content), chunk):
            yield self._content[i : i + chunk]

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakeClient:
    def __init__(self, *, robots_response: _FakeResponse | None = None, fetch_responses: list[_FakeResponse] | None = None):
        self._robots_response = robots_response
        self._fetch_responses = list(fetch_responses or [])
        self.get_calls: list[dict] = []
        self.stream_calls: list[dict] = []

    def get(self, url, **kwargs):
        self.get_calls.append({"url": url, "kwargs": kwargs})
        if self._robots_response is None:
            raise httpx.ConnectError("no robots.txt configured for this fake")
        return self._robots_response

    def stream(self, method, url, **kwargs):
        self.stream_calls.append({"method": method, "url": url, "kwargs": kwargs})
        return self._fetch_responses.pop(0)

    def close(self):
        pass


def _allow_all_robots() -> _FakeResponse:
    return _FakeResponse(200, headers={"content-type": "text/plain"}, text="User-agent: *\nAllow: /\n")


def _disallow_all_robots() -> _FakeResponse:
    return _FakeResponse(200, headers={"content-type": "text/plain"}, text="User-agent: *\nDisallow: /\n")


def _not_found_robots() -> _FakeResponse:
    return _FakeResponse(404)


def _html_ok(url: str = "https://example.com/page") -> _FakeResponse:
    return _FakeResponse(200, headers={"content-type": "text/html"}, content=b"<html>hi</html>", url=url)


# --- PublishingSource get-or-create -------------------------------------------


def test_new_hostname_gets_neutral_unverified_publishing_source():
    engine = _engine()
    with Session(engine) as session:
        ps, created = get_or_create_publishing_source_for_hostname(session, "airspacechange.caa.co.uk")
        assert created is True
        assert ps.reliability_level == "unverified"
        assert ps.source_type is None
        assert ps.name == "airspacechange.caa.co.uk"


def test_repeated_hostname_get_or_create_is_idempotent():
    engine = _engine()
    with Session(engine) as session:
        first, created_first = get_or_create_publishing_source_for_hostname(session, "example.com")
        session.commit()
        second, created_second = get_or_create_publishing_source_for_hostname(session, "example.com")
        assert created_first is True
        assert created_second is False
        assert first.id == second.id
        assert session.scalar(select(PublishingSource).where(PublishingSource.name == "example.com")) is not None
        count = len(list(session.scalars(select(PublishingSource))))
        assert count == 1


# --- AcquisitionSource get-or-create -------------------------------------------


def test_acquisition_source_same_url_is_idempotent():
    engine = _engine()
    with Session(engine) as session:
        ps, _ = get_or_create_publishing_source_for_hostname(session, "example.com")
        session.commit()
        first, created_first = get_or_create_acquisition_source_for_url(session, "https://example.com/page?x=1", ps)
        session.commit()
        second, created_second = get_or_create_acquisition_source_for_url(session, "https://example.com/page?x=1", ps)
        assert created_first is True
        assert created_second is False
        assert first.id == second.id
        assert first.canonical_url == "https://example.com/page?x=1"


def test_acquisition_source_key_is_bounded_and_deterministic():
    engine = _engine()
    with Session(engine) as session:
        ps, _ = get_or_create_publishing_source_for_hostname(session, "example.com")
        session.commit()
        long_url = "https://example.com/" + ("a" * 2000)
        with pytest.raises(ValueError):
            get_or_create_acquisition_source_for_url(session, long_url, ps)


# --- robots.txt check -----------------------------------------------------------


def test_robots_allow_all_permits_fetch():
    client = _FakeClient(robots_response=_allow_all_robots())
    assert check_robots_txt_allows("https://example.com/page", user_agent="RunwaySafeIntelligence/1.0", client=client) is True


def test_robots_disallow_all_blocks_fetch():
    client = _FakeClient(robots_response=_disallow_all_robots())
    assert check_robots_txt_allows("https://example.com/page", user_agent="RunwaySafeIntelligence/1.0", client=client) is False


def test_robots_404_defaults_to_allowed():
    client = _FakeClient(robots_response=_not_found_robots())
    assert check_robots_txt_allows("https://example.com/page", user_agent="RunwaySafeIntelligence/1.0", client=client) is True


def test_robots_unreachable_defaults_to_allowed():
    client = _FakeClient(robots_response=None)  # .get() raises ConnectError
    assert check_robots_txt_allows("https://example.com/page", user_agent="RunwaySafeIntelligence/1.0", client=client) is True


# --- fetch_discovered_url end-to-end (fake client, in-memory DB) -----------


def test_fetch_discovered_url_writes_only_acquisition_side_rows():
    engine = _engine()
    with Session(engine) as session:
        client = _FakeClient(robots_response=_allow_all_robots(), fetch_responses=[_html_ok()])
        run = fetch_discovered_url(session, "https://example.com/page", client=client)
        assert run.status in (AcquisitionRunStatus.SUCCESS, AcquisitionRunStatus.NO_CHANGE)
        assert len(list(session.scalars(select(PublishingSource)))) == 1
        assert len(list(session.scalars(select(AcquisitionSource)))) == 1
        assert len(list(session.scalars(select(AcquisitionRun)))) == 1
        assert len(list(session.scalars(select(Snapshot)))) == 1


def test_fetch_discovered_url_blocked_by_robots_writes_nothing():
    engine = _engine()
    with Session(engine) as session:
        client = _FakeClient(robots_response=_disallow_all_robots())
        with pytest.raises(RobotsDisallowedError):
            fetch_discovered_url(session, "https://example.com/page", client=client)
        assert len(list(session.scalars(select(PublishingSource)))) == 0
        assert len(list(session.scalars(select(AcquisitionSource)))) == 0


def test_fetch_discovered_url_blocked_by_unsafe_target_writes_nothing():
    engine = _engine()
    with Session(engine) as session:
        with pytest.raises(Exception):
            fetch_discovered_url(session, "http://127.0.0.1/admin")
        assert len(list(session.scalars(select(PublishingSource)))) == 0
        assert len(list(session.scalars(select(AcquisitionSource)))) == 0


# --- Idempotency / re-fetch (Part J, reusing existing AcquisitionService) --


def test_same_bytes_refetch_reuses_snapshot_no_change():
    engine = _engine()
    with Session(engine) as session:
        client1 = _FakeClient(robots_response=_allow_all_robots(), fetch_responses=[_html_ok()])
        run1 = fetch_discovered_url(session, "https://example.com/page", client=client1)
        assert run1.status == AcquisitionRunStatus.SUCCESS
        snapshot_id_1 = run1.snapshot.id

        client2 = _FakeClient(robots_response=_allow_all_robots(), fetch_responses=[_html_ok()])
        run2 = fetch_discovered_url(session, "https://example.com/page", client=client2)
        assert run2.status == AcquisitionRunStatus.NO_CHANGE
        assert run2.snapshot.id == snapshot_id_1
        assert len(list(session.scalars(select(Snapshot)))) == 1
        assert len(list(session.scalars(select(AcquisitionRun)))) == 2


def test_changed_bytes_refetch_creates_new_snapshot_old_preserved():
    engine = _engine()
    with Session(engine) as session:
        client1 = _FakeClient(robots_response=_allow_all_robots(), fetch_responses=[_html_ok()])
        run1 = fetch_discovered_url(session, "https://example.com/page", client=client1)
        old_snapshot_id = run1.snapshot.id
        old_sha = run1.snapshot.sha256

        changed = _FakeResponse(200, headers={"content-type": "text/html"}, content=b"<html>CHANGED CONTENT</html>", url="https://example.com/page")
        client2 = _FakeClient(robots_response=_allow_all_robots(), fetch_responses=[changed])
        run2 = fetch_discovered_url(session, "https://example.com/page", client=client2)

        assert run2.status == AcquisitionRunStatus.SUCCESS
        assert run2.snapshot.id != old_snapshot_id
        assert run2.snapshot.sha256 != old_sha
        # Old snapshot still exists, unchanged, immutable.
        old_snapshot = session.get(Snapshot, old_snapshot_id)
        assert old_snapshot is not None
        assert old_snapshot.sha256 == old_sha
        assert len(list(session.scalars(select(Snapshot)))) == 2


def test_snapshot_immutability_still_enforced():
    engine = _engine()
    with Session(engine) as session:
        client = _FakeClient(robots_response=_allow_all_robots(), fetch_responses=[_html_ok()])
        run = fetch_discovered_url(session, "https://example.com/page", client=client)
        snapshot = run.snapshot
        snapshot.byte_size = 999999
        with pytest.raises(ValueError):
            session.commit()
        session.rollback()


def test_transient_failure_then_success_both_recorded_no_destructive_overwrite():
    engine = _engine()
    with Session(engine) as session:
        failing_client = _FakeClient(robots_response=_allow_all_robots(), fetch_responses=[])  # stream() pops from empty list -> IndexError
        with pytest.raises(Exception):
            fetch_discovered_url(session, "https://example.com/page", client=failing_client)
        # PublishingSource/AcquisitionSource survive the failed attempt (committed before acquire()).
        assert len(list(session.scalars(select(PublishingSource)))) == 1
        assert len(list(session.scalars(select(AcquisitionSource)))) == 1
        assert len(list(session.scalars(select(AcquisitionRun)))) == 1  # the failed run itself
        assert len(list(session.scalars(select(Snapshot)))) == 0

        ok_client = _FakeClient(robots_response=_allow_all_robots(), fetch_responses=[_html_ok()])
        run2 = fetch_discovered_url(session, "https://example.com/page", client=ok_client)
        assert run2.status == AcquisitionRunStatus.SUCCESS
        assert len(list(session.scalars(select(AcquisitionRun)))) == 2
        assert len(list(session.scalars(select(Snapshot)))) == 1
