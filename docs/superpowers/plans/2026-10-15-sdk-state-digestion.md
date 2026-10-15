# SDK State 帧内部消化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** state 帧在 SDK 内部消化，用户侧事件流只含事件；订阅前置（`stream()` 返回即就绪）+ 补齐静默重连。

**Architecture:** `Session.stream()` 从惰性生成器改为"订阅前置"：调用时建连、消化 manager 连接即推的首帧 state，返回纯事件迭代器。水位去重、游标重连、state 消化全部移进迭代器内部；state 帧兼作心跳输入，超过 idle 阈值无消息即断开重连（带游标幂等重放）。Python 侧单线程惰性迭代（`EventStream` 类），TS 侧 `async stream(): Promise<AsyncGenerator<Event>>` + reader/timeout race。

**Tech Stack:** Python `zszc.agentops`（httpx 流式读，read timeout 即静默检测）；TypeScript `@zszc/agentops`（fetch + `ReadableStream.getReader()` + `Promise.race` idle timer）。

**Spec:** 本计划自含（设计在 2026-10-15 对话中拍板）：方案 B 订阅前置 + 顺带补齐静默检测；线协议零改动，manager 不动。

## Design Decisions（已拍板，不再讨论）

1. **`stream()` 语义**：返回 = 订阅就绪（首帧 state 已消化，`current_run` 已更新）。HTTP 错误（404 等）在调用行抛出，不重连。
2. **Event 形状**：`type` 不再有 `"state"`。`Event` docstring 同步（run_id/seq 仅在流式帧上为 None）。
3. **首帧约定**：manager 每次连接（含重连）首帧必为 state（`streaming.py:36`）。SDK 直接消化，不写"首帧非 state"分支。
4. **静默检测**：Python 用 httpx `Timeout(read=idle_timeout)`，`ReadTimeout` ⊂ `TransportError`，重连路径与传输错误同一条（现有 except 元组顺手收敛为 `httpx.TransportError`，RemoteProtocolError 是其子类）；TS 自定义 `IdleTimeoutError`（http.ts 内部，不进 index.ts 导出），与 `TypeError && /fetch failed|terminated/` 同走重连。
5. **idle 阈值**：client 级参数 `idle_timeout: float = 30.0` / `idleTimeoutMs = 30_000`（manager `STATE_INTERVAL` 默认 10s，3 倍余量）。
6. **生命周期**：Python `EventStream.close()` 透传底层生成器 close；TS 生成器 `.return()` 由调用方触发（与现状一致）。
7. **TS `Http.events` 删除**，由 `openEvents` 替代（不留死代码）；分帧解析逻辑单份移植。

## Global Constraints

- 代码注释英文；控制台输出/UI 文案中文；提交信息英文；master 直提，每 task 一 commit。
- 测试只连进程内 FakeApi，绝不连部署栈或主库。
- 双侧镜像：方法名/参数名一一对应，`snake_case` → `camelCase`。
- 线协议与 manager 零改动。
- 提交前全仓测试与静态检查全绿（pytest ×3 套件、`node --test`、tsc、ruff）。

## Review Focus

1. **订阅就绪语义**：`stream()` 返回时订阅请求必须已上线（否则"先订阅后 submit 不丢增量"是空话）。钉：Task 1/2 各一断言 `len(fake.requests) == 1` 在 `stream()` 返回后立即检查；Task 3 本地栈实跑 examples 验证不漏增量。
2. **静默不误杀**：帧持续到达（间隔 < idle 阈值）时绝不重连。钉：Task 1 `test_frames_flowing_never_reconnects`（帧间 0.05s delay、idle 0.3s、单请求）；Task 2 多帧顺序产出 + 单请求断言。
3. **静默触发重连带游标**：连接活着但无帧 → 重连且 replay 从游标起。钉：Task 1 `test_silence_reconnects_with_cursor`；Task 2 `idle silence reconnects with the cursor`。
4. **重连后 state 首帧被消化**：重连 script 以 state 开头，产出不含 state。钉：Task 1/2 重连用例的 script 都以 state 开头，产出断言无 state。
5. **HTTP 错误调用行抛、不重连**：语义从"首次 next 抛"移到"stream() 调用抛"。钉：Task 1/2 改写现有 404 用例。

---

### Task 1: Python SDK —— EventStream 与订阅前置

**Files:**
- Modify: `sdk/python/src/zszc/agentops/http.py`（`events` 加 `read_timeout`）
- Modify: `sdk/python/src/zszc/agentops/client.py`（`EventStream` 新类、`Session.stream`/`Client`/`create_client`/`Event` 改形）
- Test: `sdk/python/tests/test_stream.py`（全面改写）

