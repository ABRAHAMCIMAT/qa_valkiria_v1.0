"""Evaluación de los system prompts contra el modelo real (Llama 3.2 Instruct vía Ollama o cualquier endpoint compatible).

Mide la salida CRUDA de cada prompt, antes de las correcciones y completados automáticos del flujo, con las mismas
reglas de negocio que aplican los validadores (HU-002, HU-003, HU-004, HU-005). Así un cambio de prompt se decide con datos.

Uso (con Ollama y la app sintética en marcha, por ejemplo con docker-compose.synthetic.yml):
    python evals/prompt_eval.py --samples 2 --out evals/results/v2.json
    python evals/prompt_eval.py --tasks story,invest --samples 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from valkiria.application import prompts
from valkiria.application.use_cases import (
    ValkiriaService,
    _normalize_invest,
    _normalize_matrix,
    _normalize_story,
    matrix_user_prompt,
)
from valkiria.assistant.agent import ReasoningAssistant
from valkiria.assistant.catalog import build_toolbox
from valkiria.domain.models import (
    InvestEvaluation,
    RiskAssessment,
    TestMatrix,
    UserStory,
)
from valkiria.infrastructure.memory import (
    InMemoryAudit,
    InMemoryMetrics,
    InMemoryStories,
)
from valkiria.infrastructure.settings import Settings
from valkiria.memory.text import fold
from valkiria.providers.openai_compatible import OpenAICompatibleLLM
from valkiria.workflow.validators import (
    INVEST_NAMES,
    missing_case_slots,
    validate_invest,
    validate_matrix,
)

REQUIREMENTS = [
    "Consultar vehículos Nissan disponibles por concesionario",
    "Registrar una orden de venta de un vehículo validando que haya stock y que el concesionario esté activo",
    "Agendar una cita de servicio para un vehículo desde el portal del cliente",
    "Como gerente regional quiero un reporte mensual de ventas por región exportable a Excel",
]

STORIES = [
    {"title": "Consultar vehículos disponibles por concesionario", "description": "Como asesor de ventas, quiero consultar los vehículos disponibles por concesionario, para ofrecer al cliente solo lo que hay en stock.",
     "business_rules": ["Solo se muestran vehículos con stock mayor a cero", "Solo concesionarios activos"],
     "acceptance_criteria": [{"id": "AC-01", "text": "Dado un concesionario activo con stock, cuando consulto su inventario, entonces veo modelo, año, precio y stock"},
                             {"id": "AC-02", "text": "Dado un vehículo con stock cero, cuando consulto, entonces no aparece en la lista"},
                             {"id": "AC-03", "text": "Dado un concesionario inactivo, cuando consulto, entonces el sistema indica que no está disponible"}]},
    {"title": "Registrar orden de venta", "description": "Como asesor, quiero registrar una orden de venta, para apartar el vehículo al cliente.",
     "business_rules": ["No se vende un vehículo sin stock"],
     "acceptance_criteria": [{"id": "AC-01", "text": "Dado un vehículo con stock y un cliente existente, cuando registro la orden, entonces queda aprobada"},
                             {"id": "AC-02", "text": "Dado un vehículo sin stock, cuando registro la orden, entonces se rechaza con el motivo"}]},
    {"title": "Todo el portal de clientes", "description": "Como cliente, quiero un portal para ver mis autos, agendar citas, pagar servicios, chatear con asesores y recibir promociones.",
     "business_rules": [], "acceptance_criteria": [{"id": "AC-01", "text": "El portal funciona bien"}]},
]

# (mensaje, hay HU en curso, intención esperada, tono esperado en la respuesta)
CHAT_CASES = [
    ("Necesito que los asesores vean el stock por agencia para cerrar ventas más rápido", False, "crear", None),
    ("Agrega un criterio para cuando el vehículo no tiene stock", True, "ajustar", None),
    ("¿Por qué incluiste la regla de concesionarios activos?", True, "conversar", None),
    ("hola, buen día", False, "conversar", r"\b(hola|buen|buenos|buenas)\b"),
    ("Cambia el título a Consulta de inventario por agencia", True, "ajustar", None),
    ("algo de ventas", False, "conversar", None),
    ("¡Hola! ¿Cómo estás?", False, "conversar", r"\b(hola|buen|buenos|buenas|bien)\b"),
    ("Gracias, quedó muy bien la historia", True, "conversar", r"\b(gracias|gusto|a ti|me alegra)\b"),
    ("No entiendes nada, eso no es lo que pedí", True, "conversar", r"\b(disculpa|lo siento|perdon|entiendo|lamento)\b"),
    ("Necesito un portal donde los clientes vean sus autos, agenden citas de servicio, paguen en línea y chateen con asesores", False, "dividir", None),
]

ASSISTANT_CASES: list[tuple[str, dict[str, Any]]] = [
    ("¿Qué vehículos Nissan hay disponibles en inventario?", {"tool": "inventario_nissan", "all": ["sentra", "versa"], "qualified": [("kicks", r"sin stock|agotad|no disponible|stock 0")]}),
    ("¿Qué concesionarios están activos?", {"tool": "concesionarios_nissan", "all": ["apodaca"], "qualified": [("centro", "inactiv")]}),
    ("Revisa si este script es seguro: DELETE FROM vehicles WHERE stock = 0", {"tool": "analizar_script_sql", "any": ["no es seguro", "inseguro", "no seguro"]}),
    ("Reserva un vuelo a Cancún para el lunes", {"status": "unsupported"}),
    ("¿Qué es una prueba de estrés y en qué se diferencia de una de carga?", {"tool": "glosario_qa", "all": ["quiebre"]}),
    ("¿Cuántas citas de servicio hay programadas?", {"tool": "citas_servicio", "all": ["1"]}),
    ("Diseña una prueba de carga para 200 usuarios durante 600 segundos con SLA de 800 ms", {"tool": "disenar_prueba_performance", "all": ["200", "800"]}),
    ("¿Qué herramienta de automatización uso para una app móvil?", {"tool": "herramienta_automatizacion", "all": ["appium"]}),
    ("¿Qué puedes hacer?", {"tool": "capacidades"}),
    ("Genera el pipeline de Azure DevOps", {"tool": "generar_pipeline_azure"}),
    ("¿Qué herramienta me recomiendas para probar una base Oracle con Java?", {"tool": "herramienta_bd", "all": ["jdbc"]}),
    ("Envía la matriz por correo al PO", {"status": "unsupported"}),
    ("¿Qué es mutation testing?", {"tool": "glosario_qa", "all": ["mutante"]}),
    # Nuevos: no se usaron para ajustar el asistente (detectan sobreajuste).
    ("¿Qué modelos Nissan no tienen stock?", {"tool": "inventario_nissan", "all": ["kicks"]}),
    ("¿Cuál es el máximo de casos por matriz de pruebas?", {"tool": "politicas", "all": ["30"]}),
    ("Escríbeme un poema sobre autos deportivos", {"status": "unsupported"}),
    ("¿Cuáles son los criterios INVEST?", {"tool": "glosario_qa", "all": ["independiente", "testeable"]}),
    ("¿Me recomiendas un restaurante para comer hoy?", {"status": "unsupported", "all": ["lo siento"]}),
    ("Diseña una prueba de carga para 100 usuarios durante 5 minutos", {"any": ["sla"], "ends_with_question": True}),
]

GWT = re.compile(r"\bdado\b.*\bcuando\b.*\bentonces\b")
USER_VOICE = re.compile(r"^\s*como\b.+\bquiero\b.+\bpara\b")
MARKDOWN = re.compile(r"(\*\*|^#|^\s*[-*] )", re.MULTILINE)


def rate(values: list[bool]) -> float:
    return round(sum(values) / len(values), 2) if values else 0.0


class Eval:
    def __init__(self, llm: OpenAICompatibleLLM, samples: int, synthetic_url: str):
        self.llm = llm
        self.samples = samples
        self.synthetic_url = synthetic_url
        self.latency: dict[str, list[float]] = {}

    async def call(self, task: str, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any] | None:
        started = time.perf_counter()
        try:
            return await self.llm.generate_json(system=system, user=user, schema=schema)
        except Exception:  # noqa: BLE001 - una salida inválida cuenta como fallo de la métrica, no del evaluador
            return None
        finally:
            self.latency.setdefault(task, []).append(time.perf_counter() - started)

    async def story(self) -> dict[str, float]:
        m: dict[str, list[bool]] = {k: [] for k in ("json", "raw_shape", "valid", "criteria_3_6", "gwt_all", "user_voice")}
        for requirement in REQUIREMENTS:
            for _ in range(self.samples):
                out = await self.call("story", prompts.STORY_SYSTEM, requirement, UserStory.model_json_schema())
                m["json"].append(out is not None)
                out = out or {}
                criteria = out.get("acceptance_criteria")
                m["raw_shape"].append(isinstance(out.get("title"), str) and isinstance(out.get("description"), str) and isinstance(out.get("business_rules"), list)
                                      and isinstance(criteria, list) and all(isinstance(c, dict) and {"id", "text"} <= set(c) for c in criteria))
                fields = _normalize_story(out)
                try:
                    UserStory.model_validate(fields)
                    m["valid"].append(True)
                except ValueError:
                    m["valid"].append(False)
                texts = [fold(c["text"]) for c in fields.get("acceptance_criteria", [])]
                m["criteria_3_6"].append(3 <= len(texts) <= 6)
                m["gwt_all"].append(bool(texts) and all(GWT.search(t) for t in texts))
                m["user_voice"].append(bool(USER_VOICE.search(fold(str(fields.get("description", ""))))))
        return {k: rate(v) for k, v in m.items()}

    async def invest(self) -> dict[str, float]:
        m: dict[str, list[bool]] = {k: [] for k in ("json", "exact_names", "valid_status", "no_findings", "no_findings_final", "no_noise_suggestions", "small_story_flagged")}
        service = ValkiriaService(self.llm, InMemoryAudit(), InMemoryMetrics(), InMemoryStories())
        for index, story in enumerate(STORIES):
            for _ in range(self.samples):
                out = await self.call("invest", prompts.INVEST_SYSTEM, json.dumps(story, ensure_ascii=False), InvestEvaluation.model_json_schema()) or {}
                m["json"].append(bool(out))
                raw = out.get("criteria") if isinstance(out.get("criteria"), list) else []
                m["exact_names"].append(sorted(fold(str(c.get("name", ""))) for c in raw if isinstance(c, dict)) == sorted(INVEST_NAMES))
                m["valid_status"].append(bool(raw) and all(isinstance(c, dict) and c.get("status") in {"cumple", "parcial", "no_cumple"} for c in raw))
                criteria = _normalize_invest(out)["criteria"]
                m["no_findings"].append(not validate_invest(criteria))
                m["no_noise_suggestions"].append(not any(c["status"] == "cumple" and c.get("suggestion") for c in criteria))
                # Resultado final que ve el PO: la evaluación más el completado de sugerencias faltantes (HU-002, regla 2).
                await service._complete_suggestions(UserStory.model_validate(story), criteria)
                m["no_findings_final"].append(not validate_invest(criteria))
                if index == 2:  # HU deliberadamente enorme y no testeable: Pequeña y Testeable no deben "cumplir"
                    by_name = {fold(c["name"]): c["status"] for c in criteria}
                    m["small_story_flagged"].append(by_name.get("pequena") != "cumple" and by_name.get("testeable") != "cumple")
        return {k: rate(v) for k, v in m.items()}

    async def matrix(self) -> dict[str, float]:
        m: dict[str, list[bool]] = {k: [] for k in ("json", "no_findings", "max_30", "exact_criterion_ids")}
        coverage: list[float] = []
        for story in STORIES[:2]:
            ids = [c["id"] for c in story["acceptance_criteria"]]
            for _ in range(self.samples):
                out = await self.call("matrix", prompts.MATRIX_SYSTEM, matrix_user_prompt(UserStory.model_validate(story)), TestMatrix.model_json_schema()) or {}
                m["json"].append(bool(out))
                cases = _normalize_matrix(out)["cases"]
                m["no_findings"].append(bool(cases) and not validate_matrix(cases, ids))
                m["max_30"].append(len(cases) <= 30)
                m["exact_criterion_ids"].append(bool(cases) and all(c["criterion_id"] in ids for c in cases))
                coverage.append(1 - len(missing_case_slots(cases, ids)) / (3 * len(ids)))
        return {k: rate(v) for k, v in m.items()} | {"coverage": round(statistics.mean(coverage), 2)}

    async def risk(self) -> dict[str, float]:
        m: dict[str, list[bool]] = {k: [] for k in ("json", "valid_level_raw", "justified", "mitigation", "huge_story_not_low")}
        for index, story in enumerate(STORIES):
            for _ in range(self.samples):
                out = await self.call("risk", prompts.RISK_SYSTEM, json.dumps(story, ensure_ascii=False), RiskAssessment.model_json_schema()) or {}
                m["json"].append(bool(out))
                m["valid_level_raw"].append(out.get("level") in {"low", "medium", "high"})
                m["justified"].append(len(str(out.get("justification") or "").split()) >= 8)
                m["mitigation"].append(len(str(out.get("mitigation") or "").split()) >= 5)
                if index == 2:
                    m["huge_story_not_low"].append(out.get("level") in {"medium", "high"})
        return {k: rate(v) for k, v in m.items()}

    async def revision(self) -> dict[str, float]:
        m: dict[str, list[bool]] = {k: [] for k in ("json", "applied", "kept_title", "kept_criteria")}
        story = STORIES[0]
        user = ("Historia actual:\n" + json.dumps(story, ensure_ascii=False) + "\n\nSugerencias aprobadas:\n"
                + json.dumps([{"criterio": "Testeable", "sugerencia": "Agregar un criterio para un vehículo con stock igual a 1"}], ensure_ascii=False))
        for _ in range(self.samples * 2):
            out = await self.call("revision", prompts.REVISION_SYSTEM, user, UserStory.model_json_schema()) or {}
            m["json"].append(bool(out))
            fields = _normalize_story(out)
            texts = [fold(c["text"]) for c in fields.get("acceptance_criteria", [])]
            m["applied"].append(any(re.search(r"\b(1|uno|una unidad)\b", t) for t in texts))
            m["kept_title"].append(fields.get("title") == story["title"])
            m["kept_criteria"].append(all(fold(c["text"]) in texts for c in story["acceptance_criteria"]))
        return {k: rate(v) for k, v in m.items()}

    async def chat(self) -> dict[str, float]:
        m: dict[str, list[bool]] = {k: [] for k in ("json", "intent", "story_when_needed", "split_when_broad", "tone", "not_dry", "max_2_questions",
                                                     "reply_2_4_sentences", "no_markdown", "no_story_copy")}
        current = STORIES[0]
        for message, has_story, expected, tone in CHAT_CASES:
            parts = ([f"Historia actual (versión 1):\n{json.dumps(current, ensure_ascii=False)}"] if has_story else []) + [f"Mensaje nuevo del usuario:\n{message}"]
            for _ in range(self.samples):
                out = await self.call("chat", prompts.CHAT_SYSTEM, "\n\n".join(parts), UserStory.model_json_schema()) or {}
                m["json"].append(bool(out))
                intent = str(out.get("intent", "")).strip().lower()
                m["intent"].append(intent == expected)
                story = out.get("story") if isinstance(out.get("story"), dict) else None
                if expected in {"crear", "ajustar"}:
                    m["story_when_needed"].append(bool(story) and len(_normalize_story(story)["acceptance_criteria"]) >= 3)
                if expected == "dividir":
                    split = out.get("split") if isinstance(out.get("split"), list) else []
                    m["split_when_broad"].append(2 <= len(split) <= 5 and not story)
                reply = str(out.get("reply") or "")
                if tone:
                    m["tone"].append(bool(re.search(tone, fold(reply))))
                m["not_dry"].append(len(reply.split()) >= 8)
                m["max_2_questions"].append(reply.count("?") <= 2)
                sentences = [s for s in re.split(r"[.!?]+\s", reply.strip()) if s.strip()]
                m["reply_2_4_sentences"].append(1 <= len(sentences) <= 5)
                m["no_markdown"].append(not MARKDOWN.search(reply))
                m["no_story_copy"].append(not story or fold(str(story.get("description", ""))[:60]) not in fold(reply))
        return {k: rate(v) for k, v in m.items()}

    async def generation(self) -> dict[str, float]:
        m: dict[str, list[bool]] = {k: [] for k in ("json", "required_keys", "spanish")}
        for request in ("Generar una historia para consultar vehículos Nissan y preparar una vista previa de Pull Request", "Validar vehículos Nissan contra una base sintética PostgreSQL"):
            for _ in range(self.samples):
                out = await self.call("generation", prompts.GENERATION_SYSTEM, request, {"type": "object", "required": ["summary", "deliverables", "acceptance_criteria"]}) or {}
                m["json"].append(bool(out))
                m["required_keys"].append({"summary", "deliverables", "acceptance_criteria"} <= set(out))
                m["spanish"].append(bool(re.search(r"\b(el|la|de|para|los)\b", fold(json.dumps(out, ensure_ascii=False)))))
        return {k: rate(v) for k, v in m.items()}

    async def assistant(self) -> dict[str, Any]:
        service = ValkiriaService(self.llm, InMemoryAudit(), InMemoryMetrics(), InMemoryStories())
        bot = ReasoningAssistant(self.llm, build_toolbox(service=service, memory=None, workflows=None, synthetic_app_base_url=self.synthetic_url))
        passed: list[bool] = []
        failures: list[str] = []
        for question, expect in ASSISTANT_CASES:
            for _ in range(self.samples):
                started = time.perf_counter()
                answer = await bot.ask(question)
                self.latency.setdefault("assistant", []).append(time.perf_counter() - started)
                text = fold(answer.answer)
                ok = answer.status == expect.get("status", "answered")
                ok = ok and (not expect.get("tool") or expect["tool"] in answer.tools_used)
                ok = ok and all(fold(s) in text for s in expect.get("all", []))
                ok = ok and (not expect.get("any") or any(fold(s) in text for s in expect["any"]))
                ok = ok and all(fold(term) not in text or re.search(qualifier, text) for term, qualifier in expect.get("qualified", []))
                ok = ok and (not expect.get("ends_with_question") or answer.answer.rstrip().endswith("?"))
                passed.append(ok)
                if not ok:
                    failures.append(f"{question} → {answer.status}/{answer.tools_used}: {answer.answer[:120]}")
        return {"correct": rate(passed), "n": len(passed), "failures": failures}


def use_prompts(path: str) -> None:
    """Sustituye los prompts activos por los de otro archivo (misma interfaz de nombres) para evaluar versiones lado a lado."""
    import importlib.util

    from valkiria.assistant import agent

    spec = importlib.util.spec_from_file_location("prompts_alternativos", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in dir(module):
        if name.isupper():
            setattr(prompts, name, getattr(module, name))
    agent.SYSTEM, agent.COMPOSE_SYSTEM = module.ASSISTANT_SYSTEM, module.ASSISTANT_COMPOSE_SYSTEM
    agent.ARGS_SYSTEM, agent.FINAL_PROMPT = module.ASSISTANT_ARGS_SYSTEM, module.ASSISTANT_FINAL_TURN


TASKS: dict[str, Callable[[Eval], Awaitable[dict[str, Any]]]] = {
    "story": Eval.story, "invest": Eval.invest, "matrix": Eval.matrix, "risk": Eval.risk, "revision": Eval.revision,
    "chat": Eval.chat, "generation": Eval.generation, "assistant": Eval.assistant,
}


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tasks", default=",".join(TASKS))
    parser.add_argument("--samples", type=int, default=2)
    parser.add_argument("--synthetic-url", default="http://localhost:8090")
    parser.add_argument("--out")
    parser.add_argument("--prompts", help="Archivo .py con otra versión de los prompts, para compararla con la actual (A/B)")
    args = parser.parse_args()
    if args.prompts:
        use_prompts(args.prompts)
    settings = Settings()
    llm = OpenAICompatibleLLM(settings.llm_base_url, settings.llm_model, settings.secret("llm_api_key"), timeout_seconds=settings.llm_timeout_seconds, auth_header=settings.llm_auth_header,
                              token_budget=prompts.token_budget)
    evaluation = Eval(llm, args.samples, args.synthetic_url)
    results: dict[str, Any] = {"prompt_version": prompts.PROMPT_VERSION, "model": settings.llm_model, "samples": args.samples}
    for name in args.tasks.split(","):
        results[name] = await TASKS[name](evaluation)
        results[name]["p50_s"] = round(statistics.median(evaluation.latency.get(name, [0])), 1)
        print(f"{name:11} {json.dumps({k: v for k, v in results[name].items() if k != 'failures'}, ensure_ascii=False)}", flush=True)
        for failure in results[name].get("failures", []):
            print(f"{'':11} ✗ {failure}", flush=True)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
