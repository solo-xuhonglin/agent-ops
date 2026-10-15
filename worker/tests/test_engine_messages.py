import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from shared.models import Base, ExecutionRun, ExecutionSession, RunLog
from shared.spec import AgentSpec, InputParam
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from worker.engine import render_user_message, session_messages


@pytest.fixture()
def db_engine():
    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine, tables=[ExecutionRun.__table__, ExecutionSession.__table__, RunLog.__table__])
    yield engine
    # Restore the session-wide baseline (run_log only).
    Base.metadata.drop_all(engine, tables=[ExecutionRun.__table__, ExecutionSession.__table__])


from helpers import SPEC


def test_render_matches_the_contract_example():
    rendered = render_user_message(
        "分析这个项目的进度并生成概要", {"project_id": "PRJ-1042", "include_archived": True}, SPEC
    )

    assert rendered == (
        "<输入参数>\n"
        "- project_id（项目 ID，来自项目管理系统，形如 PRJ-1042）: PRJ-1042\n"
        "- include_archived（是否包含已归档项目）: true\n"
        "</输入参数>\n"
        "\n"
        "分析这个项目的进度并生成概要"
    )


def test_render_fills_defaults_and_omits_absent_without_default():
    spec = AgentSpec(
        mode="react",
        name="demo",
        instructions="x",
        input=[
            InputParam(key="flag", type="boolean", required=False, description="开关", default=False),
            InputParam(key="extra", type="string", required=False, description="可选补充"),
        ],
        model_id="fake",
        model_params={},
        max_iterations=1,
    )

    rendered = render_user_message("正文", {}, spec)

    assert rendered == "<输入参数>\n- flag（开关）: false\n</输入参数>\n\n正文"


def test_render_without_declared_params_has_no_block():
    spec = AgentSpec(
        mode="react", name="d", instructions="x", input=[], model_id="fake", model_params={}, max_iterations=1
    )

    assert render_user_message("只是对话", {}, spec) == "只是对话"


def seed_run(engine, session_id, *, created_at, rows):
    with Session(engine) as session, session.begin():
        run = ExecutionRun(
            session_id=session_id,
            agent_id=uuid.uuid4(),
            artifact_id=uuid.uuid4(),
            request_id=None,
            content="问题",
            params={"project_id": "PRJ-1"},
        )
        run.created_at = created_at
        session.add(run)
        session.flush()
        for seq, type_, action_id, payload in rows:
            session.add(RunLog(run_id=run.id, seq=seq, action_id=action_id, type=type_, payload=payload))


def seed_run_with_id(engine, session_id, *, created_at, rows):
    seed_run(engine, session_id, created_at=created_at, rows=rows)
    # seed_run does not return ids; look the run up by its newest created_at.
    with Session(engine) as session:
        from shared.models import ExecutionRun as _R
        from sqlalchemy import select as _s

        run = session.scalars(_s(_R.id).where(_R.session_id == session_id).order_by(_R.created_at.desc())).first()
        return str(run)


def test_session_messages_rebuilds_prior_runs_in_order(db_engine):
    with Session(db_engine) as session, session.begin():
        conversation = ExecutionSession(agent_id=uuid.uuid4())
        session.add(conversation)
        session.flush()
        session_id = conversation.id
    base = datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC)

    seed_run(
        db_engine,
        session_id,
        created_at=base,
        rows=[
            (
                1,
                "run_started",
                None,
                {"content": "问题一", "params": {"project_id": "PRJ-1"}, "session_id": str(session_id)},
            ),
            (2, "llm_call", "a1", {"messages": []}),
            (3, "llm_result", "a1", {"content": "答案一"}),
            (4, "run_finished", None, {"status": "succeeded", "result": "答案一"}),
        ],
    )
    seed_run(
        db_engine,
        session_id,
        created_at=base + timedelta(seconds=1),
        rows=[
            (
                1,
                "run_started",
                None,
                {"content": "问题二", "params": {"project_id": "PRJ-2"}, "session_id": str(session_id)},
            ),
            (2, "llm_call", "a2", {"messages": []}),
            (3, "llm_result", "a2", {"content": "答案二"}),
            (4, "run_failed", None, {"error": "boom"}),
        ],
    )
    seed_run(
        db_engine,
        session_id,
        created_at=base + timedelta(seconds=2),
        rows=[
            (
                1,
                "run_started",
                None,
                {"content": "进行中的问题", "params": {"project_id": "PRJ-3"}, "session_id": str(session_id)},
            ),
            (2, "llm_call", "a3", {"messages": []}),
        ],
    )

    messages = session_messages(db_engine, str(session_id), SPEC, exclude_run_id="nonexistent")

    assert messages == [
        {"role": "user", "content": render_user_message("问题一", {"project_id": "PRJ-1"}, SPEC)},
        {"role": "assistant", "content": "答案一"},
        {"role": "user", "content": render_user_message("问题二", {"project_id": "PRJ-2"}, SPEC)},
        {"role": "assistant", "content": "答案二"},
    ]


def test_session_messages_excludes_the_current_run_even_with_a_completed_exchange(db_engine):
    with Session(db_engine) as session, session.begin():
        conversation = ExecutionSession(agent_id=uuid.uuid4())
        session.add(conversation)
        session.flush()
        session_id = conversation.id
    base = datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC)

    seed_run(
        db_engine,
        session_id,
        created_at=base,
        rows=[
            (
                1,
                "run_started",
                None,
                {"content": "旧问题", "params": {"project_id": "PRJ-1"}, "session_id": str(session_id)},
            ),
            (2, "llm_call", "a1", {"messages": []}),
            (3, "llm_result", "a1", {"content": "旧答案"}),
            (4, "run_finished", None, {"status": "succeeded", "result": "旧答案"}),
        ],
    )
    # Crash window: llm_result is durable, terminal is not. The re-dispatch
    # rebuilds history without this run's own half-finished exchange.
    current = seed_run_with_id(
        db_engine,
        session_id,
        created_at=base + timedelta(seconds=1),
        rows=[
            (
                1,
                "run_started",
                None,
                {"content": "新问题", "params": {"project_id": "PRJ-2"}, "session_id": str(session_id)},
            ),
            (2, "llm_call", "a2", {"messages": []}),
            (3, "llm_result", "a2", {"content": "残响"}),
        ],
    )

    messages = session_messages(db_engine, str(session_id), SPEC, exclude_run_id=current)

    assert messages == [
        {"role": "user", "content": render_user_message("旧问题", {"project_id": "PRJ-1"}, SPEC)},
        {"role": "assistant", "content": "旧答案"},
    ]
