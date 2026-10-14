import assert from "node:assert/strict";
import test from "node:test";
import { FakeApi, sleep } from "./fake.ts";
import { createClient } from "../src/index.ts";

const SESSION_ID = "11111111-1111-1111-1111-111111111111";
const EVENTS = `/v1/sessions/${SESSION_ID}/events`;

// Isolated in its own file: a stream() that reconnects on malformed payloads
// loops forever without yielding, which wedges the whole test process — the
// very bug this test pins.
test("malformed frames raise instead of reconnecting", async () => {
  const fake = await FakeApi.start();
  try {
    fake.routeRaw(EVENTS, [[Buffer.from("data: {not json\n\n")]], { drop: true });
    const session = createClient("k", { baseUrl: fake.baseUrl, reconnectDelayMs: 20 }).getSession(SESSION_ID);

    const stream = session.stream();
    try {
      const guard = sleep(3000).then(() => {
        throw new Error("no error raised — the sdk is stuck in a reconnect loop");
      });
      await assert.rejects(() => Promise.race([stream.next(), guard]), SyntaxError);
    } finally {
      await stream.return();
    }
  } finally {
    fake.close();
  }
});
