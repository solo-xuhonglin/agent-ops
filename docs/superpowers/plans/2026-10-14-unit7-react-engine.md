# Unit 7: ReAct Engine with Fake LLM Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the stub executor with a real ReAct engine: LLM calls through a provider-shaped abstraction (fake LLM only), streamed text increments (`message_started` / `message_delta`) all the way to the SDK, and crash-resume from the run log.

**Architecture:** The engine (`worker/engine.py`) is a generator that walks one ReAct iteration — rebuild context from the session's logs, write `llm_call`, stream deltas, write `llm_result`, terminal — yielding durable rows and stream frames in write order; `worker/client.py` persists each row before its wire frame and forwards stream frames unpersisted. The LLM boundary is `LlmClient.stream(messages) -> Iterator[LlmEvent]` mirroring a real provider's chunk stream (deltas + final usage), so the real adapter later changes nothing above it. `manager` forwards stream frames to SSE subscribers and keeps a per-run accumulated-text buffer to serve docs/05 §4.2 step 3 catch-up. External events pass through one shared projection so `llm_call`'s messages (System Prompt lives there) never leave the platform.

**Tech Stack:** Python only (worker + manager + shared); no new dependencies. No LangGraph in v1 — the ReAct loop is hand-written; graph compilation is re-evaluated when DAG lands (roadmap unit 20).

**Spec:** docs/05-execution-and-streaming.md (§2 log vocabulary, §3 external events incl. 引用外发/内容不外发, §4.2 subscribe catch-up, §5 resume, §6 cancel), docs/04-orchestration.md §5 (worker compiles from the snapshot), docs/02-sdk-contract.md §4.2 (params rendering).

## Global Constraints

- LLM abstraction is worker-internal; manager never sees model ids, params, or messages.
- Stream events (`message_started`/`message_delta`) are never persisted, never seq'd, never cursor'd — they ride the run connection and SSE only (docs/05 §3.3).
- `llm_call`'s log payload keeps the full input messages (retry needs them, docs/05 §2); the external event carries `action_id` only. Projection happens in one place, used by both worker wire entries and manager replay.
- Resume semantics (docs/05 §5): retry of an open `llm_call` writes a NEW row (new seq, SAME action_id, same messages); terminal tails are no-ops; `seq` continues from the log tail.
- Cancel: check the run row's `cancel_requested` at engine start (covers pre-dispatch cancels) and a `threading.Event` (manager broadcast) between stream chunks; terminal `cancelled` is written by the worker only (docs/05 §6).
- No tools exist yet: `LlmResult.tool_calls` non-empty → `run_failed`; the iteration ceiling (`max_iterations`) lands with tools in unit 9.
- The stub `worker/executor.py` and its tests are DELETED when the engine replaces them (不留历史包袱).
- Comments/logs English; tests only touch the `*_test` DB; every verification command guarded with `test ${PIPESTATUS[0]} -eq 0 &&`.

## Review Focus

1. **System Prompt leak** — `llm_call` messages must never reach SSE, neither live (wire entry already projected) nor replay (manager projects log rows). Pinned in Task 1 (projection unit test) and Task 6 (replay path test).
2. **Subscribe mid-message** — a subscriber connecting while text is streaming gets state → replay → `message_started` + one `message_delta` with the accumulated text → live (docs/05 §4.2). Pinned in Task 6 with a scripted slow stream.
3. **Resume re-dispatch** — open `llm_call` is retried with a new seq and the same action_id; clients see a fresh `message_started`; terminal tail produces nothing. Pinned in Task 5.
4. **Params rendering exact format** — declared order, defaults filled, no-default-and-absent omitted entirely, the exact `<输入参数>` block from docs/02 §4.2. Pinned in Task 3 with the doc's own example.
5. **Stream frames don't move the SDK cursor** — after yielding only stream frames, a reconnect still sends the last PERSISTED cursor. Pinned in Task 7 (both SDKs).

---

### Task 1: shared — stream event names + external projection

**Files:**
- Modify: `shared/src/shared/events.py`
- Test: `shared/tests/test_events.py` (extend)

**Interfaces:**
- Produces: `STREAM_EVENT_TYPES = ("message_started", "message_delta")` — ride the wire, never a log type.
- Produces: `public_event(entry: dict) -> dict` — maps a full log entry `{"type", "data": {run_id, seq, action_id, **payload}}` to the external event by stripping per-type private fields: `llm_call` → drop `messages`; everything else passes through. One table: `_PRIVATE = {"llm_call": {"messages"}}`.

