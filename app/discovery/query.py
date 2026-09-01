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
    identity_field: str  # "name" | "iata_code" | "icao_code"
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
