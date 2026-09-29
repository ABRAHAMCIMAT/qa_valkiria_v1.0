import asyncio

import pytest

from valkiria.agents.orchestrator import MultiAgentOrchestrator
from valkiria.agents.registry import build_default_registry
from valkiria.application.use_cases import ValkiriaService
from valkiria.infrastructure.memory import (
    InMemoryAudit,
    InMemoryMetrics,
    InMemoryStories,
)
from valkiria.memory.long_term import (
    InMemoryLongTermStore,
    LongTermMemory,
    SqlLongTermStore,
)
from valkiria.memory.service import MEMORY_HEADER, MemoryService, build_memory
from valkiria.memory.short_term import (
    InMemorySessionStore,
    ShortTermMemory,
    SqlSessionStore,
    session_prompt,
)
from valkiria.memory.text import redact, tokens
from valkiria.workflow.engine import WorkflowEngine
from valkiria.workflow.state import InMemoryWorkflowStore
from workflow_fakes import ScriptedLLM


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def engine_with_memory(llm, memory):
    async def no_sleep(_):
        return None

    return WorkflowEngine(InMemoryWorkflowStore(), ValkiriaService(llm, InMemoryAudit(), InMemoryMetrics(), InMemoryStories()), memory=memory, sleep=no_sleep)


async def approve(engine, state, artifact, **extra):
    record = state.artifacts[artifact]
    return await engine.approve(state.id, artifact=artifact, version=record.version, content_hash=record.content_hash, decision="approved", actor="po", **extra)


# --- Texto ---------------------------------------------------------------------------------------

def test_redaction_removes_credentials_tokens_and_emails():
    text = redact("dsn postgresql+psycopg://admin:SuperClave1@db:5432/x api_key=abc123 password: hunter2 Bearer eyJhbGciOi.x.y juan@nissan.test")
    for secret in ("SuperClave1", "abc123", "hunter2", "eyJhbGciOi", "juan@nissan.test"):
        assert secret not in text
    assert "@db:5432/x" in text


def test_tokens_fold_accents_plurals_and_stopwords():
    assert tokens("Los Vehículos disponibles del concesionario") == ["vehiculo", "disponibl", "concesionario"]
    assert tokens("vehículo disponible") == tokens("vehiculos disponibles")
    assert tokens("motores") == tokens("motor")


# --- Memoria de corto plazo ---------------------------------------------------------------------

async def test_short_term_keeps_a_window_and_summarizes_older_turns():
    memory = ShortTermMemory(InMemorySessionStore(), max_turns=4)
    for i in range(6):
        await memory.append("sesion-1234", "user" if i % 2 == 0 else "assistant", f"mensaje {i}")
    session = await memory.get("sesion-1234")
    assert [t.content for t in session.turns] == ["mensaje 2", "mensaje 3", "mensaje 4", "mensaje 5"]
    assert session.summary == ["Usuario: mensaje 0", "Valkiria: mensaje 1"]
    assert "Antes en la sesión" in session_prompt(session)


async def test_short_term_expires_after_inactivity_and_redacts():
    clock = Clock()
    memory = ShortTermMemory(InMemorySessionStore(), ttl_seconds=60, clock=clock)
    await memory.append("sesion-1234", "user", "mi token=abc-123 para la HU", facts={"story_id": "s1"})
    assert "abc-123" not in (await memory.get("sesion-1234")).turns[0].content
    clock.now += 61
    assert await memory.get("sesion-1234") is None


async def test_short_term_sql_store_survives_restart(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'm.db'}"
    await ShortTermMemory(SqlSessionStore(url)).append("sesion-1234", "user", "hola", facts={"story_id": "s1"})
    session = await ShortTermMemory(SqlSessionStore(url)).get("sesion-1234")
    assert session.turns[0].content == "hola" and session.facts == {"story_id": "s1"}


# --- Memoria de largo plazo ---------------------------------------------------------------------

@pytest.fixture(params=["memory", "sql"])
def long_term(request, tmp_path):
    store = InMemoryLongTermStore() if request.param == "memory" else SqlLongTermStore(f"sqlite+pysqlite:///{tmp_path / 'lt.db'}")
    return LongTermMemory(store, clock=Clock(), retention_days=30)


async def test_recall_ranks_relevant_memories_and_filters_by_kind_and_namespace(long_term):
    await long_term.remember(kind="approved_story", content="HU aprobada 'Consultar vehículos por concesionario': el asesor ve stock por agencia.", source="t", actor="po")
    await long_term.remember(kind="po_preference", content="El PO rechazó dividir la HU de login en dos historias.", source="t", actor="po")
    await long_term.remember(kind="approved_story", content="HU aprobada sobre vehículos en otro equipo.", source="t", actor="po", namespace="otro-equipo")
    found = await long_term.recall("stock de vehículos por concesionario")
    assert found[0].record.kind == "approved_story" and "concesionario" in found[0].matched
    assert all(r.record.namespace == "default" for r in found)
    assert await long_term.recall("stock de vehículos", kinds=["po_preference"]) == []


