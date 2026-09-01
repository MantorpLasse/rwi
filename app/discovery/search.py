"""Provider-neutral search execution boundary (Mission #9D, Parts F + H).

Mirrors the existing provider-object convention this codebase already
uses for fetching (app.acquisition.faa.FAAAcquisitionProvider,
app.acquisition.mac_granicus): a SearchProvider is any object exposing a
`name` and a `search(query) -> SearchOutcome` method. Nothing here has a
database, Source/SourceAssertion, or Signal/Installation dependency of
any kind - this boundary is upstream and read-only by construction.

Failure semantics (Part H): a provider that could not legitimately answer
(timeout, HTTP error, malformed response, missing credentials at call
time, etc.) MUST return SearchOutcomeStatus.PROVIDER_FAILURE with a
human-readable `error`, never an empty `results` tuple with status OK.
Collapsing "the provider failed" into "the provider found nothing" would
silently hide real problems from a human reviewer - exactly the kind of
silent failure this codebase's acquisition layer (AcquisitionRunStatus)
already refuses to allow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol, runtime_checkable

from app.discovery.query import SearchQuery


@dataclass(frozen=True)
class SearchResult:
    """One result row from one SearchProvider answering one SearchQuery."""

    query: SearchQuery
    rank: int
    title: str
    url: str
    snippet: str
    discovered_at: datetime
    provider: str


class SearchOutcomeStatus(str, Enum):
    OK = "OK"
    NO_RESULTS = "NO_RESULTS"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"


@dataclass(frozen=True)
class SearchOutcome:
    """The full outcome of one SearchProvider.search(query) call.

    Exactly one of the following is the meaningful case, selected by
    `status`:
      - OK: `results` holds one or more SearchResult rows.
      - NO_RESULTS: the provider legitimately answered and found nothing.
        `results` is empty. This is a real, useful finding (matches
        Mission #9D Part H's requirement that NO_RESULTS never be
        confused with failure).
      - PROVIDER_FAILURE: the provider could not legitimately answer.
        `results` is empty and `error` describes what went wrong.
    """

    query: SearchQuery
    status: SearchOutcomeStatus
    results: tuple[SearchResult, ...] = field(default_factory=tuple)
    error: str | None = None

    def __post_init__(self) -> None:
        if self.status == SearchOutcomeStatus.PROVIDER_FAILURE and not self.error:
            raise ValueError(
                "SearchOutcome with status=PROVIDER_FAILURE must carry a non-empty "
                "error message - a failure must never be indistinguishable from an "
                "empty successful search."
            )
        if self.status != SearchOutcomeStatus.PROVIDER_FAILURE and self.error:
            raise ValueError(
                f"SearchOutcome with status={self.status} must not carry an error "
                "message - only PROVIDER_FAILURE carries one."
            )
        if self.status == SearchOutcomeStatus.OK and not self.results:
            raise ValueError(
                "SearchOutcome with status=OK must carry at least one result - use "
                "NO_RESULTS for a legitimate empty answer."
            )
        if self.status == SearchOutcomeStatus.NO_RESULTS and self.results:
            raise ValueError(
                "SearchOutcome with status=NO_RESULTS must not carry any results."
            )


@runtime_checkable
class SearchProvider(Protocol):
    """Provider-neutral search boundary. Any object exposing `name` and
    `search()` in this shape may be used - no inheritance required,
    matching the existing duck-typed provider convention in
    app.acquisition."""

    name: str

    def search(self, query: SearchQuery) -> SearchOutcome: ...
