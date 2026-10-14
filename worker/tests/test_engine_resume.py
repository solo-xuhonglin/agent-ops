import os
import threading
import uuid

import pytest
from helpers import SPEC
from shared.events import LogEntry, open_actions
from shared.models import Base, ExecutionRun, ExecutionSession, RunLog
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from worker.engine import execute
from worker.llm import FakeLlm, LlmResult


@pytest.fixture()
def db_engine():
    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine, tables=[ExecutionRun.__table__, ExecutionSession.__table__, RunLog.__table__])
    yield engine
    Base.metadata.drop_all(engine, tables=[ExecutionRun.__table__, ExecutionSession.__table__])


def make_dispatch(engine, *, pre_rows=()):
    with Session(engine) as session, session.begin():
        conversation = ExecutionSession(agent_id=uuid.uuid4())
        session.add(conversation)
        session.flush()
        run = ExecutionRun(
            session_id=conversation.id,
            agent_id=uuid.uuid4(),
            artifact_id=uuid.uuid4(),
            request_id=None,
            content="hello",
            params={"project_id": "P"},
            epoch=1,
        )
        session.add(run)
        session.flush()
        for seq, type_, action_id, payload in pre_rows:
            session.add(RunLog(run_id=run.id, seq=seq, action_id=action_id, type=type_, payload=payload))
        run_id, session_id = run.id, conversation.id
    return {
        "run_id": str(run_id),
        "epoch": 1,
        "session_id": str(session_id),
        "snapshot": SPEC.model_dump(mode="json"),
        "input": {"content": "hello", "params": {"project_id": "P"}, "output_schema": None, "caller": "backend"},
    }


def persist(engine, run_id, items):
    """The client's write side: every yielded row lands durably."""
    for item in items:
        if item["kind"] == "row":
            with Session(engine) as session, session.begin():
                session.add(
                    RunLog(
                        run_id=uuid.UUID(run_id),
                        seq=item["seq"],
                        action_id=item["action_id"],
                        type=item["type"],
                        payload=item["payload"],
                    )
                )


def log_entries(engine, run_id) -> list[LogEntry]:
    with Session(engine) as session:
        rows = session.scalars(select(RunLog).where(RunLog.run_id == uuid.UUID(run_id)).order_by(RunLog.seq)).all()
    return [
        LogEntry(run_id=row.run_id, seq=row.seq, action_id=row.action_id, type=row.type, payload=row.payload)
        for row in rows
    ]


OPEN_CALL_MESSAGES = [{"role": "system", "content": "旧系统提示"}, {"role": "user", "content": "旧消息"}]


def test_open_llm_call_is_retried_with_the_same_action_id(db_engine):
    dispatch = make_dispatch(
        db_engine,
        pre_rows=[
            (1, "run_started", None, {"content": "hello", "params": {"project_id": "P"}}),
            (2, "llm_call", "action-1", {"messages": OPEN_CALL_MESSAGES}),
        ],
    )
    llm = FakeLlm([(["重", "试"], LlmResult(content="重试成功"))])

    items = list(execute(db_engine, dispatch, llm, threading.Event()))
    persist(db_engine, dispatch["run_id"], items)

    rows = [item for item in items if item["kind"] == "row"]
    assert [row["type"] for row in rows] == ["llm_call", "llm_result", "run_finished"]
    assert rows[0]["seq"] == 3
    assert rows[0]["action_id"] == "action-1"
    assert rows[0]["payload"]["messages"] == OPEN_CALL_MESSAGES
    assert rows[2]["payload"] == {"status": "succeeded", "result": "重试成功"}

    # Two llm_call rows, one llm_result: the pairing invariant closes.
    assert open_actions(log_entries(db_engine, dispatch["run_id"])) == set()


def test_run_started_only_tail_continues_from_the_call(db_engine):
    dispatch = make_dispatch(
        db_engine,
        pre_rows=[(1, "run_started", None, {"content": "hello", "params": {"project_id": "P"}})],
    )

    items = list(execute(db_engine, dispatch, FakeLlm([(["答"], LlmResult(content="答"))]), threading.Event()))

    rows = [item for item in items if item["kind"] == "row"]
    assert [row["type"] for row in rows] == ["llm_call", "llm_result", "run_finished"]
    assert rows[0]["seq"] == 2
