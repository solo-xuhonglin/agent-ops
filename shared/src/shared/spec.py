from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class InputParam(BaseModel):
    key: str
    type: Literal["string", "number", "boolean"]
    required: bool = False
    description: str = Field(min_length=1)
    default: str | int | float | bool | None = None


class CompactionPolicy(BaseModel):
    # Shares of the model window and an absolute token ceiling, whichever
    # hits first (docs/05 §8.2).
    window_ratio: float = Field(default=0.7, gt=0, lt=1)
    token_limit: int = Field(default=300_000, gt=0)


class ToolSpec(BaseModel):
    # An HTTP tool: the description is model-facing and therefore required
    # (docs/08 §2.1); parameters is a JSON Schema passed to the model as-is.
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    parameters: dict = {}
    credential: str | None = None
    method: str = "GET"
    url_template: str
    headers: dict[str, str] = {}
    body_template: str | None = None
    response_max_bytes: int = Field(default=8192, gt=0)

    def to_definition(self) -> dict:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


class AgentSpec(BaseModel):
    mode: Literal["react"]
    name: str
    instructions: str
    input: list[InputParam]
    model_id: str
    model_params: dict
    max_iterations: int = Field(ge=1)
    context: CompactionPolicy = CompactionPolicy()
    tools: list[ToolSpec] = []

    @model_validator(mode="after")
    def input_keys_unique(self):
        keys = [param.key for param in self.input]
        if len(keys) != len(set(keys)):
            raise ValueError("input param keys must be unique")
        return self

    @model_validator(mode="after")
    def tool_names_unique(self):
        names = [tool.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("tool names must be unique")
        return self

    @field_validator("instructions")
    @classmethod
    def instructions_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("instructions must not be empty")
        return value

    def to_snapshot_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_snapshot_json(cls, raw: str) -> "AgentSpec":
        return cls.model_validate_json(raw)
