"""The task pattern (docs/02 §5.2): one session per job, idempotent submit,
and the result fetched later from the id alone.

AGENT_OPS_KEY is required, AGENT_OPS_BASE_URL defaults to the local edge.
"""

import os

from zszc.agentops import create_client

base_url = os.environ.get("AGENT_OPS_BASE_URL", "http://127.0.0.1:8080")
request_id = "demo-job-2026-10-14"
output_schema = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
}

with create_client(os.environ["AGENT_OPS_KEY"], base_url=base_url) as client:
    session = client.new_session()
    run = session.submit(
        "分析这个项目的进度，生成概要",
        params={"project_id": "PRJ-1042"},
        output_schema=output_schema,
        request_id=request_id,
    )
    print("submitted:", run.id, "status:", run.status)

    replay = session.submit("分析这个项目的进度，生成概要", params={"project_id": "PRJ-1042"}, request_id=request_id)
    print("idempotent replay returns the same run:", replay.id == run.id)

    result = client.get_run(run.id).wait(timeout=30)
    print("result:", result.output)
