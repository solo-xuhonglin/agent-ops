import pytest
from config.app import CONFIG_TABLES
from shared.models import Base

from config import db


@pytest.fixture(scope="session", autouse=True)
def schema():
    Base.metadata.drop_all(db.engine)
    Base.metadata.create_all(db.engine, tables=CONFIG_TABLES)
    yield
