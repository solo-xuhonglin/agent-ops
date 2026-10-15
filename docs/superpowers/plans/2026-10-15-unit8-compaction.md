# Unit 8: Context Compaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Engine-side context compaction: threshold-triggered LLM summary of the oldest conversation history, one `context_compacted` log row (full summary persisted, never external), and rebuild applying the summary as the history's new starting point.

**Architecture:** Before building the run's LLM request, the engine estimates the conversation's tokens; at or above the spec's `context.token_limit` it asks the LLM to summarize everything except the most recent exchange, writes `context_compacted` first, and sends `[system] + summary + kept tail + user`. `session_messages` treats the row as a restart marker: everything before it is replaced by the summary. The compaction LLM call emits no stream frames (it is engine behavior, not a chat message) and its tokens/cost land in the row. A compaction row already in the current run's log is reused on re-dispatch instead of re-summarizing.

**Tech Stack:** Python only; no new dependencies. Token counts are estimates (`len(content) // 4` per message, minimum 1) — real tokenization arrives with a real provider.

**Spec:** docs/05-execution-and-streaming.md §8 (who gets compacted, thresholds, method, the log row, rebuild semantics), §2 (`context_compacted` is the only unpaired type), docs/02-sdk-contract.md §6.5 (external event carries lengths and range, never the summary).

## Global Constraints

- Only the conversation history is compacted; the System Prompt is rendered fresh from the snapshot every run and never enters compaction (docs/05 §8.1).
- `window_ratio` is NOT enforced in v1 — it needs a model_id→window-size mapping that only a real provider brings; only `token_limit` triggers. Recorded in roadmap 待定项.
- The compaction LLM call emits no `message_started`/`message_delta` frames; its usage lands in the `context_compacted` payload (`input_tokens`, `output_tokens`, `cost`, `duration_ms`).
- Log payload: `{"summary", "before_tokens", "after_tokens", "compacted_messages", "input_tokens", "output_tokens", "cost", "duration_ms"}`. External projection strips `summary` only.
- Kept tail = the most recent exchange (last user+assistant pair) of the rebuilt history.
- Re-dispatch idempotency: a `context_compacted` row in the current run's rows short-circuits re-summarizing (reuse its summary).
- Summary rides the model context as `{"role": "user", "content": "<历史摘要>\n" + summary}` ahead of the kept tail.
- Comments/logs English; tests only touch the `*_test` DB; PIPESTATUS-guarded verifications.

## Review Focus

1. **Summary never leaves the platform** — external `context_compacted` (live wire entry and SSE replay) carries lengths/range/tokens but no `summary`. Pinned in Task 1 (projection unit) and Task 3 (replay path reuses the same shared projection — assert via the existing manager replay test shape, one added case).
2. **Threshold counts the conversation, not the request** — history + current user message, System Prompt excluded. Pinned in Task 2 (a spec whose system prompt alone is huge does NOT trigger).
3. **Crash between compaction row and llm_call** — re-dispatch reuses the logged summary, exactly one summary LLM call total. Pinned in Task 3.
4. **Rebuild restarts at the marker** — messages from runs/logs before `context_compacted` vanish behind the summary; later runs still appear. Pinned in Task 3.
5. **Compaction cost is recorded** — the row carries the summary call's tokens/cost/duration (not the chat call's). Pinned in Task 2.

---

### Task 1: shared — projection strips the summary

**Files:**
- Modify: `shared/src/shared/events.py` (`_PRIVATE_FIELDS`)
- Test: `shared/tests/test_events.py` (extend)

**Interfaces:**
- Produces: `public_event` on a `context_compacted` entry strips `summary`, keeps every other field (Review Focus 1).

- [ ] **Step 1: Failing test** — `context_compacted` entry with `summary` + lengths fields → projected without `summary`, with `before_tokens`/`after_tokens`/`compacted_messages`/tokens kept.
- [ ] **Step 2: RED** → **Step 3: add `"context_compacted": {"summary"}` to `_PRIVATE_FIELDS`** → **Step 4: GREEN + full shared suite** → **Step 5: Commit** — `feat(shared): strip compaction summaries from external events`

### Task 2: engine — threshold, summary call, row, message assembly

