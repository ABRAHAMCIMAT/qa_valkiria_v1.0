# Historial de cambios

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).

## 2026-10-02 — Documentación de arquitectura 4+1

### Añadido
- **Arquitectura según el modelo 4+1** (`docs/arquitectura.md` y `docs/arquitectura/`), descrita a partir del código actual y con 51 diagramas Mermaid validados:
  - **+1 Escenarios:** actores, casos de uso UML, recorrido del usuario, mapa de los 14 pasos y 10 escenarios clave con su prueba.
  - **Vista lógica:** componentes; diagramas de clases del dominio, flujo, agentes, conversación, asistente y memoria; puertos y adaptadores; grafo de capacidades; modelo de datos (ER).
  - **Vista de procesos:** secuencias de chat, motor, orquestador, asistente, ejecución HU-010, aprobaciones, pipeline y SQL seguro; máquinas de estado; concurrencia y manejo de errores.
  - **Vista de desarrollo:** paquetes, dependencias reales con sus desviaciones, build, configuración, pruebas, CI y puntos de extensión.
  - **Vista física:** despliegue en AKS, Compose y kind; flujo de secretos con Workload Identity; puertos y endurecimiento.
  - **Decisiones, calidad y riesgos:** 13 ADR, árbol de utilidad, escenarios de calidad, 12 riesgos priorizados y hoja de ruta.

## 2026-09-29 — Los resultados alimentan el flujo y el pipeline de Azure DevOps (incremento 3)

### Añadido
- **Revisión de fallos** (paso nuevo tras HU-010, `application/defects.py`):
  - Un borrador de defecto por caso fallido, ligado a su caso y criterio, con los pasos para reproducir, lo esperado frente a lo obtenido y la evidencia.
  - Clasificación sugerida y determinista (defecto, caso o script mal planteado, ambiente); la decisión es humana por cada fallo.
  - Vista previa de Bug de Azure Boards; su publicación queda pendiente de decidir la herramienta (HU-004B).
  - Los fallos idénticos conservan la decisión anterior.
- **Bloqueo del PR:** el pipeline no se aprueba mientras la ejecución vigente tenga fallos sin revisar.
- **Pipeline de Azure DevOps** (HU-007):
  - JUnit publicado para cada stack.
  - Etapa **DataValidation** con las consultas HU-011 aprobadas (`validate_data.py`, transacción de solo lectura).
  - Etapa **ReportToValkiria** (`report_results.py`) que envía los resultados de vuelta.
- **Endpoint** `POST /v1/workflows/{id}/pipeline-results`:
  - Autenticado con `VALKIRIA_PIPELINE_CALLBACK_TOKEN`; sin token responde 503.
  - Registra una nueva ejecución de origen Azure DevOps, asociada a los casos de la matriz, y prepara su revisión.
- **Token:** Bicep crea el secreto `valkiria-pipeline-token` en Key Vault, sincronizado a la API por CSI. Compose y kind traen un token solo para el entorno local.
- **Frontend:**
  - Tarjeta de revisión con una decisión por fallo.
  - Paso "Revisión de fallos" en el flujo.
  - El pipeline muestra sus archivos y requisitos.

- **Aviso en la matriz:** en modo sintético avisa, antes de aprobarla, de los casos de interfaz que nunca envían el formulario o que van y vienen entre pantallas; al ejecutarse fallarían por diseño.

### Cambiado
- Un paso que falló ya no se reintenta con cada acción ajena (aprobar otra cosa, conversar); solo cuando se vuelve a pedir o se reanuda. En vivo, una validación de datos fallida se reintentaba en cada aprobación y la hacía tardar unos 140 s.
- `SQL_VALIDATION_SYSTEM` sube de 700 a 1200 tokens: con matrices de 15 casos, el JSON llegaba truncado.
- La ejecución y su revisión son pasos de un solo uso. Si cambian los scripts o las consultas, quedan desactualizadas, sin repetirse solas; se vuelven a pedir y solo usan lo aprobado.
- Las consultas de datos solo cuentan para el pipeline y la ejecución una vez aprobadas.

