# Unit 1: config 接库 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** config 服务连上 Postgres：SQLAlchemy 引擎、启动建表、首表 `iam_tenants`、`/health` 真查库，并立起 pytest 基建。

**Architecture:** 同步 SQLAlchemy 引擎从环境变量 `DATABASE_URL` 构建；无迁移——服务启动时 `Base.metadata.create_all`，开发期改 schema 靠重建数据库。测试在宿主机对独立的 `agentops_test` 库运行，conftest 强制库名以 `_test` 结尾。

**Tech Stack:** Python 3.13, FastAPI / uvicorn, SQLAlchemy 2 + psycopg 3, pytest + httpx。

**Spec:** `docs/11-roadmap.md` 单元 1；`docs/03-domain-model.md` §1.2（表名规则）、§4（实现约定）；`AGENTS.md`（安全红线）。

## Global Constraints

- 表名带领域前缀：首表 `iam_tenants`（03 §1.2）。
- 不用外键、不做增量迁移，启动建表（03 §4.2、§4.7）。
- 测试只连 `*_test` 库，绝不指向主库（AGENTS.md 安全红线）。
- 代码注释与日志用英文；提交信息用英文；ruff line-length 120、双引号、py313。
- config 是 `iam_tenants` 的唯一写者（01 §2.1）。

## Review Focus

1. Postgres 不可达时 `/health` 应回 503 报 `db: unreachable`，而非 500 崩溃 —— Task 2 `test_health_503_when_db_down`。
2. 重复租户名被唯一约束拒绝 —— Task 1 `test_duplicate_tenant_name_rejected`。
3. `created_at` 由数据库默认填充，不由客户端给值 —— Task 1 `test_tenant_round_trip` 断言非空。
4. 测试连接误指主库时立即中止 —— Task 1 conftest 的 `_test` 库名断言。
5. 服务重复启动、重复建表不报错（幂等）—— Task 2 两个测试各自进入 lifespan，隐式覆盖。

---

### Task 1: 数据库模块、首表与 pytest 基建

**Files:**
- Create: `config/src/config/db.py`
- Create: `config/src/config/models.py`
- Create: `config/tests/conftest.py`
- Create: `config/tests/test_schema.py`
- Create: `requirements-dev.txt`（仓库根）
- Create: `deploy/docker/initdb/01-create-test-db.sh`
- Modify: `config/requirements.txt` —— 追加 `sqlalchemy`、`psycopg[binary]`
- Modify: `deploy/docker-compose.yml` —— config 服务加 `DATABASE_URL` 环境变量；postgres 发布 `127.0.0.1:5432:5432` 供宿主机测试；挂载 `./docker/initdb`
- Modify: `deploy/.env.example` —— 追加 `TEST_DATABASE_URL`
- Modify: `pyproject.toml`（仓库根）—— 追加 `[tool.pytest.ini_options]`：`testpaths = ["config/tests"]`、`pythonpath = ["config/src"]`

**Interfaces:**
- Consumes: `config.src.config.__main__` 现有的 app（本任务不动它）。
- Produces: `config.db.engine`（模块级 `sqlalchemy.Engine`，Task 2 的 health 与 lifespan 用）；`config.models.Base`（DeclarativeBase，后续所有领域模型挂它）；`config.models.Tenant`（`__tablename__ = "iam_tenants"`，列：`id` UUID 主键客户端默认 `uuid4`、`name` 唯一、`created_at` 数据库默认 `now()`）。

- [ ] **Step 1: 写失败的测试与测试地基**

`config/tests/conftest.py`：加载 `deploy/.env`（python-dotenv），要求 `TEST_DATABASE_URL` 存在、且 URL 的库名以 `_test` 结尾（否则 `RuntimeError`），然后 `os.environ["DATABASE_URL"] = TEST_DATABASE_URL`；session 级 autouse fixture 先 `Base.metadata.drop_all(engine)` 再 `create_all`。

`config/tests/test_schema.py`：

