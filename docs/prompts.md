# System prompts: diseño, reglas de negocio y evaluación

Todos los prompts viven en `src/valkiria/application/prompts.py` y se versionan con `PROMPT_VERSION` (actual: `v2`). Cada artefacto guarda el modelo y la versión del prompt con que se generó (RT-06). Un cambio de prompt se mide antes de activarse con `evals/prompt_eval.py`, que evalúa contra el modelo real con un conjunto de HU de referencia (RT-06).

## Principios

1. **Reglas de negocio en el prompt y garantías en el código.** El prompt orienta al modelo para acertar a la primera. Cuando una regla es exacta, el código la garantiza: nivel de riesgo por puntuación, máximos de casos y lotes, confirmación de supuestos, validación y completado de la matriz.
2. **Pensados para Llama 3.2 3B:**
   - Rol en una línea, reglas numeradas y formato de salida al final.
   - Ejemplos completos de otro dominio (recuperar contraseña) en lugar de marcadores como "…" o "<valor>", que un modelo pequeño copia literalmente.
   - Cortos, porque la salida también ocupa el contexto de 4096 tokens.
3. **Listas en vez de cálculos:** para la matriz, el código le entrega al modelo la lista exacta de casos requeridos (`TC-AC-01-P`, `-N`, `-E`…) en lugar de pedirle que los calcule.
4. **Conversación humana y estable:** la guía `CONVERSATION_STYLE` es común al chat y al asistente para que el trato sea el mismo en toda la interfaz.

## Reglas aplicadas por prompt

| Prompt | HU y reglas | Garantía en código |
|---|---|---|
| `STORY_SYSTEM` | HU-003B: plantilla de 4 componentes, de 3 a 6 criterios Dado/Cuando/Entonces, al menos un criterio de error, reglas solo deducibles, supuestos (máximo 3, regla 3) y división de requerimientos amplios (regla 2) | Validación con `UserStory`. Los supuestos se muestran y deben confirmarse antes de aprobar (regla 4); las HU sugeridas quedan como advertencia |
| `STORY_SPLIT_SYSTEM` | HU-003B regla 2: de 2 a 5 historias independientes, cada una con un solo flujo y un actor | Se invoca cuando el mensaje enumera 3 o más funcionalidades (`looks_broad`), aunque el chat haya elegido "crear" o haya devuelto una división vacía |
| `INVEST_SUGGESTION_SYSTEM` | HU-002 regla 2: una sugerencia accionable para un criterio concreto | Completa las sugerencias que el modelo omite al evaluar los 6 criterios |
| `REVISION_SYSTEM` | HU-003A: solo las sugerencias aprobadas, el resto literal, se conserva la plantilla | Se valida el esquema y se calcula la lista de cambios |
| `CHAT_SYSTEM` | HU-003B (crear, dividir de 2 a 5 HU, supuestos, hasta 2 preguntas), ajustes que conservan lo no pedido y estilo conversacional | Intención inválida → `conversar`. HU incompleta → se regenera con `STORY_SYSTEM`. Se limpia cualquier JSON pegado en la respuesta. Una falla del modelo nunca rompe el chat (RT-04) |
| `INVEST_SYSTEM` | HU-002: los 6 criterios con definición, estado exacto, justificación que cita la HU y sugerencia accionable solo si no cumple (reglas 1 y 2) | `validate_invest` con una corrección; las sugerencias sobre criterios que cumplen no exigen decisión |
| `MATRIX_SYSTEM` | HU-004: positivo, negativo y borde por criterio; campos de la regla 3; prioridad y tipo válidos; datos sintéticos | Lista explícita de casos. Máximo 10 criterios y 30 casos, corrección y completado con plantilla declarado |
| `RISK_SYSTEM` | HU-005: puntuación de 1 a 5 con escala anclada para complejidad, dependencias y criticidad, justificación y al menos una mitigación (reglas 1 y 4) | El nivel se calcula con la suma (3–6 bajo, 7–11 medio, 12–15 alto; regla 2), no se toma del modelo. "Histórico de defectos no considerado" y sugerencia de HU-008A ante riesgo alto |
| `GENERATION_SYSTEM` | Plan verificable del orquestador, con 3 claves exactas y sin inventar resultados | Esquema requerido |
| `ASSISTANT_SYSTEM` | Herramientas reales. Pide los datos obligatorios faltantes, por ejemplo el SLA (HU-008A, regla 3). Rechaza con amabilidad lo que está fuera del dominio | Catálogo cerrado, verificación de fidelidad, extracción determinista de valores cerrados y preguntas de aclaración |
| `ASSISTANT_COMPOSE_SYSTEM` | Redacción de 1 a 3 frases con los datos verificados, sin omitir lo inactivo o sin stock | `faithful()` y, si no se cumple, la conclusión calculada |

