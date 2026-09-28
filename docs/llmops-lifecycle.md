# Ciclo de vida LLMOps

## Propósito

LLMOps controla la petición desde su entrada hasta la operación. Cada fase registra evidencia, actor, modelo, versión de prompt, resultado y `trace_id`. JSON válido no equivale a release aprobado.

## Fases y gates

| Gate | Fase | Evidencia | Criterio |
|---|---|---|---|
| G0 | Intake | `request_id`, `trace_id`, intención y alcance | Entrada válida y acotada |
| G1 | Grounding | Políticas, contexto, fuentes y ambigüedades | Sin ambigüedad crítica |
| G2 | Generación | Modelo, prompt, esquema y artefacto | JSON/Pydantic y políticas válidos |
| G3 | Evaluación | INVEST, cobertura, riesgo y seguridad | Umbrales cumplidos |
| G4 | Aprobación | Persona, timestamp y hash | Aprobación humana exacta |
| G5 | Release | Preview/PR, análisis y secretos | Sin commit directo ni hallazgos críticos |
| G6 | Operación | Logs, métricas, auditoría y evidencia | Trazabilidad completa |

## Flujo implementado

```text
Intake → Grounding → Generation → Evaluation
                         ↓              ↓
                   Database        Automation
                         ↓              ↓
              Approval → Release → Operations
```

Intake, Grounding, Generation y Evaluation son obligatorios para toda petición clara. Database se selecciona para SQL, vehículos, inventario o base sintética. Automation se selecciona para Playwright, E2E o automatización.

## Contrato de agente

Cada agente recibe `AgentContext` y devuelve `AgentResult`. No recibe credenciales. Su resultado debe incluir estado, artefactos, decisiones, estado del gate y `trace_id`. Un handoff con traza inconsistente se rechaza.

## Estados

- `completed`: fase transferible.
- `waiting_approval`: requiere decisión humana.
- `blocked`: política o gate impide continuar.
- `failed`: fallo técnico o funcional.
- `needs_clarification`: falta información.

Los fallos reintentables tienen como máximo un reintento. El segundo fallo detiene el flujo.

## Métricas

Duración por fase, timeouts, JSON inválido, gates fallidos, casos ejecutados, filas afectadas, rollbacks, Playwright, errores por adaptador, tokens y costo cuando el proveedor los informe.

## Release

G4 es humano para cambios críticos. G5 produce `preview` y mantiene `direct_commit=false`. Una PR real requiere adaptador autorizado y aprobación explícita.