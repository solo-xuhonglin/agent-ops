import os
import uuid

import pytest
from shared.models import Base, ExecutionRun, ExecutionSession
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
MANAGER_TABLES = [ExecutionSession.__table__, ExecutionRun.__table__]


@pytest.fixture(scope="module", autouse=True)
def tables():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine, tables=MANAGER_TABLES)
    yield
    Base.metadata.drop_all(engine, tables=MANAGER_TABLES)


def test_manager_tables():
    columns = {column["name"] for column in inspect(engine).get_columns("execution_runs")}

    assert columns == {
        "id",
        "session_id",
        "agent_id",
        "artifact_id",
        "request_id",
        "content",
        "params",
        "output_schema",
        "cancel_requested",
        "epoch",
        "created_at",
    }


def test_duplicate_request_id_rejected():
    agent_id = uuid.uuid4()
    base = {
        "session_id": uuid.uuid4(),
        "agent_id": agent_id,
        "artifact_id": uuid.uuid4(),
        "request_id": "job-1",
        "content": "hi",
        "params": {},
    }
    with Session(engine) as session, session.begin():
        session.add(ExecutionRun(**base))

    with pytest.raises(IntegrityError), Session(engine) as session, session.begin():
        session.add(ExecutionRun(**base, id=uuid.uuid4()))


def test_session_round_trip():
    session = ExecutionSession(agent_id=uuid.uuid4())
    with Session(engine) as sess, sess.begin():
        sess.add(session)
        sess.flush()
        session_id = session.id

    with Session(engine) as sess:
        stored = sess.scalar(select(ExecutionSession).where(ExecutionSession.id == session_id))

    assert stored is not None
    assert stored.active_run_id is None
    assert stored.caller == "backend"
