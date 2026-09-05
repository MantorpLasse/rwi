"""Manual local-file acquisition provider (RWI HQ "Manual File Acquisition
Provider + CLI" mission).

    human manually downloads an exact official document in a normal
    browser (because the publisher's own site blocks RWI's governed HTTP
    acquisition client - e.g. flylouisville.com's confirmed 403s)
        -> ManualFileAcquisitionProvider (reads the exact local bytes,
           NO network access of any kind)
        -> the existing, completely unmodified
           app.services.acquisition.AcquisitionService.acquire()
        -> AcquisitionRun + immutable Snapshot
        -> STOP

THIS IS NOT A BYPASS. This module never fetches anything, never talks to
the publisher's host, never spoofs a User-Agent, never retries a blocked
request, and never treats a third-party mirror as the source of truth. It
only lets a human's own, already-completed, ordinary browser download
enter the exact same governed Snapshot pipeline every network-based fetch
already uses - the SAME AcquisitionService, the SAME Snapshot model, the
SAME hashing/dedup, the SAME extraction pipeline downstream. It writes
ONLY acquisition metadata (PublishingSource/AcquisitionSource/
AcquisitionRun) and an immutable Snapshot - never Source, SourceAssertion,
Signal, ReviewerAction, or any governance row, exactly like
app.services.generic_web_fetch.fetch_discovered_url() for a real HTTP
fetch.

PROVENANCE HONESTY (the reason this module exists as a separate,
dedicated provider rather than reusing GenericWebAcquisitionProvider with
a fake response): manual acquisition must be distinguishable from HTTP
acquisition from persisted metadata alone, and must never fabricate HTTP
provenance that did not actually happen inside RWI. Concretely:

  - `AcquisitionRun.provider_version` = "manual-file/1" - a version string
    in the exact same style as every other provider ("faa-http/1",
    "generic-web-http/1"), immediately distinguishing a manual run from
    any HTTP-based one in an audit trail, with zero new schema needed.
  - `AcquisitionRun.http_status` = NULL - no real HTTP response was ever
    observed by RWI, so no status code is recorded, real or fabricated.
    (AcquisitionPayload.http_status was widened from `int` to `int | None`
    in app/acquisition/faa.py for exactly this - see that dataclass's own
    updated docstring/comment.)
  - `AcquisitionRun.response_headers` = "{}" (an empty, real JSON object,
    from an empty `retrieved_headers` dict) - an honest "zero headers were
    observed" representation, never invented plausible-looking header
    values. This is the existing, unmodified
    `json.dumps(result.retrieved_headers, ...)` call inside
    AcquisitionService.acquire() itself; nothing about that call changes.
  - `AcquisitionRun.duration_seconds` = 0.0 - no network operation
    occurred inside RWI to measure a duration for; 0.0 also satisfies the
    existing `duration_seconds >= 0` CHECK constraint honestly (no fetch
    took negative time; none took HTTP time at all).
  - `AcquisitionRun.request_url` / `final_url` = the SAME caller-supplied
    original official URL. Both DB columns already exist to answer "what
    URL is this content attributed to", and for a real HTTP fetch with no
    redirects they are already identical in practice; using the one real
    URL for both here (rather than inventing a fictitious "redirect
    chain") is the honest reading of what these two fields mean for
    content that never actually traveled over HTTP through RWI at all.
  - Content type is NEVER sniffed or guessed from a filename extension
    (no MIME-detection framework is introduced here) - it is always the
    caller's own explicit, human-supplied value (see
    scripts/ingest_local_file.py's required --content-type).

Exact bytes pass through completely unchanged: this provider never
hashes (AcquisitionService.acquire() already owns SHA-256 hashing
identically for every provider), never rewrites, never re-encodes, never
touches PDF metadata, never mutates the original file on disk (opened
read-only), and performs no HTTP, no persistence, and no database access
of any kind itself - it is a pure, dependency-free bytes-in/
AcquisitionPayload-out object satisfying the exact same
app.acquisition.faa.AcquisitionProvider structural Protocol every other
provider already satisfies, so app.services.acquisition.AcquisitionService
needs zero changes to accept it.

`ingest_local_file()` below is the thin, additive orchestration this
module also owns (mirroring, not duplicating,
app.services.generic_web_fetch.fetch_discovered_url()'s own
get-or-create -> commit -> AcquisitionService.acquire() shape for the
network case): it reuses
app.services.generic_web_fetch.get_or_create_publishing_source_for_hostname()
and the SAME deterministic, hash-based AcquisitionSource keying
(app.services.generic_web_fetch._acquisition_source_key_for_url(),
imported rather than reimplemented - so the exact same real-world URL
resolves to the exact same AcquisitionSource row whether it is ever
fetched over HTTP, ingested manually, or both, at different times), never
duplicating that logic. The one deliberate difference from
get_or_create_acquisition_source_for_url() (that function itself is left
completely unmodified) is `acquisition_type="manual_file"` on a freshly
created AcquisitionSource, instead of that function's own hardcoded
"http" - an honest label, in the exact same free-text, never-branched-on
style already established by capture_faa_emas.py's own "tableau" value
alongside generic_web_fetch.py's "http".

Never commits more than the existing AcquisitionService.acquire()/
get-or-create calls already do; never imports Source/SourceAssertion/
Signal/ReviewerAction/FH-D4 machinery of any kind.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.acquisition.faa import AcquisitionPayload
from app.models import AcquisitionRun, AcquisitionSource, PublishingSource
from app.services.acquisition import AcquisitionService
from app.services.generic_web_fetch import (
    _acquisition_source_key_for_url,
    get_or_create_publishing_source_for_hostname,
)

PROVIDER_VERSION = "manual-file/1"

__all__ = [
    "PROVIDER_VERSION",
    "ManualFileAcquisitionError",
    "ManualFileAcquisitionProvider",
    "get_or_create_manual_acquisition_source",
    "ingest_local_file",
]


class ManualFileAcquisitionError(ValueError):
    """Raised for any fail-closed condition in this module: a missing
    original_url/content_type, a missing/non-regular/empty local file, or
    a URL with no hostname. A ValueError subclass so it flows through
    app.services.acquisition.AcquisitionService's own existing
    `_failure_status()` exactly like FAAAcquisitionProvider's own
    "empty payload" ValueError does - AcquisitionRunStatus.INVALID_RESPONSE,
    with a real, honest failed AcquisitionRun row recorded (never silently
    swallowed), no new failure-classification code needed."""


class ManualFileAcquisitionProvider:
    """Satisfies app.acquisition.faa.AcquisitionProvider structurally.
    Reads exactly one local file's bytes, once, on `retrieve()` - never
    eagerly in `__init__`, matching every other provider's own
    lazy-until-acquire() convention."""

    version = PROVIDER_VERSION

    def __init__(self, *, original_url: str, local_path: "str | Path", content_type: str) -> None:
        if not original_url or not original_url.strip():
            raise ManualFileAcquisitionError("original_url is required and cannot be blank")
        if not content_type or not content_type.strip():
            raise ManualFileAcquisitionError(
                "content_type is required and cannot be blank - never silently sniffed from a filename extension"
            )
        self.source_url = original_url
        self._local_path = Path(local_path)
        self._content_type = content_type

    def retrieve(self) -> AcquisitionPayload:
        if not self._local_path.exists():
            raise ManualFileAcquisitionError(f"No such file: {self._local_path}")
        if not self._local_path.is_file():
            raise ManualFileAcquisitionError(f"Not a regular file: {self._local_path}")
        content = self._local_path.read_bytes()
        if not content:
            raise ManualFileAcquisitionError(f"Local file is empty: {self._local_path}")

        return AcquisitionPayload(
            content=content,
            request_url=self.source_url,
            final_url=self.source_url,
            retrieved_headers={},
            http_status=None,
            content_type=self._content_type,
            duration_seconds=0.0,
            provider_version=self.version,
        )


def get_or_create_manual_acquisition_source(
    session: Session, url: str, publishing_source: PublishingSource,
) -> "tuple[AcquisitionSource, bool]":
    """Get-or-create, keyed by the SAME deterministic hash-based key
    app.services.generic_web_fetch.get_or_create_acquisition_source_for_url()
    uses for a network fetch of this same URL - imported, never
    reimplemented, so the two paths can never independently drift into two
    different keys for the same real-world URL. Only the freshly-created
    row's `acquisition_type` differs ("manual_file" instead of "http") -
    that function itself is left completely unmodified."""
    key = _acquisition_source_key_for_url(url)
    existing = session.scalar(select(AcquisitionSource).where(AcquisitionSource.key == key))
    if existing is not None:
        return existing, False
    if len(url) > 1000:
        raise ManualFileAcquisitionError(
            f"URL exceeds the 1000-character AcquisitionSource.canonical_url column limit: {url!r}"
        )
    display_name = url if len(url) <= 200 else url[:197] + "..."
    acquisition_source = AcquisitionSource(
        publishing_source=publishing_source,
        key=key,
        display_name=display_name,
        acquisition_type="manual_file",
        canonical_url=url,
        expected_media_type=None,
        active=True,
    )
    session.add(acquisition_source)
    session.flush()
    return acquisition_source, True


def ingest_local_file(
    session: Session, *, url: str, local_path: "str | Path", content_type: str,
) -> AcquisitionRun:
    """The single entry point scripts/ingest_local_file.py calls. Mirrors
    app.services.generic_web_fetch.fetch_discovered_url()'s own shape for
    the network case: get-or-create PublishingSource/AcquisitionSource,
    commit those rows first (so a later provider failure can never orphan
    them - the exact same ordering rationale that function's own docstring
    already documents), then delegate to the existing, unmodified
    AcquisitionService.acquire(). No SSRF/robots checks are performed -
    both are meaningless for a file a human already legitimately holds
    locally, never fetched by RWI at all."""
    if not url or not url.strip():
        raise ManualFileAcquisitionError("url is required and cannot be blank")
    hostname = urlsplit(url).hostname
    if not hostname:
        raise ManualFileAcquisitionError(f"url has no hostname: {url!r}")

    publishing_source, _ = get_or_create_publishing_source_for_hostname(session, hostname)
    acquisition_source, _ = get_or_create_manual_acquisition_source(session, url, publishing_source)
    session.commit()

    provider = ManualFileAcquisitionProvider(original_url=url, local_path=local_path, content_type=content_type)
    service = AcquisitionService(session, provider)
    return service.acquire(acquisition_source)
