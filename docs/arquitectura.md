# Arquitectura de Valkiria

## Objetivo

Valkiria separa inteligencia, reglas de negocio, adaptadores y transporte HTTP. El diseño permite probar el flujo completo con SQLite o PostgreSQL sintético, sin conectar sistemas productivos.

## Capas

```text
Frontend conversacional / REST
            ↓
API: contratos HTTP, trazas y errores
            ↓
Agentes: registry, router, orquestador y handoffs
            ↓
Aplicación: casos de uso, políticas y reportes
            ↓
Dominio: modelos, puertos, estados y reglas
            ↓
Adaptadores: LLM, SQLite/PostgreSQL, Playwright y nube
```

## Agentes

`agents/contracts.py` define `AgentContext`, `AgentResult`, `AgentPlan` y `OrchestrationResult`. `AgentRegistry` contiene los agentes permitidos. `AgentRouter` crea planes deterministas mediante `can_handle`. `MultiAgentOrchestrator` ejecuta el plan, valida handoffs, reintenta una vez los fallos reintentables y consolida resultados.

Agentes disponibles: Intake, Grounding, Generation, Evaluation, Database, Automation, Approval, Release y Operations.

## Adaptadores sintéticos

- `SyntheticSQLiteExecutor`: pruebas rápidas en memoria.
- `SyntheticPostgresExecutor`: SQLAlchemy y URL exclusiva del entorno sintético.
- `PlaywrightRunner`: navegador headless, screenshots, consola, requests, responses y duración.
- `synthetic_app`: aplicación Nissan de prueba.

Los requests nunca reciben DSN, contraseñas ni tokens. Las conexiones se resuelven desde configuración externa.

## Configuración y despliegue

`infrastructure/settings.py` centraliza la configuración con pydantic-settings (prefijo `VALKIRIA_`, `.env` opcional, secretos como `SecretStr`). La API y la aplicación sintética se empaquetan en la misma imagen Docker; Compose las levanta junto a PostgreSQL y Kubernetes despliega la API. Detalle en [Despliegue](despliegue.md).

```text
docker-compose.synthetic.yml
  postgres (5432) ← synthetic-app (8090) ← api (8000, sirve el frontend en "/")
```

## Principios

1. Arquitectura hexagonal: el dominio depende de puertos.
2. Defensa en profundidad: validación, políticas, análisis estático, aprobación y release separado.
3. Fail closed: ambigüedad, política fallida o falta de aprobación impiden liberar.
4. Trazabilidad: `trace_id` viaja por agentes, base, automatización y reportes.
5. Idempotencia: las pruebas sintéticas usan una clave derivada de caso y script.
6. Evidencia honesta: preview, simulación y ejecución real tienen estados distintos.

## Producción

El modo `synthetic` es el perfil de desarrollo y CI. `VALKIRIA_ENVIRONMENT=production` no arranca sin `VALKIRIA_ALLOW_PRODUCTION=true`. Producción requiere persistencia durable, OIDC/JWT, gestor de secretos, control de concurrencia, observabilidad centralizada y adaptadores externos con pruebas de contrato.