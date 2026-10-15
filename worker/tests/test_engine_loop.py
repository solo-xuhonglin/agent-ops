import os
import threading
import uuid

import pytest
from helpers import SPEC
from shared.models import Base, ExecutionRun, ExecutionSession, RunLog
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from worker.engine import execute, render_user_message
from worker.llm import FakeLlm, LlmDelta, LlmDone, LlmResult


@pytest.fixture()
def db_engine():
    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine, tables=[ExecutionRun.__table__, ExecutionSession.__table__, RunLog.__table__])
    yield engine
    Base.metadata.drop_all(engine, tables=[ExecutionRun.__table__, ExecutionSession.__table__])


def make_dispatch(engine, *, epoch=1, cancel_requested=False, pre_rows=()):
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
            cancel_requested=cancel_requested,
            epoch=epoch,
        )
        session.add(run)
        session.flush()
        for seq, type_, action_id, payload in pre_rows:
            session.add(RunLog(run_id=run.id, seq=seq, action_id=action_id, type=type_, payload=payload))
        run_id, session_id = run.id, conversation.id
    return {
        "run_id": str(run_id),
        "epoch": epoch,
        "session_id": str(session_id),
        "snapshot": SPEC.model_dump(mode="json"),
        "input": {"content": "hello", "params": {"project_id": "P"}, "output_schema": None, "caller": "backend"},
    }


def run_engine(db_engine, dispatch, llm, cancelled=None):
    return list(execute(db_engine, dispatch, llm, cancelled or threading.Event()))


def test_fresh_run_full_sequence(db_engine):
    dispatch = make_dispatch(db_engine)
    llm = FakeLlm([(["你", "好"], LlmResult(content="你好", input_tokens=3, output_tokens=2, cost=0.0))])

    items = run_engine(db_engine, dispatch, llm)

    kinds = [(item["kind"], item["type"]) for item in items]
    assert kinds == [
        ("row", "run_started"),
        ("row", "llm_call"),
        ("stream", "message_started"),
        ("stream", "message_delta"),
        ("stream", "message_delta"),
        ("row", "llm_result"),
        ("row", "run_finished"),
    ]
    rows = [item for item in items if item["kind"] == "row"]
    assert [row["seq"] for row in rows] == [1, 2, 3, 4]
    assert rows[1]["action_id"] == rows[2]["action_id"] is not None

    messages = rows[1]["payload"]["messages"]
    assert messages[0] == {"role": "system", "content": SPEC.instructions}
    assert messages[-1] == {"role": "user", "content": render_user_message("hello", {"project_id": "P"}, SPEC)}

    deltas = [item for item in items if item["type"] == "message_delta"]
    assert [delta["data"]["text"] for delta in deltas] == ["你", "好"]

    result_payload = rows[2]["payload"]
    assert result_payload["content"] == "你好"
    assert result_payload["input_tokens"] == 3
    assert result_payload["output_tokens"] == 2
    assert result_payload["duration_ms"] >= 0

    assert rows[3]["payload"] == {"status": "succeeded", "result": "你好"}
    assert llm.calls[0][1] == SPEC.model_id


def test_pre_dispatch_cancel_skips_the_model(db_engine):
    dispatch = make_dispatch(db_engine, cancel_requested=True)

    items = run_engine(db_engine, dispatch, FakeLlm())

    assert [(item["kind"], item["type"]) for item in items] == [
        ("row", "run_started"),
        ("row", "run_finished"),
    ]
    assert items[1]["payload"] == {"status": "cancelled", "result": None}


class CancelAfterFirstChunk:
    def __init__(self, event):
        self.event = event

    def stream(self, messages, *, tools, model_id, model_params):
        yield LlmDelta("第一段")
        self.event.set()
        yield LlmDelta("不该出现")
        yield LlmDone(LlmResult(content="永远不该落地"))


def test_cancel_mid_stream_aborts_without_llm_result(db_engine):
    dispatch = make_dispatch(db_engine)
    event = threading.Event()

    items = run_engine(db_engine, dispatch, CancelAfterFirstChunk(event), event)

    assert [(item["kind"], item["type"]) for item in items] == [
        ("row", "run_started"),
        ("row", "llm_call"),
        ("stream", "message_started"),
        ("stream", "message_delta"),
        ("row", "run_finished"),
    ]
    assert items[-1]["payload"] == {"status": "cancelled", "result": None}


def test_epoch_mismatch_yields_nothing(db_engine):
    dispatch = make_dispatch(db_engine, epoch=2)
    dispatch["epoch"] = 1

    assert run_engine(db_engine, dispatch, FakeLlm()) == []


def test_terminal_tail_is_a_noop(db_engine):
    dispatch = make_dispatch(
        db_engine,
        pre_rows=[
            (1, "run_started", None, {"content": "hello"}),
            (2, "run_finished", None, {"status": "succeeded", "result": "x"}),
        ],
    )

    assert run_engine(db_engine, dispatch, FakeLlm()) == []
