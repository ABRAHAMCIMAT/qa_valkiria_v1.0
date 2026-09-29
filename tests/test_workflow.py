import pytest

from valkiria.application.use_cases import ValkiriaService
from valkiria.infrastructure.memory import (
    InMemoryAudit,
    InMemoryMetrics,
    InMemoryStories,
)
from valkiria.workflow.engine import WorkflowConflict, WorkflowEngine
from valkiria.workflow.graph import (
    Capability,
    GraphError,
    Requirement,
    closure,
    detect_goals,
    validate_graph,
)
from valkiria.workflow.state import (
    ConcurrentModification,
    InMemoryWorkflowStore,
    SqlWorkflowStore,
    WorkflowState,
)
from workflow_fakes import INVEST, STORY, ScriptedLLM


@pytest.fixture
def llm():
    return ScriptedLLM()


def engine_for(llm, store=None):
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    engine = WorkflowEngine(store or InMemoryWorkflowStore(), ValkiriaService(llm, InMemoryAudit(), InMemoryMetrics(), InMemoryStories()), sleep=fake_sleep)
    engine.sleeps = sleeps
    return engine


def actions(plan):
    return {step.capability: step.action for step in plan.steps}


async def approve_story(engine, state):
    record = state.artifacts["story"]
    return await engine.approve(state.id, artifact="story", version=record.version, content_hash=record.content_hash, decision="approved", actor="po")


def test_graph_is_acyclic_and_closure_respects_dependencies():
    order = validate_graph()
    assert order.index("story") < order.index("matrix") < order.index("automation")
    assert closure(["automation"]) == ["story", "matrix", "automation"]


def test_graph_rejects_cycles_and_unknown_dependencies():
    with pytest.raises(GraphError, match="ciclo"):
        validate_graph({"a": Capability("a", "HU-A", "A", requires=(Requirement("b"),)), "b": Capability("b", "HU-B", "B", requires=(Requirement("a"),))})
    with pytest.raises(GraphError, match="dependencia_desconocida"):
        validate_graph({"a": Capability("a", "HU-A", "A", requires=(Requirement("zzz"),))})


def test_goal_detection_is_specific():
    assert detect_goals("Genera la matriz de pruebas y evalúa el riesgo") == ["matrix", "risk"]
    assert detect_goals("Evalúa la HU con INVEST") == ["invest"]


async def test_independent_branch_advances_while_approval_is_pending(llm):
    engine = engine_for(llm)
    state, plan = await engine.start(request="Redacta la historia de vehículos, genera la matriz de pruebas y evalúa el riesgo", goals=None, params={}, actor="po")
    assert actions(plan) == {"story": "reuse", "matrix": "reuse", "risk": "blocked"}
    assert plan.status == "waiting_approval"
    assert plan.pending_approvals == ["story"]
    assert "HU en borrador" in " ".join(state.artifacts["matrix"].warnings)
    assert plan.next_actions(state)[0]["type"] == "approve"

    state, plan = await approve_story(engine, state)
    assert plan.status == "completed"
    assert state.artifacts["risk"].based_on == {"story": 1}
    assert any("Riesgo alto" in w for w in state.artifacts["risk"].warnings)


async def test_editing_the_story_marks_dependents_stale_and_requires_new_approval(llm):
    engine = engine_for(llm)
    state, _ = await engine.start(request="Redacta la historia y genera la matriz de pruebas y evalúa el riesgo", goals=None, params={}, actor="po")
    state, _ = await approve_story(engine, state)
    assert state.artifacts["matrix"].based_on == {"story": 1}

    edited = {**state.artifacts["story"].payload, "title": "Consultar vehículos disponibles por concesionario"}
    state, plan = await engine.edit(state.id, artifact="story", payload=edited, actor="po")
    assert state.artifacts["story"].version == 2
    assert state.artifacts["matrix"].version == 2 and state.artifacts["matrix"].based_on == {"story": 2}
    assert actions(plan)["risk"] == "blocked" and plan.pending_approvals == ["story"]
    assert any(r.decision == "rerun" and r.capability == "matrix" for r in state.reasoning)


