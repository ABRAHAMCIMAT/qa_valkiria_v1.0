# Historial de cambios

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).

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
