"""The conversational pattern (docs/02 §5.1): submit, stream, talk again.

AGENT_OPS_KEY is required, AGENT_OPS_BASE_URL defaults to the local edge.
"""

import os

from zszc.agentops import create_client

base_url = os.environ.get("AGENT_OPS_BASE_URL", "http://127.0.0.1:8080")

with create_client(os.environ["AGENT_OPS_KEY"], base_url=base_url) as client:
    print("input declaration:", [param.key for param in client.get_agent().input])

    session = client.new_session()
    for content in ["分析这个项目的进度", "重点看延期的那几个"]:
        run = session.submit(content, params={"project_id": "PRJ-1042"})
        print(f"\nrun {run.id} — {content}")
        for event in session.stream():
            if event.type == "message_started":
                print("  生成中: ", end="", flush=True)
            elif event.type == "message_delta":
                print(event.data["text"], end="", flush=True)
            elif event.type == "run_finished":
                print(flush=True)
                break
        result = run.wait()
        assert result.output == result.output  # the full text, always
