import json

import httpx
import pytest
from zszc.agentops import NotFoundError, create_client
from zszc.agentops.http import Http

SESSION_ID = "11111111-1111-1111-1111-111111111111"
RUN_ID = "33333333-3333-3333-3333-333333333333"
EVENTS = f"/v1/sessions/{SESSION_ID}/events"


def sse(payload: dict, cursor: str | None = None) -> bytes:
    lines = [f"id: {cursor}"] if cursor else []
    lines.append("data: " + json.dumps(payload, separators=(",", ":")))
    return ("\n".join(lines) + "\n\n").encode()


def event_frame(seq: int, type_: str, **data) -> bytes:
    payload = {"type": type_, "data": {"run_id": RUN_ID, "seq": seq, "action_id": None, **data}}
    return sse(payload, f"{RUN_ID}:{seq}")


def state_frame(run_id: str | None) -> bytes:
    return sse({"type": "state", "data": {"session_id": SESSION_ID, "run": run_id}})


@pytest.fixture
def client(fake):
    with create_client("k", base_url=fake.base_url, reconnect_delay=0.05) as client:
        yield client


def test_stream_returns_when_subscription_is_live(fake, client):
    fake.route_raw(EVENTS, [[(0, state_frame(RUN_ID)), (0, event_frame(1, "RUN_STARTED", content="hi"))]], hold=0.5)
    session = client.get_session(SESSION_ID)

    stream = session.stream()
    assert len(fake.requests) == 1  # the subscription is on the wire before submit could race it

    first = next(stream)
    assert (first.type, first.seq) == ("RUN_STARTED", 1)
    stream.close()


def test_state_frames_stay_internal(fake, client):
    fake.route_raw(
        EVENTS,
        [
            [
                (0, state_frame(RUN_ID)),
                (0, event_frame(1, "RUN_STARTED", content="hi")),
                (0, state_frame(None)),
                (0, event_frame(2, "RUN_FINISHED", status="succeeded", result=None)),
            ]
        ],
        hold=0.5,
    )
    session = client.get_session(SESSION_ID)

    stream = session.stream()
    seen = [next(stream) for _ in range(2)]
    stream.close()

    assert [(e.type, e.seq) for e in seen] == [("RUN_STARTED", 1), ("RUN_FINISHED", 2)]
    assert session.current_run is None  # the digested state frames still drive current_run


def test_events_yield_typed_frames_and_suppress_duplicates(fake, client):
    finished = event_frame(2, "RUN_FINISHED", status="succeeded", result="stub: hi")
    fake.route_raw(
        EVENTS,
        [[(0, state_frame(RUN_ID)), (0, event_frame(1, "RUN_STARTED", content="hi")), (0, finished), (0, finished)]],
        hold=0.5,
    )
    session = client.get_session(SESSION_ID)

    stream = session.stream()
    seen = [next(stream) for _ in range(3)]
    stream.close()

    assert [(e.type, e.run_id, e.seq) for e in seen] == [
        ("RUN_STARTED", RUN_ID, 1),
        ("message", RUN_ID, None),
        ("RUN_FINISHED", RUN_ID, 2),
    ]
    assert seen[1].data == {"text": "stub: hi", "delta": "", "done": True}  # sealed by the string result
    assert seen[2].data["result"] == "stub: hi"


def test_frames_split_across_chunks_reassemble(fake, client):
    whole = event_frame(1, "RUN_STARTED", content="hi")
    fake.route_raw(EVENTS, [[(0, state_frame(RUN_ID)), (0, whole[:20]), (0, whole[20:])]], hold=0.5)
    session = client.get_session(SESSION_ID)

    stream = session.stream()
    event = next(stream)

    assert (event.type, event.seq) == ("RUN_STARTED", 1)
    stream.close()


def test_reconnect_resumes_from_cursor_and_suppresses_replay(fake, client):
    third = event_frame(3, "RUN_FINISHED", status="succeeded", result=None)
    fake.route_raw(
        EVENTS,
        [
            [
                (0, state_frame(RUN_ID)),
                (0, event_frame(1, "RUN_STARTED", content="hi")),
                (0, event_frame(2, "llm_call", action_id="a1")),
            ],
            [(0, state_frame(RUN_ID)), (0, event_frame(2, "llm_call", action_id="a1")), (0, third)],
        ],
        drop=True,
    )
    session = client.get_session(SESSION_ID)

    stream = session.stream()
    seqs = [next(stream).seq for _ in range(3)]
    stream.close()

    assert seqs == [1, 2, 3]
    assert fake.requests[0]["query"] == {}
    assert fake.requests[1]["query"] == {"from_run": RUN_ID, "from_seq": "2"}


