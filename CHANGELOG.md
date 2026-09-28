# Historial de cambios

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).

## 2026-09-28 — Revisión del Plan de Mejora Valkiria 2026

### Añadido
- `docs/revision-historias-2026.md`: revisión de EPIC-001 y HU-002 a HU-009 con hallazgos transversales, evaluación INVEST de cada historia original, contraste con el código y recomendaciones priorizadas.

### Cambiado
- `docs/epica-historias-invest.md` v3.0: historias mejoradas con la numeración del plan, KPIs, glosario de fases, dependencias, requisitos transversales RT-01 a RT-06, Definición de Terminado, HU-004 dividida (HU-004B) y HU-008 dividida (HU-008A/B), y HU-011 propuesta para incorporarse al plan.

## 2026-09-28 — Quality gates por agente

### Corregido
- `database` y `automation` comparten la fase `evaluation` con el agente de evaluación, y como los gates se guardaban por fase, el último sobrescribía a los demás. Ahora `quality_gates` tiene un gate por agente, con un campo `phase` nuevo.
- `deploy/kind/up.sh` espera a que terminen los pods de la versión anterior antes de anunciar que el entorno está listo.

### Cambiado (contrato de la API)
- Las claves de `quality_gates` son los nombres de los agentes. La única que cambia es la del agente de operaciones: pasa de `operate` a `operations`. Todas las demás ya coincidían.

## 2026-09-28 — Despliegue en Kubernetes local

### Añadido
- `deploy/kind/`: clúster kind con API, aplicación sintética y PostgreSQL, publicado en `http://localhost:8080` (`up.sh`, `down.sh`, overlay de kustomize y `kind-config.yaml`).
- `deploy/kubernetes/kustomization.yaml`: el manifiesto pasa a ser base reutilizable por overlays.
- `initContainer` que espera a PostgreSQL antes de arrancar la aplicación sintética.

## 2026-09-28 — Mutaciones en PostgreSQL

### Corregido
- Las mutaciones contra PostgreSQL siempre fallaban (`ProgrammingError`), porque la política exige `LIMIT` en `UPDATE`/`DELETE` y PostgreSQL no admite esa sintaxis. Ahora `UPDATE`/`DELETE ... WHERE cond LIMIT n` se traduce a `... WHERE ctid IN (SELECT ctid FROM t WHERE cond LIMIT n FOR UPDATE)`.

### Añadido
- `application/sql_dialect.py`: divisor de sentencias compartido y traducción de dialecto. Las formas ambiguas se rechazan con `postgresql_limit_requires_simple_form_or_subquery`.
- `dialect_rewrites` en el análisis estático y en el resultado del ejecutor de PostgreSQL (sentencia original y ejecutada).
- `tests/test_sql_dialect.py` (19 pruebas) y `tests/e2e/test_postgres_mutations.py` (4 E2E contra PostgreSQL).

### Cambiado
- `static_analyse_database_script` acepta un parámetro opcional `engine`; sin él no se aplica la validación de dialecto de PostgreSQL.
- El límite de filas se exige en cada `UPDATE`/`DELETE` (`each_update_delete_requires_row_limit`), no en cualquier sentencia del script.

## 2026-09-28 — CI con Node 24 (`fe7efd5`)

### Cambiado
- Acciones de GitHub actualizadas a Node 24: `actions/checkout@v7`, `actions/setup-python@v7` y `actions/upload-artifact@v7`.
- Runner fijado en `ubuntu-24.04`, en lugar de `ubuntu-latest`, que migra a Ubuntu 26 a partir del 19 de octubre de 2026.

## 2026-09-28 — Herramientas de desarrollo, Docker, Compose y CI (`1e17dc9`)

### Añadido
- Pipeline de CI en `.github/workflows/ci.yml` con cuatro trabajos: Ruff y Bandit; unitarias y E2E contra PostgreSQL; wheel y sdist; imagen Docker y entorno Compose con prueba de `/health`.
- `tests/test_settings.py` (carga de `.env`, precedencia, secretos, bloqueo de producción y sincronía con `.env.example`) y `tests/conftest.py` (aísla las unitarias del `.env` local).
- `.dockerignore`, `.gitattributes` (finales de línea LF) y [guía de despliegue](docs/despliegue.md).
- Extra `postgres` en `pyproject.toml` (driver psycopg para la imagen) y `pytest-asyncio` y `build` en el extra `dev`.
- Configuración de Ruff (reglas explícitas) y de Bandit en `pyproject.toml`.
- Variable `VALKIRIA_FRONTEND_DIR` para ubicar el frontend fuera del repositorio.

### Cambiado
- `Settings` migrado a pydantic-settings: lectura al instanciar, carga de `.env`, `SecretStr` para secretos, lista de orígenes separada por comas y validación de producción.
- `.env.example` unificado como plantilla única.
- Dockerfile multi-etapa: construye el wheel, copia el frontend, corre como usuario no root (`uid 10001`) e incluye healthcheck.
- `docker-compose.synthetic.yml` levanta el entorno completo: PostgreSQL con healthcheck, migraciones y datos iniciales, aplicación sintética, API y Ollama opcional, con URLs internas entre contenedores y contenedores endurecidos.
- Manifiesto de Kubernetes con imagen real, ConfigMap, Secret opcional, sondas, `runAsUser 10001`, seccomp y Service.
- Capturas de excepciones acotadas; las capturas genéricas restantes son fronteras de aislamiento justificadas.
- `domain/models.py` y los adaptadores reformateados, con una sentencia por línea.

### Corregido
- Los 35 hallazgos de Ruff (imports, import sin uso, capturas genéricas, formato de modelos, `pytest.raises(Exception)` genérico, alias obsoletos).
- Tres falsos positivos de Bandit B105 que impedían que el CI anterior pasara.
- División de sentencias SQL en scripts de una sola línea (la prueba de rollback fallaba).
- Prueba del registro de agentes, a la que le faltaba el agente `automation`.
- Prueba E2E asíncrona, que no corría por falta de soporte async.

### Eliminado
- `.env.synthetic.example` (fusionado en `.env.example`).
- `ci.yml` en la raíz (movido a `.github/workflows/`).
- `deploy/docker/docker-compose.yml`, que apuntaba a un Dockerfile inexistente.

## 2026-09-28 — HU-010 y HU-011 (`135cb56`)

### Añadido
- Selección de plataforma, lenguaje y herramienta (`/v1/automation/tools/select`).
- Exportación de casos manuales a Excel (`/v1/test-cases/export`).
- Descarga de reportes de evidencia en PDF (`/v1/reports/{id}/download`).
- Ejecución real del lote de automatización cuando el runner está habilitado.

## 2026-09-27 — Carga inicial (`52e4dc7`)

- Proyecto Valkiria QA multiagente v1.0 (paquete 0.5.0).
