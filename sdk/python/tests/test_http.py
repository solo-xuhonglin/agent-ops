import pytest
from agentops import AgentOpsError, AuthError, ConflictError, ContractError, HttpError, NotFoundError
from agentops.http import Http


def test_json_returns_body_and_sends_bearer_key(fake):
    fake.route("GET", "/v1/agent", body={"input": []})
    http = Http(fake.base_url, "k1")

    assert http.json("GET", "/v1/agent") == {"input": []}
    assert fake.requests[0]["headers"]["authorization"] == "Bearer k1"


def test_json_post_sends_body(fake):
    fake.route("POST", "/v1/sessions", status=201, body={"session_id": "s"})
    http = Http(fake.base_url, "k")

    http.json("POST", "/v1/sessions", {"content": "hi"})

    assert fake.requests[0]["json"] == {"content": "hi"}


@pytest.mark.parametrize(
    ("status", "cls"),
    [(401, AuthError), (404, NotFoundError), (409, ConflictError), (422, ContractError), (500, HttpError)],
)
def test_error_envelope_maps_by_status(fake, status, cls):
    fake.route("GET", "/v1/x", status=status, body={"error": {"code": "c", "message": "m"}})
    http = Http(fake.base_url, "k")

    with pytest.raises(cls) as exc:
        http.json("GET", "/v1/x")

    assert isinstance(exc.value, AgentOpsError)
    assert exc.value.code == "c"
    assert exc.value.message == "m"
