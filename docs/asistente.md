# Asistente de razonamiento para peticiones fuera del flujo

Cuando una petición no corresponde al flujo programado (crear o ajustar una HU, o las capacidades del flujo por historia), Valkiria no la fuerza a convertirse en una historia ni la ignora. La resuelve el asistente de razonamiento (`src/valkiria/assistant/`). El asistente usa herramientas y skills reales de Valkiria y, si no tiene la capacidad, lo dice con honestidad y detalla lo que sí puede hacer.

## Cuándo entra

`route_message` decide de forma determinista:

| Petición | Ruta |
|---|---|
| Requerimiento o trabajo sobre la HU: "Consultar vehículos por concesionario", "Como asesor quiero…", "Agrega un criterio…", "¿Puedes agregar un criterio de borde?" | Flujo de HU |
| Preguntas y consultas: "¿Qué vehículos hay en inventario?", "¿Qué es INVEST?", "Muestra los concesionarios activos" | Asistente |
| Órdenes que corresponden a una herramienta: "Genera el pipeline de Azure", "Analiza este script SQL" | Asistente |
| Peticiones fuera del dominio o prohibidas: "Reserva un vuelo", "Despliega en producción" | Asistente (respuesta honesta) |
| Seguimiento corto con una sesión activa: "y para el registro" | Continúa la tarea de la sesión |

Puntos de entrada:
- `/v1/chat`: la interfaz conversacional.
- `/v1/assistant/ask`: consulta directa.
- `/v1/agent/execute`: el agente `assistant` sustituye a Generation y Evaluation.
- `/v1/workflows`: una pregunta sin objetivos reconocibles se responde en lugar de crear una HU.
- `/v1/workflows/{id}/requests`: una pregunta dentro de un flujo se responde con el contexto de ese flujo y queda en `answers`.

## Herramientas y skills

El catálogo es cerrado: el modelo solo puede invocar lo registrado aquí, y el código valida y convierte los argumentos antes de ejecutar. `GET /v1/assistant/capabilities` lo publica.

| Nombre | Tipo | Qué hace (código real) |
|---|---|---|
| `capacidades` | herramienta | Lo que Valkiria puede y no puede hacer |
| `politicas` | herramienta | Reglas de seguridad y límites de calidad |
| `glosario_qa` | herramienta | Definiciones verificadas de conceptos de QA |
| `inventario_nissan` | herramienta | Vehículos, stock y ubicación (app sintética; solo lectura) |
| `concesionarios_nissan` | herramienta | Concesionarios activos e inactivos |
| `citas_servicio` | herramienta | Citas de servicio |
| `analizar_script_sql` | herramienta | Análisis estático de HU-011 con veredicto y explicación de cada hallazgo |
| `herramienta_bd` | herramienta | Framework recomendado por motor y lenguaje |
| `herramienta_automatizacion` | herramienta | Herramienta y lenguaje compatibles por plataforma |
| `buscar_memoria` | herramienta | Memoria de largo plazo del equipo |
| `estado_flujo` | herramienta | Estado, aprobaciones pendientes y siguientes pasos de un flujo |
| `redactar_historia` | skill | HU en borrador (HU-003B) |
| `iniciar_flujo_historia` | skill | Flujo con dependencias (HU-002 a HU-009) |
| `disenar_prueba_performance` | skill | Escenario para Azure Load Testing, sin ejecutarlo (HU-008A) |
| `generar_pipeline_azure` | skill | YAML base del pipeline (HU-007) |

## Cómo razona

1. **Políticas en código, sin LLM:** producción, commits directos, credenciales, correo e internet se rechazan de inmediato, con el motivo y una alternativa real.
2. **Pista de herramientas:** se calculan las herramientas más afines a la petición y se le indican al modelo desde el primer paso.
3. **Glosario primero:** en preguntas conceptuales ("qué es", "diferencia") sobre términos del glosario, se consulta la fuente verificada antes de que el modelo responda.
4. **Ciclo decidir → ejecutar → observar** (máximo 4 pasos). Llama 3.2 elige en JSON entre usar una herramienta, responder o declarar que no puede. El código:
   - valida el nombre, aceptando erratas cercanas dentro del catálogo;
   - valida los argumentos, tratando como ausentes los valores de plantilla;
   - ejecuta con tiempo límite y aísla errores;
   - corta las consultas repetidas.
