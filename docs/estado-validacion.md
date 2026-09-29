# Estado de validación y pendientes de producción

Última actualización: 2026-09-28 (corrección de mutaciones en PostgreSQL).

## CI en GitHub Actions

El pipeline `.github/workflows/ci.yml` pasa en `main` con sus cuatro trabajos: Ruff y Bandit; unitarias y E2E sintética; wheel y sdist; imagen Docker y entorno de desarrollo. Usa `ubuntu-24.04` y acciones con Node 24, sin avisos de obsolescencia. Historial: https://github.com/ABRAHAMCIMAT/qa_valkiria_v1.0/actions/workflows/ci.yml

## Validación local

Ejecutada en macOS con Python 3.12.2 y Docker 29.8.1, con el código ya integrado con el commit de HU-010/HU-011.

| Verificación | Comando | Resultado |
|---|---|---|
| Docker daemon | `docker info` | Activo (Docker Desktop 29.8.1) |
| Ruff | `ruff check src tests` | Sin errores |
| Bandit | `bandit -q -c pyproject.toml -r src` | Sin hallazgos |
| Unitarias | `pytest -q --ignore=tests/e2e` | 174 aprobadas (aisladas del `.env` local mediante `tests/conftest.py`) |
| E2E sintética | `RUN_SYNTHETIC_E2E=1 pytest -v tests/e2e` | 5 aprobadas contra PostgreSQL 16 (flujo de la app sintética y mutaciones con límite); repetibles |
| Paquete | `python -m build` | `valkiria-0.5.0-py3-none-any.whl` y `valkiria-0.5.0.tar.gz`; el wheel se instala y arranca en un entorno limpio |
| Imagen Docker | `docker build -f deploy/docker/Dockerfile .` | 303 MB, usuario `uid=10001`, healthcheck activo |
| Entorno Compose | `docker compose -f docker-compose.synthetic.yml up -d --build --wait` | `postgres`, `synthetic-app` y `api` en estado *healthy* |
| `/health` | `curl localhost:8000/health`, `curl localhost:8090/health` | 200 en ambos; frontend `/` y `/assets` en 200 |
| Consulta vía API a PostgreSQL | `POST /v1/database/scripts/execute` (SELECT) | `completed`, devuelve Sentra y Versa |
| Mutación vía API a PostgreSQL | `POST /v1/database/scripts/execute` (`UPDATE ... LIMIT 1` con rollback) | `completed`, 1 fila afectada, reporte generado, datos intactos |
| Workflow de CI | `actionlint .github/workflows/ci.yml` | Válido |
| Kubernetes | `kubeconform -strict deploy/kubernetes/deployment.yaml` | 3 recursos válidos |
| Bicep de Azure | `bicep build` y `bicep lint deploy/azure/bicep/main.bicep` | Compila sin errores ni advertencias; el archivo de parámetros también compila |
| Overlay de AKS | `kubectl kustomize deploy/azure/aks` + `kubeconform` (con esquemas de CRD) | 12 recursos válidos, incluido `SecretProviderClass`; el renderizado con valores de prueba no deja marcadores `__...__` |
| Pipeline de Azure DevOps | `check-jsonschema` contra el esquema oficial de Azure Pipelines | Válido; se comprobó que el validador detecta un error introducido a propósito |
| Llama 3.2 Instruct | Proveedor contra Ollama local (`llama3.2:3b-instruct-q4_K_M`) | JSON válido en unos 7,5 s |
| Kubernetes local (kind) | `deploy/kind/up.sh` | 9 recursos aplicados; API, app sintética y PostgreSQL listos en unos 75 s desde cero, con 0 reinicios; `/health`, consultas, mutaciones, bloqueo de SQL peligroso y orquestador con LLM verificados en `localhost:8080` |

## Cambios realizados

