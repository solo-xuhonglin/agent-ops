import assert from "node:assert/strict";
import test from "node:test";
import { FakeApi } from "./fake.ts";
import { Http } from "../src/http.ts";
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

async function streamOf(baseUrl: string, opts: { idleTimeoutMs?: number } = {}) {
  return createClient("k", { baseUrl, reconnectDelayMs: 20, ...opts }).getSession(SESSION_ID).stream();
}

async function take(stream: AsyncGenerator<Event>, count: number): Promise<Event[]> {
  const events: Event[] = [];
  for await (const event of stream) {
    events.push(event);
    if (events.length === count) break;
  }
  return events;
}

test("stream resolves once the subscription is live", async () => {
  await withFake(async (fake, baseUrl) => {
    fake.routeRaw(EVENTS, [[stateFrame(RUN_ID), eventFrame(1, "RUN_STARTED", { content: "hi" })]], { hold: 0.3 });
    const session = createClient("k", { baseUrl, reconnectDelayMs: 20 }).getSession(SESSION_ID);

    const stream = await session.stream();
    assert.equal(fake.requests.length, 1); // the subscription is on the wire before submit could race it

    const first = (await stream.next()).value!;
    assert.deepEqual([first.type, first.seq], ["RUN_STARTED", 1]);
    await stream.return();
  });
});

test("state frames stay internal", async () => {
  await withFake(async (fake, baseUrl) => {
    fake.routeRaw(
      EVENTS,
      [[stateFrame(RUN_ID), eventFrame(1, "RUN_STARTED", { content: "hi" }), stateFrame(null), eventFrame(2, "RUN_FINISHED", { status: "succeeded" })]],
      { hold: 0.3 },
    );
    const session = createClient("k", { baseUrl, reconnectDelayMs: 20 }).getSession(SESSION_ID);

    const stream = await session.stream();
    const seen = await take(stream, 2);
    await stream.return();

    assert.deepEqual(
      seen.map((event) => [event.type, event.seq]),
      [
        ["RUN_STARTED", 1],
        ["RUN_FINISHED", 2],
      ],
    );
    assert.equal(session.currentRun, null); // the digested state frames still drive currentRun
  });
});

test("events yield typed frames and suppress duplicates", async () => {
  await withFake(async (fake, baseUrl) => {
    const finished = eventFrame(2, "RUN_FINISHED", { status: "succeeded", result: "stub: hi" });
    fake.routeRaw(EVENTS, [[stateFrame(RUN_ID), eventFrame(1, "RUN_STARTED", { content: "hi" }), finished, finished]], { hold: 0.3 });

    const events = await take(await streamOf(baseUrl), 3);

    assert.deepEqual(
      events.map((event) => [event.type, event.runId, event.seq]),
      [
        ["RUN_STARTED", RUN_ID, 1],
        ["message", RUN_ID, null],
        ["RUN_FINISHED", RUN_ID, 2],
      ],
    );
    assert.deepEqual(events[1].data, { text: "stub: hi", delta: "", done: true }); // sealed by the string result
    assert.equal(events[2].data.result, "stub: hi");
  });
});

test("frames split across chunks reassemble", async () => {
  await withFake(async (fake, baseUrl) => {
    const whole = eventFrame(1, "RUN_STARTED", { content: "hi" });
    fake.routeRaw(EVENTS, [[stateFrame(RUN_ID), whole.subarray(0, 20), whole.subarray(20)]], { hold: 0.3 });

    const events = await take(await streamOf(baseUrl), 1);

    assert.deepEqual([events[0].type, events[0].seq], ["RUN_STARTED", 1]);
  });
});

