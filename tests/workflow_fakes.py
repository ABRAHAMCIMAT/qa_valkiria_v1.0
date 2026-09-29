"""LLM simulado y datos de prueba compartidos por las pruebas del flujo por historia."""

from valkiria.application import prompts
from valkiria.providers.openai_compatible import LLMProviderError

# El LLM simulado reconoce cada tarea por su prompt de sistema exacto: reescribir un prompt no rompe las pruebas.
_BY_PROMPT = {prompts.SQL_VALIDATION_SYSTEM: "sql", prompts.STORY_EDIT_SYSTEM: "edit", prompts.STORY_SPLIT_SYSTEM: "split", prompts.INVEST_SUGGESTION_SYSTEM: "suggestion", prompts.ASSISTANT_ARGS_SYSTEM: "args", prompts.ASSISTANT_COMPOSE_SYSTEM: "compose", prompts.CHAT_SYSTEM: "chat", prompts.REVISION_SYSTEM: "revision",
              prompts.STORY_SYSTEM: "story", prompts.INVEST_SYSTEM: "invest", prompts.MATRIX_SYSTEM: "matrix", prompts.RISK_SYSTEM: "risk"}
_ASSISTANT_PREFIX = prompts.ASSISTANT_SYSTEM.split("{")[0]


def prompt_kind(system: str) -> str:
    if system in _BY_PROMPT:
        return _BY_PROMPT[system]
    if system.startswith(_ASSISTANT_PREFIX):
        return "assistant"
    raise AssertionError(f"Prompt de sistema no reconocido: {system[:80]}")

STORY = {"title": "Consultar vehículos disponibles", "description": "Como asesor, quiero consultar vehículos disponibles, para ofrecerlos al cliente.",
         "business_rules": ["Solo vehículos con stock"], "acceptance_criteria": [{"id": "AC-01", "text": "Dado stock, cuando consulto, entonces veo el vehículo"},
                                                                                  {"id": "AC-02", "text": "Dado sin stock, cuando consulto, entonces no aparece"}]}
INVEST = {"criteria": [{"name": n, "status": "cumple", "justification": "Correcto", "suggestion": None} for n in ("Independiente", "Negociable", "Valiosa", "Estimable", "Pequeña")]
          + [{"name": "Testeable", "status": "parcial", "justification": "Falta dato de borde", "suggestion": "Agregar criterio para stock igual a 1"}]}


def full_matrix(criteria=("AC-01", "AC-02")):
    return {"cases": [{"id": f"TC-{c}-{t}", "criterion_id": c, "scenario": f"{c} {t}", "expected_result": "ok", "type": t, "priority": "high"} for c in criteria for t in ("positive", "negative", "edge")]}


class ScriptedLLM:
    """LLM falso: responde según el prompt de sistema y permite inyectar fallos."""

    model_name = "llama3.2:3b-instruct-q4_K_M"

    def __init__(self):
        self.calls: list[str] = []
        self.prompts: list[tuple[str, str]] = []
        # Decisiones del asistente de razonamiento, en orden; al agotarse responde sin herramientas.
        self.assistant_script: list[dict] = []
        # Redacciones del paso final del asistente (fase de composición verificada).
        self.compose_script: list[dict] = []
        # Argumentos extraídos cuando el código fuerza la herramienta afín.
        self.args_script: list[dict] = []
        self.chat_response: dict | None = None
        self.split_response: dict | None = None
        self.edit_response: dict | None = None
        self.failures: dict[str, int] = {}
        self.matrix = full_matrix()
        self.story = STORY
        self.invest = INVEST

    async def generate_json(self, *, system: str, user: str, schema: dict) -> dict:
        kind = prompt_kind(system)
        if kind == "args":
            self.calls.append("args")
            return self.args_script.pop(0) if self.args_script else {}
        if kind == "sql":
            self.calls.append("sql")
            return {"queries": [{"purpose": "Vehículos disponibles con stock", "sql": "SELECT vehicle_id, model, stock FROM vehicles WHERE stock > 0 LIMIT 20"},
                                {"purpose": "Intento de mutación", "sql": "DELETE FROM vehicles"}]}
        if kind == "edit":
            self.calls.append("edit")
            self.prompts.append(("edit", user))
            if self.edit_response is not None:
                return self.edit_response
            criteria = [*self.story["acceptance_criteria"], {"id": f"AC-{len(self.story['acceptance_criteria']) + 1:02d}", "text": f"Dado un dato inválido, cuando consulto, entonces veo un error claro ({len(self.calls)})"}]
            return {**self.story, "acceptance_criteria": criteria, "changes": ["Agregué un criterio de error."]}
        if kind == "split":
            self.calls.append("split")
            return self.split_response or {"split": [{"title": "Consultar mis autos"}, {"title": "Agendar cita de servicio"}, {"title": "Pagar en línea"}]}
        if kind == "suggestion":
            self.calls.append("suggestion")
            return {"suggestion": "Agregar un criterio para stock igual a 1."}
        if kind == "compose":
            self.calls.append("compose")
            self.prompts.append(("compose", user))
            return self.compose_script.pop(0) if self.compose_script else {"respuesta": ""}
        if kind == "assistant":
            self.calls.append("assistant")
            self.prompts.append(("assistant", user))
            if self.failures.get("assistant") == -1:
                raise LLMProviderError("llm_http_error", "caído")
            return self.assistant_script.pop(0) if self.assistant_script else {"accion": "responder", "respuesta": "Respuesta general."}
        if kind == "chat":
            self.calls.append("chat")
            self.prompts.append(("chat", user))
            if self.failures.get("chat") == -1:
                raise LLMProviderError("llm_timeout", "timeout")
            return self.chat_response or {"intent": "crear", "reply": "Redacté la historia.", "assumptions": [], "story": self.story}
        self.calls.append(kind)
        self.prompts.append((kind, user))
        if self.failures.get(kind, 0) > 0:
            self.failures[kind] -= 1
            raise LLMProviderError("llm_timeout", "timeout")
        if self.failures.get(kind) == -1:
            raise LLMProviderError("llm_http_error", "caído")
        if kind == "revision":
            return {**self.story, "acceptance_criteria": [*self.story["acceptance_criteria"], {"id": "AC-03", "text": "Dado stock 1, cuando consulto, entonces aparece"}]}
        if kind == "matrix":
            # Formato compacto: los casos del criterio solicitado ("Criterio AC-01: …"), por tipo.
            import re
            requested = re.search(r"Criterio (AC-\d+)", user)
            by_type = {c["type"]: c for c in self.matrix["cases"] if requested and c["criterion_id"] == requested.group(1)}
            return {t: {"scenario": c["scenario"], "steps": ["Paso 1"], "expected": c["expected_result"], "data": {}} for t, c in by_type.items()}
        return {"story": self.story, "invest": self.invest, "risk": {"level": "high", "justification": "Integración crítica", "mitigation": "Regresión"}}[kind]
