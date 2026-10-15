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
    fake.route_raw(EVENTS, [[(0, state_frame(RUN_ID)), (0, event_frame(1, "run_started", content="hi"))]], hold=0.5)
    session = client.get_session(SESSION_ID)

    stream = session.stream()
    assert len(fake.requests) == 1  # the subscription is on the wire before submit could race it

    first = next(stream)
    assert (first.type, first.seq) == ("run_started", 1)
    stream.close()


def test_state_frames_stay_internal(fake, client):
    fake.route_raw(
        EVENTS,
        [
            [
                (0, state_frame(RUN_ID)),
                (0, event_frame(1, "run_started", content="hi")),
                (0, state_frame(None)),
                (0, event_frame(2, "run_finished", status="succeeded", result=None)),
            ]
        ],
        hold=0.5,
    )
    session = client.get_session(SESSION_ID)

    stream = session.stream()
    seen = [next(stream) for _ in range(2)]
    stream.close()

    assert [(e.type, e.seq) for e in seen] == [("run_started", 1), ("run_finished", 2)]
    assert session.current_run is None  # the digested state frames still drive current_run


def test_events_yield_typed_frames_and_suppress_duplicates(fake, client):
    finished = event_frame(2, "run_finished", status="succeeded", result="stub: hi")
    fake.route_raw(
        EVENTS,
        [[(0, state_frame(RUN_ID)), (0, event_frame(1, "run_started", content="hi")), (0, finished), (0, finished)]],
        hold=0.5,
    )
    session = client.get_session(SESSION_ID)

    stream = session.stream()
    seen = [next(stream) for _ in range(3)]
    stream.close()

    assert [(e.type, e.run_id, e.seq) for e in seen] == [
        ("run_started", RUN_ID, 1),
        ("text_message_end", RUN_ID, None),
        ("run_finished", RUN_ID, 2),
    ]
    assert seen[1].data == {"run_id": RUN_ID, "text": "stub: hi"}  # synthesized from the string result
    assert seen[2].data["result"] == "stub: hi"


def test_frames_split_across_chunks_reassemble(fake, client):
    whole = event_frame(1, "run_started", content="hi")
    fake.route_raw(EVENTS, [[(0, state_frame(RUN_ID)), (0, whole[:20]), (0, whole[20:])]], hold=0.5)
    session = client.get_session(SESSION_ID)

    stream = session.stream()
    event = next(stream)

    assert (event.type, event.seq) == ("run_started", 1)
    stream.close()