**Interfaces:**
- Consumes: 现有 `Http.events(path, params)`、`Event` dataclass。
- Produces（Task 3 与 TS 镜像依赖）:
  - `create_client(key, *, base_url, poll_interval=0.5, reconnect_delay=1.0, idle_timeout=30.0)`
  - `Session.stream(*, from_run: str | None = None, from_seq: int = 0) -> EventStream`（非生成器；返回即就绪）
  - `EventStream`: `__iter__`/`__next__`/`close()`；只产出非 state 事件
  - `Http.events(self, path: str, params: dict, *, read_timeout: float) -> Iterator[tuple[str | None, dict]]`（内部 `httpx.Timeout(self._timeout, read=read_timeout)`）

- [ ] **Step 1: 改写 `sdk/python/tests/test_stream.py` 为 RED**

  测试文件整体改写，新断言集：
  - `test_stream_returns_when_subscription_is_live`：script `[(0, state_frame(RUN_ID)), (0, event_frame(1, "run_started", content="hi"))]`，hold=0.5；`stream = session.stream()` 返回后立即 `assert len(fake.requests) == 1`（就绪语义，Review Focus 1）；`next(stream)` 得 `run_started`。
  - `test_state_frames_stay_internal`：script `[(0, state_frame(RUN_ID)), (0, event_frame(1, "run_started", content="hi")), (0, state_frame(None)), (0, event_frame(2, "run_finished", status="succeeded", result=None))]`，hold=0.5；产出恰为 `[("run_started", 1), ("run_finished", 2)]`；`stream.close()` 后 `session.current_run is None`（state 消化进了 current_run，Review Focus 4 同源）。
  - `test_events_yield_typed_frames_and_suppress_duplicates`：保留现有断言，script 首部补 `(0, state_frame(RUN_ID))`。
  - `test_frames_split_across_chunks_reassemble`：保留；script 补 state 前缀（拆帧只拆事件帧）。
  - `test_reconnect_resumes_from_cursor_and_suppresses_replay`：保留断言（seqs `[1,2,3]`、第二次请求带 `from_run/from_seq`）；两个 script 各补 state 前缀（Review Focus 4）。
  - `test_http_error_raises_without_retry`：改为 `with pytest.raises(NotFoundError): session.stream()`；仍断言单请求（Review Focus 5）。
  - `test_explicit_cursor_reaches_the_wire`：保留，script 补 state 前缀。
  - `test_stream_frames_yield_without_moving_the_cursor`：保留游标断言，`seen` 断言改为不含 state 行 `[("run_started", 1), ("message_delta", None)]`（删除第一帧 state 的产出）。
  - 新增 `test_frames_flowing_never_reconnects`：script `[(0, state), (0.05, e1), (0.05, e2), (0.05, e3)]`，hold=0.5，client `idle_timeout=0.3, reconnect_delay=0.05`；三帧顺序产出且 `len(fake.requests) == 1`（Review Focus 2）。
  - 新增 `test_silence_reconnects_with_cursor`：script1 `[(0, state), (0, event_frame(1, ...))]`，script2 `[(0, state), (0, event_frame(2, ...))]`，hold=0.5（连接活着但静默），client `idle_timeout=0.2, reconnect_delay=0.05`；`next(stream)` 两次得 seq 1、2；`fake.requests[1]["query"] == {"from_run": RUN_ID, "from_seq": "1"}`（Review Focus 3）。
  - fixture：`create_client(..., reconnect_delay=0.05)`，各静默用例自传 `idle_timeout`。

- [ ] **Step 2: 跑测试确认 RED**

  Run: `.venv/Scripts/python -m pytest sdk/python/tests/test_stream.py -q`
  Expected: FAIL（`EventStream` 不存在 / state 仍被产出 / 就绪断言失败）

- [ ] **Step 3: 实现 `EventStream` 与改形**

  - `http.py`：`events` 签名加 `*, read_timeout: float`，构造 `httpx.Timeout(self._timeout, read=read_timeout)`（原 `read=None` 删除）。
  - `client.py`：
    - `Client.__init__`/`create_client` 加 `idle_timeout: float = 30.0`，存 `self.idle_timeout`。
    - `Event` docstring 改为 `"""One stream event; run_id/seq are None on stream frames."""`。
    - `EventStream` 类（client.py 内，与 Session/Run 并列）：`__init__(session, cursor)` 里调 `_open()`；`_open()` 用 `session._client._http.events(path, params, read_timeout=session._client.idle_timeout)` 建 `next(frames)` 拿首帧（必为 state）消化进 `session.current_run` 后返回生成器；`__next__` 内 `while True`：`next(self._frames)`，捕获 `httpx.TransportError` → `time.sleep(reconnect_delay)` → `self._frames = self._open()` → continue；state 帧 → 更新 current_run → continue；无 `seq` → 返回流式 Event；`seq <= watermark` → continue；否则更新水位/游标并返回 Event。`close()` 透传 `self._frames.close()`。
    - `Session.stream` 改普通方法：构造 cursor 后 `return EventStream(self, cursor)`；docstring 写明"returns once the subscription is live; state frames and reconnects stay internal"。

