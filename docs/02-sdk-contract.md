# SDK 契约

- 状态：草稿（待评审）
- 日期：2026-10-08
- 范围：对外调用契约 —— SDK 表面、语义与保证
- 关联：[01-agent-platform-design.md](./01-agent-platform-design.md) 3.1
- 具体报文格式由 `contract/` 下的 OpenAPI 定义；**本文档定义 SDK 表面的语义与保证**

---

## 1. 定位

SDK 是平台对外的唯一入口。**它只认三样东西：key、输入、会话。**

平台内部有工具、Skill、凭据、编排方式、版本、节点这些概念 —— 它们全部不出现在契约里。这条是硬约束：**任何内部概念泄漏到 SDK，都会立刻变成无法修改的兼容性负担。**

---

## 2. 两个 SDK

| SDK | 语言与形态 | 身份 |
|---|---|---|
| 后端 | Python 包，同步 + 异步两套 | Agent 的永久 key |
| 前端 | TypeScript 包，纯脚本库 | 换来的临时 key |

**前端 SDK 不提供 UI，不绑定任何框架**，浏览器与 Node 都能跑。它只做四件事：发请求、订阅流、管会话、转发令牌。

### 2.1 对称

两个 SDK 的**方法名、参数名、事件类型一一对应**。契约只定义一次，两种语言各实现一遍。本文示例用 Python 写法，TypeScript 侧同名同参，仅 `snake_case` → `camelCase`。

---

## 3. 调用模型

```
client = create_client(key, *, user=None)   # 后端给永久 key，前端给临时 key

run = client.submit(input, *, session=None, request_id=None)  → Run

run.wait(*, timeout=None)        → Result
run.stream(*, from_seq=None)     → Iterator[Event]
run.cancel()
run.id / run.session_id / run.status

client.get_run(run_id)           → Run
client.create_session()          → Session
client.get_session(session_id)   → Session
```

**key 在构造 client 时给，不在 `submit` 时给。** client 是有状态的：它绑定一个 Agent，`submit` 不必每次都重复交一次身份。

**`submit` 与 `create_session` 同步拿到 id**，但执行本身是异步的。平台在这一步就把 Run / 会话行落下来（由 `manager` 写 —— 它是这两张表的写者，见 [05-execution-and-streaming.md](./05-execution-and-streaming.md) 第 1 节），再把句柄交回；否则调用方手上的 id 无法立刻用于 `get_run` 与 `stream`。

**`submit` 永远只返回句柄。** 「怎么等」由句柄上的方法决定 —— 调用方不必在发送的那一刻就决定是阻塞、是流式、还是稍后再取。

这样安排带来三件事：

- **取结果**这个维度被句柄吸收，不再是"调哪个方法"。
- **上下文**由 `session` 参数表达。
- **断线续传**与"稍后取结果"变成同一行代码：`get_run(run_id).stream(from_seq=n)`。

扁平 API（`invoke` / `stream` 各一个方法）做不到最后一点 —— 那里"重新订阅"只能重发请求，会重复执行。

---

## 4. 两个用法

调用方式**由调用方选，不由 Agent 的编排方式决定**。

### 4.1 对话式

```
session = client.create_session()
client.submit("帮我看看 X", session=session.id).stream()
client.submit("再深入点", session=session.id).stream()
```

### 4.2 任务式

```
run = client.submit({"project": 42}, request_id="job-42")
# 稍后，或者在另一个进程、重启之后
result = client.get_run(run.id).wait()
```

### 4.3 为什么不绑定编排方式

编排方式（ReAct / DAG）是内部实现，SDK 承诺调用方看不见它。若调用方式反过来由编排方式决定，会出现：

**一个 Agent 从 ReAct 迁到 DAG 时，调用方式跟着变，所有调用方都要改代码** —— 而这次迁移本该是一次纯内部替换。

所以两者保持独立，只在文档里给推荐搭配：**DAG 常配任务式，ReAct 常配对话式**，但不写进契约。

---

## 5. 契约要点

### 5.1 幂等

