"""LLM simulado y datos de prueba compartidos por las pruebas del flujo por historia."""

from valkiria.providers.openai_compatible import LLMProviderError

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
        self.failures: dict[str, int] = {}
        self.matrix = full_matrix()
        self.story = STORY
        self.invest = INVEST

    async def generate_json(self, *, system: str, user: str, schema: dict) -> dict:
        if "Extrae de la petición los argumentos" in system:
            self.calls.append("args")
            return self.args_script.pop(0) if self.args_script else {}
        if "datos verificados" in system:
            self.calls.append("compose")
            self.prompts.append(("compose", user))
            return self.compose_script.pop(0) if self.compose_script else {"respuesta": ""}
        if "asistente de QA para Nissan" in system:
            self.calls.append("assistant")
            self.prompts.append(("assistant", user))
            if self.failures.get("assistant") == -1:
                raise LLMProviderError("llm_http_error", "caído")
            return self.assistant_script.pop(0) if self.assistant_script else {"accion": "responder", "respuesta": "Respuesta general."}
        if "Decide la intención del mensaje nuevo" in system:
            self.calls.append("chat")
            self.prompts.append(("chat", user))
            return {"intent": "crear", "reply": "Redacté la historia.", "assumptions": [], "story": self.story}
        kind = "revision" if "sugerencias aprobadas" in system else "story" if "historia de usuario clara" in system else "invest" if "INVEST" in system else "matrix" if "matriz" in system else "risk"
        self.calls.append(kind)
        self.prompts.append((kind, user))
        if self.failures.get(kind, 0) > 0:
            self.failures[kind] -= 1
            raise LLMProviderError("llm_timeout", "timeout")
        if self.failures.get(kind) == -1:
            raise LLMProviderError("llm_http_error", "caído")
        if kind == "revision":
            return {**self.story, "acceptance_criteria": [*self.story["acceptance_criteria"], {"id": "AC-03", "text": "Dado stock 1, cuando consulto, entonces aparece"}]}
        return {"story": self.story, "invest": self.invest, "matrix": self.matrix, "risk": {"level": "high", "justification": "Integración crítica", "mitigation": "Regresión"}}[kind]
