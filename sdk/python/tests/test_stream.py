import json

import pytest
from zszc.agentops import NotFoundError, create_client

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


def test_state_frames_update_current_run(fake, client):
    fake.route_raw(EVENTS, [[(0, state_frame(RUN_ID)), (0, state_frame(None))]], hold=0.5)
    session = client.get_session(SESSION_ID)

    stream = session.stream()
    first = next(stream)
    assert (first.type, first.run_id, first.seq) == ("state", None, None)
    assert session.current_run.id == RUN_ID

    second = next(stream)
    assert second.type == "state"
    assert session.current_run is None
    stream.close()


def test_events_yield_typed_frames_and_suppress_duplicates(fake, client):
    finished = event_frame(2, "run_finished", status="succeeded", result="stub: hi")
    fake.route_raw(
        EVENTS,
        [[(0, event_frame(1, "run_started", content="hi")), (0, finished), (0, finished)]],
        hold=0.5,
    )
    session = client.get_session(SESSION_ID)

    stream = session.stream()
    first, second = next(stream), next(stream)

    assert [(e.type, e.run_id, e.seq) for e in (first, second)] == [
        ("run_started", RUN_ID, 1),
        ("run_finished", RUN_ID, 2),
    ]
    assert second.data["result"] == "stub: hi"
    stream.close()


def test_frames_split_across_chunks_reassemble(fake, client):
    whole = event_frame(1, "run_started", content="hi")
    fake.route_raw(EVENTS, [[(0, whole[:20]), (0, whole[20:])]], hold=0.5)
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
            [(0, event_frame(1, "run_started", content="hi")), (0, event_frame(2, "llm_call", action_id="a1"))],
            [(0, event_frame(2, "llm_call", action_id="a1")), (0, third)],
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
        next(session.stream())

    assert len(fake.requests) == 1


def test_explicit_cursor_reaches_the_wire(fake, client):
    fake.route_raw(EVENTS, [[(0, event_frame(4, "run_finished", status="succeeded", result=None))]], hold=0.5)
    session = client.get_session(SESSION_ID)

    stream = session.stream(from_run=RUN_ID, from_seq=3)
    event = next(stream)
    stream.close()

    assert event.seq == 4
    assert fake.requests[0]["query"] == {"from_run": RUN_ID, "from_seq": "3"}


def test_stream_frames_yield_without_moving_the_cursor(fake, client):
    fake.route_raw(
        EVENTS,
        [
            [
                (0, state_frame(RUN_ID)),
                (0, event_frame(1, "run_started", {"content": "hi"})),
                (0, sse({"type": "message_delta", "data": {"run_id": RUN_ID, "text": "增量"}})),
            ],
            [(0, event_frame(2, "run_finished", {"status": "succeeded"}))],
        ],
        drop=True,
    )
    session = client.get_session(SESSION_ID)

    stream = session.stream()
    seen = [next(stream) for _ in range(3)]
    fourth = next(stream)
    stream.close()

    assert [(e.type, e.seq) for e in seen] == [("state", None), ("run_started", 1), ("message_delta", None)]
    assert seen[2].data["text"] == "增量"
    assert (fourth.type, fourth.seq) == ("run_finished", 2)
    # The reconnect carried the persisted cursor only, not the stream frame.
    assert fake.requests[1]["query"] == {"from_run": RUN_ID, "from_seq": "1"}
