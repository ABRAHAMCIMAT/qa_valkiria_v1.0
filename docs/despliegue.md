# Despliegue: configuración, Docker, Compose, CI y Kubernetes

## Configuración

`src/valkiria/infrastructure/settings.py` define `Settings` con **pydantic-settings**. Todas las variables usan el prefijo `VALKIRIA_` y se leen al instanciar `Settings()`, no al importar el módulo.

Precedencia, de mayor a menor:

1. Variables de entorno.
2. `.env` del directorio de trabajo actual.
3. `.env` de la raíz del repositorio (solo cuando se ejecuta desde el código fuente).
4. Valores por defecto seguros (modo `synthetic`, producción bloqueada, release en preview).

Reglas:

- `.env.example` es la **única plantilla**. Una prueba (`tests/test_settings.py`) verifica que sus claves coinciden exactamente con los campos de `Settings`.
- `VALKIRIA_LLM_API_KEY` y `VALKIRIA_SYNTHETIC_DATABASE_URL` son `SecretStr`: no aparecen en `repr` ni en logs. El código los lee con `settings.secret("<campo>")`. Un valor vacío equivale a no configurado.
- `VALKIRIA_ALLOWED_ORIGINS` acepta una lista separada por comas.
- `VALKIRIA_ENVIRONMENT=production` falla al iniciar si no se define también `VALKIRIA_ALLOW_PRODUCTION=true`.
- `VALKIRIA_FRONTEND_DIR` indica la carpeta del frontend. Si está vacía, se usa `frontend/` del repositorio; la imagen Docker la fija en `/app/frontend`.
- Las pruebas unitarias ignoran el `.env` local (`tests/conftest.py`); la E2E sí puede usarlo.

## Imagen Docker

Archivo: `deploy/docker/Dockerfile`. Se construye desde la raíz del repositorio:

```bash
docker build -f deploy/docker/Dockerfile -t valkiria:0.5.0 .
```

| Aspecto | Implementación |
|---|---|
| Etapas | `builder` genera el wheel con `python -m build`; la imagen final solo lo instala |
| Base | `python:3.12-slim` |
| Dependencias | Paquete `valkiria` con el extra `postgres` (driver psycopg) |
| Frontend | Copiado en `/app/frontend`, propiedad de root y de solo lectura |
| Usuario | `app`, `uid 10001`, sin shell |
| Healthcheck | `GET /health` cada 15 s con Python estándar (sin curl) |
| Contexto | `.dockerignore` excluye `.venv`, `.env`, `.git`, pruebas y documentación |
| Tamaño | Unos 300 MB |

La imagen no incluye navegadores de Playwright; `VALKIRIA_AUTOMATION_EXECUTE=true` requiere una imagen derivada con Chromium.

## Entorno de desarrollo con Docker Compose

Archivo único: `docker-compose.synthetic.yml`.

```bash
docker compose -f docker-compose.synthetic.yml up -d --build --wait   # todo
docker compose -f docker-compose.synthetic.yml up -d --wait postgres  # solo PostgreSQL
docker compose -f docker-compose.synthetic.yml down -v                # apagar y borrar datos
```

| Servicio | Puerto (solo `127.0.0.1`) | Descripción |
|---|---|---|
| `postgres` | 5432 | PostgreSQL 16 con healthcheck; al crear el volumen aplica `V1__create_schema.sql` y `V2__seed_nissan_data.sql` |
| `synthetic-app` | 8090 | Aplicación Nissan sintética (misma imagen, otro comando) |
| `api` | 8000 | API de Valkiria y frontend en `/` |
| `ollama` | 11434 | Opcional, perfil `llm` |

Detalles:

- `api` y `synthetic-app` arrancan solo cuando sus dependencias están *healthy*.
- Las URLs internas (`postgres:5432`, `synthetic-app:8090`) se fijan en Compose y sustituyen a las de `localhost` que traiga `.env`. El `.env` es opcional.
- Por defecto la API usa el LLM del host (`host.docker.internal:11434`). Para usar el contenedor de Ollama: `VALKIRIA_DOCKER_LLM_BASE_URL=http://ollama:11434/v1 docker compose -f docker-compose.synthetic.yml --profile llm up -d --build --wait`.
- Endurecimiento: `read_only`, `/tmp` en tmpfs, `cap_drop: [ALL]` y `no-new-privileges`.

Verificación rápida:

```bash
curl http://localhost:8000/health
curl http://localhost:8090/health
open http://localhost:8000/
```

## Integración continua

Archivo: `.github/workflows/ci.yml`. Corre en `push` a `main`, en Pull Requests y manualmente (`workflow_dispatch`). Usa `ubuntu-24.04`, Python 3.12 y acciones con Node 24 (`actions/checkout@v7`, `actions/setup-python@v7`, `actions/upload-artifact@v7`).

