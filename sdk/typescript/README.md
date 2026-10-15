# agent-ops TypeScript SDK

前端与 Node 调用方的客户端，包名 `@zszc/agentops`，无框架绑定、无 UI。

```ts
import { createClient } from "@zszc/agentops";

const client = createClient(key, { baseUrl: "http://127.0.0.1:8080" });
const agent = await client.getAgent();              // 输入声明
const session = await client.newSession();
const run = await session.submit("分析这个项目", { params: { project_id: "PRJ-1042" } });

const events = await session.stream();              // resolve 即订阅就绪，之后只见事件
for await (const event of events) {
  // event.type / event.runId / event.seq / event.data
}

const result = await run.wait();                    // { output }
```

- `run.wait({ timeoutMs })` 超时只放弃等待，运行继续，要停就 `run.cancel()`。失败抛 `RunFailedError`，取消抛 `RunCancelledError`。
- `session.stream()` resolve 即订阅就绪（订阅时的 HTTP 错误在调用处抛出）；内部按 `(runId, seq)` 去重，state 帧与保活消化在内部，断线与静默自动带游标重连；`fromRun` / `fromSeq` 可指定续传位置。
- 订阅跨轮复用时手动 `next()`：`for await` 循环 `break` 会关闭生成器（一次性订阅用完即弃则无妨）。
- 异常映射：401 `AuthError`、404 `NotFoundError`、409 `ConflictError`、422 `ContractError`。
- 浏览器直连需临时 key（单元 17）；当前用永久 key 直连测试。

测试：`npm test`；构建：`npm run build`（产物在 `dist/`）。
部署冒烟：`AGENT_OPS_KEY=<key> node scripts/smoke.ts`。

可运行示例见 [examples/](../../examples/README.md)。
