import assert from "node:assert/strict";
import test from "node:test";
import { FakeApi } from "./fake.ts";
import { NotFoundError, createClient } from "../src/index.ts";
import type { Event } from "../src/index.ts";

const SESSION_ID = "11111111-1111-1111-1111-111111111111";
const RUN_ID = "33333333-3333-3333-3333-333333333333";
const EVENTS = `/v1/sessions/${SESSION_ID}/events`;

function sse(payload: unknown, cursor?: string): Buffer {
  const lines = cursor !== undefined ? [`id: ${cursor}`] : [];
  lines.push(`data: ${JSON.stringify(payload)}`);
  return Buffer.from(lines.join("\n") + "\n\n");
}

function eventFrame(seq: number, type: string, data: Record<string, unknown> = {}): Buffer {
  const payload = { type, data: { run_id: RUN_ID, seq, action_id: null, ...data } };
  return sse(payload, `${RUN_ID}:${seq}`);
}

function stateFrame(runId: string | null): Buffer {
  return sse({ type: "state", data: { session_id: SESSION_ID, run: runId } });
}

async function withFake(fn: (fake: FakeApi, baseUrl: string) => Promise<void>): Promise<void> {
  const fake = await FakeApi.start();
  try {
    await fn(fake, fake.baseUrl);
  } finally {
    fake.close();
  }
}

function streamOf(baseUrl: string) {
  return createClient("k", { baseUrl, reconnectDelayMs: 20 }).getSession(SESSION_ID).stream();
}

async function take(stream: AsyncGenerator<Event>, count: number): Promise<Event[]> {
  const events: Event[] = [];
  for await (const event of stream) {
    events.push(event);
    if (events.length === count) break;
  }
  return events;
}

test("state frames update currentRun", async () => {
  await withFake(async (fake, baseUrl) => {
    fake.routeRaw(EVENTS, [[stateFrame(RUN_ID), stateFrame(null)]], { hold: 0.3 });
    const session = createClient("k", { baseUrl }).getSession(SESSION_ID);

    const stream = session.stream();
    const first = (await stream.next()).value;
    assert.deepEqual([first.type, first.runId, first.seq], ["state", null, null]);
    assert.equal(session.currentRun?.id, RUN_ID);

    const second = (await stream.next()).value;
    assert.equal(second.type, "state");
    assert.equal(session.currentRun, null);
    await stream.return();
  });
});

test("events yield typed frames and suppress duplicates", async () => {
  await withFake(async (fake, baseUrl) => {
    const finished = eventFrame(2, "run_finished", { status: "succeeded", result: "stub: hi" });
    fake.routeRaw(EVENTS, [[eventFrame(1, "run_started", { content: "hi" }), finished, finished]], { hold: 0.3 });

    const events = await take(streamOf(baseUrl), 2);

    assert.deepEqual(
      events.map((event) => [event.type, event.runId, event.seq]),
      [
        ["run_started", RUN_ID, 1],
        ["run_finished", RUN_ID, 2],
      ],
    );
    assert.equal(events[1].data.result, "stub: hi");
  });
});

test("frames split across chunks reassemble", async () => {
  await withFake(async (fake, baseUrl) => {
    const whole = eventFrame(1, "run_started", { content: "hi" });
    fake.routeRaw(EVENTS, [[whole.subarray(0, 20), whole.subarray(20)]], { hold: 0.3 });

    const events = await take(streamOf(baseUrl), 1);

    assert.deepEqual([events[0].type, events[0].seq], ["run_started", 1]);
  });
});

test("reconnect resumes from the cursor and suppresses replay", async () => {
  await withFake(async (fake, baseUrl) => {
    fake.routeRaw(
      EVENTS,
      [
        [eventFrame(1, "run_started", { content: "hi" }), eventFrame(2, "llm_call", { action_id: "a1" })],
        [eventFrame(2, "llm_call", { action_id: "a1" }), eventFrame(3, "run_finished", { status: "succeeded" })],
      ],
      { drop: true },
    );

    const events = await take(streamOf(baseUrl), 3);

    assert.deepEqual(events.map((event) => event.seq), [1, 2, 3]);
    assert.deepEqual(fake.requests[0].query, {});
    assert.deepEqual(fake.requests[1].query, { from_run: RUN_ID, from_seq: "2" });
  });
});

test("http errors raise without retry", async () => {
  await withFake(async (fake, baseUrl) => {
    fake.route("GET", EVENTS, { status: 404, body: { error: { code: "not_found", message: "no such session" } } });
    const session = createClient("k", { baseUrl, reconnectDelayMs: 20 }).getSession(SESSION_ID);

    await assert.rejects(() => session.stream().next(), NotFoundError);
    assert.equal(fake.requests.length, 1);
  });
});

test("an explicit cursor reaches the wire", async () => {
  await withFake(async (fake, baseUrl) => {
    fake.routeRaw(EVENTS, [[eventFrame(4, "run_finished", { status: "succeeded" })]], { hold: 0.3 });
    const session = createClient("k", { baseUrl }).getSession(SESSION_ID);

    const events = await take(session.stream({ fromRun: RUN_ID, fromSeq: 3 }), 1);

    assert.equal(events[0].seq, 4);
    assert.deepEqual(fake.requests[0].query, { from_run: RUN_ID, from_seq: "3" });
  });
});
