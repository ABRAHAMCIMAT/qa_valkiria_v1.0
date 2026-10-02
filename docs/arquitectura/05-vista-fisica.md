# Vista física

[← Índice de arquitectura](../arquitectura.md)

La vista física describe dónde se ejecuta cada componente, cómo se comunican los nodos y cómo se protegen. Valkiria se despliega en tres topologías con la **misma imagen**: desarrollo con Docker Compose, Kubernetes local con kind y QA en Azure (AKS). Azure es la única plataforma de destino.

| Topología | Uso | API | LLM | Base sintética | Flujos y memoria | Chromium |
|---|---|---|---|---|---|---|
| Docker Compose | Desarrollo local | 1 contenedor, imagen `-browser` | Ollama del host o perfil `llm` | PostgreSQL 16 en contenedor | En memoria | Sí (`AUTOMATION_EXECUTE=true`) |
| kind | Probar manifiestos de Kubernetes | 1 pod | Ollama del host | PostgreSQL 16 en pod (`emptyDir`) | En memoria | Sí (imagen `-browser`) |
| AKS (QA) | Entorno compartido en Azure | 2 réplicas | Ollama en el clúster | Azure Database for PostgreSQL Flexible Server | PostgreSQL (`valkiria_workflows`) | No (`AUTOMATION_EXECUTE=false`) |

## Despliegue en Azure (AKS)

### Diagrama de despliegue

```mermaid
flowchart TB
    USER(["Navegador del usuario"])
    ADOAG(["Agente de Azure Pipelines"])

    subgraph AZ["Suscripción de Azure · grupo de recursos rg-valkiria-qa"]
        LA[("Log Analytics<br/>30 días · Container Insights")]
        ACR[("Azure Container Registry<br/>Standard · sin admin")]
        KV[("Key Vault · RBAC<br/>soft delete + purge protection<br/>synthetic-database-url<br/>workflow-database-url<br/>valkiria-pipeline-token")]
        MI["Identidad administrada valkiria-api<br/>credencial federada OIDC"]

        subgraph PGF["PostgreSQL Flexible Server 16 · B1ms · TLS obligatorio"]
            DB1[("nissan_synthetic")]
            DB2[("valkiria_workflows<br/>flujos + memoria")]
        end

        subgraph AKS["AKS · 2 nodos Standard_D4s_v5 · Azure CNI overlay · network policy Azure"]
            ING["Ingress NGINX administrado<br/>webapprouting.kubernetes.azure.com"]
            subgraph NS["namespace valkiria"]
                API1["Pod valkiria-api #1<br/>uid 10001 · FS solo lectura"]
                API2["Pod valkiria-api #2"]
                SVC["Service valkiria-api :80 → 8000"]
                SAPP["Pod synthetic-app :8090<br/>initContainer migra si está vacía"]
                OLL["Pod ollama :11434<br/>llama3.2:3b-instruct-q4_K_M"]
                PVC[("PVC ollama-models 20 GiB<br/>managed-csi")]
                CSI["Secrets Store CSI<br/>SecretProviderClass valkiria-keyvault"]
                SEC["Secret valkiria-secrets"]
                SA["ServiceAccount valkiria-api<br/>Workload Identity"]
            end
        end
    end

    USER -- "HTTP :80 IP pública" --> ING
    ING --> SVC
    SVC --> API1
    SVC --> API2
    API1 -- "HTTP :11434" --> OLL
    API1 -- "HTTP :8090" --> SAPP
    API1 -- "TLS :5432" --> DB1
    API1 -- "TLS :5432" --> DB2
    SAPP -- "TLS :5432" --> DB1
    OLL --- PVC
    SA -. "token federado" .-> MI
    MI -. "Key Vault Secrets User" .-> KV
    CSI -. "lee secretos" .-> KV
    CSI -- "sincroniza" --> SEC
    SEC -. "envFrom" .-> API1
    SEC -. "envFrom" .-> SAPP
    AKS -. "AcrPull" .-> ACR
    AKS -. "omsagent" .-> LA
    ADOAG -- "az acr build" --> ACR
    ADOAG -- "kubectl apply · deploy.sh" --> AKS
    ADOAG -- "POST /pipeline-results Bearer" --> ING
```

### Nodos y artefactos

