import os
import threading
import uuid
from datetime import UTC, datetime

import pytest
from helpers import SPEC
from shared.events import LogEntry, open_actions
from shared.models import Base, ExecutionRun, ExecutionSession, RunLog
from shared.spec import ToolSpec
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from worker.engine import execute
from worker.llm import FakeLlm, LlmResult
from worker.tool_exec import CredentialUnavailable  # noqa: F401 — re-export check


@pytest.fixture()
def db_engine():
    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine, tables=[ExecutionRun.__table__, ExecutionSession.__table__, RunLog.__table__])
    yield engine
    Base.metadata.drop_all(engine, tables=[ExecutionRun.__table__, ExecutionSession.__table__])


@pytest.fixture()
def upstream():
    from test_tool_exec import FakeUpstream

    service = FakeUpstream(body='{"items": []}').start()
    yield service
    service.close()


def make_dispatch(db_engine, spec):
    with Session(db_engine) as session, session.begin():
        conversation = ExecutionSession(agent_id=uuid.uuid4())
        session.add(conversation)
        session.flush()
        run = ExecutionRun(
            session_id=conversation.id,
            agent_id=uuid.uuid4(),
            artifact_id=uuid.uuid4(),
            request_id=None,
            content="查一下",
            params={"project_id": "P"},
            epoch=1,
        )
        run.created_at = datetime(2026, 10, 15, 13, 0, 0, tzinfo=UTC)
        session.add(run)
        session.flush()
        run_id, session_id = run.id, conversation.id
    return {
        "run_id": str(run_id),
        "epoch": 1,
        "session_id": str(session_id),
        "snapshot": spec.model_dump(mode="json"),
        "input": {"content": "查一下", "params": {"project_id": "P"}, "output_schema": None, "caller": "backend"},
    }


def spec_with_tool(url, **tool_over):
    tool = ToolSpec(name="fetch", description="取数", url_template=url, **tool_over)
    return SPEC.model_copy(update={"tools": [tool], "max_iterations": 4})


TOOL_CALL = [{"name": "fetch", "arguments": {"project_id": "P"}}]


def run_engine(db_engine, dispatch, llm):
    return list(execute(db_engine, dispatch, llm, threading.Event()))


def rows_of(items):
    return [item for item in items if item["kind"] == "row"]


def test_two_iteration_run_with_one_tool(db_engine, upstream):
    dispatch = make_dispatch(db_engine, spec_with_tool(upstream.url + "/items/{{args.project_id}}"))
    llm = FakeLlm(
        [
            ([], LlmResult(content="", tool_calls=TOOL_CALL)),
            (["好"], LlmResult(content="工具说没问题")),
        ]
    )

    items = run_engine(db_engine, dispatch, llm)

    rows = rows_of(items)
    assert [(row["type"], row["seq"]) for row in rows] == [
        ("run_started", 1),
        ("llm_call", 2),
        ("llm_result", 3),
        ("tool_call", 4),
        ("tool_result", 5),
        ("llm_call", 6),
        ("llm_result", 7),
        ("run_finished", 8),
    ]
    tool_call, tool_result = rows[3], rows[4]
    assert tool_call["action_id"] == tool_result["action_id"]
    assert tool_call["payload"]["tool"] == "fetch"
    assert tool_call["payload"]["arguments"] == {"project_id": "P"}
    assert tool_call["payload"]["idempotency_key"] == f"{dispatch['run_id']}:{tool_call['action_id']}"
    assert upstream.received[0]["path"] == "/items/P"
    assert rows[-1]["payload"] == {"status": "succeeded", "result": "工具说没问题"}

    second_call_messages = rows[5]["payload"]["messages"]
    assert second_call_messages[-2]["role"] == "assistant"
    assert second_call_messages[-2]["tool_calls"][0]["function"]["name"] == "fetch"
    assert second_call_messages[-1] == {
        "role": "tool",
        "tool_call_id": tool_call["action_id"],
        "content": '{"items": []}',
    }

    assert llm.calls[0][3] == [dispatch and "fetch"] or llm.calls[0][3][0]["function"]["name"] == "fetch"

    with Session(db_engine) as session:
        log_rows = session.scalars(select(RunLog).where(RunLog.run_id == uuid.UUID(dispatch["run_id"]))).all()
    entries = [
        LogEntry(run_id=r.run_id, seq=r.seq, action_id=r.action_id, type=r.type, payload=r.payload) for r in log_rows
    ]
    assert open_actions(entries) == set()


