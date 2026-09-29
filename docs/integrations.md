# Integraciones

## Principio

Las integraciones externas son adaptadores detrás de puertos. El dominio y los agentes no importan SDKs ni reciben credenciales directamente.

## LLM

El modelo es **Llama 3.2 Instruct** (`llama3.2:3b-instruct-q4_K_M`), servido por Ollama con una API compatible con OpenAI: localmente en desarrollo y dentro de AKS en Azure. El proveedor también admite endpoints administrados compatibles con OpenAI que usen el encabezado `api-key` (`VALKIRIA_LLM_AUTH_HEADER=api-key`).

```text
VALKIRIA_LLM_BASE_URL
VALKIRIA_LLM_MODEL
VALKIRIA_LLM_API_KEY
VALKIRIA_LLM_AUTH_HEADER
```

El proveedor aplica timeout, valida JSON y devuelve errores tipados. `VALKIRIA_LLM_API_KEY` se maneja como `SecretStr` y puede quedar vacía para Ollama local. En Compose, la API usa `host.docker.internal:11434` o el servicio `ollama` del perfil `llm`. Las claves permanecen fuera del repositorio.

## PostgreSQL sintético

`SyntheticPostgresExecutor` usa SQLAlchemy y psycopg. La URL se obtiene exclusivamente desde `VALKIRIA_SYNTHETIC_DATABASE_URL` y nunca desde el request. `docker-compose.synthetic.yml` levanta PostgreSQL 16 y aplica la migración y los fixtures ficticios al crear el volumen.

La política exige `LIMIT` en cada `UPDATE` y `DELETE`. Como PostgreSQL no admite esa sintaxis, el ejecutor traduce la forma simple a una subconsulta sobre `ctid` y registra la traducción en la evidencia; las formas ambiguas se bloquean antes de conectar. Detalle en [HU-011](hu010-hu011.md#límite-de-filas-en-postgresql).

## SQLite

`SyntheticSQLiteExecutor` es el fallback para pruebas rápidas y ejecución local sin Docker. La base `:memory:` desaparece al terminar el proceso.

## Playwright

`PlaywrightRunner` ejecuta en Chromium los pasos de cada caso web contra las pantallas de la app sintética, con la traducción de `application/web_steps.py` que también usa el código generado. Requiere el extra E2E, Chromium y `VALKIRIA_AUTOMATION_EXECUTE=true`; la imagen `-browser` (`--build-arg WITH_BROWSER=true`) los incluye y es la que usan Compose y kind. Por caso devuelve el resultado, los pasos ejecutados, el paso que falla con su motivo, la captura (JPEG en `/tmp`, y dentro del resultado cuando falla), la consola y la duración.

## Aplicación Nissan sintética

`src/valkiria/synthetic_app/` expone salud, vehículos, inventario, concesionarios, órdenes de venta y citas de servicio. Usa el perfil PostgreSQL sintético cuando está configurado y SQLite como fallback local.

## Integraciones preparadas

Azure DevOps, GitHub y nubes tienen contratos o adaptadores de referencia. Habilitarlos en un entorno real requiere OAuth/OIDC, permisos mínimos, idempotencia, aprobación humana, pruebas de contrato y auditoría.

## Compatibilidad

PostgreSQL es el motor sintético principal. MySQL puede agregarse como segunda matriz. SQL Server y Oracle quedan condicionados por imágenes, licencias y runners disponibles.