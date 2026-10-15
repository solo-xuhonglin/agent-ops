# SDK 消息聚合与 AG-UI 词汇对齐 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消息流补三段式终结（`TEXT_MESSAGE_END`）、SDK 聚合成快照式 `message` 事件消灭消费端样板；对外投影的 UI 路径词汇对齐 AG-UI；A2A/AG-UI 边缘 adapter 记入 roadmap 待定项。

**Architecture:** 线协议新增 `TEXT_MESSAGE_END` 流式帧（不落库、无 seq）；`message_started`/`message_delta` 更名为 `TEXT_MESSAGE_START`/`TEXT_MESSAGE_CONTENT`（字段 `text`→`delta`）；持久事件投影层把 `run_started/run_finished/run_failed` 改名为 `RUN_STARTED/RUN_FINISHED/RUN_ERROR`（内部日志类型不动——投影与日志分离的架构回报）。SDK 双侧在流迭代器内部消化三段式，对外只产一种 `message` 事件：`{text: 累积快照, delta: 本帧增量, done: bool}`，并保证每个成功 run 恰好一条 `done=true` 封口（END 到达用缓冲封口；错过 END 由 `RUN_FINISHED` 的 result 封口）。

**Tech Stack:** Python（shared/worker/manager/sdk）、TypeScript（sdk 镜像）；SSE 线协议、进程内 FakeApi/FakeWorker 测试。

**Spec:** 本计划自含。设计依据：2026-10-15 调研（AG-UI 1.0 事件词汇、A2A/OpenAI 流式形状）与对话拍板——L2 消息终结+聚合、L1 仅对齐 UI 路径词汇（`RUN_*`/`TEXT_MESSAGE_*`），审计向事件（tool_call 等）保留平台命名。

## Design Decisions（已拍板，不再讨论）

1. **对齐范围**：只对齐 AG-UI 有直接对应物的 UI 路径词汇——`RUN_STARTED`/`RUN_FINISHED`/`RUN_ERROR`/`TEXT_MESSAGE_START`/`TEXT_MESSAGE_CONTENT`/`TEXT_MESSAGE_END`。审计向持久事件（`tool_call`/`tool_result`/`llm_call`/`llm_result`/`agent_call`/`agent_result`/`branch_taken`/`context_compacted`）保留 snake_case 平台命名（AG-UI 的 RAW/CUSTOM 并存精神）。
2. **改名只发生在投影层**：`public_event()` 输出改名；`EventType`（内部日志类型）、落库、引擎内部一律不动。
3. **payload 字段保持 snake_case**（`run_id`/`seq`/`action_id`/`delta`）；AG-UI 对齐的是事件词汇，不是字段风格。
4. **`TEXT_MESSAGE_END` 是流式帧**：与 START/CONTENT 同类——不落库、无 seq、不重放；`STREAM_EVENT_TYPES` 三成员。
5. **每 run 单消息**：不引入独立 `messageId`，帧的 `data.run_id` 即配对键（v1 引擎每 run 恰好一条终答消息）。
6. **SDK 聚合事件形状**：`Event{type: "message", run_id, seq: None, data: {text: 累积快照, delta: 本帧增量, done: bool}}`。`TEXT_MESSAGE_START`/`END` 帧的 `delta` 为空串。终端渲染用 `delta`，UI 覆盖渲染用 `text`。
7. **封口规则**：END 到达 → 封口 `text=缓冲, done=true`；`RUN_FINISHED` 到达且该 run 未封口 → 封口 `text=result`（result 为字符串）或 `text=缓冲`（result 非字符串，如 output_schema 对象），再透传 `RUN_FINISHED`。`RUN_ERROR` 不产 message 封口。每个 run 至多一条 done=true。
8. **SDK 用户侧不再出现** `TEXT_MESSAGE_*`（消化在内部）与小写旧名 `message_started`/`message_delta`/`run_started`/`run_finished`/`run_failed`。
9. **L3 不实现**：A2A/AG-UI 边缘 adapter 写入 docs/11 roadmap 待定项。

## Global Constraints

- 代码注释英文；控制台输出/UI 文案中文；提交信息英文；master 直提，每 task 一 commit。
- 测试只连进程内 fake（FakeApi/FakeWorker/uvicorn live_server）与 *_test 库。
- 内部运行日志（EventType、落库、配对不变式、open_actions）零改动；wire 协议（manager↔worker）零改动。
- 提交前全仓测试与静态检查全绿（pytest ×3、node --test、tsc、ruff）。

## Review Focus

