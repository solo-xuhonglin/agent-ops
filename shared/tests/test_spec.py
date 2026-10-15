import pytest
from pydantic import ValidationError
from shared.spec import AgentSpec, CompactionPolicy, InputParam


def make_spec(**overrides):
    fields = {
        "mode": "react",
        "name": "hello-agent",
        "instructions": "Analyze the project.",
        "input": [InputParam(key="project_id", type="string", required=True, description="The project id.")],
        "model_id": "stub",
        "model_params": {"temperature": 0.2},
        "max_iterations": 3,
    }
    fields.update(overrides)
    return AgentSpec(**fields)


def test_snapshot_round_trip():
    spec = make_spec()

    restored = AgentSpec.from_snapshot_json(spec.to_snapshot_json())

    assert restored == spec


def test_input_param_validation():
    with pytest.raises(ValidationError):
        InputParam(key="project_id", type="string", required=True, description="")
    with pytest.raises(ValidationError):
        make_spec(
            input=[
                InputParam(key="project_id", type="string", required=True, description="a"),
                InputParam(key="project_id", type="string", description="b"),
            ]
        )
    with pytest.raises(ValidationError):
        InputParam(key="project_id", type="array", required=True, description="c")


def test_max_iterations_floor():
    with pytest.raises(ValidationError):
        make_spec(max_iterations=0)


def test_compaction_defaults():
    policy = CompactionPolicy()

    assert policy.window_ratio == 0.7
    assert policy.token_limit == 300_000


def test_tool_spec_shape_and_validation():
    from shared.spec import ToolSpec

    tool = ToolSpec(
        name="fetch_project",
        description="获取项目详情",
        parameters={"type": "object", "properties": {"project_id": {"type": "string"}}},
        method="GET",
        url_template="https://example.test/projects/{{args.project_id}}",
        headers={"Authorization": "Bearer {{credential}}"},
    )
    assert tool.to_definition() == {
        "type": "function",
        "function": {
            "name": "fetch_project",
            "description": "获取项目详情",
            "parameters": {"type": "object", "properties": {"project_id": {"type": "string"}}},
        },
    }

    with pytest.raises(ValidationError):
        ToolSpec(name="", description="d", url_template="x")
    with pytest.raises(ValidationError):
        ToolSpec(name="t", description="", url_template="x")


def test_agent_spec_carries_tools_with_unique_names():
    from shared.spec import AgentSpec, ToolSpec

    def with_tools(tools):
        return AgentSpec(
            mode="react",
            name="a",
            instructions="x",
            input=[],
            model_id="m",
            model_params={},
            max_iterations=2,
            tools=tools,
        )

    tool = ToolSpec(name="t1", description="d", url_template="x")
    spec = with_tools([tool])
    assert spec.tools[0].name == "t1"
    assert with_tools([]).tools == []

    with pytest.raises(ValidationError):
        with_tools([tool, tool.model_copy()])

    assert AgentSpec.model_validate_json(spec.to_snapshot_json()).tools[0].url_template == "x"
