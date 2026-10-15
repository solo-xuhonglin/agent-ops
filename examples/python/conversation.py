"""The conversational pattern (docs/02 §5.1): subscribe to the session,
then submit turns and collect deltas from the stream.

AGENT_OPS_KEY is required, AGENT_OPS_BASE_URL defaults to the local edge.
"""

import os

from zszc.agentops import create_client

key = os.environ.get("AGENT_OPS_KEY")
if key is None:
    raise SystemExit("AGENT_OPS_KEY is required")
base_url = os.environ.get("AGENT_OPS_BASE_URL", "http://127.0.0.1:8080")

with create_client(key, base_url=base_url) as client:
    # The input declaration is negotiated: see what the agent wants first
    agent = client.get_agent()
    print("输入声明:")
    for param in agent.input:
        flag = "必填" if param.required else "可选"
        print(f"  {param.key}: {param.type}（{flag}）— {param.description}")

    session = client.new_session()
    events = session.stream()  # returns once the subscription is live

    for content in ["分析这个项目的进度", "重点看延期的那几个"]:
        run = session.submit(content, params={"project_id": "PRJ-1042"})
        print(f"\nrun {run.id[:8]} — {content}")

        # Stream frames carry no seq and are best-effort: render them live,
        # but the terminal event is the authoritative outcome.
        streamed = False
        for event in events:
            if event.type == "message_started":
                print("  回复: ", end="", flush=True)
            elif event.type == "message_delta":
                streamed = True
                print(event.data["text"], end="", flush=True)
            elif event.type == "run_finished":
                if streamed:
                    print()
                else:  # subscribed too late for the deltas
                    print(f"  回复: {event.data['result']}")
                break
            elif event.type == "run_failed":
                print(f"\n  运行失败: {event.data['error']}")
                break

    events.close()
