# Config Skeleton (auth / RBAC / audit) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the config service's API skeleton — account tables, cookie login sessions with live-read RBAC, same-transaction audit append, `/api` routing, nginx edge wiring — so every later domain unit only adds tables plus a router file.

**Architecture:** Four new tables in `shared/models.py` (`iam_accounts`, `iam_memberships`, `iam_sessions`, `audit_entries`), all written solely by config. `config/auth.py` resolves the session cookie into an `AuthContext` (account + current tenant + role, role re-read live from `memberships` on every request) and exposes `require_account` / `require_role(minimum)` dependencies with the manager error envelope. `config/routers/auth.py` carries login / logout / session / switch-team; every write and auth event appends an `audit_entries` row inside the same DB transaction via `config/audit.py`. Admin bootstrap extends the existing seed; nginx gains `/api/` → config.

**Tech Stack:** Python 3.13 / FastAPI / SQLAlchemy (existing); new dependency `argon2-cffi` (Argon2id).

**Spec:** docs/superpowers/specs/2026-10-16-config-skeleton-design.md (all decisions in its §3 table); docs/03-domain-model.md §2.5/2.6 and §4.7 rule 7; docs/10-permissions.md §1.

## Global Constraints

- Passwords stored only as Argon2id encoded strings; session tokens only as sha256 hex (64 chars). Plaintext never in DB rows, logs, or responses (design §3).
- Session cookie: name `agentops_session`, HttpOnly, SameSite=Lax, Path=/, Max-Age 7d; no Secure flag while the v1 edge serves plain HTTP.
- RBAC is live-read: every authenticated request re-reads `memberships`; role change or removal takes effect without re-login (design §3).
- Auth/permission errors speak the manager error envelope `{"error": {"code", "message"}}`.
- Tests only touch the `*_test` database (root conftest enforces); comments and logs in English.
- No domain endpoints this unit: `routers/` holds only `auth.py`.

## Review Focus

1. **Credentials never plaintext** — stored `password_hash` starts with `$argon2id$` and raw token appears nowhere but the cookie. Pinned in Task 2 (encoding) and Task 5 (row assertions after login).
2. **Dead sessions stay dead** — logout deletes the row and the old cookie gets 401; expired sessions rejected. Pinned in Task 5 (logout) and Task 4 (expiry).
3. **Membership changes are immediate** — deleting the membership downgrades to "no current team" (403 on role routes, still 200 on account routes); promoting the role opens access without re-login. Pinned in Task 4.
4. **CSRF: foreign Origin on unsafe methods rejected, Origin-less clients pass** — `Origin: http://evil.example` POST → 403; curl (no Origin) passes. Pinned in Task 5.
5. **Seed never overwrites accounts** — with any account already present the seed skips admin creation entirely, so rotating `SEED_ADMIN_PASSWORD` cannot take over. Pinned in Task 6.

---

### Task 1: shared — account, membership, session, audit tables

**Files:**
- Modify: `shared/src/shared/models.py`
- Modify: `config/src/config/app.py` (CONFIG_TABLES)
- Test: `config/tests/test_schema.py` (extend)

**Interfaces:**
- Produces: `Account` (`iam_accounts`): `id` uuid pk, `email` str unique, `password_hash` str(255), `created_at` tz now.
- Produces: `Membership` (`iam_memberships`): unique (`account_id`, `tenant_id`); `id` uuid pk, `account_id` uuid, `tenant_id` uuid, `role` str(16) (`viewer|editor|admin`), `created_at`.
- Produces: `AuthSession` (`iam_sessions`): `id` uuid pk, `token_hash` str(64) unique indexed, `account_id` uuid, `current_tenant_id` uuid nullable, `expires_at` tz, `created_at`.
- Produces: `AuditEntry` (`audit_entries`): `id` uuid pk, `actor_account_id` uuid nullable, `tenant_id` uuid nullable, `action` str(64), `subject_type` str(32) nullable, `subject_id` str(64) nullable, `detail` JSONB nullable, `created_at`.
- Produces: `CONFIG_TABLES` extended with the four tables (config stays their sole writer; `iam_tenants` already there).

