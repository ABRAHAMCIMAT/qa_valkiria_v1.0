# Integraciones

## Principio

Las integraciones externas son adaptadores detrás de puertos. El dominio y los agentes no importan SDKs ni reciben credenciales directamente.

## LLM

Se soporta un contrato compatible con OpenAI. El endpoint recomendado para desarrollo es Ollama local con `qwen2.5:7b`; también puede utilizarse un servidor compatible con vLLM, TGI, Llama u otro proveedor autorizado.

```text
VALKIRIA_LLM_BASE_URL
VALKIRIA_LLM_MODEL
VALKIRIA_LLM_API_KEY
```

El proveedor aplica timeout, valida JSON y devuelve errores tipados. Las claves permanecen fuera del repositorio.

## PostgreSQL sintético

`SyntheticPostgresExecutor` usa SQLAlchemy y psycopg. La URL se obtiene exclusivamente desde `VALKIRIA_SYNTHETIC_DATABASE_URL` y nunca desde el request. `docker-compose.synthetic.yml` levanta un contenedor con fixtures ficticios.

## SQLite

`SyntheticSQLiteExecutor` es el fallback para pruebas rápidas y ejecución local sin Docker. La base `:memory:` desaparece al terminar el proceso.

## Playwright

`PlaywrightRunner` es opcional. Requiere el extra E2E, Chromium y `VALKIRIA_AUTOMATION_EXECUTE=true`. Captura screenshots, video, consola, requests, responses y duración.

## Aplicación Nissan sintética

`src/valkiria/synthetic_app/` expone salud, vehículos, inventario, concesionarios, órdenes de venta y citas de servicio. Usa el perfil PostgreSQL sintético cuando está configurado y SQLite como fallback local.

## Integraciones preparadas

Azure DevOps, GitHub y nubes tienen contratos o adaptadores de referencia. Habilitarlos en un entorno real requiere OAuth/OIDC, permisos mínimos, idempotencia, aprobación humana, pruebas de contrato y auditoría.

## Compatibilidad

PostgreSQL es el motor sintético principal. MySQL puede agregarse como segunda matriz. SQL Server y Oracle quedan condicionados por imágenes, licencias y runners disponibles.