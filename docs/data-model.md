# 数据模型

- 状态：草稿（待评审）
- 日期：2026-10-08
- 关联：[agent-platform-design.md](./agent-platform-design.md)

---

## 1. 约定

### 1.1 领域优先

模型按**领域**组织，不按技术分层。每个领域有自己的聚合根、自己的通用语言、自己的不变式。领域边界决定表归属，而不是"这张表查起来方便放哪"。

六个领域，依赖方向**必须无环**，基础领域在下：

```
iam ──→ definition ──→ release ──→ execution

audit ← 被上述所有领域写入（不反向依赖任何领域）
```

依赖是**严格线性**的：`execution` 只依赖 `release`，**不依赖 `definition`**。会话与 Run 都绑定 `(artifact_id, artifact_version)` —— **运行只认已发布的版本**，不认草稿。

| 领域 | 聚合根 | 核心不变式 |
|---|---|---|
| `iam` | Tenant | 成员归属；应用凭据 |
| `audit` | AuditEntry | 只追加，永不修改或删除 |
| `definition` | Agent / Tool / Skill | agent 的编排结构合法（委派无环、图无环、入口唯一）；tool 的 schema 合法；skill 必须有 description |
| `release` | Artifact | 版本单调递增；已发布版本不可变 |
| `execution` | Run | run 绑定具体版本；状态机合法；step 属于唯一 run |

> **命名原则：前缀描述事物，不描述活动。** 领域名一律用名词（`definition` / `release`），不用动名词。

**v1 暂不设立的领域：**

- **凭据管理**：HTTP 工具的认证信息用环境变量占位符，不落库（见 4.1）。
- **模型目录**：模型单价、配额、路由策略暂寄在 `definition_agents` 与 `definition_nodes` 的列上。

### 1.2 命名规范

所有表位于**同一个 schema**，领域通过表名前缀表达：`<领域>_<实体>`。

| 前缀 | 领域 | 表 |
|---|---|---|
| `iam_` | 身份与访问 | `tenants`, `members`, `apps` |
| `audit_` | 审计 | `logs` |
| `definition_` | 定义 | `agents`, `nodes`, `edges`, `subagents`, `app_access`, `tools`, `skills`, `tool_bindings`, `skill_bindings` |
| `release_` | 发布 | `artifacts`, `versions` |
| `execution_` | 执行 | `sessions`, `runs`, `steps` |

共 18 张表。配套约定：主键统一 `id`；跨领域引用列命名 `<实体>_id`；时间戳 `created_at` / `updated_at`；布尔字段 `is_*`。

**不使用数据库 schema 拆分领域** —— 领域边界是应用层的概念，不是数据库对象的边界。单 schema 让迁移、备份、跨领域查询都保持简单。

### 1.3 不使用数据库外键

**所有表都不建数据库外键，引用完整性由应用层在事务内保证。**

这是刻意的取舍，不是遗漏。代价必须被正视：

| 失去的能力 | 后果 |
|---|---|
| 数据库强制完整性 | 任何绕过服务层的写入（手工 SQL、数据修复脚本、直接导入）都可能产生脏数据 |
| `ON DELETE CASCADE` | 级联删除必须由程序实现 |
| `ON DELETE RESTRICT` | 删除被引用的资源不会被数据库拦下 |
| 类型安全引用 | 引用目标是否属于正确领域、正确类型，只能程序校验 |

因此，**第 7 节「引用完整性」是本设计的强制要求，不是建议**。它描述了必须在应用层补齐的每一道防线。遗漏其中任何一条，都会直接导致脏数据。

**`tenant_id` 不在引用清单里逐条列出。** 它是租户隔离列，出现在每个领域表上，指向 `iam_tenants.id`。它的正确性由"当前请求的租户上下文"统一保证（见 7.2 第 3 条），而不是逐表校验。

### 1.4 权限由程序实现

**不使用数据库的 `GRANT` / `REVOKE`，不靠 schema 拆分做隔离。**

- 数据库只有一个应用账号，对所有表有完整权限。
- 授权数据存于 `iam_members`、`definition_app_access`，全部由程序读写。
- 权限判定发生在应用层，且**必须在两处发生**：配置面（编辑/查询）与执行面出队前（运行）。后者不可省略。
- 跨租户访问一律拒绝，不做例外。

