# E2E sintética Nissan

## Objetivo

Validar el recorrido completo sin datos reales:

```text
Frontend → API → agentes → PostgreSQL/SQLite sintético → aplicación Nissan
         → Playwright opcional → aprobación → preview → auditoría
```

## Perfil seguro

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

## Componentes

- `synthetic_db/`: migraciones y fixtures deterministas.
- `SyntheticSQLiteExecutor`: pruebas rápidas en memoria.
- `SyntheticPostgresExecutor`: SQLAlchemy y URL externa al request.
- `synthetic_app/`: API Nissan de prueba.
- `DatabaseAgent`: consulta y evidencia HU-011.
- `AutomationAgent`/`PlaywrightRunner`: casos, screenshots, consola, requests, responses y duración.

## Arranque

```bash
pip install -e '.[dev,synthetic,e2e]'
python -m playwright install chromium
docker compose -f docker-compose.synthetic.yml up -d --wait postgres
uvicorn valkiria.synthetic_app.app:create_synthetic_app --factory --port 8090
uvicorn valkiria.api.app:create_app --factory --port 8000
```

Copia `.env.example` a `.env`; ya trae la URL exclusiva del contenedor sintético. Para levantar todo en contenedores usa `docker compose -f docker-compose.synthetic.yml up -d --build --wait`. Para habilitar Playwright real usa `VALKIRIA_AUTOMATION_EXECUTE=true`; preview es el valor seguro por defecto.

## Petición

```text
Generar una historia para consultar vehículos Nissan, evaluarla con INVEST, crear una matriz positiva, negativa y de frontera, validar contra PostgreSQL sintético, ejecutar Playwright y preparar una vista previa de Pull Request.
```

## Evidencia

Mismo `trace_id`, artefactos por caso, filas, duración, rollback, screenshots, consola, requests, responses, aprobación, `direct_commit=false`, reporte y auditoría.

## Casos negativos

`DROP TABLE vehicles` debe bloquearse antes de ejecutar. También `UPDATE vehicles SET stock = 0` por ausencia de `WHERE`, transacción, rollback y límite. Se prueban además JSON LLM inválido, timeout, base no disponible, vehículo sin stock, concesionario inactivo, replay idempotente y release sin aprobación.

Oracle y SQL Server quedan condicionados por imagen, licencia y runner autorizados.