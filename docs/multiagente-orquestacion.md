# Orquestación multiagente y razonamiento verificable

## Objetivo

La plataforma recibe lenguaje natural, lo convierte en un plan, delega a agentes especializados y consolida una respuesta basada en artefactos y evidencia. No expone cadenas de razonamiento interno del modelo.

## Contexto controlado

`AgentContext` contiene `request_id`, `trace_id`, actor, petición, artefactos, decisiones y quality gates. `AgentResult` contiene agente, fase, estado, mensaje, trace, artefactos, decisiones, gate y reintentabilidad.

## Agentes

| Agente | Responsabilidad |
|---|---|
| Intake | Intención, alcance y lenguaje |
| Grounding | Políticas, fuentes y ambigüedad |
| Generation | Borrador o JSON LLM |
| Evaluation | INVEST, cobertura, riesgo y análisis estático |
| Assistant | Preguntas y peticiones fuera del flujo, con herramientas y skills; declara lo que no puede hacer ([detalle](asistente.md)) |
| Database | HU-011 contra perfil sintético |
| Automation | HU-010 y Playwright opcional |
| Approval | Versión/hash y decisión humana |
| Release | Preview/PR y protección contra commit directo |
| Operations | Auditoría, métricas y evidencia |

## Ejecución

1. Validar petición.
2. Crear `request_id` y `trace_id`.
3. Clasificar con Intake.
4. Aplicar Grounding.
5. Consultar `can_handle` y crear `AgentPlan`.
6. Ejecutar secuencialmente.
7. Validar trace en cada handoff.
8. Transferir artefactos y decisiones.
9. Reintentar una sola vez los fallos reintentables.
10. Ejecutar Operations incluso después de un bloqueo.

Una petición compleja puede recorrer:

```text
intake → grounding → generation → evaluation → database → automation → approval → release → operations
```

Approval devuelve `waiting_approval` sin publicar. Release prepara preview con `direct_commit=false`; la PR real requiere autorización explícita.

## Memoria y peticiones fuera del flujo

- Con `session_id`, el orquestador carga la memoria de la sesión y los recuerdos validados del equipo en `AgentContext.memory`. Intake hereda la intención de la sesión en un seguimiento; Grounding no bloquea seguimientos cortos; Generation usa la memoria en el prompt. Al terminar se guarda el turno. Ver [Memoria](memoria.md).
- El router selecciona `assistant` en lugar de `generation` y `evaluation` cuando la petición es una pregunta o no es trabajo del flujo. La respuesta queda en `artifacts.assistant`, con las herramientas usadas y los pasos. Ver [Asistente](asistente.md).

## Seguridad

El router solo selecciona agentes registrados. Los agentes no manejan credenciales. Las mutaciones externas deben ser secuenciales e idempotentes. La explicación pública contiene decisiones y evidencia, no razonamiento interno confidencial.

## Flujo por historia

Este orquestador resuelve una petición aislada. Para coordinar varias HU sobre una misma historia (dependencias, versiones, aprobaciones y reanudación entre peticiones) usa el [flujo de agentes por historia](flujo-historias.md).