调用方生成 `request_id`。同一个 `(Agent, request_id)` 重复提交，返回**同一个运行**，不会产生第二次执行。

**`request_id` 标识的是一次逻辑调用，不是一类任务。** 定时任务必须把区分每一次的维度编进去（日期、调度实例 id）—— 复用固定的 id，第二天拿回的是第一天那个运行，**不报错、只是从此不再执行**。

**去重永不过期** —— 没有失效窗口。

**去重靠唯一索引** —— `(Agent, request_id)` 的唯一约束落在 `execution_runs` 上，由写这张表的 `manager` 在一次写入里判定，因此是原子的。

**队列的重投不是一次新提交** —— 它恢复的是同一个 Run，走的是另一条路（见 [05-execution-and-streaming.md](./05-execution-and-streaming.md) 第 5 节）。

### 5.2 会话

不传 `session` 就是单次运行，不建会话。传了就续上多轮上下文。

**一个会话只有一个主体** —— 前端提交的归那个终端用户，后端提交的归调用方自己。两个终端用户不会落进同一个会话。

**同一个会话同一时刻只有一个活跃 Run。** 运行中再提交会被拒绝，抛 `ConflictError`。

`get_session` 返回会话、消息历史，以及**当前正在跑的那个 Run**（没有则为空）—— 页面刷新、或者第二个标签页打开时，靠它接上（见 [05-execution-and-streaming.md](./05-execution-and-streaming.md) 7.1）。

### 5.3 超时与取消

**`wait` 超时只是调用方不等了，运行继续。** 要停下来必须显式 `cancel()`。已产生的成本照记。

`run.status` 是这五个之一：`pending` / `running` / `succeeded` / `failed` / `cancelled`。**`wait` 超时不改变状态** —— 它仍是 `running`。

### 5.4 断线续传

事件带**单调递增的序号**，`stream(from_seq=n)` 从断点接着推。序号**在每个 Run 内独立编号**，从 1 开始 —— 它是 Run 的局部坐标，不是全局标识。

**传输是 SSE**，用 `fetch` 流式读，不用原生 `EventSource` —— 后者设不了自定义请求头。见 [05-execution-and-streaming.md](./05-execution-and-streaming.md) 4.1。

**内容不丢失，增量粒度不保证。** 断线期间的文本增量不会重放，重连后可能等这一步结束、一次性拿到整段文本。顺序单调递增，但**可能重复** —— 调用方按序号去重。

> 这条对服务端是硬要求：**动作事实必须落成可重放的日志**，不能只走连接。见 [05-execution-and-streaming.md](./05-execution-and-streaming.md) 第 4 节。

### 5.5 事件

事件描述**这次运行做了什么**，与运行内部的动作一一对应。分两类：

**持久事件** —— 带序号，可重放：

| 事件 | 含义 |
|---|---|
| `run_started` | 运行开始 |
| `llm_call` / `llm_result` | 一次模型调用的起止，后者带完整消息、tokens、成本 |
| `tool_call` / `tool_result` | 一次工具调用的起止，带工具名与参数 / 结果或错误 |
| `agent_call` / `agent_result` | 一次子 Agent 调用的起止。**子运行的内部事件不外发** —— 事件流是父运行的序列 |
| `branch_taken` | 一次条件判定，带分支标签 |
| `run_finished` / `run_failed` | 终态，带结果或错误详情 |

**流式事件** —— 不带序号，不重放，直接应用：

| 事件 | 含义 |
|---|---|
| `message_started` | 一条新消息开始 —— **清空当前消息缓冲**。重试与断线补发都会重发它 |
| `message_delta` | 追加文本 |

`_call` 与 `_result` 靠**动作 id** 配对 —— 不依赖严格交替（ReAct 一轮可以并发调多个工具，顺序会交错）。**未知事件类型必须忽略**，这样加一种动作类型就是纯增量。

### 5.6 错误

契约层面的错误由 SDK 抛异常；**执行失败是运行的状态，不是异常** —— 它在事件与运行状态上表达，`wait()` 时才转为异常。

