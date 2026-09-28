# 数据模型

- 状态：草稿（待评审）
- 日期：2026-09-28
- 关联：[agent-platform-design.md](./agent-platform-design.md)

---

## 1. 约定

### 1.1 领域优先

模型按**领域**组织，不按技术分层。每个领域有自己的聚合根、自己的通用语言、自己的不变式。领域边界决定了表归属，而不是"这张表查起来方便放哪"。

六个领域，依赖方向**必须无环**，基础领域在下：

```
iam ──→ definition ──→ release ──→ execution

audit  ← 被上述所有领域写入（不反向依赖任何领域）
```

依赖是**严格线性**的：`execution` 只依赖 `release`，**不依赖 `definition`**。会话与 Run 都绑定 `(artifact_id, artifact_version)` —— **运行只认已发布的版本**，不认草稿。

| 领域 | 聚合根 | 核心不变式 |
|---|---|---|
| `iam` | Tenant | 成员归属；角色与授权 |
| `audit` | AuditEntry | 只追加，永不修改或删除 |
| `definition` | Agent / Tool / Skill | tool 的 schema 合法；http 有 url、python 有 entrypoint；skill 必须有 description；agent 必须绑定模型、挂载不重复、委派无环 |
| `release` | Artifact | 版本单调递增；已发布版本不可变 |
| `execution` | Run | run 绑定具体版本；状态机合法；step 属于唯一 run |

> **命名原则：前缀描述事物，不描述活动。** 领域名一律用名词（`definition` / `release`），不用动名词。

**v1 暂不设立的领域**（见主文档待定项）：

- **凭据管理**：HTTP 工具的认证信息用环境变量占位符，不落库（见 4.1）。
- **工作流编排**：Agent 一律用 ReAct 循环执行（LLM ↔ Tool 循环），不做显式图编排。

### 1.2 命名规范

所有表位于**同一个 schema**，领域通过表名前缀表达：`<领域>_<实体>`。

| 前缀 | 领域 | 表 |
|---|---|---|
| `iam_` | 身份与访问 | `tenants`, `workspaces`, `members`, `grants` |
| `audit_` | 审计 | `logs` |
| `definition_` | 定义 | `agents`, `tools`, `skills`, `skill_assets`, `tool_bindings`, `skill_bindings`, `delegations` |
| `release_` | 发布 | `artifacts`, `versions` |
| `execution_` | 执行 | `sessions`, `runs`, `steps` |

共 17 张表。配套约定：主键统一 `id`；跨领域引用列命名 `<实体>_id`；时间戳 `created_at` / `updated_at`；布尔字段 `is_*`。

主聚合根若与领域同名，加 `_definitions` 消歧 —— 因为前缀已经是领域名，`agent_agents` 不可读。`definition_agents` 不触发这条规则：前缀与实体名不同，直接读得通。

> 定义域的绑定表叫 `definition_tool_bindings` / `definition_skill_bindings`，不叫 `definition_agent_tools` —— 后者与 `definition_tools` 只差一个词，读起来会混。

领域边界是应用层概念，不是数据库对象边界：所有表位于同一个 schema，让迁移、备份、跨领域查询保持简单。

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

### 1.4 权限由程序实现

数据库只有一个应用账号，对所有表有完整权限。

- 授权数据存于 `iam_members` 与 `iam_grants`，全部由程序读写。
- 权限判定发生在应用层，且**必须在两处发生**：API 层（编辑/查询）与执行面出队前（运行）。后者不可省略。
- 跨租户访问一律拒绝，不做例外。

---

## 2. 身份与访问域 `iam_`

最基础的领域，其他所有领域都依赖它做鉴权。