### Verificado en vivo (Llama 3.2, Compose)
- **Flujo completo** de la HU de órdenes de venta: matriz de 15 casos, 4 consultas de datos (2 bloqueadas por el análisis estático) y scripts Playwright web.
- **Ejecución en Valkiria:** 3 de 17 aprobados, con 14 borradores de defecto. El PR quedó bloqueado hasta revisarlos y, tras la revisión, se aprobó.
- **Simulación local del pipeline** con los archivos generados:
  - `npx playwright test` sobre los scripts;
  - `validate_data.py` contra PostgreSQL;
  - `report_results.py` hacia `/pipeline-results` (HTTP 200).
- **Resultado del pipeline:** la ejecución v2 de origen Azure DevOps dio los mismos 3 de 17 que la local, con su revisión de fallos.
- **Clasificación de fallos:** 13 de los 14 los causó el diseño del caso (nunca presiona "Registrar orden") y ahora se clasifican como tales.

## 2026-09-29 — Pantallas web sintéticas y ejecución de scripts web (incremento 2)

### Añadido
- **Pantallas web mínimas** en la app sintética (`synthetic_app/web.py`): consulta de inventario (`/ui`), registro de orden de venta (`/ui/orders`) y citas de servicio (`/ui/appointments`). Son HTML accesible, con etiquetas, roles y avisos `role=alert`.
- **Semántica compartida de los pasos web** (`application/web_steps.py`):
  - Cada paso se traduce a una acción concreta y cada resultado esperado a textos visibles y aviso de error.
  - Los pasos se aterrizan en el catálogo de pantallas: botón o campo real, navegación entre pantallas, datos del caso antes de enviar el formulario.
  - Un paso sin correspondencia falla señalándolo; nunca se omite.
- **Runner web** (`PlaywrightRunner`): ejecuta los pasos en Chromium y reporta por caso los pasos ejecutados, el paso que falla con su motivo y la captura. La tarjeta de ejecución muestra la captura de las fallas.
- **Imagen `-browser`** (`--build-arg WITH_BROWSER=true`) con Chromium de solo lectura. La API de Compose y kind la usa con `VALKIRIA_AUTOMATION_EXECUTE=true`.
- La matriz recibe, en modo sintético, el catálogo de pantallas y los datos que existen (`UI_GUIDE`), para que sus casos sean ejecutables.
- **Pruebas:**
  - semántica de pasos;
  - código generado;
  - flujo con un runner de prueba;
  - E2E del runner real en Chromium contra la app sintética (en CI con `playwright install chromium`).

### Cambiado
- El código Playwright (TypeScript) y Selenium (Python) generado usa la misma traducción que el runner:
  - abre la pantalla del caso en lugar de la raíz del sitio;
  - verifica textos visibles y el aviso de error en lugar de una frase literal de 40 caracteres;
  - hace clic primero en el botón y solo después en un enlace;
  - Selenium espera la página nueva tras enviar un formulario.
  
  Se verificó ejecutando el código generado con `npx playwright test` y con Selenium: dan los mismos resultados que el runner.
- Sin navegador habilitado, ejecutar scripts web explica cómo habilitarlo y ofrece un stack de API.

### Verificado en vivo (Llama 3.2, Compose y kind)
- **HU de órdenes de venta:** 15 casos web ejecutados en Chromium en 26 s, con 10 aprobados. Antes del aterrizaje de pasos, la misma matriz daba 0 de 15.
- **HU de inventario por concesionario:** 12 casos, con 5 aprobados y todos los pasos ejecutados.
- **Fallidos:** son errores reales de diseño del caso, cada uno con su motivo:
  - mensajes inventados ("Error de conexión");
  - expectativas que contradicen los datos (concesionario inactivo con la expectativa "sin stock");
  - pasos que nunca envían el formulario.
- Chromium corre dentro del pod de kind con sistema de archivos de solo lectura.

## 2026-09-29 — HU-011 en el flujo y ejecución unificada HU-010 (incremento 1)

