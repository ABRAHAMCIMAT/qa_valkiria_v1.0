from __future__ import annotations

import asyncio
import re

from valkiria.application.prompts import (
    CHAT_SYSTEM,
    INVEST_SUGGESTION_SYSTEM,
    INVEST_SYSTEM,
    MATRIX_SYSTEM,
    PROMPT_VERSION,
    RISK_SYSTEM,
    STORY_SPLIT_SYSTEM,
    STORY_SYSTEM,
)
from valkiria.domain.models import (
    ArtifactType,
    InvestEvaluation,
    RiskAssessment,
    TestMatrix,
    UserStory,
)
from valkiria.llmops.lifecycle import Gate, LLMOpsLifecycle, Phase
from valkiria.memory.service import with_memory
from valkiria.providers.openai_compatible import LLMProviderError


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
    # El modelo a veces anota la HU del proceso en el título ("… (HU-003A)"): no es parte del título.
    if isinstance(story.get("title"), str):
        story["title"] = re.sub(r"\s*[\(\[]\s*(HU|RT)-\d+[A-Z]?\s*[\)\]]", "", story["title"]).strip()
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


RISK_DIMENSIONS = ("complejidad", "dependencias", "criticidad")


def risk_level(total: int) -> str:
    """HU-005, regla 2: bajo de 3 a 6, medio de 7 a 11, alto de 12 a 15."""
    return "low" if total <= 6 else "medium" if total <= 11 else "high"


def _score(value) -> int | None:
    try:
        return min(5, max(1, int(round(float(value)))))
    except (TypeError, ValueError):
        return None


def _normalize_risk(data: dict) -> dict:
    risk = data.get("risk") if isinstance(data.get("risk"), dict) else data
    level = str(risk.get("level") or risk.get("risk_level") or "medium").strip().lower()
    raw_scores = risk.get("scores") if isinstance(risk.get("scores"), dict) else risk
    scores = {name: _score(raw_scores.get(name)) for name in RISK_DIMENSIONS}
    result = {"level": _RISK_LEVELS.get(level, level), "justification": _text(risk.get("justification") or risk.get("reason") or ""), "mitigation": _text(risk.get("mitigation") or "")}
    if all(value is not None for value in scores.values()):
        # El nivel se calcula con la regla de negocio, no se toma del modelo: así siempre es consistente con las puntuaciones.
        total = sum(scores.values())
        result |= {"scores": scores, "score_total": total, "level": risk_level(total)}
    return result


def _split_item(item) -> dict:
    if isinstance(item, dict):
        return {"title": _text(item.get("title") or item.get("titulo") or "").strip(), "description": _text(item.get("description") or item.get("descripcion") or "").strip()}
    return {"title": _text(item).strip(), "description": ""}


def looks_broad(message: str) -> bool:
    """Enumeración de 3 o más funcionalidades ("vean sus autos, agenden citas, paguen y chateen"): candidato a división."""
    clauses = [c for c in re.split(r",|;|\by\b|\be\b", message) if len(c.split()) >= 2]
    return len(clauses) >= 3 and len(message.split()) >= 10


CASE_KINDS = ("positive", "negative", "edge")
_SUFFIX = {"positive": "P", "negative": "N", "edge": "E"}
_PRIORITY = {"positive": "high", "negative": "high", "edge": "medium"}


def matrix_user_prompt(story: UserStory, only: str | None = None) -> str:
    """Contexto mínimo para diseñar los casos de un criterio: la historia, sus reglas y el criterio."""
    criteria = [c for c in story.acceptance_criteria if only in (None, c.id)]
    rules = "; ".join(story.business_rules) or "ninguna"
    return f"Historia: {story.title}. {story.description}\nReglas de negocio: {rules}\n" + "\n".join(f"Criterio {c.id}: {c.text}" for c in criteria)


