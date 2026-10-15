import json
import socket
import time
import urllib.request
from urllib.parse import urlparse

import pytest
from util import FakeWorker, live_server, write_log

from manager import internal


@pytest.fixture(scope="module")
def base_url():
    with live_server() as base:
        yield base


def api_call(base_url: str, method: str, path: str, key: str, body: dict | None = None) -> dict:
    request = urllib.request.Request(
        base_url + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(request, timeout=5).read().decode())


class Sse:
    """A raw-socket SSE reader — the tests read the stream the way a client does."""

    def __init__(self, base_url: str, path: str, key: str):
        target = urlparse(base_url)
        self.sock = socket.create_connection((target.hostname, target.port), timeout=3)
        self.sock.sendall(f"GET {path} HTTP/1.1\r\nHost: t\r\nAuthorization: Bearer {key}\r\n\r\n".encode())
        self.buffer = b""
        self._lines = []
        while b"\r\n\r\n" not in self.buffer:
            self.buffer += self.sock.recv(4096)
        header, self.buffer = self.buffer.split(b"\r\n\r\n", 1)
        self.status = int(header.split(b" ")[1])

    def frames(self, count: int) -> list[tuple[str | None, dict]]:
        frames = []
        while len(frames) < count:
            while b"\n" not in self.buffer:
                chunk = self.sock.recv(4096)
                if not chunk:
                    raise ConnectionError("stream closed early")
                self.buffer += chunk
            line, self.buffer = self.buffer.split(b"\n", 1)
            text = line.decode().rstrip("\r")
            if text == "":
                if self._lines:
                    frames.append(self._parse(self._lines))
                    self._lines = []
            else:
                self._lines.append(text)
        return frames

    @staticmethod
    def _parse(lines: list[str]) -> tuple[str | None, dict]:
        cursor = None
        payload = None
        for line in lines:
            if line.startswith("id: "):
                cursor = line.removeprefix("id: ")
            elif line.startswith("data: "):
                payload = json.loads(line.removeprefix("data: "))
        return cursor, payload

    def close(self):
        self.sock.close()


class RunLink:
    """The worker's Run connection: rows land in the log first, then ride the wire."""

    def __init__(self, engine, run_id: str, epoch: int):
        self.engine = engine
        self.run_id = run_id
        self.sock = socket.create_connection(("127.0.0.1", internal.port), timeout=3)
        self.sock.sendall(encode_state(run_id, epoch))

    def event(self, type_: str, seq: int, payload: dict, action_id: str | None = None):
        from shared.events import public_event
        from shared.wire import encode
        from util import write_log

        write_log(self.engine, self.run_id, seq, type_, payload, action_id)
        entry = {"type": type_, "data": {"run_id": self.run_id, "seq": seq, "action_id": action_id, **payload}}
        self.sock.sendall(encode(type_, public_event(entry)["data"]))

    def stream_frame(self, type_: str, data: dict):
        """A never-persisted frame: rides the wire, no row, no seq."""
        from shared.wire import encode as _encode

        self.sock.sendall(_encode(type_, data))

    def close(self):
        self.sock.close()
        time.sleep(0.2)


def encode_state(run_id: str, epoch: int) -> bytes:
    from shared.wire import encode

    return encode("state", {"worker_id": "w-stream", "run_id": run_id, "epoch": epoch})


def dispatch_run(base_url, key, worker) -> tuple[str, str, int]:
    session_id = api_call(base_url, "POST", "/v1/sessions", key)["session_id"]
    body = api_call(
        base_url, "POST", f"/v1/sessions/{session_id}/runs", key, {"content": "hi", "params": {"project_id": "P"}}
    )
    type_, data = worker.read()
    assert type_ == "dispatch"
    return session_id, body["run_id"], data["epoch"]


@pytest.fixture(autouse=True)
def stream_env(monkeypatch):
    monkeypatch.setenv("STATE_INTERVAL", "0.2")


