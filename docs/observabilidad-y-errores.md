# Observabilidad y manejo de errores

## Trazabilidad

Cada request crea o conserva un `trace_id` mediante `X-Trace-Id`. El identificador aparece en la respuesta HTTP, auditoría, métricas, artefactos, ejecuciones de base y reportes.

## Logging

`infrastructure/logging.py` emite JSON con timestamp, nivel, logger, mensaje y contexto. Redacta tokens, API keys, contraseñas, secretos, DSN, cadenas de conexión y autorización.

No se registran prompts completos, respuestas completas del LLM ni cuerpos de excepción externos.

## Contrato de error

```json
{
  "error": {
    "code": "synthetic_database_error",
    "message": "Mensaje seguro para el consumidor",
    "trace_id": "...",
    "details": {}
  }
}
```

Códigos principales:

- `request_validation_error` → 422.
- `policy_violation` → 400.
- `http_error` → 4xx.
- error del proveedor LLM → 503.
- `internal_error` → 500.

El detalle técnico queda en logs sanitizados y nunca en la respuesta pública.

## Métricas

Registrar duración total y por fase, timeouts, errores por agente, gates fallidos, artefactos, filas afectadas, rollbacks, casos Playwright y costo/tokens cuando estén disponibles.

## Diagnóstico

El primer dato de investigación es el `trace_id`. El orquestador distingue `blocked`, `waiting_approval`, `failed` y `completed`; no deben colapsarse en un booleano de éxito.