```sql
CREATE TABLE iam_tenants (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name       TEXT NOT NULL UNIQUE,
  status     TEXT NOT NULL DEFAULT 'active'
             CHECK (status IN ('active','suspended')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE iam_workspaces (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  UUID NOT NULL,                     -- ref: iam_tenants.id
  name       TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, name)
);

CREATE INDEX idx_iam_workspaces_tenant ON iam_workspaces (tenant_id);

CREATE TABLE iam_members (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    UUID NOT NULL,                   -- ref: iam_tenants.id
  workspace_id UUID,                            -- ref: iam_workspaces.id; NULL = tenant-wide
  user_id      UUID NOT NULL,
  role         TEXT NOT NULL
               CHECK (role IN ('owner','editor','runner','viewer')),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- Postgres 15+: NULLS NOT DISTINCT, otherwise a NULL workspace_id lets the
  -- same user be inserted into the same tenant more than once.
  UNIQUE NULLS NOT DISTINCT (tenant_id, workspace_id, user_id)
);

CREATE TABLE iam_grants (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  subject_type  TEXT NOT NULL CHECK (subject_type IN ('user','group')),
  subject_id    UUID NOT NULL,
  resource_kind TEXT NOT NULL CHECK (resource_kind IN ('agent','tool','skill')),
  resource_id   UUID NOT NULL,
  role          TEXT NOT NULL CHECK (role IN ('editor','runner','viewer')),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (subject_type, subject_id, resource_kind, resource_id)
);

CREATE INDEX idx_iam_grants_resource ON iam_grants (resource_kind, resource_id);
```

> **权限判定顺序，第一个命中即停：**
>
> 1. 同租户成员 → 按角色（`owner` / `editor` 可编辑，`runner` 可运行，`viewer` 只读）
> 2. 资源授权（`iam_grants`） → 按授权的角色
> 3. 其他 → 拒绝
>
> **Agent 在成员角色之前多一级**：`definition_agents.owner_id`（负责人）对该 Agent 有全权（编辑 / 发布 / 运行 / 转移归属）。
> **定义域里只有 `agent` 这一 kind 有负责人。** Tool 与 Skill 是共享能力，没有归属人，从成员角色开始判定。
> `owner_id` 是负责人（可转移，人走了要交接），与 `created_by`（谁最早建的，事实记录）不是一回事。

---

## 3. 审计域 `audit_`

**独立领域，不属于 iam。** 它的变更驱动是合规要求，与组织调整无关；且它被**所有**领域写入。

```sql
CREATE TABLE audit_logs (
  id            BIGSERIAL PRIMARY KEY,
  tenant_id     UUID NOT NULL,
  actor_id      UUID,
  action        TEXT NOT NULL,
  resource_kind TEXT,
  resource_id   UUID,
  before        JSONB,
  after         JSONB,
  at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_logs_resource ON audit_logs (resource_kind, resource_id, at DESC);
CREATE INDEX idx_audit_logs_tenant   ON audit_logs (tenant_id, at DESC);
```

> 配置不经 Git 管理后，**审计日志是唯一的变更追溯手段**，不是可选项。
> 本表**只追加**：不允许 `UPDATE`，不允许 `DELETE`（由程序保证，见 7.3）。

---

## 4. 定义域 `definition_`

**Agent、Tool、Skill 是同一类事物：声明式的、可版本化的、定义一次可复用多次的蓝图。** 它们同属一个领域，各建一张表 —— 结构迥异，但变更驱动一致：都是"改定义"。

三类定义的关系：**Agent 是组合者** —— 绑定 Tool、绑定 Skill、可委派给其他 Agent；**Tool 与 Skill 是被组合的共享能力**，不归属于任何 Agent。

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
  workspace_id       UUID,
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
  workspace_id   UUID,
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

