"""Generic web-page acquisition provider (RWI Mission #11B).

Sits at the exact same layer as app.acquisition.faa.FAAAcquisitionProvider
and app.acquisition.mac_granicus.MACGranicusAcquisitionProvider - same
`.source_url`/`.version`/`.retrieve() -> AcquisitionPayload` protocol, so
it plugs into app.services.acquisition.AcquisitionService completely
unmodified (Mission #9D/#9F's own established precedent: "the provider
interface is already generic").

UNLIKE the FAA/MAC providers, this one fetches an ARBITRARY, human-
selected public URL rather than one fixed, pre-vetted feed - so it alone
carries real network-safety responsibility (Mission #11A Part G/Mission
#11B Part F): scheme allowlisting, SSRF/private-network destination
validation (performed BEFORE every connection, including every redirect
hop - redirects are followed manually, one hop at a time, never via
httpx's own follow_redirects=True), a hard response-size cap enforced via
streaming (never buffer-then-check), and a clean, credential-free request
(no Discovery/Brave credentials, no cookies, no Authorization header -
only an honest, fixed User-Agent identifying RWI).

FETCH != EVIDENCE. This module preserves bytes; it makes no claim about
their truth, relevance, or acceptability - see
app.services.generic_web_fetch for the human-authorized orchestration
that wraps this provider, and its own module docstring for the exact
governance boundary this stops at.
"""

from __future__ import annotations

import ipaddress
import socket
import typing
from dataclasses import dataclass
from time import perf_counter
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx

from app.acquisition.faa import AcquisitionPayload, sanitized_headers
from app.config import settings

PROVIDER_VERSION = "generic-web-http/1"

ALLOWED_SCHEMES = frozenset({"http", "https"})

# Conservative V1 cap for ordinary public HTML/PDF documents (Mission
# #11B Part F.7). Large enough for real governed cases already seen this
# session (the CAA ACP PDF is a few hundred KB), small enough to bound
# memory/storage for an arbitrary, unvetted public URL. Enforced via
# streaming - the response is aborted mid-transfer past this limit, never
# buffered to completion first.
MAX_RESPONSE_BYTES = 25 * 1024 * 1024  # 25 MiB

MAX_REDIRECT_HOPS = 5

_REQUEST_HEADERS = {"User-Agent": settings.acquisition_user_agent}


class UnsafeFetchTargetError(ValueError):
    """Raised when a URL - the initial target or any redirect hop - fails
    scheme or destination-safety validation. Always raised BEFORE any
    network connection is attempted for that specific URL."""


class ResponseTooLargeError(ValueError):
    """Raised when a response body exceeds MAX_RESPONSE_BYTES. The
    connection is aborted; no content is preserved for this attempt."""


class TooManyRedirectsError(ValueError):
    """Raised when more than MAX_REDIRECT_HOPS redirects occur."""


def _is_public_ip(ip: "ipaddress.IPv4Address | ipaddress.IPv6Address") -> bool:
    """Conservative allowlist-by-exclusion: reject every category of
    non-public address, including the explicit cloud metadata address
    (redundant with the link-local check but named explicitly so intent
    is unambiguous to a future reader)."""
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.is_site_local:
        return False
    if str(ip) == "169.254.169.254":
        return False
    return True


def _resolve_validated_public_addresses(hostname: str) -> list[str]:
    """Resolves `hostname` RIGHT NOW (never a cached/earlier result) and
    returns every resolved IP address string, having first verified ALL
    of them are public - conservative: block rather than pick a "best"
    address. Raises UnsafeFetchTargetError on resolution failure or any
    non-public address. Shared by validate_fetch_target() (early,
    cheap, fail-fast preflight) and _PinnedNetworkBackend.connect_tcp()
    (Mission #11B.1 - the AUTHORITATIVE, race-free check performed at
    the moment of actual connection, see that class's own docstring)."""
    if hostname.lower() == "localhost":
        raise UnsafeFetchTargetError("Refusing to fetch localhost")
    try:
        addrinfo = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise UnsafeFetchTargetError(f"DNS resolution failed for {hostname!r}: {exc}") from exc
    if not addrinfo:
        raise UnsafeFetchTargetError(f"DNS resolution returned no addresses for {hostname!r}")
    addresses = [sockaddr[0] for _family, _type, _proto, _canonname, sockaddr in addrinfo]
    for addr in addresses:
        ip = ipaddress.ip_address(addr)
        if not _is_public_ip(ip):
            raise UnsafeFetchTargetError(f"Refusing to fetch {hostname!r}: resolves to non-public address {addr}")
    return addresses


