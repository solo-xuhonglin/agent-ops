import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class LlmResult:
    content: str
    tool_calls: list[dict] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0


@dataclass
class LlmDelta:
    text: str


@dataclass
class LlmDone:
    result: LlmResult


LlmEvent = LlmDelta | LlmDone


class LlmClient(Protocol):
    """A provider-shaped boundary: deltas in order, one LlmDone last."""

    def stream(self, messages: list[dict], *, model_id: str, model_params: dict) -> Iterator[LlmEvent]: ...


def _last_user_text(messages: list[dict]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return message.get("content", "")
    return ""


class FakeLlm:
    """Scripted provider: one reply per call; the default reply echoes the
    last user message so unscripted runs (deploy, demos) still stream."""

    def __init__(self, replies: list[tuple[list[str], LlmResult]] | None = None):
        self.replies = list(replies) if replies is not None else None
        self.calls: list[tuple[list[dict], str, dict]] = []

    def stream(self, messages: list[dict], *, model_id: str, model_params: dict) -> Iterator[LlmEvent]:
        self.calls.append((messages, model_id, model_params))
        if self.replies:
            chunks, result = self.replies.pop(0)
        else:
            content = "fake: " + _last_user_text(messages)
            half = max(1, len(content) // 2)
            chunks = [content[:half], content[half:]]
            result = LlmResult(
                content=content,
                input_tokens=max(1, len(" ".join(m.get("content", "") for m in messages)) // 4),
                output_tokens=max(1, len(content) // 4),
            )
        for chunk in chunks:
            yield LlmDelta(chunk)
        yield LlmDone(result)


def llm_from_env() -> LlmClient:
    provider = os.environ.get("LLM_PROVIDER", "fake")
    if provider == "fake":
        return FakeLlm()
    raise ValueError(f"unknown LLM_PROVIDER {provider!r}; only 'fake' exists")
