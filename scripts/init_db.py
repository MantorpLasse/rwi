from sqlalchemy.engine import Engine

from app.database import Base, engine
from app import models as _models  # noqa: F401


def initialize_database(bind: Engine = engine) -> None:
    Base.metadata.create_all(bind)


if __name__ == "__main__":
    initialize_database()
    print("Database initialized.")