def test_tool_error_is_recoverable(db_engine, upstream):
    upstream.status = 500
    dispatch = make_dispatch(db_engine, spec_with_tool(upstream.url))
    llm = FakeLlm(
        [
            ([], LlmResult(content="", tool_calls=TOOL_CALL)),
            (["好"], LlmResult(content="工具挂了但我好了")),
        ]
    )

    items = run_engine(db_engine, dispatch, llm)

    rows = rows_of(items)
    assert rows[4]["payload"]["error"] == "HTTP 500"
    assert rows[-1]["payload"]["status"] == "succeeded"
    assert "error: HTTP 500" in rows[5]["payload"]["messages"][-1]["content"]


def test_unknown_tool_name_is_recoverable(db_engine):
    dispatch = make_dispatch(db_engine, spec_with_tool("http://dead.test"))
    llm = FakeLlm(
        [
            ([], LlmResult(content="", tool_calls=[{"name": "nope", "arguments": {}}])),
            (["好"], LlmResult(content="没有这个工具")),
        ]
    )

    items = run_engine(db_engine, dispatch, llm)

    rows = rows_of(items)
    assert "unknown tool" in rows[4]["payload"]["error"]
    assert rows[-1]["payload"]["status"] == "succeeded"


def test_max_iterations_fails_the_run(db_engine, upstream):
    dispatch = make_dispatch(db_engine, spec_with_tool(upstream.url).model_copy(update={"max_iterations": 1}))
    llm = FakeLlm(
        [
            ([], LlmResult(content="", tool_calls=TOOL_CALL)),
            (["不该到这"], LlmResult(content="too late")),
        ]
    )

    items = run_engine(db_engine, dispatch, llm)

    rows = rows_of(items)
    assert rows[-1]["type"] == "run_failed"
    assert "max iterations" in rows[-1]["payload"]["error"]
    assert [row["type"] for row in rows].count("llm_call") == 1
    assert len(llm.calls) == 1


def test_missing_credential_fails_the_run(db_engine, upstream, monkeypatch):
    monkeypatch.delenv("CREDENTIAL_SVC", raising=False)
    spec = spec_with_tool(upstream.url, credential="svc", headers={"Authorization": "Bearer {{credential}}"})
    dispatch = make_dispatch(db_engine, spec)
    llm = FakeLlm([([], LlmResult(content="", tool_calls=TOOL_CALL))])

    items = run_engine(db_engine, dispatch, llm)

    rows = rows_of(items)
    assert rows[-1]["type"] == "run_failed"
    assert "svc" in rows[-1]["payload"]["error"]
    assert "tool_result" not in [row["type"] for row in rows]


def test_resume_replays_an_open_tool_call_with_the_same_key(db_engine, upstream):
    spec = spec_with_tool(upstream.url + "/items/{{args.project_id}}")
    dispatch = make_dispatch(db_engine, spec)
    with Session(db_engine) as session, session.begin():
        run_id = uuid.UUID(dispatch["run_id"])
        session.add(
            RunLog(
                run_id=run_id,
                seq=1,
                action_id=None,
                type="run_started",
                payload={"content": "查一下", "params": {"project_id": "P"}},
            )
        )
        session.add(RunLog(run_id=run_id, seq=2, action_id="llm-1", type="llm_call", payload={"messages": []}))
        session.add(
            RunLog(
                run_id=run_id,
                seq=3,
                action_id="llm-1",
                type="llm_result",
                payload={
                    "content": "",
                    "tool_calls": TOOL_CALL,
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "cost": 0.0,
                    "duration_ms": 1,
                },
            )
        )
        session.add(
            RunLog(
                run_id=run_id,
                seq=4,
                action_id="tool-1",
                type="tool_call",
                payload={
                    "tool": "fetch",
                    "arguments": {"project_id": "P"},
                    "idempotency_key": f"{dispatch['run_id']}:tool-1",
                },
            )
        )
    llm = FakeLlm([(["好"], LlmResult(content="续跑完成"))])

    items = run_engine(db_engine, dispatch, llm)

    rows = rows_of(items)
    assert [row["type"] for row in rows] == ["tool_call", "tool_result", "llm_call", "llm_result", "run_finished"]
    retried = rows[0]
    assert retried["action_id"] == "tool-1"
    assert retried["seq"] == 5
    assert retried["payload"]["arguments"] == {"project_id": "P"}
    assert upstream.received[0]["headers"]["idempotency-key"] == f"{dispatch['run_id']}:tool-1"
    assert rows[-1]["payload"] == {"status": "succeeded", "result": "续跑完成"}

    # The resumed llm_call carries the round's assistant + tool messages.
    messages = rows[2]["payload"]["messages"]
    assert messages[-2]["tool_calls"][0]["id"] == "tool-1"
    assert messages[-1]["tool_call_id"] == "tool-1"
