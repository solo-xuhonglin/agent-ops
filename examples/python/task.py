"""The task pattern (docs/02 §5.2): one session per job, idempotent submit
via request_id, and the result fetched later from the run id alone.

AGENT_OPS_KEY is required, AGENT_OPS_BASE_URL defaults to the local edge.
"""

import os
import time

from zszc.agentops import create_client

key = os.environ.get("AGENT_OPS_KEY")
if key is None:
    raise SystemExit("AGENT_OPS_KEY is required")
base_url = os.environ.get("AGENT_OPS_BASE_URL", "http://127.0.0.1:8080")

# Replay matching keys on request_id alone; retries must reuse it. A
# timestamp keeps every execution a fresh job (docs/02 §6.1).
request_id = f"examples-job-{time.strftime('%Y%m%d-%H%M%S')}"
output_schema = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
}

with create_client(key, base_url=base_url) as client:
    session = client.new_session()
    run = session.submit(
        "分析这个项目的进度，生成概要",
        params={"project_id": "PRJ-1042"},
        output_schema=output_schema,
        request_id=request_id,
    )
    print(f"已提交 run {run.id}（request_id={request_id}）")

    replay = session.submit("分析这个项目的进度，生成概要", params={"project_id": "PRJ-1042"}, request_id=request_id)
    print(f"同 request_id 重放返回同一个 run: {replay.id == run.id}")

# Simulating "later, in another process": a fresh client holding just the
# run id and the key. wait() polls the log tail, so it converges even if
# none of the original events were seen.
with create_client(key, base_url=base_url) as client:
    result = client.get_run(run.id).wait(timeout=30)
    print("结果:", result.output)
