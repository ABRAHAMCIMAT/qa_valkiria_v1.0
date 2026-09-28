from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from valkiria.agents.contracts import AgentContext, AgentResult
from valkiria.llmops.lifecycle import Phase


class BaseAgent(ABC):
    name: str
    phase: Phase

    @abstractmethod
    async def can_handle(self, context: AgentContext) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def execute(self, context: AgentContext) -> AgentResult:
        raise NotImplementedError

    def success(self, context: AgentContext, message: str, artifacts: dict[str, Any] | None = None, decisions: list[dict[str, Any]] | None = None) -> AgentResult:
        return AgentResult(self.name, self.phase, "completed", message, context.trace_id, artifacts or {}, decisions or [])

    def pending(self, context: AgentContext, message: str, artifacts: dict[str, Any] | None = None, decisions: list[dict[str, Any]] | None = None) -> AgentResult:
        return AgentResult(self.name, self.phase, "waiting_approval", message, context.trace_id, artifacts or {}, decisions or [], gate_status="skipped")

    def blocked(self, context: AgentContext, message: str, artifacts: dict[str, Any] | None = None, decisions: list[dict[str, Any]] | None = None) -> AgentResult:
        return AgentResult(self.name, self.phase, "blocked", message, context.trace_id, artifacts or {}, decisions or [], gate_status="failed")

    def failure(self, context: AgentContext, message: str, retryable: bool = False) -> AgentResult:
        return AgentResult(self.name, self.phase, "failed", message, context.trace_id, gate_status="failed", retryable=retryable)
