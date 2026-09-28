# Revisión de calidad de la versión E2E sintética

## Resultado

El repositorio cuenta con capa multiagente, perfil sintético, adaptadores SQLite/PostgreSQL, aplicación Nissan de prueba, Playwright opcional, quality gates, logging JSON, errores uniformes y documentación en español.

## Cobertura funcional

- API de historias, INVEST, matriz y riesgo.
- Router, Registry y Orchestrator.
- DatabaseAgent para HU-011.
- AutomationAgent y PlaywrightRunner para HU-010.
- Fixtures Nissan deterministas.
- Reportes y auditoría.
- Perfil `synthetic` y bloqueo de producción.
- CI en GitHub Actions: lint, seguridad, unitarias, E2E con PostgreSQL, paquete, imagen Docker y `/health`.
- Configuración tipada con pydantic-settings.

## Estado

| Capacidad | Estado |
|---|---|
| SQLite sintético | Implementado |
| Puerto y ejecutores | Implementado |
| PostgreSQL sintético | Implementado; consultas y mutaciones con límite validadas en E2E y CI |
| Fixtures Nissan | Implementado |
| DatabaseAgent | Implementado |
| Aplicación Nissan | Implementada |
| Playwright | Opcional y controlado por configuración |
| Release preview | Implementado como política |
| PR real | Requiere adaptador GitHub autorizado |
| Imagen Docker y Compose | Implementado y validado en CI |
| Kubernetes | Manifiesto validado; sin despliegue real |
| Publicación de imagen | Pendiente |
| Persistencia productiva | Pendiente |
| MySQL | Pendiente como matriz adicional |
| SQL Server/Oracle | Condicionado |

## Limitaciones honestas

La presencia del adaptador no demuestra disponibilidad de Docker, PostgreSQL, Ollama o Chromium en todos los entornos. La suite corre en CI en cada push; los resultados de la última validación están en [Estado de validación](estado-validacion.md). La aplicación sintética no es el sistema Nissan real y preview no es release.

## Criterios de aceptación

- Toda petición clara pasa por Intake, Grounding, Generation y Evaluation.
- SQL, vehículos e inventario seleccionan Database.
- Playwright y E2E seleccionan Automation.
- `trace_id` se conserva en handoffs.
- Los fallos reintentables se reintentan una sola vez.
- Las ambigüedades y políticas peligrosas bloquean.
- La falta de aprobación impide release real.
- La evidencia no contiene secretos.