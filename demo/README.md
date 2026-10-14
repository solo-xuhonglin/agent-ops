# SDK 示例

可运行示例，随单元推进逐步维护。前提：本机栈在跑（`deploy/`），密钥经 `AGENT_OPS_KEY` 提供，`AGENT_OPS_BASE_URL` 默认 `http://127.0.0.1:8080`。

| 示例 | 用法（[02-sdk-contract.md](../docs/02-sdk-contract.md) §5） | 运行（仓库根） |
|---|---|---|
| `python/conversation.py` | 对话式：流式订阅 + 多轮 | `.venv/Scripts/python demo/python/conversation.py` |
| `python/task.py` | 任务式：`request_id` 幂等 + 稍后取结果 | `.venv/Scripts/python demo/python/task.py` |
| `typescript/conversation.ts` | 对话式（TS 同构） | `node demo/typescript/conversation.ts` |
| `typescript/task.ts` | 任务式（TS 同构） | `node demo/typescript/task.ts` |

当前为桩执行，结果形如 `stub: <输入>`。
