"""Memoria de corto plazo: el hilo de una sesión (chat u orquestador).

- Ventana acotada: se conservan literales los últimos turnos; los anteriores se compactan en un resumen
  determinista para no exceder el contexto de Llama 3.2 ni depender de otra llamada al modelo.
- Expira por inactividad (TTL). Es memoria de trabajo, no un historial de auditoría.
- Se redactan credenciales y correos antes de guardar.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field
from sqlalchemy import (
    Column,
    Float,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    delete,
    select,
)

from valkiria.memory.text import redact

MAX_TURN_CHARS = 2000
SUMMARY_LINE_CHARS = 160
MAX_SUMMARY_LINES = 20


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    at: float = Field(default_factory=time.time)
    meta: dict[str, Any] = Field(default_factory=dict)


class Session(BaseModel):
    id: str
    turns: list[Turn] = Field(default_factory=list)
    # Resumen de los turnos que salieron de la ventana, del más antiguo al más reciente.
    summary: list[str] = Field(default_factory=list)
    facts: dict[str, Any] = Field(default_factory=dict)
    updated_at: float = Field(default_factory=time.time)


class SessionStore(Protocol):
    async def get(self, session_id: str) -> Session | None: ...

    async def save(self, session: Session) -> None: ...

    async def delete(self, session_id: str) -> bool: ...

    async def purge(self, older_than: float) -> int: ...


class InMemorySessionStore:
    def __init__(self):
        self._items: dict[str, str] = {}

    async def get(self, session_id: str) -> Session | None:
        raw = self._items.get(session_id)
        return Session.model_validate_json(raw) if raw else None

    async def save(self, session: Session) -> None:
        self._items[session.id] = session.model_dump_json()

    async def delete(self, session_id: str) -> bool:
        return self._items.pop(session_id, None) is not None

    async def purge(self, older_than: float) -> int:
        expired = [k for k, v in self._items.items() if Session.model_validate_json(v).updated_at < older_than]
        for key in expired:
            del self._items[key]
        return len(expired)


class SqlSessionStore:
    def __init__(self, url: str):
        self._engine = create_engine(url, future=True, pool_pre_ping=True)
        metadata = MetaData()
        self._table = Table("valkiria_memory_sessions", metadata, Column("id", String(64), primary_key=True), Column("updated_at", Float, nullable=False, index=True),
                            Column("data", Text, nullable=False))
        metadata.create_all(self._engine)

    async def get(self, session_id: str) -> Session | None:
        return await asyncio.to_thread(self._get, session_id)

    async def save(self, session: Session) -> None:
        await asyncio.to_thread(self._save, session)

    async def delete(self, session_id: str) -> bool:
        return await asyncio.to_thread(self._delete, session_id)

    async def purge(self, older_than: float) -> int:
        return await asyncio.to_thread(self._purge, older_than)

    def _get(self, session_id: str) -> Session | None:
        with self._engine.connect() as connection:
            row = connection.execute(select(self._table.c.data).where(self._table.c.id == session_id)).first()
        return Session.model_validate_json(row[0]) if row else None

    def _save(self, session: Session) -> None:
        with self._engine.begin() as connection:
            connection.execute(delete(self._table).where(self._table.c.id == session.id))
            connection.execute(self._table.insert().values(id=session.id, updated_at=session.updated_at, data=session.model_dump_json()))

    def _delete(self, session_id: str) -> bool:
        with self._engine.begin() as connection:
            return connection.execute(delete(self._table).where(self._table.c.id == session_id)).rowcount > 0

    def _purge(self, older_than: float) -> int:
        with self._engine.begin() as connection:
            return connection.execute(delete(self._table).where(self._table.c.updated_at < older_than)).rowcount


def _summary_line(turn: Turn) -> str:
    text = " ".join(turn.content.split())
    if len(text) > SUMMARY_LINE_CHARS:
        text = text[: SUMMARY_LINE_CHARS - 1] + "…"
    return f"{'Usuario' if turn.role == 'user' else 'Valkiria'}: {text}"


class ShortTermMemory:
    def __init__(self, store: SessionStore, *, max_turns: int = 12, ttl_seconds: float = 7200, clock=time.time):
        self.store = store
        self.max_turns = max_turns
        self.ttl_seconds = ttl_seconds
        self.clock = clock

    async def get(self, session_id: str) -> Session | None:
        session = await self.store.get(session_id)
        if session and self.clock() - session.updated_at > self.ttl_seconds:
            await self.store.delete(session_id)
            return None
        return session

    async def append(self, session_id: str, role: Literal["user", "assistant"], content: str, *, facts: dict[str, Any] | None = None, **meta: Any) -> Session:
        session = await self.get(session_id) or Session(id=session_id)
        session.turns.append(Turn(role=role, content=redact(content)[:MAX_TURN_CHARS], at=self.clock(), meta=meta))
        overflow = len(session.turns) - self.max_turns
        if overflow > 0:
            session.summary.extend(_summary_line(t) for t in session.turns[:overflow])
            session.summary = session.summary[-MAX_SUMMARY_LINES:]
            session.turns = session.turns[overflow:]
        if facts:
            session.facts.update({k: v for k, v in facts.items() if v is not None})
        session.updated_at = self.clock()
        await self.store.save(session)
        # Limpieza oportunista de sesiones abandonadas.
        await self.store.purge(self.clock() - self.ttl_seconds)
        return session

    async def history(self, session_id: str, limit: int = 10) -> list[dict[str, str]]:
        session = await self.get(session_id)
        return [{"role": t.role, "content": t.content} for t in session.turns[-limit:]] if session else []

    async def clear(self, session_id: str) -> bool:
        return await self.store.delete(session_id)


def session_prompt(session: Session | None, *, max_chars: int = 2500) -> str:
    """Bloque de contexto de la sesión para el prompt: hechos, resumen de lo antiguo y turnos recientes."""
    if not session or not (session.turns or session.summary or session.facts):
        return ""
    parts: list[str] = []
    if session.facts:
        parts.append("Datos de la sesión: " + "; ".join(f"{k}={v}" for k, v in session.facts.items()))
    if session.summary:
        parts.append("Antes en la sesión:\n" + "\n".join(f"- {line}" for line in session.summary[-8:]))
    recent = [_summary_line(t) for t in session.turns[-6:]]
    if recent:
        parts.append("Turnos recientes:\n" + "\n".join(recent))
    text = "\n".join(parts)
    return text[-max_chars:]
