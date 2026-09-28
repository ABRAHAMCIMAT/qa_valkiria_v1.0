# Quality gates

Un gate es una decisión verificable. Registra estado, actor, fase, `trace_id`, evidencia y motivo. Un fallo no se puede ocultar cambiando el estado final.

## G0 — Intake

Aprueba con actor, alcance, tipo y tamaño válidos. Bloquea entradas vacías, demasiado cortas, excesivas o sin resultado esperado. Evidencia: intención, palabras clave, request y trace.

## G1 — Grounding

Aprueba con políticas, contexto y restricciones cargados. Bloquea ambigüedad crítica, inyección o contexto insuficiente. Evidencia: fuentes, políticas y preguntas pendientes.

## G2 — Generación

Aprueba con JSON/Pydantic, límites, campos y políticas válidos. Bloquea JSON malformado, campos incompletos, contenido inseguro o artefacto sin trazabilidad.

## G3 — Evaluación

Aprueba con INVEST, cobertura positiva/negativa/frontera, riesgo y análisis estático satisfactorios. Bloquea cobertura insuficiente o hallazgos críticos.

## G4 — Aprobación

Aprueba una persona autorizada sobre la misma versión/hash. Bloquea ausencia de aprobador o hash diferente.

## G5 — Release

Aprueba preview o PR, sin commit directo, con análisis de secretos, dependencias y seguridad. Bloquea destino principal mediante commit directo, falta de aprobación o hallazgos críticos.

## G6 — Operación

Aprueba con auditoría, logs, métricas, duración, errores sanitizados y evidencia. Bloquea pérdida de trace, evidencia inexistente o secretos expuestos.

```text
G0 → G1 → G2 → G3 → G4 → G5 → G6
```

G4 y G5 solo pueden ser `skipped` si se genera o analiza un borrador sin publicar. Si se solicita publicar, G4 debe ser explícito y humano.