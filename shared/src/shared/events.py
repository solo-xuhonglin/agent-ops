from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

# The action types pair through action_id; docs/05 §2. A _call without its
# _result is the sole "this step never finished" signal.
_ACTION_PAIRS = {"llm_call": "llm_result", "tool_call": "tool_result", "agent_call": "agent_result"}
_ACTION_TYPES = set(_ACTION_PAIRS) | set(_ACTION_PAIRS.values())


class EventType(StrEnum):
    run_started = "run_started"
    llm_call = "llm_call"
    llm_result = "llm_result"
    tool_call = "tool_call"
    tool_result = "tool_result"
    agent_call = "agent_call"
    agent_result = "agent_result"
    branch_taken = "branch_taken"
    context_compacted = "context_compacted"
    run_finished = "run_finished"
    run_failed = "run_failed"


class RunStartedPayload(BaseModel):
    content: str
    params: dict
    output_schema: dict | None = None
    session_id: UUID
    caller: str
    end_user: str | None = None


class RunFinishedPayload(BaseModel):
    # cancelled is a run_finished too; only the worker writes it.
    status: Literal["succeeded", "cancelled"] = "succeeded"
    result: Any


class RunFailedPayload(BaseModel):
    error: str


class LogEntry(BaseModel):
    run_id: UUID
    seq: int = Field(ge=1)
    action_id: str | None
    type: EventType
    payload: dict

    @model_validator(mode="after")
    def action_types_carry_action_id(self):
        if self.type.value in _ACTION_TYPES and self.action_id is None:
            raise ValueError(f"{self.type.value} must carry an action_id")
        return self


# Stream-only event names: they ride the wire and SSE but are never a log
# type, never seq'd (docs/05 §3.3). Same lowercase vocabulary everywhere —
# structurally the AG-UI three-part message stream, spelled snake_case.
STREAM_EVENT_TYPES = ("text_message_start", "text_message_content", "text_message_end")

# Per-type payload fields that must not leave the platform (docs/05 §3.1):
# llm_call's messages carry the System Prompt; context_compacted's summary is
# free text that may embed config content. The external events keep references
# and lengths only.
_PRIVATE_FIELDS = {"llm_call": {"messages"}, "context_compacted": {"summary"}}


def public_event(entry: dict) -> dict:
    """Project a full log entry to the external event shape."""
    private = _PRIVATE_FIELDS.get(entry["type"], set())
    if not private:
        return entry
    data = {key: value for key, value in entry["data"].items() if key not in private}
    return {"type": entry["type"], "data": data}


def open_actions(entries: list[LogEntry]) -> set[str]:
    open_ids: set[str] = set()
    for item in entries:
        name = item.type.value
        if name in _ACTION_PAIRS:
            open_ids.add(item.action_id)
        elif name in _ACTION_PAIRS.values():
            open_ids.discard(item.action_id)
    return open_ids
