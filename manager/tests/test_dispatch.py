import time
from uuid import UUID

from shared.models import ExecutionRun
from sqlalchemy import select
from sqlalchemy.orm import Session
from util import FakeWorker, submit, write_log


def test_dispatch_after_submit(client, auth_headers, db_engine):
    worker = FakeWorker("w-dispatch")
    try:
        session_id = client.post("/v1/sessions", headers=auth_headers).json()["session_id"]
        body = submit(client, auth_headers, session_id)

        type_, data = worker.read()
        assert type_ == "dispatch"
        assert data["run_id"] == body["run_id"]
        assert data["session_id"] == session_id
        assert data["epoch"] == 1
        assert data["input"]["content"] == "hi"
        assert data["input"]["caller"] == "backend"
        assert data["snapshot"]["mode"] == "react"

        with Session(db_engine) as session:
            stored = session.scalar(select(ExecutionRun).where(ExecutionRun.id == UUID(data["run_id"])))
        assert stored.epoch == 1
    finally:
        worker.close()


def test_submit_without_worker_stays_pending(client, auth_headers):
    session_id = client.post("/v1/sessions", headers=auth_headers).json()["session_id"]
    body = submit(client, auth_headers, session_id)
    time.sleep(0.5)

    status = client.get(f"/v1/runs/{body['run_id']}", headers=auth_headers).json()
    assert status["status"] == "pending"


def test_cancel_pushes_frame(client, auth_headers):
    worker = FakeWorker("w-cancel")
    try:
        session_id = client.post("/v1/sessions", headers=auth_headers).json()["session_id"]
        body = submit(client, auth_headers, session_id)
        assert worker.read()[0] == "dispatch"

        response = client.post(f"/v1/runs/{body['run_id']}/cancel", headers=auth_headers)
        assert response.status_code == 200

        type_, data = worker.read()
        assert type_ == "cancel"
        assert data["run_id"] == body["run_id"]
    finally:
        worker.close()


def test_terminal_run_not_dispatched(client, auth_headers, db_engine):
    worker = FakeWorker("w-terminal")
    try:
        session_id = client.post("/v1/sessions", headers=auth_headers).json()["session_id"]
        # A run that was already terminal before any dispatch pass sees it.
        dead = ExecutionRun(
            session_id=UUID(session_id),
            agent_id=_seeded_agent_id(db_engine),
            artifact_id=_seeded_artifact_id(db_engine),
            request_id=None,
            content="terminal",
            params={},
        )
        with Session(db_engine) as session, session.begin():
            session.add(dead)
            session.flush()
            write_log(db_engine, dead.id, 1, "run_started", {})
            write_log(db_engine, dead.id, 2, "run_finished", {"status": "succeeded", "result": "x"})

        fresh = submit(client, auth_headers, session_id, content="fresh")

        type_, data = worker.read()
        assert type_ == "dispatch"
        assert data["run_id"] == fresh["run_id"]
    finally:
        worker.close()


def _seeded_agent_id(db_engine) -> UUID:
    from shared.models import AgentDefinition

    with Session(db_engine) as session:
        return session.scalar(select(AgentDefinition.id))


def _seeded_artifact_id(db_engine) -> UUID:
    from shared.models import ReleaseArtifact

    with Session(db_engine) as session:
        return session.scalar(select(ReleaseArtifact.id))
