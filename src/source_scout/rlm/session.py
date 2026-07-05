from typing import Literal
from uuid import uuid4

from .schemas import RlmFinalResult, RlmSessionConfig, RlmStep, RlmToolCall, RlmToolResult, RlmTrace


class RlmSession:
    def __init__(self, config: RlmSessionConfig, session_id: str | None = None):
        self.trace = RlmTrace(
            session_id=session_id or uuid4().hex,
            config=config,
        )

    def record_step(
        self,
        *,
        kind: Literal["reasoning", "tool", "synthesis"] = "reasoning",
        summary: str = "",
        tool_calls: list[RlmToolCall] | None = None,
        tool_results: list[RlmToolResult] | None = None,
        notes: list[str] | None = None,
    ) -> RlmStep:
        step = RlmStep(
            step_index=len(self.trace.steps) + 1,
            kind=kind,
            summary=summary,
            tool_calls=tool_calls or [],
            tool_results=tool_results or [],
            notes=notes or [],
        )
        self.trace.steps.append(step)
        return step

    def finish(self, result: RlmFinalResult) -> RlmTrace:
        self.trace.final_result = result
        return self.trace
