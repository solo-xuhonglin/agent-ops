# config 骨架设计（横切机制先行）

- 状态：草稿（待评审）
- 日期：2026-10-16
- 范围：config 服务的 API 骨架——登录会话、RBAC、audit、路由组织、边缘接通；不含领域
- 关联：[11-roadmap.md](../../11-roadmap.md)（单元 10/11 对调）、[03-domain-model.md](../../03-domain-model.md) 2.5/2.6、[10-permissions.md](../../10-permissions.md) 第 1 节

## 1. 意图

阶段 C 的七个领域单元（iam、tool/skill、secrets、agent、release、导入导出）都长在 config 服务上。本单元先立骨架：账号与登录会话、RBAC 判定、audit 追加、按领域挂路由的结构、nginx 接入。此后每个领域单元只做「建表 + 挂路由」，不再动横切。路线图上原单元 10（iam 领域）与单元 11（API 骨架）对调，骨架先行；iam 的领域部分（邀请、配额、软删除、成员管理 API）顺延。

## 2. 范围

**做：**

- 最小账号模型：平台级账号（邮箱全平台唯一 + 口令哈希）、成员关系（账号 × 租户 × 三角色）。这是登录与判权绕不开的最小集。
- auth 端点：登录、登出、查当前会话、切换当前团队。
- RBAC：声明式依赖挂在端点上，最低角色语义（viewer < editor < admin）。
- audit：`audit_entries` 表 + 同事务追加 helper，本单元由 auth 的写操作真实使用。
- 路由骨架：统一 `/api` 前缀，`routers/` 按领域一文件，本单元只有 `auth`。
- nginx：新增 `/api/` → config。
- 种子：环境变量引导首个管理员。

**不做：** 领域端点（tool / skill / secrets / agent / release / 导入导出）、邀请流程、配额与用量、软删除、web UI。

## 3. 已定决策

| 决策点 | 结论 |
|---|---|
| 前端栈 | Vue 3.5 + Vuetify 4 + Vite + TypeScript + TanStack Query；主色紫（品牌参照）；布局参照 RustFS 控制台骨架：左侧分组导航栏（可折叠）+ 顶栏（折叠钮、面包屑、语言/主题/用户菜单）+ 内容区（页标题、搜索、主操作、数据表格）。单元 22 落地，本单元 `web/` 仍为静态占位 |
| 会话 | 服务端会话表；令牌为随机串，库中只存哈希；经 HttpOnly Cookie（SameSite=Lax）下发；7 天滑动过期；允许多端并发会话；会话记住当前团队，切换团队即更新会话 |
| RBAC 判定 | 声明式依赖挂端点，最低角色语义（viewer < editor < admin）；判权每请求实时读 `memberships`，角色变更立即生效。本单元以测试内合成路由验证机制，三角色真实允许/拒绝的验收随 iam 单元 |
| 会话失效语义 | 成员被移除立即生效：会话进入「无当前团队」状态，可切换到仍属的团队，一个都不属则需成员关系的端点一律 403；登录成功但账号无任何成员关系时同为该状态 |
| CSRF | SameSite=Lax + 非安全方法校验 Origin 头 |
| 口令哈希 | Argon2id（`argon2-cffi`） |
| 管理员引导 | `SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD`；启动时库中无账号才建：平台账号 + 默认租户的 admin 成员。与 `SEED_AGENT_KEY` 同构 |
| audit | 同事务追加：`record_audit(session, actor, tenant, action, subject, detail)`；所有写操作与登录/登出都记；detail 为端点自定的 JSON |
| 边缘分流 | `/` → web、`/v1/` → manager（均现状不动）；新增 `/api/` → config |

## 4. 约束

- 每表唯一写者：accounts / memberships / sessions / audit_entries（config 侧）均由 config 服务建表与写入（[03-domain-model.md](../../03-domain-model.md) 4.7 规则 7；audit 按设计多写者，本单元只有 config 写）。
- 测试只连 `agentops_test` 库。
- 无外键，schema 变更靠重建数据库（开发期）。

## 5. 留给实现计划

表结构字段（accounts / memberships / sessions / audit_entries）、auth 端点的请求响应形状、统一错误响应格式、会话清理策略（过期行的处理时机）、测试清单。