def test_reconnect_resumes_from_cursor_and_suppresses_replay(fake, client):
    third = event_frame(3, "run_finished", status="succeeded", result=None)
    fake.route_raw(
        EVENTS,
        [
            [
                (0, state_frame(RUN_ID)),
                (0, event_frame(1, "run_started", content="hi")),
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
        [[(0, state_frame(RUN_ID)), (0, event_frame(4, "run_finished", status="succeeded", result=None))]],
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


def test_three_part_message_events_pass_through(fake, client):
    fake.route_raw(
        EVENTS,
        [
            [
                (0, state_frame(RUN_ID)),
                (0, text_frame("text_message_start")),
                (0, text_frame("text_message_content", delta="你")),
                (0, text_frame("text_message_content", delta="好")),
                (0, text_frame("text_message_end", text="你好")),
                (0, event_frame(2, "run_finished", status="succeeded", result="你好")),
            ]
        ],
        hold=0.5,
    )
    session = client.get_session(SESSION_ID)

    stream = session.stream()
    seen = [next(stream) for _ in range(5)]
    stream.close()

    assert [(e.type, e.seq) for e in seen] == [
        ("text_message_start", None),
        ("text_message_content", None),
        ("text_message_content", None),
        ("text_message_end", None),
        ("run_finished", 2),
    ]
    assert [e.data["delta"] for e in seen[1:3]] == ["你", "好"]
    assert seen[3].data["text"] == "你好"  # the end event carries the full authoritative text
    # The real end arrived: run_finished synthesizes nothing after it.


def test_missed_end_is_synthesized_from_result(fake, client):
    fake.route_raw(
        EVENTS,
        [[(0, state_frame(RUN_ID)), (0, event_frame(1, "run_finished", status="succeeded", result="final"))]],
        hold=0.5,
    )
    session = client.get_session(SESSION_ID)

    stream = session.stream()
    seen = [next(stream) for _ in range(2)]
    stream.close()

    assert [(e.type, e.data) for e in seen] == [
        ("text_message_end", {"run_id": RUN_ID, "text": "final"}),
        ("run_finished", seen[1].data),
    ]


def test_partial_stream_synthesizes_end_from_result_after_reconnect(fake):
    with create_client("k", base_url=fake.base_url, reconnect_delay=0.05, idle_timeout=0.2) as client:
        fake.route_raw(
            EVENTS,
            [
                [(0, state_frame(RUN_ID)), (0, text_frame("text_message_start")), (0, text_frame("text_message_content", delta="你"))],
                [(0, state_frame(RUN_ID)), (0, event_frame(1, "run_finished", status="succeeded", result="你好"))],
            ],
            hold=0.5,
        )
        session = client.get_session(SESSION_ID)

        stream = session.stream()
        seen = [next(stream) for _ in range(4)]
        stream.close()

        assert [(e.type, e.data.get("delta") or e.data.get("text")) for e in seen] == [
            ("text_message_start", None),
            ("text_message_content", "你"),
            ("text_message_end", "你好"),  # synthesized from the authoritative result, not the partial buffer
            ("run_finished", None),
        ]


def test_run_failed_synthesizes_nothing(fake, client):
    fake.route_raw(
        EVENTS,
        [[(0, state_frame(RUN_ID)), (0, event_frame(1, "run_failed", error="boom"))]],
        hold=0.5,
    )
    session = client.get_session(SESSION_ID)

    stream = session.stream()
    event = next(stream)
    stream.close()

    assert (event.type, event.data["error"]) == ("run_failed", "boom")


def test_stream_frames_yield_without_moving_the_cursor(fake, client):
    fake.route_raw(
        EVENTS,
        [
            [
                (0, state_frame(RUN_ID)),
                (0, event_frame(1, "run_started", content="hi")),
                (0, text_frame("text_message_content", delta="增量")),
            ],
            [(0, state_frame(RUN_ID)), (0, event_frame(2, "run_finished", status="succeeded"))],
        ],
        drop=True,
    )
    session = client.get_session(SESSION_ID)

    stream = session.stream()
    seen = [next(stream) for _ in range(2)]
    third, fourth = next(stream), next(stream)
    stream.close()

    assert [(e.type, e.seq) for e in seen] == [("run_started", 1), ("text_message_content", None)]
    assert seen[1].data["delta"] == "增量"
    assert (third.type, third.data["text"]) == ("text_message_end", "增量")  # synthesized from the buffer
    assert (fourth.type, fourth.seq) == ("run_finished", 2)
    # The reconnect carried the persisted cursor only, not the stream frame.
    assert fake.requests[1]["query"] == {"from_run": RUN_ID, "from_seq": "1"}


def test_frames_flowing_never_reconnects(fake):
    with create_client("k", base_url=fake.base_url, reconnect_delay=0.05, idle_timeout=0.3) as client:
        fake.route_raw(
            EVENTS,
            [
                [
                    (0, state_frame(RUN_ID)),
                    (0.05, event_frame(1, "run_started", content="hi")),
                    (0.05, event_frame(2, "llm_call", action_id="a1")),
                    (0.05, event_frame(3, "run_finished", status="succeeded", result=None)),
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
                [(0, state_frame(RUN_ID)), (0, event_frame(1, "run_started", content="hi"))],
                [(0, state_frame(RUN_ID)), (0, event_frame(2, "run_finished", status="succeeded", result=None))],
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
                [(0, state_frame(RUN_ID)), (0, event_frame(1, "run_started", content="hi"))],
                [(0, state_frame(RUN_ID)), (0, event_frame(2, "run_finished", status="succeeded", result=None))],
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
