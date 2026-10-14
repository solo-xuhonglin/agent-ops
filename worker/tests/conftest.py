import pytest
from shared.models import Base, RunLog

from worker import db


@pytest.fixture(scope="session", autouse=True)
def schema():
    Base.metadata.drop_all(db.engine)
    Base.metadata.create_all(db.engine, tables=[RunLog.__table__])
    yield
