"""Decide si una petición sigue el flujo programado (redactar o ajustar una HU) o la resuelve el asistente con herramientas."""

from __future__ import annotations

import re
from typing import Literal

from valkiria.assistant.capabilities import policy_block
from valkiria.memory.text import fold

_QUESTION_START = re.compile(
    r"^\s*(que|como|cual|cuales|cuando|donde|por que|porque|quien|quienes|cuanto|cuantos|cuantas|cuanta|puedes|podrias|sabes|tienes|hay|existe|existen|"
    r"explica|explicame|dime|muestrame|muestra|lista|enlista|analiza|revisa|valida|recomienda|recomiendame|sugiere|sugiereme|compara|ayudame|necesito saber|quiero saber)\b"
)
# Trabajo sobre la historia de usuario: lo resuelve el flujo conversacional de HU (crear o ajustar).
_STORY_WORK = re.compile(
    r"\b(historia|hu|user story|criterio|criterios|regla de negocio|reglas de negocio|requerimiento|redacta|redactar|ajusta|ajustar|agrega|agregar|anade|quita|quitar|"
    r"elimina el criterio|cambia|cambiar|modifica la|precisa|como usuario|como asesor|como cliente|como gerente|quiero que|necesito que|permitir|permita|funcionalidad)\b"
)

# Órdenes que corresponden a una herramienta o skill y no a redactar una HU ("Genera el pipeline de Azure").
_TOOL_ORDER = re.compile(
    r"^\s*(genera|disena|crea|arma|prepara|calcula|consulta|analiza|revisa|valida|busca|recuerda|sugiere|selecciona)\b.*"
    r"\b(pipeline|performance|rendimiento|carga|estres|jmeter|locust|sql|script|inventario|concesionarios|citas|herramienta|memoria|flujo|capacidades)\b"
)


_EDIT_VERB = re.compile(r"\b(agrega|anade|quita|cambia|ajusta|redacta|modifica|precisa|elimina)")


def is_question(text: str) -> bool:
    return "?" in text or bool(_QUESTION_START.search(fold(text)))


def route_message(text: str, *, follow_up: bool = False) -> Literal["story", "assistant"]:
    """'story' para crear o ajustar una HU; 'assistant' para preguntas, consultas o peticiones fuera del flujo.

    follow_up: la sesión ya trae una intención de trabajo ("y para el registro" continúa esa tarea).
    """
    folded = fold(text).strip()
    if policy_block(text):
        return "assistant"
    if "?" in text:
        # "¿Puedes agregar un criterio de borde?" es un ajuste a la HU aunque tenga forma de pregunta.
        return "story" if _STORY_WORK.search(folded) and _EDIT_VERB.search(folded) else "assistant"
    if _TOOL_ORDER.search(folded):
        return "assistant"
    if _STORY_WORK.search(folded):
        return "story"
    if _QUESTION_START.search(folded):
        return "assistant"
    if follow_up:
        return "story"
    words = folded.split()
    # Un requerimiento suele empezar en infinitivo ("Consultar vehículos por concesionario") o describir el caso con detalle.
    if words and (re.fullmatch(r"[a-z]{3,}(ar|er|ir)", words[0]) or len(words) >= 12):
        return "story"
    return "assistant"
