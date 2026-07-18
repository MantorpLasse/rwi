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

