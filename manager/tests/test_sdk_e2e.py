import os
import threading

import pytest
from sqlalchemy.orm import Session
from util import TEST_KEY, FakeWorker, live_server
from worker.client import _pump
from worker.llm import FakeLlm, LlmResult
from zszc.agentops import create_client

from manager import internal


@pytest.fixture(autouse=True)
def fast_state(monkeypatch):
    monkeypatch.setenv("STATE_INTERVAL", "0.2")
    monkeypatch.setenv("MANAGER_HOST", "127.0.0.1")


def serve_dispatched_run(monkeypatch_port, worker):
    """The real worker path for one dispatch: pump the engine on a Run connection."""
    type_, dispatch = worker.read()
    assert type_ == "dispatch"
    import os

    os.environ["MANAGER_PORT"] = monkeypatch_port
    _pump(dispatch, FakeLlm([(["你", "好"], LlmResult(content="你好"))]))


def test_submit_engine_execution_and_stream_back_to_the_sdk(db_engine, monkeypatch):
    with live_server() as base:
        monkeypatch.setenv("MANAGER_PORT", str(internal.port))
        worker = FakeWorker("w-e2e")
        try:
            with create_client(TEST_KEY, base_url=base, poll_interval=0.05) as client:
                agent = client.get_agent()
                assert [param.key for param in agent.input] == ["project_id"]

                session = client.new_session()
                run = session.submit("hello sdk", params={"project_id": "P"})

                stream = session.stream()
                first = next(stream)
                assert first.type == "state"
                assert session.current_run is None

                worker_thread = threading.Thread(target=serve_dispatched_run, args=(str(internal.port), worker))
                worker_thread.start()
                try:
                    types = []
                    for event in stream:
                        types.append(event.type)
                        if event.type == "run_finished":
                            break
                finally:
                    stream.close()
                    worker_thread.join(timeout=5)

                assert types == [
                    "run_started",
                    "llm_call",
                    "message_started",
                    "message_delta",
                    "message_delta",
                    "llm_result",
                    "run_finished",
                ]
                assert session.current_run is None

                result = run.wait()
                assert result.output == "你好"
                assert run.status == "succeeded"

                again = client.get_run(run.id).wait()
                assert again.output == "你好"

                # A fresh subscribe replays the run from its start, with the
                # llm_call projected (no messages on the wire).
                replayed = client.get_session(session.id).stream()
                try:
                    replay_events = [next(replayed) for _ in range(5)]
                finally:
                    replayed.close()
                assert [e.type for e in replay_events] == [
                    "state",
                    "run_started",
                    "llm_call",
                    "llm_result",
                    "run_finished",
                ]
                assert "messages" not in replay_events[2].data
        finally:
            worker.close()


def test_tool_round_trips_through_the_stream(db_engine):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    received = []

    class Upstream(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append({"path": self.path})
            payload = b'{"items": ["A"]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    import threading as _t

    _t.Thread(target=server.serve_forever, daemon=True).start()
    upstream_url = f"http://127.0.0.1:{server.server_address[1]}"

    from shared.models import AgentDefinition, ReleaseArtifact
    from shared.spec import AgentSpec, ToolSpec
    from sqlalchemy import select as sa_select

    try:
        with Session(db_engine) as session:
            agent = session.scalars(sa_select(AgentDefinition)).first()
            artifact = session.scalars(
                sa_select(ReleaseArtifact)
                .where(ReleaseArtifact.target_id == agent.id)
                .order_by(ReleaseArtifact.version.desc())
                .limit(1)
            ).first()
            spec = AgentSpec.model_validate(artifact.snapshot).model_copy(
                update={
                    "tools": [
                        ToolSpec(
                            name="fetch", description="取数", url_template=upstream_url + "/items/{{args.project_id}}"
                        )
                    ]
                }
            )
        with Session(db_engine) as session, session.begin():
            session.add(
                ReleaseArtifact(
                    target_kind="agent",
                    target_id=agent.id,
                    version=artifact.version + 1,
                    snapshot=spec.model_dump(mode="json"),
                )
            )

        with live_server() as base:
            worker = FakeWorker("w-tool")
            try:
                with create_client(TEST_KEY, base_url=base, poll_interval=0.05) as client:
                    session_handle = client.new_session()
                    run = session_handle.submit("查项目", params={"project_id": "P1"})

                    stream = session_handle.stream()
                    next(stream)  # state

                    def worker_side():
                        _type, dispatch = worker.read()
                        os.environ["MANAGER_PORT"] = str(internal.port)
                        _pump(
                            dispatch,
                            FakeLlm(
                                [
                                    (
                                        [],
                                        LlmResult(
                                            content="",
                                            tool_calls=[{"name": "fetch", "arguments": {"project_id": "P1"}}],
                                        ),
                                    ),
                                    (["完"], LlmResult(content="共 1 个项目")),
                                ]
                            ),
                        )

                    thread = threading.Thread(target=worker_side)
                    thread.start()
                    try:
                        types = []
                        stream_history = []
                        for event in stream:
                            types.append(event.type)
                            stream_history.append(event)
                            if event.type == "run_finished":
                                break
                    finally:
                        stream.close()
                        thread.join(timeout=5)

                    persisted = [event.type for event in stream_history if event.seq is not None]
                    assert persisted == [
                        "run_started",
                        "llm_call",
                        "llm_result",
                        "tool_call",
                        "tool_result",
                        "llm_call",
                        "llm_result",
                        "run_finished",
                    ]
                    assert "message_started" in types and "message_delta" in types
                    assert run.wait().output == "共 1 个项目"
                    assert received[0]["path"] == "/items/P1"
            finally:
                worker.close()
    finally:
        server.shutdown()
        server.server_close()
