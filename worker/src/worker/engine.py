from shared.events import EventType
from shared.models import ExecutionRun, RunLog
from shared.spec import AgentSpec
from sqlalchemy import select
from sqlalchemy.orm import Session


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


def session_messages(engine, session_id: str, spec: AgentSpec) -> list[dict]:
    """Rebuild the conversation from prior runs' logs (docs/05 §5).

    Each prior run contributes its user message (rendered from run_started)
    and its last assistant message; the current run is not included — the
    engine adds its own user message on top of this.
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
            rows = session.scalars(select(RunLog).where(RunLog.run_id == run_id).order_by(RunLog.seq)).all()
            started = next((row for row in rows if row.type == EventType.run_started), None)
            last_result = None
            for row in rows:
                if row.type == EventType.llm_result:
                    last_result = row
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
