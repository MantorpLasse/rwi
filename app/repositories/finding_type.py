from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finding_type import FindingType


class FindingTypeRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, finding_type: FindingType) -> FindingType:
        self.session.add(finding_type)
        self.session.flush()
        return finding_type

    def get_by_key(self, key: str) -> FindingType | None:
        return self.session.scalar(
            select(FindingType).where(FindingType.key == key)
        )

    def get_by_id(self, finding_type_id: int) -> FindingType | None:
        return self.session.get(FindingType, finding_type_id)

    def list(self) -> list[FindingType]:
        statement = select(FindingType).order_by(
            FindingType.key.asc(), FindingType.id.asc()
        )
        return list(self.session.scalars(statement))

    def list_active(self) -> list[FindingType]:
        statement = (
            select(FindingType)
            .where(FindingType.is_active.is_(True))
            .order_by(FindingType.key.asc(), FindingType.id.asc())
        )
        return list(self.session.scalars(statement))
