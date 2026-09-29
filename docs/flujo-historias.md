# Flujo de agentes por historia

El flujo por historia (`src/valkiria/workflow/`) coordina las capacidades del plan (HU-002 a HU-011) sobre **una misma historia**, respetando sus dependencias. Se detiene en las aprobaciones humanas, recuerda lo hecho entre peticiones y permite continuar con nuevas tareas sin rehacer trabajo.

Complementa al orquestador multiagente de [`/v1/agent/execute`](multiagente-orquestacion.md), que resuelve una petición aislada. El flujo por historia mantiene estado, versiones y aprobaciones a lo largo del ciclo de vida.

## Principios

| Principio | Cómo se cumple |
|---|---|
| **Dependencias explícitas** | Grafo de capacidades validado al iniciar: sin ciclos y sin dependencias desconocidas (`graph.py`). |
| **Nada se ejecuta sin sus requisitos** | El planificador recorre las dependencias de cada objetivo antes de actuar. |
| **Coherencia entre versiones** | Cada artefacto guarda con qué versión de sus dependencias se generó (`based_on`). Si la HU pasa a v2, la matriz basada en v1 se regenera. |
| **Control humano (RT-02)** | Las aprobaciones se registran sobre la versión y el hash exactos; aprobar una versión superada devuelve `409`. |
| **No se rompe** | Punto de control tras cada paso, reintentos con espera exponencial, aislamiento de fallos, pausa ordenada en aprobaciones y datos faltantes, persistencia en PostgreSQL y control de concurrencia. |
| **Razonamiento explicable** | Cada decisión (reutilizar, regenerar, esperar, pedir datos, no disponible) queda justificada en español en `reasoning`. |
| **Calidad de la salida del LLM** | Generar, validar contra las reglas de la HU, pedir una corrección y, si no basta, completar de forma determinista. Todo queda declarado en `warnings`. |

Llama 3.2 3B genera contenido, pero **no decide el flujo**: la planificación es determinista sobre el grafo. Un modelo de ese tamaño no es confiable para planificar dependencias, y así el comportamiento es reproducible y auditable.

## Grafo de capacidades

```text
story (HU-003B) ──┬─> invest (HU-002) ──[aprobación + decisión por sugerencia]──> story_revision (HU-003A) ─> nueva versión de story
                  ├─> matrix (HU-004) ──> automation (HU-009) ──> pipeline (HU-007, dependencia opcional)
                  │        └──[aprobación]──> matrix_sync (HU-004B, no disponible)
                  ├─[aprobación]─> risk (HU-005)
                  ├─> performance_design (HU-008A) ──> performance_run (HU-008B, no disponible)
                  └─[aprobación]─> azure_work_item (HU-006, vista previa)
```

| Capacidad | HU | Requiere | Datos que pide | Aprobable |
|---|---|---|---|---|
| `story` | HU-003B | — | `requirement` | Sí |
| `invest` | HU-002 | story (borrador) | — | Sí, con decisión por sugerencia |
| `story_revision` | HU-003A | invest **aprobado** | — | — |
| `matrix` | HU-004 | story (borrador; advierte si no está aprobada) | — | Sí |
| `risk` | HU-005 | story **aprobada** | — | — |
| `automation` | HU-009 | matrix (borrador) | `repository` (opcionales: `framework`, `platform`, `base_branch`) | — |
| `pipeline` | HU-007 | automation (opcional) | — | — |
| `performance_design` | HU-008A | story | `performance_users`, `performance_duration_seconds`, `performance_sla_ms` (opcionales: `performance_tool` jmeter/locust, `performance_type`) | — |
| `azure_work_item` | HU-006 | story **aprobada** | `azure_project` | Sí |
| `matrix_sync` | HU-004B | matrix aprobada | — | No disponible |
| `performance_run` | HU-008B | performance_design | — | No disponible |

`GET /v1/workflows/capabilities` devuelve este grafo.

## Decisiones del planificador

| Acción | Significado |
|---|---|
| `reuse` | El artefacto existe y está al día con sus dependencias. |
| `run` | No existe; se genera. |
| `rerun` | Quedó desactualizado: cambió la versión de una dependencia o apareció una dependencia opcional. |
| `blocked` | Espera a otra capacidad o a una aprobación (`blocked_by: ["approval:story"]`). |
| `needs_input` | Faltan datos del usuario; `next_actions` incluye la pregunta concreta. |
| `skipped` | No aplica; por ejemplo, HU-003A sin sugerencias aprobadas. |
| `unavailable` | La capacidad aún no está implementada; se informa y no se generan artefactos que no desbloquean nada. |
| `failed` | Agotó sus reintentos; se puede reanudar. |

Una capacidad ya satisfecha **no fuerza a recalcular sus dependencias**, y la obsolescencia solo considera las dependencias declaradas. Por eso no hay ciclos de regeneración: reevaluar INVEST no regenera la historia.

Estados del flujo: `completed`, `waiting_approval`, `needs_input`, `failed` o `partially_completed` (alguna capacidad no disponible).

## Robustez

