# Revisión de calidad de la versión E2E sintética

## Resultado

La rama `valkiria_nissan` cuenta con capa multiagente, perfil sintético, adaptadores SQLite/PostgreSQL, aplicación Nissan de prueba, Playwright opcional, quality gates, logging JSON, errores uniformes y documentación en español.

## Cobertura funcional

- API de historias, INVEST, matriz y riesgo.
- Router, Registry y Orchestrator.
- DatabaseAgent para HU-011.
- AutomationAgent y PlaywrightRunner para HU-010.
- Fixtures Nissan deterministas.
- Reportes y auditoría.
- Perfil `synthetic` y bloqueo de producción.
- CI con PostgreSQL efímero.

## Estado

| Capacidad | Estado |
|---|---|
| SQLite sintético | Implementado |
| Puerto y ejecutores | Implementado |
| PostgreSQL sintético | Implementado; requiere Docker y extras |
| Fixtures Nissan | Implementado |
| DatabaseAgent | Implementado |
| Aplicación Nissan | Implementada |
| Playwright | Opcional y controlado por configuración |
| Release preview | Implementado como política |
| PR real | Requiere adaptador GitHub autorizado |
| Persistencia productiva | Pendiente |
| MySQL | Pendiente como matriz adicional |
| SQL Server/Oracle | Condicionado |

## Limitaciones honestas

La presencia del adaptador no demuestra disponibilidad de Docker, PostgreSQL, Ollama o Chromium en todos los entornos. La suite debe ejecutarse en CI o localmente con extras instalados. La aplicación sintética no es el sistema Nissan real y preview no es release.

## Criterios de aceptación

- Toda petición clara pasa por Intake, Grounding, Generation y Evaluation.
- SQL, vehículos e inventario seleccionan Database.
- Playwright y E2E seleccionan Automation.
- `trace_id` se conserva en handoffs.
- Los fallos reintentables se reintentan una sola vez.
- Las ambigüedades y políticas peligrosas bloquean.
- La falta de aprobación impide release real.
- La evidencia no contiene secretos.