数据库层的隔离只提供**纵深防御的最后一道**：若将来需要，可为只读分析场景单独开受限账号。这不属于 v1。

---

## 2. 身份与访问域 `iam_`

最基础的领域，**不依赖任何其他领域**。

这里有**两套互不打通的身份**：

- **平台成员**：登录配置 UI 的人。v1 不做单点登录，账号由平台自己管。
- **调用方应用**：通过 SDK 调用 Agent 的外部系统。持应用凭据，可代其终端用户发起调用。

```sql
CREATE TABLE iam_tenants (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name       TEXT NOT NULL UNIQUE,
  status     TEXT NOT NULL DEFAULT 'active'
             CHECK (status IN ('active','suspended')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE iam_members (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  UUID NOT NULL,                     -- ref: iam_tenants.id
  user_id    UUID NOT NULL,                     -- 平台账号
  role       TEXT NOT NULL
             CHECK (role IN ('owner','editor','runner','viewer')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, user_id)
);

CREATE TABLE iam_apps (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id          UUID NOT NULL,             -- ref: iam_tenants.id
  name               TEXT NOT NULL,
  description        TEXT,
  key_hash           TEXT NOT NULL,             -- API Key 的哈希，明文只在签发时出现一次
  key_prefix         TEXT NOT NULL,             -- 展示与检索用，如 'ak_live_3f9a'
  verify_key         TEXT,                      -- 调用方签发用户令牌的公钥；不用前端 SDK 时为空
  rate_limit_per_min INTEGER NOT NULL DEFAULT 600,
  daily_quota        INTEGER,                   -- 每日调用上限；NULL = 不限
  status             TEXT NOT NULL DEFAULT 'active'
                     CHECK (status IN ('active','suspended')),
  created_by         UUID,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, name)
);

CREATE INDEX idx_iam_apps_key_prefix ON iam_apps (key_prefix);
```

> **平台成员与调用方用户是两套，不做打通。** 调用方传进来的用户标识原样记录到执行域（`caller_ref`），**不会**被当作平台成员，也不参与平台侧的角色判定。这样平台不需要做单点登录，也不需要信任外部的用户目录。

**权限判定顺序（配置面，第一个命中即停）：**

1. 同租户成员 → 按角色（`owner` / `editor` 可编辑，`runner` 可运行，`viewer` 只读）
2. 其他 → 拒绝

**Agent 在成员角色之前多一级**：`definition_agents.owner_id`（负责人）对该 Agent 有全权（编辑 / 发布 / 运行 / 转移归属）。
**定义域里只有 `agent` 这一 kind 有负责人。** Tool 与 Skill 是共享能力，没有归属人，从成员角色开始判定。
`owner_id` 是负责人（可转移，人走了要交接），与 `created_by`（谁最早建的，事实记录）不是一回事。

调用面的权限判定见 [agent-platform-design.md](./agent-platform-design.md) 第 9 节。

---

## 3. 审计域 `audit_`

**独立领域，不属于 iam。** 它的变更驱动是合规要求，与组织调整无关；且它被**所有**领域写入。

```sql
CREATE TABLE audit_logs (
  id          BIGSERIAL PRIMARY KEY,
  tenant_id   UUID NOT NULL,
  actor_kind  TEXT NOT NULL CHECK (actor_kind IN ('member','app','system')),
  actor_ref   TEXT NOT NULL,                    -- member: user_id / app: app_id / system: 任务名
  action      TEXT NOT NULL,
  entity_kind TEXT,                             -- 'agent' / 'tool' / 'skill' / 'app' ...
  entity_id   UUID,
  before      JSONB,
  after       JSONB,
  at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_logs_entity ON audit_logs (entity_kind, entity_id, at DESC);
CREATE INDEX idx_audit_logs_tenant ON audit_logs (tenant_id, at DESC);
```

> **`actor_kind` 区分两套身份** —— 配置面的改动记 `member`，调用面的动作记 `app`。
> 配置不经 Git 管理后，**审计日志是唯一的变更追溯手段**，不是可选项。
> 本表**只追加**：不允许 `UPDATE`，不允许 `DELETE`（由程序保证，见 7.3）。

---

## 4. 定义域 `definition_`

**Agent、Tool、Skill 是同一类事物：声明式的、可版本化的、定义一次可复用多次的蓝图。** 它们同属一个领域，各建一张表 —— 结构迥异，但变更驱动一致：都是"改定义"。

