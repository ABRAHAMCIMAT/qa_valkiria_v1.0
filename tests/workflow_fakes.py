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
        self.failures: dict[str, int] = {}
        self.matrix = full_matrix()
        self.story = STORY
        self.invest = INVEST

    async def generate_json(self, *, system: str, user: str, schema: dict) -> dict:
        kind = "revision" if "sugerencias aprobadas" in system else "story" if "historia de usuario clara" in system else "invest" if "INVEST" in system else "matrix" if "matriz" in system else "risk"
        self.calls.append(kind)
        if self.failures.get(kind, 0) > 0:
            self.failures[kind] -= 1
            raise LLMProviderError("llm_timeout", "timeout")
        if self.failures.get(kind) == -1:
            raise LLMProviderError("llm_http_error", "caído")
        if kind == "revision":
            return {**self.story, "acceptance_criteria": [*self.story["acceptance_criteria"], {"id": "AC-03", "text": "Dado stock 1, cuando consulto, entonces aparece"}]}
        return {"story": self.story, "invest": self.invest, "matrix": self.matrix, "risk": {"level": "high", "justification": "Integración crítica", "mitigation": "Regresión"}}[kind]
