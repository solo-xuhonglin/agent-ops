# Web UI Design-System Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the confirmed B′ design system (institutional skeleton × light skin, full-bleed white workspace, purple split login) from the approved HTML draft into the Vuetify app, with a pinned 4px spacing rhythm.

**Architecture:** Design tokens land as CSS custom properties in `web/src/style.css` (+ Vuetify theme carrying primary/secondary/background/surface/border). `AppLayout.vue` restyles drawer/app-bar/content to the white-surface shell (active nav = 3px left accent bar + soft tint, MDI outline icons); `LoginPage.vue` becomes the split brand panel; `HomePage.vue` follows the pagehead rhythm. No behavior or `/api/*` changes.

**Tech Stack:** existing Vite/Vue/Vuetify stack only; no new deps.

**Spec:** docs/superpowers/specs/2026-10-16-web-ui-design-system.md (tokens §2, spacing §3, porting contract §4 — binding).

## Global Constraints

- Tokens via CSS variables; components must not hardcode hex values (spec §4).
- Zero shadows; hairline `#E7E7EC` only; radius 4px; serif display 19px; micro labels 10px uppercase .18em; mono 12px numerals.
- Spacing on the 4px grid per spec §3 (page rhythm 20/16, blocks 24, fields 16, cells 16 horizontal).
- No behavior changes: auth flow, guards, API calls untouched; login smoke must stay green.
- Gates per task: `pnpm --dir web build` green; final: browser smoke through the edge + full `pytest -q` + ruff.

## Review Focus

1. **Auth regression** — login wrong-copy identical, 401 redirect, logout, team switch still work post-restyle. Pinned in Task 4 smoke.
2. **Full-bleed white workspace** — no gray canvas showing around content; hairlines carry structure. Pinned in Task 2 screenshot.
3. **Spacing rhythm consistent** — pagehead→20px→toolbar→16px→table; blocks 24px; fields 16px (spec §3). Pinned in Task 2/3 screenshots.
4. **Single purple accent** — purple only on active nav, primary button, role chip, brand panel; nowhere else. Pinned in Task 2/3 screenshots.
5. **Narrow viewport sanity** — 1024px width: no overlap, drawer collapses, table scrolls. Pinned in Task 4 smoke.

---

### Task 1: tokens — global CSS + Vuetify theme

**Files:**
- Modify: `web/src/style.css` (custom properties + `.display-serif` / `.micro-label` / `.num` helpers + base rhythm)
- Modify: `web/src/plugins/vuetify.ts` (theme colors incl. border)

**Interfaces:**
- Produces: `--hairline`, `--hairline-strong`, `--ink`, `--ink-2`, `--accent`, `--accent-soft`, `--canvas`, `--brand-panel`, `--font-serif`, `--font-mono`, spacing vars `--sp-1..--sp-8` (4/8/12/16/20/24/32).

- [ ] **Step 1: Implement tokens/helpers** per spec §2–§3. **Step 2: Verify** `pnpm --dir web build`. **Step 3: Commit** — `feat(web): b-prime design tokens and global styles`

### Task 2: shell + home — white-surface workspace

**Files:**
- Modify: `web/src/layouts/AppLayout.vue`, `web/src/pages/HomePage.vue`

**Interfaces:**
- Consumes: Task 1 tokens.
- Produces: shell per draft — white drawer (brand serif wordmark + 配置中心 micro label; nav MDI outline icons; active = inset 3px accent bar + `--accent-soft`), white app-bar (toggle, breadcrumb, team tag, user chip), full-bleed white main; pagehead rhythm (serif h1 + muted count).

- [ ] **Step 1: Implement** per draft; spacing exactly per spec §3. **Step 2: Verify** build + dev-server screenshots (workspace, active nav, home). **Step 3: Commit** — `feat(web): institutional shell with full-bleed white workspace`

### Task 3: login — split brand panel

**Files:**
- Modify: `web/src/pages/LoginPage.vue`

**Interfaces:**
- Consumes: Task 1 tokens; existing `useLogin` wiring unchanged.

- [ ] **Step 1: Implement** split screen: left `--brand-panel` with serif wordmark + letterspaced 配置中心 + hairline-separated description; right `--canvas` with `NO. 01 — ACCESS` micro label, serif 登录, underlined fields (16px gaps), block ink button → hover accent; fixed error copy unchanged.
- [ ] **Step 2: Verify** build + dev screenshots (login, error state). **Step 3: Commit** — `feat(web): split-panel brand login`

### Task 4: smoke, gates, review

- [ ] **Step 1: Rebuild web image + restart nginx**; edge smoke at :8080 — login (wrong copy identical, success), home, team menu, logout, 401-redirect; 1024px sanity screenshot; zero console errors.
- [ ] **Step 2: Full gates** — bare `pytest -q`, `ruff check .`, `pnpm --dir web build`.
- [ ] **Step 3: Final review subagent** (AGENTS.md), one fix pass; roadmap/memory closeout; commit — `docs: web ui design system ported (unit 11 addendum)`
