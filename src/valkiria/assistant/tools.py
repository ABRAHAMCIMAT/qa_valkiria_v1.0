"""Herramientas (consultas atómicas) y skills (capacidades compuestas) que el asistente puede invocar.

Cada una declara sus argumentos; el asistente valida y convierte lo que propone el LLM antes de ejecutar.
El catálogo es cerrado: el modelo no puede invocar nada que no esté registrado aquí.
"""

from __future__ import annotations

import difflib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from valkiria.memory.text import tokens


class ToolArgumentError(ValueError):
    pass


_PLACEHOLDERS = {"no especificada", "no especificado", "ninguna", "ninguno", "none", "null", "n/a", "na", "opcional", "tool", "string", "valor", "cualquiera", "default", "por defecto"}


def _placeholder(value: Any, name: str) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip().lower()
    return text in _PLACEHOLDERS or text == name.lower() or (text.startswith("<") and text.endswith(">"))


@dataclass(frozen=True)
class Param:
    name: str
    type: Literal["string", "integer", "boolean"]
    description: str
    required: bool = True
    choices: tuple[str, ...] = ()
    max_length: int = 4000
    # Cómo pedir el dato al usuario si falta ("el SLA: tiempo de respuesta p95…").
    ask: str | None = None
    # Extracción determinista desde la petición para valores cerrados ("app móvil" → "mobile").
    extract: Callable[[str], Any] | None = None


@dataclass
class ToolContext:
    actor: str = "anonymous"
    namespace: str = "default"
    session_id: str | None = None
    trace_id: str | None = None
    workflow_id: str | None = None
    # Resultados que la interfaz puede mostrar además de la respuesta (por ejemplo, una HU redactada por una skill).
    outputs: dict[str, Any] = field(default_factory=dict)


Handler = Callable[[dict[str, Any], ToolContext], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class Summary:
    """Conclusión verificable de una herramienta, calculada en código.

    text: respuesta correcta y completa a partir de los datos.
    anchors: datos clave que una redacción del modelo debe mencionar para considerarse fiel.
    exclusive: (dato, calificador) — el dato solo puede aparecer junto a su calificador (p. ej. un concesionario inactivo).
    verbatim: el texto ya es la mejor respuesta (listados, artefactos); no se pide redacción al modelo.
    """

    text: str
    anchors: tuple[str, ...] = ()
    exclusive: tuple[tuple[str, str], ...] = ()
    verbatim: bool = False


Summarizer = Callable[[dict[str, Any]], Summary]


@dataclass(frozen=True)
class Tool:
    name: str
    kind: Literal["tool", "skill"]
    title: str
    description: str
    handler: Handler
    params: tuple[Param, ...] = ()
    keywords: tuple[str, ...] = ()
    summarize: Summarizer | None = None

    def validate(self, raw: Any) -> dict[str, Any]:
        raw = raw if isinstance(raw, dict) else {}
        args: dict[str, Any] = {}
        problems: list[str] = []
        for param in self.params:
            value = raw.get(param.name)
            if _placeholder(value, param.name):
                value = None  # "no especificada", "tool", "<valor>"… el modelo rellenó la plantilla: equivale a no enviarlo
            if value is None or value == "":
                if param.required:
                    problems.append(f"falta '{param.name}' ({param.description})")
                continue
            try:
                if param.type == "integer":
                    value = int(value)
                elif param.type == "boolean":
                    value = value if isinstance(value, bool) else str(value).strip().lower() in {"true", "si", "sí", "1", "yes"}
                else:
                    value = str(value)[: param.max_length]
            except (TypeError, ValueError):
                problems.append(f"'{param.name}' debe ser {param.type}")
                continue
            if param.choices and str(value).lower() not in param.choices:
                problems.append(f"'{param.name}' debe ser uno de: {', '.join(param.choices)}")
                continue
            args[param.name] = value.lower() if param.choices else value
        # Argumentos desconocidos se ignoran: un modelo pequeño a veces agrega campos de más y eso no debe impedir la consulta.
        if problems:
            raise ToolArgumentError("; ".join(problems))
        return args

    def signature(self) -> str:
        params = ", ".join(f"{p.name}: {p.type}{'' if p.required else ' (opcional)'}" + (f" [{'|'.join(p.choices)}]" if p.choices else "") for p in self.params)
        return f"- {self.name}({params}) [{self.kind}]: {self.description}"


class ToolBox:
    def __init__(self, tools: list[Tool] | None = None):
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"herramienta_duplicada:{tool.name}")
        self._tools[tool.name] = tool

    def attach(self, name: str, summarize: Summarizer) -> None:
        if name in self._tools:
            self._tools[name] = replace(self._tools[name], summarize=summarize)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(str(name).strip())

    def resolve(self, name: str) -> Tool | None:
        """Nombre exacto o casi exacto ('desenar_prueba_performance'): corrige erratas sin salir del catálogo cerrado."""
        exact = self.get(name)
        if exact:
            return exact
        close = difflib.get_close_matches(str(name).strip().lower(), list(self._tools), n=1, cutoff=0.85)
        return self._tools[close[0]] if close else None

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def catalog(self) -> str:
        return "\n".join(tool.signature() for tool in self._tools.values())

    def closest(self, query: str, limit: int = 3) -> list[Tool]:
        """Herramientas más relacionadas con la petición, por coincidencia de términos (para sugerir alternativas)."""
        wanted = set(tokens(query))
        scored = []
        for tool in self._tools.values():
            # Título y palabras clave curadas: la descripción tiene verbos genéricos ("Recomienda…") que darían alternativas sin relación.
            vocabulary = set(tokens(" ".join((tool.title, *tool.keywords))))
            overlap = len(wanted & vocabulary)
            if overlap:
                scored.append((overlap, tool))
        return [tool for _, tool in sorted(scored, key=lambda item: item[0], reverse=True)[:limit]]


def compact(value: Any, limit: int = 1800) -> str:
    """Observación para el prompt: JSON compacto y acotado para no saturar el contexto del modelo."""
    text = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    return text if len(text) <= limit else text[: limit - 20] + "…(recortado)"
