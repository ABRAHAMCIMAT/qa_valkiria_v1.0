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
| 8 | Scripts de automatización | HU-009 | Pide el stack y el repositorio si faltan. Genera código real por caso y pasa lint y detección de secretos. **Verificación humana** antes del PR |
| 9 | Ejecución de scripts (sintética) | HU-010 | Requiere los scripts aprobados. Ejecuta las mismas llamadas de los scripts contra la app sintética y genera evidencia PDF |
| 10 | Pipeline de Azure DevOps | HU-007 | Comando de prueba según el stack (`npx playwright test`, `pytest`, `mvn test`, `newman run`). **Verificación humana** |
| 11 | Diseño de prueba de performance | HU-008A | Se **sugiere** con riesgo alto o si la HU tiene requisitos de rendimiento. Tipo carga, estrés o picos; pide usuarios, duración y SLA. **Verificación humana** |
| 12 | Work Item de Azure DevOps | HU-006 | Vista previa y aprobación |

El panel lateral de la interfaz muestra los 12 pasos desde el inicio, con su estado (hecho, requiere tu decisión, desactualizado, falló o no aplica), el siguiente paso sugerido y un botón para cada paso que se puede ejecutar. También muestra la HU en curso con su historial de versiones.

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

## Verificación y aprobación humana (RT-02)

Cada entregable se aprueba o rechaza sobre la versión mostrada, desde su tarjeta ("Verifiqué y apruebo vN" o "Rechazar con comentario"), desde el panel o por texto. Los puntos de verificación son:
- la HU, con confirmación de supuestos;
- las sugerencias INVEST;
- la matriz;
- los scripts, antes del PR y de ejecutarlos;
- el pipeline;
- el diseño de performance;
- el Work Item.

"Siguiente" nunca aprueba por el usuario.

## Scripts reales en el stack elegido (HU-009)

| Plataforma | Stack | Qué se genera |
|---|---|---|
| Web | Playwright (TypeScript) | Page Object con localizadores semánticos derivados de los pasos (`getByRole`, `getByLabel`, `getByText`), un spec por caso con `test.step` por paso, datos en JSON, `playwright.config.ts` |
| Web | Selenium (Python, pytest) | Page Object con XPath por texto visible, un test por caso, datos en JSON, `conftest.py` |
| API | Playwright (`request`), RestAssured (Java, JUnit 5) o Postman-Newman | Llamadas reales a la app sintética (`/vehicles`, `/inventory`, `/dealers`, `/sales-orders`, `/service-appointments`), con el código esperado según el tipo de caso |

Cada script lleva el id de su caso y el criterio. Antes del PR se ejecutan lint, detección de secretos y trazabilidad; si hay hallazgos, el PR queda bloqueado. Si el stack cambia, los scripts se regeneran como versión nueva.

## Cómo probar HU-009 y HU-010 en el flujo completo

La app sintética de Nissan es una API. Para ejecutar los scripts en este entorno:
1. Pide los scripts con un stack de API, por ejemplo: "Genera los scripts en RestAssured para la API en el repositorio nissan-qa/api-tests".
2. Revisa el código en la tarjeta y apruébalo.
3. Di "Ejecuta los scripts" o usa el botón.

Valkiria ejecuta contra la app sintética exactamente las llamadas que contienen los scripts. Muestra por caso el resultado (aprobado o fallido), el código esperado frente al obtenido y el tiempo, y deja la evidencia en PDF descargable. Los fallos se reportan tal cual, sin simular resultados. Los scripts web no tienen interfaz contra la cual ejecutarse aquí: Valkiria lo explica y ofrece regenerarlos con un stack de API, o ejecutarlos en el pipeline contra la UI real.

## Herramientas del panel

La lista completa sale del servidor: validación de base de datos, exportación a Excel y las 15 herramientas y skills del [asistente](asistente.md). Cada una usa la memoria de la conversación y la HU en curso:
- **Validación de BD:** deriva consultas SELECT de las reglas de la HU, bloquea todo lo que no sea de solo lectura, las ejecuta en la base sintética y deja evidencia.
- **Herramienta de BD:** usa el lenguaje del stack elegido, por ejemplo Java con RestAssured.
- **Herramienta de automatización:** usa la plataforma y el framework del flujo.
- **Memoria del equipo:** busca por el título de la HU.
- **Análisis de SQL:** pide el script o usa la última consulta de la sesión.
- **Pipeline y performance:** avanzan el paso del flujo.

Si falta un dato, se pide en un formulario.

## Latencia

La matriz se genera por criterio y en paralelo (`VALKIRIA_LLM_MAX_PARALLEL`, con Ollama en `OLLAMA_NUM_PARALLEL=4`), en formato compacto. El modelo solo redacta escenario, pasos, resultado y datos; el código arma id, tipo, criterio, prioridad y precondiciones (HU-004 regla 3), con cobertura garantizada. Medido con Llama 3.2: una HU de 3 criterios pasó de 57 s a 23 s.

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
