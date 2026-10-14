from shared.spec import AgentSpec, InputParam

SPEC = AgentSpec(
    mode="react",
    name="demo",
    instructions="你是一个项目分析助手",
    input=[
        InputParam(
            key="project_id", type="string", required=True, description="项目 ID，来自项目管理系统，形如 PRJ-1042"
        ),
        InputParam(
            key="include_archived", type="boolean", required=False, description="是否包含已归档项目", default=False
        ),
    ],
    model_id="fake",
    model_params={},
    max_iterations=8,
)