Agent 是**组合者**：它挂载 Tool、挂载 Skill、引用子 Agent、声明对哪些应用开放。Tool 与 Skill 是**被组合的共享能力**，不归属于任何 Agent。

运行实例不在这个领域，在 `execution_`：**一份定义 → 多个会话 → 多次运行。**

> **只有 Agent 有负责人。** `definition_agents.owner_id` 是定义域里唯一的归属字段 —— Tool 与 Skill 是共享能力，不存在"谁的 Tool"。

### 4.1 工具 `definition_tools`

**HTTP 工具的凭据处理（v1 方案）：**

v1 **没有凭据管理领域**，认证信息按以下方式处理：

- 请求头/参数中写**占位符**，如 `"Authorization": "Bearer ${GITHUB_TOKEN}"`。
- 执行时由 Worker 从**自己的环境变量**解析占位符，替换后发出请求。
- **明文密钥不得写入配置。** 落库的只是占位符字符串。

代价必须记在账上：没有集中管理、没有轮换机制、没有使用审计，密钥散落在各 Worker 的部署环境里。加回凭据领域前，这一点必须被知晓。

```sql
CREATE TABLE definition_tools (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id          UUID NOT NULL,
  name               TEXT NOT NULL,
  description        TEXT NOT NULL,             -- shown to the LLM
  tool_kind          TEXT NOT NULL CHECK (tool_kind IN ('http','python')),
  input_schema       JSONB NOT NULL,            -- JSON Schema, converted to LLM tool schema
  output_schema      JSONB,
  timeout_seconds    INTEGER NOT NULL DEFAULT 30,
  max_output_bytes   INTEGER NOT NULL DEFAULT 65536,
  -- HTTP tool
  http_method        TEXT,
  http_url           TEXT,
  http_headers       JSONB,                     -- may contain ${ENV_VAR} placeholders
  http_body_template TEXT,
  http_response_path TEXT,
  -- Python tool
  py_entrypoint      TEXT,
  py_source          TEXT,
  py_requirements    TEXT[],
  py_image_tag       TEXT,
  py_network_policy  TEXT NOT NULL DEFAULT 'none'
                     CHECK (py_network_policy IN ('none','allowlist')),
  created_by         UUID,
  updated_by         UUID,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, name),
  CHECK (
    (tool_kind = 'http'
      AND http_method IS NOT NULL AND http_url IS NOT NULL AND py_entrypoint IS NULL)
    OR
    (tool_kind = 'python'
      AND py_entrypoint IS NOT NULL AND http_url IS NULL)
  )
);
```

### 4.2 Skill `definition_skills`

```sql
CREATE TABLE definition_skills (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      UUID NOT NULL,
  name           TEXT NOT NULL,
  description    TEXT NOT NULL,                 -- required: drives progressive disclosure
  content_md     TEXT NOT NULL,
  default_policy TEXT NOT NULL DEFAULT 'on_demand'
                 CHECK (default_policy IN ('always','on_demand')),
  created_by     UUID,
  updated_by     UUID,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, name)
);
```

> `content_md` 设大小上限（建议 256 KB）。超出上限说明这个 Skill 该拆成两个，而不是给它加附件存储。

### 4.3 Agent `definition_agents`

聚合根。`mode` 决定它内部是 ReAct 循环还是显式图：

- **`react`**：指令 + 工具 + 子 Agent，模型自己决定路径。不使用节点与边。
- **`dag`**：不使用指令与循环上限，结构由 `definition_nodes` + `definition_edges` 表达。

```sql
CREATE TABLE definition_agents (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL,
  name            TEXT NOT NULL,
  description     TEXT,
  mode            TEXT NOT NULL CHECK (mode IN ('react','dag')),
  owner_id        UUID NOT NULL,                -- ref: iam_members.id, the responsible member
  -- react mode
  model_provider  TEXT,
  model_name      TEXT,
  temperature     NUMERIC(3,2),
  max_tokens      INTEGER,
  instructions    TEXT,
  max_steps       INTEGER NOT NULL DEFAULT 30,   -- ReAct loop iteration cap
  -- common
  timeout_seconds INTEGER NOT NULL DEFAULT 600,
  token_budget    INTEGER,
  lock_version    INTEGER NOT NULL DEFAULT 0,
  created_by      UUID,
  updated_by      UUID,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, name),
  CHECK (mode <> 'react' OR (model_provider IS NOT NULL AND model_name IS NOT NULL))
);
```

