// The conversational pattern (docs/02 §5.1): submit, stream, talk again.
// Run from anywhere: AGENT_OPS_KEY is required, AGENT_OPS_BASE_URL defaults
// to the local edge. The relative import is repo-local; installed callers
// import @zszc/agentops.
import { createClient } from "../../sdk/typescript/src/index.ts";

const baseUrl = process.env.AGENT_OPS_BASE_URL ?? "http://127.0.0.1:8080";
const key = process.env.AGENT_OPS_KEY;
if (key === undefined) {
  console.error("AGENT_OPS_KEY is required");
  process.exit(1);
}

const client = createClient(key, { baseUrl });
console.log("input declaration:", (await client.getAgent()).input.map((param) => param.key).join(", "));

const session = await client.newSession();
for (const content of ["分析这个项目的进度", "重点看延期的那几个"]) {
  const run = await session.submit(content, { params: { project_id: "PRJ-1042" } });
  console.log(`\nrun ${run.id} — ${content}`);
  for await (const event of session.stream()) {
    if (event.type === "state") continue; // keepalive pushes; currentRun rides on them
    console.log(`  ${event.type} seq=${event.seq}`);
    if (event.type === "run_finished") break;
  }
  console.log("  result:", (await run.wait()).output);
}