def test_subscribe_without_cursor_from_run_start(base_url, auth_headers, db_engine):
    key = auth_headers["Authorization"].removeprefix("Bearer ")
    worker = FakeWorker("w-stream")
    try:
        session_id, run_id, epoch = dispatch_run(base_url, key, worker)
        link = RunLink(db_engine, run_id, epoch)
        try:
            link.event("run_started", 1, {"content": "hi", "session_id": session_id})
            link.event("run_finished", 2, {"status": "succeeded", "result": "stub: hi"})
            time.sleep(0.3)

            stream = Sse(base_url, f"/v1/sessions/{session_id}/events", key)
            try:
                assert stream.status == 200
                frames = stream.frames(3)
            finally:
                stream.close()

            assert frames[0][0] is None and frames[0][1]["type"] == "state"
            assert frames[0][1]["data"]["session_id"] == session_id
            assert frames[1] == (
                f"{run_id}:1",
                {
                    "type": "run_started",
                    "data": {"run_id": run_id, "seq": 1, "action_id": None, "content": "hi", "session_id": session_id},
                },
            )
            assert frames[2] == (
                f"{run_id}:2",
                {
                    "type": "run_finished",
                    "data": {
                        "run_id": run_id,
                        "seq": 2,
                        "action_id": None,
                        "status": "succeeded",
                        "result": "stub: hi",
                    },
                },
            )
        finally:
            link.close()
    finally:
        worker.close()


def test_reconnect_from_cursor(base_url, auth_headers, db_engine):
    key = auth_headers["Authorization"].removeprefix("Bearer ")
    worker = FakeWorker("w-cur")
    try:
        session_id, run_id, epoch = dispatch_run(base_url, key, worker)
        link = RunLink(db_engine, run_id, epoch)
        try:
            link.event("run_started", 1, {"content": "hi", "session_id": session_id})
            link.event("run_finished", 2, {"status": "succeeded", "result": "stub: hi"})
            time.sleep(0.3)

            stream = Sse(base_url, f"/v1/sessions/{session_id}/events?from_run={run_id}&from_seq=1", key)
            try:
                frames = stream.frames(2)
            finally:
                stream.close()

            assert frames[0][1]["type"] == "state"
            assert frames[1][1]["type"] == "run_finished"
            assert frames[1][0] == f"{run_id}:2"
        finally:
            link.close()
    finally:
        worker.close()


def test_live_event_forwarded(base_url, auth_headers, db_engine):
    key = auth_headers["Authorization"].removeprefix("Bearer ")
    worker = FakeWorker("w-live")
    try:
        session_id, run_id, epoch = dispatch_run(base_url, key, worker)
        link = RunLink(db_engine, run_id, epoch)
        try:
            stream = Sse(base_url, f"/v1/sessions/{session_id}/events", key)
            try:
                assert stream.frames(1)[0][1]["type"] == "state"

                link.event("run_started", 1, {"content": "hi", "session_id": session_id})
                frame = stream.frames(1)[0]

                assert frame == (
                    f"{run_id}:1",
                    {
                        "type": "run_started",
                        "data": {
                            "run_id": run_id,
                            "seq": 1,
                            "action_id": None,
                            "content": "hi",
                            "session_id": session_id,
                        },
                    },
                )
            finally:
                stream.close()
        finally:
            link.close()
    finally:
        worker.close()


def test_periodic_state_while_idle(base_url, auth_headers):
    key = auth_headers["Authorization"].removeprefix("Bearer ")
    session_id = api_call(base_url, "POST", "/v1/sessions", key)["session_id"]

    stream = Sse(base_url, f"/v1/sessions/{session_id}/events", key)
    try:
        frames = stream.frames(3)
    finally:
        stream.close()

    assert all(cursor is None and payload["type"] == "state" for cursor, payload in frames)


def test_second_subscriber_independent(base_url, auth_headers, db_engine):
    key = auth_headers["Authorization"].removeprefix("Bearer ")
    worker = FakeWorker("w-two")
    try:
        session_id, run_id, epoch = dispatch_run(base_url, key, worker)
        link = RunLink(db_engine, run_id, epoch)
        try:
            link.event("run_started", 1, {"content": "hi", "session_id": session_id})
            time.sleep(0.3)

            first = Sse(base_url, f"/v1/sessions/{session_id}/events", key)
            second = Sse(base_url, f"/v1/sessions/{session_id}/events", key)
            try:
                frames_one = first.frames(2)
                frames_two = second.frames(2)
            finally:
                first.close()
                second.close()

            assert frames_one == frames_two
            assert frames_one[1][1]["type"] == "run_started"
        finally:
            link.close()
    finally:
        worker.close()


