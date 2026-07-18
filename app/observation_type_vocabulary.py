from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import ObservationType
from app.repositories import ObservationTypeRepository


@dataclass(frozen=True)
class ObservationTypeDefinition:
    key: str
    display_label: str
    description: str
    value_type: str


FAA_EMAS_OBSERVATION_TYPES = (
    ObservationTypeDefinition(
        key="airport.emas.product",
        display_label="Airport EMAS product",
        description="EMAS product family reported as present at an airport.",
        value_type="enumeration",
    ),
    ObservationTypeDefinition(
        key="airport.emas.system_count",
        display_label="Airport EMAS system count",
        description="Number of EMAS systems reported for an airport.",
        value_type="integer",
    ),
    ObservationTypeDefinition(
        key="airport.emas.installation_year_display",
        display_label="Airport EMAS installation year display",
        description=(
            "Source display text containing installation or replacement years "
            "whose precise semantics may be unresolved."
        ),
        value_type="raw_text",
    ),
)


def seed_observation_types(session: Session) -> list[ObservationType]:
    repository = ObservationTypeRepository(session)
    loaded: list[ObservationType] = []

    for definition in FAA_EMAS_OBSERVATION_TYPES:
        observation_type = repository.get_by_key(definition.key)
        if observation_type is None:
            observation_type = ObservationType(
                key=definition.key,
                display_label=definition.display_label,
                description=definition.description,
                value_type=definition.value_type,
                active=True,
            )
            session.add(observation_type)
            session.flush()
        loaded.append(observation_type)

    return loaded

