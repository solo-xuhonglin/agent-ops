import os
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from helpers import SPEC
from shared.models import Base, ExecutionRun, ExecutionSession, RunLog
from shared.spec import CompactionPolicy
from sqlalchemy import create_engine
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


def spec_with_limit(token_limit: int):
    return SPEC.model_copy(update={"context": CompactionPolicy(token_limit=token_limit)})


def make_dispatch(engine, spec, exchanges):
    """A dispatch whose session already holds finished runs (one exchange each)."""
    base = datetime(2026, 10, 15, 9, 0, 0, tzinfo=UTC)
    with Session(engine) as session, session.begin():
        conversation = ExecutionSession(agent_id=uuid.uuid4())
        session.add(conversation)
        session.flush()
        for index, (content, answer) in enumerate(exchanges):
            run = ExecutionRun(
                session_id=conversation.id,
                agent_id=uuid.uuid4(),
                artifact_id=uuid.uuid4(),
                request_id=None,
                content=content,
                params={"project_id": "P"},
                epoch=0,
            )
            run.created_at = base + timedelta(seconds=index)
            session.add(run)
            session.flush()
            session.add(
                RunLog(
                    run_id=run.id,
                    seq=1,
                    action_id=None,
                    type="run_started",
                    payload={"content": content, "params": {"project_id": "P"}, "session_id": str(conversation.id)},
                )
            )
            session.add(RunLog(run_id=run.id, seq=2, action_id="a", type="llm_call", payload={"messages": []}))
            session.add(RunLog(run_id=run.id, seq=3, action_id="a", type="llm_result", payload={"content": answer}))
            session.add(
                RunLog(
                    run_id=run.id,
                    seq=4,
                    action_id=None,
                    type="run_finished",
                    payload={"status": "succeeded", "result": answer},
                )
            )
        current = ExecutionRun(
            session_id=conversation.id,
            agent_id=uuid.uuid4(),
            artifact_id=uuid.uuid4(),
            request_id=None,
            content="新问题",
            params={"project_id": "P"},
            epoch=1,
        )
        current.created_at = base + timedelta(seconds=100)
        session.add(current)
        session.flush()
        run_id, session_id = current.id, conversation.id
    return {
        "run_id": str(run_id),
        "epoch": 1,
        "session_id": str(session_id),
        "snapshot": spec.model_dump(mode="json"),
        "input": {"content": "新问题", "params": {"project_id": "P"}, "output_schema": None, "caller": "backend"},
    }


def run_engine(db_engine, dispatch, llm):
    return list(execute(db_engine, dispatch, llm, threading.Event()))


def test_over_threshold_compacts_before_the_chat_call(db_engine):
    dispatch = make_dispatch(
        db_engine,
        spec_with_limit(token_limit=20),
        exchanges=[
            ("第一轮的很长的问题" * 10, "第一轮的很长的回答" * 10),
            ("第二轮的很长的问题" * 10, "第二轮的很长的回答" * 10),
        ],
    )
    llm = FakeLlm(
        [
            (["摘", "要"], LlmResult(content="此前两轮的摘要", input_tokens=500, output_tokens=50, cost=0.1)),
            (["答"], LlmResult(content="回答")),
        ]
    )

    items = run_engine(db_engine, dispatch, llm)

    kinds = [(item["kind"], item["type"]) for item in items]
    assert kinds == [
        ("row", "run_started"),
        ("row", "context_compacted"),
        ("row", "llm_call"),
        ("stream", "message_started"),
        ("stream", "message_delta"),
        ("row", "llm_result"),
        ("row", "run_finished"),
    ]
    compact = items[1]["payload"]
    assert compact["summary"] == "此前两轮的摘要"
    assert compact["compacted_messages"] == 2
    assert compact["input_tokens"] == 500
    assert compact["output_tokens"] == 50
    assert compact["cost"] == 0.1
    assert compact["duration_ms"] >= 0
    assert compact["before_tokens"] > compact["after_tokens"]

    messages = items[2]["payload"]["messages"]
    assert messages[0] == {"role": "system", "content": SPEC.instructions}
    assert messages[1] == {"role": "user", "content": "<历史摘要>\n此前两轮的摘要"}
    assert "第二轮的很长的问题" in messages[2]["content"]  # kept: the last exchange
    assert "第二轮的很长的回答" in messages[3]["content"]
    assert messages[4]["role"] == "user" and messages[4]["content"].endswith("新问题")
    assert len(messages) == 5

    # call #1 summarized the oldest history; call #2 was the chat
    assert "第一轮" in llm.calls[0][0][-1]["content"]
    assert "第二轮" not in llm.calls[0][0][-1]["content"]  # the kept tail was not summarized
    assert "第二轮" in llm.calls[1][0][2]["content"]