### Añadido
- Paso **HU-011 Validación de datos** tras la matriz.
  - **Consultas:** de solo lectura, derivadas de las reglas de la HU y de los casos negativos y de borde, cada una ligada a su criterio y caso y con su resultado esperado ("sin filas" si buscan violaciones).
  - **Validación previa:** cada consulta se prueba en la base sintética; si falla se corrige una vez y, si no, queda inválida. Se muestra cuántas filas devuelve hoy, para detectar una expectativa mal planteada antes de aprobar.
  - **Sugerencia y aprobación:** se sugiere si la HU tiene reglas de datos, y requiere verificación humana.
- **HU-010 unificada:** ejecuta en una corrida los casos de API y las consultas de datos aprobadas.
  - Veredicto por caso y evidencia PDF consolidada.
  - Los errores de SQL se reportan aparte de los fallos de datos.
  - Los scripts web sin interfaz se omiten con aviso.
- La herramienta "Validación de base de datos" usa el paso del flujo cuando hay HU y matriz.

### Cambiado
- La HU nueva se redacta con `STORY_SYSTEM`: de 36 s, con tiempos agotados, a 10 s. La cortesía usa `SMALLTALK_SYSTEM`. Un requerimiento demasiado vago recibe preguntas en lugar de una HU inventada.
- El proveedor reintenta una vez si el JSON llega incompleto, pidiendo JSON compacto. El mensaje distingue "respuesta incompleta" de "no respondió a tiempo".
- Sin HU en curso, pedir un paso del flujo explica que primero se necesita la historia.
- Ollama local con `OLLAMA_KEEP_ALIVE=24h`, como en AKS. `PROMPT_VERSION = "v4"`.

## 2026-09-29 — Scripts reales, ejecución HU-010, verificación humana y herramientas contextuales

### Añadido
- `application/script_generation.py`: código real por caso (HU-009) en el stack elegido, con Page Object, datos externalizados y lint, secretos y trazabilidad antes del PR:
  - Playwright web (TypeScript) y Selenium (Python);
  - API contra la app sintética: Playwright `request`, RestAssured o Postman-Newman.
- Paso HU-010 "Ejecución de scripts" en el flujo, tras aprobar los scripts. Ejecuta contra la app sintética las llamadas de los scripts y genera evidencia PDF por caso (`application/automation_runner.py`).
- Verificación humana de scripts, pipeline y diseño de performance, además de HU, matriz y Work Item. Botones de aprobar y rechazar en cada tarjeta.
- Sugerencia de prueba de performance (carga, estrés o picos) con riesgo alto o requisitos de rendimiento en la HU (HU-005 regla 6, HU-008A).
- Herramientas del panel completas y contextuales:
  - validación de BD derivada de la HU (`SQL_VALIDATION_SYSTEM`, solo lectura, con evidencia);
  - herramientas de BD y automatización según el stack;
  - memoria por título de HU;
  - análisis de SQL con formulario.
- Un artefacto se regenera si cambian los datos con que se generó (stack, repositorio, parámetros de performance).

### Cambiado
- Matriz en formato compacto por criterio y en paralelo: el código arma id, tipo, criterio, prioridad y precondiciones, con cobertura garantizada. Con 3 criterios pasa de 57 s a 23 s. `VALKIRIA_LLM_MAX_PARALLEL` y `OLLAMA_NUM_PARALLEL=4` en Compose y AKS. `PROMPT_VERSION = "v3"`.
- Si el modelo no responde para ningún criterio, la matriz falla con reintento en lugar de entregarse con plantillas (RT-04).
- El pipeline usa el comando de prueba del stack (`npx playwright test`, `pytest`, `mvn test`, `newman run`).

### Corregido
- Los scripts eran plantillas sin los pasos del caso.
- La app sintética con SQLite en memoria perdía las tablas entre hilos.

## 2026-09-29 — Conductor de la conversación y flujo completo en la interfaz

### Añadido
- `src/valkiria/conversation/`: el conductor de la conversación, nuevo agente principal del chat.
  - Liga cada sesión a un flujo por historia y recuerda en qué paso va.
  - Decide de forma determinista qué hacer con cada mensaje: acciones, políticas, cortesía, nueva historia explícita, modificar la HU, avanzar el flujo, atender preguntas con prioridad al humano, o preguntar antes de reiniciar.
