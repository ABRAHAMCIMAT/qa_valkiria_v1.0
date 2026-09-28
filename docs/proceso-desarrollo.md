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
bandit -q -c pyproject.toml -r src
```

Para la ruta sintética:

```bash
pip install -e '.[dev,synthetic,e2e]'
docker compose -f docker-compose.synthetic.yml up -d --wait postgres
RUN_SYNTHETIC_E2E=1 pytest -v tests/e2e
```

Paquete e imagen:

```bash
python -m build
docker compose -f docker-compose.synthetic.yml up -d --build --wait
curl http://localhost:8000/health
```

## Integración continua

Cada push a `main` y cada Pull Request ejecutan `.github/workflows/ci.yml`: Ruff y Bandit, unitarias y E2E contra PostgreSQL, wheel y sdist, e imagen Docker con el entorno Compose. Un cambio no se considera listo con el CI en rojo. Modificar el workflow requiere un token con permiso `workflow`. Detalle en [Despliegue](despliegue.md#integración-continua).

Antes de subir cambios, integrar primero los de GitHub (`git fetch` y `git rebase origin/main`), porque el repositorio tiene más de un colaborador.

## Criterios de salida

- Tests automatizados.
- Quality gates con evidencia.
- Sin secretos.
- Documentación y `CHANGELOG.md` actualizados.
- CI en verde.
- Limitaciones declaradas.
- Preview diferenciado de release.
- Sin commits directos.

Los cambios externos, PR, despliegues y uso de datos reales requieren aprobación explícita.