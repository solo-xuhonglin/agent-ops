# Unit 4: 内部协议与 worker 桩执行 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** manager 与 worker 之间的长连接协议跑起来：报到 / 心跳 / dispatch / cancel、epoch 栅栏、巡检重派；worker 用桩执行（不调模型）从 dispatch 落出 `run_started` / `run_finished` 日志，submit → 执行 → 终态全链路打通。

**Architecture:** 帧格式按 docs/06 §1.2：`[4 字节长度][JSON {"type","data"}]`，编解码进 `shared/wire.py`。manager 在同一事件循环里多挂一个 asyncio TCP 服务（lifespan 内 `create_server`），维护三份软状态：常驻连接表（按 `worker_id`）、Run 连接表（按 `run_id`）、派发等待表（run→时间戳）。submit 后由派发器把快照与输入沿常驻连接推出（epoch=旧+1 落库）；巡检周期扫描「非终态 ∧ 无 Run 连接 ∧ 未在报到清单 ∧ 超过宽限」的 Run 重派（epoch 再 +1），且门控在至少一个 worker 报到之后。worker 是 asyncio 客户端：常驻连接报到心跳，收到 dispatch 即建 Run 连接（首帧 `state{worker_id, run_id, epoch}`），桩执行按日志尾幂等续写，步骤边界查 epoch 与取消标志。

**Tech Stack:** asyncio（`loop.create_server` / `open_connection`），SQLAlchemy 同步引擎（异步侧经 `asyncio.to_thread`），pytest（协议测试用同步 socket 假 worker）。

**Spec:** `docs/06-internal-protocol.md` 全篇（连接、保活、消息格式、注册心跳、派发归属、取消、重启接管）；`docs/05-execution-and-streaming.md` §1（seq 由 worker 分配）、§2（`_call` 载荷完整、重试 seq 新增）、§5（at-least-once、重派语义）、§6（取消：worker 落终态）；`docs/11-roadmap.md` 单元 4。

## Global Constraints

- 帧格式唯一：`{"type","data"}`，`data` 恒为对象；协议名只有 `state` / `dispatch` / `cancel`（06 §1.2）。
- manager 是 `execution_sessions/runs` 唯一写者；worker 是 `execution_run_log` 唯一写者；worker 对 `execution_runs` 只读（epoch/取消检查）。
- epoch 由 manager 在每次派发时 +1 落库；旧 epoch 的执行不得再写日志（worker 步骤边界自查）。
- 巡检门控：至少一个 worker 报到后才开扫；「刚派发未连上」靠宽限窗口排除（06 §2.5）。
- 取消：manager 落标志 + 沿常驻连接推 `cancel`；终态 `cancelled` 由 worker 写（05 §6）。
- 桩执行幂等：按日志尾续写——已有 `run_started` 不重写，重派后接着写终态，seq 单调不回退。
- 注释/日志英文；提交英文；ruff 120；测试只连 `*_test` 库；提交前测试与静态检查全绿（AGENTS.md 测试规范）。

## Review Focus

1. 帧边界：多帧连发不粘连、截断帧不误读 —— Task 1 `test_multiple_frames` / `test_partial_frame`。
2. 派发即 epoch：submit 后第一个 dispatch 带 epoch=1 且库中 epoch 已 +1；重派 epoch=2 —— Task 3 `test_dispatch_carries_epoch_one` / Task 4 `test_sweep_redispatches_with_next_epoch`。
3. Run 连接即证据：dispatch 后未连 Run 连接 → 巡检在宽限后重派；连上则不重派 —— Task 4 同上 + `test_sweep_skips_connected_runs`。
4. at-least-once 幂等：同一 run 桩执行两遍只产生一对 run_started/终态，seq 不回退 —— Task 5 `test_executor_idempotent_resume`。
5. 取消语义：派发前取消 → worker 落 `run_finished(status=cancelled)`；已终态不受影响 —— Task 5 `test_cancelled_before_start`。
6. 巡检门控：无 worker 报到时不扫 —— Task 4 `test_sweep_gated_on_registration`。

---

