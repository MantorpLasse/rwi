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

from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Signal, Source, SourceAssertion

__all__ = [
    "get_known_official_hostnames",
    "HostnameRanking",
    "rank_official_hostnames",
    "select_preferred_official_hostname",
]

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


# --- Preferred-domain selection (RWI HQ "Official-Domain Discovery -
# Smarter Domain Selection" mission) -----------------------------------------
#
# NOT A TRUST RANKING (mission's own explicit design principle): every
# candidate hostname already passed get_known_official_hostnames()'s own
# reliability_level=="official" governance gate above - this section never
# re-litigates whether a domain is trustworthy. It only answers "which of
# these already-governed domains is most useful to search FIRST for
# airport-specific project intelligence", using signals this repository
# already treats as meaningful, in ascending order of how much has been
# governed about that domain for THIS airport:
#
#   1. Backs at least one PUBLISHED Signal (Signal.published == True).
#      Signal.published is the single, authoritative, denormalized
#      current-publication-state boolean - see
#      app.services.signal_publication's own module docstring, "CURRENT-
#      STATE STRATEGY (Phase 3)": "Signal.published remains the single
#      denormalized, current-state boolean the static exporter already
#      reads... SignalPublicationAction is a pure audit log... never
#      re-derived into Signal.published." There is no more-authoritative
#      seam to prefer instead - this module reads that same boolean
#      directly, read-only, exactly as the static exporter itself does.
#   2. Backs at least one Signal at all (published or not) - a human has
#      already promoted SOME evidence from this domain into governed
#      project intelligence, even if not yet published.
#   3. Backs at least one SourceAssertion with review_state == "reviewed" -
#      review_state's own existing, narrow, binary vocabulary
#      ("unreviewed"/"reviewed" - see the model's own CheckConstraint and
#      app.services.human_review_queue's/app.services.fleet_health_review_rules's
#      own existing usage) already means exactly "a human has looked at
#      this SourceAssertion" - reused verbatim here, never reinterpreted,
#      and never conflated with intelligence_review_decision/
#      promotion_policy_decision/identity_guard_decision (different
#      columns, different governance questions, untouched by this
#      module).
#   4. More airport-linked official Source rows on that domain (a modest
#      relevance nudge only, per the mission's own instruction - a wider
#      base of governed official evidence on one domain is a weak but
#      real signal of relevance).
#   5. Alphabetical hostname - the final, fully deterministic tiebreak
#      (get_known_official_hostnames()'s own pre-existing ordering).
#
# Every count below is computed from exactly the same already-governed
# Source/SourceAssertion/Signal rows get_known_official_hostnames() already
# reads - no new query family, no additional governance table, and (per
# the mission's own firewall) this selection is NEVER written back
# anywhere; it is recomputed fresh, read-only, on every call.


@dataclass(frozen=True)
class HostnameRanking:
    """One candidate hostname's ranking inputs and the resulting
    human-readable reason - diagnostic/explainability only (mirrors this
    codebase's own established "why did this happen" discipline, e.g.
    app.discovery.query.SearchQuery's template_id/identity_field/
    identity_value). Never persisted, never treated as evidence."""

    hostname: str
    published_signal_count: int
    signal_count: int
    reviewed_source_assertion_count: int
    official_source_count: int
    reason: str


def _reason_for(ranking_inputs: "tuple[int, int, int, int]") -> str:
    published_signal_count, signal_count, reviewed_sa_count, official_source_count = ranking_inputs
    if published_signal_count > 0:
        return "source domain used by published Signal(s)"
    if signal_count > 0:
        return "source domain used by Signal(s) (not yet published)"
    if reviewed_sa_count > 0:
        return "source domain used by reviewed SourceAssertion(s)"
    if official_source_count > 1:
        return "most airport-linked official Source(s) on this domain"
    return "alphabetical tiebreak (no distinguishing governed Signal/review found)"


