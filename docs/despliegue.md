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

Verificado: `/health` y el frontend en 200; consultas y mutaciones con `LIMIT` contra PostgreSQL; `DROP TABLE` bloqueado; orquestador completo con `llama3.2:3b-instruct-q4_K_M`; pods con `uid 10001`, sistema de archivos de solo lectura y 0 reinicios.

## Azure (plataforma de destino)

Azure es la única plataforma de despliegue. Todo vive en `deploy/azure/` y `azure-pipelines.yml`.

```text
Azure DevOps (azure-pipelines.yml)
  Validate → E2E → Build (az acr build) → DeployQA (aprobación en environment "valkiria-qa")
                                   │                     │
                                   ▼                     ▼
                     Azure Container Registry ──► AKS (namespace valkiria)
                                                   ├─ valkiria-api      ← Ingress (app routing)
                                                   ├─ synthetic-app     ← migra la BD si está vacía
                                                   └─ ollama (Llama 3.2 Instruct, volumen persistente)
                                                        │ Workload Identity
                                   Azure Key Vault ◄────┘  (Secret Store CSI → Secret valkiria-secrets)
                                   Azure Database for PostgreSQL Flexible Server (base sintética)
                                   Log Analytics (Container Insights)
```

### 1. Infraestructura (Bicep)

`deploy/azure/bicep/main.bicep` crea, en un grupo de recursos:

| Recurso | Configuración |
|---|---|
| Log Analytics | 30 días de retención; recibe Container Insights de AKS |
| Azure Container Registry | SKU Standard, sin usuario administrador; AKS tiene rol `AcrPull` |
| AKS | Azure CNI overlay, network policy de Azure, OIDC issuer, Workload Identity, Key Vault Secrets Provider con rotación, app routing (NGINX administrado); 2 nodos `Standard_D4s_v5` (Llama 3.2 3B en CPU necesita memoria) |
| Identidad administrada de la API | Credencial federada para `system:serviceaccount:valkiria:valkiria-api`; rol `Key Vault Secrets User` |
| Key Vault | RBAC, soft delete y purge protection; secreto `synthetic-database-url` |
| PostgreSQL Flexible Server 16 | Burstable B1ms, base `nissan_synthetic`, TLS obligatorio (`sslmode=require`) |

```bash
az group create -n rg-valkiria-qa -l eastus2
export VALKIRIA_PG_ADMIN_PASSWORD='<contraseña-fuerte>'   # nunca se guarda en el repositorio
az deployment group create -g rg-valkiria-qa -n main \
  -f deploy/azure/bicep/main.bicep -p deploy/azure/bicep/main.bicepparam
```

Salidas que usan el pipeline y `deploy.sh`: `acrName`, `acrLoginServer`, `aksName`, `keyVaultName`, `tenantId`, `workloadIdentityClientId` y `postgresFqdn`.

### 2. Manifiestos de AKS

`deploy/azure/aks/` es un overlay de kustomize sobre `deploy/kubernetes/`:

- **Imagen** desde ACR (`<acr>.azurecr.io/valkiria:<tag>`).
- **ServiceAccount `valkiria-api`** con Workload Identity. `SecretProviderClass` lee `synthetic-database-url` de Key Vault y lo sincroniza como `VALKIRIA_SYNTHETIC_DATABASE_URL` en el Secret `valkiria-secrets`.
- **Ollama** (`ollama/ollama:0.32.11`) descarga `llama3.2:3b-instruct-q4_K_M` una sola vez a un disco persistente de 20 GiB. Queda listo cuando el modelo está disponible.
- **synthetic-app**: un `initContainer` aplica la migración y los datos sintéticos solo si la base está vacía.
- **Ingress** con la clase `webapprouting.kubernetes.azure.com`.

Los valores `__ACR_LOGIN_SERVER__`, `__IMAGE_TAG__`, `__KEYVAULT_NAME__`, `__TENANT_ID__` y `__WORKLOAD_CLIENT_ID__` los reemplaza `deploy/azure/deploy.sh` con las salidas de Bicep; el script falla si queda alguno sin reemplazar.

```bash
az acr build -r <acrName> -t valkiria:0.5.0 -f deploy/docker/Dockerfile .
RESOURCE_GROUP=rg-valkiria-qa IMAGE_TAG=0.5.0 deploy/azure/deploy.sh
```

### 3. Pipeline de Azure DevOps

`azure-pipelines.yml` (validado contra el esquema oficial de Azure Pipelines):

| Etapa | Qué hace |
|---|---|
| `Validate` | Ruff, Bandit y pruebas unitarias; publica resultados JUnit |
| `E2E` | PostgreSQL 16 como contenedor de servicio, migraciones y E2E sintética |
| `Build` | Solo en `main`: wheel y sdist como artefacto; `az acr build` publica `valkiria:0.5.0-<BuildId>` en ACR |
| `DeployQA` | Environment `valkiria-qa` (aprobación manual): `deploy/azure/deploy.sh` y prueba de `/health` por el Ingress |

Requisitos en Azure DevOps: una service connection de Azure Resource Manager con Workload Identity federation llamada `valkiria-azure` y un environment `valkiria-qa` con aprobación. El pipeline no usa contraseñas: la de PostgreSQL solo se usa al crear la infraestructura y queda en Key Vault.

GitHub Actions (`.github/workflows/ci.yml`) sigue validando cada push en GitHub; el despliegue a Azure lo hace Azure DevOps.

### Modelo LLM

El modelo es **Llama 3.2 Instruct** (`llama3.2:3b-instruct-q4_K_M`) en todos los entornos: Ollama local, Compose, kind y AKS. Para usar un endpoint administrado compatible con OpenAI (por ejemplo, un deployment de Azure AI con llave), configura `VALKIRIA_LLM_BASE_URL`, `VALKIRIA_LLM_MODEL`, `VALKIRIA_LLM_API_KEY` y `VALKIRIA_LLM_AUTH_HEADER=api-key`.
