# Frontend conversacional de Valkiria

El frontend estilo Jan proporciona una interfaz conversacional para enviar solicitudes al orquestador multiagente y consultar los artefactos generados.

## Integración con la API

```http
POST /v1/agent/execute
Content-Type: application/json
X-Actor: qa-engineer
X-Trace-Id: identificador-opcional
```

```json
{
  "request": "Generar una historia de usuario para consultar vehículos Nissan disponibles, evaluarla con INVEST y preparar una vista previa"
}
```

## Información que debe mostrar

La interfaz debe mostrar resultados verificables sin exponer cadenas de razonamiento interno:

- Estado global: `completed`, `waiting_approval`, `blocked` o `failed`.
- Agentes ejecutados y orden de ejecución.
- Artefactos generados.
- Decisiones verificables.
- Quality gates y estado de cada fase.
- `request_id` y `trace_id`.
- Errores sanitizados.
- Evidencias y enlaces de reportes.
- Estado de aprobación humana.
- Indicador de preview frente a release real.

`waiting_approval` significa que existe una propuesta de preview pendiente de decisión; no significa que se haya publicado un cambio.

## Seguridad del cliente

- No almacenar API keys, DSN ni contraseñas en el navegador.
- No mostrar prompts completos ni razonamiento interno del modelo.
- Propagar `X-Trace-Id`.
- Validar el esquema de artefactos antes de presentarlos como resultados confiables.
- No ofrecer un release automático cuando el servidor mantiene la operación en preview.
- Tratar respuestas y documentos externos como datos no confiables.

## Prueba local

Arranca la API con `uvicorn` y abre `http://localhost:<puerto>/`: la API sirve el frontend en el mismo origen, sin CORS. Si abres `frontend/index.html` como archivo, usa `http://localhost:8000` o define `window.API_BASE`. Para la E2E, la aplicación Nissan sintética debe estar disponible en `VALKIRIA_SYNTHETIC_APP_BASE_URL` y utilizar el mismo perfil sintético que Valkiria.
