from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ObservationType


class ObservationTypeRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_key(self, key: str) -> ObservationType | None:
        return self.session.scalar(
            select(ObservationType).where(ObservationType.key == key)
        )

    def get_active_by_id(self, observation_type_id: int) -> ObservationType | None:
        return self.session.scalar(
            select(ObservationType).where(
                ObservationType.id == observation_type_id,
                ObservationType.active.is_(True),
            )
        )

    def list_active(self) -> list[ObservationType]:
        statement = (
            select(ObservationType)
            .where(ObservationType.active.is_(True))
            .order_by(ObservationType.display_label.asc(), ObservationType.id.asc())
        )
        return list(self.session.scalars(statement))
