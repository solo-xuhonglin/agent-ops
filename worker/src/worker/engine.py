import json
import threading
import time
import uuid

from shared.events import EventType
from shared.models import ExecutionRun, RunLog
from shared.spec import AgentSpec
from sqlalchemy import select
from sqlalchemy.orm import Session

from worker.llm import LlmClient, LlmDelta, LlmResult
from worker.tool_exec import CredentialUnavailable, ToolError, execute_tool

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
    message itself. Tool rounds of prior runs stay trace-only.
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
            if marker is not None:
                # The summary replaces everything before it (docs/05 §8.4) —
                # even when this run never produced a result (cancelled after
                # compaction); its own exchange rides after it, if any.
                messages = [_summary_message(marker.payload["summary"])]
            if started is None or last_result is None:
                continue
            messages.append(
                {
                    "role": "user",
                    "content": render_user_message(started.payload["content"], started.payload.get("params", {}), spec),
                }
            )
            messages.append({"role": "assistant", "content": last_result.payload.get("content", "")})
    return messages


def execute(engine, dispatch: dict, llm: LlmClient, cancelled: threading.Event):
    """Walk the ReAct loop as a generator of write-ordered items.

    Yields {"kind": "row", ...} durable log rows and {"kind": "stream", ...}
    never-persisted increments (docs/05 §2, §3.3). The caller makes each row
    durable before its wire frame. Idempotent across re-dispatches: terminal
    tails yield nothing; an open llm_call or tool_call is retried with the
    same action_id and arguments straight from the log (docs/05 §5).
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

    def tool_round(name: str, action_id: str, arguments: dict):
        """One tool invocation as rows; returns the message to append, or
        None after yielding run_failed (credential unavailable, docs/09 §4)."""
        run_id_text = str(run_id)
        yield row(
            "tool_call",
            action_id,
            {"tool": name, "arguments": arguments, "idempotency_key": f"{run_id_text}:{action_id}"},
        )
        tool = next((item for item in spec.tools if item.name == name), None)
        if tool is None:
            outcome = {
                "ok": False,
                "result": None,
                "error": f"unknown tool {name}",
                "duration_ms": 0,
                "truncated": False,
            }
        else:
            try:
                outcome = execute_tool(run_id_text, action_id, tool, arguments)
            except CredentialUnavailable:
                yield row("run_failed", None, {"error": f"credential '{tool.credential}' unavailable"})
                return None
            except ToolError as error:
                outcome = {"ok": False, "result": None, "error": str(error), "duration_ms": 0, "truncated": False}
        yield row(
            "tool_result",
            action_id,
            {
                "result": outcome["result"],
                "error": outcome["error"],
                "duration_ms": outcome["duration_ms"],
                "truncated": outcome["truncated"],
            },
        )
        content = outcome["result"] if outcome["ok"] else f"error: {outcome['error']}"
        return {"role": "tool", "tool_call_id": action_id, "content": content}

    def assistant_tool_calls(calls: list[dict]) -> dict:
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": json.dumps(call.get("arguments", {}), ensure_ascii=False),
                    },
                }
                for call in calls
            ],
        }

    # Open actions of either kind, retried as-is (docs/05 §5).
    pending: dict[str, RunLog] = {}
    for row_ in rows:
        if row_.type in (EventType.llm_call, EventType.tool_call):
            pending[row_.action_id] = row_
        elif row_.type in (EventType.llm_result, EventType.tool_result):
            pending.pop(row_.action_id, None)
    resume = max(pending.values(), key=lambda r: r.seq) if pending else None
    iterations_used = sum(1 for row_ in rows if row_.type == EventType.llm_result)

    def rebuild_round_tail(result_row) -> list[dict]:
        """The round's assistant tool_calls + completed tool messages, from rows."""
        round_calls = [r for r in rows if r.type == EventType.tool_call and r.seq > result_row.seq]
        completed = [r for r in rows if r.type == EventType.tool_result and r.seq > result_row.seq]
        return [
            assistant_tool_calls(
                [
                    {"id": call.action_id, "name": call.payload["tool"], "arguments": call.payload["arguments"]}
                    for call in round_calls
                ]
            ),
            *[
                {
                    "role": "tool",
                    "tool_call_id": result.action_id,
                    "content": result.payload.get("result") or f"error: {result.payload.get('error')}",
                }
                for result in completed
            ],
        ]

    if resume is not None and resume.type == EventType.llm_call:
        messages = resume.payload["messages"]
        first_action = resume.action_id
    elif resume is None and any(r.type == EventType.llm_result for r in rows):
        # A crash between rounds (no open action): continue from the run's
        # own last round, or finish a final answer that never got its tail.
        last_result_row = max((r for r in rows if r.type == EventType.llm_result), key=lambda r: r.seq)
        if not last_result_row.payload.get("tool_calls"):
            yield row("run_finished", None, {"status": "succeeded", "result": last_result_row.payload.get("content")})
            return
        call_row = max(
            (r for r in rows if r.type == EventType.llm_call and r.seq < last_result_row.seq), key=lambda r: r.seq
        )
        messages = [*call_row.payload["messages"], *rebuild_round_tail(last_result_row)]
        first_action = None
    else:
        first_action = None
        if resume is not None and resume.type == EventType.tool_call:
            # Rebuild the interrupted round from its own rows: the round's
            # llm_call payload + its assistant tool_calls + completed tools.
            call_row = max(
                (r for r in rows if r.type == EventType.llm_call and r.seq < resume.seq), key=lambda r: r.seq
            )
            result_row = max(
                (r for r in rows if r.type == EventType.llm_result and call_row.seq < r.seq < resume.seq),
                key=lambda r: r.seq,
            )
            round_calls = [r for r in rows if r.type == EventType.tool_call and r.seq > result_row.seq]
            completed = [r for r in rows if r.type == EventType.tool_result and r.seq > result_row.seq]
            messages = [
                *call_row.payload["messages"],
                assistant_tool_calls(
                    [
                        {"id": call.action_id, "name": call.payload["tool"], "arguments": call.payload["arguments"]}
                        for call in round_calls
                    ]
                ),
                *[
                    {
                        "role": "tool",
                        "tool_call_id": result.action_id,
                        "content": result.payload.get("result") or f"error: {result.payload.get('error')}",
                    }
                    for result in completed
                ],
            ]
            message = yield from tool_round(resume.payload["tool"], resume.action_id, resume.payload["arguments"])
            if message is None:
                return
            if cancelled.is_set():
                yield row("run_finished", None, {"status": "cancelled", "result": None})
                return
            messages = [*messages, message]
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

    tool_defs = [tool.to_definition() for tool in spec.tools]
    run_id_text = str(run_id)

    while True:
        if iterations_used >= spec.max_iterations:
            yield row("run_failed", None, {"error": f"max iterations ({spec.max_iterations}) exceeded"})
            return
        action_id = first_action or str(uuid.uuid4())
        first_action = None
        yield row("llm_call", action_id, {"messages": messages})

        started = time.perf_counter()
        yield {"kind": "stream", "type": "TEXT_MESSAGE_START", "data": {"run_id": run_id_text}}
        result = LlmResult(content="")
        for event in llm.stream(messages, tools=tool_defs, model_id=spec.model_id, model_params=spec.model_params):
            if cancelled.is_set():
                break
            if isinstance(event, LlmDelta):
                yield {"kind": "stream", "type": "TEXT_MESSAGE_CONTENT", "data": {"run_id": run_id_text, "delta": event.text}}
            else:
                result = event.result
        if cancelled.is_set():
            yield row("run_finished", None, {"status": "cancelled", "result": None})
            return
        yield {"kind": "stream", "type": "TEXT_MESSAGE_END", "data": {"run_id": run_id_text}}

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
        iterations_used += 1

        if not result.tool_calls:
            yield row("run_finished", None, {"status": "succeeded", "result": result.content})
            return

        calls = [{"id": str(uuid.uuid4()), **call} for call in result.tool_calls]
        messages = [*messages, assistant_tool_calls(calls)]
        for call in calls:
            if cancelled.is_set():
                yield row("run_finished", None, {"status": "cancelled", "result": None})
                return
            message = yield from tool_round(call["name"], call["id"], call.get("arguments", {}))
            if message is None:
                return
            messages = [*messages, message]


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
        [{"role": "user", "content": prompt}], tools=[], model_id=spec.model_id, model_params=spec.model_params
    ):
        if cancelled.is_set():
            break
        if not isinstance(event, LlmDelta):
            usage = event.result
    duration_ms = int((time.perf_counter() - started) * 1000)
    return usage.content, usage, duration_ms
