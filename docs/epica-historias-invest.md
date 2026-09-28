# EPIC-001 — Plataforma Valkiria de QA multiagente, E2E sintética y LLMOps

> **Versión documental:** 2.0 — rama `valkiria_nissan`.

## Objetivo

Como organización de QA, queremos transformar solicitudes de producto en historias INVEST, pruebas, riesgos, scripts, validaciones contra bases sintéticas y propuestas de integración mediante agentes especializados, para obtener trazabilidad y evidencia sin exponer sistemas productivos.

## Flujo de la épica

```text
Intake → Grounding → Generation → Evaluation
                         ↓              ↓
                   Database        Automation
                         ↓              ↓
              Approval → Release → Operations
```

## Historias

### HU-002 — Capturar y estructurar requerimientos

Como Product Owner o QA Engineer, quiero registrar un requerimiento en lenguaje natural para obtener una historia versionada con reglas y criterios de aceptación.

**INVEST:** independiente mediante el puerto LLM; negociable en plantilla; valiosa por crear el artefacto base; estimable en 5 puntos; pequeña como unidad de captura; testeable por contrato y validación Pydantic.

**Criterios:** 10 a 20,000 caracteres; salida en español; al menos un criterio; estado `draft`; actor, modelo, prompt y `trace_id` auditables.

### HU-003 — Aplicar grounding y políticas

Como QA Engineer, quiero normalizar contexto, políticas y ambigüedades antes de generar para evitar salidas fuera de alcance.

**INVEST:** independiente del proveedor; negociable en fuentes; valiosa por seguridad y precisión; estimable en 5 puntos; pequeña por solicitud; testeable con ambigüedad e inyección.

**Criterios:** Grounding precede a Generation; ambigüedad crítica bloquea; secretos no se registran; fuentes identificadas.

### HU-004 — Evaluar INVEST y generar pruebas

Como QA Engineer, quiero evaluar Independent, Negotiable, Valuable, Estimable, Small y Testable, y generar casos positivos, negativos y de frontera.

**Estimación:** 8 puntos. **Criterios:** seis atributos evaluados; máximo 30 casos; cada caso vinculado con un criterio; cobertura registrada en G3.

### HU-005 — Evaluar y priorizar riesgos

Como QA Lead, quiero calcular nivel, justificación, mitigación y orden de ejecución considerando complejidad, dependencias, criticidad e historial de defectos.

**Estimación:** 5 puntos. **Criterios:** rangos válidos; riesgo bajo/medio/alto; riesgo alto prioriza y mitiga sin impedir el análisis.

### HU-006 — Aprobar versiones exactas

Como responsable de Producto, QA o DevOps, quiero aprobar un hash exacto para impedir liberar una salida diferente de la revisada.

**Estimación:** 5 puntos. **Criterios:** actor y timestamp; hash coincidente; rechazo con motivo; sin aprobación solo preview.

### HU-007 — Integrar mediante adaptadores y PR

Como QA Engineer, quiero preparar Work Items, pipelines o Pull Requests con idempotencia y sin commit directo.

**Estimación:** 8 puntos; depende de HU-006. **Criterios:** preview; aprobación; error remoto conserva artefacto local; PR-only.

### HU-008 — Planificar rendimiento

Como QA Engineer, quiero definir herramienta, usuarios, duración y SLA para pruebas aisladas.

**Estimación:** 5 puntos. **Criterios:** JMeter, k6 o Locust; parámetros positivos; límites de recursos; aprobación para recursos críticos.

### HU-009 — Operar y auditar LLMOps

Como responsable de plataforma, quiero consultar logs JSON, métricas, gates y auditoría con `trace_id` sin exponer información sensible.

**Estimación:** 8 puntos. **Criterios:** redacción; errores uniformes; duración; gates G0-G6; métricas de calidad y operación.

### HU-010 — Automatizar por lotes

Como QA Engineer, quiero generar y ejecutar scripts por lotes trazables para reducir trabajo repetitivo.

**Estimación:** 13 puntos. **Criterios:** máximo 15 casos; Selenium, Playwright, RestAssured o Postman-Newman; datos externalizados; `case_id`; evidencia; PR-only; preview por defecto.

### HU-011 — Ejecutar scripts de base de datos de forma segura

Como QA Engineer, quiero validar datos y transacciones mediante perfiles sintéticos controlados.

**Estimación:** 13 puntos. **Criterios:** motores soportados por contrato; análisis estático; transacción, `WHERE`, límite y rollback; desarrollo/QA/staging; producción bloqueada; evidencia PDF/Word; sin DSN en requests.

## Quality gates

| Gate | Criterio |
|---|---|
| G0 | Petición y alcance válidos |
| G1 | Grounding y políticas cargados |
| G2 | Artefacto validado por esquema |
| G3 | INVEST, cobertura, riesgo y seguridad |
| G4 | Aprobación humana exacta |
| G5 | Preview/PR sin commit directo |
| G6 | Auditoría, métricas y evidencia |

## Completitud

La épica no se declara productiva hasta contar con pruebas de contrato externas, persistencia durable, autenticación, gestor de secretos, runner aislado y adaptadores aprobados. La E2E sintética sí puede ejecutarse como validación segura de integración.