# Matriz de pruebas actualizada

## Capas

| Capa | Objetivo | Evidencia |
|---|---|---|
| Dominio | Modelos, límites, estados y validaciones | `tests/test_domain.py` |
| Políticas | SQL peligroso, lotes y producción | `tests/test_hu010_hu011.py` |
| Agentes | Router, registry, handoff y estados | `tests/test_multiagent_orchestration.py` |
| Base sintética | Fixtures, consultas, rollback e idempotencia | `tests/test_synthetic_database.py` |
| API sintética | Vehículos, órdenes y errores | `tests/e2e/test_synthetic_api.py` |
| Observabilidad | JSON, redacción, trazas y métricas | pruebas de LLMOps y errores |
| Proveedor LLM | JSON inválido, timeout y error HTTP | pruebas de contrato del proveedor |
| Playwright | Casos, screenshots, consola, requests y responses | suite E2E con Chromium |
| CI | Pytest, Ruff, Bandit y PostgreSQL efímero | `ci.yml` |

## Casos mínimos

1. Petición clara con agentes necesarios.
2. Petición ambigua bloqueada en Grounding.
3. Selección correcta de Database y Automation.
4. Handoff con `trace_id` inconsistente.
5. Fallo intermedio sanitizado.
6. Reintento controlado una sola vez.
7. JSON LLM malformado.
8. Timeout del proveedor.
9. SQL peligroso bloqueado antes de conectar.
10. Mutación con rollback.
11. Replay idempotente.
12. PostgreSQL sintético disponible y no disponible.
13. Vehículo sin stock.
14. Concesionario inactivo.
15. Cliente inexistente.
16. Release sin aprobación.
17. Preview sin commit directo.
18. Reporte y auditoría consultables.

## Criterio de aceptación

Una historia no está lista con solo el camino feliz. Debe cubrir límites, errores, seguridad, trazabilidad y regresión. La E2E real usa datos sintéticos y nunca credenciales productivas.

## Comandos

```bash
pytest -q
RUN_SYNTHETIC_E2E=1 pytest -q tests/e2e/test_synthetic_api.py
ruff check src tests
bandit -q -r src
```