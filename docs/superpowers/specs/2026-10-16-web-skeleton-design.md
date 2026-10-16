# web 骨架 + 登录设计（单元 11）

- 状态：草稿（待评审）
- 日期：2026-10-16
- 范围：config UI 的工程骨架、布局壳与登录链路；不含任何领域页面
- 关联：[2026-10-16-config-skeleton-design.md](./2026-10-16-config-skeleton-design.md)（auth API 契约）、[11-roadmap.md](../../11-roadmap.md) 单元 11

## 1. 意图

配置 UI 随领域单元前后端一起交付（阶段 C 的交付模型）。本单元先立 web 工程与布局壳，把已定前端栈落成实物，并用真实浏览器第一次验证单元 10 的横切机制（cookie 会话、过期、多端、切换团队）。此后每个领域单元在本壳内点亮自己的导航项与页面。

## 2. 范围

**做：**

- Vite 工程：Vue 3.5 + Vuetify 4 + TypeScript + TanStack Query（vue-query），pnpm 管理。
- 布局壳：左侧分组导航栏（可折叠）+ 顶栏（面包屑、用户菜单）+ 内容区；紫色品牌主题（参照 `#8B2FC9 → #C03FD8` 基调）。
- 登录页：邮箱 + 口令，失败提示（后端 401 的统一文案），登录后进配置首页占位。
- auth 接线：login / logout / GET session / switch-team；会话状态用 TanStack Query；任何 401 统一跳登录页。
- 团队切换：顶栏用户菜单内，列出所属团队。
- 生产构建：多阶段 Dockerfile（node 阶段 `pnpm build`，nginx 阶段服务 `dist/`）；web 容器从「挂载静态目录」改为自构建镜像；边缘 nginx 对 web 上游加 `try_files` 回退 `index.html`（history 路由）。
- 开发态：`pnpm dev`，Vite proxy 把 `/api` 转发到本机边缘 `http://localhost:8080`。
- 验证：浏览器自动化冒烟——登录 → 会话 → 切换团队 → 登出；cookie 过期后跳登录页。

**不做：** 领域页面与导航项（各领域单元点亮）、i18n、暗色主题、组件单测（vitest 待第一个含真实逻辑的单元引入）。

## 3. 已定决策

| 决策点 | 结论 |
|---|---|
| 技术栈 | Vue 3.5 + Vuetify 4 + Vite + TypeScript + TanStack Query；pnpm |
| 路由 | HTML5 history；边缘 nginx 对 web 上游 `try_files` 回退 |
| 开发态 | Vite dev server，`/api` 代理到本机边缘（同源，无 CORS） |
| 生产构建 | 多阶段 Dockerfile（node 构建、nginx 服务静态产物）；部署栈无 node 运行时 |
| 验证门槛 | 浏览器自动化冒烟（browser-use），本单元不引入 vitest |
| 壳内容 | 配置首页占位（欢迎卡 + 版本信息）；侧栏只显示已点亮项，本单元仅有首页 |
| 文案 | 全中文；语言切换不做 |
| 主题 | 浅色 + 紫色主色；暗色不做 |

## 4. 约束

- 前端源码在 `web/`，与后端仓库同库；UI 文案中文、代码注释英文（AGENTS.md）。
- 不改 `/api/*` 后端契约；冒烟中发现的契约问题回 config 侧修。
- 浏览器冒烟只打本地 compose 栈。
- Vuetify 4 当前大版本（4.x）取最新稳定小版本。

## 5. 留给实现计划

工程文件清单与脚手架生成方式、Vuetify 主题具体色值、路由表与组件拆分、`api client` 封装形状（fetch 包装 + 错误信封解析）、compose 与 web Dockerfile 改造细节、冒烟脚本的操作清单。
