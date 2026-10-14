import pytest
from agentops import ConflictError, create_client

SESSION_ID = "11111111-1111-1111-1111-111111111111"
RUN_ID = "22222222-2222-2222-2222-222222222222"


def run_view(**over):
    view = {
        "run_id": RUN_ID,
        "session_id": SESSION_ID,
        "status": "pending",
        "result": None,
        "error": None,
        "cancel_requested": False,
    }
    view.update(over)
    return view


@pytest.fixture
def client(fake):
    fake.route("POST", "/v1/sessions", status=201, body={"session_id": SESSION_ID})
    with create_client("k", base_url=fake.base_url) as client:
        yield client


def test_get_agent_parses_input_declaration(fake, client):
    fake.route(
        "GET",
        "/v1/agent",
        body={
            "input": [
                {"key": "project_id", "type": "string", "required": True, "description": "项目 ID"},
                {
                    "key": "include_archived",
                    "type": "boolean",
                    "required": False,
                    "description": "是否包含",
                    "default": False,
                },
            ]
        },
    )

    agent = client.get_agent()

    assert agent.input[0].key == "project_id"
    assert agent.input[0].required is True
    assert agent.input[1].default is False


def test_get_session_is_a_local_handle(fake, client):
    session = client.new_session()
    fetched = client.get_session(SESSION_ID)

    assert session.id == SESSION_ID
    assert fetched.id == SESSION_ID
    assert len(fake.requests) == 1


def test_submit_returns_run_snapshot_and_sends_all_fields(fake, client):
    fake.route("POST", f"/v1/sessions/{SESSION_ID}/runs", status=201, body=run_view())
    session = client.new_session()

    run = session.submit("hi", params={"project_id": "P"}, output_schema={"type": "object"}, request_id="r1")

    assert (run.id, run.session_id, run.status) == (RUN_ID, SESSION_ID, "pending")
    assert fake.requests[-1]["json"] == {
        "content": "hi",
        "params": {"project_id": "P"},
        "output_schema": {"type": "object"},
        "request_id": "r1",
    }


def test_submit_idempotent_replay_returns_the_same_run(fake, client):
    fake.route("POST", f"/v1/sessions/{SESSION_ID}/runs", status=200, body=run_view())
    session = client.new_session()

    run = session.submit("hi")

    assert run.id == RUN_ID


def test_submit_maps_conflict(fake, client):
    fake.route(
        "POST", f"/v1/sessions/{SESSION_ID}/runs", status=409, body={"error": {"code": "conflict", "message": "busy"}}
    )
    session = client.new_session()

    with pytest.raises(ConflictError):
        session.submit("hi")


def test_get_run_loads_snapshot(fake, client):
    fake.route("GET", f"/v1/runs/{RUN_ID}", body=run_view(status="running"))

    run = client.get_run(RUN_ID)

    assert run.status == "running"
    assert run.cancel_requested is False


def test_cancel_refreshes_the_snapshot(fake, client):
    fake.route("POST", f"/v1/sessions/{SESSION_ID}/runs", status=201, body=run_view())
    fake.route("POST", f"/v1/runs/{RUN_ID}/cancel", body=run_view(cancel_requested=True))
    session = client.new_session()
    run = session.submit("hi")

    run.cancel()

    assert run.cancel_requested is True
    assert fake.requests[-1]["method"] == "POST"