test("reconnect resumes from the cursor and suppresses replay", async () => {
  await withFake(async (fake, baseUrl) => {
    fake.routeRaw(
      EVENTS,
      [
        [stateFrame(RUN_ID), eventFrame(1, "RUN_STARTED", { content: "hi" }), eventFrame(2, "llm_call", { action_id: "a1" })],
        [stateFrame(RUN_ID), eventFrame(2, "llm_call", { action_id: "a1" }), eventFrame(3, "RUN_FINISHED", { status: "succeeded" })],
      ],
      { drop: true },
    );

    const events = await take(await streamOf(baseUrl), 3);

    assert.deepEqual(events.map((event) => event.seq), [1, 2, 3]);
    assert.deepEqual(fake.requests[0].query, {});
    assert.deepEqual(fake.requests[1].query, { from_run: RUN_ID, from_seq: "2" });
  });
});

test("http errors raise without retry", async () => {
  await withFake(async (fake, baseUrl) => {
    fake.route("GET", EVENTS, { status: 404, body: { error: { code: "not_found", message: "no such session" } } });
    const session = createClient("k", { baseUrl, reconnectDelayMs: 20 }).getSession(SESSION_ID);

    await assert.rejects(() => session.stream(), NotFoundError);
    assert.equal(fake.requests.length, 1);
  });
});

test("an explicit cursor reaches the wire", async () => {
  await withFake(async (fake, baseUrl) => {
    fake.routeRaw(EVENTS, [[stateFrame(RUN_ID), eventFrame(4, "RUN_FINISHED", { status: "succeeded" })]], { hold: 0.3 });
    const session = createClient("k", { baseUrl }).getSession(SESSION_ID);

    const events = await take(await session.stream({ fromRun: RUN_ID, fromSeq: 3 }), 1);

    assert.equal(events[0].seq, 4);
    assert.deepEqual(fake.requests[0].query, { from_run: RUN_ID, from_seq: "3" });
  });
});

function textFrame(type: string, data: Record<string, unknown> = {}): Buffer {
  return sse({ type, data: { run_id: RUN_ID, ...data } });
}

test("text frames aggregate into message events", async () => {
  await withFake(async (fake, baseUrl) => {
    fake.routeRaw(
      EVENTS,
      [
        [
          stateFrame(RUN_ID),
          textFrame("TEXT_MESSAGE_START"),
          textFrame("TEXT_MESSAGE_CONTENT", { delta: "你" }),
          textFrame("TEXT_MESSAGE_CONTENT", { delta: "好" }),
          textFrame("TEXT_MESSAGE_END"),
          eventFrame(2, "RUN_FINISHED", { status: "succeeded", result: "你好" }),
        ],
      ],
      { hold: 0.3 },
    );

    const events = await take(await streamOf(baseUrl), 4);

    assert.deepEqual(
      events.map((event) => [event.type, event.seq]),
      [
        ["message", null],
        ["message", null],
        ["message", null],
        ["RUN_FINISHED", 2],
      ],
    );
    assert.deepEqual(
      events.slice(0, 3).map((event) => [event.data.done, event.data.delta, event.data.text]),
      [
        [false, "你", "你"],
        [false, "好", "你好"],
        [true, "", "你好"],
      ],
    );
  });
});

test("a string result seals a message even with no stream frames", async () => {
  await withFake(async (fake, baseUrl) => {
    fake.routeRaw(EVENTS, [[stateFrame(RUN_ID), eventFrame(1, "RUN_FINISHED", { status: "succeeded", result: "final" })]], { hold: 0.3 });

    const events = await take(await streamOf(baseUrl), 2);

    assert.deepEqual(events.map((event) => [event.type, event.data]), [
      ["message", { text: "final", delta: "", done: true }],
      ["RUN_FINISHED", events[1].data],
    ]);
  });
});

test("RUN_ERROR does not seal a message", async () => {
  await withFake(async (fake, baseUrl) => {
    fake.routeRaw(EVENTS, [[stateFrame(RUN_ID), eventFrame(1, "RUN_ERROR", { error: "boom" })]], { hold: 0.3 });

    const events = await take(await streamOf(baseUrl), 1);

    assert.deepEqual([events[0].type, events[0].data.error], ["RUN_ERROR", "boom"]);
  });
});