> `max_steps` 是 ReAct 循环的迭代上限，必须有值：没有它，一个陷入循环的 Agent 会一直烧 token。`dag` 模式下该值不使用 —— 图无环，执行必然终止。

### 4.4 图的节点与边

**只有 `mode = 'dag'` 的 Agent 才有节点和边**；`react` 模式下这两张表没有该 Agent 的行。

```sql
CREATE TABLE definition_nodes (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id        UUID NOT NULL,                -- ref: definition_agents.id
  node_key        TEXT NOT NULL,                -- 图内唯一标识，边的两端引用它
  node_type       TEXT NOT NULL
                  CHECK (node_type IN ('llm','tool','agent','condition')),
  name            TEXT,
  ord             INTEGER NOT NULL DEFAULT 0,   -- 画布布局顺序
  -- llm node
  model_provider  TEXT,
  model_name      TEXT,
  prompt_template TEXT,
  output_schema   JSONB,
  -- tool node
  tool_id         UUID,                         -- ref: definition_tools.id
  tool_input      JSONB,                        -- 参数模板，可引用上游输出
  -- agent node
  subagent_id     UUID,                         -- ref: definition_subagents.id
  -- condition node
  condition_expr  TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (agent_id, node_key),
  CHECK (
    (node_type = 'llm'       AND model_provider IS NOT NULL AND model_name IS NOT NULL)
    OR (node_type = 'tool'      AND tool_id IS NOT NULL)
    OR (node_type = 'agent'     AND subagent_id IS NOT NULL)
    OR (node_type = 'condition' AND condition_expr IS NOT NULL)
  )
);

CREATE INDEX idx_definition_nodes_agent  ON definition_nodes (agent_id);
CREATE INDEX idx_definition_nodes_tool   ON definition_nodes (tool_id);
CREATE INDEX idx_definition_nodes_subagt ON definition_nodes (subagent_id);

CREATE TABLE definition_edges (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id      UUID NOT NULL,                  -- ref: definition_agents.id
  from_node_key TEXT NOT NULL,                  -- ref: definition_nodes.node_key (same agent)
  to_node_key   TEXT NOT NULL,                  -- ref: definition_nodes.node_key (same agent)
  condition     TEXT,                           -- condition 节点的分支标签；NULL = 无条件边
  ord           INTEGER NOT NULL DEFAULT 0,
  UNIQUE NULLS NOT DISTINCT (agent_id, from_node_key, condition)
);

CREATE INDEX idx_definition_edges_agent ON definition_edges (agent_id);
```

> **`agent_id` 在边表上是冗余的**（节点已经带着它），但它让"按 Agent 取整张图"和"按 Agent 全量替换"都只查一张表，也让"边的两端限定在同一聚合内"这件事可以被显式校验。
> **边引用的是 `node_key` 而不是 `node_id`** —— 图在画布上被反复重排，用稳定键比用自增身份更好 diff。两者在同一事务内写入，不会有中间态。

### 4.5 挂载与子 Agent

Tool / Skill 挂载是**能力引用**，子 Agent 是**另一个 Agent 的引用**。三张表都限定在同一聚合内，生命周期随 Agent。

```sql
CREATE TABLE definition_tool_bindings (
  id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id UUID NOT NULL,                       -- ref: definition_agents.id
  tool_id  UUID NOT NULL,                       -- ref: definition_tools.id
  config   JSONB NOT NULL DEFAULT '{}',         -- tool-level config overrides
  ord      INTEGER NOT NULL DEFAULT 0,
  UNIQUE (agent_id, tool_id)
);

CREATE INDEX idx_definition_tool_bindings_tool ON definition_tool_bindings (tool_id);

CREATE TABLE definition_skill_bindings (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id    UUID NOT NULL,                    -- ref: definition_agents.id
  skill_id    UUID NOT NULL,                    -- ref: definition_skills.id
  load_policy TEXT NOT NULL DEFAULT 'on_demand'
              CHECK (load_policy IN ('always','on_demand')),
  ord         INTEGER NOT NULL DEFAULT 0,
  UNIQUE (agent_id, skill_id)
);

CREATE INDEX idx_definition_skill_bindings_skill ON definition_skill_bindings (skill_id);

CREATE TABLE definition_subagents (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id             UUID NOT NULL,           -- ref: definition_agents.id (parent)
  sub_agent_id         UUID NOT NULL,           -- ref: definition_agents.id (child)
  expose_as            TEXT NOT NULL,           -- 父 Agent 引用它的名字，父 Agent 内唯一
  description_override TEXT,
  ord                  INTEGER NOT NULL DEFAULT 0,
  UNIQUE (agent_id, sub_agent_id),
  UNIQUE (agent_id, expose_as),
  CHECK (agent_id <> sub_agent_id)
);

CREATE INDEX idx_definition_subagents_sub ON definition_subagents (sub_agent_id);
```