1. **每 run 恰好一条 done=true 封口**：END 路径（text=缓冲）与错过 END 路径（text=result）都只封一次。钉：Task 3/4 聚合测试双路径断言。
2. **断线重连后的封口正确性**：replay 只有持久事件，SDK 缓冲为空 → `RUN_FINISHED` 封口 text=result（非空串）。钉：Task 3/4 重连用例在重连 script 无 TEXT_MESSAGE_* 的情况下产出 done=true 且 text=result。
3. **RUN_ERROR 不封口、不吞事件**。钉：Task 3/4 失败路径断言。
4. **订阅补发快照继续累积**：manager 补发 START+CONTENT(buffer)，SDK 视角消息继续累积不被清空（START 清缓冲语义对同一 run 的补发帧要幂等）。钉：Task 2 补发断言 + Task 3 聚合测试含"订阅即收到 START+快照 CONTENT"。
5. **改名零残留、内外分层正确**：线上/SDK 断言只见新名；worker 落库断言仍是内部名。钉：Task 1 日志断言 + Task 2 线上断言；Task 5 全仓 grep 旧名。

---

### Task 1: shared + worker —— 流式三段与投影改名

**Files:**
- Modify: `shared/src/shared/events.py`（STREAM_EVENT_TYPES 三成员；public_event 增 `_EXTERNAL_EVENT_NAMES` 映射）
- Modify: `worker/src/worker/engine.py`（流帧 `TEXT_MESSAGE_START`/`TEXT_MESSAGE_CONTENT{delta}`/新增 `TEXT_MESSAGE_END`）
- Test: `worker/tests/test_engine_loop.py`（流式断言）、`worker/tests/test_pump.py`（如有帧名断言）

**Interfaces:**
- Produces（Task 2/3/4 依赖）:
  - `STREAM_EVENT_TYPES = ("TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END")`
  - `public_event(entry) -> {"type": <外部名>, "data": {...}}`：`run_started→RUN_STARTED`、`run_finished→RUN_FINISHED`、`run_failed→RUN_ERROR`，其余类型原名；私有字段剥离照旧。
  - 引擎流帧：`{"kind": "stream", "type": "TEXT_MESSAGE_START", "data": {"run_id": ...}}` → 若干 `TEXT_MESSAGE_CONTENT` `{"delta": ...}` → `{"kind": "stream", "type": "TEXT_MESSAGE_END", "data": {"run_id": ...}}`（END 在 llm_result 落库前、流耗尽后发）。

- [ ] **Step 1: RED —— 改 worker 测试断言为三段式与新名**

  test_engine_loop 的流式用例：断言流帧序列为 `TEXT_MESSAGE_START`、`TEXT_MESSAGE_CONTENT`(delta 拼接=终文)、`TEXT_MESSAGE_END`；落库行 `type` 仍为内部名（`run_started` 等）。test_pump 若断言帧名同步改。

- [ ] **Step 2: 跑 RED**

  Run: `.venv/Scripts/python -m pytest worker/tests -q`
  Expected: 流式相关 FAIL（旧名/缺 END），非流式 PASS。

- [ ] **Step 3: 实现**

  events.py：常量三成员；`_EXTERNAL_EVENT_NAMES = {"run_started": "RUN_STARTED", "run_finished": "RUN_FINISHED", "run_failed": "RUN_ERROR"}`，public_event 先剥私有字段再换名。
  engine.py：主循环流帧改名、`data` 用 `delta` 键；流耗尽（拿到 LlmDone/LlmResult 时）yield END 帧。

- [ ] **Step 4: GREEN + worker 全套**

  Run: `.venv/Scripts/python -m pytest worker/tests -q && .venv/Scripts/python -m ruff check shared worker`
  Expected: 全 PASS。

- [ ] **Step 5: Commit** `feat(worker): three-part text message frames and AG-UI-facing event names`

### Task 2: manager —— 转发、缓冲与补发

**Files:**
- Modify: `manager/src/manager/internal.py`（STREAM 分支：START 置空 / CONTENT 追加 `data["delta"]` / END 直转发）
- Modify: `manager/src/manager/streaming.py`（补发快照改 START+CONTENT{delta: buffer}）
- Test: `manager/tests/test_streaming.py`（Sse 断言）、`manager/tests/test_sdk_e2e.py`（types 列表适配）

**Interfaces:**
- Consumes: Task 1 的帧名与投影名。
- Produces: SSE 线上 `data.type` 为外部名（`RUN_STARTED`/`TEXT_MESSAGE_*`/审计原名）；补发快照形状不变（START+CONTENT）。

- [ ] **Step 1: RED** —— test_streaming/test_sdk_e2e 断言改新名（含补发快照两帧与 `RUN_FINISHED`）；e2e 的 types 序列插入 `TEXT_MESSAGE_END`（线序：...CONTENT, CONTENT, END, llm_result→投影后仍 `llm_result`, RUN_FINISHED）。
- [ ] **Step 2: 跑 RED**（manager/tests FAIL 于旧名/缺 END）。
- [ ] **Step 3: 实现** internal.py 三分支（START 置空、CONTENT `buffer += data["delta"]`、END 转发不动 buffer）与 streaming.py 补发帧名。
- [ ] **Step 4: GREEN**（manager/tests 全 PASS + ruff）。
- [ ] **Step 5: Commit** `feat(manager): forward the three-part message frames under external names`