async def test_invest_suggestions_drive_revision_without_looping(llm):
    engine = engine_for(llm)
    state, plan = await engine.start(request=None, goals=["story_revision"], params={"requirement": "Consultar vehículos disponibles"}, actor="po")
    assert plan.pending_approvals == ["invest"]
    invest = state.artifacts["invest"]
    with pytest.raises(WorkflowConflict, match="suggestion_decisions_required"):
        await engine.approve(state.id, artifact="invest", version=invest.version, content_hash=invest.content_hash, decision="approved", actor="po")

    state, plan = await engine.approve(state.id, artifact="invest", version=invest.version, content_hash=invest.content_hash, decision="approved", actor="po",
                                       suggestions={"Testeable": "approved"})
    story = state.artifacts["story"]
    assert story.produced_by == "story_revision" and story.version == 2
    assert len(story.payload["acceptance_criteria"]) == 3
    assert plan.status == "completed"
    assert llm.calls.count("revision") == 1


async def test_rejecting_all_suggestions_skips_revision(llm):
    engine = engine_for(llm)
    state, _ = await engine.start(request=None, goals=["story_revision"], params={"requirement": "Consultar vehículos"}, actor="po")
    invest = state.artifacts["invest"]
    state, plan = await engine.approve(state.id, artifact="invest", version=1, content_hash=invest.content_hash, decision="approved", actor="po", suggestions={"Testeable": "rejected"})
    assert actions(plan)["story_revision"] == "skipped"
    assert state.artifacts["story"].version == 1


async def test_transient_llm_errors_are_retried_with_backoff(llm):
    llm.failures["story"] = 2
    engine = engine_for(llm)
    state, plan = await engine.start(request="Redacta la historia de vehículos", goals=["story"], params={}, actor="po")
    assert "story" in state.artifacts
    assert engine.sleeps == [1.0, 2.0]
    assert plan.status == "completed"


async def test_failed_step_is_isolated_and_can_be_resumed(llm):
    llm.failures["matrix"] = -1
    engine = engine_for(llm)
    state, plan = await engine.start(request="Redacta la historia, genera la matriz de pruebas y evalúa el riesgo", goals=None, params={}, actor="po")
    state, plan = await approve_story(engine, state)
    assert actions(plan)["matrix"] == "failed" and "risk" in state.artifacts
    assert plan.status == "failed"
    assert state.failures["matrix"].attempts == 3
    assert any(a["type"] == "resume" for a in plan.next_actions(state))

    llm.failures.pop("matrix")
    state, plan = await engine.resume(state.id)
    assert "matrix" in state.artifacts and "matrix" not in state.failures
    assert plan.status == "completed"


async def test_incomplete_llm_matrix_is_repaired_or_completed_deterministically(llm):
    # El modelo entrega los 3 casos de AC-01 pero nada útil para AC-02: se reintenta ese criterio y, si sigue igual, se completa con plantilla declarada.
    llm.matrix = {"cases": [{"id": f"TC-{t}", "criterion_id": "AC-01", "scenario": "ok", "expected_result": "ok", "type": t} for t in ("positive", "negative", "edge")]}
    engine = engine_for(llm)
    state, _ = await engine.start(request=None, goals=["matrix"], params={"requirement": "Consultar vehículos"}, actor="qa")
    cases = state.artifacts["matrix"].payload["cases"]
    coverage = {(c["criterion_id"], c["type"]) for c in cases}
    assert coverage == {(c, t) for c in ("AC-01", "AC-02") for t in ("positive", "negative", "edge")}
    assert [c["id"] for c in cases if c["criterion_id"] == "AC-01"] == ["TC-AC-01-P", "TC-AC-01-N", "TC-AC-01-E"]
    assert all(c["preconditions"] == ["stock"] for c in cases if c["criterion_id"] == "AC-01")  # sale del "Dado …" del criterio
    assert llm.calls.count("matrix") >= 3  # AC-01 una vez; AC-02 con reintento
    assert any("plantilla determinista para AC-02" in w for w in state.artifacts["matrix"].warnings)


async def test_matrix_over_ten_criteria_fails_without_retrying(llm):
    llm.story = {**STORY, "acceptance_criteria": [{"id": f"AC-{i:02d}", "text": f"Criterio {i}"} for i in range(1, 12)]}
    engine = engine_for(llm)
    state, plan = await engine.start(request=None, goals=["matrix"], params={"requirement": "Consultar vehículos"}, actor="qa")
    failure = state.failures["matrix"]
    assert failure.error_code == "matrix_requires_split" and failure.attempts == 1 and not failure.retryable
    assert engine.sleeps == []


