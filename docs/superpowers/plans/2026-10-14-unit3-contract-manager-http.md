# Unit 3: contract 第一版与 manager HTTP 面 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 立起 `contract/openapi.yaml` v1 与 manager 的 HTTP 面：永久 key 鉴权、GET agent（输入声明）、new_session、submit（params 校验、幂等、会话租约）、get run（状态 = 日志尾）、cancel 标志。

**Architecture:** manager 是 FastAPI/uvicorn 服务（后续 SSE 与内部 TCP 长连接都在同一进程的 loop 上生长）。执行域两张表 `execution_sessions` / `execution_runs` 由 manager 建与写（03 §4.7）；Run 状态不落列，从 `execution_run_log` 尾部推导（05 §1）；租约是 session 行上的 `active_run_id`，提交时事务内抢占，终态后惰性回收。`contract/` 是对外协议唯一真相源，本单元以「OpenAPI 路径/方法与 app 路由一致」的测试钉住，不搞代码生成。

**Tech Stack:** FastAPI / uvicorn, SQLAlchemy 2 + psycopg 3, pydantic, OpenAPI 3.1 YAML。

**Spec:** `docs/11-roadmap.md` 单元 3；`docs/02-sdk-contract.md` §3（submit 句柄）、§4（输入/校验）、§6.1（幂等）、§6.2（会话/租约）、§6.3（状态五值）、§6.6（错误）、§8（屏蔽细节）；`docs/05-execution-and-streaming.md` §1（表与写者、状态=日志尾）、§6（取消）、§7（会话租约）；`docs/03-domain-model.md` §4.2/4.7。

## Global Constraints

- manager 是 `execution_sessions` / `execution_runs` 的唯一写者；只读 `execution_run_log` / `release_artifacts` / `agent_definitions`。
- Run 状态不另存一列，从日志尾推导（05 §1）。
- 幂等靠 `(agent_id, request_id)` 唯一索引，一次写入内判定（02 §6.1）。
- 同一会话同一时刻一个活跃 Run：租约抢占失败 → 409；重放（同 request_id）先于租约判定，返回原 Run。
- params 校验在 manager，只读快照里的输入声明，不碰编排结构（02 §4.4）。
- 对外只暴露输入声明，编排/模型/版本不外发（02 §8）。
- 鉴权：`Authorization: Bearer <永久key>`，服务端存/查 sha256 哈希；无独立授权表（10 §2）。
- 注释/日志英文；提交英文；ruff line-length 120。
- 测试只连 `*_test` 库。

## Review Focus

1. 重放幂等：同 `(agent, request_id)` 二次 submit 返回同一 Run，且在 Run 仍活跃时不被 409 拦截 —— Task 3 `test_submit_replay_returns_same_run`。
2. 租约：活跃 Run 在会话时第二个 submit 409；日志出现终态后新 submit 可惰性抢占 —— Task 3 `test_submit_conflict_while_active` / `test_lease_reclaimed_after_terminal`。
3. params 校验三拒一收：缺必填 / 未知键 / 类型错 → 422；合法 → 201 —— Task 3 `test_params_validation`。
4. 状态即日志尾：无日志 → pending；`run_started` 后 → running；`run_finished(payload.status)` → succeeded（带 result）/ cancelled —— Task 3 `test_status_follows_log_tail`。
5. 契约一致：contract/openapi.yaml 的路径与方法集合与 app 路由逐一对上 —— Task 2 `test_contract_matches_app_routes`。

---

### Task 1: `contract/` v1 与执行域表

**Files:**
- Create: `contract/openapi.yaml`
- Modify: `shared/src/shared/models.py` —— 追加 `ExecutionSession`、`ExecutionRun`
- Test: `shared/tests/test_models.py`（新建）、`shared/tests/test_contract.py`（新建）
- Modify: `requirements-dev.txt` —— 追加 `pyyaml`

**Interfaces:**
- Produces: `shared.models.ExecutionSession`（`execution_sessions`：`id` UUID pk、`agent_id` UUID、`caller` str 默认 `"backend"`、`active_run_id` UUID 可空、`created_at`）；`shared.models.ExecutionRun`（`execution_runs`：`id` UUID pk、`session_id`、`agent_id`、`artifact_id`、`request_id` str 可空、`content` text、`params` JSONB、`output_schema` JSONB 可空、`cancel_requested` bool 默认 False、`epoch` int 默认 0、`created_at`；`UniqueConstraint(agent_id, request_id)`）。
- Produces: contract v1 面 —— `POST /v1/sessions`、`POST /v1/sessions/{session_id}/runs`、`GET /v1/runs/{run_id}`、`POST /v1/runs/{run_id}/cancel`、`GET /v1/agent`；Bearer security；schemas：`InputParam`、`RunSubmit`（`content`、`params`、`output_schema`、`request_id`）、`RunView`（`run_id`、`session_id`、`status: pending|running|succeeded|failed|cancelled`、`result`、`error`、`cancel_requested`）、`Error`（`code`、`message`）。

- [ ] **Step 1: 写失败的测试**

`test_models.py`：`test_manager_tables`（表存在、列齐全）；`test_duplicate_request_id_rejected`（同 `(agent_id, request_id)` 二次插入 `IntegrityError`）。`test_contract.py`：`test_contract_parses_and_paths`（yaml 可解析，路径×方法集合恰为上面五条）。

