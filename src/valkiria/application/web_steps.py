"""Semántica compartida de los pasos web: la usan el generador de scripts (HU-009) y el runner con navegador (HU-010).

Así lo que se ejecuta localmente en Chromium es exactamente lo que el script entregado en el PR hace.

Cada paso de la matriz se aterriza en el catálogo de pantallas (SCREENS): la pantalla, el campo o el botón que
existen de verdad. El modelo suele escribir pasos vagos ("Buscar vehículo disponible") o que cruzan pantallas;
el aterrizaje es determinista y, si un paso no corresponde a nada del catálogo, conserva su texto y falla con
el paso señalado, sin inventar un éxito.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

_QUOTED = re.compile(r"[\"'«“](.+?)[\"'»”]")

# Catálogo de las pantallas de la app sintética (src/valkiria/synthetic_app/web.py). Para otra interfaz se
# reemplaza este catálogo; el Page Object generado usa sus mismos nombres.
SCREENS: dict[str, dict[str, Any]] = {
    "/ui": {"name": "Consulta de inventario", "keywords": ["inventario", "consulta", "consultar", "disponibles", "existencias"],
            "fields": {"Concesionario": "select", "Solo disponibles": "checkbox"}, "buttons": ["Buscar"]},
    "/ui/orders": {"name": "Registrar orden de venta", "keywords": ["orden", "ordenes", "venta", "ventas", "vender", "compra", "comprar", "apartar"],
                   "fields": {"Concesionario": "select", "Vehículo": "select", "Cliente": "select", "Total": "input"}, "buttons": ["Registrar orden"]},
    "/ui/appointments": {"name": "Citas de servicio", "keywords": ["cita", "citas", "servicio", "taller", "mantenimiento"], "fields": {}, "buttons": []},
}
DEFAULT_ROUTE = "/ui"

# Opciones de las listas en la base sintética: permiten aterrizar "Seleccionar Sentra 2025" en la lista Vehículo aunque el caso no traiga datos.
OPTIONS: dict[str, list[str]] = {
    "Concesionario": ["Nissan Apodaca Sintético", "Nissan Centro Sintético"],
    "Vehículo": ["Sentra 2025", "Kicks 2025", "Versa 2024"],
    "Cliente": ["Cliente Sintético 001", "Cliente Sintético 002"],
}

# Datos que existen en la base sintética (synthetic_db/fixtures): la matriz debe usarlos para que sus casos sean ejecutables.
SYNTHETIC_DATA = ("Vehículos: Sentra 2025 (stock 10, disponible), Kicks 2025 (stock 0, sin stock), Versa 2024 (stock 3, en el concesionario inactivo). "
                  "Concesionarios: Nissan Apodaca Sintético (activo) y Nissan Centro Sintético (inactivo). "
                  "Clientes: Cliente Sintético 001 y Cliente Sintético 002. Total de una orden: mayor a cero (p. ej. 289900).")

# Mensajes que muestran las pantallas sintéticas, por las palabras con que un resultado esperado suele describirlos.
KNOWN_MESSAGES = [
    (r"\b(inactiv\w*)\b", "Concesionario inactivo", True),
    (r"\b(veh\w*culo sin stock|sin stock|agotad\w*|rechaz\w*.*stock|stock.*rechaz\w*)\b", "Vehículo sin stock", True),
    (r"\b(no aparece\w*|sin veh\w*culos|no hay veh\w*culos|no se muestra\w*|ningun veh\w*culo)\b", "Sin vehículos disponibles", True),
    (r"\b(orden\w*.*(aprobad\w*|registrad\w*|cread\w*)|(aprobad\w*|registrad\w*).*orden\w*|aprobacion de (la )?orden)\b", "Orden aprobada", False),
    (r"\b(total.*(cero|negativ\w*|invalid\w*)|(cero|negativ\w*).*total)\b", "El total debe ser mayor a cero", True),
]
# Convención de la app sintética: los avisos son role=alert y los de error además llevan la clase "error".
ERROR_ALERT = "[role=alert].error"
_STOP = {"que", "los", "las", "del", "con", "para", "por", "una", "uno", "sus", "este", "esta", "boton", "campo", "lista", "pantalla", "seccion",
         "casilla", "tabla", "la", "el", "de", "en", "y", "a", "al", "se", "un"}
_ERROR_WORDS = re.compile(r"\b(rechaz\w*|error|invalid\w*|no permit\w*|bloque\w*|no se registra|mensaje de error)\b")


def plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(text).lower()) if unicodedata.category(c) != "Mn")


def loose_pattern(text: str) -> str:
    """Expresión regular que ignora acentos y mayúsculas ("vehiculo" encuentra "Vehículo")."""
    classes = {"a": "[aá]", "e": "[eé]", "i": "[ií]", "o": "[oó]", "u": "[uúü]", "n": "[nñ]"}
    return "".join(classes.get(ch, re.escape(ch)) for ch in plain(text).strip())


def _stems(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[a-z0-9]+", plain(text)) if w not in _STOP and len(w) > 2}


def action(step: str) -> str:
    text = plain(step).strip()
    if re.match(r"(abrir|ir|navegar|acceder|entrar|ingresar a la|visitar)\b", text):
        return "open"
    if re.match(r"(marcar|activar|habilitar|desmarcar|desactivar|deshabilitar)\b", text):
        return "tick"
    if re.match(r"(ingresar|escribir|capturar|llenar|introducir|teclear|digitar)\b", text):
        return "fill"
    if re.match(r"(seleccionar|elegir)\b", text):
        return "select"
    if re.match(r"(verificar|validar|comprobar|revisar|confirmar|observar|ver)\b", text):
        return "check"
    return "click"


def target(step: str) -> str:
    """Campo o botón al que apunta el paso: "Hacer clic en Buscar" → "Buscar"; "Seleccionar el concesionario 'Apodaca'" → "concesionario"."""
    kind = action(step)
    without_value = _QUOTED.sub("", step).strip()
    if without_value != step.strip() and kind in {"fill", "select"}:
        step = without_value
    elif _QUOTED.search(step):
        return _QUOTED.search(step).group(1)[:60]
    body = re.sub(r"^\s*\S+\s*", "", step)  # sin el verbo
    body = re.sub(r"(?i)^(clic|click)\s+(en\s+)?", "", body)
    body = re.sub(r"(?i)^(en|a|el|la|los|las|un|una|de|del)\s+", "", body)
    body = re.sub(r"(?i)^(boton|botón|campo|enlace|menu|menú|opcion|opción|pestaña|filtro|lista|casilla)\s+(de\s+)?", "", body)
    return (body.strip(" .") or step.strip(" ."))[:60].replace('"', "").replace("'", "")


def key(step: str) -> str:
    """Texto con que se localiza el elemento: el objetivo si es corto; si no, su primera palabra significativa ("vehículo Kicks de la lista" → "vehículo")."""
    wanted = target(step)
    words = wanted.split()
    if len(words) <= 2:
        return wanted
    return next((w for w in words if len(w) > 3 and plain(w) not in _STOP), words[0])


def default_value(step: str) -> str | None:
    quoted = _QUOTED.search(step)
    return quoted.group(1) if quoted else None


def screen_for(text: str) -> str | None:
    """Pantalla que nombra el texto; las específicas (órdenes, citas) antes que la consulta general."""
    folded = plain(text)
    order = sorted(SCREENS, key=lambda route: route == DEFAULT_ROUTE)
    return next((route for route in order if any(re.search(rf"\b{k}\b", folded) for k in SCREENS[route]["keywords"])), None)


def route_for(case: dict[str, Any]) -> str:
    return screen_for(" ".join([case.get("scenario", ""), *case.get("steps", [])])) or DEFAULT_ROUTE


def anchors(expected: str) -> tuple[list[str], bool]:
    """Qué debe verse para dar por cumplido el resultado esperado, y si se espera un aviso de error."""
    folded = plain(expected)
    found: list[str] = []
    expect_error = bool(_ERROR_WORDS.search(folded))
    for pattern, message, is_error in KNOWN_MESSAGES:
        if re.search(pattern, folded):
            found.append(message)
            expect_error = expect_error or is_error
    found += [q for q in _QUOTED.findall(expected)]
    if not found:
        # Sin mensaje conocido ni texto entre comillas: nombres propios del resultado (modelos, concesionarios) como evidencia.
        words = [m.group(0) for m in re.finditer(r"\b[A-ZÁÉÍÓÚÑ][\wáéíóúñ]{2,}\b", expected.strip()) if m.start() > 0]  # no la mayúscula inicial
        found += [w for w in words if plain(w) not in {"los", "las", "que", "una", "nissan", "mensaje", "error"}][:3]
    return list(dict.fromkeys(found)), expect_error


def option_matches(value: str, option_value: str, option_text: str) -> bool:
    """Una opción de lista corresponde al dato si es su valor, si el dato está en su texto o si su texto está en el dato."""
    wanted, text = plain(value).strip(), plain(option_text).strip()
    return bool(wanted) and (str(value) == str(option_value) or wanted in text or (bool(text) and text in wanted))


@dataclass
class WebStep:
    text: str
    action: str  # open | fill | select | tick | click | check
    target: str
    value: str | None = None
    data_key: str | None = None  # llave del archivo de datos de la que sale el valor
    anchors: list[str] = field(default_factory=list)  # solo en "check": lo que el paso pide ver


@dataclass
class WebPlan:
    route: str
    steps: list[WebStep] = field(default_factory=list)
    anchors: list[str] = field(default_factory=list)
    expect_error: bool = False


def _data_for(label: str, step: str, data: dict[str, Any]) -> tuple[str | None, str | None]:
    """(llave de datos, valor) del campo: el dato cuya llave coincide con el campo; si no, el texto entre comillas del paso."""
    wanted = _stems(label)
    for data_key, value in data.items():
        if _stems(str(data_key)) & wanted and value not in (None, ""):
            return str(data_key), str(value)
    return None, default_value(step)


def _best(names: list[str], words: set[str]) -> list[tuple[int, str]]:
    return sorted(((len(_stems(n) & words), n) for n in names if _stems(n) & words), key=lambda x: -x[0])


def _distinctive(option: str) -> str:
    """Palabra que distingue una opción de las demás de su lista: "Nissan Apodaca Sintético" → "apodaca"; "Cliente Sintético 002" → "002"."""
    common = {"nissan", "sintetico", "cliente", "2025", "2024"}
    words = [w for w in re.findall(r"[a-z0-9]+", plain(option)) if w not in common]
    return words[0] if words else ""


def _named_option(label: str, named: str) -> str | None:
    return next((o for o in OPTIONS.get(label, []) if _distinctive(o) and re.search(rf"\b{_distinctive(o)}\b", named)), None)


def _tick_value(text: str) -> str:
    return "off" if re.match(r"des", plain(text).strip()) else "on"


def _ground(text: str, kind: str, current: str, data: dict[str, Any]) -> tuple[str, list[WebStep]]:
    """Aterriza un paso en la pantalla: devuelve la pantalla en la que queda y las acciones concretas (con navegación si cambia de pantalla)."""
    words = _stems(text)
    if kind == "open":
        route = screen_for(text) or current
        return route, [WebStep(text=text, action="open", target=route)]
    if kind == "check":
        return current, [WebStep(text=text, action="check", target=key(text), anchors=anchors(text)[0])]
    field_words = words - _stems(text.split()[0]) if text.split() else words  # sin el verbo
    for route in [current, *(r for r in SCREENS if r != current)]:
        screen = SCREENS[route]
        steps: list[WebStep] = []
        if kind == "click":
            buttons = _best(screen["buttons"], words)
            boxes = _best([f for f, t in screen["fields"].items() if t == "checkbox"], field_words)
            if buttons:
                steps = [WebStep(text=text, action="click", target=buttons[0][1])]
            elif boxes:
                steps = [WebStep(text=text, action="tick", target=boxes[0][1], value="on")]
        else:
            labels = [label for _, label in _best(list(screen["fields"]), field_words)]
            named_options: dict[str, str] = {}
            if not labels:
                # "Seleccionar Sentra 2025" nombra el valor, no el campo: el campo es el del dato del caso con ese valor
                # o, sin datos, la lista del catálogo que tiene esa opción.
                named = plain(text)
                labels = [label for label in screen["fields"] for data_key, value in data.items()
                          if len(str(value).strip()) >= 3 and re.search(rf"\b{re.escape(plain(str(value)).strip())}\b", named) and _stems(str(data_key)) & _stems(label)]
                for label in screen["fields"]:
                    option = _named_option(label, named)
                    if option and label not in labels:
                        labels.append(label)
                        named_options[label] = option
            for label in labels:
                if label in named_options:
                    steps.append(WebStep(text=text, action="select", target=label, value=named_options[label]))
                    continue
                kind_of = screen["fields"][label]
                if kind_of == "checkbox":
                    steps.append(WebStep(text=text, action="tick", target=label, value=_tick_value(text)))
                    continue
                data_key, value = _data_for(label, text, data)
                if value is None:
                    # "Seleccionar Concesionario Nissan Centro Sintético" sin datos: la opción nombrada en el paso.
                    value = _named_option(label, plain(text))
                steps.append(WebStep(text=text, action="fill" if kind_of == "input" else "select", target=label, value=value, data_key=data_key))
        if steps:
            prefix = [] if route == current else [WebStep(text=f"(ir a {screen['name']})", action="open", target=route)]
            return route, prefix + steps
    # Sin correspondencia en el catálogo: se conserva el paso literal y, si no existe en la pantalla, falla señalándolo.
    if kind in {"fill", "select"}:
        # Campo desconocido: el paso se intenta tal cual para que falle a la vista, nunca se omite en silencio.
        data_key, value = _data_for(target(text), text, data)
        return current, [WebStep(text=text, action=kind, target=key(text), value=value if value is not None else target(text), data_key=data_key)]
    if kind == "tick":
        return current, [WebStep(text=text, action="tick", target=key(text), value=_tick_value(text))]
    return current, [WebStep(text=text, action="click", target=key(text))]


def plan_case(case: dict[str, Any]) -> WebPlan:
    data = case.get("data") or {}
    texts = [str(s) for s in case.get("steps") or []]
    first_open = screen_for(texts[0]) if texts and action(texts[0]) == "open" else None
    route = first_open or route_for(case)
    current, steps = route, []
    filled: set[str] = set()  # campos ya capturados en la visita actual a la pantalla
    for text in texts:
        before = current
        current, grounded = _ground(text, action(text), current, data)
        if current != before or any(s.action == "open" for s in grounded):
            filled = set()
        for step in grounded:
            if step.action == "click" and step.target in SCREENS[current]["buttons"]:
                # Antes de enviar el formulario se capturan los datos del caso para los campos de esa pantalla que el paso no mencionó.
                for label, kind_of in SCREENS[current]["fields"].items():
                    data_key, value = _data_for(label, "", data)
                    if label not in filled and kind_of != "checkbox" and data_key:
                        steps.append(WebStep(text=f"(dato del caso: {label})", action="fill" if kind_of == "input" else "select",
                                             target=label, value=value, data_key=data_key))
                        filled.add(label)
            if step.action in {"fill", "select"}:
                filled.add(step.target)
            steps.append(step)
    if steps and steps[0].action == "open" and steps[0].target == route:
        steps = steps[1:]  # la pantalla inicial ya se abre al empezar
    found, expect_error = anchors(str(case.get("expected_result") or ""))
    return WebPlan(route=route, steps=steps, anchors=found, expect_error=expect_error)
