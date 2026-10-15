import os
import re
import time

import httpx
from shared.spec import ToolSpec

TOOL_TIMEOUT = 10.0


class ToolError(Exception):
    """The tool request could not be built from the model's arguments."""


class CredentialUnavailable(Exception):
    """A declared credential has no value; the run fails (docs/09 §4)."""


def resolve_credential(name: str) -> str:
    # v1 resolver: the environment. The secrets store (unit 13) replaces it.
    value = os.environ.get(f"CREDENTIAL_{name.upper()}")
    if value is None:
        raise CredentialUnavailable(name)
    return value


def _substitute(template: str, arguments: dict, credential: str | None) -> str:
    def replace(match: re.Match) -> str:
        key = match.group(1)
        if key == "credential":
            if credential is None:
                raise ToolError("credential placeholder without a credential")
            return credential
        if key.startswith("args."):
            if key[5:] not in arguments:
                raise ToolError(f"unresolved placeholder {{{{{key}}}}}")
            return str(arguments[key[5:]])
        raise ToolError(f"unknown placeholder {{{{{key}}}}}")

    return re.sub(r"\{\{(\w+|\w+\.\w+)\}\}", replace, template)


def render_request(tool: ToolSpec, arguments: dict, credential: str | None) -> tuple[str, str, dict, str | None]:
    """Materialize the outbound request; the spec itself keeps its placeholders."""
    url = _substitute(tool.url_template, arguments, credential)
    headers = {key: _substitute(value, arguments, credential) for key, value in tool.headers.items()}
    body = _substitute(tool.body_template, arguments, credential) if tool.body_template is not None else None
    return tool.method, url, headers, body


def execute_tool(run_id: str, action_id: str, tool: ToolSpec, arguments: dict) -> dict:
    """One HTTP attempt; retries are the sweep's re-dispatch, made safe by the
    idempotency key (docs/05 §5)."""
    credential = resolve_credential(tool.credential) if tool.credential else None
    method, url, headers, body = render_request(tool, arguments, credential)
    headers["Idempotency-Key"] = f"{run_id}:{action_id}"

    started = time.perf_counter()
    try:
        response = httpx.request(method, url, headers=headers, content=body, timeout=TOOL_TIMEOUT)
        duration_ms = int((time.perf_counter() - started) * 1000)
        raw = response.text
        if response.status_code >= 400:
            return {
                "ok": False,
                "result": None,
                "error": f"HTTP {response.status_code}",
                "duration_ms": duration_ms,
                "truncated": False,
            }
    except httpx.HTTPError as error:
        duration_ms = int((time.perf_counter() - started) * 1000)
        return {
            "ok": False,
            "result": None,
            "error": f"{type(error).__name__}: {error}",
            "duration_ms": duration_ms,
            "truncated": False,
        }

    truncated = len(raw) > tool.response_max_bytes
    return {
        "ok": True,
        "result": raw[: tool.response_max_bytes],
        "error": None,
        "duration_ms": duration_ms,
        "truncated": truncated,
    }
