"""Conservative runtime deduplication of SearchResults (Mission #9D, Part I).

Two layers, both required by the mission, both implemented by the same
normalize_url() function (exact-URL duplicates are a strict subset of
normalized-URL duplicates - two byte-identical URLs always normalize
identically, so one pass covers both required cases):

  - exact URL match
  - normalized URL match (scheme/host case, trailing slash, query-param
    order, fragment)

What this module deliberately does NOT do: merge two results that only
share a title. A shared title is, at most, a hint for a LATER triage
stage (Mission #9C Part H) - never grounds for silently discarding a
result here. See test_discovery_search_dedup.py for an explicit
same-title/different-URL case proving this.

Query provenance across a deduplicated URL is preserved: if the same
normalized URL is (re-)discovered by more than one SearchQuery, every
query that found it is retained, in first-discovery order - this
provenance is exactly what a human reviewer needs to judge how strongly
a candidate source keeps coming up.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.discovery.query import SearchQuery
from app.discovery.search import SearchResult


def normalize_url(url: str) -> str:
    """Conservative URL normalization for deduplication only - never used
    to infer relevance or identity. Lowercases scheme and host, drops a
    trailing slash on a non-root path, drops the fragment, and sorts query
    parameters so the same resource requested with reordered params
    normalizes identically. Does NOT strip query parameters (that risks
    conflating genuinely different pages) and does NOT follow redirects."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return urlunsplit((scheme, netloc, path, query, ""))


@dataclass(frozen=True)
class DedupedResult:
    """One retained SearchResult (the first one seen for its normalized
    URL) plus every SearchQuery that (re-)discovered that same URL, in
    first-discovery order."""

    result: SearchResult
    found_by: tuple[SearchQuery, ...] = field(default_factory=tuple)


def deduplicate_results(results: list[SearchResult]) -> list[DedupedResult]:
    """Deduplicate a list of SearchResult (assumed to already be in the
    order they were discovered, e.g. query-plan order then provider rank)
    by normalized URL. The first-seen SearchResult for a given normalized
    URL is retained as-is (its own rank/title/snippet/provider/
    discovered_at); every query - including the first one - that produced
    a result at that normalized URL is recorded in `found_by`, in the
    order first encountered. Never merges on title alone."""
    order: list[str] = []
    retained: dict[str, SearchResult] = {}
    found_by: dict[str, list[SearchQuery]] = {}
    seen_queries: dict[str, set[tuple]] = {}

    for result in results:
        key = normalize_url(result.url)
        if key not in retained:
            retained[key] = result
            found_by[key] = []
            seen_queries[key] = set()
            order.append(key)
        query_key = (
            result.query.template_id,
            result.query.identity_field,
            result.query.identity_value,
        )
        if query_key not in seen_queries[key]:
            seen_queries[key].add(query_key)
            found_by[key].append(result.query)

    return [
        DedupedResult(result=retained[key], found_by=tuple(found_by[key]))
        for key in order
    ]