- **Ruff**: se corrigieron los 35 hallazgos que reporta Ruff 0.16 (los 19 originales y otros de reglas nuevas), además de 9 sentencias compuestas en una línea. Las reglas quedan fijadas en `pyproject.toml` para que el resultado no cambie según la versión de Ruff.
- **Capturas genéricas**: se acotaron a excepciones concretas (`LLMProviderError`, `SQLAlchemyError`, error de Playwright). Quedan tres capturas genéricas con `noqa` justificado, todas fronteras de aislamiento: el orquestador, el ejecutor de PostgreSQL sintético y el runner de Playwright (estas dos últimas se conservaron tal como las dejó el commit de HU-010/HU-011).
- **Pruebas**: `pytest-asyncio` en modo `auto`. Se corrigió la división de sentencias SQL en scripts de una sola línea (fallaba la prueba de rollback) y se actualizó la prueba del registro, a la que le faltaba el agente `automation`. Se añadió `tests/test_settings.py`.
- **Configuración**: `Settings` usa pydantic-settings. Las variables se leen al instanciar (antes se leían al importar el módulo) y `.env` se busca en el directorio actual y en la raíz del repositorio. Los secretos se manejan como `SecretStr` y no aparecen en `repr`. `environment=production` exige `allow_production=true`.
- **`.env.example`**: plantilla única (se eliminó `.env.synthetic.example`); una prueba verifica que coincide campo por campo con `Settings`.
- **Dockerfile**: multi-etapa (build del wheel y luego ejecución), frontend incluido, usuario no root, healthcheck y driver de PostgreSQL. Se añadió `.dockerignore` para excluir `.venv` y `.env`.
- **Compose**: un solo archivo, `docker-compose.synthetic.yml`, con contexto y Dockerfile correctos, PostgreSQL con healthcheck, migraciones y datos iniciales, aplicación sintética, API y URLs internas entre contenedores. Los puertos quedan en `127.0.0.1` y los contenedores sin capacidades extra y con sistema de archivos de solo lectura. Se eliminó `deploy/docker/docker-compose.yml`, que apuntaba a un Dockerfile inexistente.
- **CI**: `.github/workflows/ci.yml` con cuatro trabajos: `lint` (Ruff y Bandit), `test` (migraciones, unitarias y E2E contra PostgreSQL), `package` (wheel, sdist y prueba de instalación) y `docker` (imagen, verificación de usuario no root, entorno Compose y `/health`).
- **Integración con HU-010/HU-011**: el commit `135cb56` de otro colaborador tocaba los mismos archivos. Se conservó su versión en los conflictos y se añadió `tests/conftest.py` para que una de sus pruebas no dependiera del `.env` local.
- **CI con Node 24**: `actions/checkout@v7`, `actions/setup-python@v7`, `actions/upload-artifact@v7` y runner `ubuntu-24.04`.
- **Kubernetes**: imagen `ghcr.io/abrahamcimat/qa_valkiria_v1.0:0.5.0`, ConfigMap, Secret opcional, sondas de salud, `runAsUser 10001`, seccomp, `/tmp` como `emptyDir` y Service.

## Flujo de agentes por historia

| Verificación | Resultado |
|---|---|
| Pruebas del flujo (`tests/test_workflow.py`, `tests/test_workflow_api.py`) | 22 aprobadas: dependencias, aprobación exacta, obsolescencia, sugerencias, reintentos, aislamiento, reanudación, datos faltantes, persistencia tras reinicio y concurrencia |
| Pruebas de mutación | Desactivar la detección de obsolescencia o los reintentos hace fallar 2 pruebas en cada caso |
| Persistencia en PostgreSQL real | Guarda, recupera desde otra instancia y detecta escrituras concurrentes |
| Flujo completo con Llama 3.2 Instruct local | Requerimiento → HU, INVEST, matriz (corregida y completada) y 18 scripts en 2 lotes; el riesgo esperó la aprobación de la HU; estado final `completed` |

## Memoria y asistente de razonamiento

