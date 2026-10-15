# SDK 事件词汇与消息流设计（对齐行业标准，v2）

- 状态：待确认
- 日期：2026-10-15
- 范围：SDK 用户面的事件形状与命名；线协议、manager、worker、双侧 SDK、docs
- 取代：2026-10-15 消息聚合实现中的 `message` 聚合事件与 `RUN_*` 大写投影（见 git dfc59fe..26ad6ad）

---

## 1. 决策

### D1 SDK 用户面直接暴露三段式消息事件，删除 `message` 聚合

用过 AG-UI / OpenAI / Anthropic SDK 的人拿到的就是三段式流事件；自造 `message{text, delta, done}` 是第三种方言。改为：

| 事件 | 字段 | 对应标准 |
|---|---|---|
| `text_message_start` | `run_id` | AG-UI `TEXT_MESSAGE_START` / Anthropic `content_block_start` |
| `text_message_content` | `run_id`, `delta` | AG-UI `TEXT_MESSAGE_CONTENT{delta}` / OpenAI `response.output_text.delta` |
| `text_message_end` | `run_id`, **`text`（完整权威终文）** | OpenAI `response.output_text.done{text}` |

`text_message_end` 带 `text` 是对齐 OpenAI（AG-UI 的 END 不带文本，完整文本另走 `MessagesSnapshot`）——它同时承载本平台的封口保证（见 D4），且是 AG-UI 消费者的安全超集（忽略未知字段）。

### D2 type 与字段全部小写 snake_case

- 小写有 OpenAI（点分小写）与 Anthropic（下划线小写）两大先例；AG-UI 的 SCREAMING_SNAKE 是少数派，且与平台全链（日志类型、wire、payload 字段）的 snake_case 风格冲突。
- 持久事件名回到 `run_started` / `run_finished` / `run_failed`（撤销 `RUN_*` 大写投影）。
- 字段保持 `run_id` / `delta` / `text` / `seq`（snake_case，与 OpenAI/Anthropic 一致）。
- 与 AG-UI 的**字面差异**（结构同构、拼写不同、无 `messageId`——`run_id` 即配对键）在 docs/02 声明；AG-UI 生态直连靠边缘 adapter 做字面映射（roadmap 待定项 19），不改 SDK 面向人的形状。

### D3 wire / SSE / SDK 三层同名，撤销外名投影层

小写后对外名与日志类型同名，`external_event_name()` 投影层整体删除——SSE 出口只保留私有字段剥离（`llm_call.messages`、`context_compacted.summary`）。比 v1 更简单：一层映射都不剩。

### D4 SDK 合成封口保证（平台可靠性的标准形状表达）

三段式流事件不重放（行业标准如此，我们也如此）。SDK 内部保证：**每个 run 恰好一条 `text_message_end` 到达用户**——真 END 到了就透传；错过了（断线、订阅晚、replay）由 `run_finished` 触发合成 `text_message_end{text=result}`。规则沿用已实现的封口判定：`result` 为字符串用 `result`；否则用累积缓冲；两者皆无则不合成。`run_failed` 不合成。消费端从此既拿到标准形状、又不需要任何"错过增量"的样板。

### D5 事件类型全景（SDK 用户面）

`run_started` / `llm_call` / `llm_result` / `tool_call` / `tool_result` / `agent_call` / `agent_result` / `branch_taken` / `context_compacted` / `text_message_start` / `text_message_content` / `text_message_end` / `run_finished` / `run_failed`。全部小写、`_call`/`_result` 靠 `action_id` 配对、未知类型必须忽略——这三条不变。

## 2. 改动面

- `shared/events.py`：`STREAM_EVENT_TYPES` 改小写三元组；删 `external_event_name` 与外名映射。
- `worker`：引擎流帧改 `text_message_*`；`_pump` 不变（wire 本就内部名）。
- `manager`：`internal._forward` 删投影调用；流帧分支改小写；补发快照帧名改小写。
- SDK 双侧：删聚合层，三段直通 + `run_finished` 合成 end（Python pending 队列、TS 连续 yield 的既有机制复用）。
- examples/docs：conversation 消费三段（content 打 `delta`、end 换行，注释指向 `end.text` 兜底语义）；docs/02 §6.5 事件表重写、§5.1 伪代码；docs/05 §3.3；docs/06 §1 删投影句。
- 测试：worker/manager/SDK 双侧断言从 `TEXT_MESSAGE_*`/`RUN_*`/`message` 收敛到小写三段与合成 end。

## 3. 边界

- wire（manager↔worker）零变化（内部名从未改过）；运行日志与配对不变式零变化。
- 不引入 `messageId`（每 run 单消息，`run_id` 配对；多消息时再加，形状向后兼容）。
- 不实现 `MessagesSnapshot` / `STATE_SNAPSHOT` 类快照事件——重放语义由本平台游标机制承担，快照事件是 AG-UI/A2A 在**无重放能力**时的替代品。
