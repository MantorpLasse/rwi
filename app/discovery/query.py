"""SearchQuery runtime model and deterministic Search Plan builder
(Mission #9D, Parts D + E).

Query generation is deliberately template-based and deterministic, not
AI-generated: every SearchQuery carries the template id and the exact
identity field/value that produced it, so a human can always reconstruct
"why did RWI search for this text" after the fact - the same explainability
discipline every governed-evidence pathway in this codebase already
enforces for claims (see app/services/manual_claim_evidence.py).

Local-language expansion is explicitly out of scope for V1 (see Mission
#9C Part F) - this module only ever renders English-language template
text against the identity fields it is given.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.discovery.identity import AirportIdentity


@dataclass(frozen=True)
class SearchQuery:
    """One deterministically-generated search query, with full provenance.

    `rendered` is the literal text a SearchProvider is asked to search.
    `template_id`/`identity_field`/`identity_value` make the query fully
    reconstructable and explainable - required so a human reviewer can
    always answer "why did this query exist" without guessing.
    """

    rendered: str
    template_id: str
    # Not an enforced enum - a plain str used as a self-documenting label.
    # This module's own build_search_plan() only ever uses "name" |
    # "iata_code" | "icao_code"; other callers elsewhere in Discovery
    # reuse this same type for their own identity/context fields (a
    # dimension-driven caller uses "name" for its own queries;
    # plan_official_domain_document_queries() below uses
    # "official_domain" for its already-governed domain input) - always
    # the field this query's search text was rendered from, whatever that
    # field happens to be.
    identity_field: str
    identity_value: str


# Deliberately small V1 concept set (Mission #9D Part E, verbatim from the
# mission brief). Each concept lists which identity fields plausibly
# improve discovery for it - "name" is used everywhere for broad semantic
# recall; short IATA/ICAO codes are added only where a code realistically
# narrows a technical/acronym-heavy search (EMAS, RESA). This avoids the
# low-value 8-concepts x 3-fields Cartesian product the mission explicitly
# warns against: 8 concepts, 12 queries, not 24.
_CONCEPT_PLAN: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("emas", "EMAS", ("name", "iata_code", "icao_code")),
    ("runway_safety", "runway safety", ("name",)),
    ("resa", "RESA", ("name", "iata_code", "icao_code")),
    ("arresting_system", "arresting system", ("name",)),
    ("runway_extension", "runway extension", ("name",)),
    ("procurement", "procurement", ("name",)),
    ("construction", "construction", ("name",)),
    ("regulator", "civil aviation regulator", ("name",)),
)

# Fixed evaluation order for identity fields within a concept - this,
# together with _CONCEPT_PLAN's own fixed order above, is what makes
# build_search_plan's output order deterministic and reproducible.
_FIELD_ORDER: tuple[str, ...] = ("name", "iata_code", "icao_code")


def build_search_plan(identity: AirportIdentity) -> list[SearchQuery]:
    """Pure, deterministic function: AirportIdentity -> ordered list of
    SearchQuery. Same input always produces the same output, in the same
    order. Never touches a database, network, or clock.

    Multi-word `name` values are rendered as an exact-phrase (quoted)
    query, matching the actual query shape used for LCY/YTZ real-world
    research this session (e.g. `"London City Airport" EMAS`). Short
    IATA/ICAO codes are rendered unquoted.
    """
    seen_rendered: set[str] = set()
    plan: list[SearchQuery] = []

    field_values = {
        "name": identity.name,
        "iata_code": identity.iata_code,
        "icao_code": identity.icao_code,
    }

    for template_id, phrase, allowed_fields in _CONCEPT_PLAN:
        for field_name in _FIELD_ORDER:
            if field_name not in allowed_fields:
                continue
            value = field_values.get(field_name)
            if not value:
                continue
            rendered = _render(field_name, value, phrase)
            if rendered in seen_rendered:
                # A code accidentally equal to another field's value (or a
                # degenerate short name) must never produce a duplicate
                # query - conservative, deterministic de-duplication at
                # generation time, ahead of any provider call.
                continue
            seen_rendered.add(rendered)
            plan.append(
                SearchQuery(
                    rendered=rendered,
                    template_id=template_id,
                    identity_field=field_name,
                    identity_value=value,
                )
            )

    return plan


def _render(field_name: str, value: str, phrase: str) -> str:
    if field_name == "name" and " " in value:
        return f'"{value}" {phrase}'
    return f"{value} {phrase}"


# RWI HQ "Official-Domain Document Discovery Pass" mission (following that
# mission's own design recon, "Research Loop - Official-Domain / PDF
# Discovery Improvement"): a small, fixed, deterministic document-genre
# query set for a SINGLE, already-governed official domain - see
# app.services.official_domain_discovery.get_known_official_hostnames(),
# which derives such a domain read-only from existing evidence. This
# function never derives, guesses, or validates a domain itself; it only
# renders queries for one the caller already knows is governed and
# official. Exactly 4 queries, fixed order, hard-capped - never
# multiplied by dimension count, anchor count, or number of known
# domains (the caller picks at most one domain per invocation - see
# app.services.research_loop.run_research_loop's own `official_domain`
# parameter and scripts/research_airport_clue.py's own
# --use-official-domain-discovery flag).
#
# Targets the exact document-genre gap the SDF/flylouisville.com
# benchmark exposed: the standard concept/dimension query vocabulary
# above never emits a site-restricted query, and never targets
# "presentation"/"capital program"/"board" document language - exactly
# the genre of the official LRAA "2024: A Year in Review and a Look
# Ahead" PDF the standard Research Loop missed.
_OFFICIAL_DOMAIN_DOCUMENT_QUERIES: tuple[tuple[str, str], ...] = (
    ("official_domain_document_emas", "EMAS"),
    ("official_domain_document_presentation", "EMAS presentation"),
    ("official_domain_document_capital_program", "EMAS capital program"),
    ("official_domain_document_board", "EMAS board"),
)


def plan_official_domain_document_queries(official_domain: str) -> "tuple[SearchQuery, ...]":
    """Pure, deterministic: the same `official_domain` always produces the
    same ordered tuple of exactly 4 SearchQuery objects -

        site:<domain> EMAS
        site:<domain> EMAS presentation
        site:<domain> EMAS capital program
        site:<domain> EMAS board

    `official_domain` is normalized to lowercase, stripped of surrounding
    whitespace; raises ValueError for an empty/blank domain (fail closed -
    matches AirportIdentity.__post_init__'s own "required field cannot be
    blank" discipline) rather than silently emitting a meaningless
    `site: EMAS` query. Never touches a network, database, or clock.
    """
    domain = (official_domain or "").strip().lower()
    if not domain:
        raise ValueError("official_domain must be a non-empty string")
    return tuple(
        SearchQuery(
            rendered=f"site:{domain} {phrase}",
            template_id=template_id,
            identity_field="official_domain",
            identity_value=domain,
        )
        for template_id, phrase in _OFFICIAL_DOMAIN_DOCUMENT_QUERIES
    )


# RWI HQ "Airport Official-Domain Discovery Query Pass" mission (following
# that mission's own design recon, "Discovering Airport/Operator Official
# Domains Safely"): a small, fixed, deterministic query set for the OPPOSITE
# situation from plan_official_domain_document_queries() above - here, no
# official domain is known (or the only known one(s) are generic multi-
# tenant portals) and the goal is to FIND a candidate, never to search
# within one already known. This function never derives, guesses, or
# validates a hostname itself - it only renders search text; every
# candidate hostname a caller acts on must come from a literal returned
# SearchResult.url (see app.discovery.official_domain_candidates, which
# consumes this function's output).
#
# Exactly 1-3 queries, fixed order, hard-capped at 3 - never multiplied,
# never a second round, never AI-guessed. Accepts plain name/iata_code/
# icao_code strings rather than the AirportSearchContext type the caller
# already holds (see app/services's own airport search-context helper),
# deliberately: this module (app/discovery/query.py) must stay free of
# any app.services dependency (this package's own established "upstream
# and read-only" discipline - see
# tests/test_discovery_architectural_safety.py) - the caller (e.g.
# scripts/research_airport_clue.py) already holds an AirportSearchContext
# and passes its three fields through directly.
#
# The name-based query DELIBERATELY always includes the literal word
# "airport" (RWI HQ's own live-recon finding, mission's own explicit
# instruction): a bare "<name> official website" query for a single-word,
# city-named airport (e.g. "Memphis") returns overwhelmingly city/tourism/
# airline noise, never the airport's own site - real live evidence from the
# design recon, not a hypothetical. Deliberately excludes "authority"/
# "board"/"municipal"/"FAA"/any country-specific institutional vocabulary -
# those bias toward US/Commonwealth naming conventions and are not
# required for this to work internationally (the recon's own live
# Wellington/NZ check found the real operator domain using only this exact
# query shape).
def plan_airport_official_domain_discovery_queries(
    name: str, *, iata_code: "str | None" = None, icao_code: "str | None" = None,
) -> "tuple[SearchQuery, ...]":
    """Pure, deterministic: the same (name, iata_code, icao_code) always
    produces the same ordered tuple of 1-3 SearchQuery objects -

        "<name>" airport official website          (always)
        <IATA> airport official                     (only if iata_code given)
        <ICAO> airport official                     (only if icao_code given)

    `name` is required and fails closed (ValueError) if blank - matches
    AirportIdentity.__post_init__'s own "required field cannot be blank"
    discipline. `iata_code`/`icao_code` are optional enrichments, each
    stripped of surrounding whitespace; a blank string is treated the same
    as None (simply omitted, never an empty/meaningless query). Never
    touches a network, database, or clock.
    """
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValueError("name must be a non-empty string")

    rendered_name = f'"{clean_name}"' if " " in clean_name else clean_name
    queries = [
        SearchQuery(
            rendered=f"{rendered_name} airport official website",
            template_id="airport_official_domain_discovery_name",
            identity_field="name",
            identity_value=clean_name,
        )
    ]

    clean_iata = (iata_code or "").strip()
    if clean_iata:
        queries.append(
            SearchQuery(
                rendered=f"{clean_iata} airport official",
                template_id="airport_official_domain_discovery_iata",
                identity_field="iata_code",
                identity_value=clean_iata,
            )
        )

    clean_icao = (icao_code or "").strip()
    if clean_icao:
        queries.append(
            SearchQuery(
                rendered=f"{clean_icao} airport official",
                template_id="airport_official_domain_discovery_icao",
                identity_field="icao_code",
                identity_value=clean_icao,
            )
        )

    return tuple(queries)
