from __future__ import annotations

import logging
from uuid import uuid4

from valkiria.agents.contracts import AgentContext, AgentResult, OrchestrationResult
from valkiria.agents.registry import AgentRegistry
from valkiria.agents.router import AgentRouter
from valkiria.infrastructure.logging import event, get_logger
from valkiria.llmops.lifecycle import LLMOpsLifecycle
from valkiria.memory.service import MemoryService, describe, recall_prompt


class MultiAgentOrchestrator:
    """Ejecuta planes secuenciales, valida handoffs y consolida la respuesta."""

    def __init__(self, registry: AgentRegistry, audit=None, metrics=None, memory: MemoryService | None = None):
        self.registry = registry
        self.memory = memory
        self.logger = get_logger("valkiria.orchestrator")
        self.router = AgentRouter(registry)
        self.lifecycle = LLMOpsLifecycle(audit, metrics) if audit is not None and metrics is not None else None

    async def run(self, user_request: str, actor: str = "anonymous", trace_id: str | None = None, request_id: str | None = None,
                  session_id: str | None = None, namespace: str = "default") -> OrchestrationResult:
        if not user_request or not user_request.strip():
            raise ValueError("La petición del usuario no puede estar vacía.")
        context = AgentContext(request_id or str(uuid4()), trace_id or str(uuid4()), actor, user_request.strip())
        if self.memory and self.memory.enabled:
            session_id = session_id or str(uuid4())
            context.memory = await self._load_memory(context, session_id, namespace)
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
        memory_view: dict = {}
        if context.memory:
            await self._save_turn(context, session_id, plan.agents, final_status, error)
            memory_view = {"session_turns_used": bool(context.memory.get("session")), "recalled": context.memory.get("recalled", [])}
        return OrchestrationResult(context.request_id, context.trace_id, context.actor, final_status, plan, context.artifacts, context.decisions, context.quality_gates, error,
                                   session_id=session_id if context.memory else None, memory=memory_view)

    async def _load_memory(self, context: AgentContext, session_id: str, namespace: str) -> dict:
        loaded: dict = {"session_id": session_id, "namespace": namespace}
        try:
            session = await self.memory.session(session_id)
            recalled = await self.memory.recall(context.user_request, task="agent", namespace=namespace)
        # La memoria enriquece el contexto, pero su falla no debe impedir atender la petición.
        except Exception as exc:  # noqa: BLE001
            event(self.logger, logging.WARNING, "memoria_no_disponible", trace_id=context.trace_id, error_type=type(exc).__name__)
            return loaded
        loaded["session"] = await self.memory.session_context(session_id) if session else ""
        loaded["facts"] = session.facts if session else {}
        loaded["long_term"] = recall_prompt(recalled)
        loaded["recalled"] = describe(recalled)
        return loaded

    async def _save_turn(self, context: AgentContext, session_id: str | None, agents: list[str], status: str, error: str | None) -> None:
        intents = context.artifacts.get("intake", {}).get("intents")
        summary = f"Estado {status}. Agentes: {', '.join(agents)}." + (f" Motivo: {error}" if error else "")
        generation = context.artifacts.get("generation", {}).get("artifact")
        if isinstance(generation, dict) and generation.get("summary"):
            summary += f" Resultado: {generation['summary']}"
        try:
            await self.memory.add_turn(session_id, "user", context.user_request, trace_id=context.trace_id)
            await self.memory.add_turn(session_id, "assistant", summary, facts={"intents": intents if intents and intents != ["general_qa"] else None}, trace_id=context.trace_id)
        except Exception as exc:  # noqa: BLE001
            event(self.logger, logging.WARNING, "memoria_no_guardada", trace_id=context.trace_id, error_type=type(exc).__name__)

    async def _execute_with_retry(self, agent, context: AgentContext) -> AgentResult:
        attempts = 0
        while True:
            try:
                result = await agent.execute(context)
            # Frontera de aislamiento: cualquier fallo de un agente se sanitiza para no
            # filtrar detalles internos ni interrumpir la consolidación de auditoría.
            except Exception as exc:  # noqa: BLE001
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
        # Un gate por agente: varios agentes comparten fase (evaluation) y no deben sobrescribirse.
        context.quality_gates[result.agent] = {"status": result.gate_status, "agent": result.agent, "phase": result.phase.value, "outcome": result.status}
