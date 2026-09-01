"""AirportIdentity - the runtime input model for Discovery (Mission #9D,
Part C).

This is deliberately NOT an ORM model. It is never persisted, never
written to the database, and carries no foreign key to any governed
table. It exists purely to describe "which airport are we searching the
public web for" in a shape that:

  1. can be built FROM a real, canonical, governed Airport row
     (AirportIdentity.from_airport), for an airport RWI already knows, and
  2. can be typed by hand for an airport RWI does NOT yet know about
     (the exact real-world case Missions #8B/#9A worked manually).

Both paths produce the identical runtime object; nothing downstream in
Discovery needs to know or care which path was used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - type-checking only, no runtime import
    from sqlalchemy.orm import Session

    from app.models.airport import Airport


@dataclass(frozen=True)
class AirportIdentity:
    """Immutable, non-persisted airport identity for Discovery.

    Only `name` is required. `iata_code`/`icao_code`/`city`/`country` are
    optional because a not-yet-known airport may not have all of them
    reliably in hand yet. `aliases` holds any additional known names
    (e.g. governed AirportAlias strings, or a hand-typed former name) that
    the search-plan builder may use in a later slice; V1's query plan
    (Part E) does not yet consume aliases.
    """

    name: str
    iata_code: str | None = None
    icao_code: str | None = None
    city: str | None = None
    country: str | None = None
    aliases: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("AirportIdentity.name must be a non-empty string")
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "iata_code", _clean(self.iata_code))
        object.__setattr__(self, "icao_code", _clean(self.icao_code))
        object.__setattr__(self, "city", _clean(self.city))
        object.__setattr__(self, "country", _clean(self.country))
        object.__setattr__(
            self,
            "aliases",
            tuple(a.strip() for a in self.aliases if a and a.strip()),
        )

    @classmethod
    def from_airport(
        cls, airport: "Airport", *, session: "Session | None" = None
    ) -> "AirportIdentity":
        """Build an AirportIdentity from a real, canonical, governed
        Airport row. Reads plain attributes only - never mutates the
        Airport, never writes anything.

        When `session` is supplied, currently-ADMITTED governed aliases
        are included via the existing read-only
        app.services.airport_alias.get_admitted_airport_aliases() helper
        (imported lazily so this module carries no service/persistence
        dependency at import time). Without a session, aliases are empty.
        """
        aliases: tuple[str, ...] = ()
        if session is not None:
            from app.services.airport_alias import get_admitted_airport_aliases

            aliases = tuple(get_admitted_airport_aliases(session, airport.id))
        return cls(
            name=airport.name,
            iata_code=getattr(airport, "iata_code", None),
            icao_code=getattr(airport, "icao_code", None),
            city=getattr(airport, "city", None),
            country=getattr(airport, "country", None),
            aliases=aliases,
        )


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None
