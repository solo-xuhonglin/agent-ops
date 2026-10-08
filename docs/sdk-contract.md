# SDK 契约

- 状态：草稿（待评审）
- 日期：2026-10-08
- 范围：对外调用契约 —— SDK 表面、语义与保证
- 关联：[agent-platform-design.md](./agent-platform-design.md) 第 3 节
- 具体报文格式由 `contract/` 下的 OpenAPI 定义；**本文档定义 SDK 表面的语义与保证**

---

## 1. 定位

SDK 是平台对外的唯一入口。**它只认三样东西：Agent 名、输入、会话。**

平台内部有工具、Skill、编排方式、版本、节点这些概念 —— 它们全部不出现在契约里。这条是硬约束：**任何内部概念泄漏到 SDK，都会立刻变成无法修改的兼容性负担。**

---

## 2. 两个 SDK

| SDK | 语言与形态 | 身份 |
|---|---|---|
| 后端 | Python 包，同步 + 异步两套 | 应用凭据 |
| 前端 | TypeScript 包，纯脚本库 | 调用方签发的用户令牌 |

**前端 SDK 不提供 UI，不绑定任何框架**，浏览器与 Node 都能跑。它只做四件事：发请求、订阅流、管会话、转发令牌。

### 2.1 对称

两个 SDK 的**方法名、参数名、事件类型一一对应**。契约只定义一次，两种语言各实现一遍。本文示例用 Python 写法，TypeScript 侧同名同参，仅 `snake_case` → `camelCase`。

---

## 3. 调用模型

```
run = client.submit(agent, input, *, session=None, request_id=None)  → Run

run.wait(*, timeout=None)        → Result
run.stream(*, from_seq=None)     → Iterator[Event]
run.cancel()
run.id / run.session_id / run.status

client.get_run(run_id)           → Run
client.create_session(agent)     → Session
client.get_session(session_id)   → Session
```

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
session = client.create_session("report-writer")
client.submit("report-writer", "帮我看看 X", session=session.id).stream()
client.submit("report-writer", "再深入点", session=session.id).stream()
```

### 4.2 任务式

```
run = client.submit("report-writer", {"project": 42}, request_id="job-42")
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

调用方生成 `request_id`。同一个 `(应用, request_id)` 重复提交，返回**同一个运行**，不会产生第二次执行。

### 5.2 会话

不传 `session` 就是单次运行，不建会话。传了就续上多轮上下文。`get_session` 返回会话及其消息历史。

### 5.3 超时与取消

**`wait` 超时只是调用方不等了，运行继续。** 要停下来必须显式 `cancel()`。已产生的成本照记。

### 5.4 断线续传

事件带**单调递增的序号**，`stream(from_seq=n)` 从断点接着推。

保证**不丢失**，可能重复 —— 调用方按序号去重。

> 这条对服务端是硬要求：**已产生的事件必须可重放**，不能只走易失的发布订阅通道。断线期间产生的事件，重连时必须还在。

### 5.5 事件

事件只描述**进度**，不带节点、工具、模型等内部标识。

| 事件 | 含义 |
|---|---|
| `run.started` | 运行开始 |
| `step.started` / `step.finished` | 一步开始或结束，只带粗粒度类别（思考中 / 执行中） |
| `message.delta` | 文本增量 |
| `run.finished` | 完成，带结果 |
| `run.failed` | 失败，带错误详情 |

### 5.6 错误

契约层面的错误由 SDK 抛异常；**执行失败是运行的状态，不是异常** —— 它在事件与运行状态上表达，`wait()` 时才转为异常。

| 异常 | 触发 |
|---|---|
| `AuthError` | 凭据无效，或应用已停用 |
| `QuotaError` | 超出配额或触发限流 |
| `ContractError` | 参数或协议不合法 |
| `RunFailed` | 运行以失败结束（`wait` 时抛出） |
| `Timeout` | `wait` 超时；**运行仍在继续** |

---

## 6. 鉴权

### 6.1 后端

持应用凭据。凭据明文**只在签发时出现一次**，之后平台只存哈希。

### 6.2 前端

**浏览器里不放任何长期凭据。** 前端 SDK 配置一个公开的应用标识，加一个取令牌的回调：

```ts
const client = createClient({ appId, getToken })
```

令牌由**调用方自己的后端**签发，平台用该应用登记的公钥验签。SDK 不缓存令牌 —— 每次调用前问一次 `getToken`。

### 6.3 终端用户不打通

调用方传进来的用户标识原样记录，**不当团队成员**，不参与团队内的角色判定。平台不做单点登录，也不信任外部用户目录。

---

## 7. 屏蔽细节

| 不暴露 | 原因 |
|---|---|
| 编排方式是 ReAct 还是 DAG | 内部实现，改了不影响调用方 |
| 节点、边、工具名、Skill 名 | 配置细节，调用方不需要也不该依赖 |
| 版本号 | 平台决定用哪个版本；需要钉版本的场景走内部调试通道 |
| 模型与参数 | 同上 |

---

## 8. 待定项

1. **长任务的执行结果如何交付。** `wait()` 与 `stream()` 覆盖的都是"调用方在等"的场景。跑几分钟、没人等的任务，靠轮询能用但不经济。可选做法：长轮询、回调地址、或者就维持轮询。**后续细化。**
2. **事件保留多久。** 断线续传的窗口受事件保留期限制，超出后只能重新拉运行状态，拿不到中间过程。保留期与成本直接相关，需要和运行记录的保留策略一起定。