**Files:**
- Modify: `worker/src/worker/engine.py`
- Test: `worker/tests/test_compaction.py`

**Interfaces:**
- Produces: `estimate_tokens(messages: list[dict]) -> int` (sum of `max(1, len(content)//4)`, role ignored).
- Produces: inside `execute` — after run_started/cancel checks and BEFORE the fresh llm_call branch: `context = history + [user message]`; if `estimate_tokens(context) >= spec.context.token_limit` and no `context_compacted` row exists in the current run's rows → summarize: `llm.stream([{role user, "请把以下对话摘成一段…" + oldest messages}], ...)` drained WITHOUT stream frames; keep the last exchange of `history`; write row `context_compacted` with the full payload (Review Focus 5); the chat messages become `[system] + [summary user message] + kept + [user]`. The summary call checks `cancelled` between chunks → abort to `run_finished cancelled` like the chat call.
- Consumes: `LlmClient` (Task 2 unit 7), `FakeLlm` scripted replies: reply #1 = summary, reply #2 = chat answer.

- [ ] **Step 1: Failing tests** (drive `execute` with a spec whose `context.token_limit` is tiny; seed a long prior history via `session_messages` fixtures):
  - over-threshold run: items are `run_started, context_compacted, llm_call, message_started?, …, llm_result, run_finished` — compaction row BEFORE llm_call; `llm_call` messages = `[system, summary-user, kept-user, kept-assistant, current-user]`; row payload carries the scripted summary text and the summary call's token numbers; `FakeLlm.calls[0]` is the summary request (its last user message contains 请摘要 marker and the oldest text), `calls[1]` the chat.
  - under-threshold run (default token_limit): no `context_compacted` row.
  - huge system prompt alone (history empty, token_limit small): NO compaction (Review Focus 2).
  - no stream frames between `context_compacted` and `llm_call` rows (compaction is silent).
- [ ] **Step 2: RED** → **Step 3: implement** → **Step 4: GREEN + worker suite** → **Step 5: Commit** — `feat(worker): threshold-triggered context compaction`

### Task 3: engine — rebuild applies the marker; re-dispatch reuses the row

**Files:**
- Modify: `worker/src/worker/engine.py` (`session_messages`; execute's reuse branch)
- Test: `worker/tests/test_compaction.py` (extend)

**Interfaces:**
- Produces: `session_messages(...)` — a `context_compacted` row in ANY run's log (scanned per run, in order) resets the accumulated messages to `[{"role": "user", "content": "<历史摘要>\n" + row.summary}]`; runs after it still contribute (Review Focus 4).
- Produces: `execute` — if the CURRENT run's rows contain `context_compacted`, reuse its summary (skip the summary LLM call) and continue to the chat call.
- Manager replay pin: extend one `manager/tests/test_streaming.py` case — a `context_compacted` row written via `RunLink.event` replays WITHOUT `summary` (Review Focus 1, replay path).

- [ ] **Step 1: Failing tests**:
  - rebuild: three prior runs; the middle one carries `context_compacted` → messages = [summary-user, run-3 user, run-3 assistant]; run-1's text absent.
  - reuse: pre-seed current run with `run_started + context_compacted(summary=S)` rows → `execute` yields llm_call whose messages embed S, NO new `context_compacted` row, and `FakeLlm` saw exactly ONE call (Review Focus 3).
  - replay pin in manager tests.
- [ ] **Step 2: RED** → **Step 3: implement** → **Step 4: GREEN (worker + manager suites)** → **Step 5: Commit** — `feat(worker): rebuild from the compaction marker, reuse on re-dispatch`

### Task 4: docs, deploy sanity, final review

- [ ] **Step 1: docs/11** — unit 8 done; add 待定项 18: `window_ratio` 何时生效（需要 model_id→窗口大小映射，随真实 provider 落地；当前只有 `token_limit` 触发）。
- [ ] **Step 2: full suites + ruff + node --test 全绿；deploy 冒烟（smoke.ts，默认阈值不触发压缩，行为不变）。**
- [ ] **Step 3: fresh-context review subagent（requesting-code-review），按 AGENTS 协作规范整改。**
