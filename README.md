# Valkiria — Plataforma multiagente de QA y LLMOps

[![ci](https://github.com/ABRAHAMCIMAT/qa_valkiria_v1.0/actions/workflows/ci.yml/badge.svg)](https://github.com/ABRAHAMCIMAT/qa_valkiria_v1.0/actions/workflows/ci.yml)

Valkiria transforma solicitudes de QA en artefactos verificables: historias de usuario, evaluación INVEST, matrices de pruebas, análisis de riesgo, automatización, validaciones contra bases sintéticas, evidencias y propuestas de integración.

Incluye un perfil E2E seguro con datos Nissan ficticios, PostgreSQL efímero, aplicación sintética, agentes especializados, Playwright opcional y release en modo preview.

> **Estado (v0.5.0):** funcional para contratos, orquestación, validación y pruebas sintéticas. El CI valida lint, seguridad, pruebas unitarias, E2E contra PostgreSQL, paquete e imagen Docker. Las integraciones productivas requieren adaptadores autorizados, persistencia durable y controles operativos adicionales; consulta [pendientes de producción](docs/estado-validacion.md#pendientes-de-producción).

## Documentación

- [Arquitectura](docs/arquitectura.md)
- [Despliegue: configuración, Docker, Compose, CI y Kubernetes](docs/despliegue.md)
- [Orquestación multiagente](docs/multiagente-orquestacion.md)
- [Ciclo de vida LLMOps](docs/llmops-lifecycle.md)
- [Quality gates](docs/quality-gates.md)
- [E2E sintética Nissan](docs/e2e-synthetic.md)
- [HU-010 y HU-011](docs/hu010-hu011.md)
- [Épica e historias INVEST](docs/epica-historias-invest.md)
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
Intake → Grounding → Generation → Evaluation
        ↓                         ↘
 Database                     Automation
        ↓                         ↓
 Approval → Release → Operations → Auditoría y evidencia
```

El punto de entrada multiagente es:

```http
POST /v1/agent/execute
```

```json
{
  "request": "Generar una historia para consultar vehículos Nissan, evaluarla con INVEST, validar contra una base sintética y preparar una vista previa de Pull Request"
}
```

La respuesta conserva `request_id`, `trace_id`, plan de agentes, artefactos, decisiones, quality gates y errores sanitizados.

## Endpoints

| Método | Ruta | Uso |
|---|---|---|
| GET | `/health` | Estado, modo, agentes y políticas activas |
| GET | `/` | Frontend conversacional (mismo origen que la API) |
| POST | `/v1/agent/execute` | Orquestación multiagente |
| POST | `/v1/chat` | Conversación para construir una historia |
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

## Agentes especializados

- `IntakeAgent`: clasifica intención y alcance.
- `GroundingAgent`: aplica políticas y detecta ambigüedades.
- `GenerationAgent`: genera borradores o JSON validable mediante el LLM.
- `EvaluationAgent`: revisa INVEST, cobertura, riesgo y análisis estático.
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
deploy/kubernetes/     ConfigMap, Deployment y Service
deploy/terraform/      Guía de destinos en la nube
docs/                  Documentación en español
.github/workflows/     Pipeline de CI
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
VALKIRIA_LLM_MODEL=qwen2.5:7b
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
ollama pull qwen2.5:7b
```

## Entorno Docker completo

Levanta PostgreSQL sintético, la aplicación Nissan sintética y la API, que también sirve el frontend en `http://localhost:8000/`:

```bash
docker compose -f docker-compose.synthetic.yml up -d --build --wait
curl http://localhost:8000/health
docker compose -f docker-compose.synthetic.yml down -v   # apagar y borrar datos
```

La imagen se construye desde `deploy/docker/Dockerfile`, corre como usuario no root (`uid 10001`) con sistema de archivos de solo lectura e incluye healthcheck. Dentro de Compose, la API usa el LLM del host (`host.docker.internal:11434`); para usar el contenedor de Ollama añade `--profile llm` y `VALKIRIA_DOCKER_LLM_BASE_URL=http://ollama:11434/v1`. Kubernetes y CI se describen en [Despliegue](docs/despliegue.md).

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
