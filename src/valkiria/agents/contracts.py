from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from valkiria.llmops.lifecycle import Phase


@dataclass
class AgentContext:
    """Contexto explícito y controlado que se comparte entre agentes."""

    request_id: str
    trace_id: str
    actor: str
    user_request: str
    artifacts: dict[str, Any] = field(default_factory=dict)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    quality_gates: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Memoria de la sesión (corto plazo) y recuerdos validados (largo plazo) que el orquestador cargó.
    memory: dict[str, Any] = field(default_factory=dict)

    @property
    def follow_up(self) -> bool:
        """La sesión trae una intención de trabajo previa que esta petición puede continuar."""
        return bool(self.memory.get("facts", {}).get("intents"))


@dataclass
class AgentResult:
    """Resultado normalizado de un agente, sin excepciones de transporte."""

    agent: str
    phase: Phase
    status: str
    message: str
    trace_id: str
    artifacts: dict[str, Any] = field(default_factory=dict)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    gate_status: str = "passed"
    retryable: bool = False


class SpecializedAgent(Protocol):
    name: str
    phase: Phase

    async def can_handle(self, context: AgentContext) -> bool: ...

    async def execute(self, context: AgentContext) -> AgentResult: ...


@dataclass
class AgentPlan:
    request_id: str
    trace_id: str
    agents: list[str]
    rationale: list[str] = field(default_factory=list)


@dataclass
class OrchestrationResult:
    request_id: str
    trace_id: str
    actor: str
    status: str
    plan: AgentPlan
    artifacts: dict[str, Any]
    decisions: list[dict[str, Any]]
    quality_gates: dict[str, dict[str, Any]]
    error: str | None = None
    session_id: str | None = None
    memory: dict[str, Any] = field(default_factory=dict)

    def model_dump(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "actor": self.actor,
            "status": self.status,
            "plan": {
                "request_id": self.plan.request_id,
                "trace_id": self.plan.trace_id,
                "agents": self.plan.agents,
                "rationale": self.plan.rationale,
            },
            "artifacts": self.artifacts,
            "decisions": self.decisions,
            "quality_gates": self.quality_gates,
            "error": self.error,
            "session_id": self.session_id,
            "memory": self.memory,
        }
