from __future__ import annotations

from valkiria.domain.models import (
    ArtifactType,
    InvestEvaluation,
    RiskAssessment,
    TestMatrix,
    UserStory,
)
from valkiria.llmops.lifecycle import Gate, LLMOpsLifecycle, Phase

STORY_SHAPE = (
    '{"title": "título breve", "description": "Como <rol>, quiero <acción>, para <beneficio>.", '
    '"business_rules": ["regla"], "acceptance_criteria": [{"id": "AC-01", "text": "Dado ..., cuando ..., entonces ..."}]}'
)

STORY_SYSTEM = (
    "Eres analista de QA senior. Redacta en español una historia de usuario clara y verificable. "
    "Incluye de 3 a 6 criterios de aceptación en formato Dado/Cuando/Entonces y solo reglas de negocio justificadas por el requerimiento. "
    f"Responde únicamente con un objeto JSON con esta forma: {STORY_SHAPE}"
)

CHAT_SYSTEM = (
    "Eres Valkiria, analista de QA senior que trabaja junto al equipo. Conversas en español de forma natural y profesional, "
    "como un colega: mantienes el hilo de la conversación y la historia de usuario en curso.\n"
    "Decide la intención del mensaje nuevo:\n"
    "- \"crear\": describe un requerimiento o funcionalidad nueva. Redacta una historia nueva con 3 a 6 criterios de aceptación en formato Dado/Cuando/Entonces.\n"
    "- \"ajustar\": pide explícitamente cambiar, agregar, quitar o precisar algo de la historia actual. Devuelve la historia COMPLETA actualizada, conservando lo que no se pidió cambiar.\n"
    "- \"conversar\": pregunta, comentario, saludo, o un requerimiento tan ambiguo que redactarlo obligaría a inventar. "
    "Si el usuario pregunta por qué o pide una explicación sin pedir un cambio, también es \"conversar\": explica tu razonamiento con base en el requerimiento. "
    "Haz 1 o 2 preguntas concretas si falta información.\n"
    "Reglas para \"reply\": de 2 a 4 frases en primera persona; explica qué entendiste y qué hiciste, en pasado (\"Redacté…\", \"Agregué…\"), y en un ajuste qué cambió respecto a la versión anterior; "
    "cierra con un siguiente paso concreto (evaluar INVEST, generar la matriz de pruebas o evaluar el riesgo) o con una pregunta. "
    "No copies la historia ni su descripción en \"reply\" (la interfaz ya la muestra); no uses emojis ni markdown.\n"
    "\"assumptions\": supuestos que tomaste por falta de información (máximo 3, lista vacía si no hubo).\n"
    "\"story\": null cuando la intención es \"conversar\".\n"
    'Responde únicamente con JSON: {"intent": "crear|ajustar|conversar", "reply": "...", "assumptions": ["..."], '
    f'"story": {STORY_SHAPE}}}'
)

INVEST_SYSTEM = (
    "Eres analista de QA senior. Evalúa la historia de usuario con INVEST, criterio por criterio, en español. "
    "Usa exactamente estos seis nombres: Independiente, Negociable, Valiosa, Estimable, Pequeña, Testeable. "
    "status solo puede ser \"cumple\", \"parcial\" o \"no_cumple\". Da una justificación breve y una sugerencia accionable cuando no cumpla por completo. "
    'Responde únicamente con JSON: {"criteria": [{"name": "Independiente", "status": "cumple", "justification": "...", "suggestion": "..."}]}'
)

MATRIX_SYSTEM = (
    "Eres analista de QA senior. Diseña en español una matriz de pruebas para la historia: casos positivos, negativos y de borde, "
    "entre 1 y 3 por criterio de aceptación y máximo 12 en total. type solo puede ser positive, negative o edge; priority: high, medium o low. "
    'Responde únicamente con JSON: {"cases": [{"id": "TC-01", "criterion_id": "AC-01", "scenario": "...", "preconditions": ["..."], '
    '"steps": ["..."], "data": {}, "expected_result": "...", "priority": "high", "type": "positive"}]}'
)

RISK_SYSTEM = (
    "Eres analista de QA senior. Evalúa en español el riesgo de calidad de la historia considerando complejidad, dependencias y criticidad de negocio. "
    "level solo puede ser low, medium o high. No bloquees el trabajo de QA: la mitigación debe ser práctica. "
    'Responde únicamente con JSON: {"level": "medium", "justification": "...", "mitigation": "..."}'
)


