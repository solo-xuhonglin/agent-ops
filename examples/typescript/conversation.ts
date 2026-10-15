// The conversational pattern (docs/02 §5.1): subscribe to the session,
// then submit turns and render the three-part message stream.
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
const events = await session.stream(); // resolves once the subscription is live

for (const content of ["分析这个项目的进度", "重点看延期的那几个"]) {
  const run = await session.submit(content, { params: { project_id: "PRJ-1042" } });
  console.log(`\nrun ${run.id.slice(0, 8)} — ${content}`);

  // The three-part message stream prints live: content deltas, then the end
  // event closes the turn. end.text always carries the full authoritative
  // text — the SDK synthesizes it from the result when the stream frames
  // were missed. Manual next(): `for await` + break closes the generator,
  // and this subscription is reused across turns.
  for (;;) {
    const event = (await events.next()).value!;
    if (event.type === "text_message_content") process.stdout.write(event.data.delta);
    else if (event.type === "text_message_end") {
      process.stdout.write("\n");
      break;
    } else if (event.type === "run_failed") {
      console.log(`运行失败: ${event.data.error}`);
      break;
    }
  }
}
await events.return(undefined); // close the subscription so the process can exit