> **`definition_subagents` 是"这个 Agent 用到哪些子 Agent"的唯一声明。** 两种模式共用它：
> - `react` 模式：每一条自动暴露成父 Agent 的一个工具，工具名就是 `expose_as`。
> - `dag` 模式：`agent` 类型的节点通过 `subagent_id` 指向它。
>
> 因此**环检测只需要看这一张表**，不必扫描所有节点的定义。

### 4.6 对外开放 `definition_app_access`

**授权关系跟着被保护的资源走，不跟着主体走。** 把这张表放进定义域，`iam` 得以保持"不依赖任何领域"的基础地位，`definition → iam` 的方向也天然成立。

```sql
CREATE TABLE definition_app_access (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id   UUID NOT NULL,                     -- ref: definition_agents.id
  app_id     UUID NOT NULL,                     -- ref: iam_apps.id
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (agent_id, app_id)
);

CREATE INDEX idx_definition_app_access_app ON definition_app_access (app_id);
```

> **默认拒绝。** 没有这一行的应用调不动这个 Agent —— 对外边界不能靠"同租户即可"隐式放行。
> 谁来决定？**Agent 的负责人**：`owner_id` 天然有权管理这张表。

---

## 5. 发布域 `release_`

**这是独立领域，不是各资源类型的附属。** 它有自己的语言（草稿 / 发布 / 版本 / 回滚 / changelog）和不变式，并且**泛化于所有可发布物**。

```sql
CREATE TABLE release_artifacts (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id          UUID NOT NULL,
  entity_kind        TEXT NOT NULL CHECK (entity_kind IN ('agent','tool','skill')),
  entity_id          UUID NOT NULL,             -- ref: the entity in its own domain
  status             TEXT NOT NULL DEFAULT 'draft'
                     CHECK (status IN ('draft','published','archived')),
  current_version_id UUID,                      -- ref: release_versions.id, always a 'stable' one
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (entity_kind, entity_id)
);

CREATE INDEX idx_release_artifacts_tenant ON release_artifacts (tenant_id, status);

CREATE TABLE release_versions (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  artifact_id  UUID NOT NULL,                   -- ref: release_artifacts.id
  version      INTEGER NOT NULL,
  channel      TEXT NOT NULL DEFAULT 'stable'
               CHECK (channel IN ('stable','debug')),
  spec         JSONB NOT NULL,                  -- full snapshot, serialized
  changelog    TEXT,
  published_by UUID,
  published_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (artifact_id, version)
);

CREATE INDEX idx_release_versions_artifact ON release_versions (artifact_id, version DESC);
```

**职责边界要说清楚：**

- `release_artifacts` 只做**发布身份**。name、description 这些留在定义域自己持有 —— 它不是"资源主表"的翻版。
- `entity_kind` + `entity_id` 是指向定义域实体的**多态引用**。方向是 `release → definition`。
- 草稿内容的并发控制（`lock_version`）留在定义域实体上，因为那是**编辑**的并发，不是**发布**的并发。

**`channel` 把"上线"和"调试"分开：**

| channel | 谁产生 | 作用 |
|---|---|---|
| `stable` | 正式发布 | 推进 `current_version_id`；对外调用默认用它 |
| `debug` | Playground 试跑 | **不推进** `current_version_id`；只用于试跑与回放 |

> 这样"运行只认已发布版本"这条约束不用打破，调试也不会污染线上版本序列。
> `current_version_id` 必须指向 `stable` 版本 —— 这是跨行约束，由程序保证。
> `release_artifacts` 与 `release_versions` 互相引用，发布时需在同一事务内先插版本再回填 `current_version_id`。