- `/v1/chat` acepta acciones (`run`, `input`, `approve`, `reject`, `decide_suggestions`, `edit_story`, `new_story`, `choose_split`, `export_matrix`, `resume`). Responde con `flow` (11 pasos con su estado y versiones), `artifact`, `actions` y `resume`.
- Motor: `adopt_story` (la HU del chat inicia el flujo) y `revise_story` (toda modificación crea la versión N+1). Nuevo prompt `STORY_EDIT_SYSTEM`.
- Interfaz con el flujo completo:
  - panel con los 11 pasos, su estado, el siguiente paso y botones;
  - historial de versiones;
  - tarjetas para cada artefacto (HU, INVEST, matriz, riesgo, scripts, pipeline, performance, Work Item);
  - formularios para decidir sugerencias, aportar datos y confirmar supuestos;
  - recordatorio del paso en curso.
- `docs/conversacion.md`; 16 pruebas en `tests/test_conversation.py`.

### Corregido
- "Mejora la historia" no siempre generaba una versión nueva. Ahora toda modificación crea la versión N+1 con su lista de cambios.
- Algunas preguntas devolvían el chat al inicio como si se pidiera una historia nueva. Ahora se atienden y se retoma el paso en curso.
- "Siguiente" o "¿qué sigue?" podían aprobar un artefacto. Ahora piden la confirmación explícita (RT-02).
- La matriz de una HU con muchos criterios superaba el límite de 60 s. Ahora se genera por criterio y cada llamada cabe en el límite.
- Las preguntas sobre la HU en curso se responden con el estado real del flujo.

## 2026-09-29 — System prompts v2: reglas de negocio de las HU y conversación natural

### Añadido
- `src/valkiria/application/prompts.py`: todos los prompts centralizados y versionados (`PROMPT_VERSION = "v2"`). Aplican las reglas de HU-002, 003A/B, 004, 005 y 008A, y comparten una guía de conversación: tono de colega, saludos, disculpas, límites amables y máximo 2 preguntas.
- `evals/prompt_eval.py`: evaluación reproducible contra el modelo real, con comparación entre versiones (`--prompts`). Resultados en `docs/estado-validacion.md`. Documentación en `docs/prompts.md`.
- Reglas garantizadas en código:
  - **HU-005:** puntuación de 1 a 5 por dimensión y nivel derivado de la suma.
  - **HU-003B:**
    - división de requerimientos amplios en el chat, con elección de la HU;
    - supuestos que se confirman antes de aprobar (`assumptions_confirmed`);
    - HU adicionales sugeridas como advertencia.
  - **HU-002:** sugerencias faltantes completadas con una tarea acotada.
  - **HU-004:** lista explícita de casos requeridos.
  - **HU-008A:** los datos obligatorios faltantes, como el SLA, se piden y no se inventan.
- **RT-04:** tiempo límite de 60 s (`VALKIRIA_LLM_TIMEOUT_SECONDS`). El chat nunca se rompe: ante una falla responde con `trace_id` y la opción de reintentar.
- **RT-05:** el texto se redacta antes de enviarlo al modelo.
- **RT-06:** cada artefacto registra el modelo y la versión del prompt.
- Chat:
  - saludos, agradecimientos y molestias se responden conversando;
  - se usa el resumen de turnos antiguos;
  - la interfaz muestra la división de HU y la opción de reintentar.
- Presupuesto de tokens de salida por prompt, que evita que el modo JSON se quede en bucle hasta el tiempo límite.
- `STORY_SPLIT_SYSTEM`: la división se verifica con una tarea acotada, aunque el chat elija "crear" o devuelva una división vacía. Una HU nueva nunca se redacta con la HU en curso como contexto.
- Asistente:
  - las consultas de datos responden con la conclusión calculada;
  - extracción determinista de valores cerrados;
  - preguntas de aclaración;
  - glosario literal para conceptos;
  - negativas amables que distinguen una falla temporal de una capacidad inexistente.

