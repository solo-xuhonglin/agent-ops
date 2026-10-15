import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from shared.spec import ToolSpec
from worker.tool_exec import CredentialUnavailable, ToolError, execute_tool, render_request


class FakeUpstream:
    """A mock external service recording what it receives."""

    def __init__(self, *, status=200, body="ok"):
        self.received: list[dict] = []
        self.status = status
        self.body = body
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())

    def _handler(self):
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            def _dispatch(self):
                length = int(self.headers.get("Content-Length") or 0)
                upstream.received.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body": self.rfile.read(length).decode() if length else None,
                    }
                )
                payload = upstream.body.encode()
                self.send_response(upstream.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = _dispatch
            do_POST = _dispatch

            def log_message(self, *args):
                pass

        return Handler

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def start(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def upstream():
    service = FakeUpstream().start()
    yield service
    service.close()


def tool(**over):
    base = {"name": "fetch", "description": "d", "method": "GET", "url_template": "http://dead.test/x"}
    base.update(over)
    return ToolSpec(**base)


def test_renders_args_into_url_headers_and_body():
    spec = tool(
        method="POST",
        url_template="http://u.test/projects/{{args.project_id}}",
        headers={"X-Token": "{{args.token}}"},
        body_template='{"id": "{{args.project_id}}"}',
    )

    method, url, headers, body = render_request(spec, {"project_id": "P1", "token": "T"}, credential=None)

    assert (method, url, body) == ("POST", "http://u.test/projects/P1", '{"id": "P1"}')
    assert headers["X-Token"] == "T"


def test_renders_the_credential_without_touching_the_spec(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_SVC", "secret-value")
    spec = tool(headers={"Authorization": "Bearer {{credential}}"}, credential="svc")

    _, _, headers, _ = render_request(spec, {}, credential="secret-value")

    assert headers["Authorization"] == "Bearer secret-value"
    # The spec (what the log row carries) keeps the placeholder, never the value.
    assert spec.headers["Authorization"] == "Bearer {{credential}}"


def test_unresolved_placeholder_is_an_error():
    with pytest.raises(ToolError):
        render_request(tool(url_template="http://u.test/{{args.missing}}"), {}, credential=None)


def test_credential_env_missing_raises():
    from worker.tool_exec import resolve_credential

    with pytest.raises(CredentialUnavailable):
        resolve_credential("nope")


def test_execute_sends_idempotency_key_and_returns_the_body(upstream):
    spec = tool(url_template=upstream.url + "/items/{{args.id}}")

    outcome = execute_tool("run-1", "act-1", spec, {"id": "42"})

    assert outcome == {
        "ok": True,
        "result": "ok",
        "error": None,
        "duration_ms": outcome["duration_ms"],
        "truncated": False,
    }
    assert upstream.received[0]["path"] == "/items/42"
    assert upstream.received[0]["headers"]["idempotency-key"] == "run-1:act-1"


def test_execute_reports_http_errors(upstream):
    upstream.status = 500
    outcome = execute_tool("r", "a", tool(url_template=upstream.url), {})

    assert outcome["ok"] is False
    assert "500" in outcome["error"]


def test_execute_reports_connection_failures():
    outcome = execute_tool("r", "a", tool(url_template="http://127.0.0.1:9/x"), {})

    assert outcome["ok"] is False
    assert outcome["error"]


def test_execute_trims_oversized_responses(upstream):
    upstream.body = json.dumps({"blob": "x" * 100})
    spec = tool(url_template=upstream.url, response_max_bytes=16)

    outcome = execute_tool("r", "a", spec, {})

    assert outcome["truncated"] is True
    assert len(outcome["result"]) == 16
