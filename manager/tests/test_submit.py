import uuid

from shared.models import ExecutionSession, RunLog
from sqlalchemy import select
from sqlalchemy.orm import Session


def new_session(client, auth_headers) -> str:
    return client.post("/v1/sessions", headers=auth_headers).json()["session_id"]


def submit(client, auth_headers, session_id: str, **body):
    return client.post(f"/v1/sessions/{session_id}/runs", headers=auth_headers, json=body)


def write_log(db_engine, run_id, seq: int, type_: str, payload: dict | None = None):
    with Session(db_engine) as session, session.begin():
        session.add(RunLog(run_id=run_id, seq=seq, action_id=None, type=type_, payload=payload or {}))


def test_submit_creates_pending_run(client, auth_headers, db_engine):
    session_id = new_session(client, auth_headers)

    response = submit(client, auth_headers, session_id, content="hi", params={"project_id": "PRJ-1"})

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    assert body["cancel_requested"] is False
    with Session(db_engine) as session:
        stored = session.scalar(select(ExecutionSession).where(ExecutionSession.id == session_id))
    assert stored.active_run_id is not None


def test_submit_replay_returns_same_run(client, auth_headers):
    session_id = new_session(client, auth_headers)
    first = submit(client, auth_headers, session_id, content="hi", params={"project_id": "P"}, request_id="job-1")
    second = submit(client, auth_headers, session_id, content="hi", params={"project_id": "P"}, request_id="job-1")

    assert second.status_code == 200
    assert second.json()["run_id"] == first.json()["run_id"]


def test_submit_conflict_while_active(client, auth_headers):
    session_id = new_session(client, auth_headers)
    assert submit(client, auth_headers, session_id, content="a", params={"project_id": "P"}).status_code == 201

    conflict = submit(client, auth_headers, session_id, content="b", params={"project_id": "P"})

    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "conflict"


def test_lease_reclaimed_after_terminal(client, auth_headers, db_engine):
    session_id = new_session(client, auth_headers)
    run_id = submit(client, auth_headers, session_id, content="a", params={"project_id": "P"}).json()["run_id"]
    write_log(db_engine, run_id, 1, "run_started", {"content": "a"})
    write_log(db_engine, run_id, 2, "run_finished", {"status": "succeeded", "result": "done"})

    again = submit(client, auth_headers, session_id, content="b", params={"project_id": "P"})

    assert again.status_code == 201


def test_params_validation(client, auth_headers):
    session_id = new_session(client, auth_headers)

    missing = submit(client, auth_headers, session_id, content="hi", params={})
    unknown = submit(client, auth_headers, session_id, content="hi", params={"project_id": "P", "extra": 1})
    wrong_type = submit(client, auth_headers, session_id, content="hi", params={"project_id": 42})
    ok = submit(client, auth_headers, session_id, content="hi", params={"project_id": "P"})

    assert missing.status_code == 422
    assert unknown.status_code == 422
    assert wrong_type.status_code == 422
    assert ok.status_code == 201


def test_status_follows_log_tail(client, auth_headers, db_engine):
    session_id = new_session(client, auth_headers)
    run_id = submit(client, auth_headers, session_id, content="hi", params={"project_id": "P"}).json()["run_id"]

    assert client.get(f"/v1/runs/{run_id}", headers=auth_headers).json()["status"] == "pending"

    write_log(db_engine, run_id, 1, "run_started", {"content": "hi"})
    assert client.get(f"/v1/runs/{run_id}", headers=auth_headers).json()["status"] == "running"

    write_log(db_engine, run_id, 2, "run_finished", {"status": "succeeded", "result": "hello"})
    final = client.get(f"/v1/runs/{run_id}", headers=auth_headers).json()
    assert final["status"] == "succeeded"
    assert final["result"] == "hello"


def test_cancel_sets_flag_idempotent(client, auth_headers):
    session_id = new_session(client, auth_headers)
    run_id = submit(client, auth_headers, session_id, content="hi", params={"project_id": "P"}).json()["run_id"]

    first = client.post(f"/v1/runs/{run_id}/cancel", headers=auth_headers)
    second = client.post(f"/v1/runs/{run_id}/cancel", headers=auth_headers)

    assert first.status_code == 200
    assert first.json()["cancel_requested"] is True
    assert second.status_code == 200
    assert client.get(f"/v1/runs/{run_id}", headers=auth_headers).json()["status"] == "pending"


def test_submit_missing_session_404(client, auth_headers):
    response = submit(client, auth_headers, str(uuid.uuid4()), content="hi", params={"project_id": "P"})

    assert response.status_code == 404


def test_framework_422_uses_error_envelope(client, auth_headers):
    session_id = new_session(client, auth_headers)

    response = submit(client, auth_headers, session_id, params={"project_id": "P"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "contract"


def test_cross_session_replay_returns_same_run(client, auth_headers):
    first_session = new_session(client, auth_headers)
    other_session = new_session(client, auth_headers)
    first = submit(client, auth_headers, first_session, content="a", params={"project_id": "P"}, request_id="job-x")

    replay = submit(client, auth_headers, other_session, content="a", params={"project_id": "P"}, request_id="job-x")

    assert replay.status_code == 200
    assert replay.json()["run_id"] == first.json()["run_id"]


def test_status_terminal_branches(client, auth_headers, db_engine):
    session_id = new_session(client, auth_headers)
    failed_run = submit(client, auth_headers, session_id, content="a", params={"project_id": "P"}).json()["run_id"]
    write_log(db_engine, failed_run, 1, "run_started", {})
    write_log(db_engine, failed_run, 2, "run_failed", {"error": "boom"})
    failed = client.get(f"/v1/runs/{failed_run}", headers=auth_headers).json()

    other_session = new_session(client, auth_headers)
    cancelled_run = submit(client, auth_headers, other_session, content="a", params={"project_id": "P"}).json()[
        "run_id"
    ]
    write_log(db_engine, cancelled_run, 1, "run_started", {})
    write_log(db_engine, cancelled_run, 2, "run_finished", {"status": "cancelled"})
    cancelled = client.get(f"/v1/runs/{cancelled_run}", headers=auth_headers).json()

    assert failed["status"] == "failed"
    assert failed["error"] == "boom"
    assert cancelled["status"] == "cancelled"