- [ ] **Step 4: 跑测试确认 GREEN，全套件回归**

  Run: `.venv/Scripts/python -m pytest sdk/python/tests -q && .venv/Scripts/python -m ruff check sdk/python`
  Expected: 全 PASS（fake.py、test_handles/test_wait 不受影响）

- [ ] **Step 5: Commit**

  ```bash
  git add sdk/python
  git commit -m "sdk(py): subscribe-first EventStream that digests state frames and reconnects on silence"
  ```

### Task 2: TypeScript SDK —— 镜像

**Files:**
- Modify: `sdk/typescript/src/http.ts`（删 `events`，加 `openEvents`/`FrameSource`/`IdleTimeoutError`）
- Modify: `sdk/typescript/src/client.ts`（`ClientOptions`、`Session.stream` async 化、`Event` 注释）
- Test: `sdk/typescript/test/stream.test.ts`（镜像改写）

**Interfaces:**
- Consumes: Task 1 定稿的语义（就绪、state 消化、idle、游标重连）。
- Produces（Task 3 依赖）:
  - `createClient(key, { baseUrl, pollIntervalMs?, reconnectDelayMs?, idleTimeoutMs? })`（默认 500/1000/30000）
  - `Session.stream(options?: { fromRun?: string; fromSeq?: number }): Promise<AsyncGenerator<Event>>`（await 完成 = 就绪）
  - `Http.openEvents(path: string, params: Record<string, string>, idleTimeoutMs: number): Promise<FrameSource>`；`FrameSource { next(): Promise<Frame>; close(): void }`；`class IdleTimeoutError extends Error`（http.ts export，index.ts 不导出）

- [ ] **Step 1: 改写 `sdk/typescript/test/stream.test.ts` 为 RED**

  镜像 Task 1 断言集：
  - `streamOf` 改为 `async function streamOf(baseUrl: string, opts?)` 返回 `await ... .stream()`；`take` 接受 `AsyncGenerator<Event>` 不变。
  - "stream resolves once the subscription is live"：`fake.routeRaw(EVENTS, [[stateFrame(RUN_ID), eventFrame(1, ...)]], { hold: 0.3 })`；`const stream = await session.stream();` 后立即 `assert.equal(fake.requests.length, 1)`；首个产出为 run_started。
  - "state frames stay internal"：`[stateFrame(RUN_ID), eventFrame(1), stateFrame(null), eventFrame(2)]` → 产出两事件；`stream.return()` 后 `session.currentRun === null`。
  - duplicates / split-across-chunks / explicit cursor：保留，script 补 stateFrame 前缀。
  - reconnect：保留 seq/游标断言，两 script 补 stateFrame 前缀。
  - "http errors raise without retry"：`await assert.rejects(() => session.stream(), NotFoundError)`；单请求。
  - "stream frames yield without moving the cursor"：`seen` 断言去掉 state 行。
  - 新增 "frames flowing never reconnect"：`[[stateFrame, e1, e2, e3]]`（顺序写出一块流），idle 200ms；三事件顺序产出 + 单请求。
  - 新增 "idle silence reconnects with the cursor"：script1 `[stateFrame, e1]` hold 0.5（连接活着但静默），script2 `[stateFrame, e2]`；client `{ reconnectDelayMs: 20, idleTimeoutMs: 200 }`；take 2 → seq 1、2；`fake.requests[1].query` 带 `from_run/from_seq: "1"`。
  - 注意：TS FakeApi `routeRaw` 无逐块 delay，"flowing" 用例靠"帧持续到达不断流"覆盖不误杀；真正带间隔的计时覆盖在 Python 侧（Task 1）。

- [ ] **Step 2: 跑测试确认 RED**

  Run: `cd sdk/typescript && npm test 2>&1 | tail -20`
  Expected: FAIL（stream 返回 Promise / state 仍产出）

