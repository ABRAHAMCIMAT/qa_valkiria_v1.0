from __future__ import annotations

from uuid import uuid4

from valkiria.agents.contracts import AgentContext, AgentResult, OrchestrationResult
from valkiria.agents.registry import AgentRegistry
from valkiria.agents.router import AgentRouter
from valkiria.llmops.lifecycle import LLMOpsLifecycle


class MultiAgentOrchestrator:
    """Ejecuta planes secuenciales, valida handoffs y consolida la respuesta."""

    def __init__(self, registry: AgentRegistry, audit=None, metrics=None):
        self.registry = registry
        self.router = AgentRouter(registry)
        self.lifecycle = LLMOpsLifecycle(audit, metrics) if audit is not None and metrics is not None else None

    async def run(self, user_request: str, actor: str = "anonymous", trace_id: str | None = None, request_id: str | None = None) -> OrchestrationResult:
        if not user_request or not user_request.strip():
            raise ValueError("La petición del usuario no puede estar vacía.")
        context = AgentContext(request_id or str(uuid4()), trace_id or str(uuid4()), actor, user_request.strip())
        plan = await self.router.plan(context)
        final_status = "completed"
        error = None
        executed: set[str] = set()
        for agent_name in plan.agents:
            agent = self.registry.get(agent_name)
            result = await self._execute_with_retry(agent, context)
            executed.add(agent.name)
            self._apply_result(context, result)
            if self.lifecycle:
                lifecycle_context = self.lifecycle.new_context(actor)
                lifecycle_context.trace_id = context.trace_id
                await self.lifecycle.record(lifecycle_context, agent.phase, agent.name, result.status, metadata={"gate_status": result.gate_status, "retryable": result.retryable})
            if result.status == "waiting_approval":
                final_status = "waiting_approval"
                continue
            if result.status in {"failed", "blocked", "needs_clarification"}:
                final_status = "blocked" if result.status in {"blocked", "needs_clarification"} else "failed"
                error = result.message
                break
        if "operations" not in executed and "operations" in self.registry.names():
            operations = self.registry.get("operations")
            result = await self._execute_with_retry(operations, context)
            self._apply_result(context, result)
            if result.status == "failed":
                final_status = "failed"
                error = result.message
        if self.lifecycle:
            lifecycle_context = self.lifecycle.new_context(actor)
            lifecycle_context.trace_id = context.trace_id
            await self.lifecycle.finish(lifecycle_context, final_status)
        return OrchestrationResult(context.request_id, context.trace_id, context.actor, final_status, plan, context.artifacts, context.decisions, context.quality_gates, error)

    async def _execute_with_retry(self, agent, context: AgentContext) -> AgentResult:
        attempts = 0
        while True:
            try:
                result = await agent.execute(context)
            except Exception as exc:
                result = AgentResult(agent.name, agent.phase, "failed", f"El agente {agent.name} falló de forma controlada.", context.trace_id, gate_status="failed", retryable=False)
                context.decisions.append({"agent": agent.name, "decision": "exception_sanitized", "error_type": type(exc).__name__})
            if not result.retryable or attempts >= 1 or result.status == "completed":
                return result
            attempts += 1
            context.decisions.append({"agent": agent.name, "decision": "retry", "attempt": attempts})

    @staticmethod
    def _apply_result(context: AgentContext, result: AgentResult) -> None:
        if result.trace_id != context.trace_id:
            raise RuntimeError("El handoff rechazó un trace_id inconsistente.")
        context.artifacts.update(result.artifacts)
        context.decisions.extend(result.decisions)
        context.quality_gates[result.phase.value] = {"status": result.gate_status, "agent": result.agent, "outcome": result.status}