- [ ] **Step 1: Failing tests** — `public_event` on an `llm_call` entry with messages strips `messages`, keeps `run_id`/`seq`/`action_id` (Review Focus 1); on `run_started`/`llm_result`/`run_finished` entries nothing is stripped; `STREAM_EVENT_TYPES` holds exactly the two names.
- [ ] **Step 2: RED** — `pytest shared/tests -q` → ImportError/AttributeError.
- [ ] **Step 3: Implement** in `events.py` (one function + one dict).
- [ ] **Step 4: GREEN + full shared suite**; **Step 5: Commit** — `feat(shared): stream event names and external event projection`

### Task 2: worker LLM abstraction + fake

**Files:**
- Create: `worker/src/worker/llm.py`
- Test: `worker/tests/test_llm.py`

**Interfaces:**
- Produces: `LlmResult` dataclass (`content: str`, `tool_calls: list[dict] = []`, `input_tokens: int = 0`, `output_tokens: int = 0`, `cost: float = 0.0`); `LlmDelta(text: str)`; `LlmDone(result: LlmResult)`; `LlmEvent = LlmDelta | LlmDone`.
- Produces: `LlmClient` Protocol: `stream(messages: list[dict], *, model_id: str, model_params: dict) -> Iterator[LlmEvent]` — deltas in order, exactly one `LlmDone` last (mirrors a provider chunk stream).
- Produces: `FakeLlm(replies: list[tuple[list[str], LlmResult]] | None = None)` — pops one scripted reply per call; when `replies` is exhausted or None, the default reply echoes the last user message: deltas `["fake: " + content split in two]`, result `content` joined, tokens estimated `max(1, len(text) // 4)`, `cost 0.0`. Records every call's `(messages, model_id, model_params)` in `.calls` for assertions.
- Produces: `llm_from_env() -> LlmClient` — reads `LLM_PROVIDER` (only `"fake"` exists; unknown values raise).

- [ ] **Step 1: Failing tests** — scripted reply streams deltas then done; messages/model params recorded; default echo reply shape; `llm_from_env()` default is fake, unknown provider raises.
- [ ] **Step 2: RED**; **Step 3: Implement**; **Step 4: GREEN**; **Step 5: Commit** — `feat(worker): llm client abstraction with scripted fake`

### Task 3: engine message building — params rendering + history rebuild

**Files:**
- Create: `worker/src/worker/engine.py`
- Test: `worker/tests/test_engine_messages.py`

**Interfaces:**
- Consumes: `AgentSpec` (shared.spec), `RunLog`/`ExecutionRun` (shared.models).
- Produces: `render_user_message(content: str, params: dict, spec: AgentSpec) -> str` — the docs/02 §4.2 block, EXACT format:
  ```
  <输入参数>
  - project_id（项目 ID，来自项目管理系统，形如 PRJ-1042）: PRJ-1042
  - include_archived（是否包含已归档项目）: true
  </输入参数>

  分析这个项目的进度并生成概要
  ```
  declared order; default filled when absent; no-default-and-absent line omitted entirely; no declared params → no block at all, just `content`. Python values render `str(v)` except booleans lowercase (`true`/`false`).
