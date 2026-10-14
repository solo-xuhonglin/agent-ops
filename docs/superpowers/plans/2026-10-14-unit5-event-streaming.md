# Unit 5: 事件推流 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 事件从 worker 经 Run 连接流向 manager，再以 SSE 推给会话订阅者：订阅即补发（状态 → 游标后的持久事件 → 接实时）、断线带游标重连不丢持久事件、周期 state 兼作保活。

**Architecture:** worker 桩执行器改为**返回本次新写的日志条目**（先落库、后上报，06 §2.3），`client._run_one` 把每条沿 Run 连接发出，帧形如 `{"type": <事件名>, "data": {run_id, seq, action_id, **payload}}`（= 日志行去掉 created_at，payload 内联）。manager 内部连接处理扩展：认识 `state` 与全部事件名，事件按 `run_session` 映射（派发时记录）找到会话，投给该会话的订阅者队列（asyncio.Queue 集合），并刷新会话最后已知 (run, seq)。新增 `GET /v1/sessions/{session_id}/events`（Bearer、`from_run`/`from_seq` 游标、`text/event-stream`）：首帧即时 state → DB 补发游标后的持久事件 → 队列实时帧；空闲按 `STATE_INTERVAL` 周期重推 state（事件的投影，非第二真相源）。SSE 事件帧带 `id: <run_id>:<seq>`，state 不带 id。订阅期间生成中的消息累积文本（docs 05 §4.2 第 3 步）随单元 7 的流式增量一起做。

**Tech Stack:** FastAPI StreamingResponse（async 生成器）、asyncio.Queue、现有 wire 编解码、SQLAlchemy 读日志。

**Spec:** `docs/05-execution-and-streaming.md` §3（对外事件、引用外发）、§4（推流与重连：4.1 传输/保活、4.2 订阅补什么、4.3 保证、4.4 窗口、4.5 多订阅者）；`docs/06-internal-protocol.md` §1.1（客户端侧周期 state）、§1.2（事件帧与 SSE id）、§2.3（先落库再上报、事件是转发的字节）；`docs/02-sdk-contract.md` §6.4（游标是 (run_id, seq)、可能重复由客户端去重）；`docs/11-roadmap.md` 单元 5。

## Global Constraints

- 事件帧 `data` = 日志行去 created_at、payload 内联（06 §1.2 示例形状）。
- worker 先写库、后上报；只上报**本次新写**的条目（重派续写不重发旧条目）。
- SSE：`id` 仅事件帧携带（= 游标）；state 帧 `{"type":"state","data":{session_id, run|null}}`；周期小于链路最短超时（默认 10s，env `STATE_INTERVAL`）。
- 订阅单位是会话；游标缺省 = 从当前 Run 开头补（05 §4.2）。
- 持久事件顺序单调、可能重复——由消费者按 (run_id, seq) 去重（02 §6.4），服务端不保证不重发。
- 会话 state 是事件的投影：只从已转发事件派生，不做第二来源（06 §4）。
- 注释/日志英文；提交英文；ruff 120；测试只连 `*_test` 库；提交前测试与静态检查全绿。

## Review Focus

1. 先落库后上报 + 只发新条目：重派续写时旧条目不重发、新终态发出 —— Task 1 `test_execute_returns_only_new_entries`。
2. 订阅补发次序：即时 state → 游标后持久事件（有序、带 id 游标行）—— Task 2 `test_subscribe_replays_from_cursor` / `test_subscribe_without_cursor_from_run_start`。
3. 实时转发：订阅后 Run 连接发的事件经 SSE 到达，id 行正确 —— Task 2 `test_live_event_forwarded`。
4. 断线重连：带 (run, seq) 重连只补剩余部分 —— Task 2 `test_reconnect_from_cursor`。
5. 周期 state：空闲流在窗口内收到多次 state（保活兼收敛）—— Task 2 `test_periodic_state_while_idle`。
6. 多订阅者互不影响、断开即注销 —— Task 2 `test_second_subscriber_independent` / `test_disconnect_unsubscribes`。