| Verificación | Resultado |
|---|---|
| Pruebas de memoria (`tests/test_memory.py`) | 15 aprobadas. Cubren ventana y resumen, expiración, redacción, deduplicación, retención, `namespace`, persistencia en SQLite, aprendizaje desde aprobaciones y ediciones, uso en la siguiente HU, tolerancia a fallos y seguimiento de sesión en el orquestador |
| Pruebas del asistente (`tests/test_assistant.py`) | 35 aprobadas. Cubren enrutamiento, políticas, herramientas reales, argumentos inválidos, erratas y valores de plantilla, verificación de fidelidad, repetición, caída del modelo, honestidad y API |
| Memoria en PostgreSQL real | Persiste tras reiniciar, redacta, deduplica y recupera |
| Integración en vivo con Llama 3.2 | Chat: pregunta con herramienta, creación de HU y ajuste usando solo la sesión. Flujo: aprendió la HU aprobada y las preferencias del PO, y el siguiente flujo las usó. Una pregunta dentro del flujo se respondió con `estado_flujo`. Orquestador: el seguimiento heredó la intención y la pregunta se resolvió con el agente `assistant` |
| Batería de 13 peticiones fuera del flujo, 2 corridas | 26 de 26 correctas: 22 con la herramienta o skill adecuada y 4 rechazos honestos; ver [Asistente](asistente.md#validación-con-llama-32-instruct) |

## Conductor de la conversación

- **Pruebas:** 16 en `tests/test_conversation.py`, que cubren el recorrido completo por los 11 pasos y cada regla de decisión (ver [Conductor](conversacion.md#validación)).
- **Recorrido en vivo con Llama 3.2** (20 turnos, Compose):
  - "mejora la historia" dos veces generó v2 y v3;
  - una pregunta de QA y un agradecimiento a mitad del flujo lo retomaron;
  - un texto con forma de requerimiento generó una aclaración y "ninguna, sigamos" retomó el flujo;
  - se completaron INVEST, aprobación, matriz (18 casos para 6 criterios, sin tiempos agotados), riesgo, scripts, pipeline, performance y Work Item;
  - "¿qué sigue?" pidió la aprobación en lugar de aprobar.
- **Tiempos observados:**
  - historia, 11 a 21 s;
  - evaluaciones, 7 a 11 s;
  - matriz, unos 18 s por criterio;
  - pasos deterministas (scripts, pipeline, performance, Work Item) y aprobaciones, instantáneos.

## System prompts v2

Evaluación con `evals/prompt_eval.py` contra `llama3.2:3b-instruct-q4_K_M` real. Se comparan los prompts v1 y v2 con los mismos casos; v2 se midió con 3 muestras por caso. Se mide la salida cruda del modelo y, donde el código completa o garantiza una regla, también el resultado final.

| Tarea | Métrica | v1 | v2 |
|---|---|---|---|
| Historia (HU-003B) | De 3 a 6 criterios Dado/Cuando/Entonces y "Como/quiero/para" | 88 % | 100 % |
| INVEST (HU-002) | Resultado final sin hallazgos (tras completar sugerencias faltantes) | 17 % | 100 % |
| INVEST | Sin sugerencias en criterios que cumplen | 17 % | 100 % |
| INVEST | Detecta una HU que no es pequeña ni testeable | 0 % | 100 % |
| Matriz (HU-004) | Sin hallazgos y cobertura completa | 0 % | 100 % |
| Riesgo (HU-005) | Puntuación, justificación y mitigación válidas; una HU enorme no queda en riesgo bajo | 100 % | 100 % |
| Revisión (HU-003A) | Aplica lo aprobado y conserva el resto | 100 % | 100 % |
| Chat | Intención correcta | 80 % | 97 % |
| Chat | Propone dividir un requerimiento amplio (HU-003B, regla 2) | 0 % | 100 % |
| Chat | Tono: saluda, agradece o se disculpa según el caso | 75 % | 100 % |
| Chat | Respuestas no secas | 75 % | 97 % |
| Generación | Claves obligatorias | 0 % | 100 % |
| Asistente | 19 peticiones fuera del flujo, incluidas 6 nuevas | 89 % | 95 % |

Notas:
- **Matriz:** la mejora viene de la lista explícita de casos que el código entrega al modelo. Con esa lista, el prompt v1 también llega a 100 %; sin ella, 0 %.
- **INVEST:** Llama 3.2 omite el campo `suggestion` al evaluar los seis criterios a la vez (0 % en la salida cruda con ambas versiones). El código pide la sugerencia faltante con una tarea acotada (`INVEST_SUGGESTION_SYSTEM`), y el resultado final cumple HU-002 en el 100 % de los casos.
- **Chat:** en el 56 % de los casos de crear o ajustar, el modelo entrega la HU completa directamente (v1: 67 %). En los demás, el código la regenera con `STORY_SYSTEM` y limpia la respuesta, así que la persona siempre recibe la HU.
- **Asistente:** el único caso que falló en v2 (herramienta de automatización móvil) se corrigió después. El código extrae de la petición los valores cerrados, solo envía los datos obligatorios al forzar una herramienta y descarta los borradores escritos antes de consultar. Verificado 3 de 3 contra el modelo.
- **Conversación en vivo** en Compose y kind (saludo, requerimiento, requerimiento amplio, consulta de datos, agradecimiento y petición fuera de dominio): los 6 turnos correctos en ambos entornos. La HU se divide en 5 historias y los datos llegan verificados en 5 a 21 s.
- 158 pruebas unitarias, incluidas `tests/test_business_rules.py` y las regresiones de cada falla real del modelo.

## Resuelto: gates de agentes que comparten fase

Los quality gates se guardaban por fase, y `database` y `automation` comparten la fase `evaluation`, así que el último en ejecutarse sobrescribía el gate del agente de evaluación. Ahora hay un gate por agente, con la fase como campo. Cambio de contrato: la clave del gate de operaciones pasa de `operate` a `operations`. Detalle en [Quality gates](quality-gates.md#en-la-respuesta-del-orquestador).

## Resuelto: mutaciones en PostgreSQL

La política exigía `LIMIT` en `UPDATE`/`DELETE` y PostgreSQL no admite esa sintaxis, así que toda mutación terminaba en `failed` (`ProgrammingError`). Ahora el ejecutor traduce la forma simple a `WHERE ctid IN (SELECT ctid ... LIMIT n FOR UPDATE)`, registra la traducción en la evidencia y bloquea antes de conectar las formas que no puede traducir con seguridad. El límite también se exige ahora en cada `UPDATE`/`DELETE`, no en cualquier parte del script. Detalle en [HU-011](hu010-hu011.md#límite-de-filas-en-postgresql).

Pendiente relacionado: los `INSERT ... VALUES` siguen requiriendo la palabra `LIMIT` en el script, igual que antes y en ambos motores. Es una decisión de política que no se cambió.

## Pendientes de producción

1. **Ejecutar en una suscripción de Azure**: la infraestructura (Bicep), el overlay de AKS y `azure-pipelines.yml` están validados sin conexión, pero no se ejecutaron contra una suscripción real. Falta crear la service connection `valkiria-azure` y el environment `valkiria-qa` en Azure DevOps y hacer el primer despliegue.
2. **Persistencia**: auditoría, métricas, historias, lotes y reportes viven en memoria y se pierden al reiniciar. En Kubernetes, con 2 réplicas, cada pod tiene su propio estado. Falta un almacenamiento durable.
3. **Red privada**: en Azure, PostgreSQL acepta conexiones de servicios de Azure por firewall y el Ingress es HTTP público. Para producción faltan integración con VNet o Private Endpoint, dominio y TLS (certificado en Key Vault). Los secretos ya vienen de Key Vault con Workload Identity.
4. **Capacidad del LLM**: Llama 3.2 3B en CPU responde en segundos por petición y Ollama atiende las peticiones de forma secuencial. Con más usuarios hará falta un nodo con GPU o más réplicas. Sin modelo disponible, los endpoints que dependen del LLM fallan de forma controlada.
5. **Playwright en la imagen**: la imagen no incluye navegadores; `VALKIRIA_AUTOMATION_EXECUTE=true` requiere una imagen con Chromium.
6. **Formato**: `ruff format --check` reformatearía unos 40 archivos, sobre todo líneas largas. No se aplicó para no mezclar un cambio masivo de estilo con correcciones funcionales, y no se exige en el CI.
7. **Memoria y asistente**:
   - Sin `VALKIRIA_WORKFLOW_DATABASE_URL` ni `VALKIRIA_MEMORY_DATABASE_URL`, la memoria vive en el proceso y no se comparte entre réplicas. En Azure usa `valkiria_workflows`.
   - La búsqueda es léxica; para grandes volúmenes o sinónimos conviene búsqueda vectorial (Azure AI Search o pgvector) detrás de `LongTermStore`.
   - Las herramientas de datos consultan solo la app sintética.
8. **Kubernetes**: el manifiesto base se desplegó y verificó en kind (`deploy/kind/`); el overlay de AKS está validado pero no desplegado. Faltan TLS, NetworkPolicy, HPA y PodDisruptionBudget.