CREATE TABLE definition_skill_assets (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  skill_id    UUID NOT NULL,                    -- ref: definition_skills.id
  path        TEXT NOT NULL,
  mime_type   TEXT,
  size_bytes  INTEGER NOT NULL,
  storage_ref TEXT NOT NULL,                    -- object storage key
  UNIQUE (skill_id, path)
);
```

> `content_md` 设大小上限（建议 256 KB）。超出部分应拆分为 `definition_skill_assets`。
> `input_schema` 必须是合法 JSON Schema，由程序校验 —— 没有数据库约束兜底。

### 4.3 Agent `definition_agents`

聚合根是 `definition_agents`。绑定关系（挂载哪些工具、哪些技能、委派给哪些子 Agent）是它的值对象集合，全部限定在**同一聚合**内，因此绑定表都带 `agent_id` 且生命周期随 Agent。

```sql
CREATE TABLE definition_agents (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL,
  workspace_id    UUID,
  name            TEXT NOT NULL,
  description     TEXT,
  owner_id        UUID NOT NULL,                -- ref: external identity; the responsible user
  model_provider  TEXT NOT NULL,
  model_name      TEXT NOT NULL,
  temperature     NUMERIC(3,2),
  max_tokens      INTEGER,
  instructions    TEXT,
  max_steps       INTEGER NOT NULL DEFAULT 30,   -- ReAct loop iteration cap
  timeout_seconds INTEGER NOT NULL DEFAULT 600,
  token_budget    INTEGER,
  lock_version    INTEGER NOT NULL DEFAULT 0,
  created_by      UUID,
  updated_by      UUID,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, name)
);

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

CREATE TABLE definition_delegations (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id             UUID NOT NULL,           -- ref: definition_agents.id (parent)
  delegate_agent_id    UUID NOT NULL,           -- ref: definition_agents.id (child)
  expose_as            TEXT NOT NULL,           -- tool name visible to the parent LLM
  description_override TEXT,
  ord                  INTEGER NOT NULL DEFAULT 0,
  UNIQUE (agent_id, expose_as),
  CHECK (agent_id <> delegate_agent_id)
);

CREATE INDEX idx_definition_delegations_delegate ON definition_delegations (delegate_agent_id);
```

> **委派可能成环**（A 调 B、B 调 A）。没有外键，数据库不提供任何保护 —— **环检测是应用层的强制校验**，在保存与编译时各做一次。
> `max_steps` 是 ReAct 循环的迭代上限，必须有值：没有它，一个陷入循环的 Agent 会一直烧 token。

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
  current_version_id UUID,                      -- ref: release_versions.id
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (entity_kind, entity_id)
);

CREATE INDEX idx_release_artifacts_tenant ON release_artifacts (tenant_id, status);

CREATE TABLE release_versions (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  artifact_id  UUID NOT NULL,                   -- ref: release_artifacts.id
  version      INTEGER NOT NULL,
  spec         JSONB NOT NULL,                  -- full snapshot, serialized
  changelog    TEXT,
  published_by UUID,
  published_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (artifact_id, version)
);

CREATE INDEX idx_release_versions_artifact ON release_versions (artifact_id, version DESC);
```

**职责边界要说清楚：**

- `release_artifacts` 只做**发布身份**。name、description、labels 这些留在各领域自己持有 —— 它不是"资源主表"的翻版。
- `entity_kind` + `entity_id` 是指向各领域实体的**多态引用**。这是领域间依赖，方向是 `release → definition`。
- 草稿内容的并发控制（`lock_version`）留在各领域实体上，因为那是**编辑**的并发，不是**发布**的并发。

> `release_artifacts` 与 `release_versions` 互相引用，发布时需在同一事务内先插版本再回填 `current_version_id`。

---

## 6. 执行域 `execution_`

```sql
CREATE TABLE execution_sessions (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        UUID NOT NULL,
  artifact_id      UUID NOT NULL,               -- ref: release_artifacts.id
  artifact_version INTEGER NOT NULL,            -- session is pinned to a published version
  user_id          UUID,
  state            JSONB,                       -- LangGraph checkpointer state
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_execution_sessions_artifact ON execution_sessions (artifact_id, artifact_version);

CREATE TABLE execution_runs (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        UUID NOT NULL,
  artifact_id      UUID NOT NULL,               -- ref: release_artifacts.id
  artifact_version INTEGER NOT NULL,            -- run is pinned to a published version
  session_id       UUID,                        -- ref: execution_sessions.id
  status           TEXT NOT NULL
                   CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
  input            JSONB,
  output           JSONB,
  error            JSONB,
  token_usage      JSONB,
  cost_usd         NUMERIC(12,6),
  triggered_by     UUID,
  started_at       TIMESTAMPTZ,
  finished_at      TIMESTAMPTZ
);

CREATE INDEX idx_execution_runs_artifact ON execution_runs (artifact_id, started_at DESC);
CREATE INDEX idx_execution_runs_session  ON execution_runs (session_id);
CREATE INDEX idx_execution_runs_active   ON execution_runs (status) WHERE status IN ('queued','running');

CREATE TABLE execution_steps (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id         UUID NOT NULL,                 -- ref: execution_runs.id
  parent_step_id UUID,                          -- ref: execution_steps.id
  seq            INTEGER NOT NULL,
  step_kind      TEXT NOT NULL
                 CHECK (step_kind IN ('llm','tool','skill_load','subagent')),
  name           TEXT,
  input          JSONB,
  output         JSONB,
  token_usage    JSONB,
  latency_ms     INTEGER,
  status         TEXT,
  started_at     TIMESTAMPTZ,
  finished_at    TIMESTAMPTZ
);

CREATE INDEX idx_execution_steps_run ON execution_steps (run_id, seq);
```