| Nodo | Artefacto desplegado | Recursos y configuración | Sondas |
|---|---|---|---|
| `valkiria-api` (Deployment, 2 réplicas) | `<acr>.azurecr.io/valkiria:<tag>` | ConfigMap `valkiria-config` + Secret `valkiria-secrets`; `/tmp` `emptyDir`; volumen CSI en `/mnt/secrets` | `readiness` y `liveness` en `GET /health` |
| `synthetic-app` (Deployment, 1) | Misma imagen, comando `uvicorn valkiria.synthetic_app.app:create_synthetic_app` | `initContainer` `postgres:16-alpine` aplica V1 y V2 si la base está vacía | `GET /health` |
| `ollama` (Deployment, 1) | `ollama/ollama:0.32.11` | Descarga el modelo una vez al PVC; `OLLAMA_NUM_PARALLEL=4`, `OLLAMA_KEEP_ALIVE=24h` | `readiness`: el modelo aparece en `ollama list` |
| Ingress | NGINX administrado (app routing) | Sin host ni TLS todavía: responde en la IP pública | — |
| PostgreSQL Flexible Server | Bases `nissan_synthetic` y `valkiria_workflows` | B1ms, `sslmode=require`, regla de firewall `AllowAzureServices` | — |

### Flujo de secretos (sin contraseñas en el pipeline ni en el repositorio)

```mermaid
sequenceDiagram
    autonumber
    participant OP as Operador
    participant BI as Bicep
    participant KV as Key Vault
    participant POD as Pod valkiria-api
    participant SA as ServiceAccount
    participant AAD as Microsoft Entra ID
    participant CSI as Secrets Store CSI
    participant K8S as Secret valkiria-secrets

    OP->>BI: az deployment group create con VALKIRIA_PG_ADMIN_PASSWORD del entorno
    BI->>KV: crea synthetic-database-url, workflow-database-url, valkiria-pipeline-token
    Note over BI,KV: la contraseña solo existe al crear la infraestructura
    POD->>SA: token proyectado azure.workload.identity/use
    CSI->>AAD: intercambia el token federado por un token de acceso
    AAD-->>CSI: token de la identidad valkiria-api
    CSI->>KV: lee los 3 secretos con rol Key Vault Secrets User
    CSI->>K8S: sincroniza como VALKIRIA_SYNTHETIC_DATABASE_URL y otras
    K8S-->>POD: envFrom al arrancar
    POD->>POD: Settings los carga como SecretStr
```

### Despliegue paso a paso

```mermaid
flowchart LR
    A["1 · az group create<br/>rg-valkiria-qa"] --> B["2 · Bicep main.bicep<br/>ACR, AKS, KV, PG, LA, identidad"]
    B --> C["3 · Salidas: acrName, aksName,<br/>keyVaultName, tenantId,<br/>workloadIdentityClientId, postgresFqdn"]
    C --> D["4 · az acr build<br/>valkiria:tag"]
    D --> E["5 · deploy.sh<br/>reemplaza __TOKENS__ y falla si queda alguno"]
    E --> F["6 · kubectl apply -k deploy/azure/aks<br/>overlay sobre deploy/kubernetes"]
    F --> G["7 · Espera rollout<br/>y prueba /health por el Ingress"]
```

En Azure DevOps, los pasos 4 a 7 los ejecutan las etapas `Build` y `DeployQA` del pipeline, con aprobación manual en el environment `valkiria-qa` y una service connection con Workload Identity federation (`valkiria-azure`).

## Desarrollo local con Docker Compose

```mermaid
flowchart LR
    subgraph HOST["Máquina del desarrollador"]
        BR(["Navegador<br/>localhost:8000"])
        OLLH["Ollama del host<br/>:11434"]
        subgraph DC["docker-compose.synthetic.yml"]
            API["api :8000<br/>valkiria:0.5.0-browser<br/>Chromium · AUTOMATION_EXECUTE=true"]
            SAPP["synthetic-app :8090<br/>valkiria:0.5.0"]
            PG[("postgres :5432<br/>postgres:16-alpine<br/>V1 + V2 al crear el volumen")]
            OLLC["ollama :11434<br/>perfil llm, opcional"]
        end
    end
    BR --> API
    API -- "host.docker.internal:11434" --> OLLH
    API -. "perfil llm" .-> OLLC
    API -- "synthetic-app:8090" --> SAPP
    API -- "postgres:5432" --> PG
    SAPP --> PG
```