- [ ] **Step 2: 跑测试确认失败** —— Run: `.venv/Scripts/python -m pytest shared/tests/test_models.py shared/tests/test_contract.py -v`，Expected: FAIL（模型与 yaml 不存在）。
- [ ] **Step 3: 实现表模型与 openapi.yaml**
- [ ] **Step 4: 全套通过 + ruff + Commit**

```bash
git add shared/ contract/ requirements-dev.txt
git commit -m "Write the contract v1 and the execution-side tables"
```

### Task 2: manager 骨架、鉴权与只读面

**Files:**
- Create: `manager/src/manager/db.py`、`manager/src/manager/app.py`、`manager/src/manager/api.py`、`manager/src/manager/auth.py`
- Create: `manager/requirements.txt`（fastapi、uvicorn、sqlalchemy、psycopg[binary]、pydantic）、`manager/Dockerfile`（根上下文，同 worker 形制）
- Create: `manager/tests/conftest.py`、`manager/tests/test_api_auth.py`
- Modify: `pyproject.toml`（pythonpath/testpaths 加 manager）、`deploy/docker-compose.yml`（manager：build 根上下文、DATABASE_URL、depends_on postgres healthy）

**Interfaces:**
- Consumes: `shared.models`（Task 1）、`config.seed.seed`（单测夹具直接调用建种子）。
- Produces: `manager.db.engine`；`manager.app.app`（lifespan 建 `MANAGER_TABLES = [ExecutionSession.__table__, ExecutionRun.__table__]` + `/health` 查库）；`manager.auth.require_agent`（FastAPI 依赖：Bearer → sha256 → `AgentDefinition`，失败 401 `{"error":{"code":"auth","message":...}}`）；`GET /v1/agent` → `{"input":[InputParam...]}`（最新已发布快照的输入声明）；`POST /v1/sessions` → 201 `{"session_id": ...}`。

- [ ] **Step 1: 写失败的测试**

conftest：drop_all 后建 `MANAGER_TABLES + CONFIG_TABLES + [RunLog]`，`seed(engine, KEY)`；TestClient 夹具带 `Authorization: Bearer KEY`。
`test_api_auth.py`：`test_rejects_missing_or_wrong_key`（无头 401 / 错 key 401）；`test_get_agent_returns_input_declaration`（input 数组含 `project_id` 且 `description` 非空）；`test_new_session`（201，返回 session_id）。

- [ ] **Step 2: 跑测试确认失败** —— `ModuleNotFoundError: manager`
- [ ] **Step 3: 实现 db/app/auth/api 四模块与部署接线**
- [ ] **Step 4: 全套通过 + ruff + Commit**

```bash
git add manager/ pyproject.toml deploy/
git commit -m "Stand up the manager HTTP face with key auth"
```

### Task 3: submit、幂等、租约与状态推导

**Files:**
- Create: `manager/src/manager/runs.py`
- Modify: `manager/src/manager/api.py` —— 挂 submit / get / cancel 路由
- Test: `manager/tests/test_submit.py`

**Interfaces:**
- Consumes: Task 2 的 `require_agent` / app；`shared.events` 的 `EventType` 与 `RunFinishedPayload`（写日志用 `RunLog` 直插，模拟 worker）。
- Produces: `manager.runs.submit(agent, session_id, body) -> RunView`（事务内：重放判定 → 租约抢占/惰性回收 → 建行 pending）；`manager.runs.run_view(run_id) -> RunView`（状态从日志尾推导：无日志 pending；`run_finished` 看 `payload["status"]`（succeeded/cancelled，带 `result`）；`run_failed` → failed；其余 running）；`manager.runs.request_cancel(run_id)`（置 `cancel_requested`，幂等）。

- [ ] **Step 1: 写失败的测试**

`test_submit.py`：`test_submit_creates_pending_run`（201、pending、`session.active_run_id` 指向它）；`test_submit_replay_returns_same_run`（活跃中重放同 request_id → 同 run_id、非 409）；`test_submit_conflict_while_active`（不同 request_id → 409）；`test_lease_reclaimed_after_terminal`（直插 `run_finished` 后新 submit 201）；`test_params_validation`（缺必填/未知键/类型错 → 422；合法 → 201）；`test_status_follows_log_tail`（pending → 插 `run_started` → running → 插 `run_finished` → succeeded 带 result）；`test_cancel_sets_flag_idempotent`。

- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现 runs.py 与路由**（校验规则：声明内必填必须出现；提供值类型对上 string/number/boolean（bool 不算 number）；未声明键 422）
- [ ] **Step 4: 全套通过 + ruff + Commit**

```bash
git add manager/
git commit -m "Submit with idempotency, lease, and log-tail status"
```

### Task 4: 部署验证与文档收口

- [ ] **Step 1: 重建 manager 镜像部署**：compose up --build manager；exec 内 `GET /health`、Bearer 下 `POST /v1/sessions` 冒烟；`psql \dt` 见 `execution_sessions/runs`。
- [ ] **Step 2: 文档对账**：roadmap 已完成清单 + 单元 3；02/05 与实现出入处修正（预期无）。
- [ ] **Step 3: Commit**

```bash
git add docs/ deploy/
git commit -m "Mark unit 3 done and reconcile the docs"
```
