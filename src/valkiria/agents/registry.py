from __future__ import annotations

from collections import OrderedDict

from valkiria.agents.approval import ApprovalAgent
from valkiria.agents.assistant import AssistantAgent
from valkiria.agents.automation import AutomationAgent
from valkiria.agents.contracts import SpecializedAgent
from valkiria.agents.database import DatabaseAgent
from valkiria.agents.evaluation import EvaluationAgent
from valkiria.agents.generation import GenerationAgent
from valkiria.agents.grounding import GroundingAgent
from valkiria.agents.intake import IntakeAgent
from valkiria.agents.operations import OperationsAgent
from valkiria.agents.release import ReleaseAgent


class AgentRegistry:
    """Registro determinista; evita seleccionar agentes no autorizados."""

    def __init__(self):
        self._agents: OrderedDict[str, SpecializedAgent] = OrderedDict()

    def register(self, agent: SpecializedAgent) -> None:
        if agent.name in self._agents:
            raise ValueError(f"El agente ya está registrado: {agent.name}")
        self._agents[agent.name] = agent

    def get(self, name: str) -> SpecializedAgent:
        try:
            return self._agents[name]
        except KeyError as exc:
            raise KeyError(f"Agente no registrado: {name}") from exc

    def values(self) -> list[SpecializedAgent]:
        return list(self._agents.values())

    def names(self) -> list[str]:
        return list(self._agents)


def build_default_registry(llm=None, audit=None, metrics=None, database_executor=None, automation_runner=None, synthetic_app_base_url="http://localhost:8090", assistant=None) -> AgentRegistry:
    registry = AgentRegistry()
    registry.register(IntakeAgent())
    registry.register(GroundingAgent())
    registry.register(GenerationAgent(llm=llm))
    registry.register(EvaluationAgent())
    registry.register(AssistantAgent(assistant))
    registry.register(DatabaseAgent(executor=database_executor))
    registry.register(AutomationAgent(runner=automation_runner, base_url=synthetic_app_base_url))
    registry.register(ApprovalAgent())
    registry.register(ReleaseAgent())
    registry.register(OperationsAgent(audit=audit, metrics=metrics))
    return registry