- [ ] **Step 3: 实现**

  - `http.ts`：删 `events`；`openEvents` 实现 —— `fetch(url, { headers })`，`!ok` 时 `fromResponse` 抛（保持现语义）；`reader = (response.body as ReadableStream<Uint8Array>).getReader()`；行解析状态机从原 `events` 移植（`id: `/`data: `/空行 → Frame）。`next()` 算法：循环取完整帧；缓冲区无完整帧时 `Promise.race([reader.read(), idlePromise])`，idle 定时器每次 read 前重设，超时 reject `IdleTimeoutError`；read 到字节即重置（帧跨块凑整期间不算静默）。`close()` 调 `void reader.cancel()`。
  - `client.ts`：`ClientOptions` 加 `idleTimeoutMs?`（构造器默认 `30_000`，与 poll/reconnect 同法存 readonly）；`Event` 注释删 "state"；`Session.stream` 改 async 方法：`await openEvents(...)` → `await` 首帧（必为 state）消化进 `currentRun` → 返回闭包 AsyncGenerator：循环 `link.next()`，catch `IdleTimeoutError || (TypeError && /fetch failed|terminated/)` → `link.close()` → `sleep(reconnectDelayMs)` → 重开连接（带游标）→ 消化首帧 state → continue；其余逻辑（state 消化、水位、无 seq 直出）与 Python 镜像；其他错误 `link.close()` 后上抛。

- [ ] **Step 4: 跑测试确认 GREEN，全套件 + 类型检查**

  Run: `cd sdk/typescript && npm test 2>&1 | tail -5 && npm run build`
  Expected: 全 PASS，build 无错

- [ ] **Step 5: Commit**

  ```bash
  git add sdk/typescript
  git commit -m "sdk(ts): mirror subscribe-first stream with internal state digestion and idle reconnect"
  ```

### Task 3: examples、smoke、文档与终审

**Files:**
- Modify: `examples/python/conversation.py`（删 pump 线程与就绪屏障）
- Modify: `examples/typescript/conversation.ts`（删 `await events.next()` 屏障）
- Modify: `sdk/typescript/scripts/smoke.ts:18-21`（删 `!== "state"` 过滤）
- Modify: `sdk/python/README.md:13,20`、`sdk/typescript/README.md:13,21`（stream 行为描述更新）
- Modify: `docs/02-sdk-contract.md` §5.1（注释）、§8（屏蔽表加一行）
- Modify: `docs/05-execution-and-streaming.md` §4.1（state 段补 SDK 消化一句）
- Test: 本地栈实跑四个 examples + smoke（验收，不新增测试文件）

**Interfaces:**
- Consumes: Task 1 `EventStream`（`stream()` 返回即就绪、`close()`）；Task 2 `await session.stream()`、`.return()`。

- [ ] **Step 1: 简化 examples**

  Python `conversation.py`：删除 `pump` 函数、`queue`/`threading` 导入与线程启动、`events.get(timeout=5)` 屏障；改为 `events = session.stream()`（注释 `# returns once the subscription is live`）→ 循环 submit → `for event in events` → 末尾 `events.close()`。事件分支（message_started/delta/run_finished/run_failed 兜底）不变。
  TS `conversation.ts`：`const events = await session.stream();`（删 `await events.next()` 行与注释），其余不变。
  `smoke.ts`：`if (event.type !== "state")` 过滤删除，直接 `types.push(event.type)`。

- [ ] **Step 2: 文档收口**

  - docs/02 §5.1 伪代码注释改为：`events = session.stream()            # 订阅即建连：返回时已就绪，才不漏流式帧`。
  - docs/02 §8 屏蔽表加行：`| 保活与状态帧（state） | 平台内部机制 —— SDK 消化、兼作心跳，用户侧事件流只有事件 |`。
  - docs/05 §4.1 周期 state 段末补一句：state 帧由 SDK 消化（心跳输入，静默超时即带游标重连），不进用户侧事件流。
  - 两个 sdk README：示例循环注释删"state 帧与"；stream bullet 补"state 帧与保活内部消化，静默自动重连"。

- [ ] **Step 3: 全仓验证**

  Run: 全仓 pytest 三套件 + `cd sdk/typescript && npm test && npm run build` + ruff；examples TS 重链接（`cd examples/typescript && rm -rf node_modules package-lock.json && npm install`）。
  Expected: 全绿。

- [ ] **Step 4: 本地栈实跑**

  Run（`SEED_AGENT_KEY` 来自 `deploy/.env`）：四个 examples + `sdk/typescript/scripts/smoke.ts`。
  Expected: 对话式两侧均实时渲染增量（订阅先于提交生效，Review Focus 1 实证）；smoke `smoke ok`。

- [ ] **Step 5: fresh reviewer 终审（按 AGENTS.md，最多 5 条）**

  派 fresh 子代理审整个分支 diff，聚焦 Review Focus 五条 + AGENTS 规范；Important 全修。

- [ ] **Step 6: Commit**

  ```bash
  git add -A
  git commit -m "examples and docs follow the subscribe-first stream"
  ```
