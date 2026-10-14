import uuid

import pytest
from shared.models import Base, ExecutionRun, ExecutionSession, RunLog
from sqlalchemy import select
from sqlalchemy.orm import Session
from worker.executor import execute


@pytest.fixture(autouse=True)
def schema():
    import os

    from sqlalchemy import create_engine

    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine, tables=[ExecutionRun.__table__, ExecutionSession.__table__, RunLog.__table__])
    yield engine
    # Leave the baseline the suite started from (run_log only) so the
    # worker-ownership test still sees exactly its own table.
    Base.metadata.drop_all(engine, tables=[ExecutionRun.__table__, ExecutionSession.__table__])


def make_run(engine, cancel_requested=False, epoch=1):
    session = ExecutionSession(agent_id=uuid.uuid4())
    with Session(engine) as sess, sess.begin():
        sess.add(session)
        sess.flush()
        run = ExecutionRun(
            session_id=session.id,
            agent_id=uuid.uuid4(),
            artifact_id=uuid.uuid4(),
            request_id=None,
            content="hello",
            params={"project_id": "P"},
            cancel_requested=cancel_requested,
            epoch=epoch,
        )
        sess.add(run)
        sess.flush()
        return {
            "run_id": str(run.id),
            "epoch": epoch,
            "session_id": str(session.id),
            "snapshot": {},
            "input": {"content": "hello", "params": {"project_id": "P"}, "output_schema": None, "caller": "backend"},
        }


def entries(engine, run_id):
    with Session(engine) as session:
        rows = session.scalars(select(RunLog).where(RunLog.run_id == uuid.UUID(run_id)).order_by(RunLog.seq)).all()
    return [(row.seq, row.type, row.payload) for row in rows]


def test_stub_writes_lifecycle(schema):
    dispatch = make_run(schema)

    execute(schema, dispatch)

    rows = entries(schema, dispatch["run_id"])
    assert len(rows) == 2
    assert rows[0][1] == "run_started"
    assert rows[0][2]["content"] == "hello"
    assert rows[0][2]["session_id"] == dispatch["session_id"]
    assert rows[0][2]["caller"] == "backend"
    assert rows[1][1] == "run_finished"
    assert rows[1][2]["status"] == "succeeded"
    assert rows[1][2]["result"] == "stub: hello"


def test_executor_idempotent_resume(schema):
    dispatch = make_run(schema)

    execute(schema, dispatch)
    execute(schema, dispatch)

    rows = entries(schema, dispatch["run_id"])
    assert len(rows) == 2
    assert [row[0] for row in rows] == [1, 2]


def test_cancelled_before_start(schema):
    dispatch = make_run(schema, cancel_requested=True)

    execute(schema, dispatch)

    rows = entries(schema, dispatch["run_id"])
    assert len(rows) == 2
    assert rows[1][1] == "run_finished"
    assert rows[1][2]["status"] == "cancelled"


def test_epoch_fence(schema):
    dispatch = make_run(schema, epoch=1)
    # The run row has moved on to epoch 2: this dispatch is stale.
    with Session(schema) as session, session.begin():
        stored = session.scalar(select(ExecutionRun).where(ExecutionRun.id == uuid.UUID(dispatch["run_id"])))
        stored.epoch = 2

    execute(schema, dispatch)

    assert entries(schema, dispatch["run_id"]) == []


def test_execute_returns_entries(schema):
    dispatch = make_run(schema)

    entries = execute(schema, dispatch)

    assert [entry["type"] for entry in entries] == ["run_started", "run_finished"]
    assert entries[0]["data"]["seq"] == 1
    assert entries[0]["data"]["run_id"] == dispatch["run_id"]
    assert entries[0]["data"]["content"] == "hello"
    assert entries[1]["data"]["seq"] == 2
    assert entries[1]["data"]["status"] == "succeeded"


def test_execute_returns_only_new_entries(schema):
    dispatch = make_run(schema)

    execute(schema, dispatch)
    assert execute(schema, dispatch) == []
