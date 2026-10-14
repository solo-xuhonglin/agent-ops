import os

import pytest
from config.app import CONFIG_TABLES
from config.seed import seed
from fastapi.testclient import TestClient
from manager.app import MANAGER_TABLES, app
from shared.models import Base, RunLog
from sqlalchemy import create_engine

TEST_KEY = "manager-test-key"
engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)


@pytest.fixture(scope="session", autouse=True)
def schema():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine, tables=MANAGER_TABLES + CONFIG_TABLES + [RunLog.__table__])
    seed(engine, TEST_KEY)
    yield


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def auth_headers():
    return {"Authorization": f"Bearer {TEST_KEY}"}