- [ ] **Step 1: Failing tests** — each new table accepts a round-trip insert/query; duplicate email rejected; duplicate (`account_id`, `tenant_id`) rejected.
- [ ] **Step 2: RED** → **Step 3: implement models + CONFIG_TABLES** → **Step 4: GREEN + config suite** → **Step 5: Commit** — `feat(shared): account, membership, session and audit tables`

### Task 2: config — Argon2id password hashing

**Files:**
- Create: `config/src/config/security.py`
- Modify: `config/requirements.txt` (+`argon2-cffi`; `pip install -r config/requirements.txt` into the dev venv)
- Test: `config/tests/test_security.py`

**Interfaces:**
- Produces: `hash_password(password: str) -> str` (Argon2 `PasswordHasher` encoded string).
- Produces: `verify_password(password: str, hashed: str) -> bool`.

- [ ] **Step 1: Failing tests** — round-trip True; wrong password False; encoded string starts with `$argon2id$` and contains no plaintext (Review Focus 1).
- [ ] **Step 2: RED** → **Step 3: implement** → **Step 4: GREEN** → **Step 5: Commit** — `feat(config): argon2id password hashing`

### Task 3: config — same-transaction audit append

**Files:**
- Create: `config/src/config/audit.py`
- Test: `config/tests/test_audit.py`

**Interfaces:**
- Produces: `record_audit(session: Session, *, actor_account_id: UUID | None, tenant_id: UUID | None, action: str, subject_type: str | None = None, subject_id: str | None = None, detail: dict | None = None) -> None` — adds the row on the caller's session; commits and rollbacks ride the caller's transaction.

- [ ] **Step 1: Failing tests** — row visible after the caller's commit; caller rollback drops the row (same-transaction semantics pinned).
- [ ] **Step 2: RED** → **Step 3: implement** → **Step 4: GREEN** → **Step 5: Commit** — `feat(config): same-transaction audit append`

### Task 4: config — session resolution and RBAC dependencies