- Produces: `session_messages(engine, session_id: str, spec: AgentSpec) -> list[dict]` — prior runs of the session in `created_at` order, each contributing `{"role":"user","content": render_user_message(...)}` (from that run's `run_started` payload) and `{"role":"assistant","content": llm_result payload's content}` (from that run's LAST `llm_result`); runs without either are skipped; does NOT include the current run (the engine adds its own user message).

- [ ] **Step 1: Failing tests** — rendering matches the doc's exact block (Review Focus 4); defaults filled / absent-omitted / empty-spec-no-block cases; boolean renders `false`; history: two prior runs (one finished with exchange, one `run_failed` after `llm_result` — its assistant message still counts) → messages in order, current run's rows present in DB but excluded.
- [ ] **Step 2: RED**; **Step 3: Implement**; **Step 4: GREEN**; **Step 5: Commit** — `feat(worker): engine message building from spec and history`

### Task 4: engine loop — one run's full sequence with streaming

**Files:**
- Modify: `worker/src/worker/engine.py`
- Test: `worker/tests/test_engine_loop.py`

**Interfaces:**
- Consumes: Task 2's `LlmClient`/events, Task 3's builders, Task 1's `public_event`.
- Produces: `execute(engine, dispatch: dict, llm: LlmClient, cancelled: threading.Event) -> Iterator[dict]` — generator yielding items in write order:
  - `{"kind": "row", "type": str, "seq": int, "action_id": str | None, "payload": dict}` — durable log row
  - `{"kind": "stream", "type": "message_started" | "message_delta", "data": {"run_id": str} | {"run_id": str, "text": str}}`
  
  Flow: epoch fence (`run.epoch == dispatch["epoch"]` else yields nothing) → terminal-tail check (no-op) → `run_started` row if missing (payload like the stub's) → open-call resume (Task 5) or fresh: new `action_id`; `llm_call` row `{"messages": [...]}` (system prompt from `spec.instructions` first) → `message_started` → per `LlmDelta` a `message_delta` (cancel Event checked between chunks → abort stream, write `run_finished {"status": "cancelled", "result": None}`, stop) → `llm_result` row `{"content", "input_tokens", "output_tokens", "cost", "duration_ms"}` → `tool_calls` non-empty → `run_failed {"error": "..."}`; else `run_finished {"status": "succeeded", "result": content}`.
  Engine start also checks the run row's `cancel_requested` (pre-dispatch cancel → straight to terminal after `run_started`).

- [ ] **Step 1: Failing tests** (drive the generator against the test DB, collect items):
  - fresh run: items are exactly `run_started(row), llm_call(row), message_started(stream), message_delta ×N, llm_result(row), run_finished(row)`; seqs 1..4 monotonic; `llm_call.action_id == llm_result.action_id`, `message_delta.text` concatenates to the reply content; `run_finished.result == "fake: hello"`-style content; `messages[0]` is the system prompt (Review Focus 1 source).
  - scripted multi-chunk reply (2 deltas) → 2 `message_delta` items.
  - cancel Event pre-set → `run_started` then `run_finished {"status": "cancelled"}`, no llm rows; cancel set mid-stream (a `LlmDelta` stream whose generator sets the Event on first chunk) → aborted: `message_started`, one `message_delta`, then `run_finished cancelled`, no `llm_result`.
  - `tool_calls` reply → `run_failed`.
  - epoch mismatch → no items.
- [ ] **Step 2: RED**; **Step 3: Implement**; **Step 4: GREEN**; **Step 5: Commit** — `feat(worker): react engine loop with streamed increments`

### Task 5: engine resume + worker client wiring (stub executor deleted)

**Files:**
- Modify: `worker/src/worker/engine.py` (resume branch), `worker/src/worker/client.py` (drive engine, wire frames, cancel events)
- Delete: `worker/src/worker/executor.py`, `worker/tests/test_executor.py`
- Test: `worker/tests/test_engine_resume.py`, extend `worker/tests/test_client.py`

**Interfaces:**
- Consumes: Task 4's `execute`; `public_event`; `shared.wire.encode`.
- Produces: resume branch — tail has an open `llm_call` (its `action_id` in `open_actions` of the run's entries): the retry writes a NEW `llm_call` row (new seq, SAME action_id, messages copied from the logged one — not rebuilt), then streams as fresh; a `run_started`-only tail continues from the call.
- Produces: `client._run_one(dispatch)` rewrite — iterate `execute(db.engine, dispatch, llm, event)`: `row` item → insert `RunLog` row, then `send(encode(entry_type, public_event(entry)))`; `stream` item → `send(encode(item["type"], item["data"]))` immediately (no row). The persistent link's `cancel` frame sets `client.cancel_events[run_id]` (a `threading.Event`); `_run_one` registers/consumes its Event.

- [ ] **Step 1: Failing tests**:
  - resume: DB pre-seeded with `run_started` + open `llm_call` (seq 2, action_id A) → generator yields NEW `llm_call` (seq 3, action_id A, same messages), then stream + `llm_result` + `run_finished`; `open_actions` of the final log is empty (Review Focus 3).
  - terminal tail → no items (existing behavior kept).
  - client wiring: `_run_one` against a fake manager socket — rows land in DB before their frames ride the wire; `message_delta` frame sent with no seq in data.
- [ ] **Step 2: RED**; **Step 3: Implement + delete stub**; **Step 4: GREEN + full worker suite**; **Step 5: Commit** — `feat(worker): engine resume and client wiring replace the stub`

### Task 6: manager — stream forwarding, accumulation buffer, subscribe catch-up

**Files:**
- Modify: `manager/src/manager/internal.py`, `manager/src/manager/streaming.py`
- Test: `manager/tests/test_streaming.py` (extend)

**Interfaces:**
- Consumes: `STREAM_EVENT_TYPES`, `public_event`.
- Produces: `internal.message_buffers: dict[str, str]` (run_id → accumulated text); `_handle_connection`: names in `STREAM_EVENT_TYPES` → `message_started` resets `message_buffers[run_id] = ""`, `message_delta` appends `data["text"]`, both forwarded to subscribers (no `session_runs` projection change); terminal event or run-conn close pops the buffer.
- Produces: `streaming.replay_events` (manager/runs.py) wraps rows through `public_event` before yielding (Review Focus 1, replay path).
- Produces: `streaming.stream` — after replay, before the live loop: if `session_runs[sid]` names an active run and `message_buffers` holds it, yield `_frame({"type":"message_started",...})` + `_frame({"type":"message_delta","text": buffer})` (docs/05 §4.2 step 3; Review Focus 2). Snapshot read AFTER subscribe — a one-delta duplicate window is accepted and ledgered, loss is not.
- Cursor audit step: verify SSE `id:` only ever marks persisted frames and SDK reconnect semantics hold with stream frames interleaved; resolve or close the unit-5 ledger item about cursor formats.

- [ ] **Step 1: Failing tests** (RunLink-based, as in unit 5): run conn sends `message_started` + two `message_delta` → SSE subscriber receives them in order, no `id:` line, state frame still periodic; buffer cleared after `run_finished`; replay of a finished run with `llm_call` rows emits NO `messages` field; subscribe AFTER `message_started`+`message_delta` (slow stream, subscribe in between) → catch-up `message_started` + one delta with accumulated text, then live frames continue.
- [ ] **Step 2: RED**; **Step 3: Implement**; **Step 4: GREEN + full manager suite**; **Step 5: Commit** — `feat(manager): forward stream events with subscribe catch-up`

### Task 7: SDKs handle stream frames + manager e2e with the real engine

**Files:**
- Modify: `sdk/python/src/zszc/agentops/client.py`, `sdk/typescript/src/client.ts`
- Test: `sdk/python/tests/test_stream.py`, `sdk/typescript/test/stream.test.ts`, `manager/tests/test_sdk_e2e.py`

**Interfaces:**
- Produces (both SDKs): in `stream()`, a non-state frame WITHOUT `seq` (or `data.seq` absent) yields immediately as `Event(type, run_id, None, data)` — no watermark update, no cursor move (Review Focus 5); frames WITH seq behave as before.
- `manager/tests/test_sdk_e2e.py`: `serve_dispatched_run` now drives the REAL engine (`worker.engine.execute` with a scripted `FakeLlm` of 2 chunks) — the SDK sees `state, run_started, llm_call, message_started, message_delta, message_delta, llm_result, run_finished` live, in order; `run.wait()` returns the reply content; a second subscribe with cursor gets replay WITHOUT `messages` on `llm_call`.

- [ ] **Step 1: Failing tests** — python: stream-frame passthrough + cursor unmoved (fake SSE script: state, persisted, message_delta, then drop → reconnect query carries the persisted cursor); TS mirror; e2e order assertion.
- [ ] **Step 2: RED**; **Step 3: Implement**; **Step 4: GREEN (pytest + node --test)**; **Step 5: Commit** — `feat(sdk): pass stream events through without moving the cursor`

### Task 8: deploy verify, demos, docs, final review

**Files:**
- Modify: `demo/python/conversation.py`, `demo/typescript/conversation.ts`, `demo/README.md`, `docs/04-orchestration.md`, `docs/11-roadmap.md`

- [ ] **Step 1: Deploy verify** — rebuild `worker` image (`cd deploy && docker compose up -d --build worker`); run both conversation demos: output shows streamed deltas then `result: fake: ...`; `sdk/typescript/scripts/smoke.ts` still green (its expected output string updated to the fake reply).
- [ ] **Step 2: Demos** — conversation demos print deltas as they arrive (not just types); `demo/README.md`'s stub line updated to the fake-LLM shape.
- [ ] **Step 3: Docs** — `docs/04` §3 rewritten to current state (one hand-written engine; unified compilation re-evaluated with DAG, unit 20); `docs/11` marks unit 7 done, next unit 8.
- [ ] **Step 4: Full suites + ruff + npm test 全绿；fresh-context review subagent（requesting-code-review），按 AGENTS 协作规范整改。**
