"""Asistente de razonamiento para peticiones que no siguen el flujo programado.

Ciclo razonar → actuar → observar (ReAct) sobre un catálogo cerrado de herramientas y skills:
1. Las políticas (producción, commits directos, credenciales, correo, internet) se resuelven en código, sin LLM.
2. En cada paso, Llama 3.2 elige una acción en JSON: usar una herramienta, responder o declarar que no puede.
3. El código valida la herramienta y sus argumentos, la ejecuta con tiempo límite y devuelve la observación.
4. La respuesta se basa en las observaciones. Si no se usó ninguna herramienta, se declara como conocimiento
   general no verificado. Si no hay capacidad, se dice con honestidad y se listan las alternativas reales.
5. La redacción final del modelo se acepta solo si es fiel a las conclusiones que cada herramienta calcula en código
   (menciona los datos clave, no se niega teniéndolos, no contradice calificadores); si no, se usan esas conclusiones.
6. Si el modelo falla o no converge, hay un camino determinista: nunca se inventa una respuesta.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from valkiria.application.prompts import ASSISTANT_ARGS_SYSTEM as ARGS_SYSTEM
from valkiria.application.prompts import ASSISTANT_COMPOSE_SYSTEM as COMPOSE_SYSTEM
from valkiria.application.prompts import ASSISTANT_FINAL_TURN as FINAL_PROMPT
from valkiria.application.prompts import ASSISTANT_SYSTEM as SYSTEM
from valkiria.application.qa_artifacts import PolicyViolation
from valkiria.assistant.capabilities import (
    capability_summary,
    honest_unsupported,
    policy_block,
)
from valkiria.assistant.glossary import exact_terms, is_conceptual
from valkiria.assistant.tools import (
    Summary,
    Tool,
    ToolArgumentError,
    ToolBox,
    ToolContext,
    compact,
)
from valkiria.infrastructure.logging import event, get_logger
from valkiria.memory.text import fold, tokens
from valkiria.providers.openai_compatible import LLMProviderError

DUPLICATE = "Consulta repetida: ya tienes ese resultado."
_REFUSAL = re.compile(r"\b(no (se )?puedo|no se puede|no tengo (la )?(capacidad|informacion|acceso)|no es posible|no cuento con)\b")
# Hablar de la herramienta o de la observación en vez de responder ("otros que no se muestran en esta observación") no es fiel a los datos.
_META = re.compile(r"observacion|la herramienta|no se muestra|\{\}|json|argumento")
UNGROUNDED_NOTE = "Nota: respondí con conocimiento general de QA; no lo verifiqué con herramientas de Valkiria."
_ACTIONS = {"usar_herramienta": "tool", "tool": "tool", "use_tool": "tool", "herramienta": "tool", "responder": "answer", "answer": "answer", "respuesta": "answer",
            "final": "answer", "no_puedo": "cannot", "cannot": "cannot", "unsupported": "cannot"}


class Step(BaseModel):
    number: int
    reason: str = ""
    action: str
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    observation: str | None = None
    error: str | None = None


class AssistantAnswer(BaseModel):
    status: Literal["answered", "unsupported"]
    answer: str
    grounded: bool = False
    tools_used: list[str] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)
    # llm: redacción del modelo verificada; tool: conclusión verificada de la herramienta; deterministic: sin modelo; policy: regla de seguridad.
    mode: Literal["llm", "tool", "deterministic", "policy"] = "llm"
    missing_capability: str | None = None
    capabilities: dict[str, list[str]] | None = None
    outputs: dict[str, Any] = Field(default_factory=dict)


def _decision(raw: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    action = _ACTIONS.get(str(raw.get("accion") or raw.get("action") or "").strip().lower(), "")
    if not action:
        # Llama 3.2 a veces omite "accion" pero trae el contenido; se infiere sin adivinar de más.
        action = "tool" if raw.get("herramienta") or raw.get("tool") else "answer" if raw.get("respuesta") or raw.get("answer") else "cannot" if raw.get("falta") else ""
    return action, raw


def _clean_missing(text: str) -> str | None:
    # "la capacidad de reservar vuelos" → "reservar vuelos": evita "No tengo la capacidad de la capacidad de…".
    if re.search(r"har[ií]a falta|capacidad que|<|>", text):
        return None  # el modelo copió la plantilla en vez de nombrar la capacidad
    cleaned = re.sub(r"^(no\s+)?(tengo\s+)?(la\s+)?(capacidad|posibilidad|habilidad|herramienta)(\s+(de|para))?\s+", "", text.strip(), flags=re.IGNORECASE).strip().rstrip(".")
    return cleaned or None


def faithful(text: str, summaries: list[Summary]) -> bool:
    """La redacción del modelo menciona los datos clave, no se niega teniéndolos y no contradice los calificadores."""
    folded = fold(text)
    if _REFUSAL.search(folded) or _META.search(folded):
        return False
    for summary in summaries:
        if summary.anchors and not all(fold(anchor) in folded for anchor in summary.anchors):
            return False
        for term, qualifier in summary.exclusive:
            if fold(term) in folded and not re.search(qualifier, folded):
                return False
    return True


@dataclass
class _Run:
    steps: list[Step] = field(default_factory=list)
    used: list[str] = field(default_factory=list)
    summaries: dict[str, Summary] = field(default_factory=dict)
    seen: set[str] = field(default_factory=set)
    reviewed: bool = False
    # Datos obligatorios que la petición no trae, por herramienta (se piden al usuario, no se inventan).
    missing: dict[str, list[str]] = field(default_factory=dict)
    question: str = ""


class ReasoningAssistant:
    def __init__(self, llm, toolbox: ToolBox, *, max_steps: int = 4, tool_timeout: float = 20.0):
        self.llm = llm
        self.toolbox = toolbox
        self.max_steps = max_steps
        self.tool_timeout = tool_timeout
        self.logger = get_logger("valkiria.assistant")

    async def ask(self, question: str, *, ctx: ToolContext | None = None, context: str = "") -> AssistantAnswer:
        ctx = ctx or ToolContext()
        block = policy_block(question)
        if block:
            text = honest_unsupported(question, missing=block.capability, reason=block.reason, alternative=block.alternative, toolbox=self.toolbox)
            return AssistantAnswer(status="unsupported", answer=text, mode="policy", missing_capability=block.capability, capabilities=capability_summary(self.toolbox))
        if self.llm is None:
            return await self._deterministic(question, ctx, [], "No hay un modelo configurado.")
        run = _Run(question=question)
        # Pista determinista: las herramientas más afines a la petición orientan a un modelo pequeño.
        hints = self.toolbox.closest(question, limit=3)
        glossary = self.toolbox.get("glosario_qa")
        if glossary and is_conceptual(question) and exact_terms(question):
            # Conceptos presentes en el glosario: se consulta primero la fuente verificada, sin depender de que el modelo lo decida.
            await self._use_tool(run, "El concepto está en el glosario verificado.", {"herramienta": glossary.name, "argumentos": {"termino": question}}, ctx)
        system = SYSTEM.format(max_steps=self.max_steps, catalog=self.toolbox.catalog())
        draft: str | None = None
        draft_grounded = False
        refused: str | None = None
        while len(run.steps) < self.max_steps + 1:
            final_turn = len(run.steps) >= self.max_steps
            try:
                raw = await self.llm.generate_json(system=system, user=self._prompt(question, context, run.steps, final_turn, hints), schema={"type": "object"})
            except (LLMProviderError, TimeoutError, ValueError, TypeError) as exc:
                event(self.logger, logging.WARNING, "asistente_llm_fallo", trace_id=ctx.trace_id, error_type=type(exc).__name__)
                if run.used:
                    return await self._compose(question, run, None, ctx, use_llm=False)
                return await self._deterministic(question, ctx, run.steps, "El modelo no respondió a tiempo.")
            action, data = _decision(raw)
            raw_action = str(data.get("accion") or data.get("action") or "").strip()
            if not action and self.toolbox.resolve(raw_action):
                # Llama 3.2 a veces pone el nombre de la herramienta como acción y sus argumentos sueltos.
                loose = {k: v for k, v in data.items() if k not in {"accion", "action", "razon", "reason"}}
                action, data = "tool", {**data, "herramienta": raw_action, "argumentos": data.get("argumentos") or loose}
            reason = str(data.get("razon") or data.get("reason") or "")[:300]
            number = len(run.steps) + 1
            if action == "answer":
                draft = html.unescape(str(data.get("respuesta") or data.get("answer") or "")).strip()
                draft_grounded = bool(run.used)
                strong = self._strong_hint(question, hints)
                if draft and not run.used and strong and not run.reviewed and not final_turn:
                    # Responder de memoria cuando hay una herramienta claramente afín suele producir datos inventados.
                    run.reviewed, draft = True, None
                    run.steps.append(Step(number=number, reason=reason, action="answer", error=f"Antes de responder de memoria, usa la herramienta {strong.name}: {strong.description}"))
                    continue
                if draft:
                    run.steps.append(Step(number=number, reason=reason, action="answer"))
                    break
                run.steps.append(Step(number=number, reason=reason, action="invalid", error="respuesta vacía"))
                continue
            if action == "cannot":
                if run.used:
                    break  # con datos en mano, un "no puedo" es un error del modelo: se redacta con lo observado
                if not run.reviewed and not final_turn and (hints or is_conceptual(question)):
                    # Verificar antes de rendirse: revisar las herramientas afines, o responder con conocimiento general si es un concepto de QA.
                    run.reviewed = True
                    review = ("Es una pregunta conceptual de QA: si ninguna herramienta aplica, responde con tu conocimiento general." if is_conceptual(question)
                              else "Antes de declarar que no puedes, revisa si alguna aplica: " + ", ".join(t.name for t in hints) + ".")
                    run.steps.append(Step(number=number, reason=reason, action="cannot", error=review))
                    continue
                refused = _clean_missing(str(data.get("falta") or data.get("missing") or "")) or ""
                run.steps.append(Step(number=number, reason=reason, action="cannot"))
                break
            if action != "tool" or final_turn:
                error = f"acción '{raw_action}' no válida; usa usar_herramienta, responder o no_puedo" if action != "tool" else "se agotaron los pasos"
                run.steps.append(Step(number=number, reason=reason, action="invalid", error=error))
                continue
            step = await self._use_tool(run, reason, data, ctx)
            if step.error == DUPLICATE and run.used:
                break  # repetir una consulta no aporta: se redacta con lo que ya se tiene
        strong = self._strong_hint(question, hints)
        wanted = set(tokens(question))
        if strong and strong.name not in run.used and not any(wanted & self._vocabulary(name) for name in run.used):
            # El modelo se rindió, respondió de memoria o usó algo irrelevante, pero una herramienta encaja claramente: se usa.
            await self._call_hint(run, strong, question, ctx)
        if run.used:
            # Un borrador escrito antes de consultar cualquier herramienta es de memoria: no puede ser la respuesta final.
            return await self._compose(question, run, draft if draft_grounded else None, ctx)
        if draft and draft.rstrip().endswith("?"):
            # Una pregunta de aclaración (por ejemplo, pedir el SLA) no afirma nada: no lleva la nota de "no verificado".
            return AssistantAnswer(status="answered", answer=draft, steps=run.steps, outputs=ctx.outputs)
        if run.missing:
            # HU-008A, regla 3 y en general: si faltan datos obligatorios, se piden con amabilidad en vez de inventarlos.
            absent = next(iter(run.missing.values()))
            needed = absent[0] if len(absent) == 1 else ", ".join(absent[:-1]) + " y " + absent[-1]
            text = f"Con gusto te ayudo con eso. Para no inventar datos, ¿me indicas {needed}?"
            return AssistantAnswer(status="answered", answer=text, steps=run.steps, mode="tool", outputs=ctx.outputs)
        if draft:
            return AssistantAnswer(status="answered", answer=f"{draft}\n\n{UNGROUNDED_NOTE}", steps=run.steps, outputs=ctx.outputs)
        if refused is not None:
            return AssistantAnswer(status="unsupported", answer=honest_unsupported(question, missing=refused or None, reason=None, toolbox=self.toolbox), steps=run.steps,
                                   missing_capability=refused or None, capabilities=capability_summary(self.toolbox), outputs=ctx.outputs)
        return await self._deterministic(question, ctx, run.steps, "No logré decidir una acción válida.")

    def _vocabulary(self, name: str) -> set[str]:
        tool = self.toolbox.get(name)
        # Título y palabras clave curadas; la descripción menciona fuentes genéricas ("app sintética") que confunden.
        return set(tokens(" ".join((tool.title, *tool.keywords)))) if tool else set()

    @staticmethod
    def _strong_hint(question: str, hints: list[Tool]) -> Tool | None:
        if not hints:
            return None
        overlap = set(tokens(question)) & set(tokens(" ".join((hints[0].title, hints[0].description, *hints[0].keywords))))
        return hints[0] if len(overlap) >= 2 else None

    async def _call_hint(self, run: _Run, tool: Tool, question: str, ctx: ToolContext) -> None:
        """Ejecuta la herramienta afín; si requiere argumentos, se le pide al modelo solo extraerlos (tarea más simple que elegir)."""
        args: dict[str, Any] = {}
        if any(p.required for p in tool.params):
            try:
                args = await self.llm.generate_json(system=ARGS_SYSTEM, user=f"HERRAMIENTA: {tool.signature()}\nPETICIÓN: {question}", schema={"type": "object"})
            except (LLMProviderError, TimeoutError, ValueError, TypeError):
                return
            args = args.get("argumentos", args) if isinstance(args, dict) else {}
            # Al forzar la herramienta solo se envían los datos obligatorios: un opcional mal elegido
            # (por ejemplo, una herramienta no compatible con la plataforma) bloquearía una consulta válida.
            args = {p.name: args[p.name] for p in tool.params if p.required and p.name in args}
        await self._use_tool(run, f"La herramienta {tool.name} encaja claramente con la petición.", {"herramienta": tool.name, "argumentos": args}, ctx)

    async def _compose(self, question: str, run: _Run, draft: str | None, ctx: ToolContext, *, use_llm: bool = True) -> AssistantAnswer:
        """La respuesta final debe ser fiel a las conclusiones verificadas de las herramientas; si no, se usan esas conclusiones."""
        tools_used = list(dict.fromkeys(run.used))
        # Solo cuentan las consultas relacionadas con la pregunta: una herramienta irrelevante no debe dominar la respuesta.
        wanted = set(tokens(question))
        relevant = [name for name in tools_used if wanted & self._vocabulary(name)] or tools_used
        summaries = [run.summaries[name] for name in relevant]
        fallback = "\n\n".join(summary.text for summary in summaries)
        if len(summaries) == 1 and summaries[0].verbatim:
            return AssistantAnswer(status="answered", answer=fallback, grounded=True, tools_used=tools_used, steps=run.steps, mode="tool", outputs=ctx.outputs)
        if draft and faithful(draft, summaries):
            return AssistantAnswer(status="answered", answer=draft, grounded=True, tools_used=tools_used, steps=run.steps, outputs=ctx.outputs)
        if use_llm:
            try:
                raw = await self.llm.generate_json(system=COMPOSE_SYSTEM, user=f"PREGUNTA: {question}\n\nDATOS VERIFICADOS:\n{fallback}", schema={"type": "object"})
                text = html.unescape(str(raw.get("respuesta") or raw.get("answer") or "")).strip()
            except (LLMProviderError, TimeoutError, ValueError, TypeError):
                text = ""
            if text and faithful(text, summaries):
                return AssistantAnswer(status="answered", answer=text, grounded=True, tools_used=tools_used, steps=run.steps, outputs=ctx.outputs)
        return AssistantAnswer(status="answered", answer=fallback, grounded=True, tools_used=tools_used, steps=run.steps, mode="tool", outputs=ctx.outputs)

    async def _use_tool(self, run: _Run, reason: str, data: dict[str, Any], ctx: ToolContext) -> Step:
        step = await self._execute_tool(len(run.steps) + 1, reason, data, ctx, run)
        run.steps.append(step)
        return step

    async def _execute_tool(self, number: int, reason: str, data: dict[str, Any], ctx: ToolContext, run: _Run) -> Step:
        name = str(data.get("herramienta") or data.get("tool") or "").strip()
        raw_args = data.get("argumentos") or data.get("arguments") or data.get("args") or {}
        tool = self.toolbox.resolve(name)
        if not tool:
            return Step(number=number, reason=reason, action="tool", tool=name, error=f"La herramienta '{name}' no existe. Disponibles: {', '.join(t.name for t in self.toolbox.all())}.")
        raw_args = dict(raw_args) if isinstance(raw_args, dict) else {}
        for param in tool.params:
            # Valores cerrados que la petición menciona explícitamente: se toman de ella, no de lo que el modelo recuerde.
            if param.extract and run.question and param.extract(run.question) and (param.required or param.name not in raw_args):
                raw_args[param.name] = param.extract(run.question)
        try:
            args = tool.validate(raw_args)
        except ToolArgumentError as exc:
            provided = raw_args
            absent = [p.ask or p.description for p in tool.params if p.required and provided.get(p.name) in (None, "")]
            if absent:
                run.missing[tool.name] = absent
            return Step(number=number, reason=reason, action="tool", tool=tool.name, arguments=provided, error=f"Argumentos inválidos: {exc}")
        key = tool.name + json.dumps(args, sort_keys=True, default=str)
        if key in run.seen:
            return Step(number=number, reason=reason, action="tool", tool=tool.name, arguments=args, error=DUPLICATE)
        run.seen.add(key)
        try:
            result = await asyncio.wait_for(tool.handler(args, ctx), timeout=self.tool_timeout)
        except PolicyViolation as exc:
            return Step(number=number, reason=reason, action="tool", tool=tool.name, arguments=args, error=f"Bloqueado por política: {exc}")
        except (TimeoutError, httpx.HTTPError) as exc:
            return Step(number=number, reason=reason, action="tool", tool=tool.name, arguments=args, error=f"Herramienta no disponible ({type(exc).__name__}).")
        # Frontera de aislamiento: una herramienta que falla no debe tumbar la conversación ni filtrar detalles.
        except Exception as exc:  # noqa: BLE001
            event(self.logger, logging.ERROR, "herramienta_error", trace_id=ctx.trace_id, tool=tool.name, error_type=type(exc).__name__)
            return Step(number=number, reason=reason, action="tool", tool=tool.name, arguments=args, error="Error interno de la herramienta.")
        run.used.append(tool.name)
        run.summaries[tool.name] = tool.summarize(result) if tool.summarize else Summary(compact(result), verbatim=True)
        return Step(number=number, reason=reason, action="tool", tool=tool.name, arguments=args, observation=compact(result))

    def _prompt(self, question: str, context: str, steps: list[Step], final_turn: bool, hints: list | None = None) -> str:
        parts = [context] if context else []
        parts.append(f"PETICIÓN: {question}")
        if hints and not steps:
            parts.append("Herramientas que parecen relevantes: " + ", ".join(t.name for t in hints) + ".")
        for s in steps:
            if s.action == "tool":
                parts.append(f"Paso {s.number}: usaste {s.tool} con {json.dumps(s.arguments, ensure_ascii=False, default=str)} → " + (
                    f"observación: {s.observation}\n(Si esta observación responde la petición, usa ahora la acción 'responder'.)" if s.observation else f"error: {s.error}"))
            elif s.action == "cannot" and s.error:
                parts.append(f"Paso {s.number}: dijiste que no puedes. Revisión: {s.error}")
            elif s.action == "answer" and s.error:
                parts.append(f"Paso {s.number}: respondiste sin datos verificados. Revisión: {s.error}")
            elif s.error:
                parts.append(f"Paso {s.number}: respuesta inválida ({s.error}). Responde con un JSON válido del formato indicado.")
        if final_turn:
            parts.append(FINAL_PROMPT)
        return "\n\n".join(parts)

    async def _deterministic(self, question: str, ctx: ToolContext, steps: list[Step], why: str) -> AssistantAnswer:
        """Sin modelo disponible: si una herramienta sin argumentos obligatorios encaja claramente, se usa; si no, se declara el límite."""
        run = _Run(steps=steps)
        candidates = [t for t in self.toolbox.closest(question, limit=1) if not any(p.required for p in t.params)]
        if candidates:
            await self._use_tool(run, "Coincidencia directa con la petición (sin modelo).", {"herramienta": candidates[0].name, "argumentos": {}}, ctx)
            if run.used:
                summary = run.summaries[run.used[0]]
                return AssistantAnswer(status="answered", answer=f"{why} Consulté '{candidates[0].title}' directamente.\n\n{summary.text}", grounded=True, tools_used=run.used,
                                       steps=run.steps, mode="deterministic", outputs=ctx.outputs)
        text = honest_unsupported(question, missing=None, reason=why, toolbox=self.toolbox)
        return AssistantAnswer(status="unsupported", answer=text, steps=run.steps, mode="deterministic", capabilities=capability_summary(self.toolbox), outputs=ctx.outputs)