---

## 6. 执行域 `execution_`

```sql
CREATE TABLE execution_sessions (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        UUID NOT NULL,
  artifact_id      UUID NOT NULL,               -- ref: release_artifacts.id
  artifact_version INTEGER NOT NULL,            -- session is pinned to a published version
  app_id           UUID,                        -- ref: iam_apps.id; NULL = platform-internal call
  caller_ref       TEXT,                        -- external user id; member id for internal calls
  state            JSONB,                       -- LangGraph checkpointer state
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_execution_sessions_artifact ON execution_sessions (artifact_id, artifact_version);
CREATE INDEX idx_execution_sessions_app      ON execution_sessions (app_id, created_at DESC);

CREATE TABLE execution_runs (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        UUID NOT NULL,
  artifact_id      UUID NOT NULL,               -- ref: release_artifacts.id
  artifact_version INTEGER NOT NULL,            -- run is pinned to a published version
  session_id       UUID,                        -- ref: execution_sessions.id
  parent_run_id    UUID,                        -- ref: execution_runs.id; set for sub-agent runs
  request_id       TEXT,                        -- caller-supplied idempotency key
  app_id           UUID,                        -- ref: iam_apps.id
  caller_ref       TEXT,
  status           TEXT NOT NULL
                   CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
  input            JSONB,
  output           JSONB,
  error            JSONB,
  token_usage      JSONB,
  cost_usd         NUMERIC(12,6),
  started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at      TIMESTAMPTZ,
  -- Postgres 15+: NULLS NOT DISTINCT; app_id is NULL for internal calls and those
  -- must not collide with each other.
  UNIQUE NULLS NOT DISTINCT (app_id, request_id)
);

CREATE INDEX idx_execution_runs_artifact ON execution_runs (artifact_id, started_at DESC);
CREATE INDEX idx_execution_runs_session  ON execution_runs (session_id);
CREATE INDEX idx_execution_runs_parent   ON execution_runs (parent_run_id);
CREATE INDEX idx_execution_runs_active   ON execution_runs (status) WHERE status IN ('queued','running');

CREATE TABLE execution_steps (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id         UUID NOT NULL,                 -- ref: execution_runs.id
  parent_step_id UUID,                          -- ref: execution_steps.id
  seq            INTEGER NOT NULL,
  step_kind      TEXT NOT NULL
                 CHECK (step_kind IN ('llm','tool','agent','condition','skill_load')),
  node_key       TEXT,                          -- dag mode: the node this step corresponds to
  name           TEXT,
  input          JSONB,
  output         JSONB,
  token_usage    JSONB,
  latency_ms     INTEGER,
  status         TEXT,
  started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at    TIMESTAMPTZ
);

CREATE INDEX idx_execution_steps_run ON execution_steps (run_id, seq);
```

> **执行域是唯一允许被物理删除的领域**（删除顺序见 7.3）。其余领域一律软删除。
> **会话与 Run 都绑定 `release_artifacts.id` + 版本号，不绑定 `definition_agents` 里的草稿。** 整个执行域不引用定义域，只引用发布域 —— 这既保证历史执行可精确复现，也让依赖图保持严格线性。
> `node_key` 只在 `dag` 模式下有值，是**快照里的键**而非外键 —— 定义后来改了，历史 trace 仍然指向它当时跑的那个节点。
> `parent_run_id` 记录子 Agent 的嵌套运行：深度由应用层限制（建议上限 8），**环检测在定义域完成**，执行期不必再判。

---

## 7. 引用完整性（程序控制）

没有数据库外键兜底，以下四道防线**缺一不可**。

### 7.1 引用清单

实现校验逻辑时的完整清单。**领域内引用**与**跨领域引用**分开标注 —— 跨领域引用必须显式承认依赖方向。

**领域内引用：**