def validate_fetch_target(url: str) -> None:
    """Scheme + SSRF/destination safety check. This is the early, cheap,
    fail-fast PREFLIGHT check - it rejects an obviously-unsafe URL before
    any DB write or network setup happens at all. It is deliberately NOT
    the sole line of defense any more (Mission #11B.1): the actual
    outbound connection is separately, authoritatively re-validated at
    connect time by _PinnedNetworkBackend below, which closes the
    DNS-rebinding TOCTOU gap this function alone could not (see that
    class's own docstring for the full explanation and the Mission
    #11B.1 HQ report's Part B for the installed-httpcore-library trace
    proving the mechanism).
    """
    parts = urlsplit(url)
    if parts.scheme not in ALLOWED_SCHEMES:
        raise UnsafeFetchTargetError(f"Unsupported URL scheme: {parts.scheme!r}")
    hostname = parts.hostname
    if not hostname:
        raise UnsafeFetchTargetError("URL has no hostname")
    _resolve_validated_public_addresses(hostname)


class _PinnedNetworkBackend(httpcore.NetworkBackend):
    """Closes the DNS-rebinding TOCTOU gap (Mission #11B.1): performs its
    OWN fresh, authoritative resolution + public-address validation of
    `host` at the exact moment httpcore is about to open a NEW physical
    TCP connection - never reusing an earlier (possibly stale) result -
    then connects directly to that validated literal IP address.

    Installed-library trace proving this is safe (httpcore 1.0.9,
    httpcore/_sync/connection.py HTTPConnection._connect): TLS SNI
    (`server_hostname`) and the HTTP Host header are BOTH derived from
    the request's own `origin.host` - the original hostname - never from
    whatever `connect_tcp()` actually connects to. Pinning the physical
    connection here has ZERO effect on certificate-hostname verification
    or the Host header; both remain the real, standard httpx/httpcore
    behavior, completely unmodified. Only the literal socket destination
    changes, and only after passing the same _is_public_ip check
    validate_fetch_target() uses.

    Delegates the actual socket connect/TLS/read/write work entirely to
    httpcore's own real, standard httpcore.SyncBackend - nothing here
    reimplements networking, TLS, or certificate verification."""

    def __init__(self) -> None:
        self._real = httpcore.SyncBackend()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: typing.Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.NetworkStream:
        validated_addresses = _resolve_validated_public_addresses(host)
        pinned_ip = validated_addresses[0]
        return self._real.connect_tcp(
            pinned_ip, port, timeout=timeout, local_address=local_address, socket_options=socket_options
        )

    def connect_unix_socket(
        self, path: str, timeout: float | None = None, socket_options: typing.Iterable[httpcore.SOCKET_OPTION] | None = None
    ) -> httpcore.NetworkStream:
        raise NotImplementedError("Generic web fetch never connects via unix sockets")

    def sleep(self, seconds: float) -> None:
        self._real.sleep(seconds)


class SafeHTTPTransport(httpx.HTTPTransport):
    """An httpx.HTTPTransport identical to the standard one in every way
    except its connection pool's network_backend is _PinnedNetworkBackend
    instead of httpcore's default - the ONE change needed to close the
    DNS-rebinding gap. Certificate verification (via httpx's own public
    httpx.create_ssl_context(), the same helper httpx.HTTPTransport
    itself uses internally), HTTP/1.1 framing, and connection pooling are
    all otherwise the real, standard, unmodified httpx/httpcore
    implementation."""

    def __init__(self) -> None:
        # Deliberately does NOT call super().__init__() - that would
        # construct (and immediately discard) a whole throwaway
        # httpcore.ConnectionPool, including its own separate SSL
        # context, pure waste. httpx.HTTPTransport's other methods
        # (handle_request, close, context-manager protocol - all
        # inherited, unmodified) only ever depend on self._pool
        # existing, which this sets directly, once.
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(),
            network_backend=_PinnedNetworkBackend(),
        )


