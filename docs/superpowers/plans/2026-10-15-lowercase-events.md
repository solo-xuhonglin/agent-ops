# 事件词汇收敛（小写三段式）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** SDK 用户面直接暴露小写三段式消息事件（`text_message_start/content/end`），删除 `message` 聚合与 `RUN_*` 大写投影，封口保证改为「SDK 补齐缺失的 `text_message_end`」。

**Architecture:** 全链（wire/SSE/SDK）同名小写 snake_case；`text_message_end` 带完整权威终文（对齐 OpenAI `response.output_text.done`）；外名投影层整体删除，SSE 出口只保留私有字段剥离。spec：`docs/superpowers/specs/2026-10-15-event-vocabulary-design.md`（D1-D5 已拍板）。

**Tech Stack:** Python（shared/worker/manager/sdk）、TypeScript（sdk）；SSE、进程内 fake 测试。

## Global Constraints

- 注释英文、输出中文、提交英文；master 直提，每 task 一 commit。
- 测试只连进程内 fake；wire 与运行日志零变化（内部名从未改过）。
- 提交前全仓绿（pytest ×3、node --test、tsc、ruff）。

## Review Focus

1. **每 run 恰一条 `text_message_end`**：真 END 透传后 `run_finished` 不再合成；错过 END 时恰好合成一条。钉：Task 2/3 双路径断言。
2. **合成 end 的 text 权威性**：result 字符串 > 累积缓冲 > 不合成；`run_failed` 不合成。钉：Task 2/3 用例。
3. **三层同名零投影残留**：`RUN_`/`TEXT_MESSAGE`/SDK 面 `"message"` 全仓（SDK 视角文档与代码）零残留。钉：Task 4 grep + 各测试断言。
4. **replay 场景合成**：重连后只有持久事件 → SDK 合成 end{text=result}。钉：Task 2/3 重连用例。
5. **消费面全小写**：e2e/examples/smoke 断言 `run_finished`/`text_message_*`。钉：Task 1/4。

---

### Task 1: 服务端 —— 帧名小写与投影层删除

**Files:** `shared/src/shared/events.py`、`worker/src/worker/engine.py`、`manager/src/manager/internal.py`、`manager/src/manager/streaming.py`；测试 `worker/tests/test_engine_loop.py`、`test_pump.py`、`test_compaction.py`、`manager/tests/test_streaming.py`、`test_internal.py`、`test_sdk_e2e.py`

**Interfaces（Produces）:** SSE 线上事件名 = `text_message_start` / `text_message_content{delta}` / `text_message_end{}`（流帧，无 seq）+ 持久事件内部名（`run_started` 等，SSE 同名直出）。`STREAM_EVENT_TYPES = ("text_message_start", "text_message_content", "text_message_end")`。`public_event` 只剥私有字段。

- [ ] **Step 1: RED** —— 上述测试断言：`TEXT_MESSAGE_*` → 小写；SSE 上 `RUN_STARTED/RUN_FINISHED/RUN_ERROR` → `run_started/run_finished/run_failed`（wire 断言本就是内部名，不变）。
- [ ] **Step 2: 跑 RED**（worker+manager 套件 FAIL 于命名）。
- [ ] **Step 3: 实现** —— events.py 删 `_EXTERNAL_EVENT_NAMES`/`external_event_name`、常量小写；engine.py 流帧小写；internal.py 删投影 import 与调用（`_forward(session_id, {"type": type_, "data": data})` 直出）；streaming.py 补发帧小写。
- [ ] **Step 4: GREEN**（pytest manager/tests worker/tests + ruff）。
- [ ] **Step 5: Commit** `refactor: lowercase three-part message frames, drop the external-name projection`

### Task 2: Python SDK —— 三段直通与合成封口

**Files:** `sdk/python/src/zszc/agentops/client.py`、`manager/tests/test_sdk_e2e.py`；测试 `sdk/python/tests/test_stream.py`

**Interfaces（Produces）:** SDK `Event.type` 含 `text_message_start/content/end`（直通线帧）、`run_started/run_finished/run_failed`（小写）、审计原名。合成规则：`run_finished` 到达且该 run 未见 `text_message_end` → 先产 `text_message_end{run_id, text}`（result 字符串优先，否则缓冲；皆无则不合成）再透传。

- [ ] **Step 1: RED** —— test_stream.py：聚合用例改三段断言（start/content{delta}/end{text}）；「错过 END 由 run_finished 合成」「真 END 后不再合成」「run_failed 不合成」「replay 合成 text=result」四类用例；既有用例 `RUN_*` → 小写、`message` → 三段。
- [ ] **Step 2: 跑 RED**。
- [ ] **Step 3: 实现** —— EventStream：删 `_sealed`/`_pending` 聚合产 message 的路径，改为 `_buffers`（累积 delta）+ `_ended: set`；三段直通（content 同时累积缓冲；end 标记 `_ended` 并 pop 缓冲）；`run_finished` 未 `_ended` 时 pending 合成再 pending 透传（pending 队列保留）。
- [ ] **Step 4: GREEN**（pytest sdk/python/tests + manager e2e 断言同步小写三段）。
- [ ] **Step 5: Commit** `refactor(sdk/py): expose the three-part message events directly with a synthesized seal`

### Task 3: TypeScript SDK —— 镜像

**Files:** `sdk/typescript/src/client.ts`；测试 `sdk/typescript/test/stream.test.ts`

- [ ] **Step 1: RED** —— 镜像 Task 2 断言集。
- [ ] **Step 2: 跑 RED**。
- [ ] **Step 3: 实现** —— generator：buffers/ended Map；三段直通；run_finished 未 ended 先 yield 合成 end 再 yield 透传。
- [ ] **Step 4: GREEN**（npm test + tsc）。
- [ ] **Step 5: Commit** `refactor(sdk/ts): mirror the direct three-part message events`

### Task 4: examples、docs 与终审

**Files:** `examples/python/conversation.py`、`examples/typescript/conversation.ts`、`sdk/typescript/scripts/smoke.ts`、`sdk/python/README.md`、`sdk/typescript/README.md`、`docs/02-sdk-contract.md`（§5.1/§6.5/§8）、`docs/05-execution-and-streaming.md`（§2 表/§3.3/§4.2）、`docs/06-internal-protocol.md`（§1）

- [ ] **Step 1: examples/smoke** —— conversation 消费 `text_message_content{delta}` 打增量、`text_message_end` 换行（注释指向 end.text 兜底语义）、`run_failed` 错误分支；smoke break 条件 `run_finished`。
- [ ] **Step 2: docs** —— §6.5 重写为小写事件表 + end 带 text + 合成保证 + 与 AG-UI 字面差异声明；§5.1 伪代码；§8 state 行核对；05/06 跟随；README 双侧。全仓 grep：SDK 视角无 `TEXT_MESSAGE`/`RUN_STARTED`/`"message"` 残留。
- [ ] **Step 3: 全仓验证**（pytest ×3、node --test、tsc、ruff、examples 重链接）。
- [ ] **Step 4: 本地栈实跑**（重建 worker/manager + restart nginx；四 examples + smoke）。
- [ ] **Step 5: fresh reviewer 终审（≤5 条）**，Important 全修（TDD）。
- [ ] **Step 6: Commit** `docs and examples follow the lowercase three-part message events`
