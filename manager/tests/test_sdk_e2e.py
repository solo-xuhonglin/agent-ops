import socket
import threading

import pytest
from agentops import create_client
from shared.wire import encode
from util import TEST_KEY, FakeWorker, live_server
from worker.executor import execute

from manager import internal


@pytest.fixture(autouse=True)
def fast_state(monkeypatch):
    monkeypatch.setenv("STATE_INTERVAL", "0.2")


def serve_dispatched_run(engine, worker):
    """The real worker path for one dispatch: execute, then report on a Run connection."""
    type_, dispatch = worker.read()
    assert type_ == "dispatch"
    sock = socket.create_connection(("127.0.0.1", internal.port), timeout=3)
    try:
        sock.sendall(
            encode("state", {"worker_id": worker.worker_id, "run_id": dispatch["run_id"], "epoch": dispatch["epoch"]})
        )
        for entry in execute(engine, dispatch):
            sock.sendall(encode(entry["type"], entry["data"]))
    finally:
        sock.close()


def test_submit_stub_execution_and_stream_back_to_the_sdk(db_engine):
    with live_server() as base:
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

                worker_thread = threading.Thread(target=serve_dispatched_run, args=(db_engine, worker))
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

                assert types == ["run_started", "run_finished"]
                assert session.current_run is None

                result = run.wait()
                assert result.output == "stub: hello sdk"
                assert run.status == "succeeded"

                again = client.get_run(run.id).wait()
                assert again.output == "stub: hello sdk"
        finally:
            worker.close()
