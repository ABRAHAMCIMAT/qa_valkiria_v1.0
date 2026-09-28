# Historial de cambios

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).

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