def test_disconnect_unsubscribes(base_url, auth_headers):
    key = auth_headers["Authorization"].removeprefix("Bearer ")
    session_id = api_call(base_url, "POST", "/v1/sessions", key)["session_id"]

    stream = Sse(base_url, f"/v1/sessions/{session_id}/events", key)
    stream.frames(1)
    stream.close()
    time.sleep(0.3)

    assert internal.subscribers.get(session_id) in (None, set())


def test_sse_rejects_foreign_session(base_url, auth_headers, db_engine):
    from uuid import uuid4

    from shared.models import ExecutionSession
    from sqlalchemy.orm import Session

    key = auth_headers["Authorization"].removeprefix("Bearer ")
    with Session(db_engine) as session, session.begin():
        foreign = ExecutionSession(agent_id=uuid4())
        session.add(foreign)
        session.flush()
        foreign_id = foreign.id

    stream = Sse(base_url, f"/v1/sessions/{foreign_id}/events", key)
    assert stream.status == 404
    stream.close()


def test_unknown_from_run_rejected(base_url, auth_headers):
    from uuid import uuid4

    key = auth_headers["Authorization"].removeprefix("Bearer ")
    session_id = api_call(base_url, "POST", "/v1/sessions", key)["session_id"]

    stream = Sse(base_url, f"/v1/sessions/{session_id}/events?from_run={uuid4()}", key)
    assert stream.status == 422
    stream.close()


def test_state_returns_to_idle_after_terminal(base_url, auth_headers, db_engine):
    key = auth_headers["Authorization"].removeprefix("Bearer ")
    worker = FakeWorker("w-idle")
    try:
        session_id, run_id, epoch = dispatch_run(base_url, key, worker)
        link = RunLink(db_engine, run_id, epoch)
        try:
            link.event("run_started", 1, {"content": "hi", "session_id": session_id})
            link.event("run_finished", 2, {"status": "succeeded", "result": "x"})
            time.sleep(0.3)

            stream = Sse(base_url, f"/v1/sessions/{session_id}/events", key)
            try:
                frames = stream.frames(3)
            finally:
                stream.close()

            assert frames[0][1]["data"]["run"] is None
        finally:
            link.close()
    finally:
        worker.close()


def test_full_queue_drops_subscriber():
    import asyncio as aio

    queue = aio.Queue(maxsize=1)
    queue.put_nowait({"type": "state", "data": {}})
    internal.subscribe("sess-full", queue)

    internal._forward("sess-full", {"type": "run_started", "data": {"seq": 5}})

    assert queue.qsize() == 1
    assert "sess-full" not in internal.subscribers


