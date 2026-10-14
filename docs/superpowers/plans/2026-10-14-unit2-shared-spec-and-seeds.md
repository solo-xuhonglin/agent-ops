# Unit 2: shared spec 模型、事件词汇表与种子 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 立起 `shared/` 包——Agent（ReAct）spec 模型与输入声明、运行日志/事件词汇表、领域表定义——各服务按写者归属建自己的表，种子数据（租户、Agent、发布版本）经 config 落库。

**Architecture:** `shared/` 是领域模型的唯一定义（01 §4）：pydantic 承担 spec 与事件词汇（校验即模型约束），SQLAlchemy 承担领域表；每个服务在自己的启动里只建自己是唯一写者的表（03 §4.7），shared 本身不连库。种子是 config 的 CLI（`python -m config.seed`）——领域表只有 config 写，种子不破例。快照 = `AgentSpec` 序列化 JSON，存 `release_artifacts.snapshot`。

**Tech Stack:** Python 3.13, pydantic v2, SQLAlchemy 2 + psycopg 3, pytest。

**Spec:** `docs/11-roadmap.md` 单元 2；`docs/05-execution-and-streaming.md` §1–2（日志表与词汇表）；`docs/03-domain-model.md` §1.2 表名、§4 实现约定；`docs/02-sdk-contract.md` §4.1（输入声明、说明必填）；`docs/04-orchestration.md` §1（ReAct 必须有迭代上限）；`docs/07-release.md` §1（快照只含配置）。

## Global Constraints

- 领域模型唯一定义在 `shared/`；`config/models.py` 里的 `Tenant` 迁入 shared，config 只保留「我建哪些表」的清单。
- 表名带领域前缀：`agent_definitions`、`release_artifacts`、`execution_run_log`（03 §1.2、05 §1）。
- 建表按写者归属：config 建 `iam_tenants` / `agent_definitions` / `release_artifacts`，worker 建 `execution_run_log`；不做增量迁移（03 §4.7）。
- 无外键，引用靠应用层（03 §4.2）。
- 快照只含配置：不含 key、责任人（07 §1）。
- 凡是给模型看的声明，说明字段必填；输入声明 key 唯一（02 §4.1）。
- 代码注释与日志英文；提交英文；ruff line-length 120、双引号、py313。
- 测试只连 `*_test` 库（安全红线）。

## Review Focus

1. 输入声明缺 description / key 重复 / 非法类型 → 校验拒绝 —— Task 1 `test_input_param_validation`。
2. 交错顺序下 `_call`/`_result` 按 `action_id` 配对；未配对的 `_call` 即未完成步骤 —— Task 2 `test_pairing_interleaved` / `test_open_action_detected`。
3. 建表不越权：config 建完库里恰好只有它的三张表，worker 恰好只有 `execution_run_log` —— Task 3 `test_config_creates_only_its_tables` / `test_worker_creates_only_its_tables`。
4. `(run_id, seq)` 主键：同一 Run 重复 seq 被拒 —— Task 3 `test_duplicate_seq_rejected`。
5. 种子幂等：重复执行不产生重复行；落库快照能反序列化回 `AgentSpec` —— Task 4 `test_seed_idempotent` / `test_snapshot_round_trips_through_db`。

---

### Task 1: `shared/` 包与 Agent spec 模型

**Files:**
- Create: `shared/src/shared/__init__.py`（空）
- Create: `shared/src/shared/spec.py`
- Create: `shared/tests/test_spec.py`
- Create: `conftest.py`（仓库根——环境准备从 `config/tests/conftest.py` 上移，全体测试共享）
- Modify: `config/tests/conftest.py` —— 删环境准备段，只留 schema fixture
- Modify: `pyproject.toml` —— `pythonpath = ["config/src", "shared/src"]`、`testpaths = ["config/tests", "shared/tests"]`

**Interfaces:**
- Produces: `shared.spec.InputParam`（`key`、`type: Literal["string","number","boolean"]`、`required=False`、`description` 非空、`default=None`）；`shared.spec.CompactionPolicy`（`window_ratio=0.7`、`token_limit=300_000`，默认值来自 05 §8.2）；`shared.spec.AgentSpec`（`mode: Literal["react"]`、`name`、`instructions`、`input: list[InputParam]`、`model_id`、`model_params: dict`、`max_iterations: int ≥1`、`context: CompactionPolicy`）；`AgentSpec.to_snapshot_json() -> str` 与 `AgentSpec.from_snapshot_json(raw: str) -> AgentSpec`。挂载（tools/skills）暂不入模型——单元 9/13 以带默认值的字段加入，旧快照自然兼容。

- [ ] **Step 1: 写失败的测试**