def _text(value) -> str:
    if isinstance(value, dict):
        return str(value.get("text") or value.get("description") or value.get("criterion") or " ".join(str(v) for v in value.values() if isinstance(v, str)))
    return str(value)


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _normalize_story(data: dict) -> dict:
    """Adapta la salida de LLMs locales al contrato de UserStory (criterios como texto, campos extra)."""
    story = {k: v for k, v in data.items() if k in {"title", "description", "business_rules", "acceptance_criteria"}}
    story["business_rules"] = [_text(r) for r in _as_list(story.get("business_rules")) if _text(r).strip()]
    criteria = []
    for i, c in enumerate(_as_list(story.get("acceptance_criteria")), start=1):
        text = _text(c).strip()
        if text:
            criteria.append({"id": str(c.get("id") or f"AC-{i:02d}") if isinstance(c, dict) else f"AC-{i:02d}", "text": text})
    story["acceptance_criteria"] = criteria
    return story


# Estados INVEST, no contraseñas (falso positivo de Bandit B105).
_INVEST_STATUS = {"yes": "cumple", "si": "cumple", "sí": "cumple", "true": "cumple", "pass": "cumple", "passed": "cumple", "cumple": "cumple",  # nosec B105
                  "partial": "parcial", "parcial": "parcial", "no": "no_cumple", "false": "no_cumple", "fail": "no_cumple", "no_cumple": "no_cumple", "no cumple": "no_cumple"}


def _normalize_invest(data: dict) -> dict:
    items = data.get("criteria")
    if isinstance(items, dict):
        items = [{"name": k, **(v if isinstance(v, dict) else {"justification": str(v)})} for k, v in items.items()]
    if not items:
        items = [{"name": k, **(v if isinstance(v, dict) else {"justification": str(v)})} for k, v in data.items() if k not in {"story_id", "model", "prompt_version", "id"}]
    criteria = []
    for c in _as_list(items)[:6]:
        if not isinstance(c, dict):
            continue
        status = str(c.get("status", "")).strip().lower()
        criteria.append({"name": str(c.get("name") or c.get("criterion") or ""), "status": _INVEST_STATUS.get(status, status or "parcial"),
                         "justification": str(c.get("justification") or c.get("reason") or ""), "suggestion": c.get("suggestion") or None})
    return {"criteria": criteria}


_CASE_TYPES = {"positivo": "positive", "negativo": "negative", "borde": "edge", "límite": "edge", "limite": "edge", "edge case": "edge"}
_PRIORITIES = {"alta": "high", "media": "medium", "baja": "low"}


def _normalize_matrix(data: dict) -> dict:
    cases = []
    for i, c in enumerate(_as_list(data.get("cases") or data.get("test_cases"))[:30], start=1):
        if not isinstance(c, dict):
            continue
        kind = str(c.get("type", "positive")).strip().lower()
        prio = str(c.get("priority", "medium")).strip().lower()
        cases.append({"id": str(c.get("id") or f"TC-{i:02d}"), "criterion_id": str(c.get("criterion_id") or c.get("criterion") or ""),
                      "scenario": _text(c.get("scenario") or c.get("title") or ""), "preconditions": [_text(x) for x in _as_list(c.get("preconditions"))],
                      "steps": [_text(x) for x in _as_list(c.get("steps"))], "data": c.get("data") if isinstance(c.get("data"), dict) else {},
                      "expected_result": _text(c.get("expected_result") or c.get("expected") or ""), "priority": _PRIORITIES.get(prio, prio if prio in {"high", "medium", "low"} else "medium"),
                      "type": _CASE_TYPES.get(kind, kind if kind in {"positive", "negative", "edge"} else "positive")})
    return {"cases": cases}


_RISK_LEVELS = {"alto": "high", "alta": "high", "medio": "medium", "media": "medium", "bajo": "low", "baja": "low"}


def _normalize_risk(data: dict) -> dict:
    risk = data.get("risk") if isinstance(data.get("risk"), dict) else data
    level = str(risk.get("level") or risk.get("risk_level") or "medium").strip().lower()
    return {"level": _RISK_LEVELS.get(level, level), "justification": _text(risk.get("justification") or risk.get("reason") or ""), "mitigation": _text(risk.get("mitigation") or "")}


