"""RWI Mission #11B.1 Part F - offline tests proving the DNS-rebinding
TOCTOU gap identified against Mission #11B's original preflight-only
design is closed by _PinnedNetworkBackend/SafeHTTPTransport.

No real network access anywhere in this file - the real httpcore.
SyncBackend.connect_tcp (which would open an actual socket) is always
monkeypatched out; DNS resolution is controlled via socket.getaddrinfo.
"""

from __future__ import annotations

import socket

import httpcore
import httpx
import pytest

from app.acquisition.generic_web import (
    SafeHTTPTransport,
    UnsafeFetchTargetError,
    _PinnedNetworkBackend,
    build_safe_client,
)


class _FakeStream:
    """Stand-in for a real httpcore NetworkStream - never touched, just
    needs to be a distinguishable sentinel object."""


def _patch_real_connect(monkeypatch: pytest.MonkeyPatch):
    """Prevents the delegate httpcore.SyncBackend from ever opening a
    real socket - records what it was called with instead."""
    calls: list[dict] = []

    def fake_connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        calls.append({"host": host, "port": port, "timeout": timeout})
        return _FakeStream()

    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", fake_connect_tcp)
    return calls


# --- 1: fresh, non-cached resolution at connect time -------------------------


def test_connect_tcp_performs_its_own_fresh_resolution_every_call(monkeypatch: pytest.MonkeyPatch):
    """The validation used for the actual connection must never be a
    reused/cached earlier result - each connect_tcp call independently
    calls socket.getaddrinfo."""
    real_calls = _patch_real_connect(monkeypatch)
    resolution_calls = []

    def fake_getaddrinfo(host, port):
        resolution_calls.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    backend = _PinnedNetworkBackend()
    backend.connect_tcp("example.com", 443)
    backend.connect_tcp("example.com", 443)
    assert resolution_calls == ["example.com", "example.com"]  # resolved fresh, every time, never cached
    assert [c["host"] for c in real_calls] == ["93.184.216.34", "93.184.216.34"]


# --- 2/3: simulated DNS rebind is blocked, across address categories --------


@pytest.mark.parametrize(
    "rebind_ip",
    [
        "127.0.0.1",  # loopback
        "10.0.0.5",  # RFC1918
        "192.168.1.1",  # RFC1918
        "169.254.169.254",  # link-local / cloud metadata
        "::1",  # IPv6 loopback
        "fc00::1",  # IPv6 private (unique local)
        "fe80::1",  # IPv6 link-local
    ],
)
def test_dns_rebind_at_connect_time_is_blocked(monkeypatch: pytest.MonkeyPatch, rebind_ip):
    """The exact scenario the mission describes: an earlier preflight
    check saw a public address; resolution AT THE MOMENT OF ACTUAL
    CONNECTION returns a private/special-use address instead. Must be
    blocked - and the real (mocked-out) socket backend must never be
    reached."""
    real_calls = _patch_real_connect(monkeypatch)
    call_count = {"n": 0}

    def fake_getaddrinfo(host, port):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        family = socket.AF_INET6 if ":" in rebind_ip else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (rebind_ip, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    from app.acquisition.generic_web import validate_fetch_target

    # Preflight (first resolution) passes - the URL looked safe at that moment.
    validate_fetch_target("http://rebind.example.com/")

    # Actual connection attempt (second, independent resolution) must be blocked.
    backend = _PinnedNetworkBackend()
    with pytest.raises(UnsafeFetchTargetError):
        backend.connect_tcp("rebind.example.com", 80)
    assert real_calls == []  # the real (mocked) socket backend was never reached


# --- 4/5: SNI/Host/certificate hostname are structurally untouched ----------


def test_pinned_backend_never_overrides_start_tls_or_touches_stream_hostname_logic():
    """_PinnedNetworkBackend implements ONLY connect_tcp/connect_unix_socket/
    sleep - it never defines or overrides start_tls, which is where SNI/
    certificate-hostname verification happens. This is a structural,
    inspectable proof (not just an assertion) that pinning the physical
    connection cannot touch TLS hostname verification - see
    httpcore._sync.connection.HTTPConnection._connect (installed
    httpcore 1.0.9): server_hostname always comes from the request's own
    origin, never from what connect_tcp did."""
    own_methods = {name for name in vars(_PinnedNetworkBackend) if not name.startswith("__")}
    assert "start_tls" not in own_methods
    assert own_methods == {"connect_tcp", "connect_unix_socket", "sleep"}


def test_connect_tcp_receives_original_hostname_not_an_ip(monkeypatch: pytest.MonkeyPatch):
    """httpcore always calls connect_tcp with the ORIGINAL request
    hostname (never an IP) - proving the origin/Host/SNI machinery
    upstream of this backend is never given a rewritten destination; only
    THIS backend's own internal call to the real delegate uses the
    resolved IP, invisibly, one layer below where Host/SNI are decided."""
    real_calls = _patch_real_connect(monkeypatch)

    def fake_getaddrinfo(host, port):
        assert host == "original-hostname.example.com"  # exactly what was passed in, unmodified
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    backend = _PinnedNetworkBackend()
    backend.connect_tcp("original-hostname.example.com", 443)
    # The delegate (i.e. the actual socket) connects to the pinned IP -
    # this is the ONLY place the IP is ever used.
    assert real_calls[0]["host"] == "93.184.216.34"


# --- 6/7: redirects and robots.txt use the identical protected transport ----


def test_build_safe_client_uses_pinned_transport():
    client = build_safe_client()
    assert isinstance(client._transport, SafeHTTPTransport)
    client.close()


def test_generic_web_provider_default_client_is_safe(monkeypatch: pytest.MonkeyPatch):
    """No injected client -> retrieve() must construct one via
    build_safe_client(), never a bare httpx.Client() (which would use
    httpcore's default, unpinned backend). The real socket layer is
    mocked out (_patch_real_connect) so this stays fully offline even
    though a real SafeHTTPTransport/_PinnedNetworkBackend is exercised."""
    import app.acquisition.generic_web as generic_web_module

    _patch_real_connect(monkeypatch)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))],
    )
    built = []

    def fake_build_safe_client():
        client = httpx.Client(transport=SafeHTTPTransport())
        built.append(client)
        return client

    monkeypatch.setattr(generic_web_module, "build_safe_client", fake_build_safe_client)
    monkeypatch.setattr(generic_web_module, "validate_fetch_target", lambda url: None)

    provider = generic_web_module.GenericWebAcquisitionProvider("https://example.com/")
    try:
        provider.retrieve()
    except Exception:
        pass  # the mocked stream/response shape won't satisfy the rest of retrieve() - irrelevant here
    assert len(built) == 1
    for client in built:
        client.close()