## 2026-09-29 — Memoria corta y larga, y asistente de razonamiento con herramientas

### Añadido
- `src/valkiria/memory/`: memoria de corto plazo y memoria de largo plazo.
  - **Corto plazo:** ventana de turnos con resumen determinista, datos de sesión y expiración por inactividad.
  - **Largo plazo:** aprende solo de decisiones humanas, con BM25 y decaimiento por antigüedad, redacción de secretos, deduplicación, retención, `namespace` y olvido.
  - **Persistencia:** SQLite o PostgreSQL. Por defecto usa la base de los flujos; en Azure, `valkiria_workflows`.
- El flujo por historia aprende de aprobaciones, decisiones INVEST, ediciones, rechazos con comentario y fallos definitivos. Además:
  - Inyecta los recuerdos relevantes en HU, INVEST, matriz y riesgo.
  - Registra `memory_used` en cada artefacto.
  - Si la memoria falla, continúa sin ella.
- Sesiones en `/v1/chat` y `/v1/agent/execute`:
  - El chat recupera la HU en curso sin que el cliente reenvíe el historial.
  - El orquestador resuelve seguimientos cortos heredando la intención.
- `src/valkiria/assistant/`: asistente de razonamiento para peticiones fuera del flujo.
  - Catálogo cerrado de 11 herramientas y 4 skills conectadas a código real.
  - Políticas resueltas en código.
  - Glosario de QA verificado.
  - Ciclo decidir → ejecutar → observar.
  - Verificación antes de rendirse o de responder de memoria.
  - Redacción final verificada contra las conclusiones calculadas por cada herramienta.
  - Respuesta honesta de "no puedo", con alternativas reales.
- Agente `assistant` en el orquestador. `/v1/chat`, `/v1/workflows` y `/v1/workflows/{id}/requests` enrutan al asistente lo que no es trabajo del flujo.
- Endpoints `/v1/assistant/ask`, `/v1/assistant/capabilities` y `/v1/memory/...`. `/health` informa la memoria, los agentes y las herramientas.
- Interfaz: conserva `session_id`, muestra las herramientas usadas y "Cómo lo resolví".
- 6 variables `VALKIRIA_MEMORY_*`. `docs/memoria.md` y `docs/asistente.md`.
- 50 pruebas nuevas (memoria, asistente, enrutamiento y regresiones de fallas reales de Llama 3.2).

### Cambiado
- Las peticiones sin tareas reconocibles ya no se convierten por defecto en una HU ni se ignoran dentro de un flujo: se responden con herramientas.
- Grounding ya no bloquea preguntas cortas ("¿Qué puedes hacer?") ni seguimientos de una sesión activa.

## 2026-09-28 — Flujo de agentes por historia

### Añadido
- `src/valkiria/workflow/`, con cinco piezas:
  - Grafo de capacidades por HU con validación de ciclos.
  - Planificador determinista con razonamiento explicable.
  - Estado versionado con `based_on`, aprobaciones por versión y hash exactos, e historial.
  - Motor con puntos de control, reintentos con espera exponencial, aislamiento de fallos y reanudación.
  - Validadores de INVEST y de la matriz, con corrección y completado determinista.
- API `/v1/workflows` (iniciar, continuar, aprobar, editar, reanudar, consultar y capacidades).
- Persistencia `VALKIRIA_WORKFLOW_DATABASE_URL` (SQLite o PostgreSQL con control optimista); en Azure, base `valkiria_workflows` con la URL en Key Vault.
- 22 pruebas del flujo y de su API; `docs/flujo-historias.md`.

### Corregido
- El prompt de la matriz pedía "1 a 3 casos por criterio, máximo 12", contra la regla de HU-004. Ahora exige al menos un caso positivo, uno negativo y uno de borde por criterio, con un máximo de 30.
- El placeholder del pipeline (HU-007) ya no reporta la etapa de pruebas como aprobada: queda `SucceededWithIssues` con advertencia.

## 2026-09-28 — Azure como única plataforma y Llama 3.2 Instruct