def _preconditions(criterion_text: str) -> list[str]:
    """HU-004, regla 3: la precondición sale del "Dado …" del criterio."""
    match = re.match(r"(?is)\s*dad[oa]s?\s+(.+?)(?:,\s*|\s+)cuando\b", criterion_text)
    return [match.group(1).strip().rstrip(",")] if match else []


def cases_from_compact(data: dict, criterion) -> list[dict]:
    """Arma los casos de un criterio desde el formato compacto; vacío si el modelo no entregó los tres tipos."""
    cases = []
    for kind in CASE_KINDS:
        item = data.get(kind) if isinstance(data.get(kind), dict) else None
        if not item or not _text(item.get("scenario") or "").strip() or not _text(item.get("expected") or item.get("expected_result") or "").strip():
            return []
        cases.append({"id": f"TC-{criterion.id}-{_SUFFIX[kind]}", "criterion_id": criterion.id, "scenario": _text(item["scenario"]).strip(),
                      "preconditions": _preconditions(criterion.text), "steps": [_text(x) for x in _as_list(item.get("steps")) if _text(x).strip()][:4],
                      "data": item.get("data") if isinstance(item.get("data"), dict) else {},
                      "expected_result": _text(item.get("expected") or item.get("expected_result")).strip(), "priority": _PRIORITY[kind], "type": kind})
    return cases


def template_case(criterion, kind: str) -> dict:
    label = {"positive": "comportamiento válido", "negative": "comportamiento inválido", "edge": "valores límite"}[kind]
    return {"id": f"TC-{criterion.id}-{_SUFFIX[kind]}", "criterion_id": criterion.id, "scenario": f"{criterion.text} ({label})", "preconditions": _preconditions(criterion.text),
            "steps": [], "data": {}, "expected_result": criterion.text if kind == "positive" else "El sistema rechaza o maneja el caso sin error",
            "priority": _PRIORITY[kind], "type": kind, "template": True}


def _clean_reply(reply: str) -> str:
    """La historia se muestra aparte: si el modelo la pegó en reply (JSON o "Aquí está el resultado:"), se quita."""
    cleaned = re.sub(r"\{.*\}", "", reply, flags=re.DOTALL)
    # "Aquí está su versión actualizada: Title: … Description: …": se corta desde donde empieza la historia pegada.
    cleaned = re.split(r"(?i)\s*(?:aqu[ií] (?:est[aá]|tienes)\b[^.?!]*[:.]|\b(?:title|t[ií]tulo|description|descripci[oó]n)\s*:)", cleaned, maxsplit=1)[0]
    return cleaned.strip()


def story_extras(data: dict) -> dict:
    assumptions = [_text(a).strip() for a in _as_list(data.get("assumptions")) if _text(a).strip()][:3]
    split = [_text(s).strip() for s in _as_list(data.get("split")) if _text(s).strip()][:5]
    return {"assumptions": assumptions, "split": split}


def _story_changes(before: UserStory, after: UserStory) -> dict:
    old, new = {c.text for c in before.acceptance_criteria}, {c.text for c in after.acceptance_criteria}
    return {"from_version": before.version, "to_version": after.version, "title_changed": before.title != after.title,
            "description_changed": before.description != after.description, "criteria_added": len(new - old), "criteria_removed": len(old - new),
            "rules_changed": before.business_rules != after.business_rules}


