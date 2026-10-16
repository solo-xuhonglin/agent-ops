# Web Skeleton + Login (Unit 11) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `web/` from a static placeholder into the config UI's Vite project — purple-themed layout shell plus a login flow wired to the real `/api/auth/*` — so later domain units only light up their own nav item and page inside this shell.

**Architecture:** Vue 3.5 + Vuetify 4 + TypeScript + TanStack Query (vue-query), pnpm, HTML5 history routing. `web/src/api/` holds a fetch wrapper (error-envelope parsing, 401 semantics) and typed auth calls; session state is a single TanStack Query; the layout shell (drawer + app bar + content) mounts pages from `web/src/pages/`. Production ships a multi-stage Docker image (node builds `dist/`, nginx serves it with a `try_files` history fallback); the edge nginx stays a pure proxy.

**Tech Stack:** Vite, Vue 3.5, Vuetify 4 (latest stable 4.x), @tanstack/vue-query, vue-router 4, TypeScript, pnpm; docker multi-stage (node:22-alpine → nginx:1.29-alpine).

**Spec:** docs/superpowers/specs/2026-10-16-web-skeleton-design.md (decisions §3, including the review rulings: try_files lives in the web image's own nginx; home card shows current team & role; no-team is an empty state in the team menu; smoke fixture writes the local dev DB; multi-device is out of this unit's gate).

## Global Constraints

- UI copy in Chinese; code comments in English (AGENTS.md).
- No changes to the `/api/*` contract; contract problems found during smoke go back to config, not patched around in the UI.
- Stack pinned: Vue 3.5 + Vuetify 4.x latest stable + Vite + TypeScript + @tanstack/vue-query; pnpm; history routing.
- Theme: light, `primary: "#8B2FC9"`, `secondary: "#C03FD8"`.
- No vitest / component tests this unit; the gates are `pnpm build` (type-check + build) and browser smoke against the local compose stack only.
- The second team for switch-team smoke comes from a one-off fixture (`docker compose exec` + `shared.models`) writing the local dev DB — never the test DB, never a remote host.

## Review Focus (each pinned by the browser smoke in Task 3/4)

1. **Session death redirects, no loops** — any page visited after logout/expired cookie lands on `/login`; the login page itself never triggers the guard redirect. Pinned in Task 3 smoke.
2. **No credential enumeration in copy** — wrong password and unknown email show the same message 「邮箱或口令不正确」; the UI never distinguishes them. Pinned in Task 3 smoke.
3. **History routing survives refresh** — deep link/refresh on `/login` or any route serves `index.html`, not nginx 404 (web image `try_files`). Pinned in Task 4 smoke (through the edge).
4. **Team switch is immediate and sticky** — after switching, the top bar and home card show the new team/role; a reload keeps it (session remembers). Pinned in Task 4 smoke.
5. **The web image is self-contained** — no host-directory mount; after stack rebuild all static assets resolve (no broken asset 404s). Pinned in Task 4 smoke.

---

### Task 1: Vite project scaffold

**Files:**
- Replace: `web/` (placeholder `index.html` → Vite project: `package.json`, `pnpm-lock.yaml`, `vite.config.ts`, `tsconfig.json`, `index.html`, `src/main.ts`, `src/App.vue` + a placeholder route view)
- Modify: `deploy/docker-compose.yml` — unchanged this task; dev proxy targets the running edge

**Interfaces:**
- Produces: a runnable `pnpm dev` (Vite on 5173, proxy `/api` → `http://localhost:8080`) and `pnpm build` (`vue-tsc -b && vite build`) pipeline that later tasks fill.
- Produces: dependency set `vue`, `vue-router`, `vuetify`, `@mdi/font`, `@tanstack/vue-query`.

- [ ] **Step 1: Scaffold** — `pnpm create vite` (vue-ts template) into `web/`; add deps; wire the Vuetify vite plugin and `@mdi/font`; set the dev proxy and `server.port`; delete the placeholder content.
- [ ] **Step 2: Verify** — `pnpm build` green (type-check + build); `pnpm dev` boots and serves a page at `http://localhost:5173`.
- [ ] **Step 3: Commit** — `feat(web): vite scaffold with vue, vuetify and tanstack query`

### Task 2: Purple theme and the layout shell

**Files:**
- Create: `web/src/plugins/vuetify.ts` (theme), `web/src/layouts/AppLayout.vue`, `web/src/router/index.ts`, `web/src/pages/HomePage.vue` (placeholder card)
- Modify: `web/src/App.vue`, `web/src/main.ts` (register router + Vuetify)

**Interfaces:**
- Produces: routes `/` (AppLayout → HomePage, meta title 配置首页) with the drawer (grouped nav area, rail collapse) and app bar (toggle, breadcrumb from route meta, user menu stub) the next task fills.
- Produces: theme tokens `primary #8B2FC9` / `secondary #C03FD8` applied app-wide.