| 异常 | 触发 |
|---|---|
| `AuthError` | key 无效，或已被重置 |
| `QuotaError` | 超出配额或触发限流。**v1 不产生** —— 配额只建不用（见 [10-permissions.md](./10-permissions.md) 2.2） |
| `ContractError` | 参数或协议不合法 |
| `ConflictError` | 会话里已有活跃的运行，这次提交被拒绝（见 5.2） |
| `RunFailed` | 运行以失败结束（`wait` 时抛出） |
| `Timeout` | `wait` 超时；**运行仍在继续** |

---

## 6. 鉴权

### 6.1 后端：永久 key

创建 Agent 时生成，只对那一个 Agent 有效，永久不过期。明文**只在生成时出现一次**，之后平台只存哈希。**平台不转交明文** —— 把它交给调用方是一次运维动作，由当时操作的人完成（创建时是编辑，重置时是管理员）。

**重置**由管理员操作，旧的立即失效。

### 6.2 前端：临时 key

**浏览器里不放永久 key。** 前端先找**调用方自己的后端**换一个临时 key：

```
前端 ──→ 调用方后端 ──(永久 key)──→ 平台
                                     ↓
前端 ←──── 临时 key（1 小时，只能调那一个 Agent）
```

换 key 时带上终端用户标识，**绑在临时 key 上**，之后前端不用再传。

**临时 key 是一张签名令牌** —— 内容是「哪个 Agent + 哪个终端用户 + 过期时间 + key 版本号」，平台用一把密钥签名。**它不落任何存储**：验证是算一次签名，不是查一次表。

**重置永久 key 就是把版本号 +1** —— 由它派生的临时 key 带着旧版本号，一并失效。重置的用意就是立刻断掉。

> 换 key 是平台的 HTTP 接口，由调用方后端调用 —— 它不属于前端 SDK。前端只需要知道去哪里拿这把 key。

### 6.3 终端用户标识只是审计

调用时带上来的用户标识**原样记录，不参与判权** —— 判权只看 key。平台不做单点登录，也不信任外部用户目录。

---

## 7. 屏蔽细节

对外是**一次运行的行为记录**，不是平台内部的结构。边界是：**引用外发，内容不外发。**

| 不暴露 | 原因 |
|---|---|
| 编排方式是 ReAct 还是 DAG | 内部实现，改了不影响调用方 |
| 节点 id、边、图的形状 | 同上 —— 分支标签会外发，图形状不会 |
| 工具定义、Skill 正文、System Prompt | 配置的**内容**，不是运行的事实 |
| 凭据从哪来 | Agent 用自己在外部系统的账号，调用方不需要知道也不需要提供 |
| 版本号 | 平台决定用哪个版本 |
| 模型与参数 | 同上 |
| 子运行的内部事件 | 事件流是父运行的序列；子运行细节只在平台侧排障可见 |

---

## 8. 待定项

1. **长任务的执行结果如何交付。** `wait()` 与 `stream()` 覆盖的都是"调用方在等"的场景。**定时任务是后端的主要场景，而它正是"没人等"的那一类** —— 靠轮询能用但不经济。可选做法：长轮询、回调地址、或者就维持轮询。**后续细化。**
2. **运行日志保留多久。** 它就是重放窗口。超出窗口后 `from_seq` 直接报错，调用方只能重新拉运行状态、拿不到中间过程（见 [05-execution-and-streaming.md](./05-execution-and-streaming.md) 4.4）。保留期与成本直接相关 —— 日志同时是运行记录、观测数据与重放窗口，一处策略管三件事。
3. **前端临时 key 怎么续期。** 临时 key 只有 1 小时，页面开着超过 1 小时就会失效。可选做法：调用方自己重建 client（简单，但每处都要写一遍）；或者 client 接受一个取 key 的回调，SDK 在**认证过期**时重取、用同一个 `request_id` 重放（调用方无感，但 SDK 多了一条自动重试路径，得限定它只在这种错误上触发）。