`shared/tests/test_spec.py`：`test_snapshot_round_trip`（构造含一个必填参数的 AgentSpec → to_json → from_json → 相等）；`test_input_param_validation`（description 为空、key 重复、type 传 "array" 三种均 `ValidationError`）；`test_max_iterations_floor`（0 被拒）。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python -m pytest shared/tests -v`
Expected: FAIL —— `shared.spec` 不存在。

- [ ] **Step 3: 实现 `spec.py` 并迁移根 conftest**

pydantic v2；key 唯一用 model_validator 检查；`.venv/Scripts/python -m pip install pydantic` 并把 `pydantic` 加进 `config/requirements.txt`。根 conftest 内容 = 现 `config/tests/conftest.py` 的环境段（load `deploy/.env`、`TEST_DATABASE_URL` 存在且库名 `_test` 结尾、覆盖 `DATABASE_URL`），逐字搬移。

- [ ] **Step 4: 全套测试确认通过**

Run: `.venv/Scripts/python -m pytest -v`
Expected: 全部 passed（config 4 个 + shared 3 个）。

- [ ] **Step 5: Commit**

```bash
git add shared/ conftest.py config/ pyproject.toml
git commit -m "Define the agent snapshot spec in shared"
```

### Task 2: 事件词汇表与配对

**Files:**
- Create: `shared/src/shared/events.py`
- Create: `shared/tests/test_events.py`

**Interfaces:**
- Consumes: 无（独立于 Task 1）。
- Produces: `shared.events.EventType`（StrEnum，11 个成员：`run_started`、`llm_call`、`llm_result`、`tool_call`、`tool_result`、`agent_call`、`agent_result`、`branch_taken`、`context_compacted`、`run_finished`、`run_failed`——与 05 §2 一一对应）；payload 模型 `RunStartedPayload`（`content`、`params`、`output_schema`、`session_id`、`caller`、`end_user=None`）、`RunFinishedPayload`（`result`）、`RunFailedPayload`（`error`）；`LogEntry`（`run_id: UUID`、`seq ≥ 1`、`action_id: str | None`、`type: EventType`、`payload: dict`）；`open_actions(entries) -> set[str]`（有 `_call` 无 `_result` 的 action_id 集合，05 §2 的「没跑完」判据）。流式事件（`message_started` / `message_delta`）不落库、不在本词汇表，待单元 5。

- [ ] **Step 1: 写失败的测试**

`test_events.py`：`test_event_type_vocabulary`（恰好 11 个成员且名字逐字对上）；`test_pairing_interleaved`（`llm_call(a)`、`tool_call(b)`、`tool_result(b)`、`llm_result(a)` 交错 → `open_actions` 为空）；`test_open_action_detected`（缺 `tool_result(b)` → `{b}`）；`test_non_action_types_ignored`（`run_started` / `context_compacted` 不影响配对）；`test_log_entry_rejects_unknown_type`。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python -m pytest shared/tests/test_events.py -v`
Expected: FAIL —— `shared.events` 不存在。

- [ ] **Step 3: 实现 `events.py`**

`open_actions` 单遍扫描：`_call` 记入、`_result` 移除；动作类型配对表 `{"llm_call": "llm_result", "tool_call": "tool_result", "agent_call": "agent_result"}`。

- [ ] **Step 4: 全套测试确认通过，Commit**

Run: `.venv/Scripts/python -m pytest -v` → 全绿。

```bash
git add shared/
git commit -m "Define the run-log vocabulary and action pairing"
```

### Task 3: 领域表入 shared，各服务建自己的表

**Files:**
- Create: `shared/src/shared/models.py` —— `Tenant`（自 `config/models.py` 迁入）、`AgentDefinition`、`ReleaseArtifact`、`RunLog`
- Delete: `config/src/config/models.py`
- Modify: `config/src/config/app.py` —— 导入改自 `shared.models`，lifespan 建表改为「config 拥有的三张表」
- Modify: `worker/src/worker/__main__.py` —— 接库：`DATABASE_URL`、建 `execution_run_log`、启动日志不变
- Create: `worker/requirements.txt`（`sqlalchemy`、`psycopg[binary]`、`pydantic`）
- Create: `worker/tests/conftest.py`、`worker/tests/test_runlog.py`
- Modify: `pyproject.toml` —— pythonpath/testpaths 增加 worker
- Modify: `deploy/docker-compose.yml` —— config 与 worker 的 build 改根上下文：`{context: .., dockerfile: config/Dockerfile}`；worker 加 `DATABASE_URL` 与 `depends_on: postgres(healthy)`
- Modify: `config/Dockerfile`、`worker/Dockerfile` —— 根上下文版：`COPY shared/ /app/shared/` + `COPY <svc>/src/`，`PYTHONPATH=/app/shared/src:/app/<svc>/src`
- Create: `.dockerignore`（仓库根：排除 `.git`、`.venv`、`docs`、`deploy`、`web`、`*.md` 等）