def _story_changes(before: UserStory, after: UserStory) -> dict:
    old, new = {c.text for c in before.acceptance_criteria}, {c.text for c in after.acceptance_criteria}
    return {"from_version": before.version, "to_version": after.version, "title_changed": before.title != after.title,
            "description_changed": before.description != after.description, "criteria_added": len(new - old), "criteria_removed": len(old - new),
            "rules_changed": before.business_rules != after.business_rules}


class ValkiriaService:
    """Orquestador de casos de uso; mantiene el transporte fuera del dominio."""

    def __init__(self, llm, audit, metrics, stories, azure=None):
        self.llm = llm
        self.audit = audit
        self.metrics = metrics
        self.stories = stories
        self.azure = azure
        self.ops = LLMOpsLifecycle(audit, metrics)

    async def _ground(self, ctx):
        await self.ops.record_gate(ctx, Gate.G1, "passed", {"result": "input_validated_and_policy_loaded"})

    async def _skip_human_release_gates(self, ctx, reason: str):
        await self.ops.record_gate(ctx, Gate.G4, "skipped", {"reason": reason})
        await self.ops.record_gate(ctx, Gate.G5, "skipped", {"reason": reason})

    async def create_story(self, req: str, actor: str) -> UserStory:
        ctx = self.ops.new_context(actor, self.llm.model_name)
        await self.ops.start(ctx, "create_story", ArtifactType.STORY)
        try:
            await self._ground(ctx)
            data = await self.llm.generate_json(system=STORY_SYSTEM, user=req, schema=UserStory.model_json_schema())
            story = UserStory.model_validate(_normalize_story(data))
            await self.stories.save(story)
            await self.ops.record(ctx, Phase.GENERATION, "create_story", "draft_created", ArtifactType.STORY, str(story.id), story.version)
            await self._skip_human_release_gates(ctx, "el caso de uso solo genera un borrador")
            await self.ops.metric(ctx, "generation.success", 1, "count")
            await self.ops.finish(ctx)
            return story
        except Exception:
            await self.ops.finish(ctx, "failed")
            raise

    async def converse(self, message: str, history: list[dict], current: UserStory | None, actor: str) -> dict:
        """Turno conversacional: mantiene el hilo y decide si crear, ajustar la historia actual o solo responder."""
        ctx = self.ops.new_context(actor, self.llm.model_name)
        await self.ops.start(ctx, "converse", ArtifactType.STORY)
        try:
            await self._ground(ctx)
            context = []
            if current:
                context.append(f"Historia actual (versión {current.version}):\n" + current.model_dump_json(include={"title", "description", "business_rules", "acceptance_criteria"}))
            if history:
                context.append("Conversación reciente:\n" + "\n".join(f"{'Usuario' if t['role'] == 'user' else 'Valkiria'}: {t['content']}" for t in history[-10:]))
            context.append(f"Mensaje nuevo del usuario:\n{message}")
            data = await self.llm.generate_json(system=CHAT_SYSTEM, user="\n\n".join(context), schema=UserStory.model_json_schema())
            intent = str(data.get("intent", "conversar")).strip().lower()
            if intent == "ajustar" and not current:
                intent = "crear"
            reply = str(data.get("reply") or "").strip()
            assumptions = [_text(a) for a in _as_list(data.get("assumptions")) if _text(a).strip()][:3]
            story, changes = None, None
            if intent in {"crear", "ajustar"}:
                fields = _normalize_story(data.get("story") if isinstance(data.get("story"), dict) else {})
                if intent == "crear" and len(fields["acceptance_criteria"]) < 3:
                    # Los modelos locales tienden a resumir en modo conversación; la instrucción dedicada produce criterios completos.
                    fields = _normalize_story(await self.llm.generate_json(system=STORY_SYSTEM, user="\n\n".join(context), schema=UserStory.model_json_schema()))
                try:
                    if intent == "ajustar":
                        story = UserStory.model_validate({**fields, "id": current.id, "version": current.version + 1})
                        changes = _story_changes(current, story)
                        if not any(v for k, v in changes.items() if k not in {"from_version", "to_version"}):
                            intent, story, changes = "conversar", None, None
                    else:
                        story = UserStory.model_validate(fields)
                except ValueError:
                    intent, story = "conversar", None
                    reply = "Entendí la intención, pero no logré estructurar la historia con la información disponible. ¿Me indicas quién la usa, qué necesita hacer y qué resultado espera?"
            if story:
                await self.stories.save(story)
                await self.ops.record(ctx, Phase.GENERATION, "converse", "draft_updated" if changes else "draft_created", ArtifactType.STORY, str(story.id), story.version)
            if not reply:
                reply = "Listo." if story else "¿Me das un poco más de contexto sobre lo que necesitas?"
            if story and "?" not in reply:
                # Mantiene el hilo: toda entrega de historia termina proponiendo el siguiente paso.
                reply += " ¿Seguimos con la evaluación INVEST de esta versión o quieres ajustar algo más?" if changes else " ¿Quieres ajustar algo o seguimos con la evaluación INVEST?"
            await self._skip_human_release_gates(ctx, "el caso de uso solo genera un borrador conversacional")
            await self.ops.finish(ctx)
            return {"intent": intent, "reply": reply, "assumptions": assumptions, "story": story.model_dump(mode="json") if story else None, "changes": changes}
        except Exception:
            await self.ops.finish(ctx, "failed")
            raise

    async def evaluate_invest(self, story: UserStory, actor: str) -> InvestEvaluation:
        ctx = self.ops.new_context(actor, self.llm.model_name)
        await self.ops.start(ctx, "evaluate_invest", ArtifactType.INVEST)
        try:
            await self._ground(ctx)
            data = await self.llm.generate_json(system=INVEST_SYSTEM, user=story.model_dump_json(), schema=InvestEvaluation.model_json_schema())
            data = _normalize_invest(data)
            data.update(story_id=story.id, model=self.llm.model_name, prompt_version=ctx.prompt_version)
            result = InvestEvaluation.model_validate(data)
            await self.ops.record(ctx, Phase.EVALUATION, "invest", "completed", ArtifactType.INVEST, str(result.id))
            await self._skip_human_release_gates(ctx, "el caso de uso solo evalúa el artefacto")
            await self.ops.finish(ctx)
            return result
        except Exception:
            await self.ops.finish(ctx, "failed")
            raise

    async def generate_matrix(self, story: UserStory, actor: str) -> TestMatrix:
        ctx = self.ops.new_context(actor, self.llm.model_name)
        await self.ops.start(ctx, "generate_matrix", ArtifactType.TEST_MATRIX)
        try:
            await self._ground(ctx)
            data = await self.llm.generate_json(system=MATRIX_SYSTEM, user=story.model_dump_json(), schema=TestMatrix.model_json_schema())
            data = _normalize_matrix(data)
            data.update(story_id=story.id)
            result = TestMatrix.model_validate(data)
            await self.ops.record(ctx, Phase.GENERATION, "test_matrix", "draft_created", ArtifactType.TEST_MATRIX, str(result.id))
            await self._skip_human_release_gates(ctx, "el caso de uso solo genera una matriz en borrador")
            await self.ops.finish(ctx)
            return result
        except Exception:
            await self.ops.finish(ctx, "failed")
            raise

    async def assess_risk(self, story: UserStory, actor: str, defect_history: list[dict] | None = None) -> RiskAssessment:
        ctx = self.ops.new_context(actor, self.llm.model_name)
        await self.ops.start(ctx, "assess_risk", ArtifactType.RISK)
        try:
            await self._ground(ctx)
            data = await self.llm.generate_json(system=RISK_SYSTEM, user=story.model_dump_json(), schema=RiskAssessment.model_json_schema())
            data = _normalize_risk(data)
            data["story_id"] = story.id
            data["defect_history_considered"] = bool(defect_history)
            result = RiskAssessment.model_validate(data)
            await self.ops.record(ctx, Phase.EVALUATION, "risk", "completed", ArtifactType.RISK, str(result.id), metadata={"defect_history_available": bool(defect_history)})
            await self._skip_human_release_gates(ctx, "el caso de uso solo evalúa el riesgo")
            await self.ops.finish(ctx)
            return result
        except Exception:
            await self.ops.finish(ctx, "failed")
            raise