5. **Verificar antes de rendirse o de responder de memoria:**
   - Un "no puedo" con datos en mano pasa directo a la redacción.
   - Un "no puedo" o una respuesta de memoria, cuando existe una herramienta afín, se revisa una vez.
   - Si la pregunta es conceptual, se le pide responder con conocimiento general.
   - Si al final no se usó nada relevante y una herramienta encaja claramente, se ejecuta. Si requiere argumentos, se le pide al modelo solo extraerlos.
6. **Redacción verificada:** cada herramienta calcula en código una conclusión con sus datos clave; por ejemplo, "Activos: Apodaca. Inactivos: Centro". La redacción del modelo se acepta solo si cumple tres condiciones:
   - menciona esos datos;
   - no se niega teniéndolos;
   - no contradice los calificadores, como presentar como activo un concesionario inactivo.

   Si no las cumple, se le pide una redacción basada exclusivamente en los datos. Si tampoco sirve, se responde con la conclusión verificada. Las consultas irrelevantes no entran en la respuesta.
7. **Honestidad:**
   - Una respuesta sin herramientas se marca: "respondí con conocimiento general de QA; no lo verifiqué con herramientas de Valkiria".
   - Sin capacidad, se responde "No tengo la capacidad de…". Se agregan las alternativas más cercanas, lo que sí puede hacer y los límites.
8. **Sin modelo disponible:** se usa la herramienta sin argumentos que encaje o se declara el límite. Nunca se inventa una respuesta.

## Respuesta

```json
{
  "status": "answered | unsupported",
  "answer": "…",
  "grounded": true,
  "tools_used": ["concesionarios_nissan"],
  "mode": "llm | tool | deterministic | policy",
  "steps": [{"number": 1, "reason": "…", "action": "tool", "tool": "concesionarios_nissan", "arguments": {}, "observation": "…", "error": null}],
  "missing_capability": null,
  "capabilities": null,
  "outputs": {"story": {}, "pipeline_yaml": "…", "performance_plan": {}}
}
```

`mode` indica de dónde sale el texto final:
- `llm`: redacción del modelo verificada contra los datos.
- `tool`: conclusión calculada por la herramienta.
- `deterministic`: sin modelo.
- `policy`: regla de seguridad.

En el chat, la interfaz muestra las herramientas usadas y un desplegable "Cómo lo resolví" con los pasos.

## Validación con Llama 3.2 Instruct

Batería de 13 peticiones fuera del flujo contra `llama3.2:3b-instruct-q4_K_M` real, repetida dos veces con el código final:
- inventario, concesionarios activos, revisión de un script SQL, citas;
- estrés vs. carga, mutation testing;
- diseño de prueba de carga, herramienta de automatización móvil, herramienta de BD para Oracle;
- capacidades, pipeline, correo al PO, reservar un vuelo.

Resultado: 26 de 26 correctas.
- **11 por petición**, respondidas con la herramienta o skill adecuada y con datos verificados. Por ejemplo, el Kicks sin stock no aparece como disponible y el concesionario Centro aparece como inactivo.
- **2 por petición**, rechazadas con honestidad: el correo al PO por política y el vuelo por estar fuera del dominio.
- **Tiempo:** de 2 a 13 s por petición en una Mac con GPU.

Cada salvaguarda se agregó a partir de un error real del modelo en iteraciones previas, y todas tienen prueba de regresión en `tests/test_assistant.py`:
- negarse teniendo los datos;
- responder de memoria cuando había una skill;
- leer al revés los hallazgos de SQL;
- presentar como activo un concesionario inactivo;
- confundir estrés con resistencia;
- escribir mal el nombre de una skill;
- rellenar argumentos con valores de plantilla;
- usar una herramienta irrelevante;
- comentar "la observación" en lugar de responder.

## Límites

- La calidad de la elección de herramienta depende del modelo; las salvaguardas evitan respuestas falsas, pero no garantizan elegir siempre la herramienta óptima a la primera.
- Las herramientas de datos consultan solo la app sintética de Nissan; no hay conectores a sistemas reales.
- El enrutamiento es por reglas; peticiones muy ambiguas pueden ir al flujo de HU. El usuario puede reformularlas como pregunta.