| 引用方 | 引用列 | 目标 |
|---|---|---|
| `iam_members` | `tenant_id` | `iam_tenants.id` |
| `iam_apps` | `tenant_id` | `iam_tenants.id` |
| `definition_nodes` | `agent_id` | `definition_agents.id` |
| `definition_nodes` | `tool_id` | `definition_tools.id` |
| `definition_nodes` | `subagent_id` | `definition_subagents.id` |
| `definition_edges` | `agent_id` | `definition_agents.id` |
| `definition_edges` | `from_node_key` / `to_node_key` | `definition_nodes.node_key`（同一 `agent_id` 内） |
| `definition_tool_bindings` | `agent_id` / `tool_id` | `definition_agents.id` / `definition_tools.id` |
| `definition_skill_bindings` | `agent_id` / `skill_id` | `definition_agents.id` / `definition_skills.id` |
| `definition_subagents` | `agent_id` / `sub_agent_id` | `definition_agents.id` |
| `definition_app_access` | `agent_id` | `definition_agents.id` |
| `release_artifacts` | `current_version_id` | `release_versions.id` |
| `release_versions` | `artifact_id` | `release_artifacts.id` |
| `execution_runs` | `session_id` / `parent_run_id` | `execution_sessions.id` / `execution_runs.id` |
| `execution_steps` | `run_id` / `parent_step_id` | `execution_runs.id` / `execution_steps.id` |

**跨领域引用：**

| 引用方 | 引用列 | 目标领域 | 目标 |
|---|---|---|---|
| `definition_agents` | `owner_id` | iam | `iam_members.id` |
| `definition_app_access` | `app_id` | iam | `iam_apps.id` |
| `release_artifacts` | `entity_id` | definition | `entity_kind` 指向的定义实体 |
| `execution_sessions` | `artifact_id` | release | `release_artifacts.id` |
| `execution_runs` | `artifact_id` | release | `release_artifacts.id` |

> 除了这五条，**`tenant_id` 出现在所有领域表上**，指向 `iam_tenants.id`。它由租户上下文统一保证，不逐条列出（见 1.3）。
> `execution_*` 表上的 `app_id` 指向 `iam_apps.id` —— 那是**调用方标识的记录**，不参与任何业务判定，只用于归属与审计，因此不构成一条依赖。

### 7.2 写入前校验

**所有写入必须在同一个事务内完成校验与落库。**

校验顺序（任一失败即回滚）：

1. **存在性** —— 每个引用列的目标行存在且未被归档。
2. **领域与类型** —— 目标实体的种类与预期一致。多态引用（`entity_kind`）必须校验 kind 与 id 指向的实体**确实属于该种类**。
3. **租户一致性** —— 被引用实体与引用方的 `tenant_id` 相同。跨租户引用一律拒绝，不做例外。
4. **可见性** —— 当前操作者对被引用资源有 `read` 权限。
5. **聚合内不变式** —— 三组：
   - **委派无环**：`definition_subagents` 构成的图上没有环。
   - **图结构合法**（仅 `dag`）：无环、有唯一入口、无孤立节点、`condition` 节点的出边条件完整、边的两端在同一 Agent 内。
   - **模式一致性**：`react` 模式的 Agent 不得有节点与边；`dag` 模式的 Agent 不得有 `instructions` 与 `max_steps`。
6. **跨领域依赖方向** —— 只允许第 1.1 节定义的方向。例如 `execution` 不可引用 `definition`：运行只认已发布的版本。

**并发竞态**：校验通过到写入落库之间存在窗口，期间另一事务可能归档被引用的实体。处理方式：

- 校验查询对被引用行使用 `SELECT ... FOR SHARE`，与可能归档它的事务互斥。
- 归档是 `UPDATE`（软删除），不删行，因此最坏情况是引用到一个刚被归档的实体 —— 由第 1 条拦截；运行时另有兜底：编译期再次校验引用可用性。

### 7.3 删除规则

**除执行域与审计域外，一律软删除。** 实体的删除 = `status = 'archived'`，行保留。这同时消除了大部分悬空引用风险 —— 引用目标永远存在，只是被标记不可用。

**审计域是只追加的**：`audit_logs` 不允许 `UPDATE`，不允许 `DELETE`，由程序保证。

物理删除仅允许通过专用运维任务执行，且必须：

1. 调用 `find_referrers(kind, resource_id)` 按第 7.1 节清单逐列反查，列出所有引用方。
2. 若存在活跃引用，默认拒绝并返回引用清单。
3. 强制删除时，须在同一事务内按依赖顺序**先删引用方行**，再删实体行。

**删除一个 Agent 的级联顺序**（其余实体同理，按各自被引用情况展开）：

```
definition_app_access
  → definition_tool_bindings / definition_skill_bindings
  → definition_subagents        （agent_id 与 sub_agent_id 两侧都要反查）
  → definition_edges
  → definition_nodes
  → definition_agents
```