def rank_official_hostnames(session: Session, airport_id: int) -> "tuple[HostnameRanking, ...]":
    """Deterministic, read-only ranking of get_known_official_hostnames()'s
    own output, most-preferred first - see module section docstring above
    for the exact, fixed priority order. Returns an empty tuple (never
    raises) when the airport has no governed official domain at all -
    the same safe fallback get_known_official_hostnames() itself uses.

    Every count is computed from Source rows already reachable via an
    existing SourceAssertion.airport_id or Signal.airport_id row for this
    airport (the same governed relationship get_known_official_hostnames()
    itself requires) - never a new relationship, never a guess.
    """
    hostnames = get_known_official_hostnames(session, airport_id)
    if not hostnames:
        return ()

    # hostname -> the set of official Source ids that resolve to it, so a
    # hostname backed by more than one Source row is counted once per
    # governed fact, not once per Source row.
    source_rows = session.execute(
        select(Source.id, Source.url).where(
            Source.id.in_(
                set(
                    session.scalars(
                        select(SourceAssertion.source_id).where(SourceAssertion.airport_id == airport_id)
                    ).all()
                )
                | set(
                    session.scalars(
                        select(Signal.source_id).where(
                            Signal.airport_id == airport_id, Signal.source_id.is_not(None)
                        )
                    ).all()
                )
            ),
            Source.reliability_level == _OFFICIAL_RELIABILITY_LEVEL,
        )
    ).all()

    source_ids_by_hostname: "dict[str, set[int]]" = {h: set() for h in hostnames}
    for source_id, url in source_rows:
        host = _hostname_from_url(url)
        if host in source_ids_by_hostname:
            source_ids_by_hostname[host].add(source_id)

    all_official_source_ids = {sid for ids in source_ids_by_hostname.values() for sid in ids}

    published_signal_rows = session.execute(
        select(Signal.source_id).where(
            Signal.airport_id == airport_id,
            Signal.source_id.in_(all_official_source_ids),
            Signal.published.is_(True),
        )
    ).all()
    all_signal_rows = session.execute(
        select(Signal.source_id).where(
            Signal.airport_id == airport_id, Signal.source_id.in_(all_official_source_ids),
        )
    ).all()
    reviewed_sa_rows = session.execute(
        select(SourceAssertion.source_id).where(
            SourceAssertion.airport_id == airport_id,
            SourceAssertion.source_id.in_(all_official_source_ids),
            SourceAssertion.review_state == "reviewed",
        )
    ).all()

    def _count_per_hostname(rows: "list") -> "dict[str, int]":
        counts: "dict[str, int]" = {h: 0 for h in hostnames}
        for (source_id,) in rows:
            for host, ids in source_ids_by_hostname.items():
                if source_id in ids:
                    counts[host] += 1
        return counts

    published_counts = _count_per_hostname(published_signal_rows)
    signal_counts = _count_per_hostname(all_signal_rows)
    reviewed_counts = _count_per_hostname(reviewed_sa_rows)

    rankings = []
    for host in hostnames:
        inputs = (
            published_counts[host], signal_counts[host], reviewed_counts[host], len(source_ids_by_hostname[host]),
        )
        rankings.append(
            HostnameRanking(
                hostname=host,
                published_signal_count=inputs[0],
                signal_count=inputs[1],
                reviewed_source_assertion_count=inputs[2],
                official_source_count=inputs[3],
                reason=_reason_for(inputs),
            )
        )

    rankings.sort(
        key=lambda r: (
            -r.published_signal_count, -r.signal_count, -r.reviewed_source_assertion_count,
            -r.official_source_count, r.hostname,
        )
    )
    return tuple(rankings)


def select_preferred_official_hostname(session: Session, airport_id: int) -> "str | None":
    """The single entry point callers (e.g. scripts/research_airport_clue.py)
    should use in place of "take the alphabetically-first known hostname" -
    read-only, deterministic, fails closed to None when the airport has no
    governed official domain at all. See rank_official_hostnames() for the
    full diagnostic ranking (all candidates, their raw counts, and each
    one's own reason) when that detail is useful to show a human."""
    ranked = rank_official_hostnames(session, airport_id)
    return ranked[0].hostname if ranked else None
