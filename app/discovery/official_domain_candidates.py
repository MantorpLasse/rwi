"""Candidate official-domain grouping and noise suppression (RWI HQ
"Airport Official-Domain Discovery Query Pass" mission, following that
mission's own design recon, "Discovering Airport/Operator Official Domains
Safely").

    triaged discovery results (from app.discovery.query's own
    plan_airport_official_domain_discovery_queries())
        -> group_candidates_by_hostname() (pure, no network, no database)
        -> (candidates, suppressed_noise_hostnames)
        -> STOP

DISCOVERY ONLY - not evidence, not governance. Every OfficialDomainCandidate
here is a runtime-only, non-persisted grouping of already-existing
SearchResult/TriagedResult objects - it is never itself evidence, never
itself an official domain, and never itself governed. SearchResult remains
SearchResult; a candidate becomes a governed official domain only through
the existing, separate, human-initiated Fetch -> Source -> SourceAssertion
path (see scripts/research_airport_clue.py's own --discover-official-domain
help text for the exact handoff) - nothing in this module creates,
persists, or promotes anything.

KNOWN_NOISE_DOMAINS is candidate SUPPRESSION only, and is a DIFFERENT
concept from app.services.official_domain_discovery.
GENERIC_MULTI_TENANT_PORTAL_HOSTNAMES: that set holds already-GOVERNED,
fully valid official domains that are merely deprioritized for further
discovery; THIS set holds domains that are never a governed official
airport/operator/authority/regulator source at all (flight trackers,
social media, encyclopedias, airline sites, directory aggregators) - they
are excluded from the candidate list outright, not merely ranked lower.
Deliberately small and version-controlled, built ONLY from hostnames
actually observed as false positives during the design recon's own live
sampling (never a broad TLD/pattern rule, never guessed) - see each
entry's own comment. Government/regulator/airport-operator domains
(FAA broadly, local government, airport authorities, any other generic
governed official portal) are NEVER added here - this module has no
opinion about them at all; they simply pass through ungrouped-as-noise
like any other non-noise hostname.

Query convergence (how many distinct queries found a hostname) is used
ONLY as a secondary sort key among already-non-noise candidates, never as
proof of officialness and never as a way to promote a noise-domain hit
into a "strong" candidate - see group_candidates_by_hostname()'s own
docstring for the exact ordering.

This module performs zero database access, zero network access, zero LLM/
randomization call, and imports no ORM model, no Session/engine
constructor, and no governance/persistence service - it is a pure
function over already-collected DedupedResult/TriagedResult objects,
exactly like app.discovery.triage/app.discovery.dedup themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from app.discovery.triage import PriorityBand, TriagedResult

__all__ = [
    "KNOWN_NOISE_DOMAINS",
    "OfficialDomainCandidate",
    "group_candidates_by_hostname",
]

# --- Known noise domains (RWI HQ design recon's own live-observed false
# positives - never a guess, never a broad pattern) --------------------------
#
# Every entry below was an ACTUAL result returned by a real live Brave query
# during the design recon's own bounded sample (MSP/CLE/MEM/EWR/CLT/LIT/WLG),
# for exactly the mission's own named categories. Kept deliberately small -
# "do not build a huge blacklist" is the mission's own explicit instruction.
KNOWN_NOISE_DOMAINS: frozenset[str] = frozenset(
    {
        # Encyclopedia
        "en.wikipedia.org",
        "wikipedia.org",
        # Social media
        "facebook.com",
        "twitter.com",
        "x.com",
        "linkedin.com",
        # Flight-tracking / aviation-hobbyist directories (never the
        # airport's own operator/authority site)
        "flightaware.com",
        "airnav.com",
        "flightradar24.com",
        "aopa.org",
        # Generic airport directory/guide aggregator (distinct from any
        # specific airport's own site)
        "airport.guide",
        # Airline domain, not an airport operator/authority (observed for
        # MEM/CLT: "aa.com" - American Airlines' own flight-information
        # page for that airport, not the airport's own site)
        "aa.com",
    }
)


@dataclass(frozen=True)
class OfficialDomainCandidate:
    """One non-persisted grouping of every discovered SearchResult sharing
    one normalized hostname - discovery-only, never evidence, never itself
    a governed official domain. `priority_band`/`reasons` are copied
    verbatim from that hostname's own best-banded TriagedResult (app.discovery.triage
    is not re-implemented or second-guessed here); `supporting_result_count`
    is the number of DISTINCT queries that converged on this hostname
    (secondary sort key only - see module docstring's own "query
    convergence" note)."""

    hostname: str
    best_url: str
    best_title: str
    best_snippet: str
    supporting_result_count: int
    priority_band: PriorityBand
    reasons: "tuple[str, ...]"


def _hostname_from_result_url(url: str) -> "str | None":
    """Independent copy of the same lowercase/strip-www/drop-port
    normalization app.services.official_domain_discovery._hostname_from_url()
    and app.services.official_hub_followup._hostname_of() already use -
    not imported (this module must stay free of any app.services
    dependency, matching app/discovery/'s own established "upstream and
    read-only" discipline) - the same reasoning, an independent copy, per
    this codebase's own "independent copy, same reasoning, zero
    cross-import" convention (see e.g.
    app.services.research_literal_anchors's own PATTERN PROVENANCE note)."""
    if not url:
        return None
    host = urlsplit(url).netloc.lower().split(":")[0]
    if not host:
        return None
    if host.startswith("www."):
        host = host[len("www."):]
    return host or None


_BAND_ORDER = {PriorityBand.HIGH: 0, PriorityBand.MEDIUM: 1, PriorityBand.LOW: 2}


def _is_known_noise(hostname: str) -> bool:
    return hostname in KNOWN_NOISE_DOMAINS or any(
        hostname.endswith("." + noise) for noise in KNOWN_NOISE_DOMAINS
    )


def group_candidates_by_hostname(
    triaged: "list[TriagedResult]",
) -> "tuple[tuple[OfficialDomainCandidate, ...], tuple[str, ...]]":
    """Pure, deterministic, no network/database access. Groups an already-
    triaged discovery result set (e.g. from running
    app.discovery.query.plan_airport_official_domain_discovery_queries()'s
    own queries through the existing SearchProvider -> deduplicate_results()
    -> triage_results() pipeline, completely unmodified) by normalized
    hostname.

    Returns `(candidates, suppressed_noise_hostnames)`:

      - `candidates`: one OfficialDomainCandidate per surviving (non-noise)
        hostname, sorted by (priority_band, -supporting_result_count,
        hostname) - existing triage banding is ALWAYS the primary key,
        never overridden by convergence count alone (mission's own
        explicit "do not treat query convergence alone as proof"
        instruction) - a noise-domain hit can never become a "strong"
        candidate merely by being found many times, because it never
        reaches this sorted list at all.
      - `suppressed_noise_hostnames`: every distinct hostname that WAS
        excluded because it matched KNOWN_NOISE_DOMAINS - for honest
        "N noise domains suppressed" reporting, never silently dropped
        with no trace.

    A hostname whose URL fails to parse at all is silently skipped (same
    fail-closed discipline every hostname-extraction helper in this
    codebase already uses) - never an error, never a crash.
    """
    groups: "dict[str, list[TriagedResult]]" = {}
    for t in triaged:
        host = _hostname_from_result_url(t.deduped.result.url)
        if host is None:
            continue
        groups.setdefault(host, []).append(t)

    candidates: "list[OfficialDomainCandidate]" = []
    suppressed: "list[str]" = []
    for host, items in groups.items():
        if _is_known_noise(host):
            suppressed.append(host)
            continue

        best = min(items, key=lambda t: _BAND_ORDER[t.band])
        supporting_queries = {q.rendered for item in items for q in item.deduped.found_by}
        candidates.append(
            OfficialDomainCandidate(
                hostname=host,
                best_url=best.deduped.result.url,
                best_title=best.deduped.result.title,
                best_snippet=best.deduped.result.snippet,
                supporting_result_count=len(supporting_queries),
                priority_band=best.band,
                reasons=best.reasons,
            )
        )

    candidates.sort(key=lambda c: (_BAND_ORDER[c.priority_band], -c.supporting_result_count, c.hostname))
    return tuple(candidates), tuple(sorted(suppressed))
