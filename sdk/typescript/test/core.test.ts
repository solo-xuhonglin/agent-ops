import assert from "node:assert/strict";
import test from "node:test";
import { FakeApi } from "./fake.ts";
import {
  AgentOpsError,
  AuthError,
  ConflictError,
  ContractError,
  Http,
  HttpError,
  NotFoundError,
  Run,
  RunCancelledError,
  RunFailedError,
  TimeoutError,
  createClient,
} from "../src/index.ts";

const SESSION_ID = "11111111-1111-1111-1111-111111111111";
const RUN_ID = "22222222-2222-2222-2222-222222222222";

function runView(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    run_id: RUN_ID,
    session_id: SESSION_ID,
    status: "pending",
    result: null,
    error: null,
    cancel_requested: false,
    ...over,
  };
}

async function withFake(fn: (fake: FakeApi, baseUrl: string) => Promise<void>): Promise<void> {
  const fake = await FakeApi.start();
  try {
    await fn(fake, fake.baseUrl);
  } finally {
    fake.close();
  }
}

test("json returns the body and sends the bearer key", async () => {
  await withFake(async (fake, baseUrl) => {
    fake.route("GET", "/v1/agent", { body: { input: [] } });
    const client = createClient("k1", { baseUrl });
    assert.deepEqual(await client.getAgent(), { input: [] });
    assert.equal(fake.requests[0].headers.authorization, "Bearer k1");
  });
});

test("error envelopes map by status", async () => {
  const cases: [number, new (...args: never[]) => HttpError][] = [
    [401, AuthError],
    [404, NotFoundError],
    [409, ConflictError],
    [422, ContractError],
    [500, HttpError],
  ];
  for (const [status, cls] of cases) {
    await withFake(async (fake, baseUrl) => {
      fake.route("GET", "/v1/missing", { status, body: { error: { code: "c", message: "m" } } });
      const http = new Http(baseUrl, "k");
      await assert.rejects(() => http.json("GET", "/v1/missing"), (error: unknown) => {
        assert.ok(error instanceof cls, `${cls.name} for ${status}`);
        assert.ok(error instanceof AgentOpsError);
        assert.equal((error as HttpError).code, "c");
        assert.equal((error as HttpError).message, "m");
        return true;
      });
    });
  }
});

test("getAgent parses the input declaration", async () => {
  await withFake(async (fake, baseUrl) => {
    fake.route("GET", "/v1/agent", {
      body: {
        input: [
          { key: "project_id", type: "string", required: true, description: "项目 ID" },
          { key: "include_archived", type: "boolean", required: false, description: "是否包含", default: false },
        ],
      },
    });
    const client = createClient("k", { baseUrl });
    const agent = await client.getAgent();
    assert.equal(agent.input[0].key, "project_id");
    assert.equal(agent.input[0].required, true);
    assert.equal(agent.input[1].default, false);
  });
});

test("getSession is a local handle", async () => {
  await withFake(async (fake, baseUrl) => {
    fake.route("POST", "/v1/sessions", { status: 201, body: { session_id: SESSION_ID } });
    const client = createClient("k", { baseUrl });
    const session = await client.newSession();
    const fetched = client.getSession(SESSION_ID);
    assert.equal(session.id, SESSION_ID);
    assert.equal(fetched.id, SESSION_ID);
    assert.equal(fake.requests.length, 1);
  });
});

test("submit returns the run snapshot and sends all fields", async () => {
  await withFake(async (fake, baseUrl) => {
    fake.route("POST", "/v1/sessions", { status: 201, body: { session_id: SESSION_ID } });
    fake.route("POST", `/v1/sessions/${SESSION_ID}/runs`, { status: 201, body: runView() });
    const client = createClient("k", { baseUrl });
    const session = await client.newSession();
    const run = await session.submit("hi", { params: { project_id: "P" }, outputSchema: { type: "object" }, requestId: "r1" });
    assert.deepEqual([run.id, run.sessionId, run.status], [RUN_ID, SESSION_ID, "pending"]);
    assert.deepEqual(fake.requests.at(-1)?.json, {
      content: "hi",
      params: { project_id: "P" },
      output_schema: { type: "object" },
      request_id: "r1",
    });
    assert.equal(fake.requests.at(-1)?.headers["content-type"], "application/json");
  });
});

test("submit idempotent replay returns the same run", async () => {
  await withFake(async (fake, baseUrl) => {
    fake.route("POST", "/v1/sessions", { status: 201, body: { session_id: SESSION_ID } });
    fake.route("POST", `/v1/sessions/${SESSION_ID}/runs`, { status: 200, body: runView() });
    const client = createClient("k", { baseUrl });
    const session = await client.newSession();
    const run = await session.submit("hi");
    assert.equal(run.id, RUN_ID);
  });
});

test("cancel refreshes the snapshot", async () => {
  await withFake(async (fake, baseUrl) => {
    fake.route("POST", "/v1/sessions", { status: 201, body: { session_id: SESSION_ID } });
    fake.route("POST", `/v1/sessions/${SESSION_ID}/runs`, { status: 201, body: runView() });
    fake.route("POST", `/v1/runs/${RUN_ID}/cancel`, { body: runView({ cancel_requested: true }) });
    const client = createClient("k", { baseUrl });
    const session = await client.newSession();
    const run = await session.submit("hi");
    await run.cancel();
    assert.equal(run.cancelRequested, true);
    assert.equal(fake.requests.at(-1)?.method, "POST");
  });
});

function serving(fake: FakeApi, views: Record<string, unknown>[]): void {
  const remaining = [...views];
  fake.route("GET", `/v1/runs/${RUN_ID}`, {
    handler: () => [200, remaining.length > 1 ? (remaining.shift() as Record<string, unknown>) : remaining[0]],
  });
}

function bareRun(baseUrl: string): Run {
  return new Run(createClient("k", { baseUrl, pollIntervalMs: 20 }), RUN_ID, SESSION_ID);
}

test("wait polls until success", async () => {
  await withFake(async (fake, baseUrl) => {
    serving(fake, [runView({ status: "running" }), runView({ status: "succeeded", result: "stub: hi" })]);
    const run = bareRun(baseUrl);
    const result = await run.wait();
    assert.equal(result.output, "stub: hi");
    assert.equal(run.status, "succeeded");
  });
});

test("wait on a terminal run resolves with one request", async () => {
  await withFake(async (fake, baseUrl) => {
    serving(fake, [runView({ status: "succeeded", result: "done" })]);
    const run = bareRun(baseUrl);
    await run.wait();
    assert.equal(fake.requests.length, 1);
  });
});

test("wait raises RunFailedError, RunCancelledError, TimeoutError", async () => {
  await withFake(async (fake, baseUrl) => {
    serving(fake, [runView({ status: "failed", error: "boom" })]);
    const run = bareRun(baseUrl);
    await assert.rejects(() => run.wait(), RunFailedError);
  });
  await withFake(async (fake, baseUrl) => {
    fake.route("GET", `/v1/runs/${RUN_ID}`, { body: runView({ status: "cancelled" }) });
    const run = bareRun(baseUrl);
    await assert.rejects(() => run.wait(), RunCancelledError);
  });
  await withFake(async (fake, baseUrl) => {
    fake.route("GET", `/v1/runs/${RUN_ID}`, { body: runView({ status: "running" }) });
    const run = bareRun(baseUrl);
    await assert.rejects(() => run.wait({ timeoutMs: 60 }), TimeoutError);
    assert.equal(run.status, "running");
  });
});
