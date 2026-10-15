# SDK 示例

以外部调用方的视角写的可运行示例，只依赖 SDK 本身（`zszc.agentops` / `@zszc/agentops`），随单元推进逐步维护。

前提：本机栈在跑（`deploy/`），密钥经 `AGENT_OPS_KEY` 提供，`AGENT_OPS_BASE_URL` 默认 `http://127.0.0.1:8080`。

| 示例 | 用法（[02-sdk-contract.md](../docs/02-sdk-contract.md) §5） |
|---|---|
| `python/conversation.py` | 对话式：输入声明协商 + 流式订阅 + 多轮 |
| `python/task.py` | 任务式：`request_id` 幂等 + 凭 run id 异地取结果 |
| `typescript/conversation.ts` | 对话式（TS 同构） |
| `typescript/task.ts` | 任务式（TS 同构） |

## 在本仓库内运行

示例自身不需要安装——对外只有 SDK 会被安装（`pip install zszc.agentops` / `npm install @zszc/agentops`）。SDK 尚未发布，在仓库里跑示例用路径做替身：

```bash
# Python：PYTHONPATH 代替 pip install
PYTHONPATH=sdk/python/src .venv/Scripts/python examples/python/conversation.py

# TypeScript：examples/typescript/package.json 里的 file: 链接代替 npm install
cd sdk/typescript && npm run build && cd ../../examples/typescript && npm install
node conversation.ts
```

当前为 fake LLM（部署默认 `LLM_PROVIDER=fake`，回显输入并分段流式），结果形如 `fake: <输入>`。