**Presupuesto de tokens por prompt** (`token_budget`): en modo JSON, Llama 3.2 a veces entra en un bucle de espacios en blanco hasta agotar el tiempo. Un tope por tarea (500 para las decisiones del asistente, 3500 para la matriz, etc.) corta ese caso en segundos y deja actuar a la recuperación. En vivo, la consulta de concesionarios bajó de 63-69 s a 5-21 s.

**Consultas de datos** (inventario, concesionarios, citas): la respuesta es la conclusión calculada por la herramienta, redactada de forma natural. Así se evitan nombres repetidos u omitidos por el modelo.

Transversales:
- **RT-04:** tiempo límite de 60 s (`VALKIRIA_LLM_TIMEOUT_SECONDS`). Si el modelo falla, el chat responde con un mensaje claro, el `trace_id` y la opción de reintentar, sin perder la conversación.
- **RT-05:** el proveedor redacta credenciales, tokens y correos antes de enviar cualquier texto al modelo, y los prompts prohíben pedir o repetir datos sensibles.

## Cómo conversa Valkiria

- Cordial y profesional, como un colega: saluda, agradece y nunca responde seco.
- Si el usuario está molesto, lo reconoce, se disculpa en una frase y propone cómo corregir.
- Resuelve dentro de su especialidad (HU, INVEST, matriz, riesgo, automatización, pipeline, performance, bases y datos sintéticos de Nissan). Lo que está fuera lo declina con amabilidad y ofrece lo que sí puede hacer.
- No inventa datos ni acciones. Si falta información, pregunta, con un máximo de 2 preguntas.

En el chat, los saludos, agradecimientos y comentarios se responden conversando, no con herramientas. Las preguntas y consultas pasan al [asistente](asistente.md) y los requerimientos, al flujo de HU.

## Evaluación

```bash
# Con Ollama y la app sintética en marcha
python evals/prompt_eval.py --samples 3 --out evals/results/v2.json
# Comparar con otra versión de los prompts (mismos nombres de constantes)
python evals/prompt_eval.py --prompts ruta/prompts_v1.py --tasks chat,assistant
```

Mide la salida cruda de cada prompt, antes de las correcciones automáticas, con las mismas reglas que los validadores:

| Tarea | Métricas |
|---|---|
| story | Forma, validez, de 3 a 6 criterios, Dado/Cuando/Entonces y "Como/quiero/para" |
| invest | Nombres exactos, estados válidos, sin hallazgos de HU-002, sin sugerencias sobre criterios que cumplen y detección de una HU no pequeña ni testeable |
| matrix | Sin hallazgos de HU-004, cobertura positivo/negativo/borde e ids de criterio exactos |
| risk | Nivel válido, justificación, mitigación y nivel de una HU enorme |
| revision | Sugerencia aplicada, título y criterios conservados |
| chat | Intención, HU completa cuando corresponde, división de requerimientos amplios, tono (saludo, agradecimiento, disculpa), respuestas no secas, máximo 2 preguntas y sin markdown |
| generation | Claves obligatorias |
| assistant | 19 peticiones fuera del flujo con la herramienta y los datos esperados, rechazos amables y pedido de datos faltantes |

Resultados: ver [Estado de validación](estado-validacion.md#system-prompts-v2).
