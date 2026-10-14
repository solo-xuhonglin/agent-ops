from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from shared.models import AgentDefinition, ExecutionSession, ReleaseArtifact
from shared.spec import AgentSpec
from sqlalchemy import select
from sqlalchemy.orm import Session

from manager import db
from manager.auth import ApiError, require_agent

router = APIRouter(prefix="/v1")


class SessionView(BaseModel):
    session_id: UUID


def latest_spec(agent: AgentDefinition) -> AgentSpec:
    with Session(db.engine) as session:
        artifact = session.scalar(
            select(ReleaseArtifact)
            .where(ReleaseArtifact.target_kind == "agent", ReleaseArtifact.target_id == agent.id)
            .order_by(ReleaseArtifact.version.desc())
            .limit(1)
        )
    if artifact is None:
        raise ApiError(404, "not_found", "the agent has no published version")
    return AgentSpec.model_validate(artifact.snapshot)


@router.get("/agent")
def get_agent(agent: AgentDefinition = Depends(require_agent)) -> dict:
    spec = latest_spec(agent)
    return {"input": [param.model_dump() for param in spec.input]}


@router.post("/sessions", status_code=201)
def new_session(agent: AgentDefinition = Depends(require_agent)) -> SessionView:
    with Session(db.engine) as session, session.begin():
        row = ExecutionSession(agent_id=agent.id)
        session.add(row)
        session.flush()
        return SessionView(session_id=row.id)