test("stream frames yield without moving the cursor", async () => {
  await withFake(async (fake, baseUrl) => {
    fake.routeRaw(
      EVENTS,
      [
        [stateFrame(RUN_ID), eventFrame(1, "RUN_STARTED", { content: "hi" }), textFrame("TEXT_MESSAGE_CONTENT", { delta: "增量" })],
        [stateFrame(RUN_ID), eventFrame(2, "RUN_FINISHED", { status: "succeeded" })],
      ],
      { drop: true },
    );
    const session = createClient("k", { baseUrl, reconnectDelayMs: 20 }).getSession(SESSION_ID);

    const stream = await session.stream();
    const seen = [(await stream.next()).value!, (await stream.next()).value!];
    const third = (await stream.next()).value!;
    const fourth = (await stream.next()).value!;
    await stream.return();

    assert.deepEqual(
      seen.map((e) => [e.type, e.seq]),
      [
        ["RUN_STARTED", 1],
        ["message", null],
      ],
    );
    assert.equal(seen[1].data.delta, "增量");
    assert.deepEqual([third.type, third.data.done, third.data.text], ["message", true, "增量"]); // sealed by the buffer
    assert.deepEqual([fourth.type, fourth.seq], ["RUN_FINISHED", 2]);
    // The reconnect carried the persisted cursor only, not the stream frame.
    assert.deepEqual(fake.requests[1].query, { from_run: RUN_ID, from_seq: "1" });
  });
});

test("frames flowing never reconnect", async () => {
  await withFake(async (fake, baseUrl) => {
    fake.routeRaw(
      EVENTS,
      [
        [
          stateFrame(RUN_ID),
          eventFrame(1, "RUN_STARTED", { content: "hi" }),
          eventFrame(2, "llm_call", { action_id: "a1" }),
          eventFrame(3, "RUN_FINISHED", { status: "succeeded" }),
        ],
      ],
      { hold: 0.3 },
    );

    const events = await take(await streamOf(baseUrl, { idleTimeoutMs: 200 }), 3);

    assert.deepEqual(events.map((event) => event.seq), [1, 2, 3]);
    assert.equal(fake.requests.length, 1);
  });
});

test("idle silence reconnects with the cursor", async () => {
  await withFake(async (fake, baseUrl) => {
    fake.routeRaw(
      EVENTS,
      [
        [stateFrame(RUN_ID), eventFrame(1, "RUN_STARTED", { content: "hi" })],
        [stateFrame(RUN_ID), eventFrame(2, "RUN_FINISHED", { status: "succeeded" })],
      ],
      { hold: 0.5 }, // the link stays open but silent after the script
    );

    const events = await take(await streamOf(baseUrl, { idleTimeoutMs: 200 }), 2);

    assert.deepEqual(events.map((event) => event.seq), [1, 2]);
    assert.deepEqual(fake.requests[1].query, { from_run: RUN_ID, from_seq: "1" });
  });
});

test("a failed redial keeps retrying with the cursor", async () => {
  await withFake(async (fake, baseUrl) => {
    fake.routeRaw(
      EVENTS,
      [
        [stateFrame(RUN_ID), eventFrame(1, "RUN_STARTED", { content: "hi" })],
        [stateFrame(RUN_ID), eventFrame(2, "RUN_FINISHED", { status: "succeeded" })],
      ],
      { hold: 0.5 },
    );

    const realOpen = Http.prototype.openEvents;
    let calls = 0;
    Http.prototype.openEvents = async function (...args: Parameters<typeof realOpen>) {
      calls += 1;
      if (calls === 2) throw new TypeError("fetch failed"); // the first redial fails
      return realOpen.apply(this, args);
    };
    try {
      const events = await take(await streamOf(baseUrl, { idleTimeoutMs: 200 }), 2);

      assert.deepEqual(events.map((event) => event.seq), [1, 2]);
      assert.equal(calls, 3);
      assert.deepEqual(fake.requests[1].query, { from_run: RUN_ID, from_seq: "1" });
    } finally {
      Http.prototype.openEvents = realOpen;
    }
  });
});