### Task 1: `shared/wire.py` 编解码器

**Files:** Create `shared/src/shared/wire.py`；Test `shared/tests/test_wire.py`

**Interfaces:** Produces: `encode(type_: str, data: dict) -> bytes`（4 字节大端长度前缀）；`async read_frame(reader: asyncio.StreamReader) -> tuple[str, dict]`；`read_frame_sync(sock: socket.socket) -> tuple[str, dict]`（测试与诊断用）。

- [ ] **Step 1: 失败测试**：`test_round_trip`（encode→读回 type/data）；`test_multiple_frames`（两帧拼接连发，逐帧读出）；`test_partial_frame`（先写 4 字节长度+一半载荷，读超时/阻塞行为——同步口用 `sock.settimeout` 断言不返回完整帧即可简化为：载荷不足时抛 `ConnectionError` 或阻塞，选阻塞语义并断言超时）。
- [ ] **Step 2: RED** → **Step 3: 实现** → **Step 4: 全套绿 + ruff + Commit** `Add the length-prefixed wire codec`

### Task 2: manager 内部 TCP 服务与连接注册

**Files:** Create `manager/src/manager/internal.py`；Modify `manager/src/manager/app.py`（lifespan 起 `start_server`、注册关闭）；Test `manager/tests/test_internal.py`

**Interfaces:** Produces: `internal.start(loop) -> asyncio.Server`（绑 `MANAGER_INTERNAL_HOST:0.0.0.0` / `MANAGER_INTERNAL_PORT:9000`，端口 0 时实测端口存 `internal.port`）；连接语义：首帧 `state{worker_id}` → 常驻连接（记 `internal.workers: dict[worker_id, conn]`，`internal.reported_running: set[str]`）；首帧 `state{worker_id, run_id, epoch}` → Run 连接（`internal.run_conns: set[run_id]`，断开即移除）；之后周期 `state` 刷新。`internal.broadcast(type_, data)`。环境变量：`MANAGER_INTERNAL_PORT`、`SWEEP_INTERVAL`（默认 2.0）、`DISPATCH_GRACE`（默认 3.0）。

- [ ] **Step 1: 失败测试**（TestClient 起 lifespan 后用同步 socket 假 worker 连 `127.0.0.1:internal.port`）：`test_worker_registration`（报到 state → 无错误帧；断开再连可重连）；`test_run_connection_registers`（发 `state{worker_id, run_id, epoch}` → manager 侧集合含 run_id；断开 → 移除）。测试侧断言通过暴露的内部状态（`internal.run_conns`）进行。
- [ ] **Step 2: RED** → **Step 3: 实现**（`asyncio.start_server`，每连接读帧循环；state 分流；异常即断连清理）→ **Step 4: 绿 + ruff + Commit** `Serve the internal link and track worker presence`

### Task 3: dispatch 链路与取消推送

**Files:** Modify `manager/src/manager/internal.py`（派发器协程、`request_dispatch()` 线程安全唤醒、`dispatch_pass`）；Modify `manager/src/manager/api.py`（submit 成功后 `request_dispatch()`；cancel 落标志后 `broadcast("cancel", {run_id})`）；Test `manager/tests/test_dispatch.py`

**Interfaces:** Produces: dispatch 帧 `{"run_id", "epoch", "session_id", "snapshot", "input": {content, params, output_schema, caller}}`；`internal.request_dispatch()`（供同步 HTTP 线程调用）；派发选 worker：任一已连常驻连接；无 worker 则留待巡检。epoch：`UPDATE execution_runs SET epoch = epoch + 1 RETURNING epoch`。
Consumes: Task 2 注册表、`wire.encode`、`shared.models`。