def test_http_error_raises_without_retry(fake, client):
    fake.route("GET", EVENTS, status=404, body={"error": {"code": "not_found", "message": "no such session"}})
    session = client.get_session(SESSION_ID)

    with pytest.raises(NotFoundError):
        session.stream()

    assert len(fake.requests) == 1


def test_explicit_cursor_reaches_the_wire(fake, client):
    fake.route_raw(
        EVENTS,
        [[(0, state_frame(RUN_ID)), (0, event_frame(4, "RUN_FINISHED", status="succeeded", result=None))]],
        hold=0.5,
    )
    session = client.get_session(SESSION_ID)

    stream = session.stream(from_run=RUN_ID, from_seq=3)
    event = next(stream)
    stream.close()

    assert event.seq == 4
    assert fake.requests[0]["query"] == {"from_run": RUN_ID, "from_seq": "3"}


def text_frame(type_: str, **data) -> bytes:
    return sse({"type": type_, "data": {"run_id": RUN_ID, **data}})


def test_text_frames_aggregate_into_message_events(fake, client):
    fake.route_raw(
        EVENTS,
        [
            [
                (0, state_frame(RUN_ID)),
                (0, text_frame("TEXT_MESSAGE_START")),
                (0, text_frame("TEXT_MESSAGE_CONTENT", delta="你")),
                (0, text_frame("TEXT_MESSAGE_CONTENT", delta="好")),
                (0, text_frame("TEXT_MESSAGE_END")),
                (0, event_frame(2, "RUN_FINISHED", status="succeeded", result="你好")),
            ]
        ],
        hold=0.5,
    )
    session = client.get_session(SESSION_ID)

    stream = session.stream()
    seen = [next(stream) for _ in range(4)]
    stream.close()

    assert [(e.type, e.seq) for e in seen] == [
        ("message", None),
        ("message", None),
        ("message", None),
        ("RUN_FINISHED", 2),
    ]
    assert [(e.data["done"], e.data["delta"], e.data["text"]) for e in seen[:3]] == [
        (False, "你", "你"),
        (False, "好", "你好"),
        (True, "", "你好"),
    ]


def test_run_finish_seals_without_stream_frames(fake, client):
    fake.route_raw(
        EVENTS,
        [[(0, state_frame(RUN_ID)), (0, event_frame(1, "RUN_FINISHED", status="succeeded", result="final"))]],
        hold=0.5,
    )
    session = client.get_session(SESSION_ID)

    stream = session.stream()
    seen = [next(stream) for _ in range(2)]
    stream.close()

    assert [(e.type, e.data) for e in seen] == [
        ("message", {"text": "final", "delta": "", "done": True}),
        ("RUN_FINISHED", seen[1].data),
    ]


def test_partial_stream_sealed_by_result_after_reconnect(fake):
    with create_client("k", base_url=fake.base_url, reconnect_delay=0.05, idle_timeout=0.2) as client:
        fake.route_raw(
            EVENTS,
            [
                [(0, state_frame(RUN_ID)), (0, text_frame("TEXT_MESSAGE_START")), (0, text_frame("TEXT_MESSAGE_CONTENT", delta="你"))],
                [(0, state_frame(RUN_ID)), (0, event_frame(1, "RUN_FINISHED", status="succeeded", result="你好"))],
            ],
            hold=0.5,
        )
        session = client.get_session(SESSION_ID)

        stream = session.stream()
        seen = [next(stream) for _ in range(3)]
        stream.close()

        assert [(e.type, e.data.get("done")) for e in seen] == [
            ("message", False),
            ("message", True),  # sealed by the authoritative result, not the partial buffer
            ("RUN_FINISHED", None),
        ]
        assert seen[1].data["text"] == "你好"


def test_run_error_does_not_seal_a_message(fake, client):
    fake.route_raw(
        EVENTS,
        [[(0, state_frame(RUN_ID)), (0, event_frame(1, "RUN_ERROR", error="boom"))]],
        hold=0.5,
    )
    session = client.get_session(SESSION_ID)

    stream = session.stream()
    event = next(stream)
    stream.close()

    assert (event.type, event.data["error"]) == ("RUN_ERROR", "boom")


