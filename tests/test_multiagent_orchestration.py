import asyncio

from valkiria.agents.contracts import AgentContext, AgentResult
from valkiria.agents.grounding import GroundingAgent
from valkiria.agents.intake import IntakeAgent
from valkiria.agents.operations import OperationsAgent
from valkiria.agents.orchestrator import MultiAgentOrchestrator
from valkiria.agents.registry import AgentRegistry, build_default_registry
from valkiria.agents.router import AgentRouter


def run(coro):
    return asyncio.run(coro)


def test_registry_contains_all_specialized_agents():
    registry = build_default_registry()
    assert registry.names() == ["intake", "grounding", "generation", "evaluation", "database", "automation", "approval", "release", "operations"]


def test_router_selects_multiple_agents_for_complex_request():
    registry = build_default_registry()
    context = AgentContext("r1", "t1", "qa", "Generar historia INVEST, matriz de pruebas y preparar Pull Request")
    plan = run(AgentRouter(registry).plan(context))
    assert plan.agents == ["intake", "grounding", "generation", "evaluation", "approval", "release", "operations"]


def test_router_selects_database_for_synthetic_nissan_request():
    registry = build_default_registry()
    context = AgentContext("r-db", "t-db", "qa", "Validar vehículos Nissan contra una base sintética PostgreSQL")
    plan = run(AgentRouter(registry).plan(context))
    assert plan.agents == ["intake", "grounding", "generation", "evaluation", "database", "operations"]


def test_ambiguous_request_is_blocked_after_grounding():
    result = run(MultiAgentOrchestrator(build_default_registry()).run("ambigua", actor="qa", trace_id="trace-1", request_id="request-1"))
    assert result.status == "blocked"
    assert result.trace_id == "trace-1"
    assert result.quality_gates["grounding"]["status"] == "failed"
    assert "operations" in result.artifacts


def test_handoff_preserves_trace_id_and_consolidates_artifacts():
    result = run(MultiAgentOrchestrator(build_default_registry()).run("Generar una historia de usuario para login seguro", trace_id="trace-2"))
    assert result.status == "completed"
    assert result.trace_id == "trace-2"
    assert "intake" in result.artifacts
    assert "grounding" in result.artifacts
    assert "generation" in result.artifacts
    assert "evaluation" in result.artifacts
    assert "operations" in result.artifacts


def test_release_waits_without_human_approval():
    result = run(MultiAgentOrchestrator(build_default_registry()).run("Generar y publicar un Pull Request de automatización", trace_id="trace-3"))
    assert result.status == "waiting_approval"
    assert result.quality_gates["approval"]["status"] == "skipped"
    assert result.artifacts["release"]["direct_commit"] is False
    assert result.artifacts["release"]["mode"] == "preview"


class FlakyAgent(IntakeAgent):
    def __init__(self):
        self.calls = 0

    async def execute(self, context):
        self.calls += 1
        if self.calls == 1:
            return AgentResult(self.name, self.phase, "failed", "temporal", context.trace_id, retryable=True, gate_status="failed")
        return self.success(context, "reintentado", {"flaky": {"ok": True}})


def test_retry_controlado_only_once():
    registry = AgentRegistry()
    flaky = FlakyAgent()
    registry.register(flaky)
    registry.register(GroundingAgent())
    registry.register(OperationsAgent())
    result = run(MultiAgentOrchestrator(registry).run("Una petición suficientemente clara para reintentar", trace_id="trace-4"))
    assert result.status == "completed"
    assert flaky.calls == 2
    assert result.artifacts["flaky"]["ok"] is True


def test_exception_is_sanitized_and_does_not_leak_details():
    class ExplodingAgent(IntakeAgent):
        async def execute(self, context):
            raise RuntimeError("secret-internal-detail")

    registry = AgentRegistry()
    registry.register(ExplodingAgent())
    registry.register(GroundingAgent())
    registry.register(OperationsAgent())
    result = run(MultiAgentOrchestrator(registry).run("Una petición clara de más de veinte caracteres", trace_id="trace-5"))
    assert result.status == "failed"
    assert "secret-internal-detail" not in str(result.model_dump())


def test_agents_sharing_a_phase_keep_their_own_quality_gate():
    # Database y Automation comparten la fase de evaluación: ninguno debe sobrescribir el gate de otro.
    result = run(MultiAgentOrchestrator(build_default_registry()).run("Validar vehículos Nissan contra una base sintética PostgreSQL y ejecutar casos Playwright E2E", trace_id="trace-gates"))
    gates = result.quality_gates
    for agent in ("evaluation", "database", "automation"):
        assert gates[agent]["agent"] == agent
        assert gates[agent]["phase"] == "evaluation"
    assert set(gates) == set(result.plan.agents)
