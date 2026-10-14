import os

import pytest

# Ephemeral internal port so parallel-safe manager tests never fight over 9000;
# a long sweep cadence keeps the periodic dispatcher out of test determinism.
os.environ.setdefault("MANAGER_INTERNAL_PORT", "0")
os.environ.setdefault("SWEEP_INTERVAL", "60")

from config.app import CONFIG_TABLES
from config.seed import seed
from fastapi.testclient import TestClient
from manager.app import MANAGER_TABLES, app
from shared.models import Base, ExecutionRun, RunLog
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from util import TEST_KEY

from manager import internal

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


@pytest.fixture(scope="session")
def db_engine():
    return engine


@pytest.fixture(autouse=True)
def quiet_link(db_engine):
    # Finish every non-terminal run after each test so later passes never
    # re-dispatch leftovers, and forget dispatch bookkeeping.
    from manager.runs import is_active

    yield
    with Session(db_engine) as session, session.begin():
        for run in session.scalars(select(ExecutionRun)).all():
            if is_active(session, run.id):
                last_seq = session.scalar(select(func.max(RunLog.seq)).where(RunLog.run_id == run.id)) or 0
                session.add(
                    RunLog(
                        run_id=run.id,
                        seq=last_seq + 1,
                        action_id=None,
                        type="run_finished",
                        payload={"status": "cancelled"},
                    )
                )
    internal.dispatched_at.clear()
