from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.observation import Observation


class ObservationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, observation: Observation) -> Observation:
        self.session.add(observation)
        self.session.flush()
        return observation

    def get_by_id(self, observation_id: int) -> Observation | None:
        return self.session.get(Observation, observation_id)

    def list_by_document(self, document_id: int) -> list[Observation]:
        statement = (
            select(Observation)
            .where(Observation.document_id == document_id)
            .order_by(Observation.created_at.asc(), Observation.id.asc())
        )
        return list(self.session.scalars(statement))
