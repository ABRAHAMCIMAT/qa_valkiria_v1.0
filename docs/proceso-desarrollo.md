# Proceso de desarrollo

## Flujo obligatorio

```text
Requerimiento → Intake → Grounding → Generación → Evaluación
             → Aprobación → Release → Operación
```

Database y Automation se insertan cuando la solicitud los requiere.

## Antes de programar

1. Identificar historia, alcance y criterios de éxito.
2. Actualizar épica o historia INVEST.
3. Definir artefactos, riesgos y quality gates.
4. Seleccionar adaptadores sin exponer secretos.
5. Diseñar camino feliz, límites y fallos.

## Durante la implementación

- Mantener separación dominio/aplicación/adaptadores.
- Añadir contrato y prueba con cada agente.
- Propagar `trace_id`.
- Registrar evidencia y métricas.
- Usar errores tipados.
- Mantener preview y PR-only.
- Escribir documentación en español junto con el cambio.

## Antes de integrar

```bash
pip install -e '.[dev]'
pytest -q
ruff check src tests
bandit -q -r src
```

Para la ruta sintética:

```bash
pip install -e '.[dev,synthetic,e2e]'
RUN_SYNTHETIC_E2E=1 pytest -q tests/e2e/test_synthetic_api.py
```

## Criterios de salida

- Tests automatizados.
- Quality gates con evidencia.
- Sin secretos.
- Documentación actualizada.
- Limitaciones declaradas.
- Preview diferenciado de release.
- Sin commits directos.

Los cambios externos, PR, despliegues y uso de datos reales requieren aprobación explícita.