### Añadido
- `deploy/azure/bicep/main.bicep` y `main.bicepparam`: Log Analytics, ACR, AKS (Workload Identity, Key Vault CSI, app routing, Container Insights), identidad administrada con credencial federada, Key Vault y PostgreSQL Flexible Server 16.
- `deploy/azure/aks/`: overlay de AKS con imagen desde ACR, secretos desde Key Vault (`SecretProviderClass`), Ollama con Llama 3.2 Instruct y volumen persistente, app sintética con migración automática e Ingress.
- `deploy/azure/deploy.sh` y `azure-pipelines.yml` (Validate, E2E, Build con `az acr build`, DeployQA con aprobación).
- `VALKIRIA_LLM_AUTH_HEADER` (`authorization` o `api-key`) para endpoints compatibles con OpenAI que usan llave `api-key`, con pruebas del proveedor.

### Cambiado
- Modelo por defecto: `llama3.2:3b-instruct-q4_K_M` (Llama 3.2 Instruct).
- Historias v3.0 y revisión: se eliminó AWS. La épica exige Azure; HU-006 y HU-007 usan Key Vault; HU-008B se ejecuta en Azure Load Testing (JMeter o Locust).
- Adaptador de nubes: solo Azure.

### Eliminado
- `deploy/terraform/` (guía multinube que incluía AWS).

## 2026-09-28 — Revisión del Plan de Mejora Valkiria 2026

### Añadido
- `docs/revision-historias-2026.md`: revisión de EPIC-001 y HU-002 a HU-009 con hallazgos transversales, evaluación INVEST de cada historia original, contraste con el código y recomendaciones priorizadas.

### Cambiado
- `docs/epica-historias-invest.md` v3.0: historias mejoradas con la numeración del plan, KPIs, glosario de fases, dependencias, requisitos transversales RT-01 a RT-06, Definición de Terminado, HU-004 dividida (HU-004B) y HU-008 dividida (HU-008A/B), y HU-011 propuesta para incorporarse al plan.

## 2026-09-28 — Quality gates por agente

### Corregido
- `database` y `automation` comparten la fase `evaluation` con el agente de evaluación, y como los gates se guardaban por fase, el último sobrescribía a los demás. Ahora `quality_gates` tiene un gate por agente, con un campo `phase` nuevo.
- `deploy/kind/up.sh` espera a que terminen los pods de la versión anterior antes de anunciar que el entorno está listo.

### Cambiado (contrato de la API)
- Las claves de `quality_gates` son los nombres de los agentes. La única que cambia es la del agente de operaciones: pasa de `operate` a `operations`. Todas las demás ya coincidían.

## 2026-09-28 — Despliegue en Kubernetes local

### Añadido
- `deploy/kind/`: clúster kind con API, aplicación sintética y PostgreSQL, publicado en `http://localhost:8080` (`up.sh`, `down.sh`, overlay de kustomize y `kind-config.yaml`).
- `deploy/kubernetes/kustomization.yaml`: el manifiesto pasa a ser base reutilizable por overlays.
- `initContainer` que espera a PostgreSQL antes de arrancar la aplicación sintética.

## 2026-09-28 — Mutaciones en PostgreSQL

### Corregido
- Las mutaciones contra PostgreSQL siempre fallaban (`ProgrammingError`), porque la política exige `LIMIT` en `UPDATE`/`DELETE` y PostgreSQL no admite esa sintaxis. Ahora `UPDATE`/`DELETE ... WHERE cond LIMIT n` se traduce a `... WHERE ctid IN (SELECT ctid FROM t WHERE cond LIMIT n FOR UPDATE)`.

### Añadido
- `application/sql_dialect.py`: divisor de sentencias compartido y traducción de dialecto. Las formas ambiguas se rechazan con `postgresql_limit_requires_simple_form_or_subquery`.
- `dialect_rewrites` en el análisis estático y en el resultado del ejecutor de PostgreSQL (sentencia original y ejecutada).
- `tests/test_sql_dialect.py` (19 pruebas) y `tests/e2e/test_postgres_mutations.py` (4 E2E contra PostgreSQL).

