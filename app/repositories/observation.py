from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.document import Document
from app.models.observation import Observation


class ObservationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, observation: Observation) -> Observation:
        self.session.add(observation)
        self.session.flush()
        return observation

    def get_by_id(self, observation_id: int) -> Observation | None:
        statement = (
            select(Observation)
            .where(Observation.id == observation_id)
            .options(
                joinedload(Observation.document).joinedload(Document.source),
                joinedload(Observation.observation_type),
                joinedload(Observation.supersedes),
            )
        )
        return self.session.scalar(statement)

    def list_all(self) -> list[Observation]:
        statement = (
            select(Observation)
            .options(
                joinedload(Observation.document),
                joinedload(Observation.observation_type),
            )
            .order_by(Observation.created_at.desc(), Observation.id.desc())
        )
        return list(self.session.scalars(statement))

    def list_by_document(self, document_id: int) -> list[Observation]:
        statement = (
            select(Observation)
            .where(Observation.document_id == document_id)
            .options(joinedload(Observation.observation_type))
            .order_by(Observation.created_at.asc(), Observation.id.asc())
        )
        return list(self.session.scalars(statement))

    def list_superseding(self, observation_id: int) -> list[Observation]:
        statement = (
            select(Observation)
            .where(Observation.supersedes_observation_id == observation_id)
            .order_by(Observation.created_at.asc(), Observation.id.asc())
        )
        return list(self.session.scalars(statement))