async def test_missing_inputs_are_requested_and_then_used(llm):
    engine = engine_for(llm)
    state, plan = await engine.start(request=None, goals=["automation"], params={"requirement": "Consultar vehículos"}, actor="qa")
    assert plan.status == "needs_input" and plan.missing_inputs == ["framework", "repository"]
    assert any("repositorio" in a["question"] for a in plan.next_actions(state)) and any("stack" in a["question"] for a in plan.next_actions(state))

    state, plan = await engine.request(state.id, request=None, goals=None, params={"repository": "org/qa-automation", "framework": "playwright"}, actor="qa")
    batch = state.artifacts["automation"].payload["batches"][0]
    assert plan.status == "completed" and batch["delivery"] == "pull_request_only" and len(batch["case_ids"]) == 6


async def test_pipeline_is_regenerated_when_scripts_appear(llm):
    engine = engine_for(llm)
    state, _ = await engine.start(request=None, goals=["pipeline"], params={}, actor="devops")
    assert "SucceededWithIssues" in state.artifacts["pipeline"].payload["yaml"]
    state, plan = await engine.request(state.id, request="Genera los scripts de automatización", goals=None, params={"requirement": "Consultar vehículos", "repository": "org/qa", "framework": "playwright"}, actor="qa")
    pipeline = state.artifacts["pipeline"]
    assert pipeline.version == 2 and pipeline.based_on == {"automation": 1}
    assert all(s.startswith("npx playwright test") for s in pipeline.payload["scripts"])


async def test_unavailable_capability_is_reported_not_crashed(llm):
    engine = engine_for(llm)
    state, plan = await engine.start(request=None, goals=["matrix_sync"], params={"requirement": "Consultar vehículos"}, actor="qa")
    assert actions(plan)["matrix_sync"] == "unavailable"
    assert "matrix" not in state.artifacts  # no se genera trabajo que no desbloquea nada


async def test_approving_a_superseded_version_is_rejected(llm):
    engine = engine_for(llm)
    state, _ = await engine.start(request="Redacta la historia de vehículos", goals=["story"], params={}, actor="po")
    with pytest.raises(WorkflowConflict, match="stale_version"):
        await engine.approve(state.id, artifact="story", version=1, content_hash="otro-hash", decision="approved", actor="po")


async def test_state_survives_a_restart_with_sql_store(llm, tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'workflows.db'}"
    state, _ = await engine_for(llm, SqlWorkflowStore(url)).start(request="Redacta la historia y evalúa el riesgo", goals=None, params={}, actor="po")
    restarted = engine_for(llm, SqlWorkflowStore(url))
    state, plan = await restarted.get(state.id)
    assert plan.pending_approvals == ["story"]
    state, plan = await approve_story(restarted, state)
    assert plan.status == "completed" and "risk" in state.artifacts


@pytest.mark.parametrize("make_store", [lambda tmp: InMemoryWorkflowStore(), lambda tmp: SqlWorkflowStore(f"sqlite+pysqlite:///{tmp / 'c.db'}")])
async def test_concurrent_writes_are_detected(make_store, tmp_path):
    store = make_store(tmp_path)
    saved = await store.save(WorkflowState())
    await store.save(saved)
    with pytest.raises(ConcurrentModification):
        await store.save(saved)


async def test_only_suggestions_of_unmet_criteria_need_a_decision(llm):
    # Llama 3.2 suele sugerir mejoras también en criterios que ya cumplen; esas no exigen decisión del PO (HU-002, regla 2).
    llm.invest = {"criteria": [{**c, "suggestion": c["suggestion"] or "Mejora opcional"} for c in INVEST["criteria"]]}
    engine = engine_for(llm)
    state, _ = await engine.start(request=None, goals=["story_revision"], params={"requirement": "Consultar vehículos"}, actor="po")
    invest = state.artifacts["invest"]
    state, plan = await engine.approve(state.id, artifact="invest", version=1, content_hash=invest.content_hash, decision="approved", actor="po", suggestions={"Testeable": "approved"})
    assert state.artifacts["story"].produced_by == "story_revision"
