"""Qué puede y qué no puede hacer Valkiria, construido desde el código (no desde lo que diga el modelo).

La respuesta honesta de "no puedo" siempre sale de aquí: así nunca se promete algo que no existe.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from valkiria.assistant.tools import Tool, ToolBox
from valkiria.memory.text import fold
from valkiria.workflow.graph import CAPABILITIES

LIMITS = (
    "No opero sobre producción ni con datos reales: solo sobre el entorno sintético de Nissan.",
    "No hago commits ni push directos: todo cambio de código se entrega como pull request.",
    "Todavía no publico en Azure DevOps: preparo la vista previa del Work Item para aprobación (HU-006).",
    "No sincronizo matrices con Azure Test Plans (HU-004B) ni ejecuto pruebas de carga (HU-008B).",
    "No tengo acceso a internet, correo, calendarios ni sistemas fuera de Valkiria.",
    "No revelo ni administro credenciales; los secretos viven en Azure Key Vault.",
)


@dataclass(frozen=True)
class PolicyBlock:
    capability: str
    reason: str
    alternative: str


# Peticiones que se rechazan sin consultar al modelo: son decisiones de política, no de razonamiento.
_POLICIES = (
    (re.compile(r"\b(produccion|productivo|prod)\b"), re.compile(r"\b(ejecut|corr|despleg|despliega|borr|elimin|modific|actualiz|conect|aplic|lanz|migr)"),
     PolicyBlock("operar sobre producción", "Valkiria bloquea producción por diseño (VALKIRIA_ALLOW_PRODUCTION=false).",
                 "Puedo analizar el script o el plan de forma estática y ejecutarlo contra la base sintética de Nissan.")),
    (re.compile(r"\b(commit|push|merge)\b"), re.compile(r"\b(directo|directamente|main|master|haz|hacer|sube|subir|empuja)\b"),
     PolicyBlock("hacer commits, push o merge directos", "La política es PR-only: ningún agente escribe directamente en ramas protegidas.",
                 "Puedo preparar los scripts y la vista previa del pull request para que un humano lo revise.")),
    (re.compile(r"\b(contrasena|password|credencial|credenciales|api ?key|token|secreto|connection string|cadena de conexion)\b"), re.compile(r"\b(dame|dime|muestra|muestrame|revela|cual es|cuales son|imprime|comparte|envia)\b"),
     PolicyBlock("revelar credenciales o secretos", "Los secretos viven en Azure Key Vault y ningún agente los expone.",
                 "Puedo explicarte qué variable de configuración usa cada integración y cómo se inyecta desde Key Vault.")),
    (re.compile(r"\b(correo|email|e-mail|mail|whatsapp|teams|slack)\b"), re.compile(r"\b(envia|enviar|manda|mandar|notifica|notificar|escribe)\b"),
     PolicyBlock("enviar correos o mensajes", "Valkiria no tiene canales de notificación configurados.",
                 "Puedo redactar el texto del mensaje para que tú lo envíes.")),
    (re.compile(r"\b(internet|google|web|pagina|sitio)\b"), re.compile(r"\b(busca|buscar|navega|consulta|abre|descarga)\b"),
     PolicyBlock("buscar en internet", "No tengo acceso a internet.", "Puedo responder con el conocimiento de QA del modelo, indicando que no está verificado.")),
)


def policy_block(text: str) -> PolicyBlock | None:
    folded = fold(text)
    for subject, action, block in _POLICIES:
        if subject.search(folded) and action.search(folded):
            return block
    return None


def capability_summary(toolbox: ToolBox) -> dict[str, list[str]]:
    flow = [f"{c.hu} {c.title}" for c in CAPABILITIES.values() if c.available]
    unavailable = [f"{c.hu} {c.title}: {c.unavailable_reason}" for c in CAPABILITIES.values() if not c.available]
    return {
        "herramientas": [f"{t.title}: {t.description}" for t in toolbox.all() if t.kind == "tool"],
        "skills": [f"{t.title}: {t.description}" for t in toolbox.all() if t.kind == "skill"],
        "flujo_por_historia": flow,
        "no_disponible": unavailable,
        "limites": list(LIMITS),
    }


def honest_unsupported(request: str, *, missing: str | None, reason: str | None, toolbox: ToolBox, closest: list[Tool] | None = None,
                       alternative: str | None = None) -> str:
    what = missing.strip().rstrip(".") if missing and missing.strip() else "resolver esta petición con mis herramientas"
    lines = [f"No tengo la capacidad de {what}."]
    if reason:
        lines.append(reason.strip())
    if alternative:
        lines.append(alternative)
    related = closest if closest is not None else toolbox.closest(request)
    if related:
        lines.append("\nLo más cercano que sí puedo hacer:")
        lines += [f"- {t.title}: {t.description}" for t in related]
    others = [t.title for t in toolbox.all() if t not in (related or [])]
    lines.append(("\nTambién puedo: " if related else "\nLo que sí puedo hacer: ") + "; ".join(others) + ".")
    lines.append("Y el flujo por historia completo: HU, INVEST, matriz de pruebas, riesgo, scripts, pipeline, diseño de performance y vista previa de Work Item.")
    return "\n".join(lines)
