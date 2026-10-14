from typing import Any
from uuid import UUID

from pydantic import BaseModel
from shared.events import EventType
from shared.models import AgentDefinition, ExecutionRun, ExecutionSession, ReleaseArtifact, RunLog
from shared.spec import AgentSpec
from sqlalchemy import select
from sqlalchemy.orm import Session

from manager import db
from manager.auth import ApiError


class RunSubmitBody(BaseModel):
    content: str
    params: dict = {}
    output_schema: dict | None = None
    request_id: str | None = None


class RunView(BaseModel):
    run_id: UUID
    session_id: UUID
    status: str
    result: Any = None
    error: str | None = None
    cancel_requested: bool


_TERMINAL_TYPES = {EventType.run_finished, EventType.run_failed}

# bool is an int in Python, so number excludes it explicitly.
_TYPE_CHECKS = {
    "string": lambda value: isinstance(value, str),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
}


def latest_artifact(agent: AgentDefinition) -> ReleaseArtifact:
    with Session(db.engine) as session:
        artifact = session.scalar(
            select(ReleaseArtifact)
            .where(ReleaseArtifact.target_kind == "agent", ReleaseArtifact.target_id == agent.id)
            .order_by(ReleaseArtifact.version.desc())
            .limit(1)
        )
    if artifact is None:
        raise ApiError(404, "not_found", "the agent has no published version")
    return artifact


def latest_spec(agent: AgentDefinition) -> AgentSpec:
    return AgentSpec.model_validate(latest_artifact(agent).snapshot)


def validate_params(spec: AgentSpec, params: dict) -> None:
    declared = {param.key: param for param in spec.input}
    for key, value in params.items():
        if key not in declared:
            raise ApiError(422, "contract", f"unknown param: {key}")
        if not _TYPE_CHECKS[declared[key].type](value):
            raise ApiError(422, "contract", f"param {key} must be a {declared[key].type}")
    for param in spec.input:
        if param.required and param.key not in params:
            raise ApiError(422, "contract", f"missing required param: {param.key}")


def submit(agent: AgentDefinition, session_id: UUID, body: RunSubmitBody) -> tuple[RunView, int]:
    spec = latest_spec(agent)
    validate_params(spec, body.params)

    with Session(db.engine) as session, session.begin():
        row = session.scalar(
            select(ExecutionSession)
            .where(ExecutionSession.id == session_id, ExecutionSession.agent_id == agent.id)
            .with_for_update()
        )
        if row is None:
            raise ApiError(404, "not_found", "no such session")

        if body.request_id is not None:
            existing = session.scalar(
                select(ExecutionRun).where(ExecutionRun.agent_id == agent.id, ExecutionRun.request_id == body.request_id)
            )
            if existing is not None:
                return build_view(existing), 200

        if row.active_run_id is not None and _is_active(session, row.active_run_id):
            raise ApiError(409, "conflict", "the session already has an active run")

        run = ExecutionRun(
            session_id=session_id,
            agent_id=agent.id,
            artifact_id=latest_artifact(agent).id,
            request_id=body.request_id,
            content=body.content,
            params=body.params,
            output_schema=body.output_schema,
        )
        session.add(run)
        session.flush()
        row.active_run_id = run.id
        return build_view(run), 201


def get_run(agent: AgentDefinition, run_id: UUID) -> RunView:
    with Session(db.engine) as session:
        run = session.scalar(select(ExecutionRun).where(ExecutionRun.id == run_id, ExecutionRun.agent_id == agent.id))
    if run is None:
        raise ApiError(404, "not_found", "no such run")
    return build_view(run)


def request_cancel(agent: AgentDefinition, run_id: UUID) -> RunView:
    with Session(db.engine) as session, session.begin():
        run = session.scalar(select(ExecutionRun).where(ExecutionRun.id == run_id, ExecutionRun.agent_id == agent.id))
        if run is None:
            raise ApiError(404, "not_found", "no such run")
        run.cancel_requested = True
        session.flush()
        return build_view(run)


def _is_active(session: Session, run_id: UUID) -> bool:
    tail = session.scalar(select(RunLog).where(RunLog.run_id == run_id).order_by(RunLog.seq.desc()).limit(1))
    return tail is None or tail.type not in _TERMINAL_TYPES


def build_view(run: ExecutionRun) -> RunView:
    with Session(db.engine) as session:
        tail = session.scalar(select(RunLog).where(RunLog.run_id == run.id).order_by(RunLog.seq.desc()).limit(1))

    result = None
    error = None
    if tail is None:
        status = "pending"
    elif tail.type == EventType.run_finished:
        status = tail.payload.get("status", "succeeded")
        result = tail.payload.get("result")
    elif tail.type == EventType.run_failed:
        status = "failed"
        error = tail.payload.get("error")
    else:
        status = "running"

    return RunView(
        run_id=run.id,
        session_id=run.session_id,
        status=status,
        result=result,
        error=error,
        cancel_requested=run.cancel_requested,
    )