> **执行域是唯一允许被物理删除的领域**（删除顺序见 7.3）。其余领域一律软删除。
> **会话与 Run 都绑定 `release_artifacts.id` + 版本号，不绑定 `definition_agents` 里的草稿。** 整个执行域不引用定义域，只引用发布域 —— 这既保证了历史执行可精确复现，也让依赖图保持严格线性。代价是未发布的草稿跑不起来（见主文档待定项 10）。
> `step_kind` 不含 `condition`：v1 只用 ReAct 循环，没有显式图编排的条件节点。

---

## 7. 引用完整性（程序控制）

没有数据库外键兜底，以下四道防线**缺一不可**。

### 7.1 引用清单

实现校验逻辑时的完整清单。**领域内引用**与**跨领域引用**分开标注 —— 跨领域引用必须显式承认依赖方向。

**领域内引用：**

| 引用方 | 引用列 | 目标 |
|---|---|---|
| `iam_workspaces` | `tenant_id` | `iam_tenants.id` |
| `iam_members` | `tenant_id` / `workspace_id` | `iam_tenants.id` / `iam_workspaces.id` |
| `definition_skill_assets` | `skill_id` | `definition_skills.id` |
| `definition_tool_bindings` | `agent_id` | `definition_agents.id` |
| `definition_tool_bindings` | `tool_id` | `definition_tools.id` |
| `definition_skill_bindings` | `agent_id` | `definition_agents.id` |
| `definition_skill_bindings` | `skill_id` | `definition_skills.id` |
| `definition_delegations` | `agent_id` / `delegate_agent_id` | `definition_agents.id` |
| `release_artifacts` | `current_version_id` | `release_versions.id` |
| `release_versions` | `artifact_id` | `release_artifacts.id` |
| `execution_runs` | `session_id` | `execution_sessions.id` |
| `execution_steps` | `run_id` / `parent_step_id` | `execution_runs.id` / `execution_steps.id` |

**跨领域引用：**

| 引用方 | 引用列 | 目标领域 | 目标 |
|---|---|---|---|
| `definition_agents` | `owner_id` | 外部 | 身份系统（SSO）中的用户 |
| `release_artifacts` | `entity_id` | 由 `entity_kind` 决定 | 对应领域的实体 |
| `execution_sessions` | `artifact_id` | release | `release_artifacts.id` |
| `execution_runs` | `artifact_id` | release | `release_artifacts.id` |
| `iam_grants` | `resource_id` | 由 `resource_kind` 决定 | 对应领域的实体 |

### 7.2 写入前校验

**所有写入必须在同一个事务内完成校验与落库。**

校验顺序（任一失败即回滚）：

1. **存在性** —— 每个引用列的目标行存在且未被归档。
2. **领域与类型** —— 目标实体的种类与预期一致。多态引用（`entity_kind` / `resource_kind`）必须校验 kind 与 id 指向的实体**确实属于该种类**。
3. **租户一致性** —— 被引用实体与引用方的 `tenant_id` 相同。跨租户引用一律拒绝，不做例外。
4. **可见性** —— 当前操作者对被引用资源有 `read` 权限。
5. **聚合内不变式** —— 委派环检测（`definition_delegations`）。
6. **跨领域依赖方向** —— 只允许第 1.1 节定义的方向。例如 `execution` 不可引用 `definition`：运行只认已发布的版本，不认草稿。

