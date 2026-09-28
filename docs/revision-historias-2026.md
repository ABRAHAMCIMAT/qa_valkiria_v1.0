# Revisión del Plan de Mejora Valkiria 2026 (EPIC-001, HU-002 a HU-009)

> **Fuente revisada:** `Plan_Mejora_Valkiria_2026.pdf` (EPIC-001 y 9 historias: HU-002, HU-003A, HU-003B y HU-004 a HU-009).
> **Contrastado con:** el código del repositorio `qa_valkiria_v1.0` (commit `79f64ae`, paquete 0.5.0).
> **Historias mejoradas:** [Épica e historias INVEST v3.0](epica-historias-invest.md).
> **Fecha:** 2026-09-28.
> **Decisión posterior (2026-09-28):** Azure es la única plataforma y el modelo es Llama 3.2 Instruct. El conflicto T1 queda resuelto y las historias mejoradas ya no mencionan AWS.

## Resumen

Las historias están bien encaminadas: tienen el formato "Como / quiero / para", reglas de negocio explícitas, criterios Dado/Cuando/Entonces y un principio sólido de validación humana. Aun así, **ninguna está lista para desarrollo tal como está escrita**. Los problemas se concentran en cinco puntos:

1. **Conflictos entre reglas.** La épica exigía desplegar todo en AWS, pero HU-006 y HU-007 dependen de Azure DevOps y Azure Key Vault (**resuelto**: se adoptó Azure como única plataforma). HU-004 exige una HU aprobada, y HU-009 permite generar desde matrices en borrador.
2. **Términos sin definir.** Las fases A, C y D; "requerimiento amplio"; "herramienta de gestión de pruebas"; los SLA; el canal de notificación; "coincide exactamente".
3. **Historias demasiado grandes.** HU-004, HU-005 y HU-008 mezclan generación, integración externa y ejecución. HU-008 incluye aprovisionar infraestructura de ejecución.
4. **Criterios difíciles de probar.** Faltan umbrales numéricos, casos negativos y el comportamiento ante errores del LLM o de sistemas externos.
5. **Numeración distinta a la del repositorio.** El código y la documentación actual usan otra numeración (ver [Mapeo de numeración](#mapeo-de-numeración)).

Del lado de la implementación: **HU-002, HU-003A, HU-003B, HU-004, HU-005 y HU-009 están parcialmente implementadas**. HU-006, HU-007 y HU-008 tienen funciones de apoyo en el código que **no están conectadas a la API**, así que para un usuario no existen.

## Hallazgos transversales

| # | Hallazgo | Impacto | Recomendación |
|---|---|---|---|
| T1 | **Plataforma de nube contradictoria.** La regla 1 de la épica exigía AWS, mientras HU-006 y HU-007 integran Azure DevOps y Azure Key Vault. | Las historias no se podían cumplir a la vez. | **Resuelto (2026-09-28):** Azure es la única plataforma (AKS, ACR, Key Vault, PostgreSQL Flexible Server, Azure DevOps, Azure Load Testing) y el modelo es Llama 3.2 Instruct autoalojado en AKS. |
| T2 | **Fases A, C y D sin definir.** Se usan en HU-002, HU-004 y HU-005, pero no hay glosario. | Cada lector interpreta el flujo a su manera. | Añadir a la épica un glosario de fases: A (análisis/refinamiento), B (creación), C (planificación y riesgo), D (diseño de pruebas), E (automatización). |
| T3 | **La numeración empieza en HU-002 y usa sufijos A/B.** No existe HU-001 y el criterio de cierre dice "HU-002 a HU-009", lo que deja ambiguo si HU-003A/B cuentan por separado. | Confusión en seguimiento y reportes. | Mantener la numeración del plan como fuente de verdad y declarar en la épica la lista cerrada: HU-002, HU-003A, HU-003B, HU-004 a HU-009. |
| T4 | **La trazabilidad y la validación humana son reglas de la épica sin historia propia.** No hay criterios que las verifiquen ni quién las construye. | Riesgo de que cada HU lo resuelva distinto, o de que nadie lo haga. | Convertirlas en **requisitos transversales (RT)** con criterios propios y exigirlas en la Definición de Terminado de cada HU. |
| T5 | **Faltan requisitos no funcionales.** No hay tiempos de respuesta, disponibilidad, roles y permisos, retención de datos, gobierno del modelo LLM ni manejo de datos sensibles en las HU. | Imposible dimensionar la infraestructura ni aceptar la entrega. | Añadir los RT-03 a RT-06 (ver documento de historias). |
| T6 | **Sin prioridad, estimación ni dependencias.** | No se puede planificar la adopción progresiva que pide la regla 4 de la épica. | Añadir prioridad (MoSCoW), estimación en puntos y dependencias explícitas a cada HU. |
| T7 | **Criterios sin comportamiento ante fallos.** Ninguna HU dice qué pasa si el LLM no responde, devuelve JSON inválido o falla la integración externa. | Comportamiento no especificado en los casos más frecuentes en producción. | Añadir en cada HU un criterio negativo de "servicio no disponible" que conserve el trabajo del usuario. |
| T8 | **La épica no tiene un resultado medible.** "Reducir el esfuerzo manual y acelerar el time to market" no tiene métrica. | No se puede saber si la épica aportó valor. | Definir KPIs de línea base y objetivo: horas de refinamiento por HU, tiempo de requerimiento a matriz aprobada, porcentaje de sugerencias aceptadas y tasa de defectos escapados. |

## Mapeo de numeración

El repositorio usa hoy una numeración anterior. Recomendación: adoptar la del plan y renombrar la documentación y las pruebas del repo en un cambio posterior.

| Plan 2026 | Capacidad | Repositorio actual |
|---|---|---|
| HU-002 | Evaluación INVEST | HU-004 (junto con la matriz) |
| HU-003A | HU desde recomendaciones INVEST | No existe como HU |
| HU-003B | HU desde requerimiento libre | HU-002 |
| HU-004 | Matriz de pruebas | HU-004 (junto con INVEST) |
| HU-005 | Riesgos | HU-005 |
| HU-006 | Work Items en Azure DevOps | HU-007 (junto con pipelines y PR) |
| HU-007 | YAML de pipelines | HU-007 |
| HU-008 | Performance | HU-008 |
| HU-009 | Scripts de automatización | **HU-010** |
| — | Ejecución segura de scripts de BD | **HU-011** (no está en el plan) |
| — | Grounding, aprobación exacta, operación/LLMOps | HU-003, HU-006 y HU-009 (pasan a requisitos transversales) |

**Decisión pendiente para el PO:** HU-011 (base de datos sintética) está implementada y probada, pero no figura en el plan. Se recomienda incorporarla al plan como HU-011 conservando su número; la historia mejorada está incluida en el documento de historias.

## Revisión por historia

La evaluación INVEST corresponde a la **historia original del PDF**: ✅ cumple, 🟡 parcial, ❌ no cumple. El estado de implementación corresponde al **código actual**.

### EPIC-001 — Agente de IA especializado en QA

| I | N | V | E | S | T |
|---|---|---|---|---|---|
| — | ✅ | ✅ | ❌ | — | ❌ |

- **Problemas:**
  - El criterio de cierre ("HU implementadas y desplegadas") no mide valor.
  - La regla 1 choca con HU-006 y HU-007 (T1).
  - Las reglas 2 y 3 no tienen historia que las implemente (T4).
- **Mejora:** KPIs de éxito, glosario de fases, lista cerrada de HU, requisitos transversales y reformulación de la regla 1.

### HU-002 — Evaluación INVEST

| I | N | V | E | S | T |
|---|---|---|---|---|---|
| 🟡 | ✅ | ✅ | 🟡 | 🟡 | 🟡 |

- **Problemas:**
  - El tercer criterio (aprobar sugerencias → nueva versión) es exactamente HU-003A, así que las dos historias se solapan.
  - "Justificación" y "sugerencia accionable" no tienen un criterio verificable.
  - No define qué pasa si la HU está incompleta (sin criterios de aceptación), ni el tratamiento de un resultado "parcial".
- **Mejora:**
  - HU-002 se queda con la evaluación, la aprobación o rechazo de sugerencias y el historial; la redacción de la nueva versión pasa a HU-003A.
  - Se define una sugerencia accionable: una redacción alternativa concreta o un criterio faltante, no un consejo genérico.
  - Se agrega una validación previa de campos mínimos.
- **Estado en el código:** 🟡 Parcial.
  - `POST /v1/stories/{id}/invest` evalúa los 6 criterios con `cumple`/`parcial`/`no_cumple`, justificación y sugerencia.
  - La evaluación **no se guarda**: no hay historial ni flujo de aprobación de sugerencias.
  - La historia guarda solo su última versión.

### HU-003A — HU desde recomendaciones INVEST

| I | N | V | E | S | T |
|---|---|---|---|---|---|
| 🟡 | ✅ | ✅ | ✅ | ✅ | 🟡 |

- **Problemas:**
  - El título no tiene el "para <beneficio>".
  - "Los 4 componentes" y "publicar" no están definidos (¿publicar es HU-006?).
  - Falta el caso negativo de no tener sugerencias aprobadas.
  - Falta mostrar qué cambió respecto a la versión anterior.
- **Mejora:** beneficio explícito, diferencias entre versiones visibles, estados del borrador (borrador IA → aprobada / descartada) y "publicar" definido como aprobar la versión, con Azure DevOps como paso aparte (HU-006).
- **Estado en el código:** 🟡 Parcial. `POST /v1/chat` con intención "ajustar" genera la versión completa e incrementa `version`, pero **no está vinculado a una evaluación INVEST** ni a sugerencias aprobadas, y no existen los estados aprobar/descartar.

### HU-003B — HU desde requerimiento libre

| I | N | V | E | S | T |
|---|---|---|---|---|---|
| ✅ | ✅ | ✅ | 🟡 | 🟡 | ❌ |

- **Problemas:**
  - "Amplio" y "no hay certeza" son subjetivos, así que no se pueden probar.
  - No hay límite de HU generadas por requerimiento.
  - El título no tiene beneficio.
- **Mejora:**
  - Se define "amplio" de forma objetiva: más de un actor principal, más de un flujo independiente o más de 6 criterios estimados.
  - Máximo 5 HU propuestas por requerimiento.
  - Cada supuesto se documenta en un campo propio que el PO debe confirmar.
- **Estado en el código:** 🟡 Parcial. `POST /v1/stories` y `POST /v1/chat` crean una HU en borrador marcada como generada por IA y registran supuestos (máximo 3). **No divide** los requerimientos amplios en varias HU.

### HU-004 — Matriz de pruebas

| I | N | V | E | S | T |
|---|---|---|---|---|---|
| 🟡 | ✅ | ✅ | 🟡 | ❌ | 🟡 |

- **Problemas:**
  - Exige una "HU aprobada", mientras que HU-009 permite trabajar con matrices en borrador.
  - La "herramienta de gestión de pruebas" no está identificada, así que no se puede estimar.
  - La regla 6 (HU → ejecución → defecto) depende de HU-009 y de un gestor de defectos: es otra historia.
  - Con un mínimo de 3 casos por criterio y un máximo de 30, el límite real es 10 criterios por HU, y eso no está escrito.
- **Mejora:**
  - Se separa **HU-004** (generar, editar, aprobar y exportar a Excel/CSV) de **HU-004B** (sincronizar con la herramienta de gestión y trazabilidad hasta el defecto).
  - Se explicita el límite de 10 criterios.
  - Se acepta generar desde una HU en borrador, con una advertencia.
- **Estado en el código:** 🟡 Parcial, con una **contradicción**.
  - `POST /v1/stories/{id}/test-matrix` genera casos con todos los campos requeridos, y `POST /v1/test-cases/export` exporta a Excel.
  - El prompt del LLM pide **"entre 1 y 3 por criterio y máximo 12 en total"**, lo que viola el mínimo de un caso positivo, uno negativo y uno de borde por criterio, y el tope de 30.
  - `build_test_matrix` sí cumple las reglas, pero **no está conectada**.
  - Faltan CSV, edición, aprobación y trazabilidad a ejecución.

### HU-005 — Riesgos

| I | N | V | E | S | T |
|---|---|---|---|---|---|
| ✅ | ✅ | ✅ | 🟡 | 🟡 | 🟡 |

- **Problemas:**
  - No define la escala de complejidad, dependencias y criticidad, ni los umbrales de nivel.
  - El canal de notificación a PO y DevOps no está definido.
  - "Masivamente por sprint" requiere conocer el sprint, lo que implica depender de Azure DevOps.
  - Reúne cuatro capacidades distintas: análisis, lote, notificación y ajuste manual.
- **Mejora:**
  - Escala de 1 a 5 por dimensión, con umbrales explícitos.
  - La notificación se hace por un canal configurable (correo o Teams) y se reintenta si falla, sin bloquear el análisis.
  - El lote por sprint se basa en una lista de HU seleccionadas; la importación del sprint queda fuera del alcance.
  - Cada ajuste manual queda auditado.
- **Estado en el código:** 🟡 Parcial.
  - `POST /v1/stories/{id}/risk` devuelve nivel, justificación y mitigación, y marca si se consideró el histórico de defectos (siempre "no considerado", porque la API no recibe histórico).
  - Faltan escala explícita, lote, orden de ejecución, notificación y ajuste con justificación.
  - La función determinista con escala 1–5 existe, pero **no está conectada**.

### HU-006 — Work Items en Azure DevOps

| I | N | V | E | S | T |
|---|---|---|---|---|---|
| 🟡 | ✅ | ✅ | 🟡 | ✅ | 🟡 |

- **Problemas:**
  - El título dice "generación" cuando es publicación y sincronización.
  - "Coincide exactamente" no dice qué campos ni en qué formato.
  - No contempla que alguien edite el Work Item directamente en Azure DevOps, lo que genera conflictos.
  - No indica dónde se guarda el token.
- **Mejora:** campos obligatorios definidos (título, descripción, criterios y reglas en HTML, etiquetas); ante un conflicto no se sobrescribe, se muestran las diferencias; token en Azure Key Vault; reintentos con límite; clave de idempotencia persistida.
- **Estado en el código:** ❌ No disponible para el usuario. `build_azure_work_item` (vista previa, crear o actualizar, clave de idempotencia) y un adaptador de referencia existen, pero **sin endpoint ni llamada real** a Azure DevOps, y el mapeo solo cubre título y descripción.

### HU-007 — YAML de pipelines

| I | N | V | E | S | T |
|---|---|---|---|---|---|
| ✅ | ✅ | ✅ | 🟡 | ✅ | 🟡 |

- **Problemas:**
  - No dice cómo se valida la sintaxis.
  - El placeholder podría reportar pruebas en verde sin haber ejecutado nada, lo que daría falsa confianza.
  - No indica cómo se entrega al repositorio (HU-009 usa pull request).
- **Mejora:**
  - La validación se hace con el esquema de Azure Pipelines y, si hay conexión, con una ejecución de vista previa.
  - El placeholder **marca la etapa como omitida con advertencia visible**, nunca como aprobada.
  - La entrega es por pull request, igual que HU-009.
  - Los secretos van por Variable Group vinculado a Key Vault o service connection con Workload Identity federation.
- **Estado en el código:** ❌ No disponible para el usuario. `generate_pipeline_yaml` genera build, test y publicación con placeholder y Variable Group, pero **sin endpoint ni validación de sintaxis**; además, el placeholder usa `echo`, que siempre termina en verde.

### HU-008 — Performance

| I | N | V | E | S | T |
|---|---|---|---|---|---|
| ✅ | ✅ | ✅ | ❌ | ❌ | 🟡 |

- **Problemas:**
  - Es la historia más grande del plan: generación de escenarios y scripts, aprovisionamiento aislado, ejecución y reporte contra SLA.
  - No dice dónde se definen los SLA.
  - No fija límites de costo, duración ni usuarios.
  - No restringe los entornos objetivo: una prueba de carga contra producción sería un incidente.
- **Mejora:**
  - Se divide en **HU-008A** (generar escenario y script, sin ejecutar) y **HU-008B** (ejecutar en Azure Load Testing con límites y reportar contra SLA). Como Azure Load Testing ejecuta JMeter y Locust, k6 sale del alcance.
  - Solo contra entornos autorizados que no sean de producción.
  - Límites por defecto: 30 minutos, 500 usuarios virtuales y un presupuesto por ejecución, con destrucción automática de recursos.
- **Estado en el código:** ❌ No implementada. `performance_plan` solo valida la herramienta y los parámetros; no genera scripts, no ejecuta y no reporta. El adaptador de nube devuelve `planned`.

### HU-009 — Scripts de automatización

| I | N | V | E | S | T |
|---|---|---|---|---|---|
| ✅ | ✅ | ✅ | 🟡 | 🟡 | 🟡 |

- **Problemas:**
  - El tercer criterio (ejecución en ambiente de prueba) no aparece en la descripción ni define el ambiente.
  - Generar desde una matriz en borrador no dice qué pasa si la matriz cambia después.
  - Faltan controles de calidad del código generado (lint, secretos embebidos).
- **Mejora:**
  - La ejecución se hace en el ambiente de prueba configurado por proyecto.
  - Los scripts generados desde un borrador quedan marcados y se avisa si la matriz cambia.
  - Lint y detección de secretos se ejecutan antes del pull request.
  - Se define el contenido del pull request: casos vinculados y evidencia.
- **Estado en el código:** 🟡 El más avanzado.
  - `POST /v1/automation/batches/generate` limita a 15 casos, valida framework y plataforma, genera scripts vinculados a cada `case_id` y marca "solo pull request".
  - La ejecución real con Playwright produce resultados de aprobado o fallido por caso y un reporte PDF.
  - Faltan la creación real del pull request y los controles de lint y secretos. Page Object Model y datos externalizados aparecen como indicadores, pero no se verifican.

## Recomendaciones priorizadas

| Prioridad | Acción | Responsable sugerido |
|---|---|---|
| 1 | ~~Resolver T1 (plataforma de nube).~~ Resuelto: Azure como única plataforma. | PO + Arquitectura |
| 2 | Corregir en el código el prompt de la matriz (máximo 12 y 1–3 por criterio), porque contradice HU-004 en la implementación ya existente. | Desarrollo |
| 3 | Aprobar el glosario de fases, los requisitos transversales RT-01 a RT-06 y la Definición de Terminado. | PO + QA Lead |
| 4 | Implementar la persistencia de evaluaciones y versiones (base de HU-002, HU-003A y HU-004): hoy todo vive en memoria. | Desarrollo |
| 5 | Dividir HU-004 y HU-008 como se propone y reestimar. | Equipo en refinamiento |
| 6 | Conectar a la API las funciones que ya existen (`build_test_matrix`, riesgo determinista, `generate_pipeline_yaml`, `build_azure_work_item`) o eliminarlas para no aparentar funcionalidad. | Desarrollo |
| 7 | Incorporar HU-011 (BD sintética) al plan y alinear la numeración del repo. | PO |

## Estado de implementación (resumen)

| HU | Estado | Brecha principal |
|---|---|---|
| HU-002 | 🟡 Parcial | Persistencia, historial y aprobación de sugerencias |
| HU-003A | 🟡 Parcial | Vínculo con la evaluación INVEST y estados del borrador |
| HU-003B | 🟡 Parcial | División de requerimientos amplios |
| HU-004 | 🟡 Parcial | El prompt viola el mínimo por criterio; faltan edición, aprobación, CSV y trazabilidad |
| HU-005 | 🟡 Parcial | Escala, lote, notificación y ajuste auditado |
| HU-006 | ❌ No disponible | Endpoint e integración real con Azure DevOps |
| HU-007 | ❌ No disponible | Endpoint, validación de sintaxis y placeholder honesto |
| HU-008 | ❌ No implementada | Generación de scripts, ejecución en Azure Load Testing y reporte contra SLA |
| HU-009 | 🟡 Avanzada | Pull request real y controles de calidad del código |
| HU-011 | ✅ Implementada | Fuera del plan; persistencia y motores adicionales |

Además, en toda la plataforma faltan **persistencia durable** (el estado vive en memoria), **autenticación y roles** (el actor llega en una cabecera sin verificar) y un **despliegue real en Azure**: la infraestructura (Bicep), el overlay de AKS y el pipeline de Azure DevOps ya están en el repositorio y validados, pero aún no se ejecutaron contra una suscripción. Son condiciones para cerrar la épica según su propia regla 1.