- [ ] **Step 1: Implement** — shell per spec §2 (drawer + app bar + `v-main` router-view); nav shows only 配置首页； user menu renders a stub item.
- [ ] **Step 2: Verify** — `pnpm build` green; `pnpm dev` renders the shell in the browser (screenshot: drawer, top bar, purple accents on 配置首页).
- [ ] **Step 3: Commit** — `feat(web): app layout shell with purple theme`

### Task 3: Auth wiring and the login page

**Files:**
- Create: `web/src/api/client.ts`, `web/src/api/auth.ts`, `web/src/composables/useSession.ts`, `web/src/pages/LoginPage.vue`
- Modify: `web/src/router/index.ts` (add `/login`, auth guard), `web/src/layouts/AppLayout.vue` (user menu: teams, logout), `web/src/pages/HomePage.vue` (welcome card + current team & role)

**Interfaces:**
- Produces (client.ts): `class ApiClientError extends Error { code: string; status: number }` and `request<T>(path: string, init?: RequestInit): Promise<T>` — same-origin fetch, JSON body, parses the `{"error": {"code","message"}}` envelope into `ApiClientError`, rethrows network errors.
- Produces (auth.ts): `type TeamView = { tenant_id: string; tenant_name: string; role: string }`, `type SessionView = { account: { id: string; email: string }; teams: TeamView[]; current_tenant_id: string | null }`; `login(email, password): Promise<SessionView>`, `logout(): Promise<void>`, `fetchSession(): Promise<SessionView>`, `switchTeam(tenantId: string): Promise<SessionView>` — all via `request` against `/api/auth/*`.
- Produces (useSession.ts): TanStack Query `sessionKey = ['session']`, `useSessionQuery()` (enabled after first navigation), `useLogin()`, `useLogout()`, `useSwitchTeam()` — mutations invalidate `sessionKey`; a 401 from the session query redirects to `/login` (guard + query error handler cooperate; the guard must not loop on `/login` itself).
- Produces: login form with the single failure message 「邮箱或口令不正确」; user menu lists teams (current marked; 「暂无团队」 empty state when none), switches on click, and logs out.

- [ ] **Step 1: Implement** per interfaces above; router guard sends unauthenticated users to `/login`.
- [ ] **Step 2: Verify (browser smoke, dev mode against the local stack)** —
  - visit `/` unauthenticated → lands on `/login`;
  - login with wrong password → 「邮箱或口令不正确」; login with unknown email → the same message (Review Focus 2);
  - login as seed admin → home shows 欢迎 + default 团队/管理员； user menu shows the team;
  - logout → back to `/login`; visiting `/` again stays on `/login` (Review Focus 1);
  - `pnpm build` green.
- [ ] **Step 3: Commit** — `feat(web): login flow and session wiring`

### Task 4: Production image and edge smoke

**Files:**
- Create: `web/Dockerfile`, `web/nginx.conf` (root `dist/`, `try_files $uri /index.html`)
- Modify: `deploy/docker-compose.yml` — `web` builds from `web/Dockerfile`, host-directory volume mount removed

**Interfaces:**
- Consumes: Task 3's auth flow; the running local stack (seed admin; second team via fixture).

- [ ] **Step 1: Implement** the multi-stage image (node:22-alpine `pnpm install --frozen-lockfile && pnpm build` → nginx:1.29-alpine copies `dist/` + `web/nginx.conf`); rewire compose.
- [ ] **Step 2: Fixture** — one-off `docker compose exec config python -c "…"` creating tenant `smoke-team` and an admin membership for the seed admin (idempotent: skip if the tenant exists).
- [ ] **Step 3: Verify (edge smoke at `http://localhost:8080`, full stack rebuilt + nginx restarted)** —
  - refresh/deep-link on `/login` serves the app, not 404 (Review Focus 3);
  - login → home → user menu shows both teams → switch to `smoke-team` → top bar and home card update; reload keeps it (Review Focus 4);
  - logout → `/login`; all static assets loaded (no 404 in devtools) (Review Focus 5);
  - `/v1/agent` still 401s (manager routing untouched).
- [ ] **Step 4: Commit** — `feat(deploy): self-built web image with history fallback`

### Task 5: Docs closeout and final review

- [ ] **Step 1: Full verification** — bare `pytest -q` (backend untouched but proves it), `pnpm build`, `ruff check .` all green.
- [ ] **Step 2: Docs** — roadmap: unit 11 complete; nothing else pending.
- [ ] **Step 3: Final review** — dispatch the review subagent (AGENTS.md 协作规范), one fix pass if needed; commit — `docs: unit 11 complete`
