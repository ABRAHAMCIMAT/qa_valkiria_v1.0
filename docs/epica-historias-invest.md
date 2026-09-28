# EPIC-001 — Agente de IA especializado en QA

> **Versión documental:** 3.0 (2026-09-28). Mejora del *Plan de Mejora Valkiria 2026* según la [revisión de historias](revision-historias-2026.md).
> Sustituye a la v2.0, que usaba otra numeración (ver [mapeo](revision-historias-2026.md#mapeo-de-numeración)).
> **Plataforma:** Microsoft Azure es la única plataforma de despliegue (decisión del 2026-09-28). **Modelo LLM:** Llama 3.2 Instruct.
> **Estimaciones y prioridades:** sugeridas; el equipo debe validarlas en refinamiento.

## Épica

**Título:** Como organización de Producto, QA y DevOps, queremos un agente de IA especializado en QA integrado a nuestro flujo de trabajo, para reducir el esfuerzo manual repetitivo del ciclo de vida de una historia de usuario y acelerar el time to market sin perder control humano ni trazabilidad.

**Descripción:** agrupa las capacidades del agente a lo largo del ciclo de vida de una HU (fases A a E), adoptables de forma progresiva e independiente y sin requerir conocimientos avanzados de IA.

**Reglas de negocio:**
1. Todos los componentes (API, agentes, modelo LLM, datos, ejecución de pruebas y CI/CD) se despliegan exclusivamente en Microsoft Azure: AKS, Azure Container Registry, Azure Key Vault, Azure Database for PostgreSQL y Azure DevOps, con cifrado en tránsito (TLS 1.2 o superior) y en reposo (claves administradas por Azure o en Key Vault). Los secretos viven en Azure Key Vault y los pods acceden con Workload Identity, sin credenciales en el clúster.
2. Ninguna funcionalidad reemplaza la validación humana final en decisiones críticas: publicar, aprobar, integrar código o ejecutar sobre recursos de Azure con costo (ver RT-02).
3. Toda acción relevante queda registrada con usuario, fecha, resultado, artefacto y versión (ver RT-01).
4. Cada capacidad se adopta de forma progresiva e independiente: una HU puede habilitarse por proyecto sin requerir las demás, salvo las dependencias declaradas.
5. El modelo es Llama 3.2 Instruct, autoalojado en AKS con Ollama y expuesto con una API compatible con OpenAI. Los datos de las HU no salen del tenant de Azure de la organización.

**Alcance cerrado:** HU-002, HU-003A, HU-003B, HU-004, HU-004B, HU-005, HU-006, HU-007, HU-008A, HU-008B, HU-009 y HU-011, más los requisitos transversales RT-01 a RT-06.

**Resultados esperados (KPIs):** se mide una línea base antes del piloto y se revisa por trimestre.

| KPI | Objetivo inicial |
|---|---|
| Tiempo de requerimiento a HU aprobada | −40 % frente a la línea base |
| Tiempo de HU aprobada a matriz aprobada | −50 % |
| Sugerencias INVEST aceptadas por el PO | ≥ 60 % |
| Casos generados aceptados sin edición mayor | ≥ 70 % |
| Defectos escapados a producción en HU con matriz del agente | Sin aumento frente a la línea base |

**Criterio de cierre:** la épica se completa cuando todas las HU del alcance cumplen sus criterios y la Definición de Terminado, están desplegadas en Azure y los KPIs tienen al menos un ciclo de medición.

### Glosario de fases

| Fase | Nombre | HU |
|---|---|---|
| A | Análisis y refinamiento | HU-002, HU-003A |
| B | Creación de la HU | HU-003B, HU-006 |
| C | Planificación y riesgo | HU-005, HU-008A |
| D | Diseño de pruebas | HU-004, HU-004B |
| E | Automatización, CI y ejecución | HU-007, HU-008B, HU-009, HU-011 |

### Mapa de dependencias

```text
HU-003B ─┐
         ├─> HU-002 ─> HU-003A ─> (HU aprobada) ─┬─> HU-006
         │                                        ├─> HU-004 ─> HU-004B
         │                                        │      └────> HU-009 ─> HU-007
         │                                        └─> HU-005 ─> HU-008A ─> HU-008B
RT-01..RT-06 aplican a todas
```

## Requisitos transversales

| ID | Requisito | Criterio verificable |
|---|---|---|
| RT-01 | **Trazabilidad y auditoría** | Cada acción registra `trace_id`, usuario autenticado, fecha UTC, acción, artefacto, versión y resultado. El registro es inmutable, se conserva 12 meses y se puede consultar por HU y por usuario. |
| RT-02 | **Validación humana** | Publicar, aprobar, abrir un pull request o ejecutar sobre recursos de Azure requiere confirmación explícita de un rol autorizado sobre la **versión exacta** (hash) mostrada. Sin confirmación, solo hay vista previa. |
| RT-03 | **Roles y permisos** | Autenticación corporativa (SSO/OIDC). Roles: PO, QA Engineer, QA Lead, DevOps y Administrador. Cada HU declara qué rol puede ejecutar cada acción. |
| RT-04 | **Disponibilidad del LLM y degradación** | Si el modelo no responde en 60 s o devuelve una respuesta inválida, el usuario recibe un error claro con `trace_id`, conserva sus datos y puede reintentar. No se inventan resultados. |
| RT-05 | **Datos sensibles** | No se envían al modelo credenciales ni datos personales reales. Los secretos se gestionan en Azure Key Vault y nunca aparecen en logs, prompts ni artefactos. |
| RT-06 | **Gobierno del modelo** | Modelo de referencia: Llama 3.2 Instruct. Cada artefacto generado registra modelo, versión del prompt y fecha. Los cambios de modelo o de prompt se prueban con un conjunto de HU de referencia antes de activarse. |

## Definición de Terminado (aplica a cada HU)

- Criterios de aceptación automatizados (unitarios y de contrato) y en verde en CI.
- RT-01 a RT-05 verificados para la funcionalidad.
- Artefactos generados por IA marcados como "borrador generado por IA" hasta su aprobación.
- Documentación en español actualizada; desplegado en el entorno de QA de Azure (AKS) mediante el pipeline de Azure DevOps.
- Sin hallazgos críticos de seguridad (análisis estático y de dependencias).

---

## HU-002 — Evaluación INVEST de una HU

**Título:** Como Product Owner, quiero que el agente evalúe mi HU con los seis criterios INVEST y me proponga mejoras concretas, para asegurar que esté bien definida antes de pasar a análisis.

**Fase:** A · **Prioridad:** Must · **Estimación:** 5 pts · **Depende de:** RT-01 y RT-03 · **Roles:** PO (evaluar, aprobar o rechazar), QA (consultar).

**Descripción:** el agente analiza título, descripción, reglas y criterios de aceptación; evalúa Independiente, Negociable, Valiosa, Estimable, Pequeña y Testeable, y entrega por criterio un estado, una justificación y, si no se cumple del todo, una sugerencia. El PO aprueba o rechaza cada sugerencia. La redacción de la nueva versión corresponde a HU-003A.

**Reglas de negocio:**
1. Cada criterio recibe un estado (`cumple`, `parcial` o `no_cumple`) y una justificación que cita la parte de la HU en la que se basa.
2. Todo criterio `parcial` o `no_cumple` incluye al menos una sugerencia **accionable**: una redacción alternativa concreta o el elemento faltante (por ejemplo, un criterio de aceptación). No son accionables los consejos genéricos del tipo "mejorar la claridad".
3. Requisitos mínimos para evaluar: título, descripción y al menos un criterio de aceptación. Si faltan, se indica qué falta y no se evalúa.
4. Ninguna sugerencia modifica la HU sin aprobación explícita del PO.
5. Cada evaluación queda versionada y asociada a la versión de la HU evaluada (RT-01).
6. Fuera de alcance: idiomas distintos del español.

**Criterios de aceptación:**
- **Dado** una HU con título, descripción y criterios, **cuando** el PO solicita la evaluación, **entonces** recibe los 6 criterios INVEST, cada uno con estado y justificación.
- **Dado** un criterio `parcial` o `no_cumple`, **cuando** se muestra el resultado, **entonces** incluye al menos una sugerencia con una redacción alternativa concreta.
- **Dado** una HU sin criterios de aceptación, **cuando** se solicita la evaluación, **entonces** se informa "Faltan criterios de aceptación" y no se genera evaluación.
- **Dado** una evaluación con sugerencias, **cuando** el PO aprueba o rechaza cada una, **entonces** se registra su decisión y la HU no cambia hasta ejecutar HU-003A.
- **Dado** una HU con evaluaciones previas, **cuando** el PO consulta el historial, **entonces** ve todas las evaluaciones con fecha, usuario, versión evaluada y decisión sobre cada sugerencia.
- **Dado** que el modelo no responde, **cuando** se solicita la evaluación, **entonces** se aplica RT-04 y no se registra una evaluación incompleta.

---

## HU-003A — Nueva versión de la HU a partir de sugerencias aprobadas

**Título:** Como Product Owner, quiero que el agente redacte una nueva versión de mi HU incorporando las sugerencias INVEST que aprobé, para no reescribirla a mano y conservar el historial de cambios.

**Fase:** A · **Prioridad:** Must · **Estimación:** 3 pts · **Depende de:** HU-002 · **Roles:** PO.

**Reglas de negocio:**
1. Solo se activa si existe una evaluación INVEST con al menos una sugerencia aprobada.
2. La nueva versión conserva la plantilla estándar: título, descripción ("Como / quiero / para"), reglas de negocio y criterios de aceptación.
3. Solo cambia lo que indican las sugerencias aprobadas; el resto se conserva literalmente.
4. La versión queda en estado "borrador generado por IA" hasta que el PO la apruebe o la descarte.
5. El PO puede editar libremente el borrador antes de aprobarlo.
6. Aprobar crea la versión N+1; descartar conserva la versión N. Ambas decisiones quedan registradas (RT-01).

**Criterios de aceptación:**
- **Dado** una evaluación con sugerencias aprobadas, **cuando** el PO solicita "generar HU actualizada", **entonces** recibe una versión completa con los 4 componentes y la lista de cambios frente a la versión anterior.
- **Dado** que no hay sugerencias aprobadas, **cuando** se solicita generar, **entonces** se informa "No hay sugerencias aprobadas" y no se genera versión.
- **Dado** un borrador, **cuando** el PO lo revisa, **entonces** puede editarlo, aprobarlo (crea la versión N+1) o descartarlo (se conserva la versión N).
- **Dado** una versión aprobada, **cuando** se consulta el historial, **entonces** se ven la evaluación y las sugerencias que la originaron.

---

## HU-003B — HU a partir de un requerimiento en lenguaje natural

**Título:** Como Product Owner, quiero que el agente redacte una o varias HU a partir de un requerimiento en lenguaje natural, para partir de un borrador estructurado en lugar de una hoja en blanco.

**Fase:** B · **Prioridad:** Must · **Estimación:** 5 pts · **Depende de:** RT-04 · **Roles:** PO.

**Reglas de negocio:**
1. El requerimiento tiene entre 10 y 20 000 caracteres.
2. Un requerimiento es **amplio** si contiene más de un actor principal, más de un flujo independiente o requeriría más de 6 criterios de aceptación. En ese caso se propone dividirlo, con un máximo de 5 HU por requerimiento.
3. Un requerimiento es **ambiguo** si falta el actor, la acción o el resultado esperado. En ese caso el agente hace hasta 2 preguntas concretas o redacta la HU documentando los supuestos (máximo 3).
4. Cada supuesto aparece en un campo visible y el PO debe confirmarlo o corregirlo antes de aprobar la HU.
5. Toda HU generada queda como "borrador generado por IA" y es editable por completo.

**Criterios de aceptación:**
- **Dado** un requerimiento claro con un actor y un flujo, **cuando** se solicita crear la HU, **entonces** se genera una HU con los 4 componentes, de 3 a 6 criterios Dado/Cuando/Entonces, marcada como borrador.
- **Dado** un requerimiento con dos flujos independientes, **cuando** se procesa, **entonces** se proponen al menos 2 HU, cada una testeable por separado, y el PO elige cuáles crear.
- **Dado** un requerimiento sin resultado esperado, **cuando** se procesa, **entonces** el agente pregunta por él o documenta el supuesto en el campo de supuestos.
- **Dado** una HU con supuestos sin confirmar, **cuando** el PO intenta aprobarla, **entonces** se le pide confirmarlos primero.

---

## HU-004 — Matriz de pruebas de una HU

**Título:** Como QA Engineer, quiero que el agente genere la matriz de pruebas de una HU, para estandarizar la cobertura positiva, negativa y de borde de cada criterio de aceptación.

**Fase:** D · **Prioridad:** Must · **Estimación:** 5 pts · **Depende de:** HU-003A o HU-003B · **Roles:** QA Engineer (generar, editar, aprobar), QA Lead (aprobar).

**Reglas de negocio:**
1. Mínimo por criterio de aceptación: 1 caso positivo, 1 negativo y 1 de borde.
2. Máximo 30 casos por generación. Como cada criterio exige al menos 3 casos, una HU admite como máximo 10 criterios por generación; si tiene más, se sugiere dividir la HU o generar por lotes de criterios.
3. Cada caso incluye ID, criterio de aceptación de origen, escenario, precondiciones, pasos, datos, resultado esperado, prioridad (alta/media/baja) y tipo (positivo/negativo/borde).
4. Se puede generar desde una HU aprobada o en borrador; si es un borrador, la matriz lo indica con una advertencia.
5. El QA puede editar, eliminar y agregar casos; cada cambio queda registrado (RT-01).
6. La matriz se aprueba explícitamente y se exporta a Excel (.xlsx) y CSV.

**Criterios de aceptación:**
- **Dado** una HU con 3 criterios, **cuando** se solicita la matriz, **entonces** se entregan al menos 9 casos: uno positivo, uno negativo y uno de borde por criterio, cada uno vinculado a su criterio.
- **Dado** una HU con 11 criterios, **cuando** se solicita la matriz, **entonces** no se genera y se sugiere dividir la HU o generar por lotes.
- **Dado** una matriz generada, **cuando** el QA edita un caso, **entonces** el cambio se guarda con usuario y fecha y la matriz vuelve a estado borrador.
- **Dado** una matriz aprobada, **cuando** se exporta, **entonces** el archivo Excel o CSV contiene todos los campos de la regla 3 y el ID de la HU.
- **Dado** una matriz aprobada, **cuando** se consulta la trazabilidad, **entonces** cada caso muestra su HU y su criterio de origen.

## HU-004B — Sincronización con la herramienta de gestión de pruebas y trazabilidad hasta el defecto

**Título:** Como QA Lead, quiero sincronizar la matriz aprobada con nuestra herramienta de gestión de pruebas y ver la cadena HU → caso → ejecución → defecto, para no duplicar trabajo y medir la cobertura real.

**Fase:** D · **Prioridad:** Should · **Estimación:** 8 pts (a reestimar según la herramienta) · **Depende de:** HU-004, HU-009 y la **decisión de herramienta** (por ejemplo, Azure Test Plans o Xray).

**Reglas de negocio:**
1. La herramienta destino se configura por proyecto; la primera versión soporta una sola herramienta.
2. La sincronización es idempotente: actualiza el caso ya sincronizado en lugar de duplicarlo.
3. La trazabilidad enlaza los resultados de HU-009 y los defectos registrados en la herramienta.

**Criterios de aceptación:**
- **Dado** una matriz aprobada, **cuando** se sincroniza, **entonces** cada caso existe en la herramienta con su ID de origen.
- **Dado** un caso ya sincronizado y editado, **cuando** se vuelve a sincronizar, **entonces** se actualiza sin duplicarse.
- **Dado** un caso ejecutado con defecto asociado, **cuando** se consulta la trazabilidad, **entonces** se ven la HU, el caso, la ejecución y el defecto.

---

## HU-005 — Identificación de riesgos antes de la ejecución

**Título:** Como QA Engineer, quiero que el agente estime el riesgo de las HU planificadas, para priorizar la ejecución de pruebas en la planificación semanal.

**Fase:** C · **Prioridad:** Must · **Estimación:** 5 pts · **Depende de:** HU aprobada · **Roles:** QA Engineer (analizar y ajustar), PO y DevOps (reciben notificaciones).

**Reglas de negocio:**
1. Dimensiones obligatorias, en escala de 1 a 5: complejidad, dependencias y criticidad de negocio. El agente propone cada valor con su justificación y el QA puede corregirlo.
2. Nivel por puntuación (suma de 3 a 15): bajo de 3 a 6, medio de 7 a 11, alto de 12 a 15.
3. El histórico de defectos es opcional. Si no está disponible, el análisis se genera igual e indica "histórico de defectos no considerado".
4. Cada riesgo incluye nivel, justificación y al menos una recomendación de mitigación.
5. Se analiza una HU o un lote de hasta 30 HU seleccionadas; el lote devuelve un orden de ejecución sugerido de mayor a menor riesgo.
6. Un riesgo alto notifica a PO y DevOps por el canal configurado del proyecto (correo o Teams). Si la notificación falla, se reintenta y se muestra un aviso, pero el análisis se entrega igual.
7. El QA puede cambiar el nivel o el orden; el cambio exige una justificación y queda registrado (RT-01).

**Criterios de aceptación:**
- **Dado** 5 HU seleccionadas, **cuando** se solicita el análisis, **entonces** cada una recibe puntuación por dimensión, nivel, justificación y mitigación, y se devuelve un orden sugerido.
- **Dado** que no hay histórico de defectos, **cuando** se genera el análisis, **entonces** se completa e indica "histórico de defectos no considerado".
- **Dado** una HU con puntuación 12 o mayor, **cuando** se completa el análisis, **entonces** se notifica a PO y DevOps y se sugiere (sin forzar) una prueba de performance (HU-008A).
- **Dado** un desacuerdo del equipo, **cuando** el QA cambia el nivel sin justificación, **entonces** el cambio se rechaza; con justificación, se guarda con usuario y fecha.

---

## HU-006 — Publicación y sincronización de la HU en Azure DevOps

**Título:** Como Product Owner, quiero publicar la HU aprobada como Work Item en Azure DevOps y mantenerla sincronizada, para evitar la carga manual duplicada.

**Fase:** B · **Prioridad:** Should · **Estimación:** 8 pts · **Depende de:** HU-003A o HU-003B, RT-02 y RT-05 · **Roles:** PO (publicar), Administrador (configurar el mapeo).

**Reglas de negocio:**
1. La conexión usa un token con permisos limitados al proyecto (lectura y escritura de Work Items), guardado en Azure Key Vault y rotado según la política de seguridad. Se prefiere una identidad administrada o service principal con acceso a Azure DevOps cuando el tenant lo permita.
2. El mapeo de campos es configurable por proyecto y proceso (Agile, Scrum o CMMI). Campos mínimos: título, descripción, criterios de aceptación, reglas de negocio (en HTML) y la etiqueta `valkiria`.
3. Idempotencia: se guarda el ID del Work Item asociado a la HU. Una nueva publicación actualiza ese Work Item; nunca crea un duplicado.
4. El PO confirma sobre una vista previa que muestra exactamente los campos que se enviarán (RT-02).
5. Si el Work Item se modificó en Azure DevOps después de la última sincronización, no se sobrescribe: se muestran las diferencias y el PO decide.
6. Los errores de sincronización se reintentan hasta 3 veces y se notifican con el motivo; la HU nunca se pierde.

**Criterios de aceptación:**
- **Dado** una HU aprobada nunca publicada, **cuando** el PO confirma la vista previa, **entonces** se crea el Work Item con el mapeo configurado y se guarda su ID.
- **Dado** una HU ya publicada y editada, **cuando** se vuelve a publicar, **entonces** se actualiza el mismo Work Item.
- **Dado** un Work Item modificado en Azure DevOps, **cuando** se intenta publicar, **entonces** se muestran las diferencias y no se sobrescribe sin confirmación.
- **Dado** un error de Azure DevOps (por ejemplo, token vencido), **cuando** ocurre, **entonces** se notifica al PO el motivo y la HU queda intacta con estado "no sincronizada".
- **Dado** el Work Item creado, **cuando** se compara con la HU aprobada, **entonces** todos los campos mapeados coinciden.

---

## HU-007 — YAML de pipeline de Azure DevOps

**Título:** Como equipo DevOps, quiero que el agente genere el YAML del pipeline de pruebas, para tener la CI lista aunque los scripts todavía no existan.

**Fase:** E · **Prioridad:** Could · **Estimación:** 5 pts · **Depende de:** HU-009 (opcional) y RT-05 · **Roles:** DevOps.

**Reglas de negocio:**
1. El YAML incluye como mínimo las etapas de build, test y publicación de resultados (JUnit).
2. La sintaxis se valida contra el esquema de Azure Pipelines antes de entregar y, si hay conexión al proyecto, con una ejecución de vista previa (sin ejecutar el pipeline).
3. Sin scripts existentes, la etapa de test usa un placeholder documentado que **se marca como omitido con una advertencia visible**; nunca reporta pruebas aprobadas.
4. Las variables sensibles se referencian mediante Variable Groups vinculados a Azure Key Vault o service connections de Azure Resource Manager con Workload Identity federation; nunca en texto plano.
5. El YAML se entrega como pull request para revisión de DevOps; sin commit automático.

**Criterios de aceptación:**
- **Dado** que no existen scripts, **cuando** se solicita el pipeline, **entonces** se genera un YAML válido cuya etapa de test aparece como omitida con advertencia.
- **Dado** scripts existentes de HU-009, **cuando** se solicita el pipeline, **entonces** la etapa de test los referencia por su ruta.
- **Dado** una variable sensible, **cuando** se genera el YAML, **entonces** se referencia por un Variable Group vinculado a Key Vault y no aparece su valor.
- **Dado** un YAML con error de sintaxis, **cuando** se valida, **entonces** no se entrega y se indica la línea y el motivo.
- **Dado** un YAML válido, **cuando** DevOps lo revisa, **entonces** puede editarlo en el pull request antes de integrarlo.

---

## HU-008A — Diseño de pruebas de performance

**Título:** Como QA Engineer, quiero que el agente diseñe el escenario y el script de una prueba de performance para cualquier HU, con o sin análisis de riesgo previo, para validar tiempos de respuesta antes de liberar.

**Fase:** C · **Prioridad:** Should · **Estimación:** 5 pts · **Depende de:** ninguna (HU-005 solo sugiere) · **Roles:** QA Engineer.

**Reglas de negocio:**
1. Hay dos disparadores independientes: la solicitud manual del QA o la sugerencia de HU-005 por riesgo alto. Ninguno es requisito del otro.
2. Cada escenario define tipo (carga, estrés o picos), usuarios virtuales, rampa, duración, transacciones objetivo y métricas con su SLA (p95 de latencia, tasa de error, throughput).
3. Los SLA provienen de la HU o del proyecto; si no existen, el QA los ingresa antes de generar.
4. La herramienta (JMeter o Locust, soportadas por Azure Load Testing) se elige por proyecto y el script se genera para esa herramienta.
5. El script se entrega por pull request y no se ejecuta en esta HU.

**Criterios de aceptación:**
- **Dado** una solicitud del QA sin análisis de riesgo, **cuando** se procesa, **entonces** se genera un escenario con métricas y SLA y un script para la herramienta del proyecto.
- **Dado** una HU de riesgo alto, **cuando** HU-005 termina, **entonces** se sugiere (sin forzar) crear la prueba.
- **Dado** que no hay SLA definidos, **cuando** se solicita la prueba, **entonces** se piden antes de generar.

## HU-008B — Ejecución de pruebas de performance en Azure

**Título:** Como QA Engineer, quiero ejecutar la prueba de performance en Azure Load Testing con recursos aislados y recibir un reporte contra SLA, para decidir la liberación con datos.

**Fase:** E · **Prioridad:** Could · **Estimación:** 13 pts · **Depende de:** HU-008A y RT-02 · **Roles:** QA Engineer (solicitar), DevOps (aprobar la ejecución).

**Reglas de negocio:**
1. Solo contra entornos autorizados que no sean de producción, registrados por proyecto.
2. Recursos aislados (recurso de Azure Load Testing en un grupo de recursos dedicado; red privada hacia el entorno objetivo) con límites por defecto: 30 minutos, 500 usuarios virtuales y un presupuesto máximo por ejecución. Superarlos requiere aprobación de DevOps.
3. Los recursos temporales de la ejecución se liberan al terminar, incluso si la prueba falla; el costo queda asociado al grupo de recursos dedicado.
4. El reporte compara cada métrica contra su SLA (criterios de fallo de Azure Load Testing) con resultado de aprobado o fallido, e incluye el costo estimado.

**Criterios de aceptación:**
- **Dado** un escenario aprobado dentro de los límites, **cuando** se ejecuta, **entonces** se ejecuta en Azure Load Testing de forma aislada y los recursos temporales se liberan al terminar.
- **Dado** un entorno objetivo de producción, **cuando** se solicita ejecutar, **entonces** se bloquea.
- **Dado** una ejecución terminada, **cuando** se genera el reporte, **entonces** cada métrica muestra valor, SLA y resultado.
- **Dado** un escenario que excede los límites, **cuando** se solicita ejecutar, **entonces** requiere aprobación de DevOps.

---

## HU-009 — Scripts de automatización por lotes

**Título:** Como QA Engineer, quiero que el agente genere scripts de automatización por lotes acotados a partir de casos de la matriz, para automatizar más rápido sin sobrecargar una sola ejecución.

**Fase:** E · **Prioridad:** Must · **Estimación:** 8 pts · **Depende de:** HU-004 · **Roles:** QA Engineer (generar, ejecutar), revisor del repositorio (aprobar el pull request).

**Reglas de negocio:**
1. Máximo 15 casos por lote; si se seleccionan más, se sugiere dividirlos en lotes.
2. Framework configurable por proyecto: Playwright o Selenium (web), RestAssured o Postman-Newman (API).
3. Cada script lleva el ID del caso que automatiza y cada caso enlaza a su script.
4. El script sigue el patrón del proyecto (por ejemplo, Page Object Model) y los datos se externalizan en archivos separados.
5. Se puede generar desde matrices en borrador; esos scripts quedan marcados y, si la matriz cambia, se avisa qué scripts quedaron desactualizados.
6. Antes del pull request se ejecutan lint y detección de secretos; si hay hallazgos, no se abre el PR.
7. Todo script se entrega por pull request; sin commit directo.

**Criterios de aceptación:**
- **Dado** un lote de 15 casos, **cuando** se solicita la generación, **entonces** se entrega un script por caso, vinculado a su ID.
- **Dado** 16 casos seleccionados, **cuando** se solicita la generación, **entonces** no se genera y se sugiere dividir en lotes de 15 como máximo.
- **Dado** los scripts generados, **cuando** se ejecutan en el ambiente de prueba configurado, **entonces** cada caso obtiene un resultado aprobado o fallido con evidencia trazable.
- **Dado** un script con un secreto embebido, **cuando** se prepara la integración, **entonces** no se abre el pull request y se indica el hallazgo.
- **Dado** un script validado, **cuando** se solicita su integración, **entonces** se abre un pull request que lista los casos vinculados, sin commit directo.

---

## HU-011 — Ejecución segura de scripts de base de datos (propuesta de incorporación)

**Título:** Como QA Engineer, quiero ejecutar scripts de validación de datos contra bases sintéticas controladas, para verificar reglas de datos sin arriesgar entornos productivos.

**Fase:** E · **Prioridad:** Should · **Estimación:** 8 pts · **Estado:** implementada en el repositorio (ver [HU-010 y HU-011](hu010-hu011.md)).

**Reglas de negocio:**
1. La conexión se resuelve por perfil configurado; nunca se aceptan DSN ni credenciales en la solicitud.
2. El análisis estático bloquea antes de conectar: `DROP`, `TRUNCATE`, privilegios y comandos del sistema.
3. Las mutaciones requieren transacción, `WHERE`, límite de filas en cada `UPDATE`/`DELETE` y rollback.
4. El entorno de producción está siempre bloqueado.
5. Cada ejecución genera evidencia descargable (PDF o Word) con resultado, filas afectadas y `trace_id`.

**Criterios de aceptación:**
- **Dado** un `SELECT` sobre el perfil sintético, **cuando** se ejecuta, **entonces** devuelve filas y evidencia.
- **Dado** un `DROP TABLE`, **cuando** se solicita ejecutar, **entonces** se bloquea antes de conectar.
- **Dado** un `UPDATE ... LIMIT 1` con rollback en PostgreSQL, **cuando** se ejecuta, **entonces** afecta como máximo 1 fila y los datos quedan intactos.
- **Dado** el entorno `production`, **cuando** se solicita ejecutar, **entonces** se bloquea sin generar evidencia.
