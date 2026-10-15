import os
import socket
import threading
import uuid

import pytest
from helpers import SPEC
from shared.models import Base, ExecutionRun, ExecutionSession, RunLog
from shared.wire import read_frame_sync
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from worker.client import _pump
from worker.llm import FakeLlm, LlmResult


@pytest.fixture()
def db_engine(monkeypatch):
    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine, tables=[ExecutionRun.__table__, ExecutionSession.__table__, RunLog.__table__])

    received = []
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    monkeypatch.setenv("MANAGER_HOST", "127.0.0.1")
    monkeypatch.setenv("MANAGER_PORT", str(port))

    def accept_loop():
        conn, _ = listener.accept()
        try:
            while True:
                received.append(read_frame_sync(conn))
        except (ConnectionError, OSError, EOFError):
            pass

    thread = threading.Thread(target=accept_loop, daemon=True)
    thread.start()

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
        run_id, session_id = run.id, conversation.id

    yield {
        "engine": engine,
        "run_id": str(run_id),
        "session_id": str(session_id),
        "frames": received,
        "converge": lambda: (listener.close(), thread.join(timeout=5)),
    }
    listener.close()
    thread.join(timeout=5)
    Base.metadata.drop_all(engine, tables=[ExecutionRun.__table__, ExecutionSession.__table__])


def test_pump_persists_rows_and_wires_projected_frames(db_engine):
    dispatch = {
        "run_id": db_engine["run_id"],
        "epoch": 1,
        "session_id": db_engine["session_id"],
        "snapshot": SPEC.model_dump(mode="json"),
        "input": {"content": "hello", "params": {"project_id": "P"}, "output_schema": None, "caller": "backend"},
    }
    llm = FakeLlm([(["回", "答"], LlmResult(content="回答"))])

    _pump(dispatch, llm)
    db_engine["converge"]()  # the reader thread exits on EOF; converge before asserting

    names = [(type_, data.get("seq")) for type_, data in db_engine["frames"]]
    assert names == [
        ("state", None),
        ("RUN_STARTED", 1),
        ("llm_call", 2),
        ("TEXT_MESSAGE_START", None),
        ("TEXT_MESSAGE_CONTENT", None),
        ("TEXT_MESSAGE_CONTENT", None),
        ("TEXT_MESSAGE_END", None),
        ("llm_result", 3),
        ("RUN_FINISHED", 4),
    ]
    llm_call_frame = db_engine["frames"][2][1]
    assert "messages" not in llm_call_frame
    assert llm_call_frame["action_id"] == db_engine["frames"][7][1]["action_id"]
    assert [data["delta"] for type_, data in db_engine["frames"] if type_ == "TEXT_MESSAGE_CONTENT"] == ["回", "答"]

    with Session(db_engine["engine"]) as session:
        rows = session.scalars(
            select(RunLog).where(RunLog.run_id == uuid.UUID(db_engine["run_id"])).order_by(RunLog.seq)
        ).all()
    assert [row.type for row in rows] == ["run_started", "llm_call", "llm_result", "run_finished"]
    assert rows[1].payload["messages"][0]["role"] == "system"


def test_pump_survives_engine_failures(db_engine):
    dispatch = {
        "run_id": db_engine["run_id"],
        "epoch": 1,
        "session_id": db_engine["session_id"],
        "snapshot": {"mode": "bogus", "instructions": ""},
        "input": {"content": "hello", "params": {"project_id": "P"}, "output_schema": None, "caller": "backend"},
    }

    _pump(dispatch, FakeLlm())  # a broken snapshot must not escape the pump
