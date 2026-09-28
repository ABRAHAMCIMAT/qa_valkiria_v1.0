"""Utilidades de dialecto SQL para los ejecutores sintéticos.

La política de Valkiria exige un límite de filas en cada `UPDATE`/`DELETE`. SQLite admite
`UPDATE ... LIMIT n`, pero PostgreSQL no, así que para PostgreSQL la forma simple se traduce a
una subconsulta sobre `ctid` que limita exactamente las mismas filas:

    UPDATE t SET a = 1 WHERE cond LIMIT 5
    → UPDATE t SET a = 1 WHERE ctid IN (SELECT ctid FROM t WHERE cond LIMIT 5 FOR UPDATE)

Solo se traduce la forma simple. Ante cualquier duda (subconsultas, RETURNING, alias, joins o
palabras clave repetidas, incluso dentro de literales) no se traduce y el análisis estático
bloquea el script: se prefiere rechazar a ejecutar algo distinto de lo aprobado.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?"
_LIMITED_UPDATE = re.compile(rf"^update\s+(?P<table>{_IDENTIFIER})\s+set\s+(?P<assignments>.+?)\s+where\s+(?P<condition>.+?)\s+limit\s+(?P<limit>\d+)$", re.IGNORECASE | re.DOTALL)
_LIMITED_DELETE = re.compile(rf"^delete\s+from\s+(?P<table>{_IDENTIFIER})\s+where\s+(?P<condition>.+?)\s+limit\s+(?P<limit>\d+)$", re.IGNORECASE | re.DOTALL)
_LIMIT_CLAUSE = re.compile(r"\blimit\b", re.IGNORECASE)
_PARENTHESIZED = re.compile(r"\([^()]*\)")
_AMBIGUOUS = re.compile(r"\b(select|returning|join|using|with)\b", re.IGNORECASE)

POSTGRESQL_LIMIT_NOT_TRANSLATABLE = "postgresql_limit_requires_simple_form_or_subquery"


@dataclass(frozen=True)
class DialectTranslation:
    original: str
    executed: str
    finding: str | None = None

    @property
    def rewritten(self) -> bool:
        return self.executed != self.original


def split_statements(script: str) -> list[str]:
    """Divide por ";" y usa complete_statement para no cortar literales que contengan ";"."""
    statements: list[str] = []
    current = ""
    for character in script:
        current += character
        if character == ";" and sqlite3.complete_statement(current):
            statement = current.strip().rstrip(";").strip()
            if statement:
                statements.append(statement)
            current = ""
    remainder = current.strip().rstrip(";").strip()
    if remainder:
        statements.append(remainder)
    return statements


def statement_keyword(statement: str) -> str:
    stripped = _COMMENTS.sub(" ", statement).strip()
    return stripped.split(maxsplit=1)[0].lower() if stripped else ""


def _top_level(text: str) -> str:
    """Elimina el contenido entre paréntesis (subconsultas) para mirar solo el nivel superior."""
    previous = None
    while previous != text:
        previous, text = text, _PARENTHESIZED.sub("()", text)
    return text


def _count(word: str, text: str) -> int:
    return len(re.findall(rf"\b{word}\b", text, re.IGNORECASE))


def translate_for_postgresql(statement: str) -> DialectTranslation:
    """Traduce `UPDATE/DELETE ... LIMIT n` a una forma válida en PostgreSQL, o explica por qué no.

    La sentencia ejecutada reutiliza literalmente los fragmentos originales (asignaciones y condición),
    sin normalizar espacios, para no alterar literales.
    """
    candidate = statement.strip()
    detection = " ".join(_COMMENTS.sub(" ", candidate).split())
    keyword = statement_keyword(detection)
    # Un LIMIT dentro de una subconsulta ya es válido en PostgreSQL; solo se trata el del nivel superior.
    if keyword not in {"update", "delete"} or not _LIMIT_CLAUSE.search(_top_level(detection)):
        return DialectTranslation(statement, statement)
    unsupported = DialectTranslation(statement, statement, POSTGRESQL_LIMIT_NOT_TRANSLATABLE)
    if "--" in candidate or "/*" in candidate:
        return unsupported
    if _AMBIGUOUS.search(candidate) or _count("where", candidate) != 1 or _count("limit", candidate) != 1:
        return unsupported
    if keyword == "update":
        match = _LIMITED_UPDATE.match(candidate)
        if not match or _count("from", candidate):
            return unsupported
        # Recompone fragmentos del mismo script ya analizado; no incorpora datos externos (falso positivo de Bandit B608).
        executed = f"UPDATE {match['table']} SET {match['assignments']} WHERE ctid IN (SELECT ctid FROM {match['table']} WHERE {match['condition']} LIMIT {match['limit']} FOR UPDATE)"  # nosec B608
    else:
        match = _LIMITED_DELETE.match(candidate)
        if not match or _count("from", candidate) != 1:
            return unsupported
        # Recompone fragmentos del mismo script ya analizado; no incorpora datos externos (falso positivo de Bandit B608).
        executed = f"DELETE FROM {match['table']} WHERE ctid IN (SELECT ctid FROM {match['table']} WHERE {match['condition']} LIMIT {match['limit']} FOR UPDATE)"  # nosec B608
    return DialectTranslation(statement, executed)