def test_stream_frames_forwarded_without_cursor(base_url, auth_headers, db_engine):
    key = auth_headers["Authorization"].removeprefix("Bearer ")
    worker = FakeWorker("w-live")
    try:
        session_id, run_id, epoch = dispatch_run(base_url, key, worker)
        stream = Sse(base_url, f"/v1/sessions/{session_id}/events", key)
        link = RunLink(db_engine, run_id, epoch)
        try:
            link.event("run_started", 1, {"content": "hi", "session_id": session_id})
            link.event("llm_call", 2, {"messages": [{"role": "system", "content": "SECRET"}]}, action_id="a1")
            link.stream_frame("text_message_start", {"run_id": run_id})
            link.stream_frame("text_message_content", {"run_id": run_id, "delta": "你好"})
            link.stream_frame("text_message_end", {"run_id": run_id, "text": "你好"})
            link.event("run_finished", 3, {"status": "succeeded", "result": "你好"}, action_id=None)
            time.sleep(0.3)
            read = []
            while True:
                frame = stream.frames(1)[0]
                read.append(frame)
                if frame[0] == f"{run_id}:3":
                    break
        finally:
            stream.close()
            link.close()

        # Read like a conforming client: keepalives interleave, and the
        # subscribe-before-replay race may duplicate an event — dedup by seq.
        seen: set[str] = set()
        frames = []
        for frame in read:
            if frame[1]["type"] == "state":
                continue
            if frame[0] is not None:
                if frame[0] in seen:
                    continue
                seen.add(frame[0])
            frames.append(frame)
        assert read[0][1]["type"] == "state"
        assert [frame[0] for frame in frames] == [f"{run_id}:1", f"{run_id}:2", None, None, None, f"{run_id}:3"]
        assert [frames[0][1]["type"], frames[1][1]["type"], frames[5][1]["type"]] == ["run_started", "llm_call", "run_finished"]
        # llm_call rides the wire WITHOUT its messages (引用外发,内容不外发).
        assert "messages" not in frames[1][1]["data"]
        assert frames[2] == (None, {"type": "text_message_start", "data": {"run_id": run_id}})
        assert frames[3] == (None, {"type": "text_message_content", "data": {"run_id": run_id, "delta": "你好"}})
        assert frames[4] == (None, {"type": "text_message_end", "data": {"run_id": run_id, "text": "你好"}})
        # terminal clears the accumulation buffer
        assert run_id not in internal.message_buffers
    finally:
        worker.close()


def test_subscribe_mid_message_gets_accumulated_catch_up(base_url, auth_headers, db_engine):
    key = auth_headers["Authorization"].removeprefix("Bearer ")
    worker = FakeWorker("w-catchup")
    try:
        session_id, run_id, epoch = dispatch_run(base_url, key, worker)
        link = RunLink(db_engine, run_id, epoch)
        try:
            link.event("run_started", 1, {"content": "hi", "session_id": session_id})
            link.event("llm_call", 2, {"messages": []}, action_id="a2")
            link.stream_frame("text_message_start", {"run_id": run_id})
            link.stream_frame("text_message_content", {"run_id": run_id, "delta": "第一"})
            time.sleep(0.3)

            stream = Sse(base_url, f"/v1/sessions/{session_id}/events", key)
            try:
                read = []
                while True:
                    frame = stream.frames(1)[0]
                    read.append(frame)
                    if frame[1]["type"] == "text_message_content":
                        break
                link.stream_frame("text_message_content", {"run_id": run_id, "delta": "第二"})
                live = None
                while True:
                    frame = stream.frames(1)[0]
                    if frame[1]["type"] != "state":
                        live = frame
                        break
            finally:
                stream.close()
        finally:
            link.close()

        frames = [frame for frame in read if frame[1]["type"] != "state"]
        assert read[0][1]["type"] == "state"
        assert [frame[1]["type"] for frame in frames] == ["run_started", "llm_call", "text_message_start", "text_message_content"]
        assert frames[3][1]["data"]["delta"] == "第一"
        assert live == (None, {"type": "text_message_content", "data": {"run_id": run_id, "delta": "第二"}})
    finally:
        worker.close()


def test_context_compacted_replays_without_its_summary(base_url, auth_headers, db_engine):
    key = auth_headers["Authorization"].removeprefix("Bearer ")
    worker = FakeWorker("w-compact")
    try:
        session_id, run_id, _epoch = dispatch_run(base_url, key, worker)
        write_log(db_engine, run_id, 1, "run_started", {"content": "hi"})
        write_log(
            db_engine,
            run_id,
            2,
            "context_compacted",
            {"summary": "不该外发的摘要全文", "before_tokens": 99, "after_tokens": 9, "compacted_messages": 2},
        )
        time.sleep(0.1)

        stream = Sse(base_url, f"/v1/sessions/{session_id}/events", key)
        try:
            frames = stream.frames(3)
        finally:
            stream.close()
    finally:
        worker.close()

    assert [frame[1]["type"] for frame in frames] == ["state", "run_started", "context_compacted"]
    assert "summary" not in frames[2][1]["data"]
    assert frames[2][1]["data"]["before_tokens"] == 99