async def test_remember_deduplicates_redacts_and_can_forget(long_term):
    first, created = await long_term.remember(kind="domain_fact", content="La API usa password=Secreta1 en QA.", source="t", actor="qa")
    again, created_again = await long_term.remember(kind="domain_fact", content="La  API usa password=Secreta1 en QA.", source="t", actor="qa")
    assert created and not created_again and again.id == first.id
    assert "Secreta1" not in first.content
    assert await long_term.forget(first.id)
    assert await long_term.search(None) == []


async def test_retention_excludes_expired_memories(long_term):
    await long_term.remember(kind="lesson", content="Dividir historias con más de diez criterios.", source="t", actor="qa")
    long_term.clock.now += 31 * 86400
    assert await long_term.recall("dividir historias criterios") == []
    assert await long_term.purge_expired() == 1


# --- Integración con el flujo por historia --------------------------------------------------------

async def test_workflow_learns_from_approvals_and_uses_it_in_the_next_story():
    llm, memory = ScriptedLLM(), build_memory(None)
    engine = engine_with_memory(llm, memory)
    state, _ = await engine.start(request=None, goals=["story_revision"], params={"requirement": "Consultar vehículos disponibles por concesionario"}, actor="po")
    state, _ = await approve(engine, state, "invest", suggestions={"Testeable": "rejected"})
    state, _ = await approve(engine, state, "story")
    kinds = {r.kind for r in await memory.long_term.search(None)}
    assert kinds == {"po_preference", "approved_story"}

    llm.prompts.clear()
    second, _ = await engine.start(request=None, goals=["story"], params={"requirement": "Consultar vehículos disponibles en otro concesionario"}, actor="po")
    story_prompt = next(user for kind, user in llm.prompts if kind == "story")
    assert MEMORY_HEADER in story_prompt and "HU aprobada" in story_prompt
    assert second.artifacts["story"].memory_used and any(r.decision == "memory" for r in second.reasoning)


async def test_human_edits_and_deterministic_failures_become_memories():
    llm, memory = ScriptedLLM(), build_memory(None)
    engine = engine_with_memory(llm, memory)
    state, _ = await engine.start(request=None, goals=["story"], params={"requirement": "Consultar vehículos"}, actor="po")
    edited = {**state.artifacts["story"].payload, "title": "Consultar stock por concesionario"}
    await engine.edit(state.id, artifact="story", payload=edited, actor="po")

    llm.story = {**llm.story, "acceptance_criteria": [{"id": f"AC-{i:02d}", "text": f"Criterio {i}"} for i in range(1, 12)]}
    await engine.start(request=None, goals=["matrix"], params={"requirement": "HU enorme"}, actor="qa")
    records = {r.kind: r.content for r in await memory.long_term.search(None)}
    assert "Consultar stock por concesionario" in records["human_correction"]
    assert "matrix_requires_split" in records["lesson"]


async def test_workflow_continues_when_memory_storage_fails():
    class BrokenStore(InMemoryLongTermStore):
        async def list(self, namespace, kinds=None):
            raise ConnectionError("db caída")

    memory = MemoryService(ShortTermMemory(InMemorySessionStore()), LongTermMemory(BrokenStore()))
    state, plan = await engine_with_memory(ScriptedLLM(), memory).start(request=None, goals=["story"], params={"requirement": "Consultar vehículos"}, actor="po")
    assert plan.status == "completed" and "story" in state.artifacts
    assert any("no disponible" in r.reason for r in state.reasoning)


# --- Integración con el orquestador ---------------------------------------------------------------

def test_orchestrator_session_resolves_short_follow_ups():
    orchestrator = MultiAgentOrchestrator(build_default_registry(), memory=build_memory(None))
    first = asyncio.run(orchestrator.run("Generar una matriz de pruebas para login seguro", session_id="sesion-orq-1"))
    assert first.session_id == "sesion-orq-1"
    follow_up = asyncio.run(orchestrator.run("y para el registro", session_id="sesion-orq-1"))
    assert follow_up.status == "completed"
    assert follow_up.artifacts["grounding"]["follow_up"] is True
    assert follow_up.artifacts["intake"]["intents_from_session"] is True
    assert follow_up.memory["session_turns_used"] is True

    without_session = asyncio.run(MultiAgentOrchestrator(build_default_registry()).run("y para el registro"))
    assert without_session.status == "blocked"
