from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class RlmModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RlmSessionConfig(RlmModel):
    task: str = ""
    root_path: str = "."
    model_id: str | None = None
    max_steps: int = Field(default=8, ge=1, le=50)
    max_tool_results: int = Field(default=50, ge=1, le=500)
    max_read_lines: int = Field(default=80, ge=1, le=500)
    max_file_bytes: int = Field(default=200_000, ge=1)
    allow_project_mutation: Literal[False] = False
    allow_code_execution: Literal[False] = False


class RlmToolCall(RlmModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    call_id: str | None = None


class RlmToolResult(RlmModel):
    tool: str
    ok: bool
    result: dict[str, Any] | None = None
    error: str | None = None
    call_id: str | None = None


class RlmStep(RlmModel):
    step_index: int = Field(ge=1)
    kind: Literal["reasoning", "tool", "synthesis"] = "reasoning"
    summary: str = ""
    tool_calls: list[RlmToolCall] = Field(default_factory=list)
    tool_results: list[RlmToolResult] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class RlmFinding(RlmModel):
    summary: str
    evidence_paths: list[str] = Field(default_factory=list)
    candidate_id: str | None = None
    repo_id: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class RlmFinalResult(RlmModel):
    status: Literal["completed", "incomplete", "failed"] = "incomplete"
    answer: str = ""
    findings: list[RlmFinding] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class RlmTrace(RlmModel):
    session_id: str
    config: RlmSessionConfig
    steps: list[RlmStep] = Field(default_factory=list)
    final_result: RlmFinalResult | None = None
    created_at: str = Field(default_factory=_now_iso)

    def to_jsonable(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
