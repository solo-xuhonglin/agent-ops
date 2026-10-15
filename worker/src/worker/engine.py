import threading
import time
import uuid

from shared.events import EventType
from shared.models import ExecutionRun, RunLog
from shared.spec import AgentSpec
from sqlalchemy import select
from sqlalchemy.orm import Session

from worker.llm import LlmClient, LlmDelta, LlmResult

_TERMINAL_TYPES = {EventType.run_finished, EventType.run_failed}


def _render_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def render_user_message(content: str, params: dict, spec: AgentSpec) -> str:
    """The user message: an annotated params block above the content (docs/02 §4.2).

    Rendered in declaration order; defaults filled; declared params with no
    default that were not passed are omitted entirely.
    """
    lines = []
    for param in spec.input:
        value = params.get(param.key, param.default)
        if value is None:
            continue
        lines.append(f"- {param.key}（{param.description}）: {_render_value(value)}")
    if not lines:
        return content
    block = "<输入参数>\n" + "\n".join(lines) + "\n</输入参数>"
    return f"{block}\n\n{content}"


def session_messages(engine, session_id: str, spec: AgentSpec, exclude_run_id: str) -> list[dict]:
    """Rebuild the conversation from prior runs' logs (docs/05 §5).

    Each prior run contributes its user message (rendered from run_started)
    and its last assistant message. exclude_run_id keeps the current run's
    own (possibly half-finished) exchange out — the engine adds its user
    message itself.
    """
    messages: list[dict] = []
    with Session(engine) as session:
        run_ids = [
            str(value)
            for value in session.scalars(
                select(ExecutionRun.id).where(ExecutionRun.session_id == session_id).order_by(ExecutionRun.created_at)
            )
        ]
        for run_id in run_ids:
            if run_id == exclude_run_id:
                continue
            rows = session.scalars(select(RunLog).where(RunLog.run_id == run_id).order_by(RunLog.seq)).all()
            started = next((row for row in rows if row.type == EventType.run_started), None)
            last_result = None
            marker = None
            for row in rows:
                if row.type == EventType.llm_result:
                    last_result = row
                elif row.type == EventType.context_compacted:
                    marker = row
            if started is None or last_result is None:
                continue
            if marker is not None:
                # The summary replaces everything before it (docs/05 §8.4);
                # the compacting run's own exchange rides after it.
                messages = [_summary_message(marker.payload["summary"])]
            messages.append(
                {
                    "role": "user",
                    "content": render_user_message(started.payload["content"], started.payload.get("params", {}), spec),
                }
            )
            messages.append({"role": "assistant", "content": last_result.payload.get("content", "")})
    return messages


