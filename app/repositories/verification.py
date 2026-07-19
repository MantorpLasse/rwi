from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.verification import Verification


class VerificationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, verification: Verification) -> Verification:
        self.session.add(verification)
        self.session.flush()
        return verification

    def get_by_id(self, verification_id: int) -> Verification | None:
        statement = (
            select(Verification)
            .where(Verification.id == verification_id)
            .options(joinedload(Verification.observation))
        )
        return self.session.scalar(statement)

    def list_by_observation(self, observation_id: int) -> list[Verification]:
        statement = (
            select(Verification)
            .where(Verification.observation_id == observation_id)
            .order_by(Verification.reviewed_at.asc(), Verification.id.asc())
        )
        return list(self.session.scalars(statement))
