"""Utilidades de texto para la memoria: redacción de datos sensibles y tokenización para la búsqueda léxica."""

from __future__ import annotations

import re
import unicodedata

REDACTED = "[REDACTADO]"

# Orden importante: primero las credenciales dentro de URLs, luego pares clave=valor y al final tokens sueltos.
_SENSITIVE = [
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s:/@]+:[^\s@]+@"), rf"\1{REDACTED}@"),
    (re.compile(r"(?i)\b(password|passwd|contraseña|clave|secret|secreto|token|api[_-]?key|authorization)\b\s*[:=]\s*\S+"), rf"\1={REDACTED}"),
    (re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+"), f"Bearer {REDACTED}"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[CORREO]"),
    (re.compile(r"\b[A-Za-z0-9+/_-]{40,}={0,2}"), REDACTED),
]


def redact(text: str) -> str:
    """Quita credenciales, tokens y correos antes de guardar o mostrar un recuerdo."""
    for pattern, replacement in _SENSITIVE:
        text = pattern.sub(replacement, text)
    return text


_STOPWORDS = frozenset(
    "a al algo ante como con contra cual cuando de del desde donde dos el ella en entonces entre era es esa ese esta este esto estos fue ha hay la las le les lo los mas me mi mismo muy "
    "ni no nos o otra otro para pero poco por que quien se sea segun ser si sin sobre su sus tambien tanto te tiene todo tu un una uno unos y ya dado cuando entonces quiero como "
    "the and for with of to in on is are".split()
)


def fold(text: str) -> str:
    """Minúsculas y sin acentos: 'Vehículo' y 'vehiculo' deben coincidir."""
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", fold(text))
    return [_stem(w) for w in words if w not in _STOPWORDS and len(w) > 2]


def _stem(word: str) -> str:
    """Raíz mínima para plurales: quita la 's' final y luego la 'e' final ('disponibles' = 'disponible', 'motores' = 'motor')."""
    if word.endswith("s") and len(word) > 4:
        word = word[:-1]
    if word.endswith("e") and len(word) > 4:
        word = word[:-1]
    return word
