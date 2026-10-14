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
