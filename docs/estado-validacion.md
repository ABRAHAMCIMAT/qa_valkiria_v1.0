# Estado de validación y pendientes de producción

Última actualización: 2026-09-28 (commit `fe7efd5`).

## CI en GitHub Actions

El pipeline `.github/workflows/ci.yml` pasa en `main` con sus cuatro trabajos: Ruff y Bandit; unitarias y E2E sintética; wheel y sdist; imagen Docker y entorno de desarrollo. Usa `ubuntu-24.04` y acciones con Node 24, sin avisos de obsolescencia. Historial: https://github.com/ABRAHAMCIMAT/qa_valkiria_v1.0/actions/workflows/ci.yml

## Validación local

Ejecutada en macOS con Python 3.12.2 y Docker 29.8.1, con el código ya integrado con el commit de HU-010/HU-011.

| Verificación | Comando | Resultado |
|---|---|---|
| Docker daemon | `docker info` | Activo (Docker Desktop 29.8.1) |
| Ruff | `ruff check src tests` | Sin errores |
| Bandit | `bandit -q -c pyproject.toml -r src` | Sin hallazgos |
| Unitarias | `pytest -q --ignore=tests/e2e` | 39 aprobadas (aisladas del `.env` local mediante `tests/conftest.py`) |
| E2E sintética | `RUN_SYNTHETIC_E2E=1 pytest -v tests/e2e` | 1 aprobada contra PostgreSQL 16 (repetible) |
| Paquete | `python -m build` | `valkiria-0.5.0-py3-none-any.whl` y `valkiria-0.5.0.tar.gz`; el wheel se instala y arranca en un entorno limpio |
| Imagen Docker | `docker build -f deploy/docker/Dockerfile .` | 303 MB, usuario `uid=10001`, healthcheck activo |
| Entorno Compose | `docker compose -f docker-compose.synthetic.yml up -d --build --wait` | `postgres`, `synthetic-app` y `api` en estado *healthy* |
| `/health` | `curl localhost:8000/health`, `curl localhost:8090/health` | 200 en ambos; frontend `/` y `/assets` en 200 |
| Consulta vía API a PostgreSQL | `POST /v1/database/scripts/execute` (SELECT) | `completed`, devuelve Sentra y Versa |
| Workflow de CI | `actionlint .github/workflows/ci.yml` | Válido |
| Kubernetes | `kubeconform -strict deploy/kubernetes/deployment.yaml` | 3 recursos válidos |

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

## Pendientes de producción

1. **Mutaciones en PostgreSQL**: la política estática exige `LIMIT` en `UPDATE` y `DELETE`, pero PostgreSQL no admite `UPDATE ... LIMIT`. Por eso toda mutación contra PostgreSQL termina en `failed` (`ProgrammingError`); solo funciona en SQLite. Hay que adaptar la regla por motor (por ejemplo, `WHERE ctid IN (SELECT ctid ... LIMIT n)`) y cubrirla con una prueba E2E.
2. **Publicar la imagen**: el CI la construye pero no la publica. Falta un trabajo que haga push a GHCR con etiqueta por versión y digest, y referenciar el digest en Kubernetes.
3. **Persistencia**: auditoría, métricas, historias, lotes y reportes viven en memoria y se pierden al reiniciar. En Kubernetes, con 2 réplicas, cada pod tiene su propio estado. Falta un almacenamiento durable.
4. **Secretos**: `VALKIRIA_LLM_API_KEY` y la URL de base de datos deben venir de un gestor de secretos (Secret de Kubernetes o proveedor externo). La contraseña `valkiria_synthetic_only` es exclusiva del entorno efímero.
5. **LLM en contenedores**: Compose usa el Ollama del host o el perfil `llm`. Sin un modelo disponible, los endpoints que dependen del LLM responden 503 (error controlado).
6. **Playwright en la imagen**: la imagen no incluye navegadores; `VALKIRIA_AUTOMATION_EXECUTE=true` requiere una imagen con Chromium.
7. **Formato**: `ruff format --check` reformatearía unos 40 archivos, sobre todo líneas largas. No se aplicó para no mezclar un cambio masivo de estilo con correcciones funcionales, y no se exige en el CI.
8. **Kubernetes**: el manifiesto se validó con `kubeconform`, pero no se desplegó en un clúster. Faltan Ingress/TLS, NetworkPolicy, HPA y PodDisruptionBudget según la plataforma de destino.