def test_stream_frames_yield_without_moving_the_cursor(fake, client):
    fake.route_raw(
        EVENTS,
        [
            [
                (0, state_frame(RUN_ID)),
                (0, event_frame(1, "RUN_STARTED", content="hi")),
                (0, text_frame("TEXT_MESSAGE_CONTENT", delta="增量")),
            ],
            [(0, state_frame(RUN_ID)), (0, event_frame(2, "RUN_FINISHED", status="succeeded"))],
        ],
        drop=True,
    )
    session = client.get_session(SESSION_ID)

    stream = session.stream()
    seen = [next(stream) for _ in range(2)]
    third, fourth = next(stream), next(stream)
    stream.close()

    assert [(e.type, e.seq) for e in seen] == [("RUN_STARTED", 1), ("message", None)]
    assert seen[1].data["delta"] == "增量"
    assert (third.type, third.data["done"], third.data["text"]) == ("message", True, "增量")  # sealed by the buffer
    assert (fourth.type, fourth.seq) == ("RUN_FINISHED", 2)
    # The reconnect carried the persisted cursor only, not the stream frame.
    assert fake.requests[1]["query"] == {"from_run": RUN_ID, "from_seq": "1"}


def test_frames_flowing_never_reconnects(fake):
    with create_client("k", base_url=fake.base_url, reconnect_delay=0.05, idle_timeout=0.3) as client:
        fake.route_raw(
            EVENTS,
            [
                [
                    (0, state_frame(RUN_ID)),
                    (0.05, event_frame(1, "RUN_STARTED", content="hi")),
                    (0.05, event_frame(2, "llm_call", action_id="a1")),
                    (0.05, event_frame(3, "RUN_FINISHED", status="succeeded", result=None)),
                ]
            ],
            hold=0.5,
        )
        session = client.get_session(SESSION_ID)

        stream = session.stream()
        seqs = [next(stream).seq for _ in range(3)]
        stream.close()

        assert seqs == [1, 2, 3]
        assert len(fake.requests) == 1


def test_silence_reconnects_with_cursor(fake):
    with create_client("k", base_url=fake.base_url, reconnect_delay=0.05, idle_timeout=0.2) as client:
        fake.route_raw(
            EVENTS,
            [
                [(0, state_frame(RUN_ID)), (0, event_frame(1, "RUN_STARTED", content="hi"))],
                [(0, state_frame(RUN_ID)), (0, event_frame(2, "RUN_FINISHED", status="succeeded", result=None))],
            ],
            hold=0.5,  # the link stays open but silent after the script
        )
        session = client.get_session(SESSION_ID)

        stream = session.stream()
        seqs = [next(stream).seq for _ in range(2)]
        stream.close()

        assert seqs == [1, 2]
        assert fake.requests[1]["query"] == {"from_run": RUN_ID, "from_seq": "1"}


def test_failed_redial_keeps_retrying_with_cursor(fake, monkeypatch):
    with create_client("k", base_url=fake.base_url, reconnect_delay=0.05, idle_timeout=0.2) as client:
        fake.route_raw(
            EVENTS,
            [
                [(0, state_frame(RUN_ID)), (0, event_frame(1, "RUN_STARTED", content="hi"))],
                [(0, state_frame(RUN_ID)), (0, event_frame(2, "RUN_FINISHED", status="succeeded", result=None))],
            ],
            hold=0.5,
        )
        real_events = Http.events
        calls = {"n": 0}

        def flaky_events(self, path, params, *, read_timeout):
            calls["n"] += 1
            if calls["n"] == 2:
                raise httpx.ConnectError("refused")  # the first redial fails
            return real_events(self, path, params, read_timeout=read_timeout)

        monkeypatch.setattr(Http, "events", flaky_events)
        session = client.get_session(SESSION_ID)

        stream = session.stream()
        seqs = [next(stream).seq for _ in range(2)]
        stream.close()

        assert seqs == [1, 2]
        assert calls["n"] == 3
        assert fake.requests[1]["query"] == {"from_run": RUN_ID, "from_seq": "1"}


def test_empty_link_surfaces_as_transport_error(fake, monkeypatch):
    def dead_events(self, path, params, *, read_timeout):
        yield from ()  # EOF before the state push

    monkeypatch.setattr(Http, "events", dead_events)
    with create_client("k", base_url=fake.base_url, reconnect_delay=0.05) as client:
        session = client.get_session(SESSION_ID)

        with pytest.raises(httpx.TransportError):
            session.stream()