- **Puntos de control:** el estado se guarda después de cada paso. Con `VALKIRIA_WORKFLOW_DATABASE_URL` (SQLite o PostgreSQL) sobrevive a reinicios; en Azure usa la base `valkiria_workflows`, con la URL en Key Vault.
- **Reintentos:** hasta 3 intentos con espera de 1 s y luego 2 s ante timeouts, errores del proveedor o salidas inválidas del LLM. Las violaciones de política no se reintentan porque fallarían igual.
- **Aislamiento:** un paso fallido no detiene las ramas independientes. Por ejemplo, si falla la matriz, el riesgo se evalúa igual. Un error inesperado se registra como `internal_error`, sin detalles internos.
- **Concurrencia:** cada flujo tiene un bloqueo por proceso y el almacenamiento usa control optimista por `revision`; una escritura concurrente devuelve `409`.
- **Cota de ejecución:** cada llamada ejecuta a lo sumo `2 × capacidades` pasos.

## Calidad de las salidas del LLM

| Capacidad | Validación | Si falla |
|---|---|---|
| INVEST | 6 criterios con nombre, estado válido y justificación; sugerencia en cada criterio `parcial` o `no_cumple` | Una corrección con los hallazgos concretos; si persiste, falla con reintento |
| Matriz | Positivo, negativo y borde por criterio; máximo 30; cada caso vinculado a un criterio existente | Una corrección; después, completar con casos de plantilla, declarado en `warnings` |
| Matriz con más de 10 criterios | HU-004, regla 2 | Falla sin reintentar (`matrix_requires_split`): dividir la HU |
| Nueva versión (HU-003A) | Esquema completo de la HU | Falla con reintento |

Solo requieren decisión del PO las sugerencias de criterios `parcial` o `no_cumple`. Llama 3.2 suele sugerir mejoras también en criterios que ya cumplen; esas se conservan como información, pero no bloquean.

## API

| Método | Ruta | Uso |
|---|---|---|
| POST | `/v1/workflows` | Iniciar: `{"request": "...", "goals": [...], "params": {...}}`. Si no se indican objetivos, se detectan en la petición. |
| GET | `/v1/workflows/{id}` | Estado, plan, próximas acciones, artefactos, fallos y razonamiento |
| POST | `/v1/workflows/{id}/requests` | Continuar con nuevas tareas o datos (`params`) |
| POST | `/v1/workflows/{id}/approvals` | Aprobar o rechazar `{"artifact", "version", "content_hash", "decision", "suggestions"}` |
| PUT | `/v1/workflows/{id}/artifacts/{story\|matrix}` | Edición humana: crea una versión nueva y vuelve a pedir aprobación |
| POST | `/v1/workflows/{id}/resume` | Reintentar los pasos fallidos |
| GET | `/v1/workflows/capabilities` | Grafo de capacidades |

### Ejemplo

```bash
# 1. Una sola petición con varias tareas
curl -s -X POST localhost:8000/v1/workflows -H 'Content-Type: application/json' -H 'X-Actor: po' -d '{
  "request": "Redacta la historia para consultar vehículos Nissan por concesionario, evalúa la HU con INVEST, genera la matriz de pruebas, evalúa el riesgo y genera los scripts de automatización"}'
# → status "waiting_approval": HU, INVEST y matriz generadas; riesgo espera la aprobación de la HU; automatización pide "repository".

# 2. Aportar el dato pendiente
curl -s -X POST localhost:8000/v1/workflows/$ID/requests -H 'Content-Type: application/json' -d '{"params": {"repository": "org/valkiria-qa-automation"}}'

# 3. Aprobar la versión exacta de la HU (version y content_hash vienen en next_actions)
curl -s -X POST localhost:8000/v1/workflows/$ID/approvals -H 'Content-Type: application/json' -H 'X-Actor: po' \
  -d '{"artifact": "story", "version": 1, "content_hash": "<hash>", "decision": "approved"}'
# → status "completed"
```

Verificado con Llama 3.2 Instruct local (CPU):
- La primera llamada generó HU, INVEST y matriz en unos 90 s. La matriz se corrigió una vez y se completaron 5 casos con plantilla.
- Aportar el repositorio generó 18 scripts en 2 lotes.
- Aprobar la HU desbloqueó el riesgo en unos 6 s.

## Memoria y preguntas dentro del flujo

- Antes de generar la HU, INVEST, la matriz o el riesgo se recuperan recuerdos validados del equipo, y cada artefacto registra `memory_used`.
- Aprobar la HU, decidir sobre las sugerencias INVEST, editar, rechazar con comentario o fallar de forma definitiva genera memoria de largo plazo para las siguientes historias. El `namespace` se indica en `params.namespace`. Ver [Memoria](memoria.md).
- Una petición sin tareas reconocibles, como "¿En qué va este flujo?", se responde con el asistente usando el contexto del flujo y queda en `answers`. `POST /v1/workflows` con una pregunta devuelve la respuesta sin crear un flujo (`id: null`). Ver [Asistente](asistente.md).

## Límites actuales

- `matrix_sync` (HU-004B) y `performance_run` (HU-008B) aparecen como no disponibles.
- `azure_work_item` genera la vista previa aprobable, pero todavía no llama a Azure DevOps.
- La notificación de riesgo alto a PO y DevOps queda como advertencia; no hay canal configurado.
- La detección de objetivos por palabras clave cubre el vocabulario del plan. Para peticiones ambiguas conviene indicar `goals` de forma explícita.