**并发竞态**：校验通过到写入落库之间存在窗口，期间另一事务可能归档被引用的实体。处理方式：

- 校验查询对被引用行使用 `SELECT ... FOR SHARE`，与可能归档它的事务互斥。
- 归档是 `UPDATE`（软删除），不删行，因此最坏情况是引用到一个刚被归档的实体 —— 由第 1 条拦截；运行时另有兜底：编译期再次校验引用可用性。

### 7.3 删除规则

**除执行域与审计域外，一律软删除。** 实体的删除 = `status = 'archived'`（或 `revoked`），行保留。这同时消除了大部分悬空引用风险 —— 引用目标永远存在，只是被标记不可用。

**审计域是只追加的**：`audit_logs` 不允许 `UPDATE`，不允许 `DELETE`，由程序保证。

物理删除仅允许通过专用运维任务执行，且必须：

1. 调用 `find_referrers(kind, resource_id)` 按第 7.1 节清单逐列反查，列出所有引用方。
2. 若存在活跃引用，默认拒绝并返回引用清单。
3. 强制删除时，须在同一事务内按依赖顺序**先删引用方行**，再删实体行。

**级联删除顺序**（按领域依赖方向反向执行）：

```
iam_grants
  → definition_tool_bindings / definition_skill_bindings / definition_delegations
  → definition_skill_assets
  → release_versions
  → release_artifacts
  → definition_agents / definition_tools / definition_skills
  → iam_members / iam_workspaces / iam_tenants
```

执行域例外：`execution_steps` / `execution_runs` / `execution_sessions` 允许按保留策略物理删除，但**删除 `execution_sessions` 前必须先将引用它的 `execution_runs.session_id` 置空**。

### 7.4 孤儿巡检

没有 `CASCADE`，孤儿行不会被自动清理，必须由定时任务兜底。

**建议每 6 小时跑一次**，按 7.1 清单逐条执行反连接检测。示例：

```sql
-- Orphan tool bindings pointing at a missing tool
SELECT b.id, b.agent_id, b.tool_id
FROM definition_tool_bindings b
LEFT JOIN definition_tools t ON t.id = b.tool_id
WHERE t.id IS NULL;
```

其余引用列按同模式展开。发现孤儿时：

- 记录到 `audit_logs`（`action = 'orphan_detected'`）并告警。
- **不自动删除。** 孤儿数据往往意味着上游有 bug，自动清理会销毁证据。由人工确认后再清理。

**多态引用的巡检要点**：`entity_kind` / `resource_kind` 必须参与 join 条件，否则会误报。例如：

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
3. 跨环境的 ID 翻译在边界完成：导出时把引用渲染为 `{domain, kind, name}` 业务标识，导入时解析回目标环境的 UUID。
4. 任一失败则整个导入回滚，返回失败项与原因，不留部分写入。

---

## 8. 读模型

没有共享父表，因此"列出某租户下所有可发布物"这类跨领域查询没有一张表可查。**这类查询走读模型，不污染领域模型。**

- 读模型是**投影**，由领域写操作驱动更新，可以是物化视图或独立表（用 `read_model_` 前缀与领域表区分）。
- 典型投影：`read_model_catalog` —— 汇总 `(tenant_id, domain, kind, name, description, status, latest_version, updated_at)`，供 UI 的列表页与搜索使用。
- 读模型**不参与业务写入**，可随时从领域表重建。重建脚本是它存在的必要配套。

> 用读模型而非共享父表，是 CQRS 的基本取舍：写入侧保持领域纯净，读取侧为查询便利做冗余。代价是投影有延迟，且需要在每次领域写入后触发更新。

---

## 9. 与主文档的关系

- **领域划分的理由、依赖方向、聚合边界**见 [agent-platform-design.md](./agent-platform-design.md) 第 3 节。
- 本文档描述**物理结构**与**完整性约束的实现要求**。
- 任何新增引用列，都必须同时更新 7.1 的引用清单与 7.4 的巡检查询。这是硬性要求。
