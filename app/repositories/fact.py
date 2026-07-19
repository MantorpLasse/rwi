from datetime import date

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session, aliased, joinedload, selectinload

from app.models.fact import Fact, FactStatus


class FactRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, fact: Fact) -> Fact:
        self.session.add(fact)
        self.session.flush()
        return fact

    def get_by_id(self, fact_id: int) -> Fact | None:
        statement = (
            select(Fact)
            .where(Fact.id == fact_id)
            .options(
                joinedload(Fact.supersedes),
                selectinload(Fact.superseded_by),
                selectinload(Fact.supporting_verifications),
            )
        )
        return self.session.scalar(statement)

    def list(self) -> list[Fact]:
        statement = select(Fact).order_by(Fact.created_at.asc(), Fact.id.asc())
        return list(self.session.scalars(statement))

    def list_current(self, as_of: date | None = None) -> list[Fact]:
        effective_date = as_of or date.today()
        successor = aliased(Fact)
        applicable_successor = exists(
            select(successor.id).where(
                successor.supersedes_fact_id == Fact.id,
                or_(successor.valid_from.is_(None), successor.valid_from <= effective_date),
                or_(successor.valid_to.is_(None), successor.valid_to >= effective_date),
            )
        )
        statement = (
            select(Fact)
            .where(
                Fact.status == FactStatus.ACCEPTED,
                or_(Fact.valid_from.is_(None), Fact.valid_from <= effective_date),
                or_(Fact.valid_to.is_(None), Fact.valid_to >= effective_date),
                ~applicable_successor,
            )
            .order_by(Fact.fact_type_key.asc(), Fact.subject_type.asc(), Fact.subject_identifier.asc(), Fact.id.asc())
        )
        return list(self.session.scalars(statement))