### Cambiado
- `static_analyse_database_script` acepta un parámetro opcional `engine`; sin él no se aplica la validación de dialecto de PostgreSQL.
- El límite de filas se exige en cada `UPDATE`/`DELETE` (`each_update_delete_requires_row_limit`), no en cualquier sentencia del script.

## 2026-09-28 — CI con Node 24 (`fe7efd5`)

### Cambiado
- Acciones de GitHub actualizadas a Node 24: `actions/checkout@v7`, `actions/setup-python@v7` y `actions/upload-artifact@v7`.
- Runner fijado en `ubuntu-24.04`, en lugar de `ubuntu-latest`, que migra a Ubuntu 26 a partir del 19 de octubre de 2026.

## 2026-09-28 — Herramientas de desarrollo, Docker, Compose y CI (`1e17dc9`)

### Añadido
- Pipeline de CI en `.github/workflows/ci.yml` con cuatro trabajos: Ruff y Bandit; unitarias y E2E contra PostgreSQL; wheel y sdist; imagen Docker y entorno Compose con prueba de `/health`.
- `tests/test_settings.py` (carga de `.env`, precedencia, secretos, bloqueo de producción y sincronía con `.env.example`) y `tests/conftest.py` (aísla las unitarias del `.env` local).
- `.dockerignore`, `.gitattributes` (finales de línea LF) y [guía de despliegue](docs/despliegue.md).
- Extra `postgres` en `pyproject.toml` (driver psycopg para la imagen) y `pytest-asyncio` y `build` en el extra `dev`.
- Configuración de Ruff (reglas explícitas) y de Bandit en `pyproject.toml`.
- Variable `VALKIRIA_FRONTEND_DIR` para ubicar el frontend fuera del repositorio.

### Cambiado
- `Settings` migrado a pydantic-settings: lectura al instanciar, carga de `.env`, `SecretStr` para secretos, lista de orígenes separada por comas y validación de producción.
- `.env.example` unificado como plantilla única.
- Dockerfile multi-etapa: construye el wheel, copia el frontend, corre como usuario no root (`uid 10001`) e incluye healthcheck.
- `docker-compose.synthetic.yml` levanta el entorno completo: PostgreSQL con healthcheck, migraciones y datos iniciales, aplicación sintética, API y Ollama opcional, con URLs internas entre contenedores y contenedores endurecidos.
- Manifiesto de Kubernetes con imagen real, ConfigMap, Secret opcional, sondas, `runAsUser 10001`, seccomp y Service.
- Capturas de excepciones acotadas; las capturas genéricas restantes son fronteras de aislamiento justificadas.
- `domain/models.py` y los adaptadores reformateados, con una sentencia por línea.

### Corregido
- Los 35 hallazgos de Ruff (imports, import sin uso, capturas genéricas, formato de modelos, `pytest.raises(Exception)` genérico, alias obsoletos).
- Tres falsos positivos de Bandit B105 que impedían que el CI anterior pasara.
- División de sentencias SQL en scripts de una sola línea (la prueba de rollback fallaba).
- Prueba del registro de agentes, a la que le faltaba el agente `automation`.
- Prueba E2E asíncrona, que no corría por falta de soporte async.

### Eliminado
- `.env.synthetic.example` (fusionado en `.env.example`).
- `ci.yml` en la raíz (movido a `.github/workflows/`).
- `deploy/docker/docker-compose.yml`, que apuntaba a un Dockerfile inexistente.

## 2026-09-28 — HU-010 y HU-011 (`135cb56`)

### Añadido
- Selección de plataforma, lenguaje y herramienta (`/v1/automation/tools/select`).
- Exportación de casos manuales a Excel (`/v1/test-cases/export`).
- Descarga de reportes de evidencia en PDF (`/v1/reports/{id}/download`).
- Ejecución real del lote de automatización cuando el runner está habilitado.

## 2026-09-27 — Carga inicial (`52e4dc7`)

- Proyecto Valkiria QA multiagente v1.0 (paquete 0.5.0).