Todos los puertos se publican solo en `127.0.0.1`. `api` y `synthetic-app` arrancan cuando sus dependencias están *healthy*. Los contenedores corren con `read_only`, `/tmp` en tmpfs, `cap_drop: [ALL]` y `no-new-privileges`.

## Kubernetes local con kind

```mermaid
flowchart LR
    BR(["localhost:8080"]) --> NP
    subgraph KIND["Clúster kind valkiria · namespace valkiria-local"]
        NP["Service valkiria-api<br/>NodePort 30080"] --> API["valkiria-api · 1 réplica<br/>imagen -browser"]
        API --> SAPP["synthetic-app<br/>initContainer espera a PostgreSQL"]
        API --> PG[("postgres 16<br/>ConfigMap synthetic-db-init<br/>emptyDir")]
        SAPP --> PG
        SEC["Secret valkiria-secrets<br/>secretGenerator con URL y token locales"] -.-> API
    end
    API -- "host.docker.internal:11434" --> OLL["Ollama del host"]
```

`deploy/kind/up.sh` crea el clúster, construye y carga la imagen, aplica el overlay y espera (unos 75 s desde cero). Reutiliza `deploy/kubernetes/` como base.

## Puertos y protocolos

| Origen | Destino | Puerto | Protocolo | Autenticación |
|---|---|---|---|---|
| Navegador | Ingress / API | 80 (AKS), 8000 (Compose), 8080 (kind) | HTTP | Ninguna (`X-Actor` informativo); OIDC pendiente |
| API | Ollama | 11434 | HTTP, `/v1/chat/completions` | Ninguna en el clúster; `Bearer` o `api-key` si es un endpoint administrado |
| API | App sintética | 8090 | HTTP | Ninguna (red interna) |
| API, app sintética | PostgreSQL | 5432 | PostgreSQL con TLS en Azure | Usuario y contraseña desde Key Vault |
| Pipeline | API | 80 (Ingress) | HTTP | `Bearer valkiria-pipeline-token`, comparado con `hmac.compare_digest` |
| Pipeline | PostgreSQL sintético | 5432 | PostgreSQL | URL desde el grupo de variables ligado a Key Vault |
| AKS | ACR | 443 | HTTPS | Identidad del kubelet con `AcrPull` |
| Pods | Key Vault | 443 | HTTPS | Workload Identity |

## Endurecimiento por capa

```mermaid
flowchart TB
    subgraph IMG["Imagen"]
        i1["multi-etapa sin herramientas de build"]
        i2["usuario app uid 10001 sin shell"]
        i3["frontend de root y solo lectura"]
        i4["HEALTHCHECK con Python estándar"]
    end
    subgraph POD["Pod"]
        p1["runAsNonRoot · seccomp RuntimeDefault"]
        p2["readOnlyRootFilesystem · /tmp emptyDir"]
        p3["sin capacidades · sin escalamiento"]
        p4["token de service account solo con Workload Identity"]
    end
    subgraph NET["Red"]
        n1["Azure CNI overlay + network policy"]
        n2["servicios internos ClusterIP"]
        n3["PostgreSQL con TLS obligatorio"]
    end
    subgraph SECRETS["Secretos"]
        s1["Key Vault con RBAC y purge protection"]
        s2["CSI con rotación"]
        s3["SecretStr en la aplicación"]
        s4["redacción en logs y en prompts"]
    end
    IMG --> POD --> NET --> SECRETS
```

## Brechas físicas conocidas

| Brecha | Impacto | Acción |
|---|---|---|
| Ingress sin TLS ni dominio | Tráfico en claro hacia la IP pública | Certificado desde Key Vault y DNS |
| PostgreSQL con acceso público (`AllowAzureServices`) | Cualquier servicio de Azure puede intentar conectarse | Private Endpoint o integración con VNet |
| Sin autenticación de usuarios | Cualquiera con acceso a la IP usa la API | OIDC/JWT con Microsoft Entra ID |
| AKS sin Chromium | Los scripts web no se ejecutan en QA; solo API y datos | Imagen `-browser` para la API en AKS o ejecución web solo en el pipeline |
| Ollama en CPU, 1 réplica | Latencia de 20 a 90 s por paso; punto único de falla del LLM | Nodo con GPU o endpoint administrado compatible con OpenAI |
| Imagen por etiqueta, no por digest | Una etiqueta reescrita cambia lo desplegado | Desplegar por digest |
| `CORS` con `localhost` en AKS | Restringe el uso desde otros orígenes | Configurar el dominio real |