def test_under_threshold_runs_without_compaction(db_engine):
    dispatch = make_dispatch(db_engine, SPEC, exchanges=[("问题一", "回答一")])
    llm = FakeLlm([(["答"], LlmResult(content="回答"))])

    items = run_engine(db_engine, dispatch, llm)

    assert "context_compacted" not in [item["type"] for item in items]
    assert "问题一" in llm.calls[0][0][1]["content"]


def test_a_huge_system_prompt_alone_does_not_trigger(db_engine):
    spec = spec_with_limit(token_limit=20).model_copy(update={"instructions": "极长的系统指令" * 100})
    dispatch = make_dispatch(db_engine, spec, exchanges=[])

    items = run_engine(db_engine, dispatch, FakeLlm([(["答"], LlmResult(content="回答"))]))

    assert "context_compacted" not in [item["type"] for item in items]


def seed_run_with_rows(engine, session_id, *, created_at, rows, epoch=0):

    with Session(engine) as session, session.begin():
        run = ExecutionRun(
            session_id=session_id,
            agent_id=uuid.uuid4(),
            artifact_id=uuid.uuid4(),
            request_id=None,
            content="c",
            params={"project_id": "P"},
            epoch=epoch,
        )
        run.created_at = created_at
        session.add(run)
        session.flush()
        for seq, type_, action_id, payload in rows:
            session.add(RunLog(run_id=run.id, seq=seq, action_id=action_id, type=type_, payload=payload))
        return str(run.id)


def test_rebuild_restarts_at_the_compaction_marker(db_engine):
    from datetime import UTC

    from worker.engine import session_messages

    base = datetime(2026, 10, 15, 10, 0, 0, tzinfo=UTC)
    with Session(db_engine) as session, session.begin():
        conversation = ExecutionSession(agent_id=uuid.uuid4())
        session.add(conversation)
        session.flush()
        session_id = conversation.id

    seed_run_with_rows(
        db_engine,
        session_id,
        created_at=base,
        rows=[
            (1, "run_started", None, {"content": "远古问题", "params": {}}),
            (2, "llm_call", "a", {"messages": []}),
            (3, "llm_result", "a", {"content": "远古回答"}),
            (4, "run_finished", None, {"status": "succeeded", "result": "远古回答"}),
        ],
    )
    seed_run_with_rows(
        db_engine,
        session_id,
        created_at=base + timedelta(seconds=1),
        rows=[
            (1, "run_started", None, {"content": "触发压缩的问题", "params": {}}),
            (
                2,
                "context_compacted",
                None,
                {"summary": "远古对话的摘要", "before_tokens": 99, "after_tokens": 9, "compacted_messages": 2},
            ),
            (3, "llm_call", "b", {"messages": []}),
            (4, "llm_result", "b", {"content": "压缩后的回答"}),
            (5, "run_finished", None, {"status": "succeeded", "result": "压缩后的回答"}),
        ],
    )
    seed_run_with_rows(
        db_engine,
        session_id,
        created_at=base + timedelta(seconds=2),
        rows=[
            (1, "run_started", None, {"content": "之后的问题", "params": {}}),
            (2, "llm_call", "c", {"messages": []}),
            (3, "llm_result", "c", {"content": "之后的回答"}),
            (4, "run_finished", None, {"status": "succeeded", "result": "之后的回答"}),
        ],
    )

    messages = session_messages(db_engine, str(session_id), SPEC, exclude_run_id="00000000-0000-0000-0000-000000000000")

    assert messages == [
        {"role": "user", "content": "<历史摘要>\n远古对话的摘要"},
        {"role": "user", "content": render("触发压缩的问题")},
        {"role": "assistant", "content": "压缩后的回答"},
        {"role": "user", "content": render("之后的问题")},
        {"role": "assistant", "content": "之后的回答"},
    ]


def render(content):
    from worker.engine import render_user_message

    return render_user_message(content, {}, SPEC)


def test_re_dispatch_reuses_the_logged_compaction(db_engine):
    dispatch = make_dispatch(
        db_engine,
        spec_with_limit(token_limit=10_000_000),  # far above: only the logged row may compact
        exchanges=[("历史一", "回答一"), ("历史二", "回答二")],
    )
    # The crash window: compaction row durable, llm_call not.
    with Session(db_engine) as session, session.begin():
        run_id = uuid.UUID(dispatch["run_id"])
        session.add(
            RunLog(
                run_id=run_id,
                seq=1,
                action_id=None,
                type="run_started",
                payload={"content": "新问题", "params": {"project_id": "P"}},
            )
        )
        session.add(
            RunLog(
                run_id=run_id,
                seq=2,
                action_id=None,
                type="context_compacted",
                payload={"summary": "已记录的摘要", "before_tokens": 1, "after_tokens": 1, "compacted_messages": 2},
            )
        )
    llm = FakeLlm([(["答"], LlmResult(content="回答"))])

    items = run_engine(db_engine, dispatch, llm)

    kinds = [item["type"] for item in items if item["kind"] == "row"]
    assert kinds == ["llm_call", "llm_result", "run_finished"]  # no second compaction
    assert len(llm.calls) == 1
    assert "<历史摘要>\n已记录的摘要" in [m["content"] for m in llm.calls[0][0]]


