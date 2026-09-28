# Patrones y mantenibilidad

## Patrones adoptados

- **Arquitectura hexagonal:** dominio aislado de infraestructura.
- **Ports and Adapters:** contratos para LLM, bases, repositorios y nubes.
- **Registry:** conjunto explícito de agentes permitidos.
- **Strategy:** proveedores LLM y ejecutores de bases intercambiables.
- **Orchestrator:** ejecución secuencial, handoffs y consolidación.
- **Policy Object:** reglas de lotes y seguridad SQL centralizadas.
- **Repository/Store:** persistencia sustituible; memoria para desarrollo.
- **Anti-Corruption Layer:** traducción de modelos externos.
- **Structured logging:** formato común y redacción.
- **Typed errors:** códigos públicos estables y diagnóstico separado.

## Reglas

1. El dominio no hace I/O.
2. Los agentes no manejan secretos.
3. Los endpoints validan y delegan.
4. Cada agente declara nombre, fase, `can_handle` y `execute`.
5. Cada fase registra gate, auditoría y métrica.
6. Toda integración externa tiene timeout, error sanitizado y prueba de contrato.
7. Toda mutación externa es secuencial, idempotente y aprobable.
8. Las funciones sintéticas son deterministas.
9. No usar `assert True` como evidencia funcional.
10. Documentar limitaciones y estados reales.
11. Capturar excepciones concretas. Una captura genérica solo se admite en una frontera de aislamiento y con `noqa` justificado.
12. La configuración se lee solo mediante `Settings`; nada de `os.getenv` disperso.
13. Toda variable nueva de `Settings` se añade a `.env.example` (lo exige una prueba).
14. Ruff y Bandit sin hallazgos antes de integrar; las reglas están fijadas en `pyproject.toml`.

## Evolución

La siguiente etapa productiva debe añadir persistencia durable, colas, control de concurrencia, autenticación, adaptadores reales, retención de evidencias y pruebas de contrato contra entornos efímeros.