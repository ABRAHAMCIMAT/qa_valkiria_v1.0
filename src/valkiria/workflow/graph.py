"""Grafo de capacidades del ciclo de vida de una historia (EPIC-001 v3.0).

Cada capacidad corresponde a una HU del plan y produce un artefacto. Las dependencias son explícitas:
el planificador nunca ejecuta una capacidad sin sus requisitos y detecta artefactos desactualizados
cuando cambia la versión de una dependencia.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Requirement:
    artifact: str
    # True: la dependencia debe estar aprobada por una persona (RT-02). False: basta un borrador.
    approved: bool = False


@dataclass(frozen=True)
class Capability:
    key: str
    hu: str
    title: str
    requires: tuple[Requirement, ...] = ()
    # Se usan si existen, pero no bloquean (por ejemplo, el pipeline referencia scripts si ya hay).
    optional: tuple[str, ...] = ()
    # El artefacto resultante necesita aprobación humana antes de que otros lo consuman como aprobado.
    approvable: bool = False
    # Parámetros que el usuario debe aportar; sin ellos el paso queda en "needs_input".
    inputs: tuple[str, ...] = ()
    available: bool = True
    unavailable_reason: str = ""
    keywords: tuple[str, ...] = field(default=(), compare=False)


CAPABILITIES: dict[str, Capability] = {
    capability.key: capability
    for capability in (
        Capability("story", "HU-003B", "Historia a partir de un requerimiento", approvable=True, inputs=("requirement",),
                   keywords=("historia", "requerimiento", "redacta", "crea la hu", "crear la hu")),
        Capability("invest", "HU-002", "Evaluación INVEST", requires=(Requirement("story"),), approvable=True,
                   keywords=("invest", "refina la", "refinar la", "refinamiento", "evalúa la hu", "evalua la hu", "evalúa la historia", "evalua la historia")),
        Capability("story_revision", "HU-003A", "Nueva versión desde sugerencias aprobadas", requires=(Requirement("invest", approved=True),),
                   keywords=("aplica las sugerencias", "aplicar sugerencias", "nueva versión", "nueva version", "actualiza la hu", "hu actualizada")),
        Capability("matrix", "HU-004", "Matriz de pruebas", requires=(Requirement("story"),), approvable=True,
                   keywords=("matriz", "casos de prueba", "casos manuales")),
        Capability("risk", "HU-005", "Análisis de riesgo", requires=(Requirement("story", approved=True),),
                   keywords=("riesgo", "riesgos", "priorizar", "prioriza")),
        Capability("automation", "HU-009", "Scripts de automatización", requires=(Requirement("matrix"),), inputs=("framework", "repository"), approvable=True,
                   keywords=("automatiza", "automatización", "automatizacion", "scripts", "playwright", "selenium", "restassured", "postman")),
        Capability("execution", "HU-010", "Ejecución de scripts en el entorno sintético", requires=(Requirement("automation", approved=True),),
                   keywords=("ejecuta los scripts", "ejecutar los scripts", "corre los scripts", "ejecuta las pruebas", "ejecutar las pruebas", "corre las pruebas",
                             "ejecuta la automatización", "ejecuta la automatizacion", "ejecución de scripts", "ejecucion de scripts")),
        Capability("pipeline", "HU-007", "YAML de pipeline de Azure DevOps", optional=("automation",), approvable=True,
                   keywords=("pipeline", "yaml")),
        Capability("performance_design", "HU-008A", "Diseño de prueba de performance", requires=(Requirement("story"),),
                   inputs=("performance_users", "performance_duration_seconds", "performance_sla_ms"), approvable=True,
                   keywords=("performance", "rendimiento", "carga", "estrés", "estres", "picos")),
        Capability("azure_work_item", "HU-006", "Work Item en Azure DevOps", requires=(Requirement("story", approved=True),), approvable=True,
                   inputs=("azure_project",), keywords=("azure devops", "work item", "publica la hu", "publicar la hu")),
        Capability("matrix_sync", "HU-004B", "Sincronización con gestión de pruebas", requires=(Requirement("matrix", approved=True),),
                   available=False, unavailable_reason="La herramienta de gestión de pruebas aún no está decidida (HU-004B).",
                   keywords=("sincroniza", "test plans", "xray")),
        Capability("performance_run", "HU-008B", "Ejecución en Azure Load Testing", requires=(Requirement("performance_design"),),
                   available=False, unavailable_reason="La ejecución en Azure Load Testing (HU-008B) todavía no está implementada.",
                   keywords=("azure load testing", "ejecuta la prueba de performance")),
    )
}

# Alias: en la HU-003A la "historia" nueva reemplaza la versión anterior del artefacto "story".
PRODUCES = {"story_revision": "story"}


class GraphError(ValueError):
    pass


def artifact_of(key: str) -> str:
    return PRODUCES.get(key, key)


def producer_of(artifact: str) -> str:
    return artifact


def validate_graph(capabilities: dict[str, Capability] = CAPABILITIES) -> list[str]:
    """Falla si hay dependencias desconocidas o ciclos; devuelve un orden topológico."""
    for capability in capabilities.values():
        for dependency in [r.artifact for r in capability.requires] + list(capability.optional):
            if dependency not in capabilities:
                raise GraphError(f"dependencia_desconocida:{capability.key}->{dependency}")
    order: list[str] = []
    state: dict[str, str] = {}

    def visit(key: str, path: tuple[str, ...]) -> None:
        if state.get(key) == "done":
            return
        if state.get(key) == "visiting":
            raise GraphError("ciclo:" + "->".join((*path, key)))
        state[key] = "visiting"
        capability = capabilities[key]
        for dependency in [r.artifact for r in capability.requires] + list(capability.optional):
            visit(dependency, (*path, key))
        state[key] = "done"
        order.append(key)

    for key in capabilities:
        visit(key, ())
    return order


TOPOLOGICAL_ORDER = validate_graph()


def closure(goals: list[str], capabilities: dict[str, Capability] = CAPABILITIES) -> list[str]:
    """Capacidades necesarias para cumplir los objetivos, en orden topológico (dependencias primero)."""
    needed: set[str] = set()

    def add(key: str) -> None:
        if key in needed:
            return
        needed.add(key)
        for requirement in capabilities[key].requires:
            add(requirement.artifact)

    for goal in goals:
        if goal not in capabilities:
            raise GraphError(f"objetivo_desconocido:{goal}")
        add(goal)
    return [key for key in TOPOLOGICAL_ORDER if key in needed]


def detect_goals(request: str) -> list[str]:
    """Objetivos explícitos en la petición, por palabras clave (determinista; el LLM no decide el flujo)."""
    text = request.lower()
    return [key for key in TOPOLOGICAL_ORDER if any(keyword in text for keyword in CAPABILITIES[key].keywords)]
