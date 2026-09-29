"""Memoria de largo plazo: lo que el equipo validó y conviene recordar en historias futuras.

Gobierno de lo que se aprende:
- Solo se escribe a partir de decisiones humanas (aprobaciones, rechazos, ediciones) o de hechos que un
  usuario registra explícitamente. Un borrador del LLM nunca se memoriza por sí solo: así el modelo no
  aprende de sus propios errores.
- Todo recuerdo se redacta (credenciales, tokens, correos), se deduplica por contenido y caduca según la
  política de retención. Se puede consultar y olvidar por API.
- La búsqueda es léxica (BM25 con decaimiento por antigüedad): determinista, explicable y sin depender de un
  modelo de embeddings adicional. La interfaz `LongTermStore` permite cambiarla por búsqueda vectorial.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import time
from collections import Counter
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import (
    Column,
    Float,
    Index,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    delete,
    select,
    update,
)

from valkiria.memory.text import fold, redact, tokens

MemoryKind = Literal["approved_story", "po_preference", "human_correction", "lesson", "domain_fact"]
MAX_CONTENT_CHARS = 1200
HALF_LIFE_DAYS = 90.0


class MemoryRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    namespace: str = "default"
    kind: MemoryKind
    content: str
    source: str
    actor: str = "anonymous"
    tags: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    last_used_at: float | None = None
    uses: int = 0

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(fold(" ".join(self.content.split())).encode()).hexdigest()[:24]


class Recall(BaseModel):
    record: MemoryRecord
    score: float
    matched: list[str]


class LongTermStore(Protocol):
    async def upsert(self, record: MemoryRecord) -> tuple[MemoryRecord, bool]: ...

    async def list(self, namespace: str, kinds: list[str] | None = None) -> list[MemoryRecord]: ...

    async def get(self, record_id: str) -> MemoryRecord | None: ...

    async def delete(self, record_id: str) -> bool: ...

    async def touch(self, record_ids: list[str], at: float) -> None: ...

    async def purge(self, older_than: float) -> int: ...


class InMemoryLongTermStore:
    def __init__(self):
        self._items: dict[str, MemoryRecord] = {}

    async def upsert(self, record: MemoryRecord) -> tuple[MemoryRecord, bool]:
        for existing in self._items.values():
            if existing.namespace == record.namespace and existing.content_hash == record.content_hash:
                existing.created_at = record.created_at
                existing.tags = sorted(set(existing.tags) | set(record.tags))
                return existing.model_copy(), False
        self._items[record.id] = record.model_copy()
        return record, True

    async def list(self, namespace: str, kinds: list[str] | None = None) -> list[MemoryRecord]:
        return [r.model_copy() for r in self._items.values() if r.namespace == namespace and (not kinds or r.kind in kinds)]

    async def get(self, record_id: str) -> MemoryRecord | None:
        record = self._items.get(record_id)
        return record.model_copy() if record else None

    async def delete(self, record_id: str) -> bool:
        return self._items.pop(record_id, None) is not None

    async def touch(self, record_ids: list[str], at: float) -> None:
        for record_id in record_ids:
            if record_id in self._items:
                self._items[record_id].uses += 1
                self._items[record_id].last_used_at = at

    async def purge(self, older_than: float) -> int:
        expired = [k for k, r in self._items.items() if r.created_at < older_than]
        for key in expired:
            del self._items[key]
        return len(expired)


class SqlLongTermStore:
    """Persistencia en SQLite o PostgreSQL. En Azure comparte la base `valkiria_workflows`."""

    def __init__(self, url: str):
        self._engine = create_engine(url, future=True, pool_pre_ping=True)
        metadata = MetaData()
        self._table = Table(
            "valkiria_memory_records", metadata,
            Column("id", String(64), primary_key=True),
            Column("namespace", String(120), nullable=False),
            Column("kind", String(40), nullable=False),
            Column("content_hash", String(64), nullable=False),
            Column("created_at", Float, nullable=False),
            Column("data", Text, nullable=False),
            UniqueConstraint("namespace", "content_hash", name="uq_memory_namespace_hash"),
            Index("ix_memory_namespace_kind", "namespace", "kind"),
        )
        metadata.create_all(self._engine)

    async def upsert(self, record: MemoryRecord) -> tuple[MemoryRecord, bool]:
        return await asyncio.to_thread(self._upsert, record)

    async def list(self, namespace: str, kinds: list[str] | None = None) -> list[MemoryRecord]:
        return await asyncio.to_thread(self._list, namespace, kinds)

    async def get(self, record_id: str) -> MemoryRecord | None:
        return await asyncio.to_thread(self._get, record_id)

    async def delete(self, record_id: str) -> bool:
        return await asyncio.to_thread(self._delete, record_id)

    async def touch(self, record_ids: list[str], at: float) -> None:
        await asyncio.to_thread(self._touch, record_ids, at)

    async def purge(self, older_than: float) -> int:
        return await asyncio.to_thread(self._purge, older_than)

    def _upsert(self, record: MemoryRecord) -> tuple[MemoryRecord, bool]:
        t = self._table
        with self._engine.begin() as connection:
            row = connection.execute(select(t.c.data).where(t.c.namespace == record.namespace, t.c.content_hash == record.content_hash)).first()
            if row:
                existing = MemoryRecord.model_validate_json(row[0])
                existing.created_at = record.created_at
                existing.tags = sorted(set(existing.tags) | set(record.tags))
                connection.execute(update(t).where(t.c.id == existing.id).values(created_at=existing.created_at, data=existing.model_dump_json()))
                return existing, False
            connection.execute(t.insert().values(id=record.id, namespace=record.namespace, kind=record.kind, content_hash=record.content_hash,
                                                 created_at=record.created_at, data=record.model_dump_json()))
            return record, True

    def _list(self, namespace: str, kinds: list[str] | None) -> list[MemoryRecord]:
        t = self._table
        query = select(t.c.data).where(t.c.namespace == namespace)
        if kinds:
            query = query.where(t.c.kind.in_(kinds))
        with self._engine.connect() as connection:
            return [MemoryRecord.model_validate_json(row[0]) for row in connection.execute(query)]

    def _get(self, record_id: str) -> MemoryRecord | None:
        with self._engine.connect() as connection:
            row = connection.execute(select(self._table.c.data).where(self._table.c.id == record_id)).first()
        return MemoryRecord.model_validate_json(row[0]) if row else None

    def _delete(self, record_id: str) -> bool:
        with self._engine.begin() as connection:
            return connection.execute(delete(self._table).where(self._table.c.id == record_id)).rowcount > 0

    def _touch(self, record_ids: list[str], at: float) -> None:
        t = self._table
        with self._engine.begin() as connection:
            for row in connection.execute(select(t.c.id, t.c.data).where(t.c.id.in_(record_ids))).all():
                record = MemoryRecord.model_validate_json(row[1])
                record.uses += 1
                record.last_used_at = at
                connection.execute(update(t).where(t.c.id == row[0]).values(data=record.model_dump_json()))

    def _purge(self, older_than: float) -> int:
        with self._engine.begin() as connection:
            return connection.execute(delete(self._table).where(self._table.c.created_at < older_than)).rowcount


def rank(query: str, records: list[MemoryRecord], *, now: float, k1: float = 1.2, b: float = 0.75) -> list[Recall]:
    """BM25 sobre contenido y etiquetas, ponderado por antigüedad (vida media de 90 días)."""
    query_terms = set(tokens(query))
    if not query_terms or not records:
        return []
    docs = [tokens(r.content + " " + " ".join(r.tags)) for r in records]
    avg_len = sum(len(d) for d in docs) / len(docs) or 1.0
    frequency = Counter(term for d in docs for term in set(d))
    results = []
    for record, doc in zip(records, docs, strict=True):
        counts = Counter(doc)
        matched = sorted(term for term in query_terms if counts[term])
        if not matched:
            continue
        score = 0.0
        for term in matched:
            idf = math.log(1 + (len(docs) - frequency[term] + 0.5) / (frequency[term] + 0.5))
            tf = counts[term]
            score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * len(doc) / avg_len))
        age_days = max(0.0, now - record.created_at) / 86400
        score *= 0.5 + 0.5 * 0.5 ** (age_days / HALF_LIFE_DAYS)
        results.append(Recall(record=record, score=round(score, 4), matched=matched))
    return sorted(results, key=lambda r: r.score, reverse=True)


class LongTermMemory:
    def __init__(self, store: LongTermStore, *, top_k: int = 4, retention_days: int = 365, min_score: float = 0.5, clock=time.time):
        self.store = store
        self.top_k = top_k
        self.retention_seconds = retention_days * 86400
        self.min_score = min_score
        self.clock = clock

    async def remember(self, *, kind: MemoryKind, content: str, source: str, actor: str, namespace: str = "default", tags: list[str] | None = None) -> tuple[MemoryRecord, bool]:
        text = " ".join(redact(content).split())[:MAX_CONTENT_CHARS]
        if not text:
            raise ValueError("memory_content_empty")
        record = MemoryRecord(namespace=namespace, kind=kind, content=text, source=source, actor=actor, tags=sorted({fold(t) for t in tags or [] if t}), created_at=self.clock())
        return await self.store.upsert(record)

    async def recall(self, query: str, *, namespace: str = "default", kinds: list[str] | None = None, k: int | None = None) -> list[Recall]:
        now = self.clock()
        records = [r for r in await self.store.list(namespace, kinds) if now - r.created_at <= self.retention_seconds]
        found = [r for r in rank(query, records, now=now) if r.score >= self.min_score][: k or self.top_k]
        if found:
            await self.store.touch([r.record.id for r in found], now)
        return found

    async def search(self, query: str | None, *, namespace: str = "default", kinds: list[str] | None = None, limit: int = 50) -> list[MemoryRecord]:
        if query:
            return [r.record for r in rank(query, await self.store.list(namespace, kinds), now=self.clock())][:limit]
        return sorted(await self.store.list(namespace, kinds), key=lambda r: r.created_at, reverse=True)[:limit]

    async def get(self, record_id: str) -> MemoryRecord | None:
        return await self.store.get(record_id)

    async def forget(self, record_id: str) -> bool:
        return await self.store.delete(record_id)

    async def purge_expired(self) -> int:
        return await self.store.purge(self.clock() - self.retention_seconds)