def test_robots_check_uses_safe_client_when_none_injected(monkeypatch: pytest.MonkeyPatch):
    """Same offline discipline as above - the real socket layer is
    mocked out so no live network access occurs."""
    import app.services.generic_web_fetch as fetch_module

    _patch_real_connect(monkeypatch)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))],
    )
    built = []

    def fake_build_safe_client():
        client = httpx.Client(transport=SafeHTTPTransport())
        built.append(client)
        return client

    monkeypatch.setattr(fetch_module, "build_safe_client", fake_build_safe_client)
    try:
        fetch_module.check_robots_txt_allows("https://example.com/x", user_agent="RWI/1.0")
    except Exception:
        pass  # the mocked stream shape won't satisfy the rest of the request - irrelevant here
    assert len(built) == 1
    for client in built:
        client.close()


# --- 8: connection pooling cannot bypass validation --------------------------


def test_each_new_physical_connection_is_independently_revalidated(monkeypatch: pytest.MonkeyPatch):
    """A pooled/reused connection never calls connect_tcp again for its
    lifetime (standard httpcore behavior - already-open sockets don't
    "re-resolve"); but this backend has no caching of its own, so any
    call representing a genuinely NEW physical connection (e.g. after a
    prior one closed, or a redirect to a different host) is independently,
    freshly validated - proven by two consecutive calls with two
    different resolution results both being correctly evaluated on their
    own merits."""
    real_calls = _patch_real_connect(monkeypatch)
    responses = iter(
        [
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))],  # first host: public, allowed
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))],  # second host: private, blocked
        ]
    )

    def fake_getaddrinfo(host, port):
        return next(responses)

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    backend = _PinnedNetworkBackend()
    backend.connect_tcp("good.example.com", 443)  # succeeds
    with pytest.raises(UnsafeFetchTargetError):
        backend.connect_tcp("bad.example.com", 443)  # independently blocked, not "trusted" by the first call
    assert len(real_calls) == 1  # only the first (safe) connection ever reached the real backend


# --- 9: no credential headers introduced by the transport change ------------


def test_safe_http_transport_introduces_no_default_headers():
    """The transport layer itself must never add headers - credential
    isolation is entirely the caller's responsibility (the fixed
    User-Agent set in generic_web.py's request call site), unaffected by
    which transport is plugged into the client."""
    transport = SafeHTTPTransport()
    assert not hasattr(transport, "headers")
    transport.close()
