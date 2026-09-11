"""Read-only official-domain derivation for Discovery (RWI HQ "Official-
Domain Document Discovery Pass" mission, following that mission's own
design recon, "RWI HQ Research Loop - Official-Domain / PDF Discovery
Improvement").

    airport_id
        -> get_known_official_hostnames() (pure read, one Session)
        -> deterministic tuple[str, ...] of normalized hostnames
        -> STOP

WHY THIS SEAM AND NO OTHER (design recon Part C): AirportIdentity
(app.discovery.identity) deliberately carries no website/domain field -
operator domains are intentionally left unclassified rather than guessed
from an airport's name (see app.discovery.triage's own REGULATOR_DOMAINS/
VENDOR_CONTRACTOR_DOMAINS comment: "AirportIdentity carries no website
field to check against, so operator domains are intentionally left
unclassified in V1, not guessed"). This module does not change that - it
adds no new field to AirportIdentity and invents no domain registry. It
only reads a hostname back out of ALREADY-GOVERNED evidence that already
exists: a Source row this Airport's own SourceAssertion or Signal rows
already point at, whose `reliability_level` is already "official" (the
same column/value every other governed reliability gate in this codebase
already uses - see app.services.airport_alias.py and
app.services.airport_identifier.py's own `InsufficientSourceReliabilityError`
checks for the identical convention). Nothing here is persisted,
guessed, scraped, or added as a new column/model - this is a pure,
read-only derivation over data that is already there.

SAFETY / WHAT THIS IS NOT (design recon Part C.4's own explicit risk
list, carried forward here):
  - Not proof of a single "the" official domain - an airport can
    legitimately have more than one governed official Source on more than
    one hostname (e.g. a state DOT AND the airport authority itself); this
    function returns ALL of them, deterministically ordered, and takes no
    position on which one is "more official."
  - Not usable to bootstrap a brand-new, not-yet-researched airport - it
    only ever returns something once at least one official-reliability
    Source already exists for that airport_id.
  - Never treated as evidence, identity, or reliability information
    itself downstream - callers (see app.services.research_loop's own
    `official_domain` parameter) use it only to raise discovery PRIORITY
    of a bounded, additional search pass and a small triage score bonus -
    exactly the same non-evidentiary role app.discovery.triage's existing
    curated REGULATOR_DOMAINS/VENDOR_CONTRACTOR_DOMAINS already play.

Zero writes: this module only ever executes SELECT statements against the
caller-supplied Session; it never calls session.add()/session.commit()/
session.flush(), and never imports anything from app.services.
*_persistence or governance modules.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Signal, Source, SourceAssertion

__all__ = ["get_known_official_hostnames"]

_OFFICIAL_RELIABILITY_LEVEL = "official"
_WWW_PREFIX = "www."


def _hostname_from_url(url: "str | None") -> "str | None":
    """Fail-closed: returns None (never raises) for a missing, empty, or
    malformed URL - a malformed Source.url must never abort discovery for
    every other, well-formed official Source the airport may also have.
    Lowercases, strips a leading "www.", and drops any port suffix."""
    if not url:
        return None
    try:
        netloc = urlsplit(url.strip()).netloc
    except ValueError:
        return None
    host = netloc.lower().split(":")[0]
    if not host:
        return None
    if host.startswith(_WWW_PREFIX):
        host = host[len(_WWW_PREFIX):]
    return host or None


def get_known_official_hostnames(session: Session, airport_id: int) -> "tuple[str, ...]":
    """Deterministic (alphabetically sorted), deduplicated tuple of known
    official hostnames for `airport_id`, derived ONLY from Source rows
    that are:

      1. reliability_level == "official", AND
      2. reachable from this airport via an EXISTING governed
         relationship - either a SourceAssertion.airport_id == airport_id
         row's own source_id, or a Signal.airport_id == airport_id row's
         own source_id (a Signal may have source_id=None; that is simply
         skipped, never an error).

    Read-only: exactly two SELECTs plus a final SELECT for the matching
    Source rows' URLs - no ORM object is ever added, flushed, or
    committed. Returns an empty tuple (never raises) when the airport has
    no governed official Source yet, or when every candidate URL fails to
    parse to a usable hostname - both are normal, expected states, not
    errors.
    """
    source_ids: set[int] = set(
        session.scalars(
            select(SourceAssertion.source_id).where(SourceAssertion.airport_id == airport_id)
        ).all()
    )
    source_ids.update(
        session.scalars(
            select(Signal.source_id).where(
                Signal.airport_id == airport_id, Signal.source_id.is_not(None)
            )
        ).all()
    )
    if not source_ids:
        return ()

    urls = session.scalars(
        select(Source.url).where(
            Source.id.in_(source_ids), Source.reliability_level == _OFFICIAL_RELIABILITY_LEVEL
        )
    ).all()

    hostnames: list[str] = []
    seen: set[str] = set()
    for url in urls:
        host = _hostname_from_url(url)
        if host is None or host in seen:
            continue
        seen.add(host)
        hostnames.append(host)

    return tuple(sorted(hostnames))
