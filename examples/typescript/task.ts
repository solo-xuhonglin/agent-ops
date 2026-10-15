// The task pattern (docs/02 §5.2): one session per job, idempotent submit
// via request_id, and the result fetched later from the run id alone.
// AGENT_OPS_KEY is required, AGENT_OPS_BASE_URL defaults to the local edge.
import { createClient } from "@zszc/agentops";

const key = process.env.AGENT_OPS_KEY;
if (key === undefined) {
  console.error("AGENT_OPS_KEY is required");
  process.exit(1);
}
const baseUrl = process.env.AGENT_OPS_BASE_URL ?? "http://127.0.0.1:8080";

// Replay matching keys on request_id alone; retries must reuse it. A
// timestamp keeps every execution a fresh job (docs/02 §6.1).
const requestId = `examples-job-${new Date().toISOString()}`;
const outputSchema = {
  type: "object",
  properties: { summary: { type: "string" } },
  required: ["summary"],
};

const client = createClient(key, { baseUrl });
const session = await client.newSession();
const run = await session.submit("分析这个项目的进度，生成概要", {
  params: { project_id: "PRJ-1042" },
  outputSchema,
  requestId,
});
console.log(`已提交 run ${run.id}（request_id=${requestId}）`);

const replay = await session.submit("分析这个项目的进度，生成概要", {
  params: { project_id: "PRJ-1042" },
  requestId,
});
console.log("同 request_id 重放返回同一个 run:", replay.id === run.id);

// Simulating "later, in another process": a fresh client holding just the
// run id and the key. wait() polls the log tail, so it converges even if
// none of the original events were seen.
const later = createClient(key, { baseUrl });
const result = await (await later.getRun(run.id)).wait({ timeoutMs: 30_000 });
console.log("结果:", result.output);
