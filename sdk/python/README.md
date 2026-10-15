# agent-ops Python SDK

后端调用方的同步客户端，包名 `zszc.agentops`。

```python
from zszc.agentops import create_client

with create_client(key, base_url="http://127.0.0.1:8080") as client:
    agent = client.get_agent()  # 输入声明
    session = client.new_session()
    run = session.submit("分析这个项目", params={"project_id": "PRJ-1042"})

    events = session.stream()  # 返回即订阅就绪，之后只见事件
    for event in events:
        ...

    result = run.wait()  # Result(output)
```

- `run.wait(timeout=...)` 单位秒；超时只放弃等待，运行继续，要停就 `run.cancel()`。失败抛 `RunFailed`，取消抛 `RunCancelled`。
- `session.stream()` 返回即订阅就绪（订阅时的 HTTP 错误在调用处抛出）；内部按 `(run_id, seq)` 去重，state 帧与保活消化在内部，断线与静默自动带游标重连；`from_run` / `from_seq` 可指定续传位置。
- 异常映射：401 `AuthError`、404 `NotFoundError`、409 `ConflictError`、422 `ContractError`。

测试：`python -m pytest sdk/python/tests`

可运行示例见 [examples/](../../examples/README.md)。