def test_a_resultless_compaction_run_still_marks_the_rebuild(db_engine):
    from worker.engine import session_messages

    base = datetime(2026, 10, 15, 11, 0, 0, tzinfo=UTC)
    with Session(db_engine) as session, session.begin():
        conversation = ExecutionSession(agent_id=uuid.uuid4())
        session.add(conversation)
        session.flush()
        session_id = conversation.id

    seed_run_with_rows(
        db_engine,
        session_id,
        created_at=base,
        rows=[
            (1, "run_started", None, {"content": "远古问题", "params": {}}),
            (2, "llm_result", "a", {"content": "远古回答"}),
            (3, "run_finished", None, {"status": "succeeded", "result": "远古回答"}),
        ],
    )
    # Cancelled after compaction landed, before any llm_result.
    seed_run_with_rows(
        db_engine,
        session_id,
        created_at=base + timedelta(seconds=1),
        rows=[
            (1, "run_started", None, {"content": "被取消的问题", "params": {}}),
            (
                2,
                "context_compacted",
                None,
                {"summary": "已付费的摘要", "before_tokens": 1, "after_tokens": 1, "compacted_messages": 2},
            ),
            (3, "run_finished", None, {"status": "cancelled", "result": None}),
        ],
    )
    seed_run_with_rows(
        db_engine,
        session_id,
        created_at=base + timedelta(seconds=2),
        rows=[
            (1, "run_started", None, {"content": "之后的问题", "params": {}}),
            (2, "llm_result", "c", {"content": "之后的回答"}),
            (3, "run_finished", None, {"status": "succeeded", "result": "之后的回答"}),
        ],
    )

    messages = session_messages(db_engine, str(session_id), SPEC, exclude_run_id="00000000-0000-0000-0000-000000000000")

    assert messages == [
        {"role": "user", "content": "<历史摘要>\n已付费的摘要"},
        {"role": "user", "content": render("之后的问题")},
        {"role": "assistant", "content": "之后的回答"},
    ]


def test_moderate_history_plus_huge_system_prompt_does_not_trigger(db_engine):
    spec = spec_with_limit(token_limit=20).model_copy(update={"instructions": "极长的系统指令" * 100})
    dispatch = make_dispatch(db_engine, spec, exchanges=[("短问题一", "短回答一")])

    items = run_engine(db_engine, dispatch, FakeLlm([(["答"], LlmResult(content="回答"))]))

    assert "context_compacted" not in [item["type"] for item in items]


def test_second_marker_restarts_the_rebuild_again(db_engine):
    from worker.engine import session_messages

    base = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)
    with Session(db_engine) as session, session.begin():
        conversation = ExecutionSession(agent_id=uuid.uuid4())
        session.add(conversation)
        session.flush()
        session_id = conversation.id

    seed_run_with_rows(
        db_engine,
        session_id,
        created_at=base,
        rows=[
            (1, "run_started", None, {"content": "第一段", "params": {}}),
            (
                2,
                "context_compacted",
                None,
                {"summary": "第一次摘要", "before_tokens": 1, "after_tokens": 1, "compacted_messages": 2},
            ),
            (3, "llm_result", "a", {"content": "第一次回答"}),
            (4, "run_finished", None, {"status": "succeeded", "result": "第一次回答"}),
        ],
    )
    seed_run_with_rows(
        db_engine,
        session_id,
        created_at=base + timedelta(seconds=1),
        rows=[
            (1, "run_started", None, {"content": "中间段", "params": {}}),
            (2, "llm_result", "b", {"content": "中间回答"}),
            (3, "run_finished", None, {"status": "succeeded", "result": "中间回答"}),
        ],
    )
    seed_run_with_rows(
        db_engine,
        session_id,
        created_at=base + timedelta(seconds=2),
        rows=[
            (1, "run_started", None, {"content": "第二段", "params": {}}),
            (
                2,
                "context_compacted",
                None,
                {"summary": "第二次摘要", "before_tokens": 1, "after_tokens": 1, "compacted_messages": 2},
            ),
            (3, "llm_result", "c", {"content": "第二次回答"}),
            (4, "run_finished", None, {"status": "succeeded", "result": "第二次回答"}),
        ],
    )

    messages = session_messages(db_engine, str(session_id), SPEC, exclude_run_id="00000000-0000-0000-0000-000000000000")

    assert messages == [
        {"role": "user", "content": "<历史摘要>\n第二次摘要"},
        {"role": "user", "content": render("第二段")},
        {"role": "assistant", "content": "第二次回答"},
    ]