**删除一个已发布物的顺序：**

```
execution_steps → execution_runs → execution_sessions   （仅限保留策略允许时）
  → release_versions
  → release_artifacts
```

执行域例外：`execution_steps` / `execution_runs` / `execution_sessions` 允许按保留策略物理删除，但**删除 `execution_sessions` 前必须先将引用它的 `execution_runs.session_id` 置空**。

### 7.4 孤儿巡检

没有 `CASCADE`，孤儿行不会被自动清理，必须由定时任务兜底。

**建议每 6 小时跑一次**，按 7.1 清单逐条执行反连接检测。示例：

```sql
-- Tool nodes pointing at a missing tool
SELECT n.id, n.agent_id, n.tool_id
FROM definition_nodes n
LEFT JOIN definition_tools t ON t.id = n.tool_id
WHERE n.node_type = 'tool' AND t.id IS NULL;

-- Edges whose endpoints no longer exist in the same agent
SELECT e.id, e.agent_id, e.from_node_key, e.to_node_key
FROM definition_edges e
LEFT JOIN definition_nodes f ON f.agent_id = e.agent_id AND f.node_key = e.from_node_key
LEFT JOIN definition_nodes t ON t.agent_id = e.agent_id AND t.node_key = e.to_node_key
WHERE f.id IS NULL OR t.id IS NULL;
```

其余引用列按同模式展开。发现孤儿时：

- 记录到 `audit_logs`（`action = 'orphan_detected'`）并告警。
- **不自动删除。** 孤儿数据往往意味着上游有 bug，自动清理会销毁证据。由人工确认后再清理。

**多态引用的巡检要点**：`entity_kind` 必须参与 join 条件，否则会误报。例如：

```sql
-- Artifacts whose entity_kind does not match the referenced entity
SELECT a.id, a.entity_kind, a.entity_id
FROM release_artifacts a
LEFT JOIN definition_agents g ON a.entity_kind = 'agent' AND g.id = a.entity_id
LEFT JOIN definition_tools  t ON a.entity_kind = 'tool'  AND t.id = a.entity_id
LEFT JOIN definition_skills s ON a.entity_kind = 'skill' AND s.id = a.entity_id
WHERE g.id IS NULL AND t.id IS NULL AND s.id IS NULL;
```

### 7.5 导入路径的额外要求

导入是唯一会批量写入跨领域引用的入口，也是最容易绕过校验的地方。导入必须在**单一事务**内完成：

1. 全部实体按领域依赖拓扑排序（iam → definition → release）。
2. 每个实体写入前执行 7.2 的完整校验。
3. 跨环境的 ID 翻译在边界完成：导出时把引用渲染为 `{kind, name}` 业务标识，导入时解析回目标环境的 UUID。
4. 任一失败则整个导入回滚，返回失败项与原因，不留部分写入。

**导入一个 Agent 时，写入顺序固定**：`definition_agents` → `definition_nodes` → `definition_edges` → 三张挂载/子 Agent 表 → `definition_app_access`。边引用 `node_key`，所以必须在节点之后写。

---

## 8. 读模型

没有共享父表，因此"列出某租户下所有可发布物"这类跨领域查询没有一张表可查。**这类查询走读模型，不污染领域模型。**

- 读模型是**投影**，由领域写操作驱动更新，可以是物化视图或独立表（用 `read_model_` 前缀与领域表区分）。
- 典型投影：`read_model_catalog` —— 汇总 `(tenant_id, entity_kind, name, description, status, latest_stable_version, updated_at)`，供配置 UI 的列表页与搜索使用。
- 读模型**不参与业务写入**，可随时从领域表重建。重建脚本是它存在的必要配套。

> 用读模型而非共享父表，是 CQRS 的基本取舍：写入侧保持领域纯净，读取侧为查询便利做冗余。代价是投影有延迟，且需要在每次领域写入后触发更新。

---

## 9. 与主文档的关系

- **领域划分的理由、依赖方向、聚合边界**见 [agent-platform-design.md](./agent-platform-design.md) 第 4 节。
- 本文档描述**物理结构**与**完整性约束的实现要求**。
- 任何新增引用列，都必须同时更新 7.1 的引用清单与 7.4 的巡检查询。这是硬性要求。
