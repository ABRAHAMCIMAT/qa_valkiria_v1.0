from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4

from valkiria.domain.models import ArtifactType, AuditEvent, Metric


class Phase(StrEnum):
    INTAKE = "intake"
    GROUNDING = "grounding"
    GENERATION = "generation"
    EVALUATION = "evaluation"
    APPROVAL = "approval"
    RELEASE = "release"
    OPERATE = "operate"


class Gate(StrEnum):
    G0 = "G0"
    G1 = "G1"
    G2 = "G2"
    G3 = "G3"
    G4 = "G4"
    G5 = "G5"
    G6 = "G6"

_PHASE_GATE = {
    Phase.INTAKE: Gate.G0,
    Phase.GROUNDING: Gate.G1,
    Phase.GENERATION: Gate.G2,
    Phase.EVALUATION: Gate.G3,
    Phase.APPROVAL: Gate.G4,
    Phase.RELEASE: Gate.G5,
    Phase.OPERATE: Gate.G6,
}


@dataclass
class RunContext:
    actor: str
    trace_id: str
    prompt_version: str = "v1"
    model: str = "unknown"
    gates: dict[str, dict[str, Any]] = field(default_factory=dict)
    started_at: float = field(default_factory=time.perf_counter)


class LLMOpsLifecycle:
    """Orquestador de fases, quality gates, auditoría y métricas."""

    def __init__(self, audit, metrics):
        self.audit = audit
        self.metrics = metrics

    async def start(self, ctx: RunContext, action: str, artifact_type: ArtifactType | None = None):
        await self.record_gate(ctx, Gate.G0, "passed", {"action": action})
        await self.audit.append(AuditEvent(trace_id=ctx.trace_id, actor=ctx.actor, action=f"{Phase.INTAKE}:{action}", artifact_type=artifact_type, outcome="started"))

    async def record_gate(self, ctx: RunContext, gate: Gate, status: str, metadata: dict[str, Any] | None = None):
        if status not in {"passed", "failed", "skipped"}:
            raise ValueError("El estado del quality gate no es válido.")
        details = {"gate": gate.value, "status": status, **(metadata or {})}
        ctx.gates[gate.value] = details
        await self.audit.append(AuditEvent(trace_id=ctx.trace_id, actor=ctx.actor, action=f"quality_gate:{gate.value}", outcome=status, metadata=details))

    async def record(self, ctx: RunContext, phase: Phase, action: str, outcome: str, artifact_type: ArtifactType | None = None, artifact_id: str | None = None, version: int | None = None, metadata: dict[str, Any] | None = None):
        await self.record_gate(ctx, _PHASE_GATE[phase], "passed" if outcome not in {"failed", "blocked"} else "failed", {"action": action, **(metadata or {})})
        await self.audit.append(AuditEvent(trace_id=ctx.trace_id, actor=ctx.actor, action=f"{phase}:{action}", artifact_type=artifact_type, artifact_id=artifact_id, version=version, outcome=outcome, metadata=metadata or {}))

    async def metric(self, ctx: RunContext, name: str, value: float, unit: str):
        await self.metrics.record(Metric(name=name, value=value, unit=unit, trace_id=ctx.trace_id))

    async def finish(self, ctx: RunContext, outcome: str = "completed"):
        status = "passed" if outcome == "completed" else "failed"
        await self.record_gate(ctx, Gate.G6, status, {"elapsed_ms": round((time.perf_counter() - ctx.started_at) * 1000, 2), "outcome": outcome})
        await self.metric(ctx, "run.duration_ms", round((time.perf_counter() - ctx.started_at) * 1000, 2), "ms")

    @staticmethod
    def new_context(actor: str, model: str = "unknown") -> RunContext:
        return RunContext(actor=actor, trace_id=str(uuid4()), model=model)
