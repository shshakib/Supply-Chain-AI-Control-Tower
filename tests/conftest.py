from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from control_tower.database import create_database_engine, create_schema
from control_tower.synthetic import SyntheticDataGenerator


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    test_engine = create_database_engine("sqlite://")
    create_schema(test_engine)

    with Session(test_engine) as session:
        SyntheticDataGenerator(session, seed=42).generate()
        session.commit()

    yield test_engine
    test_engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as test_session:
        yield test_session
