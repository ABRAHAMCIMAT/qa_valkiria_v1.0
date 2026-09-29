"""Glosario de QA curado: fuente verificada para preguntas conceptuales (evita depender de lo que recuerde el modelo)."""

from __future__ import annotations

import re

from valkiria.memory.text import fold, tokens

GLOSSARY: dict[str, str] = {
    "prueba de carga": "Verifica el comportamiento con la carga esperada (usuarios concurrentes habituales y el pico previsto) y confirma que se cumplen los tiempos de respuesta y el SLA.",
    "prueba de estres": "Aumenta la carga más allá de la capacidad esperada hasta que el sistema se degrada o falla, para conocer su punto de quiebre y cómo se recupera.",
    "prueba de pico": "Aplica aumentos súbitos de carga en poco tiempo (spike) para ver si el sistema absorbe la ráfaga y vuelve a la normalidad.",
    "prueba de resistencia": "Mantiene una carga sostenida durante horas (soak) para detectar fugas de memoria, agotamiento de recursos y degradación progresiva.",
    "prueba de humo": "Verificación rápida de las funciones críticas después de un despliegue para decidir si vale la pena seguir probando.",
    "prueba de regresion": "Vuelve a ejecutar pruebas existentes para confirmar que un cambio no rompió funcionalidad que ya operaba.",
    "prueba exploratoria": "Diseño y ejecución simultáneos guiados por la experiencia del probador, útil para encontrar defectos que los guiones no anticipan.",
    "invest": "Criterios para evaluar una historia de usuario: Independiente, Negociable, Valiosa, Estimable, Pequeña y Testeable (HU-002).",
    "criterios de aceptacion": "Condiciones verificables que debe cumplir la historia; en Valkiria se redactan como Dado/Cuando/Entonces (Gherkin).",
    "particion de equivalencia": "Divide las entradas en clases que el sistema debería tratar igual y prueba un representante de cada clase.",
    "valores limite": "Prueba en los bordes de cada rango (mínimo, máximo y justo fuera de ellos), donde se concentran los defectos.",
    "caso positivo negativo borde": "Positivo: entrada válida con resultado esperado. Negativo: entrada inválida que debe rechazarse. Borde: valores límite. Valkiria exige uno de cada tipo por criterio (HU-004).",
    "matriz de trazabilidad": "Relaciona cada criterio de aceptación con los casos de prueba que lo cubren para demostrar cobertura.",
    "piramide de pruebas": "Muchas pruebas unitarias, menos de integración/API y pocas de interfaz de extremo a extremo, por costo y estabilidad.",
    "prueba e2e": "Prueba de extremo a extremo que recorre el flujo completo como lo haría el usuario, incluyendo integraciones.",
    "mutation testing": "Introduce cambios pequeños y deliberados en el código (mutantes) y mide qué porcentaje detectan las pruebas; evalúa la efectividad de la suite, no del producto.",
    "prueba unitaria": "Verifica una unidad de código aislada (función o clase), con dependencias simuladas; es rápida y la base de la pirámide.",
    "prueba de integracion": "Verifica que componentes o servicios funcionen juntos (API, base de datos, colas) con sus interfaces reales.",
    "prueba de aceptacion": "Confirma que la funcionalidad cumple los criterios de aceptación acordados con negocio antes de darla por terminada.",
    "tdd": "Desarrollo guiado por pruebas: escribir primero una prueba que falla, luego el código mínimo que la pasa y después refactorizar.",
    "bdd": "Desarrollo guiado por comportamiento: especifica el comportamiento con ejemplos en lenguaje de negocio (Dado/Cuando/Entonces) que se automatizan.",
    "cobertura de codigo": "Porcentaje de líneas o ramas ejecutadas por las pruebas; indica qué no se probó, pero no garantiza que lo probado esté bien verificado.",
    "prueba inestable": "Prueba flaky: pasa o falla sin cambios en el código (tiempos, datos compartidos, dependencias externas); resta confianza al CI y debe aislarse y corregirse.",
    "sla p95": "Percentil 95 del tiempo de respuesta: el 95 % de las peticiones debe responder por debajo del umbral acordado.",
}


_CONCEPTUAL = re.compile(r"\b(que es|que son|que significa|significado|diferencia|diferencias|define|definicion|explica|explicame|en que consiste|para que sirve)\b")


def is_conceptual(text: str) -> bool:
    """Pregunta por un concepto ("¿qué es…?", "diferencia entre…"), no una orden ("Diseña una prueba de carga…")."""
    return bool(_CONCEPTUAL.search(fold(text)))


def exact_terms(text: str) -> list[str]:
    folded = fold(text)
    return [key for key in GLOSSARY if key in folded]


def lookup(term: str, limit: int = 3) -> list[dict[str, str]]:
    folded = fold(term)
    exact = [key for key in GLOSSARY if key in folded or folded in key]
    # "prueba" y "caso" aparecen en casi todos los términos: no sirven para decidir cuál se pidió.
    wanted = set(tokens(term)) - {"prueba", "caso", "tipo"}
    scored = sorted(((len(wanted & set(tokens(key))), key) for key in GLOSSARY if key not in exact), reverse=True)
    keys = exact + [key for score, key in scored if score]
    return [{"termino": key, "definicion": GLOSSARY[key]} for key in keys[:limit]]