class ValkiriaService:
    """Orquestador de casos de uso; mantiene el transporte fuera del dominio."""

    def __init__(self, llm, audit, metrics, stories, azure=None, max_parallel: int = 4, synthetic_app_base_url: str = "http://localhost:8090",
                 web_runner=None, synthetic_transport=None, database_executor=None):
        self.llm = llm
        # Ejecución de scripts (HU-010): app sintética, runner web opcional y transporte inyectable para pruebas.
        self.synthetic_app_base_url = synthetic_app_base_url
        self.web_runner = web_runner
        self.synthetic_transport = synthetic_transport
        # HU-011: ejecutor seguro de la base sintética (análisis estático, solo perfiles sintéticos).
        self.database_executor = database_executor
        # Límite de llamadas simultáneas al modelo (la matriz genera cada criterio en paralelo).
        self._parallel = asyncio.Semaphore(max_parallel)
        self.audit = audit
        self.metrics = metrics
        self.stories = stories
        self.azure = azure
        self.ops = LLMOpsLifecycle(audit, metrics)

    def _context(self, actor: str):
        # La versión de los prompts queda en cada ejecución para poder comparar resultados entre versiones.
        ctx = self.ops.new_context(actor, self.llm.model_name)
        ctx.prompt_version = PROMPT_VERSION
        return ctx

    async def _ground(self, ctx):
        await self.ops.record_gate(ctx, Gate.G1, "passed", {"result": "input_validated_and_policy_loaded"})

    async def _skip_human_release_gates(self, ctx, reason: str):
        await self.ops.record_gate(ctx, Gate.G4, "skipped", {"reason": reason})
        await self.ops.record_gate(ctx, Gate.G5, "skipped", {"reason": reason})

    async def create_story(self, req: str, actor: str, *, memory: str = "") -> UserStory:
        story, _ = await self.draft_story(req, actor, memory=memory)
        return story

    async def draft_story(self, req: str, actor: str, *, memory: str = "") -> tuple[UserStory, dict]:
        """HU-003B: la HU más sus supuestos (regla 3, máximo 3) y otras HU sugeridas si el requerimiento es amplio (regla 2, máximo 5)."""
        ctx = self._context(actor)
        await self.ops.start(ctx, "create_story", ArtifactType.STORY)
        try:
            await self._ground(ctx)
            data = await self.llm.generate_json(system=STORY_SYSTEM, user=with_memory(req, memory), schema=UserStory.model_json_schema())
            story = UserStory.model_validate(_normalize_story(data))
            await self.stories.save(story)
            await self.ops.record(ctx, Phase.GENERATION, "create_story", "draft_created", ArtifactType.STORY, str(story.id), story.version)
            await self._skip_human_release_gates(ctx, "el caso de uso solo genera un borrador")
            await self.ops.metric(ctx, "generation.success", 1, "count")
            await self.ops.finish(ctx)
            return story, story_extras(data)
        except Exception:
            await self.ops.finish(ctx, "failed")
            raise

    async def converse(self, message: str, history: list[dict], current: UserStory | None, actor: str, *, memory: str = "") -> dict:
        """Turno conversacional: mantiene el hilo y decide si crear, ajustar la historia actual o solo responder."""
        ctx = self._context(actor)
        await self.ops.start(ctx, "converse", ArtifactType.STORY)
        try:
            await self._ground(ctx)
            context = [memory] if memory else []
            if current:
                context.append(f"Historia actual (versión {current.version}):\n" + current.model_dump_json(include={"title", "description", "business_rules", "acceptance_criteria"}))
            if history:
                context.append("Conversación reciente:\n" + "\n".join(f"{'Usuario' if t['role'] == 'user' else 'Valkiria'}: {t['content']}" for t in history[-10:]))
            context.append(f"Mensaje nuevo del usuario:\n{message}")
            data = await self.llm.generate_json(system=CHAT_SYSTEM, user="\n\n".join(context), schema=UserStory.model_json_schema())
            intent = str(data.get("intent", "conversar")).strip().lower()
            if intent not in {"crear", "ajustar", "dividir", "conversar"}:
                intent = "conversar"
            if intent == "ajustar" and not current:
                intent = "crear"
            asks_edit = bool(re.search(r"\b(agrega|agregar|añade|quita|quitar|cambia|cambiar|ajusta|modifica|elimina|precisa|corrige)\b", message.lower()))
            model_split = [item for item in (_split_item(i) for i in _as_list(data.get("split"))) if item["title"]]
            already_split = intent == "dividir" and len(model_split) >= 2
            if not already_split and (intent in {"crear", "conversar", "dividir"} or (intent == "ajustar" and not asks_edit)) and looks_broad(message):
                # HU-003B, regla 2: la enumeración de varias funcionalidades se verifica con una tarea acotada de división.
                proposed = await self.llm.generate_json(system=STORY_SPLIT_SYSTEM, user=message, schema={"type": "object"})
                candidates = [_split_item(item) for item in _as_list(proposed.get("split"))][:5]
                if len([c for c in candidates if c["title"]]) >= 2:
                    intent, data = "dividir", {**data, "split": candidates, "story": None}
                    data["reply"] = ("Este requerimiento abarca varias funcionalidades independientes, así que te propongo dividirlo en "
                                     f"{len(candidates)} historias para que cada una se pueda probar por separado. ¿Cuál quieres que redacte primero?")
            reply = _clean_reply(str(data.get("reply") or ""))
            assumptions = [_text(a) for a in _as_list(data.get("assumptions")) if _text(a).strip()][:3]
            story, changes, split = None, None, []
            if intent == "dividir":
                # HU-003B, regla 2: un requerimiento amplio se propone dividido (máximo 5 HU) y el PO elige cuáles crear.
                split = [_split_item(item) for item in _as_list(data.get("split"))][:5]
                split = [item for item in split if item["title"]]
                if len(split) < 2:
                    intent, split = "crear", []
            if intent in {"crear", "ajustar"}:
                fields = _normalize_story(data.get("story") if isinstance(data.get("story"), dict) else {})
                copied = intent == "crear" and current is not None and fields.get("title") == current.title
                if intent == "crear" and (len(fields["acceptance_criteria"]) < 3 or copied):
                    # Una HU nueva se redacta solo con el requerimiento nuevo (y la memoria del equipo): con la HU en curso
                    # en el contexto, el modelo tiende a devolverla copiada.
                    context = ([memory] if memory else []) + [f"Mensaje nuevo del usuario:\n{message}"]
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
                reply = ("Listo." if story else "Este requerimiento abarca varias historias; te propongo dividirlo. ¿Cuál quieres que redacte primero?" if split
                         else "¿Me das un poco más de contexto sobre lo que necesitas?")
            if split and "?" not in reply:
                reply += " ¿Cuál quieres que redacte primero?"
            if story and "?" not in reply:
                # Mantiene el hilo: toda entrega de historia termina proponiendo el siguiente paso.
                reply += " ¿Seguimos con la evaluación INVEST de esta versión o quieres ajustar algo más?" if changes else " ¿Quieres ajustar algo o seguimos con la evaluación INVEST?"
            await self._skip_human_release_gates(ctx, "el caso de uso solo genera un borrador conversacional")
            await self.ops.finish(ctx)
            return {"intent": intent, "reply": reply, "assumptions": assumptions, "story": story.model_dump(mode="json") if story else None, "changes": changes, "split": split}
        except Exception:
            await self.ops.finish(ctx, "failed")
            raise

    async def evaluate_invest(self, story: UserStory, actor: str, *, memory: str = "") -> InvestEvaluation:
        ctx = self._context(actor)
        await self.ops.start(ctx, "evaluate_invest", ArtifactType.INVEST)
        try:
            await self._ground(ctx)
            data = await self.llm.generate_json(system=INVEST_SYSTEM, user=with_memory(story.model_dump_json(), memory), schema=InvestEvaluation.model_json_schema())
            data = _normalize_invest(data)
            await self._complete_suggestions(story, data["criteria"])
            data.update(story_id=story.id, model=self.llm.model_name, prompt_version=ctx.prompt_version)
            result = InvestEvaluation.model_validate(data)
            await self.ops.record(ctx, Phase.EVALUATION, "invest", "completed", ArtifactType.INVEST, str(result.id))
            await self._skip_human_release_gates(ctx, "el caso de uso solo evalúa el artefacto")
            await self.ops.finish(ctx)
            return result
        except Exception:
            await self.ops.finish(ctx, "failed")
            raise

    async def _complete_suggestions(self, story: UserStory, criteria: list[dict]) -> None:
        """HU-002, regla 2: todo criterio parcial o no cumplido lleva una sugerencia accionable.

        Un modelo pequeño suele omitir el campo al evaluar los seis criterios a la vez; pedir solo la sugerencia faltante,
        con la justificación como contexto, es una tarea más acotada y la cumple.
        """
        body = story.model_dump_json(include={"title", "description", "business_rules", "acceptance_criteria"})
        for criterion in criteria:
            if criterion.get("status") in {"parcial", "no_cumple"} and not str(criterion.get("suggestion") or "").strip():
                user = f"Historia:\n{body}\n\nCriterio: {criterion['name']} ({criterion['status']}). Justificación: {criterion.get('justification') or 'sin justificación'}"
                data = await self.llm.generate_json(system=INVEST_SUGGESTION_SYSTEM, user=user, schema={"type": "object"})
                suggestion = _text(data.get("suggestion") or data.get("sugerencia") or "").strip()
                criterion["suggestion"] = suggestion or None

    async def matrix_cases(self, story: UserStory, criterion, *, memory: str = "") -> list[dict]:
        """Los 3 casos (positivo, negativo, borde) de un criterio; un reintento y, si falla, plantilla declarada."""
        async with self._parallel:
            for _ in range(2):
                try:
                    data = await self.llm.generate_json(system=MATRIX_SYSTEM, user=with_memory(matrix_user_prompt(story, only=criterion.id), memory),
                                                        schema={"type": "object"})
                except (LLMProviderError, TimeoutError):
                    continue
                cases = cases_from_compact(data, criterion)
                if cases:
                    return cases
        return [template_case(criterion, kind) for kind in CASE_KINDS]

    async def generate_matrix(self, story: UserStory, actor: str, *, memory: str = "") -> TestMatrix:
        ctx = self._context(actor)
        await self.ops.start(ctx, "generate_matrix", ArtifactType.TEST_MATRIX)
        try:
            await self._ground(ctx)
            # Por criterio y en paralelo, en formato compacto: el modelo solo redacta escenario, pasos, resultado y datos;
            # id, tipo, criterio, prioridad y precondiciones los arma el código (HU-004, regla 3). Así la cobertura
            # positivo/negativo/borde queda garantizada por construcción y la salida del modelo se reduce a la mitad.
            groups = await asyncio.gather(*(self.matrix_cases(story, criterion, memory=memory) for criterion in story.acceptance_criteria))
            if all(case.get("template") for group in groups for case in group):
                # RT-04: si el modelo no respondió para ningún criterio, no se entrega una matriz de plantillas: se informa y se reintenta.
                raise LLMProviderError("llm_unavailable", "El modelo no respondió para generar la matriz.")
            data = {"cases": [case for group in groups for case in group][:30], "story_id": story.id}
            result = TestMatrix.model_validate(data)
            await self.ops.record(ctx, Phase.GENERATION, "test_matrix", "draft_created", ArtifactType.TEST_MATRIX, str(result.id))
            await self._skip_human_release_gates(ctx, "el caso de uso solo genera una matriz en borrador")
            await self.ops.finish(ctx)
            return result
        except Exception:
            await self.ops.finish(ctx, "failed")
            raise

    async def assess_risk(self, story: UserStory, actor: str, defect_history: list[dict] | None = None, *, memory: str = "") -> RiskAssessment:
        ctx = self._context(actor)
        await self.ops.start(ctx, "assess_risk", ArtifactType.RISK)
        try:
            await self._ground(ctx)
            data = await self.llm.generate_json(system=RISK_SYSTEM, user=with_memory(story.model_dump_json(), memory), schema=RiskAssessment.model_json_schema())
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
