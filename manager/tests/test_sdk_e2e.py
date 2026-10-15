import threading

import pytest
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