**Interfaces:**
- Consumes: Task 1 的根 conftest（worker 测试直接受益）。
- Produces: `shared.models.AgentDefinition`（`id` UUID pk、`tenant_id` UUID、`name` 唯一、`key_hash`、`key_version` 默认 1、`created_at`）；`shared.models.ReleaseArtifact`（`id` UUID pk、`target_kind`、`target_id`、`version`、`snapshot` JSONB、`created_at`，`(target_kind, target_id, version)` 唯一）；`shared.models.RunLog`（`execution_run_log`：`run_id` UUID、`seq`、`action_id` 可空、`type`、`payload` JSONB、`created_at`，PK `(run_id, seq)`）；服务侧清单常量：`config.app.CONFIG_TABLES`、`worker.__main__` 内联 `[RunLog.__table__]`。

- [ ] **Step 1: 写失败的测试**

`worker/tests/conftest.py`：session 级 fixture 对 `db.engine` `drop_all` + `create_all(tables=[RunLog.__table__])`。`worker/tests/test_runlog.py`：`test_worker_creates_only_its_tables`（inspect 表集合恰为 `{execution_run_log}`）；`test_runlog_round_trip`（写一条 `run_started` 再读回，字段一致）；`test_duplicate_seq_rejected`（同 `(run_id, seq)` 二次插入 `IntegrityError`）。
`config/tests/test_schema.py` 追加 `test_config_creates_only_its_tables`（经 `config.app` 的建表清单建完后，inspect 恰为 `{iam_tenants, agent_definitions, release_artifacts}`）。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python -m pytest worker/tests config/tests -v`
Expected: FAIL —— `shared.models` 不存在 / worker 无 `DATABASE_URL` 建表逻辑。

- [ ] **Step 3: 实现 `models.py`、迁移与接库**

config 的 `CONFIG_TABLES = [Tenant.__table__, AgentDefinition.__table__, ReleaseArtifact.__table__]`，lifespan `create_all(db.engine, tables=CONFIG_TABLES)`；worker `__main__` 同型。Dockerfile / compose / `.dockerignore` 一并改。

- [ ] **Step 4: 全套测试通过；重建两个镜像部署验证**

Run: `.venv/Scripts/python -m pytest -v` → 全绿；`cd deploy && docker compose up -d --build config worker`，`docker compose exec worker python -c "..."` 查启动日志无异常、`docker compose exec postgres psql -U agentops -d agentops -c '\dt'` 见 `execution_run_log`。

- [ ] **Step 5: Commit**

```bash
git add shared/ config/ worker/ pyproject.toml deploy/ .dockerignore
git commit -m "Move domain tables into shared and wire worker to the database"
```

### Task 4: 种子 CLI（租户、Agent、发布版本）

**Files:**
- Create: `config/src/config/seed.py`（`python -m config.seed`）
- Create: `config/tests/test_seed.py`
- Modify: `deploy/.env.example` —— 追加 `SEED_AGENT_KEY`（.env 本地同步）

**Interfaces:**
- Consumes: `shared.spec.AgentSpec`（Task 1）、`shared.models`（Task 3）、`config.db.engine`。
- Produces: `config.seed.seed(engine, key: str) -> SeedResult`（幂等：库中无租户则建 `default` 租户；无名为 `hello-agent` 的 Agent 则建，`key_hash = sha256(key)`；无 `(agent, version=1)` 的 artifact 则插入 `AgentSpec` 快照）；`__main__` 读 `DATABASE_URL` 与 `SEED_AGENT_KEY` 执行并打英文日志。种子 spec 固定在模块内：`hello-agent`、一条指令、一个必填参数 `project_id`（说明字段完整）、`model_id="stub"`、`max_iterations=3`。

- [ ] **Step 1: 写失败的测试**

`test_seed_idempotent`（对测试库连跑两遍 → 租户、Agent、artifact 各 1 行）；`test_snapshot_round_trips_through_db`（取出 `snapshot` JSON → `AgentSpec.from_snapshot_json` → 与种子 spec 相等）；`test_key_hash_is_sha256`（`key_hash == hashlib.sha256(key).hexdigest()`）。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python -m pytest config/tests/test_seed.py -v`
Expected: FAIL —— `config.seed` 不存在。

- [ ] **Step 3: 实现 `seed.py`**

- [ ] **Step 4: 全套测试通过；容器内跑种子验证**

Run: `.venv/Scripts/python -m pytest -v` → 全绿；`cd deploy && docker compose exec config python -m config.seed` → 日志列出三行创建；再跑一遍 → 全部「已存在，跳过」。

- [ ] **Step 5: Commit**

```bash
git add config/ deploy/.env.example
git commit -m "Seed tenant, agent, and published snapshot from config"
```

### Task 5: 文档收口

**Files:**
- Modify: `docs/11-roadmap.md` —— 已完成清单加上单元 2
- 对账后仅在实现与文档有出入处修改：01 §4（`shared/` 已实装）、03 §4.7、05 §1–2（词汇表与 `events.py` 一致性）

- [ ] **Step 1: 逐篇对账 01/03/05/07 与实现，改到一致**
- [ ] **Step 2: Commit**

```bash
git add docs/
git commit -m "Mark unit 2 done and reconcile the docs with shared/"
```