```python
def test_tenant_round_trip():
    # insert a Tenant(name=...) via Session, read it back in a second Session,
    # assert id matches and created_at is not None (server-side default fired)

def test_duplicate_tenant_name_rejected():
    # insert two Tenants with the same name, assert IntegrityError
```

- [ ] **Step 2: 建宿主机 venv 并安装依赖，跑测试确认失败**

Run: `python -m venv .venv && .venv/Scripts/python -m pip install -r config/requirements.txt -r requirements-dev.txt && .venv/Scripts/python -m pytest -v`
Expected: FAIL —— `config.db` / `config.models` 尚不存在。

- [ ] **Step 3: 实现与基建文件**

`db.py`：模块级 `DATABASE_URL = os.environ["DATABASE_URL"]`、`engine = create_engine(DATABASE_URL, pool_pre_ping=True)`（pre_ping：postgres 容器重启后池里不留死连接）。
`models.py`：`Base(DeclarativeBase)` 与 `Tenant`，列如 Interfaces 所列。
其余：根 `requirements-dev.txt`（pytest、httpx、python-dotenv、ruff）、`pyproject.toml` pytest 段、`config/requirements.txt` 追加两依赖、compose 三处改动、`.env.example` 追加 `TEST_DATABASE_URL`、initdb 脚本内容为一条 `CREATE DATABASE agentops_test;`。

- [ ] **Step 4: 重建数据库使 initdb 生效，跑测试确认通过**

Run: `cd deploy && docker compose down -v && docker compose up -d` 等 postgres healthy；回根目录 `.venv/Scripts/python -m pytest -v`
Expected: 2 passed。

- [ ] **Step 5: Commit**

```bash
git add config/ requirements-dev.txt deploy/ pyproject.toml
git commit -m "Wire config to Postgres: engine, iam_tenants, pytest harness"
```

### Task 2: `/health` 查库与启动建表

**Files:**
- Create: `config/src/config/app.py` —— app、lifespan（`Base.metadata.create_all(db.engine)` 后 log "config started"）、`/health`
- Create: `config/tests/test_health.py`
- Modify: `config/src/config/__main__.py` —— 瘦身为 `uvicorn.run(app, ...)`

**Interfaces:**
- Consumes: Task 1 的 `config.db.engine`、`config.models.Base`。health 通过 `from . import db` 后访问 `db.engine`（属性访问，测试可 monkeypatch）。
- Produces: `config.app.app`（FastAPI 实例，后续所有路由挂它）；`GET /health` 成功返回 `{"status": "ok", "db": "ok"}`，数据库不可达返回 503 `{"status": "degraded", "db": "unreachable"}`。

- [ ] **Step 1: 写失败的测试**

`config/tests/test_health.py`：`test_health_reports_db`（TestClient 进 lifespan 后 GET `/health`，断言 200 与 JSON 形状）；`test_health_503_when_db_down`（`monkeypatch.setattr` 把 `config.app.db.engine` 换成指向 `127.0.0.1:1` 的引擎，断言 503 与 `db == "unreachable"`）。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python -m pytest config/tests/test_health.py -v`
Expected: FAIL —— `config.app` 不存在（app 还在 `__main__` 里，且无 db 字段）。

- [ ] **Step 3: 实现 `app.py` 并瘦身 `__main__.py`**

health 里用 `with db.engine.connect()` 执行 `SELECT 1`，捕获 `sqlalchemy.exc.OperationalError` 转 503。`__main__.py` 只留 uvicorn 启动。

- [ ] **Step 4: 跑全部测试确认通过，ruff 干净**

Run: `.venv/Scripts/python -m pytest -v && .venv/Scripts/python -m ruff check config`
Expected: 全部 passed；ruff 无告警。

- [ ] **Step 5: 重建 config 镜像并部署验证**

Run: `cd deploy && docker compose up -d --build config` 后 `docker compose exec config python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').read().decode())"`
Expected: `{"status":"ok","db":"ok"}`。

- [ ] **Step 6: Commit**

```bash
git add config/
git commit -m "Check the database in /health and create tables at startup"
```
