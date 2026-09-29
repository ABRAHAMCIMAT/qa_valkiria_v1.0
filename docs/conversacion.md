# Conductor de la conversación: el flujo completo de Valkiria

El chat (`POST /v1/chat`) está atendido por el **conductor de la conversación** (`src/valkiria/conversation/`), el agente principal de Valkiria. Cada sesión queda ligada a un flujo por historia ([motor de flujo](flujo-historias.md)) y el conductor siempre sabe en qué paso va. El flujo lo decide el código, de forma determinista y auditable. El modelo (Llama 3.2) solo redacta historias, versiones y respuestas, y contesta preguntas con [herramientas](asistente.md).

## El flujo completo

| # | Paso | HU | Cómo se avanza |
|---|---|---|---|
| 1 | Historia de usuario | HU-003B | Describe el requerimiento. Si es amplio, se propone dividirlo y eliges cuál redactar |
| 2 | Evaluación INVEST | HU-002 | "Evalúa la historia con INVEST" o el botón |
| 3 | Nueva versión con sugerencias | HU-003A | Marca qué sugerencias aplicar. Se genera la versión N+1 y se reevalúa INVEST |
| 4 | Aprobación de la HU | RT-02 | "Apruebo la historia" o el botón. Si hay supuestos, se confirman antes |
| 5 | Matriz de pruebas | HU-004 | "Genera la matriz". Se genera por criterio: 3 casos por criterio. Se puede exportar a Excel |
| 6 | Aprobación de la matriz | RT-02 | Botón o "apruebo la matriz" |
| 7 | Análisis de riesgo | HU-005 | Requiere la HU aprobada; si no lo está, se explica la dependencia |
| 8 | Scripts de automatización | HU-009 | Pide el repositorio si falta; entrega solo por pull request |
| 9 | Pipeline de Azure DevOps | HU-007 | Referencia los scripts existentes |
| 10 | Diseño de prueba de performance | HU-008A | Pide usuarios, duración y SLA si faltan |
| 11 | Work Item de Azure DevOps | HU-006 | Vista previa y aprobación |

El panel lateral de la interfaz muestra los 11 pasos desde el inicio, con su estado (hecho, requiere tu decisión, desactualizado, falló o no aplica), el siguiente paso sugerido y un botón para cada paso que se puede ejecutar. También muestra la HU en curso con su historial de versiones.

## Cómo decide qué hacer con cada mensaje

1. **Acciones de la interfaz** (botones): ejecutar un paso, aprobar, decidir sugerencias, aportar datos, elegir una HU de la división, exportar.
2. **Aclaración pendiente:** "ajusta la actual", "nueva" o "ninguna, sigamos".
3. **Políticas:** producción, commits directos o credenciales se rechazan con amabilidad y el flujo sigue.
4. **Cortesía:** un saludo, agradecimiento o molestia se responde conversando, sin tocar la historia.
5. **"Nueva historia" explícita:** es lo único que reinicia. La HU anterior queda guardada.
6. **Sin HU en curso:** el requerimiento se convierte en HU; las preguntas van al asistente.
7. **Con HU en curso:**
   - **Modificar** ("mejórala", "agrega un criterio…", "cambia el título…") → **siempre la versión N+1**, con la lista de cambios. Lo que dependía de la versión anterior queda desactualizado y se regenera al pedirlo.
   - **Aplicar sugerencias, aprobar, "siguiente", pedir un paso** ("genera la matriz") → avanza el flujo respetando las dependencias. "Siguiente" nunca aprueba por ti.
   - **Preguntas u otras peticiones dentro de la función de Valkiria** → se atienden primero (prioridad al humano) y la respuesta termina con un recordatorio: "Seguimos con la HU «…» v3. Siguiente paso sugerido: …". Las preguntas sobre la HU en curso ("¿cuántos casos tiene la matriz?") se responden con el estado real del flujo.
   - **Algo que parece un requerimiento nuevo** → se pregunta si ajustar la HU actual o empezar otra, con botones. Nunca reinicia solo.

## Versiones

Cada modificación de la historia crea una versión nueva, sea por una instrucción, por sugerencias INVEST aplicadas o por una edición manual. La anterior queda en el historial. Cada versión registra quién la produjo (`story`, `story_revision`, `ai_edit`, `manual_edit`), el modelo y la versión del prompt (RT-06). Si una instrucción no produce cambios, se dice con honestidad y no se crea una versión vacía.

## API

`POST /v1/chat` recibe un mensaje o una acción:

```json
{"session_id": "…", "message": "Mejora la historia"}
{"session_id": "…", "action": {"type": "decide_suggestions", "decisions": {"Testeable": "approved"}}}
{"session_id": "…", "action": {"type": "approve", "artifact": "story", "assumptions_confirmed": true}}
{"session_id": "…", "action": {"type": "input", "goal": "automation", "params": {"repository": "org/repo"}}}
```

Tipos de acción: `run`, `input`, `approve`, `reject`, `decide_suggestions`, `edit_story`, `new_story`, `choose_split`, `export_matrix` y `resume`.

La respuesta incluye:
- `reply` e `intent`;
- `artifact` (tipo, versión, contenido y advertencias);
- `flow` (los 11 pasos con su estado, la HU con sus versiones y los siguientes pasos);
- `actions` (botones sugeridos) y `resume` (recordatorio del paso en curso);
- `assistant` (herramientas usadas, cuando aplica);
- `download` (Excel de la matriz).

Si el modelo falla, se responde con `intent: "error"`, el `trace_id` y la opción de reintentar, sin perder el flujo (RT-04).

## Validación

- `tests/test_conversation.py` tiene 16 pruebas:
  - recorrido completo por los 11 pasos;
  - una versión nueva por cada modificación, y lo que depende de la HU queda desactualizado;
  - preguntas y cortesía a mitad del flujo sin romperlo;
  - aclaración en lugar de reinicio y retomar escrito a mano;
  - "nueva historia" explícita;
  - "siguiente", que nunca aprueba por el usuario;
  - dependencias explicadas, supuestos confirmados y políticas;
  - división de requerimientos amplios;
  - fallas del modelo con reintento;
  - preguntas respondidas con el estado del flujo.
- **Recorrido en vivo con Llama 3.2** (20 turnos): dos mejoras seguidas (v2 y v3), una pregunta de QA y un agradecimiento a mitad del flujo, una aclaración y el retomar, INVEST, aprobación, matriz, pregunta sobre la matriz, riesgo, scripts, pipeline, performance y Work Item. Todo resuelto sin reiniciar el flujo, y "¿Qué sigue?" pidió la aprobación en lugar de aprobar.
