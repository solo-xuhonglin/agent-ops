// The task pattern (docs/02 §5.2): one session per job, idempotent submit,
// and the result fetched later from the id alone.
// AGENT_OPS_KEY is required, AGENT_OPS_BASE_URL defaults to the local edge.
import { createClient } from "@zszc/agentops";

const baseUrl = process.env.AGENT_OPS_BASE_URL ?? "http://127.0.0.1:8080";
const key = process.env.AGENT_OPS_KEY;
if (key === undefined) {
  console.error("AGENT_OPS_KEY is required");
  process.exit(1);
}

const requestId = "demo-job-2026-10-14";
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
console.log("submitted:", run.id, "status:", run.status);

const replay = await session.submit("分析这个项目的进度，生成概要", {
  params: { project_id: "PRJ-1042" },
  requestId,
});
console.log("idempotent replay returns the same run:", replay.id === run.id);

// 稍后取结果：getRun 是异步的，先拿到句柄再 wait（docs/02 §5.2 的断线续传）
const handle = await client.getRun(run.id);
const result = await handle.wait({ timeoutMs: 30_000 });
console.log("result:", result.output);