**Files:**
- Create: `config/src/config/auth.py`
- Create: `config/src/config/errors.py` (`ApiError`, same shape as manager's)
- Test: `config/tests/test_rbac.py`

**Interfaces:**
- Produces: `SESSION_COOKIE = "agentops_session"`, `SESSION_TTL = timedelta(days=7)`.
- Produces: `role_rank(role: str) -> int` (viewer 0, editor 1, admin 2; anything else raises `ValueError`).
- Produces: `create_auth_session(session: Session, account_id: UUID, current_tenant_id: UUID | None) -> str` — returns the raw token once; stores sha256 hex and `expires_at = now + SESSION_TTL`.
- Produces: `AuthContext` dataclass: `account: Account`, `tenant_id: UUID | None`, `role: str | None`.
- Produces: `resolve_auth(session: Session, token: str | None) -> AuthContext` — unknown/expired → `ApiError(401, "auth", ...)`; on success slides `expires_at` (write flushed by caller) and live-reads the membership for `current_tenant_id` (no membership → `role=None`, `tenant_id=None`).
- Produces: `require_account` (FastAPI dependency `Request -> AuthContext`, reads the cookie) and `require_role(minimum: str)` dependency factory — no cookie/invalid/expired → 401 `auth`; authenticated but no current team → 403 `no_team`; `role_rank(role) < role_rank(minimum)` → 403 `forbidden`.

- [ ] **Step 1: Failing tests** — a scratch `FastAPI()` app mounts synthetic routes `Depends(require_account)` and `Depends(require_role("editor"))` (the design's synthetic-route acceptance):
  - no cookie / garbage token / expired session → 401 (Review Focus 2).
  - viewer on editor route → 403 `forbidden`; editor and admin → 200, response carries the live role.
  - membership deleted after login → account route still 200 with `role=None`, editor route → 403 `no_team` (Review Focus 3).
  - role changed viewer→admin directly in the DB → editor route 200 without re-login (Review Focus 3).
- [ ] **Step 2: RED** → **Step 3: implement** → **Step 4: GREEN** → **Step 5: Commit** — `feat(config): session resolution and rbac dependencies`

### Task 5: config — auth endpoints and app wiring

**Files:**
- Create: `config/src/config/routers/__init__.py`, `config/src/config/routers/auth.py`
- Modify: `config/src/config/app.py` — include the router, register `ApiError` + `RequestValidationError` handlers (manager envelope), add the origin guard middleware.
- Test: `config/tests/test_auth_api.py`

**Interfaces:**
- Consumes: Tasks 1–4 (`create_auth_session`, `resolve_auth`, `require_account`, `record_audit`, `hash_password`, `verify_password`).
- Produces: `GET /api/auth/session` → 200 `{account: {id, email}, teams: [{tenant_id, tenant_name, role}], current_tenant_id}`; re-sets the cookie (sliding Max-Age); 401 without a valid session.
- Produces: `POST /api/auth/login` `{email, password}` → 200 same shape + Set-Cookie; default `current_tenant_id` = earliest-created membership (my call); 401 `invalid_credentials` with a generic message (same for unknown email and wrong password); audits `auth.login` (success) and `auth.login_failed` (actor None, attempted email in `detail`).
- Produces: `POST /api/auth/logout` → 204; deletes the session row, expires the cookie; audits `auth.logout`.
- Produces: `POST /api/auth/switch-team` `{tenant_id}` → 200 updated shape; not a member → 403 `forbidden`; audits `auth.switch_team`.
- Produces: origin guard middleware — unsafe methods carrying an `Origin` whose host differs from the request Host → 403 `origin`; absent Origin passes.

- [ ] **Step 1: Failing tests** (TestClient with a cookie jar):
  - login ok: cookie flags HttpOnly + SameSite=Lax + Max-Age 604800; teams and `current_tenant_id` correct; `auth.login` audit row committed; DB `password_hash` starts `$argon2id$`, `token_hash` is 64-hex and ≠ cookie value (Review Focus 1).
  - wrong password and unknown email → the same 401 body (no enumeration); `auth.login_failed` row per failure.
  - GET session with cookie → 200; without → 401; logout → 204 and the old cookie now 401s (Review Focus 2).
  - switch-team to a member tenant → 200; to a foreign tenant → 403.
  - a second login yields a second concurrent valid session (multi-device by design); logout of one leaves the other working.
  - POST with `Origin: http://evil.example` → 403 `origin`; same POST without Origin → passes (Review Focus 4).
- [ ] **Step 2: RED** → **Step 3: implement** → **Step 4: GREEN + config suite** → **Step 5: Commit** — `feat(config): auth endpoints with cookie sessions`

### Task 6: seed — bootstrap admin

**Files:**
- Modify: `config/src/config/seed.py`, `config/src/config/seed.py::__main__`
- Test: `config/tests/test_seed.py` (extend)

**Interfaces:**
- Produces: `seed(engine, key: str, admin_email: str, admin_password: str) -> SeedResult` — existing tenant/agent/artifact logic unchanged; after them, if no `Account` row exists at all: create the account (Argon2id hash) and its admin membership on the default tenant; `SeedResult` gains `admin_created: bool`. Any existing account → skip admin creation entirely (Review Focus 5).
- Produces: `__main__` reads `SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD`.

- [ ] **Step 1: Failing tests** — fresh seed creates account + admin membership and `verify_password` passes; re-seed idempotent (`admin_created=False`); with a pre-existing unrelated account the admin is not created even with different env values.
- [ ] **Step 2: RED** → **Step 3: implement** → **Step 4: GREEN** → **Step 5: Commit** — `feat(config): seed bootstrap admin account`

### Task 7: edge, compose, docs, final review

**Files:**
- Modify: `deploy/nginx/default.conf` — `location /api/ { proxy_pass http://config:8000; proxy_set_header Host $host; }`
- Modify: `deploy/docker-compose.yml` — config service env `SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD`; nginx `depends_on` gains config.
- Modify: `docs/11-roadmap.md` — swap units 10/11 (skeleton done first, iam domain follows with the three-role acceptance), mark the skeleton unit complete.

- [ ] **Step 1: Deploy smoke** — rebuild the local stack (`docker compose up -d --build`); through the edge: `POST /api/auth/login` with the seed admin → cookie jar → `GET /api/auth/session` 200 → `POST /api/auth/logout` 204; `GET /health` 200; `/v1/` SDK behavior unchanged (nginx restart included — DNS cache).
- [ ] **Step 2: Full verification** — bare `pytest` (all suites) and `ruff check .` green.
- [ ] **Step 3: docs + final review** — roadmap update; dispatch the review subagent (AGENTS.md 协作规范); commit — `feat(deploy): route /api/ to config; docs: config skeleton unit complete`
