"""RWI Mission #11B Part M - offline tests for
app.acquisition.generic_web (URL safety + GenericWebAcquisitionProvider).

No real network access anywhere in this file. DNS-dependent cases either
use literal IP-address URLs (safe, local, no real lookup) or monkeypatch
socket.getaddrinfo for hostname-based cases.
"""

from __future__ import annotations

import socket

import httpx
import pytest

from app.acquisition.generic_web import (
    MAX_RESPONSE_BYTES,
    GenericWebAcquisitionProvider,
    ResponseTooLargeError,
    TooManyRedirectsError,
    UnsafeFetchTargetError,
    validate_fetch_target,
)

_SETTINGS_UA_HEADER = "User-Agent"


# --- Fakes: no real network -------------------------------------------------


class _FakeStreamResponse:
    def __init__(self, status_code: int, *, headers: dict | None = None, content: bytes = b"", url: str = "https://example.com/"):
        self.status_code = status_code
        self.headers = httpx.Headers(headers or {})
        self._content = content
        self.url = httpx.URL(url)

    @property
    def is_redirect(self) -> bool:
        return self.status_code in (301, 302, 303, 307, 308)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=httpx.Request("GET", str(self.url)), response=self
            )

    def iter_bytes(self):
        chunk = 4096
        for i in range(0, len(self._content), chunk):
            yield self._content[i : i + chunk]

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakeStreamClient:
    def __init__(self, responses: list[_FakeStreamResponse], *, exc: Exception | None = None):
        self._responses = list(responses)
        self._exc = exc
        self.calls: list[dict] = []
        self.closed = False

    def stream(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, "kwargs": kwargs})
        if self._exc is not None:
            raise self._exc
        return self._responses.pop(0)

    def close(self):
        self.closed = True


def _html_response(url: str = "https://example.com/page") -> _FakeStreamResponse:
    return _FakeStreamResponse(
        200, headers={"content-type": "text/html; charset=utf-8"}, content=b"<html>hello</html>", url=url
    )


def _pdf_response(url: str = "https://example.com/doc.pdf") -> _FakeStreamResponse:
    return _FakeStreamResponse(200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.4 fake", url=url)


# --- validate_fetch_target: scheme -------------------------------------------


@pytest.mark.parametrize("url", ["http://good.example.com/", "https://good.example.com/"])
def test_allowed_schemes_pass_when_destination_resolves_public(url, monkeypatch: pytest.MonkeyPatch):
    def fake_getaddrinfo(host, port):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    validate_fetch_target(url)  # does not raise


@pytest.mark.parametrize("scheme", ["file", "ftp", "data", "javascript", "gopher"])
def test_disallowed_schemes_rejected(scheme):
    with pytest.raises(UnsafeFetchTargetError):
        validate_fetch_target(f"{scheme}://example.com/x")


# --- validate_fetch_target: literal-IP SSRF cases (no mocking needed) -------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://127.0.0.1:8080/admin",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data/",
        "http://169.254.1.1/",
        "http://10.0.0.5/",
        "http://172.16.0.5/",
        "http://192.168.1.1/",
        "http://[fe80::1]/",
        "http://[fc00::1]/",
        "http://0.0.0.0/",
    ],
)
def test_private_and_special_use_literal_ips_rejected(url):
    with pytest.raises(UnsafeFetchTargetError):
        validate_fetch_target(url)


def test_localhost_hostname_rejected():
    with pytest.raises(UnsafeFetchTargetError):
        validate_fetch_target("http://localhost/")


def test_public_literal_ip_accepted():
    validate_fetch_target("http://8.8.8.8/")  # does not raise


def test_public_ipv6_literal_accepted():
    validate_fetch_target("http://[2001:4860:4860::8888]/")  # does not raise


def test_ipv4_mapped_ipv6_loopback_rejected():
    """A known SSRF-bypass encoding: ::ffff:127.0.0.1 is IPv4-mapped IPv6
    for 127.0.0.1. Python's ipaddress module correctly resolves
    is_private/is_loopback through the mapping, so this is blocked by the
    same check, not a special case."""
    with pytest.raises(UnsafeFetchTargetError):
        validate_fetch_target("http://[::ffff:127.0.0.1]/")


# --- validate_fetch_target: hostname resolving to private IP (mocked) ------


def test_hostname_resolving_to_private_ip_rejected(monkeypatch: pytest.MonkeyPatch):
    def fake_getaddrinfo(host, port):
        assert host == "evil.example.com"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.99", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(UnsafeFetchTargetError):
        validate_fetch_target("http://evil.example.com/")