def execute(engine, dispatch: dict, llm: LlmClient, cancelled: threading.Event):
    """Walk one ReAct iteration as a generator of write-ordered items.

    Yields {"kind": "row", ...} durable log rows and {"kind": "stream", ...}
    never-persisted increments (docs/05 §2, §3.3). The caller makes each row
    durable before its wire frame. Idempotent across re-dispatches: terminal
    tails yield nothing, and an open llm_call is retried (Task 5) with the
    same action_id (docs/05 §5).
    """
    run_id = uuid.UUID(dispatch["run_id"])
    with Session(engine) as session:
        run = session.scalar(select(ExecutionRun).where(ExecutionRun.id == run_id))
        if run is None or run.epoch != dispatch["epoch"]:
            return
        rows = session.scalars(select(RunLog).where(RunLog.run_id == run_id).order_by(RunLog.seq)).all()
        pre_cancelled = run.cancel_requested
    if rows and rows[-1].type in _TERMINAL_TYPES:
        return

    state = {"seq": rows[-1].seq if rows else 0}

    def row(type_: str, action_id: str | None, payload: dict) -> dict:
        state["seq"] += 1
        return {"kind": "row", "type": type_, "seq": state["seq"], "action_id": action_id, "payload": payload}

    spec = AgentSpec.model_validate(dispatch["snapshot"])

    if not any(row_.type == EventType.run_started for row_ in rows):
        payload = dispatch["input"]
        yield row(
            "run_started",
            None,
            {
                "content": payload["content"],
                "params": payload["params"],
                "output_schema": payload["output_schema"],
                "session_id": dispatch["session_id"],
                "caller": payload["caller"],
            },
        )

    if pre_cancelled or cancelled.is_set():
        yield row("run_finished", None, {"status": "cancelled", "result": None})
        return

    # An open llm_call is retried as-is: a NEW row with the SAME action_id and
    # the messages copied from the log — no model re-decision (docs/05 §5).
    pending: dict[str, RunLog] = {}
    for row_ in rows:
        if row_.type == EventType.llm_call:
            pending[row_.action_id] = row_
        elif row_.type == EventType.llm_result:
            pending.pop(row_.action_id, None)

    if pending:
        resume = max(pending.values(), key=lambda r: r.seq)
        action_id = resume.action_id
        messages = resume.payload["messages"]
    else:
        history = session_messages(engine, dispatch["session_id"], spec, str(run_id))
        user_message = render_user_message(dispatch["input"]["content"], dispatch["input"]["params"], spec)
        user_entry = {"role": "user", "content": user_message}
        context = [*history, user_entry]
        logged = next((r for r in rows if r.type == EventType.context_compacted), None)
        if logged is not None:
            history = [_summary_message(logged.payload["summary"]), *history[-2:]]
        elif estimate_tokens(context) >= spec.context.token_limit and len(history) > 2:
            oldest, kept = history[:-2], history[-2:]
            summary, usage, summary_ms = _summarize(llm, spec, oldest, cancelled)
            if cancelled.is_set():
                yield row("run_finished", None, {"status": "cancelled", "result": None})
                return
            compacted = [_summary_message(summary), *kept]
            yield row(
                "context_compacted",
                None,
                {
                    "summary": usage.content,
                    "before_tokens": estimate_tokens(context),
                    "after_tokens": estimate_tokens([*compacted, user_entry]),
                    "compacted_messages": len(oldest),
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cost": usage.cost,
                    "duration_ms": summary_ms,
                },
            )
            history = compacted
        messages = [{"role": "system", "content": spec.instructions}, *history, user_entry]
        action_id = str(uuid.uuid4())
    yield row("llm_call", action_id, {"messages": messages})

    started = time.perf_counter()
    run_id_text = str(run_id)
    yield {"kind": "stream", "type": "message_started", "data": {"run_id": run_id_text}}
    result = LlmResult(content="")
    for event in llm.stream(messages, model_id=spec.model_id, model_params=spec.model_params):
        if cancelled.is_set():
            break
        if isinstance(event, LlmDelta):
            yield {"kind": "stream", "type": "message_delta", "data": {"run_id": run_id_text, "text": event.text}}
        else:
            result = event.result
    if cancelled.is_set():
        yield row("run_finished", None, {"status": "cancelled", "result": None})
        return

    yield row(
        "llm_result",
        action_id,
        {
            "content": result.content,
            "tool_calls": result.tool_calls,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "cost": result.cost,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )
    if result.tool_calls:
        yield row("run_failed", None, {"error": "the model requested tools but none are available"})
        return
    yield row("run_finished", None, {"status": "succeeded", "result": result.content})


def estimate_tokens(messages: list[dict]) -> int:
    """A provider-independent stand-in for a real tokenizer: chars//4."""
    return sum(max(1, len(str(message.get("content", ""))) // 4) for message in messages)


def _summary_message(summary: str) -> dict:
    return {"role": "user", "content": f"<历史摘要>\n{summary}"}


def _summarize(
    llm: LlmClient, spec: AgentSpec, oldest: list[dict], cancelled: threading.Event
) -> tuple[str, LlmResult, int]:
    """Summarize the oldest history with a silent LLM call (no stream frames).

    The summary is engine behavior, not a chat message (docs/05 §8.3); its
    tokens and cost ride the context_compacted row instead.
    """
    started = time.perf_counter()
    prompt = "请把以下对话压缩成一段要点摘要：\n" + "\n".join(f"[{m['role']}] {m['content']}" for m in oldest)
    usage = LlmResult(content="")
    for event in llm.stream(
        [{"role": "user", "content": prompt}], model_id=spec.model_id, model_params=spec.model_params
    ):
        if cancelled.is_set():
            break
        if not isinstance(event, LlmDelta):
            usage = event.result
    duration_ms = int((time.perf_counter() - started) * 1000)
    return usage.content, usage, duration_ms
