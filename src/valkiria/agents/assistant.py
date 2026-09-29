from __future__ import annotations

from valkiria.agents.base import BaseAgent
from valkiria.agents.contracts import AgentContext
from valkiria.assistant.routing import route_message
from valkiria.assistant.tools import ToolContext
from valkiria.llmops.lifecycle import Phase


class AssistantAgent(BaseAgent):
    """Resuelve preguntas y peticiones fuera del flujo programado razonando con herramientas y skills."""

    name = "assistant"
    phase = Phase.GENERATION

    def __init__(self, assistant=None):
        self.assistant = assistant

    async def can_handle(self, context: AgentContext) -> bool:
        return route_message(context.user_request, follow_up=context.follow_up) == "assistant"

    async def execute(self, context: AgentContext):
        if self.assistant is None:
            return self.failure(context, "El asistente de razonamiento no está configurado.")
        memory = "\n\n".join(part for part in (context.memory.get("long_term", ""), ("SESIÓN ACTUAL:\n" + context.memory["session"]) if context.memory.get("session") else "") if part)
        ctx = ToolContext(actor=context.actor, namespace=context.memory.get("namespace", "default"), session_id=context.memory.get("session_id"), trace_id=context.trace_id)
        answer = await self.assistant.ask(context.user_request, ctx=ctx, context=memory)
        decision = {"agent": self.name, "decision": "answered_with_tools" if answer.tools_used else "answered" if answer.status == "answered" else "capability_gap",
                    "tools": answer.tools_used, "mode": answer.mode}
        message = "Petición fuera del flujo resuelta con herramientas." if answer.tools_used else "Petición respondida." if answer.status == "answered" else "La petición excede las capacidades; se informó qué sí es posible."
        return self.success(context, message, {"assistant": answer.model_dump(mode="json")}, [decision])
