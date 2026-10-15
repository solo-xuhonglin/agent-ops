import { createClient } from "../src/client.ts";

const baseUrl = process.env.AGENT_OPS_BASE_URL ?? "http://127.0.0.1:8080";
const key = process.env.AGENT_OPS_KEY;
if (key === undefined) {
  console.error("AGENT_OPS_KEY is required (the agent's permanent key)");
  process.exit(1);
}

const client = createClient(key, { baseUrl, pollIntervalMs: 200, reconnectDelayMs: 500 });
const agent = await client.getAgent();
console.log("agent input:", agent.input.map((param) => param.key).join(", "));

const session = await client.newSession();
const run = await session.submit("hello from the typescript sdk", { params: { project_id: "P" } });

const types: string[] = [];
for await (const event of session.stream()) {
  if (event.type !== "state") types.push(event.type);
  if (event.type === "run_finished") break;
}
console.log("events:", types.join(" -> "));

const result = await run.wait({ timeoutMs: 30_000 });
console.log("result:", result.output);
if (typeof result.output !== "string" || !result.output.startsWith("fake: ") || !result.output.includes("hello from the typescript sdk")) {
  console.error("unexpected engine output");
  process.exit(1);
}
console.log("smoke ok");
