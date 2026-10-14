import uuid

import pytest
from pydantic import ValidationError
from shared.events import EventType, LogEntry, open_actions


def entry(type_: EventType, action_id: str | None = None, seq: int = 1) -> LogEntry:
    return LogEntry(run_id=uuid.uuid4(), seq=seq, action_id=action_id, type=type_, payload={})


def test_event_type_vocabulary():
    assert {member.value for member in EventType} == {
        "run_started",
        "llm_call",
        "llm_result",
        "tool_call",
        "tool_result",
        "agent_call",
        "agent_result",
        "branch_taken",
        "context_compacted",
        "run_finished",
        "run_failed",
    }


def test_pairing_interleaved():
    entries = [
        entry(EventType.llm_call, "a", 1),
        entry(EventType.tool_call, "b", 2),
        entry(EventType.tool_result, "b", 3),
        entry(EventType.llm_result, "a", 4),
    ]

    assert open_actions(entries) == set()


def test_open_action_detected():
    entries = [
        entry(EventType.llm_call, "a", 1),
        entry(EventType.tool_call, "b", 2),
        entry(EventType.llm_result, "a", 3),
    ]

    assert open_actions(entries) == {"b"}


def test_non_action_types_ignored():
    entries = [entry(EventType.run_started, seq=1), entry(EventType.context_compacted, seq=2)]

    assert open_actions(entries) == set()


def test_log_entry_rejects_unknown_type():
    with pytest.raises(ValidationError):
        LogEntry(run_id=uuid.uuid4(), seq=1, action_id=None, type="message_delta", payload={})


def test_action_types_require_action_id():
    with pytest.raises(ValidationError):
        entry(EventType.tool_call, None)
    with pytest.raises(ValidationError):
        entry(EventType.llm_result, None)

    assert entry(EventType.run_started, None).action_id is None


def test_run_finished_payload_carries_status():
    from shared.events import RunFinishedPayload

    assert RunFinishedPayload(result="done").status == "succeeded"
    assert RunFinishedPayload(result=None, status="cancelled").status == "cancelled"
