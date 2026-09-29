"""Estado persistente de un flujo por historia.

Cada artefacto guarda su versión, un hash del contenido y las versiones de las dependencias con las que se
generó (`based_on`). Así se detecta, sin ambigüedad, cuándo un artefacto quedó desactualizado.
Las aprobaciones se registran sobre la versión y el hash exactos (RT-02).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    insert,
    select,
    update,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def content_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


class Approval(BaseModel):
    version: int
    content_hash: str
    decision: Literal["approved", "rejected"]
    actor: str
    comment: str = ""
    # Decisiones adicionales sin tocar el contenido aprobado (por ejemplo, aprobar o rechazar cada sugerencia INVEST).
    details: dict[str, Any] = Field(default_factory=dict)
    at: datetime = Field(default_factory=utcnow)


class ArtifactRecord(BaseModel):
    key: str
    version: int = 1
    payload: dict[str, Any]
    content_hash: str
    based_on: dict[str, int] = Field(default_factory=dict)
    produced_by: str
    warnings: list[str] = Field(default_factory=list)
    approval: Approval | None = None
    # Recuerdos de largo plazo que se consideraron al generarlo (explicabilidad).
    memory_used: list[dict[str, Any]] = Field(default_factory=list)
    # HU-003B, regla 4: supuestos que el PO debe confirmar antes de aprobar.
    assumptions: list[str] = Field(default_factory=list)
    # Datos del usuario con que se generó (stack, repositorio…): si cambian, el artefacto se regenera.
    inputs: dict[str, Any] = Field(default_factory=dict)
    # RT-06: modelo y versión del prompt con que se generó.
    model: str | None = None
    prompt_version: str | None = None
    created_at: datetime = Field(default_factory=utcnow)

    @property
    def approved(self) -> bool:
        return bool(self.approval and self.approval.decision == "approved" and self.approval.version == self.version and self.approval.content_hash == self.content_hash)


class StepFailure(BaseModel):
    capability: str
    error_code: str
    message: str
    attempts: int
    retryable: bool
    at: datetime = Field(default_factory=utcnow)


class ReasoningEntry(BaseModel):
    at: datetime = Field(default_factory=utcnow)
    capability: str | None = None
    decision: str
    reason: str


class WorkflowState(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    revision: int = 0
    actor: str = "anonymous"
    trace_id: str = Field(default_factory=lambda: str(uuid4()))
    goals: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, ArtifactRecord] = Field(default_factory=dict)
    failures: dict[str, StepFailure] = Field(default_factory=dict)
    history: list[ArtifactRecord] = Field(default_factory=list)
    reasoning: list[ReasoningEntry] = Field(default_factory=list)
    # Respuestas del asistente a peticiones fuera del flujo hechas dentro de este flujo (las más recientes).
    answers: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    def put_artifact(self, key: str, payload: dict[str, Any], *, produced_by: str, based_on: dict[str, int], warnings: list[str] | None = None,
                     memory_used: list[dict[str, Any]] | None = None, assumptions: list[str] | None = None, model: str | None = None,
                     prompt_version: str | None = None, inputs: dict[str, Any] | None = None) -> ArtifactRecord:
        previous = self.artifacts.get(key)
        if previous:
            self.history.append(previous)
        record = ArtifactRecord(key=key, version=(previous.version + 1) if previous else 1, payload=payload, content_hash=content_hash(payload),
                                based_on=based_on, produced_by=produced_by, warnings=warnings or [], memory_used=memory_used or [],
                                assumptions=assumptions or [], model=model, prompt_version=prompt_version, inputs=inputs or {})
        self.artifacts[key] = record
        self.failures.pop(produced_by, None)
        return record

    def think(self, decision: str, reason: str, capability: str | None = None) -> None:
        self.reasoning.append(ReasoningEntry(capability=capability, decision=decision, reason=reason))


class ConcurrentModification(RuntimeError):
    """Otro proceso guardó una revisión más nueva del mismo flujo."""


class WorkflowStore(Protocol):
    async def get(self, workflow_id: str) -> WorkflowState | None: ...

    async def save(self, state: WorkflowState) -> WorkflowState: ...


class InMemoryWorkflowStore:
    def __init__(self):
        self._items: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def get(self, workflow_id: str) -> WorkflowState | None:
        raw = self._items.get(workflow_id)
        return WorkflowState.model_validate_json(raw) if raw else None

    async def save(self, state: WorkflowState) -> WorkflowState:
        async with self._lock:
            current = self._items.get(state.id)
            if current and WorkflowState.model_validate_json(current).revision != state.revision:
                raise ConcurrentModification(state.id)
            saved = state.model_copy(update={"revision": state.revision + 1, "updated_at": utcnow()})
            self._items[state.id] = saved.model_dump_json()
            return saved


class SqlWorkflowStore:
    """Persistencia en SQLite o PostgreSQL mediante SQLAlchemy; sobrevive a reinicios del proceso."""

    def __init__(self, url: str):
        self._engine = create_engine(url, future=True, pool_pre_ping=True)
        metadata = MetaData()
        self._table = Table("valkiria_workflows", metadata, Column("id", String(64), primary_key=True), Column("revision", Integer, nullable=False), Column("state", Text, nullable=False))
        metadata.create_all(self._engine)

    async def get(self, workflow_id: str) -> WorkflowState | None:
        return await asyncio.to_thread(self._get, workflow_id)

    async def save(self, state: WorkflowState) -> WorkflowState:
        return await asyncio.to_thread(self._save, state)

    def _get(self, workflow_id: str) -> WorkflowState | None:
        with self._engine.connect() as connection:
            row = connection.execute(select(self._table.c.state).where(self._table.c.id == workflow_id)).first()
        return WorkflowState.model_validate_json(row[0]) if row else None

    def _save(self, state: WorkflowState) -> WorkflowState:
        saved = state.model_copy(update={"revision": state.revision + 1, "updated_at": utcnow()})
        with self._engine.begin() as connection:
            if state.revision == 0:
                connection.execute(insert(self._table).values(id=saved.id, revision=saved.revision, state=saved.model_dump_json()))
            else:
                result = connection.execute(update(self._table).where(self._table.c.id == state.id, self._table.c.revision == state.revision).values(revision=saved.revision, state=saved.model_dump_json()))
                if result.rowcount != 1:
                    raise ConcurrentModification(state.id)
        return saved


def build_workflow_store(url: str | None) -> WorkflowStore:
    return SqlWorkflowStore(url) if url else InMemoryWorkflowStore()
