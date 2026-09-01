"""Human-authorized generic web fetch orchestration (RWI Mission #11B).

    human FETCH authorization -> Generic Web Acquisition -> AcquisitionRun
    -> immutable Snapshot -> STOP

FETCH means exactly: "Attempt to retrieve and preserve the resource at
this human-selected URL for later inspection/extraction." It never means
accepted evidence, verified claims, confirmed EMAS presence, approved
airport identity, a Signal, or an Installation. This module writes ONLY
PublishingSource/AcquisitionSource/AcquisitionRun/Snapshot rows - it
imports nothing from, and never calls, any Source/SourceAssertion/
CandidateFragment/IdentityGuard/Signal/Installation/governance-mutation
code (see tests/test_generic_web_fetch_architectural_safety.py, which
enforces this by AST inspection, not just convention).

PublishingSource authority semantics (Mission #11B Part D): a previously-
unseen hostname is registered with NEUTRAL defaults
(source_type=None, reliability_level="unverified") - never
"official"/"government"/"regulator"/"vendor" merely because
app.discovery.triage classified the SAME domain that way for review-
priority purposes. Triage authority (a runtime-only reviewer hint) and
acquisition trust (a persisted row) are deliberately kept separate and
never conflated - see app.discovery.triage's own module docstring for
why the triage-side classification exists in the first place.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.acquisition.generic_web import GenericWebAcquisitionProvider, build_safe_client, validate_fetch_target
from app.config import settings
from app.discovery.dedup import normalize_url
from app.models import AcquisitionRun, AcquisitionSource, PublishingSource
from app.services.acquisition import AcquisitionService

logger = logging.getLogger(__name__)

_KEY_PREFIX = "generic_web:"


class RobotsDisallowedError(ValueError):
    """Raised when a successfully-retrieved robots.txt explicitly
    disallows fetching this URL for RWI's user agent. Per standard
    Robots Exclusion Protocol semantics (RFC 9309) and this mission's
    own explicit instruction, an UNREACHABLE robots.txt (404, network
    failure) does NOT raise this - it defaults to allowed, matching real
    crawler/library behavior (urllib.robotparser's own default). This is
    only raised when a rule was actually, successfully obtained and it
    says no."""


def _hostname_of(url: str) -> str:
    hostname = urlsplit(url).hostname
    if not hostname:
        raise ValueError(f"URL has no hostname: {url!r}")
    return hostname.lower()


def _acquisition_source_key_for_url(url: str) -> str:
    """Deterministic, bounded get-or-create key (Mission #11B Part D:
    "use stable deterministic identity"). Derived from the same
    normalize_url() Discovery's own dedup layer already uses (app.
    discovery.dedup) - one consistent URL-normalization behavior across
    Discovery and Acquisition, rather than two independently-drifting
    ones. Hashed (not the raw URL) because AcquisitionSource.key is a
    bounded String(150) column and real URLs can exceed that."""
    import hashlib

    normalized = normalize_url(url)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"{_KEY_PREFIX}{digest}"


def check_robots_txt_allows(
    url: str,
    *,
    user_agent: str,
    client: httpx.Client | None = None,
    timeout_seconds: float | None = None,
) -> bool:
    """Best-effort, standards-based robots.txt check (Mission #11B Part
    G). Fetches {scheme}://{host}/robots.txt through the SAME
    scheme/SSRF validation as any other fetch target (no bypass for
    robots.txt itself) using stdlib urllib.robotparser to interpret it -
    no new dependency, no crawling framework.

    Returns True (allowed) when: the path is genuinely permitted, OR
    robots.txt could not be reliably obtained at all (404, connection
    failure, timeout) - matching real-world robots.txt convention: an
    absent/unreachable robots.txt states no restriction, it does not
    imply "forbidden". Returns False ONLY when a robots.txt was
    successfully retrieved and it explicitly disallows this path for
    `user_agent` (checked as "*" and as the literal user_agent).
    """
    parts = urlsplit(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"

    try:
        validate_fetch_target(robots_url)
    except ValueError:
        # Same-host safety failure as the real target would hit anyway;
        # do not treat this as a robots-specific block, let the real
        # fetch attempt raise its own clear error.
        return True

    owns_client = client is None
    http_client = client or build_safe_client()
    try:
        response = http_client.get(
            robots_url,
            timeout=timeout_seconds or settings.acquisition_timeout_seconds,
            headers={"User-Agent": user_agent},
            follow_redirects=True,
        )
    except httpx.HTTPError:
        return True  # unreachable robots.txt -> default allow (standard behavior)
    finally:
        if owns_client:
            http_client.close()

    if response.status_code == 404:
        return True
    if response.status_code >= 400:
        return True  # any other client/server error fetching robots.txt -> default allow

    parser = RobotFileParser()
    parser.set_url(robots_url)
    try:
        parser.parse(response.text.splitlines())
    except Exception:  # pragma: no cover - malformed robots.txt content
        return True

    return parser.can_fetch(user_agent, url)


def get_or_create_publishing_source_for_hostname(
    session: Session, hostname: str
) -> tuple[PublishingSource, bool]:
    """Get-or-create, keyed on `name == hostname` (PublishingSource has
    no DB-level unique constraint - application-level idempotency,
    mirroring the discipline every other get-or-create in this pipeline
    already uses). NEUTRAL defaults only - never inherits or infers
    authority from anything Discovery/Triage classified this domain as."""
    existing = session.scalar(select(PublishingSource).where(PublishingSource.name == hostname))
    if existing is not None:
        return existing, False
    publishing_source = PublishingSource(
        name=hostname,
        source_type=None,
        homepage_url=f"https://{hostname}",
        country_code=None,
        reliability_level="unverified",
        notes="Auto-registered by generic web fetch (Mission #11B); not a trust or authority claim.",
    )
    session.add(publishing_source)
    session.flush()
    return publishing_source, True


def get_or_create_acquisition_source_for_url(
    session: Session, url: str, publishing_source: PublishingSource
) -> tuple[AcquisitionSource, bool]:
    """Get-or-create, keyed on the deterministic hash-based key (Part D).
    `canonical_url` is the human-approved URL, verbatim - never rewritten,
    never populated from search title/snippet."""
    key = _acquisition_source_key_for_url(url)
    existing = session.scalar(select(AcquisitionSource).where(AcquisitionSource.key == key))
    if existing is not None:
        return existing, False
    if len(url) > 1000:
        raise ValueError(f"URL exceeds the 1000-character AcquisitionSource.canonical_url column limit: {url!r}")
    display_name = url if len(url) <= 200 else url[:197] + "..."
    acquisition_source = AcquisitionSource(
        publishing_source=publishing_source,
        key=key,
        display_name=display_name,
        acquisition_type="http",
        canonical_url=url,
        expected_media_type=None,  # Part I: no content-type gate - generic fetch accepts what it finds
        active=True,
    )
    session.add(acquisition_source)
    session.flush()
    return acquisition_source, True


def fetch_discovered_url(
    session: Session,
    url: str,
    *,
    client: httpx.Client | None = None,
    timeout_seconds: float | None = None,
    skip_robots_check: bool = False,
) -> AcquisitionRun:
    """The single human-authorized entry point: one URL in, one
    AcquisitionRun out. Order of operations, each explained:

    1. validate_fetch_target(url) FIRST - fail before any DB write or
       network call for a URL that is unsafe on its face.
    2. robots.txt check (best-effort, standards-based) - fail before any
       DB write if explicitly disallowed.
    3. get-or-create PublishingSource/AcquisitionSource, THEN COMMIT
       immediately - deliberately BEFORE calling AcquisitionService.
       acquire(). AcquisitionService.acquire() rolls back the session on
       any failure; committing the get-or-created rows first means a
       failed fetch never rolls back (and orphans the FK target of) the
       PublishingSource/AcquisitionSource rows it just created - a real
       correctness risk found and closed during this mission's own
       implementation, not merely assumed safe.
    4. AcquisitionService.acquire() - completely unmodified, existing,
       already-tested machinery.
    """
    validate_fetch_target(url)

    if not skip_robots_check:
        allowed = check_robots_txt_allows(
            url,
            user_agent=settings.acquisition_user_agent,
            client=client,
            timeout_seconds=timeout_seconds,
        )
        if not allowed:
            raise RobotsDisallowedError(f"robots.txt disallows fetching {url!r} for RWI's user agent")

    hostname = _hostname_of(url)
    publishing_source, _ = get_or_create_publishing_source_for_hostname(session, hostname)
    acquisition_source, _ = get_or_create_acquisition_source_for_url(session, url, publishing_source)
    session.commit()

    provider = GenericWebAcquisitionProvider(url, client=client, timeout_seconds=timeout_seconds)
    service = AcquisitionService(session, provider)
    return service.acquire(acquisition_source)