def test_hostname_resolving_to_public_ip_accepted(monkeypatch: pytest.MonkeyPatch):
    def fake_getaddrinfo(host, port):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    validate_fetch_target("http://good.example.com/")  # does not raise


def test_hostname_with_one_public_and_one_private_address_rejected(monkeypatch: pytest.MonkeyPatch):
    """Conservative: if ANY resolved address is non-public, reject - never
    pick a "best" address (Mission #11B Part F.2: "Be conservative")."""

    def fake_getaddrinfo(host, port):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(UnsafeFetchTargetError):
        validate_fetch_target("http://mixed.example.com/")


def test_dns_resolution_failure_rejected(monkeypatch: pytest.MonkeyPatch):
    def fake_getaddrinfo(host, port):
        raise socket.gaierror("name resolution failed")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(UnsafeFetchTargetError):
        validate_fetch_target("http://nonexistent.example.invalid/")


# --- Provider: successful responses -----------------------------------------


def test_successful_html_response(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.acquisition.generic_web.validate_fetch_target", lambda url: None)
    client = _FakeStreamClient([_html_response()])
    provider = GenericWebAcquisitionProvider("https://example.com/page", client=client)
    payload = provider.retrieve()
    assert payload.content == b"<html>hello</html>"
    assert payload.content_type == "text/html; charset=utf-8"
    assert payload.http_status == 200
    assert payload.request_url == "https://example.com/page"
    assert payload.final_url == "https://example.com/page"
    assert payload.provider_version == GenericWebAcquisitionProvider.version


def test_successful_pdf_response(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.acquisition.generic_web.validate_fetch_target", lambda url: None)
    client = _FakeStreamClient([_pdf_response()])
    provider = GenericWebAcquisitionProvider("https://example.com/doc.pdf", client=client)
    payload = provider.retrieve()
    assert payload.content_type == "application/pdf"
    assert payload.content.startswith(b"%PDF")


def test_final_url_differs_from_request_url_after_redirect(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.acquisition.generic_web.validate_fetch_target", lambda url: None)
    redirect = _FakeStreamResponse(302, headers={"location": "https://example.com/final"}, url="https://example.com/start")
    final = _html_response(url="https://example.com/final")
    client = _FakeStreamClient([redirect, final])
    provider = GenericWebAcquisitionProvider("https://example.com/start", client=client)
    payload = provider.retrieve()
    assert payload.request_url == "https://example.com/start"
    assert payload.final_url == "https://example.com/final"


def test_sanitized_headers_strip_sensitive_response_headers(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.acquisition.generic_web.validate_fetch_target", lambda url: None)
    response = _FakeStreamResponse(
        200,
        headers={"content-type": "text/html", "set-cookie": "session=abc123", "x-custom": "keep-me"},
        content=b"<html></html>",
    )
    client = _FakeStreamClient([response])
    provider = GenericWebAcquisitionProvider("https://example.com/", client=client)
    payload = provider.retrieve()
    assert "set-cookie" not in payload.retrieved_headers
    assert payload.retrieved_headers.get("x-custom") == "keep-me"


# --- Provider: failures -------------------------------------------------------


def test_timeout_propagates(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.acquisition.generic_web.validate_fetch_target", lambda url: None)
    client = _FakeStreamClient([], exc=httpx.ReadTimeout("timed out"))
    provider = GenericWebAcquisitionProvider("https://example.com/", client=client)
    with pytest.raises(httpx.ReadTimeout):
        provider.retrieve()


def test_connection_failure_propagates(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.acquisition.generic_web.validate_fetch_target", lambda url: None)
    client = _FakeStreamClient([], exc=httpx.ConnectError("refused"))
    provider = GenericWebAcquisitionProvider("https://example.com/", client=client)
    with pytest.raises(httpx.ConnectError):
        provider.retrieve()


def test_http_error_status_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.acquisition.generic_web.validate_fetch_target", lambda url: None)
    client = _FakeStreamClient([_FakeStreamResponse(404, content=b"not found")])
    provider = GenericWebAcquisitionProvider("https://example.com/missing", client=client)
    with pytest.raises(httpx.HTTPStatusError):
        provider.retrieve()


def test_empty_payload_raises_value_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.acquisition.generic_web.validate_fetch_target", lambda url: None)
    client = _FakeStreamClient([_FakeStreamResponse(200, headers={"content-type": "text/html"}, content=b"")])
    provider = GenericWebAcquisitionProvider("https://example.com/empty", client=client)
    with pytest.raises(ValueError):
        provider.retrieve()


# --- Redirects -----------------------------------------------------------------


def test_relative_redirect_location_resolved_and_validated(monkeypatch: pytest.MonkeyPatch):
    """Mission #11B.1 Part G: the missing focused test for a relative
    Location header (e.g. 'Location: /documents/foo.pdf'), proving
    urljoin() resolves it against the current URL and the resolved
    absolute destination passes through the SAME validate_fetch_target()
    safety check as any other redirect hop - not a broadened exemption."""
    monkeypatch.setattr("app.acquisition.generic_web.validate_fetch_target", lambda url: None)
    redirect = _FakeStreamResponse(302, headers={"location": "/documents/foo.pdf"}, url="https://example.com/start")
    final = _html_response(url="https://example.com/documents/foo.pdf")
    client = _FakeStreamClient([redirect, final])
    provider = GenericWebAcquisitionProvider("https://example.com/start", client=client)
    payload = provider.retrieve()
    assert payload.final_url == "https://example.com/documents/foo.pdf"
    # Confirm the resolved (absolute) URL, not the raw relative fragment, was requested for the second hop.
    assert client.calls[1]["url"] == "https://example.com/documents/foo.pdf"


def test_relative_redirect_to_private_ip_path_still_blocked(monkeypatch: pytest.MonkeyPatch):
    """A relative Location cannot be used to smuggle a private destination
    past validation either - urljoin() only resolves the PATH/query
    relatively; scheme+host still come from the current (safe) URL in
    ordinary cases, but this proves the resolved absolute URL is always
    re-validated regardless, not merely assumed safe because it looked
    relative."""
    redirect = _FakeStreamResponse(302, headers={"location": "//127.0.0.1/admin"}, url="https://example.com/start")
    client = _FakeStreamClient([redirect])
    provider = GenericWebAcquisitionProvider("https://example.com/start", client=client)
    with pytest.raises(UnsafeFetchTargetError):
        provider.retrieve()


def test_safe_public_redirect_followed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.acquisition.generic_web.validate_fetch_target", lambda url: None)
    redirect = _FakeStreamResponse(301, headers={"location": "https://example.com/b"}, url="https://example.com/a")
    final = _html_response(url="https://example.com/b")
    client = _FakeStreamClient([redirect, final])
    provider = GenericWebAcquisitionProvider("https://example.com/a", client=client)
    payload = provider.retrieve()
    assert payload.final_url == "https://example.com/b"


def test_redirect_to_private_ip_blocked(monkeypatch: pytest.MonkeyPatch):
    """Each redirect hop is re-validated with the REAL validate_fetch_target
    (not monkeypatched here) - the initial URL is safe, but the redirect
    target is a private IP and must be blocked before being requested."""
    redirect = _FakeStreamResponse(302, headers={"location": "http://127.0.0.1/admin"}, url="https://example.com/a")
    client = _FakeStreamClient([redirect])
    provider = GenericWebAcquisitionProvider("https://example.com/a", client=client)
    with pytest.raises(UnsafeFetchTargetError):
        provider.retrieve()
    # Only the first hop should have been attempted - the private-IP
    # target must never be requested.
    assert len(client.calls) == 1


def test_redirect_to_localhost_blocked(monkeypatch: pytest.MonkeyPatch):
    redirect = _FakeStreamResponse(302, headers={"location": "http://localhost/secret"}, url="https://example.com/a")
    client = _FakeStreamClient([redirect])
    provider = GenericWebAcquisitionProvider("https://example.com/a", client=client)
    with pytest.raises(UnsafeFetchTargetError):
        provider.retrieve()


def test_redirect_loop_exceeds_max_hops(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.acquisition.generic_web.validate_fetch_target", lambda url: None)
    responses = [
        _FakeStreamResponse(302, headers={"location": f"https://example.com/{i+1}"}, url=f"https://example.com/{i}")
        for i in range(10)
    ]
    client = _FakeStreamClient(responses)
    provider = GenericWebAcquisitionProvider("https://example.com/0", client=client)
    with pytest.raises(TooManyRedirectsError):
        provider.retrieve()


def test_redirect_missing_location_header_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.acquisition.generic_web.validate_fetch_target", lambda url: None)
    client = _FakeStreamClient([_FakeStreamResponse(302, headers={}, url="https://example.com/a")])
    provider = GenericWebAcquisitionProvider("https://example.com/a", client=client)
    with pytest.raises(ValueError):
        provider.retrieve()


# --- Size limit -----------------------------------------------------------------


def test_response_below_cap_succeeds(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.acquisition.generic_web.validate_fetch_target", lambda url: None)
    content = b"x" * 1000
    client = _FakeStreamClient([_FakeStreamResponse(200, headers={"content-type": "text/plain"}, content=content)])
    provider = GenericWebAcquisitionProvider("https://example.com/small", client=client)
    payload = provider.retrieve()
    assert len(payload.content) == 1000


def test_response_exactly_at_cap_succeeds(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.acquisition.generic_web.validate_fetch_target", lambda url: None)
    content = b"x" * MAX_RESPONSE_BYTES
    client = _FakeStreamClient([_FakeStreamResponse(200, headers={"content-type": "text/plain"}, content=content)])
    provider = GenericWebAcquisitionProvider("https://example.com/exact", client=client)
    payload = provider.retrieve()
    assert len(payload.content) == MAX_RESPONSE_BYTES


def test_slow_trickle_response_times_out_without_real_sleeping(monkeypatch: pytest.MonkeyPatch):
    """A server dribbling bytes slowly enough that no single read exceeds
    httpx's own per-read timeout must still be bounded by an overall
    wall-clock deadline - simulated here via a fake clock, not real
    sleeping."""
    import app.acquisition.generic_web as generic_web_module

    monkeypatch.setattr(generic_web_module, "validate_fetch_target", lambda url: None)
    fake_now = [0.0]

    def fake_perf_counter():
        fake_now[0] += 100.0  # jump far past any deadline on each call
        return fake_now[0]

    monkeypatch.setattr(generic_web_module, "perf_counter", fake_perf_counter)
    client = _FakeStreamClient([_FakeStreamResponse(200, headers={"content-type": "text/plain"}, content=b"trickle")])
    provider = GenericWebAcquisitionProvider("https://example.com/slow", client=client, timeout_seconds=5.0)
    with pytest.raises(generic_web_module.ResponseTimedOutError):
        provider.retrieve()


def test_response_over_cap_aborts_without_full_buffering(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.acquisition.generic_web.validate_fetch_target", lambda url: None)
    content = b"x" * (MAX_RESPONSE_BYTES + 1)
    client = _FakeStreamClient([_FakeStreamResponse(200, headers={"content-type": "text/plain"}, content=content)])
    provider = GenericWebAcquisitionProvider("https://example.com/huge", client=client)
    with pytest.raises(ResponseTooLargeError):
        provider.retrieve()


# --- Credentials -----------------------------------------------------------------


def test_request_never_carries_authorization_or_cookie_headers(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.acquisition.generic_web.validate_fetch_target", lambda url: None)
    client = _FakeStreamClient([_html_response()])
    provider = GenericWebAcquisitionProvider("https://example.com/page", client=client)
    provider.retrieve()
    sent_headers = client.calls[0]["kwargs"]["headers"]
    lowered_keys = {k.lower() for k in sent_headers}
    assert "authorization" not in lowered_keys
    assert "cookie" not in lowered_keys
    assert "x-subscription-token" not in lowered_keys  # Brave's header name, must never appear here


def test_request_headers_are_exactly_user_agent(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.acquisition.generic_web.validate_fetch_target", lambda url: None)
    client = _FakeStreamClient([_html_response()])
    provider = GenericWebAcquisitionProvider("https://example.com/page", client=client)
    provider.retrieve()
    sent_headers = client.calls[0]["kwargs"]["headers"]
    assert set(sent_headers) == {"User-Agent"}


def test_follow_redirects_flag_is_false_on_underlying_client_call(monkeypatch: pytest.MonkeyPatch):
    """Redirects must be handled manually with per-hop validation, never
    via httpx's own follow_redirects=True (Mission #11B Part F.3)."""
    monkeypatch.setattr("app.acquisition.generic_web.validate_fetch_target", lambda url: None)
    client = _FakeStreamClient([_html_response()])
    provider = GenericWebAcquisitionProvider("https://example.com/page", client=client)
    provider.retrieve()
    assert client.calls[0]["kwargs"]["follow_redirects"] is False


def test_owned_client_is_closed_after_use():
    """When no client is injected, the provider must construct and clean
    up its own - it must never reuse an ambient/shared client that could
    carry unrelated state."""
    provider = GenericWebAcquisitionProvider("http://127.0.0.1/")  # will fail validate_fetch_target before any client use
    with pytest.raises(UnsafeFetchTargetError):
        provider.retrieve()  # never even constructs a client for an unsafe target
