# Valkiria — Plataforma multiagente de QA y LLMOps

[![ci](https://github.com/ABRAHAMCIMAT/qa_valkiria_v1.0/actions/workflows/ci.yml/badge.svg)](https://github.com/ABRAHAMCIMAT/qa_valkiria_v1.0/actions/workflows/ci.yml)

Valkiria transforma solicitudes de QA en artefactos verificables: historias de usuario, evaluación INVEST, matrices de pruebas, análisis de riesgo, automatización, validaciones contra bases sintéticas, evidencias y propuestas de integración.

Incluye un perfil E2E seguro con datos Nissan ficticios, PostgreSQL efímero, aplicación sintética, agentes especializados, Playwright opcional y release en modo preview.

> **Estado (v0.5.0):** funcional para contratos, orquestación, validación y pruebas sintéticas. El CI valida lint, seguridad, pruebas unitarias, E2E contra PostgreSQL, paquete e imagen Docker. Las integraciones productivas requieren adaptadores autorizados, persistencia durable y controles operativos adicionales; consulta [pendientes de producción](docs/estado-validacion.md#pendientes-de-producción).

## Documentación

- [Arquitectura](docs/arquitectura.md)
- [Despliegue: configuración, Docker, Compose, CI y Kubernetes](docs/despliegue.md)
- [Orquestación multiagente](docs/multiagente-orquestacion.md)
- [Flujo de agentes por historia (dependencias, aprobaciones y reanudación)](docs/flujo-historias.md)
- [Memoria de corto y largo plazo](docs/memoria.md)
- [Asistente de razonamiento con herramientas (peticiones fuera del flujo)](docs/asistente.md)
- [System prompts: reglas de negocio, conversación y evaluación](docs/prompts.md)
- [Ciclo de vida LLMOps](docs/llmops-lifecycle.md)
- [Quality gates](docs/quality-gates.md)
- [E2E sintética Nissan](docs/e2e-synthetic.md)
- [HU-010 y HU-011](docs/hu010-hu011.md)
- [Épica e historias INVEST (v3.0)](docs/epica-historias-invest.md)
- [Revisión del Plan de Mejora 2026](docs/revision-historias-2026.md)
- [Matriz de pruebas](docs/matriz-pruebas.md)
- [Observabilidad y errores](docs/observabilidad-y-errores.md)
- [Seguridad](docs/security.md)
- [Integraciones](docs/integrations.md)
- [Patrones y mantenibilidad](docs/patrones-y-mantenibilidad.md)
- [Proceso de desarrollo](docs/proceso-desarrollo.md)
- [Revisión de calidad](docs/revision-calidad.md)
- [Estado de validación y pendientes de producción](docs/estado-validacion.md)
- [Historial de cambios](CHANGELOG.md)

## Flujo funcional

```text
Frontend conversacional
        ↓
API FastAPI
        ↓
Memoria corta (sesión) + memoria larga (conocimiento validado)
        ↓
Intake → Grounding ─┬─ trabajo del flujo → Generation → Evaluation
                    └─ pregunta o petición fuera del flujo → Assistant (herramientas y skills)
        ↓                         ↘
 Database                     Automation
        ↓                         ↓
 Approval → Release → Operations → Auditoría y evidencia
```

Las peticiones que no siguen el flujo programado se razonan con herramientas y skills reales. Si Valkiria no tiene la capacidad, lo dice y detalla lo que sí puede hacer; ver [Asistente](docs/asistente.md). Las HU aprobadas, las decisiones del PO y las correcciones humanas se recuerdan para las siguientes historias; ver [Memoria](docs/memoria.md).

El punto de entrada multiagente es:

```http
POST /v1/agent/execute
```

```json
{
  "request": "Generar una historia para consultar vehículos Nissan, evaluarla con INVEST, validar contra una base sintética y preparar una vista previa de Pull Request"
}
```

La respuesta conserva `request_id`, `trace_id`, plan de agentes, artefactos, decisiones, quality gates (uno por agente, con su fase; ver [Quality gates](docs/quality-gates.md#en-la-respuesta-del-orquestador)) y errores sanitizados.

## Endpoints

| Método | Ruta | Uso |
|---|---|---|
| GET | `/health` | Estado, modo, agentes y políticas activas |
| GET | `/` | Frontend conversacional (mismo origen que la API) |
| POST | `/v1/agent/execute` | Orquestación multiagente (acepta `session_id` para seguimientos) |
| POST | `/v1/chat` | Conversación: construye la HU o responde preguntas con herramientas; mantiene la sesión |
| POST | `/v1/assistant/ask` | Pregunta o petición fuera del flujo, resuelta con herramientas y skills |
| GET | `/v1/assistant/capabilities` | Catálogo de herramientas y skills, límites y capacidades no disponibles |
| GET, DELETE | `/v1/memory/sessions/{id}` | Consultar o borrar la memoria de una sesión |
| GET, POST | `/v1/memory/long-term` | Buscar recuerdos o registrar un hecho del dominio |
| DELETE | `/v1/memory/long-term/{id}` | Olvidar un recuerdo |
| POST | `/v1/stories` | Crear historia a partir de un requerimiento |
| POST | `/v1/stories/{id}/invest` | Evaluación INVEST |
| POST | `/v1/stories/{id}/test-matrix` | Matriz de casos |
| POST | `/v1/stories/{id}/risk` | Análisis de riesgo |
| POST | `/v1/test-cases/export` | Exportar casos manuales a `.xlsx` |
| POST | `/v1/automation/tools/select` | Plataforma, lenguaje y herramienta compatibles |
| POST | `/v1/automation/batches/generate` | Generar lote de scripts (máx. 15 casos) |
| GET | `/v1/automation/batches/{id}` | Consultar lote |
| POST | `/v1/automation/batches/{id}/execute` | Ejecutar lote (requiere runner habilitado) |
| POST | `/v1/database/tools/suggest` | Sugerir herramienta de pruebas de BD |
| POST | `/v1/database/scripts/execute` | Ejecutar script contra la base sintética |
| GET | `/v1/database/executions/{id}` | Consultar ejecución |
| GET | `/v1/reports/{id}` | Consultar reporte de evidencia |
| GET | `/v1/reports/{id}/download` | Descargar reporte (PDF) |
| GET | `/v1/audit`, `/v1/metrics` | Auditoría y métricas en memoria |
| POST | `/v1/workflows` | Flujo por historia: planifica y ejecuta las HU solicitadas respetando dependencias |
| GET | `/v1/workflows/{id}` | Estado, plan razonado, próximas acciones y artefactos versionados |
| POST | `/v1/workflows/{id}/requests` | Continuar el flujo con nuevas tareas o datos |
| POST | `/v1/workflows/{id}/approvals` | Aprobar o rechazar una versión exacta (HU, INVEST con sugerencias, matriz, Work Item) |
| PUT | `/v1/workflows/{id}/artifacts/{story\|matrix}` | Edición humana con nueva versión |
| POST | `/v1/workflows/{id}/resume` | Reintentar pasos fallidos |
| GET | `/v1/workflows/capabilities` | Grafo de dependencias entre HU |

## Agentes especializados

- `IntakeAgent`: clasifica intención y alcance.
- `GroundingAgent`: aplica políticas y detecta ambigüedades.
- `GenerationAgent`: genera borradores o JSON validable mediante el LLM.
- `EvaluationAgent`: revisa INVEST, cobertura, riesgo y análisis estático.
- `AssistantAgent`: resuelve preguntas y peticiones fuera del flujo razonando con herramientas y skills; declara con honestidad lo que no puede hacer.
- `DatabaseAgent`: ejecuta consultas contra un perfil sintético autorizado.
- `AutomationAgent`: prepara o ejecuta casos Playwright.
- `ApprovalAgent`: solicita aprobación humana de la versión exacta.
- `ReleaseAgent`: prepara preview o PR y bloquea commits directos.
- `OperationsAgent`: consolida auditoría, métricas y evidencia.

## Estructura del repositorio

```text
src/valkiria/          Código: api, agents, application, domain, infrastructure, providers, adapters, synthetic_app
tests/                 Unitarias, conftest.py y tests/e2e (E2E sintética)
frontend/              Interfaz conversacional servida por la API en "/"
synthetic_db/          Migración de esquema y datos Nissan ficticios
deploy/docker/         Dockerfile multi-etapa
deploy/kubernetes/     ConfigMap, Deployment y Service (base de kustomize)
deploy/kind/           Overlay y scripts para probar en Kubernetes local (kind)
deploy/azure/          Bicep (infraestructura), overlay de AKS y deploy.sh para Azure
docs/                  Documentación en español
.github/workflows/     CI en GitHub Actions
azure-pipelines.yml    CI/CD en Azure DevOps: validación, E2E, imagen en ACR y despliegue en AKS
docker-compose.synthetic.yml   Entorno de desarrollo completo
.env.example           Plantilla única de configuración
```

## Configuración

`.env.example` es la única plantilla y coincide campo por campo con `Settings` (una prueba lo verifica). La configuración se carga con pydantic-settings: las variables de entorno tienen prioridad sobre `.env`, y los secretos (`VALKIRIA_LLM_API_KEY`, `VALKIRIA_SYNTHETIC_DATABASE_URL`) se manejan como `SecretStr`. Perfil seguro por defecto:

```text
VALKIRIA_MODE=synthetic
VALKIRIA_ENVIRONMENT=qa
VALKIRIA_ALLOW_PRODUCTION=false
VALKIRIA_DB_PROFILE=synthetic_postgresql
VALKIRIA_DB_ENGINE=postgresql
VALKIRIA_AUTOMATION_RUNNER=playwright
VALKIRIA_AUTOMATION_EXECUTE=false
VALKIRIA_AUTOMATION_HEADLESS=true
VALKIRIA_RELEASE_MODE=preview
VALKIRIA_DIRECT_COMMIT=false
VALKIRIA_PR_REQUIRED=true
VALKIRIA_LLM_BASE_URL=http://localhost:11434/v1
VALKIRIA_LLM_MODEL=llama3.2:3b-instruct-q4_K_M
VALKIRIA_MEMORY_ENABLED=true
```

Playwright real solo se habilita explícitamente con `VALKIRIA_AUTOMATION_EXECUTE=true`. `VALKIRIA_ENVIRONMENT=production` no arranca sin `VALKIRIA_ALLOW_PRODUCTION=true`. Detalle completo en [Despliegue](docs/despliegue.md#configuración).

## Inicio local

Requisitos: Python 3.11 o superior (el CI y la imagen usan 3.12) y Docker.

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev,synthetic,e2e]'
cp .env.example .env
docker compose -f docker-compose.synthetic.yml up -d --wait postgres
python -m playwright install chromium
```

Para arrancar la aplicación Nissan sintética y la API:

```bash
uvicorn valkiria.synthetic_app.app:create_synthetic_app --factory --port 8090
uvicorn valkiria.api.app:create_app --factory --port 8000
```

Para Ollama local:

```bash
ollama serve
ollama pull llama3.2:3b-instruct-q4_K_M
```

## Entorno Docker completo

Levanta PostgreSQL sintético, la aplicación Nissan sintética y la API, que también sirve el frontend en `http://localhost:8000/`:

```bash
docker compose -f docker-compose.synthetic.yml up -d --build --wait
curl http://localhost:8000/health
docker compose -f docker-compose.synthetic.yml down -v   # apagar y borrar datos
```

La imagen se construye desde `deploy/docker/Dockerfile`, corre como usuario no root (`uid 10001`) con sistema de archivos de solo lectura e incluye healthcheck. Dentro de Compose, la API usa el LLM del host (`host.docker.internal:11434`); para usar el contenedor de Ollama añade `--profile llm` y `VALKIRIA_DOCKER_LLM_BASE_URL=http://ollama:11434/v1`. Kubernetes y CI se describen en [Despliegue](docs/despliegue.md).

## Despliegue en Azure

Azure es la única plataforma de despliegue y el modelo es **Llama 3.2 Instruct**, autoalojado en AKS con Ollama.

```bash
az group create -n rg-valkiria-qa -l eastus2
export VALKIRIA_PG_ADMIN_PASSWORD='<contraseña-fuerte>'
az deployment group create -g rg-valkiria-qa -n main -f deploy/azure/bicep/main.bicep -p deploy/azure/bicep/main.bicepparam
az acr build -r <acrName> -t valkiria:0.5.0 -f deploy/docker/Dockerfile .
RESOURCE_GROUP=rg-valkiria-qa IMAGE_TAG=0.5.0 deploy/azure/deploy.sh
```

Bicep crea ACR, AKS (Workload Identity, Key Vault CSI y app routing), Key Vault, PostgreSQL Flexible Server y Log Analytics. En Azure DevOps, `azure-pipelines.yml` automatiza validación, E2E, imagen y despliegue con aprobación manual. Detalle en [Despliegue](docs/despliegue.md#azure-plataforma-de-destino).

## Kubernetes local para pruebas

```bash
deploy/kind/up.sh     # clúster kind con API, app sintética y PostgreSQL → http://localhost:8080
deploy/kind/down.sh   # eliminarlo
```

Reutiliza el manifiesto de `deploy/kubernetes/`. Requiere Docker, `kind` y `kubectl`; con `ollama serve` corriendo, el orquestador usa el LLM local. Detalle en [Despliegue](docs/despliegue.md#kubernetes-local-para-pruebas-kind).

## Validaciones

```bash
pytest -q                                   # unitarias (la E2E se omite sin RUN_SYNTHETIC_E2E)
RUN_SYNTHETIC_E2E=1 pytest -v tests/e2e     # E2E; requiere PostgreSQL sintético levantado
ruff check src tests
bandit -q -c pyproject.toml -r src
python -m build                             # wheel y sdist en dist/
```

El CI (`.github/workflows/ci.yml`) ejecuta Ruff, Bandit, unitarias, E2E contra PostgreSQL, construcción del paquete y de la imagen Docker, y prueba `/health` del entorno Compose.

Las pruebas sintéticas no usan datos reales. La URL de PostgreSQL se obtiene únicamente desde `VALKIRIA_SYNTHETIC_DATABASE_URL`; nunca se acepta dentro de un request.

## Seguridad y límites

- No se aceptan contraseñas, tokens ni DSN en prompts o requests.
- `DROP`, `TRUNCATE`, privilegios y comandos del sistema se bloquean.
- Las mutaciones requieren transacción, `WHERE`, límite en cada `UPDATE`/`DELETE` y rollback. PostgreSQL no admite `UPDATE ... LIMIT`, así que `UPDATE`/`DELETE ... WHERE cond LIMIT n` se traduce en PostgreSQL a `... WHERE ctid IN (SELECT ctid FROM t WHERE cond LIMIT n FOR UPDATE)`; las formas ambiguas se bloquean antes de conectar. Ver [HU-011](docs/hu010-hu011.md#límite-de-filas-en-postgresql).
- No existe commit directo automático a `main`.
- La persistencia predeterminada es temporal (en memoria) para desarrollo.
- PostgreSQL sintético es el adaptador principal; MySQL puede incorporarse como matriz adicional.
- SQL Server y Oracle quedan condicionados por imagen, licencia y runner.
- La aplicación sintética y Playwright son instrumentos de prueba, no sistemas productivos.
