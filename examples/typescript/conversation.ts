// The conversational pattern (docs/02 §5.1): subscribe to the session,
// then submit turns and collect deltas from the stream.
// AGENT_OPS_KEY is required, AGENT_OPS_BASE_URL defaults to the local edge.
import { createClient } from "@zszc/agentops";

const key = process.env.AGENT_OPS_KEY;
if (key === undefined) {
  console.error("AGENT_OPS_KEY is required");
  process.exit(1);
}
const baseUrl = process.env.AGENT_OPS_BASE_URL ?? "http://127.0.0.1:8080";

const client = createClient(key, { baseUrl });

// The input declaration is negotiated: see what the agent wants first
const agent = await client.getAgent();
console.log("输入声明:");
for (const param of agent.input) {
  const flag = param.required ? "必填" : "可选";
  console.log(`  ${param.key}: ${param.type}（${flag}）— ${param.description}`);
}

const session = await client.newSession();
const events = session.stream();
await events.next(); // first state push: the subscription is live

for (const content of ["分析这个项目的进度", "重点看延期的那几个"]) {
  const run = await session.submit(content, { params: { project_id: "PRJ-1042" } });
  console.log(`\nrun ${run.id.slice(0, 8)} — ${content}`);

  // Stream frames carry no seq and are best-effort: render them live,
  // but the terminal event is the authoritative outcome.
  let streamed = false;
  for (;;) {
    const event = (await events.next()).value!;
    if (event.type === "message_started") process.stdout.write("  回复: ");
    else if (event.type === "message_delta") {
      streamed = true;
      process.stdout.write(event.data.text);
    } else if (event.type === "run_finished") {
      if (streamed) process.stdout.write("\n");
      else console.log(`  回复: ${event.data.result}`); // subscribed too late for the deltas
      break;
    } else if (event.type === "run_failed") {
      console.log(`\n  运行失败: ${event.data.error}`);
      break;
    }
  }
}
await events.return(undefined); // close the subscription so the process can exit