---

### Task 1: worker 执行器产出事件条目

**Files:** Modify `worker/src/worker/executor.py`（返回 `list[dict]` 条目）；Modify `worker/src/worker/client.py`（`_run_one` 逐条发送后关连接）；Modify `worker/tests/test_executor.py`

**Interfaces:** Produces: `execute(engine, dispatch) -> list[dict]`，条目形如 `{"type": "run_started", "data": {"run_id": str, "seq": 1, "action_id": None, **payload}}`，顺序即写入顺序；无新写时返回 `[]`。

- [ ] **Step 1: 失败测试**：`test_execute_returns_entries`（fresh → 恰两条，data 内联 payload、seq 1/2）；`test_execute_returns_only_new_entries`（跑两遍 → 第二遍 `[]`）；改 `test_cancelled_before_start` 断言终态条目 `status == cancelled`。
- [ ] **Step 2: RED** → **Step 3: 实现**（收集写入并返回；client 逐条 `encode(type, data)` 发送）→ **Step 4: 全套绿 + ruff + Commit** `Report written entries over the run connection`

### Task 2: manager 转发与 SSE 订阅

**Files:** Modify `manager/src/manager/internal.py`（事件名放行、`run_session` 映射、`subscribers` 注册表、转发与会话投影）；Create `manager/src/manager/streaming.py`（SSE 生成器与帧格式化）；Modify `manager/src/manager/api.py`（挂 `GET /v1/sessions/{session_id}/events`）；Modify `manager/src/manager/runs.py`（`replay_events(session_id, from_run, from_seq)` 按会话 Run 顺序补发）；Modify `contract/openapi.yaml`（新路径）；Test `manager/tests/test_streaming.py`

**Interfaces:** Produces: `internal.subscribe(session_id, queue)` / `unsubscribe` / 内部转发；`manager.streaming.sse_event(payload) -> str`（带 id）与 `sse_state(session_id) -> str`（不带 id）；`GET /v1/sessions/{session_id}/events?from_run=&from_seq=`；env `STATE_INTERVAL`（默认 10）。
Consumes: Task 1 的事件帧形状；`shared.events.EventType`（合法事件名集合）。

- [ ] **Step 1: 失败测试**（FakeWorker 提交并经 Run 连接发事件，TestClient 流式读取定长帧数后关闭）：`test_subscribe_without_cursor_from_run_start`（state → run_started(seq1) → run_finished(seq2)，事件帧 id 行 = `run:seq`）；`test_reconnect_from_cursor`（from_run+from_seq=1 → 只补 run_finished）；`test_live_event_forwarded`（先读 state，再在 Run 连接发事件，SSE 下一帧即它）；`test_periodic_state_while_idle`（STATE_INTERVAL=0.2，读到 2 个 state 帧）；`test_second_subscriber_independent`（两个流各自收到补发）；`test_disconnect_unsubscribes`（关闭流后 `internal.subscribers` 为空）。
- [ ] **Step 2: RED** → **Step 3: 实现**（注意：先注册队列再读 DB 补发，重复由客户端去重；内部连接收到非 state/事件名仍按 06 §1.2 断连）→ **Step 4: 全套绿 + ruff + Commit** `Stream session events over SSE with replay and cursor`

### Task 3: 部署端到端、文档与终审

- [ ] Step 1: 容器重建后真实链路验证：submit → 流式读到 run_finished（有界读取后断开）；nginx 边缘加 manager 路由（`/v1` → manager）并从宿主机经 8080 走通一次（对外只暴露 manager 与 web 的拓扑收口）。
- [ ] Step 2: 文档对账（05 §4/06 §1.1–2.3 与实现一致；contract 新路径）+ roadmap 已完成清单 + Commit。
- [ ] Step 3: 终审 fresh reviewer → 修复通道 → 清理。
