from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from shared.models import AgentDefinition, ExecutionSession
from sqlalchemy.orm import Session

from manager import db, internal, runs
from manager.auth import require_agent
from manager.runs import RunSubmitBody, RunView

router = APIRouter(prefix="/v1")


class SessionView(BaseModel):
    session_id: UUID


@router.get("/agent")
def get_agent(agent: AgentDefinition = Depends(require_agent)) -> dict:
    spec = runs.latest_spec(agent)
    return {"input": [param.model_dump() for param in spec.input]}


@router.post("/sessions", status_code=201)
def new_session(agent: AgentDefinition = Depends(require_agent)) -> SessionView:
    with Session(db.engine) as session, session.begin():
        row = ExecutionSession(agent_id=agent.id)
        session.add(row)
        session.flush()
        return SessionView(session_id=row.id)


@router.post("/sessions/{session_id}/runs")
def submit_run(session_id: UUID, body: RunSubmitBody, agent: AgentDefinition = Depends(require_agent)):
    view, status_code = runs.submit(agent, session_id, body)
    internal.request_dispatch()
    return JSONResponse(content=view.model_dump(mode="json"), status_code=status_code)


@router.get("/runs/{run_id}")
def get_run(run_id: UUID, agent: AgentDefinition = Depends(require_agent)) -> RunView:
    return runs.get_run(agent, run_id)


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: UUID, agent: AgentDefinition = Depends(require_agent)) -> RunView:
    view = runs.request_cancel(agent, run_id)
    internal.broadcast("cancel", {"run_id": str(run_id)})
    return view
