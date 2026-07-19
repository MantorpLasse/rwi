from sqlalchemy import exists, select
from sqlalchemy.orm import Session, aliased, joinedload, selectinload

from app.models.intelligence import Intelligence, IntelligenceStatus


class IntelligenceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, intelligence: Intelligence) -> Intelligence:
        self.session.add(intelligence)
        self.session.flush()
        return intelligence

    def get_by_id(self, intelligence_id: int) -> Intelligence | None:
        statement = (
            select(Intelligence)
            .where(Intelligence.id == intelligence_id)
            .options(
                joinedload(Intelligence.supersedes),
                selectinload(Intelligence.superseded_by),
                selectinload(Intelligence.supporting_facts),
            )
        )
        return self.session.scalar(statement)

    def list(self) -> list[Intelligence]:
        statement = select(Intelligence).order_by(
            Intelligence.created_at.asc(), Intelligence.id.asc()
        )
        return list(self.session.scalars(statement))

    def list_current(self) -> list[Intelligence]:
        successor = aliased(Intelligence)
        has_successor = exists(
            select(successor.id).where(
                successor.supersedes_intelligence_id == Intelligence.id
            )
        )
        statement = (
            select(Intelligence)
            .where(
                Intelligence.status == IntelligenceStatus.ACTIVE,
                ~has_successor,
            )
            .order_by(
                Intelligence.finding_type_id.asc(),
                Intelligence.derived_at.asc(),
                Intelligence.id.asc(),
            )
        )
        return list(self.session.scalars(statement))