def build_safe_client() -> httpx.Client:
    """The one place a plain, un-pinned httpx.Client must never be
    constructed for an arbitrary destination - every generic-web network
    call (the main fetch AND the robots.txt check, Mission #11B.1 Part
    F.7) goes through this."""
    return httpx.Client(transport=SafeHTTPTransport())


class ResponseTimedOutError(ValueError):
    """Raised when streaming a response body exceeds the overall wall-clock
    deadline. httpx's own per-read timeout only bounds the gap BETWEEN
    chunks - a server trickling bytes slowly (each individual read
    arriving just inside that window) would otherwise never time out, an
    honest gap this closes with a simple total-elapsed check."""


def _read_with_limit(response: httpx.Response, limit: int, *, deadline: float) -> bytes:
    total = 0
    chunks: list[bytes] = []
    for chunk in response.iter_bytes():
        if perf_counter() > deadline:
            raise ResponseTimedOutError("Response body took too long to fully arrive (slow-trickle guard).")
        total += len(chunk)
        if total > limit:
            raise ResponseTooLargeError(
                f"Response exceeded the {limit}-byte limit; aborted without buffering the rest."
            )
        chunks.append(chunk)
    return b"".join(chunks)


class GenericWebAcquisitionProvider:
    """Fetches ONE arbitrary, human-approved public URL. Matches the
    existing provider protocol exactly (`.source_url`, `.version`,
    `.retrieve() -> AcquisitionPayload`) - AcquisitionService is used
    completely unmodified."""

    version = PROVIDER_VERSION

    def __init__(
        self,
        source_url: str,
        *,
        client: httpx.Client | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.source_url = source_url
        self.timeout_seconds = timeout_seconds or settings.acquisition_timeout_seconds
        self._client = client

    def retrieve(self) -> AcquisitionPayload:
        started = perf_counter()
        # One overall deadline for the whole operation (all redirect hops
        # and the final body read combined) - at least 60s or 4x the
        # per-request timeout, whichever is larger, so a legitimate large
        # (but under the byte cap) document on a slow connection still
        # succeeds, while a deliberately slow-trickling server cannot
        # hold the connection open indefinitely.
        overall_deadline = started + max(self.timeout_seconds * 4, 60.0)
        validate_fetch_target(self.source_url)

        client = self._client
        owns_client = client is None
        if owns_client:
            client = build_safe_client()

        try:
            url = self.source_url
            hops = 0
            while True:
                with client.stream(
                    "GET", url, timeout=self.timeout_seconds, headers=_REQUEST_HEADERS, follow_redirects=False
                ) as response:
                    if response.is_redirect:
                        hops += 1
                        if hops > MAX_REDIRECT_HOPS:
                            raise TooManyRedirectsError(
                                f"Exceeded {MAX_REDIRECT_HOPS} redirect hops fetching {self.source_url!r}"
                            )
                        location = response.headers.get("location")
                        if not location:
                            raise ValueError("Redirect response missing Location header")
                        next_url = urljoin(str(response.url), location)
                        validate_fetch_target(next_url)
                        url = next_url
                        continue

                    response.raise_for_status()
                    content = _read_with_limit(response, MAX_RESPONSE_BYTES, deadline=overall_deadline)
                    if not content:
                        raise ValueError("Generic web fetch returned an empty payload")
                    return AcquisitionPayload(
                        content=content,
                        request_url=self.source_url,
                        final_url=str(response.url),
                        retrieved_headers=sanitized_headers(response),
                        http_status=response.status_code,
                        content_type=response.headers.get("content-type"),
                        duration_seconds=perf_counter() - started,
                        provider_version=self.version,
                    )
        finally:
            if owns_client:
                client.close()