- [ ] **Step 1: 失败测试**（假 worker 先报到，再 HTTP submit）：`test_dispatch_after_submit`（收到 dispatch：run_id/session_id 匹配、epoch==1、input 含 content 与 caller、snapshot 为快照 dict；库中 run.epoch==1）；`test_submit_without_worker_stays_pending`（无 worker → 无帧、状态 pending）；`test_cancel_pushes_frame`（cancel → 常驻连接收到 `cancel{run_id}`）；`test_dispatch_to_terminal_run_not_sent`（终态 run 不再派发——巡检跳过的前置）。
- [ ] **Step 2: RED** → **Step 3: 实现**（派发器：wait wakeup/超时 → `to_thread` 查非终态 ∧ 无 Run 连接 ∧ 无 worker 认领的 run → 逐个 epoch+1 并发送；发送成功记 dispatched 时间戳）→ **Step 4: 绿 + ruff + Commit** `Dispatch submitted runs over the persistent link`

### Task 4: 巡检与 epoch 重派

**Files:** Modify `manager/src/manager/internal.py`（派发器循环兼做巡检：宽限窗口 + 报到门控）；Test `manager/tests/test_sweep.py`

**Interfaces:** Consumes: Task 3 派发器。Produces: 巡检语义落地（门控 + 宽限 + 重派）。测试环境变量经 monkeypatch 设 `SWEEP_INTERVAL=0.2`、`DISPATCH_GRACE=0.3`。

- [ ] **Step 1: 失败测试**：`test_sweep_redispatches_with_next_epoch`（报到 worker、submit 收到 dispatch 后不开 Run 连接，等宽限+周期 → 收到第二个 dispatch，epoch==2）；`test_sweep_skips_connected_runs`（开 Run 连接后不再收到重派帧，窗口内无新帧）；`test_sweep_gated_on_registration`（无 worker 报到，submit 后等窗口 → 无任何帧）。
- [ ] **Step 2: RED** → **Step 3: 实现** → **Step 4: 绿 + ruff + Commit** `Sweep stale runs and redispatch under a new epoch`

### Task 5: worker 服务（连接循环 + 桩执行）

**Files:** Create `worker/src/worker/executor.py`、`worker/src/worker/client.py`；Modify `worker/src/worker/__main__.py`（连 manager、报到、心跳、分发 dispatch）；Modify `worker/requirements.txt`（无需新增）；Modify `deploy/docker-compose.yml`（worker 加 `MANAGER_HOST=manager` / `MANAGER_PORT=9000`，manager 无需发布端口）；Test `worker/tests/test_executor.py`

**Interfaces:** Produces: `executor.execute(engine, dispatch: dict) -> None`（幂等续写：无 `run_started` → 写 seq=1；终态已存在 → 返回；否则按取消标志写 `run_finished`：`status=cancelled`（result=None）或 `succeeded`（`result="stub: " + content`），seq=尾+1；步骤边界读 run 行校验 `epoch` 与 `cancel_requested`，epoch 不符即放弃）；`client.run()`（常驻连接：报到 `state{worker_id, running:[...]}`、周期心跳、读 dispatch → 建任务：连 Run 口、发首帧 `state{worker_id, run_id, epoch}`、`to_thread(execute)`、结束关连接）。worker_id：hostname。
Consumes: `wire`、`shared.events` payload 模型、`shared.models`。

- [ ] **Step 1: 失败测试**（直接调 `execute`，DB 夹具建 run 行）：`test_stub_writes_lifecycle`（两行日志：seq1 run_started 载荷含 content/params/session_id/caller；seq2 run_finished succeeded，result 前缀 stub）；`test_executor_idempotent_resume`（连跑两遍 → 仍两行，seq 不重复）；`test_cancelled_before_start`（run 行置 cancel_requested → 执行 → 终态 cancelled）；`test_epoch_fence`（dispatch epoch=1，库中 epoch 已变 2 → 不写任何行）。
- [ ] **Step 2: RED** → **Step 3: 实现 executor + client + __main__ + compose** → **Step 4: 绿 + ruff + Commit** `Run the stub executor over the internal link`
- [ ] **Step 5: 部署端到端**：`docker compose up -d --build manager worker`；容器内经 manager HTTP submit → 轮询 GET run 至 `succeeded` → psql 查 `execution_run_log` 两行。

### Task 6: 文档收口

- [ ] 对账 06/05 与实现（帧格式、报到、门控、宽限语义一致；差异处修文档）；roadmap 已完成清单加单元 4；Commit。