### Task 3: Python SDK —— 消息聚合

**Files:**
- Modify: `sdk/python/src/zszc/agentops/client.py`（EventStream 聚合：buffer/sealed/pending；Event docstring）
- Test: `sdk/python/tests/test_stream.py`

**Interfaces:**
- Consumes: Task 2 的线上形状。
- Produces（Task 5 依赖）: 用户侧 `Event.type` 集合 = `{"message", "RUN_STARTED", "RUN_FINISHED", "RUN_ERROR", "llm_call", "llm_result", "tool_call", "tool_result", ...}`；`message` 的 `data = {"text": str, "delta": str, "done": bool}`。

- [ ] **Step 1: RED** —— test_stream.py：
  - 消息三段聚合：START+CONTENT(d1)+CONTENT(d2)+END+RUN_FINISHED → 产出 `message{text=d1, delta=d1, done=False}`、`message{text=d1+d2, ...}`、`message{text=d1+d2, delta="", done=True}`（END 封口）、随后 `RUN_FINISHED`。
  - 错过 END 的兜底：无 TEXT_MESSAGE_*、只有 RUN_FINISHED{result: "final"} → 单条 `message{text="final", done=True}` + `RUN_FINISHED`。
  - 部分增量后断线重连：script1 只到 CONTENT，script2 只有 RUN_FINISHED{result} → 封口 text=result。
  - RUN_ERROR 不产 message。
  - 既有用例（水位/游标/静默/重拨）事件名适配，行为断言不变。
- [ ] **Step 2: 跑 RED**。
- [ ] **Step 3: 实现** —— EventStream 增 `self._buffers: dict[str, str]`、`self._sealed: set[str]`、`self._pending: deque[Event]`；`__next__` 先弹 pending；消化 TEXT_MESSAGE_*（START 清该 run 缓冲并产 `message{text:"", delta:"", done:False}`？——不产：START 是元事件，首条 CONTENT 才产 message；实现取简：START 不产出）；RUN_FINISHED 未封口则 pending 封口再 pending 透传。docstring 写清封口规则。
- [ ] **Step 4: GREEN**（sdk/python/tests 全 PASS + ruff）。
- [ ] **Step 5: Commit** `feat(sdk/py): aggregate text-message frames into snapshot message events`

### Task 4: TypeScript SDK —— 镜像

**Files:**
- Modify: `sdk/typescript/src/client.ts`（generator 聚合：buffers/sealed）
- Test: `sdk/typescript/test/stream.test.ts`

**Interfaces:**
- Produces: 镜像 Task 3——`Event.type === "message"`，`data = {text, delta, done}`。

- [ ] **Step 1: RED** —— 镜像 Task 3 四类断言（TS 侧）。
- [ ] **Step 2: 跑 RED**。
- [ ] **Step 3: 实现** —— generator 内局部 `buffers`/`sealed` Map；TEXT_MESSAGE_* 消化；RUN_FINISHED 未封口先 yield 封口再 yield 透传。
- [ ] **Step 4: GREEN**（npm test + tsc build）。
- [ ] **Step 5: Commit** `feat(sdk/ts): mirror the message aggregation`

### Task 5: examples、docs、roadmap 与终审

**Files:**
- Modify: `examples/python/conversation.py`、`examples/typescript/conversation.ts`（删 streamed 样板，改 message 事件渲染 delta、done 收尾）
- Modify: `sdk/typescript/scripts/smoke.ts`（types 断言适配）
- Modify: `sdk/python/README.md`、`sdk/typescript/README.md`（事件词汇说明）
- Modify: `docs/02-sdk-contract.md` §6.5（事件表：AG-UI 词汇区 + 平台词汇区；message 聚合语义）、§5.1（示例循环）
- Modify: `docs/05-execution-and-streaming.md` §3.3/§4.2（三段式流帧、补发）
- Modify: `docs/11-roadmap.md`（待定项：A2A/AG-UI 边缘 adapter）
- Test: 全仓验证 + 本地栈实跑 + fresh 终审

- [ ] **Step 1: examples 重写** —— conversation 循环体变为 `if event.type == "message": print(event.data["delta"], end="") / done 时收尾`，删 streamed 与 START/END 处理；task 示例不动。
- [ ] **Step 2: docs 收口**（上述各节；grep 确认 SDK 用户视角无旧名残留：`message_started`/`message_delta`/`run_started`（小写对外语境））。
- [ ] **Step 3: 全仓验证** —— pytest ×3、node --test、tsc、ruff、examples 重链接。
- [ ] **Step 4: 本地栈实跑** —— 四 examples + smoke（对话式增量实时渲染、done 收尾正确）。
- [ ] **Step 5: fresh reviewer 终审（AGENTS 规范，最多 5 条）**，Important 全修（TDD）。
- [ ] **Step 6: Commit** `docs and examples follow the aggregated message events`