| Trabajo | Qué hace |
|---|---|
| `lint` — Ruff y Bandit | `ruff check src tests` y `bandit -q -c pyproject.toml -r src` |
| `test` — Unitarias y E2E sintética | PostgreSQL 16 como servicio; aplica migraciones y datos con `psql`; unitarias; E2E con `RUN_SYNTHETIC_E2E=1` |
| `package` — Wheel y sdist | `python -m build`; instala el wheel en un entorno limpio, arranca la app y publica `dist/` como artefacto (7 días) |
| `docker` — Imagen y entorno | Tras `lint` y `test`: construye la imagen, verifica `uid 10001`, levanta Compose y prueba `/health` de la API y de la app sintética y `/` |

Para actualizar el runner a Ubuntu 26, cambia `runs-on: ubuntu-24.04` en los cuatro trabajos.

Subir cambios en `.github/workflows/` requiere un token con el permiso `workflow` (`gh auth refresh -h github.com -s workflow`).

## Kubernetes

Archivo: `deploy/kubernetes/deployment.yaml` (validado con `kubeconform -strict`).

- **ConfigMap `valkiria-config`**: perfil sintético y políticas de release.
- **Secret `valkiria-secrets`** (opcional, no incluido): `VALKIRIA_SYNTHETIC_DATABASE_URL` y `VALKIRIA_LLM_API_KEY`.
- **Deployment `valkiria-api`**: 2 réplicas, imagen `ghcr.io/abrahamcimat/qa_valkiria_v1.0:0.5.0`, `runAsUser 10001`, seccomp `RuntimeDefault`, sistema de archivos de solo lectura, sin capacidades, `/tmp` como `emptyDir`, sondas de disponibilidad y de vida en `/health`, y sin token de service account.
- **Service `valkiria-api`**: puerto 80 hacia el 8000 del contenedor.

Antes de aplicar, publica la imagen:

```bash
docker build -f deploy/docker/Dockerfile -t ghcr.io/abrahamcimat/qa_valkiria_v1.0:0.5.0 .
docker push ghcr.io/abrahamcimat/qa_valkiria_v1.0:0.5.0
kubectl apply -f deploy/kubernetes/deployment.yaml
```

Pendiente: el CI aún no publica la imagen, y el estado en memoria no se comparte entre réplicas. Consulta [Estado de validación](estado-validacion.md).

## Kubernetes local para pruebas (kind)

`deploy/kind/` despliega el entorno completo en un clúster [kind](https://kind.sigs.k8s.io/) dentro de Docker, reutilizando el manifiesto de `deploy/kubernetes/` como base de kustomize.

```bash
deploy/kind/up.sh     # crea el clúster, construye y carga la imagen, aplica y espera (≈75 s desde cero)
open http://localhost:8080
deploy/kind/down.sh   # elimina el clúster y todo lo desplegado
```

Requisitos: Docker, `kind` y `kubectl` (`brew install kind kubectl`). Puedes volver a ejecutar `up.sh` después de cambiar el código: reconstruye la imagen y reinicia los pods.

| Recurso (namespace `valkiria-local`) | Origen | Notas |
|---|---|---|
| `valkiria-api` | `deploy/kubernetes/deployment.yaml` | Parche local: 1 réplica y Service `NodePort` 30080, publicado en `127.0.0.1:8080` |
| `synthetic-app` | `deploy/kind/synthetic-app.yaml` | Misma imagen; un `initContainer` espera a PostgreSQL |
| `postgres` | `deploy/kind/postgres.yaml` | PostgreSQL 16 con migración y datos sintéticos (ConfigMap `synthetic-db-init`); datos en `emptyDir` |
| `valkiria-secrets` | `deploy/kind/kustomization.yaml` | URL del PostgreSQL efímero |

El parche `deploy/kind/api-patch.yaml` apunta el LLM a Ollama en la Mac anfitriona (`http://host.docker.internal:11434/v1`). Si Ollama no está corriendo (`ollama serve`), todo funciona salvo la generación con LLM: `/v1/agent/execute` responde `failed` en la fase de generación, de forma controlada.

Verificado: `/health` y el frontend en 200; consultas y mutaciones con `LIMIT` contra PostgreSQL; `DROP TABLE` bloqueado; orquestador completo con `qwen2.5:7b`; pods con `uid 10001`, sistema de archivos de solo lectura y 0 reinicios.

## Nube

`deploy/terraform/README.md` describe los destinos recomendados por proveedor. El repositorio no contiene credenciales ni recursos de infraestructura irreversibles.
