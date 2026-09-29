from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from valkiria.agents.orchestrator import MultiAgentOrchestrator
from valkiria.agents.registry import build_default_registry
from valkiria.api.errors import ApplicationError
from valkiria.application.automation_execution import (
    PolicyViolation,
    build_evidence_report,
    create_automation_batch,
    execute_database_script,
    export_test_cases_to_excel,
    select_execution_tool,
    suggest_database_tool,
)
from valkiria.application.prompts import token_budget
from valkiria.application.use_cases import ValkiriaService
from valkiria.assistant import ReasoningAssistant, build_toolbox, route_message
from valkiria.assistant.capabilities import capability_summary
from valkiria.assistant.tools import ToolContext
from valkiria.conversation.controller import ConversationController
from valkiria.domain.models import UserStory
from valkiria.infrastructure.execution_memory import (
    InMemoryBatchStore,
    InMemoryExecutionStore,
    InMemoryReportStore,
)
from valkiria.infrastructure.logging import configure_logging, event, get_logger
from valkiria.infrastructure.memory import (
    InMemoryAudit,
    InMemoryMetrics,
    InMemoryStories,
)
from valkiria.infrastructure.playwright_runner import PlaywrightRunner
from valkiria.infrastructure.settings import Settings
from valkiria.infrastructure.synthetic_database import build_synthetic_executor
from valkiria.memory.service import build_memory, describe, recall_prompt
from valkiria.providers.openai_compatible import LLMProviderError, OpenAICompatibleLLM
from valkiria.workflow.engine import (
    WorkflowConflict,
    WorkflowEngine,
    WorkflowNotFound,
    view,
)
from valkiria.workflow.graph import CAPABILITIES, detect_goals
from valkiria.workflow.state import ConcurrentModification, build_workflow_store


def _error_response(code: str, message: str, trace_id: str, status_code: int, details: dict[str, Any] | None = None) -> JSONResponse:
    body: dict[str, Any] = {"error": {"code": code, "message": message, "trace_id": trace_id}}
    if details:
        body["error"]["details"] = details
    return JSONResponse(status_code=status_code, content=body, headers={"X-Trace-Id": trace_id})


# Ubicación del frontend al ejecutar desde el repositorio; en la imagen Docker se define VALKIRIA_FRONTEND_DIR.
DEFAULT_FRONTEND_DIR = Path(__file__).resolve().parents[3] / "frontend"


SESSION_ID = r"^[A-Za-z0-9._:-]{8,64}$"
NAMESPACE = r"^[a-z0-9][a-z0-9._-]{0,63}$"


class AgentExecutionReq(BaseModel):
    # Con session_id, una petición de seguimiento puede ser corta: la sesión aporta el contexto.
    request: str = Field(min_length=3, max_length=20000)
    session_id: str | None = Field(default=None, pattern=SESSION_ID)
    namespace: str = Field(default="default", pattern=NAMESPACE)


class DatabaseExecutionReq(BaseModel):
    engine: str
    environment: str
    profile: str = "synthetic_postgresql"
    script: str = Field(min_length=1, max_length=100000)
    case_id: str = Field(min_length=1, max_length=200)
    output_format: str = "pdf"


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class ChatReq(BaseModel):
    # Un mensaje en lenguaje natural o una acción de la interfaz (aprobar, ejecutar un paso, decidir sugerencias…).
    message: str | None = Field(default=None, max_length=20000)
    action: dict[str, Any] | None = None
    workflow_id: str | None = Field(default=None, max_length=64)
    # Opcional: si se omite y hay session_id, el servidor usa el hilo guardado en la memoria de corto plazo.
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)
    story_id: str | None = None
    session_id: str | None = Field(default=None, pattern=SESSION_ID)
    namespace: str = Field(default="default", pattern=NAMESPACE)


class AssistantReq(BaseModel):
    question: str = Field(min_length=1, max_length=20000)
    session_id: str | None = Field(default=None, pattern=SESSION_ID)
    namespace: str = Field(default="default", pattern=NAMESPACE)


class MemoryRecordReq(BaseModel):
    # Por API solo se registra conocimiento explícito; lo demás se aprende de aprobaciones y ediciones.
    kind: Literal["domain_fact", "lesson"] = "domain_fact"
    content: str = Field(min_length=10, max_length=1200)
    namespace: str = Field(default="default", pattern=NAMESPACE)
    tags: list[str] = Field(default_factory=list, max_length=10)


class FreeReq(BaseModel):
    requirement: str = Field(min_length=10, max_length=20000)


class AutomationBatchReq(BaseModel):
    cases: list[dict[str, Any]] = Field(min_length=1)
    framework: str
    platform: str
    repository: str
    base_branch: str = "main"
    matrix_status: str = "draft"


class DatabaseToolReq(BaseModel):
    engine: str
    language: str = "python"
    environment: str = "qa"


class TestCaseExportReq(BaseModel):
    story_id: str = ""
    cases: list[dict[str, Any]] = Field(min_length=1, max_length=30)


class ToolSelectionReq(BaseModel):
    platform: str
    preferred_tool: str | None = None


class WorkflowStartReq(BaseModel):
    request: str | None = Field(default=None, min_length=3, max_length=20000)
    goals: list[str] | None = Field(default=None, max_length=len(CAPABILITIES))
    params: dict[str, Any] = Field(default_factory=dict)


class WorkflowApprovalReq(BaseModel):
    artifact: str
    version: int = Field(ge=1)
    content_hash: str | None = None
    decision: Literal["approved", "rejected"]
    comment: str = Field(default="", max_length=2000)
    suggestions: dict[str, Literal["approved", "rejected"]] | None = None
    # HU-003B, regla 4: confirma los supuestos del borrador de la HU antes de aprobarla.
    assumptions_confirmed: bool = False


class WorkflowEditReq(BaseModel):
    payload: dict[str, Any]


def _check_goals(goals: list[str] | None) -> None:
    unknown = [goal for goal in goals or [] if goal not in CAPABILITIES]
    if unknown:
        raise HTTPException(422, "unknown_goals:" + ",".join(unknown))


def create_app(llm=None):
    configure_logging()
    logger = get_logger("valkiria.api")
    app = FastAPI(title="Valkiria API", version="0.5.0", description="API auditable para la plataforma multiagente de QA")
    settings = Settings()
    audit = InMemoryAudit()
    metrics = InMemoryMetrics()
    stories = InMemoryStories()
    batches = InMemoryBatchStore()
    executions = InMemoryExecutionStore()
    reports = InMemoryReportStore()
    llm = llm or OpenAICompatibleLLM(settings.llm_base_url, settings.llm_model, settings.secret("llm_api_key"), timeout_seconds=settings.llm_timeout_seconds,
                                     auth_header=settings.llm_auth_header, token_budget=token_budget)
    database_executor = build_synthetic_executor(settings.db_profile, settings.secret("synthetic_database_url")) if settings.mode == "synthetic" else None
    automation_runner = PlaywrightRunner(settings.automation_headless, settings.automation_timeout_seconds) if settings.automation_execute and settings.automation_runner == "playwright" else None
    service = ValkiriaService(llm, audit, metrics, stories)
    memory = build_memory(settings.secret("memory_database_url") or settings.secret("workflow_database_url"), enabled=settings.memory_enabled,
                          max_turns=settings.memory_short_term_turns, ttl_minutes=settings.memory_short_term_ttl_minutes,
                          top_k=settings.memory_long_term_top_k, retention_days=settings.memory_long_term_retention_days)
    workflows = WorkflowEngine(build_workflow_store(settings.secret("workflow_database_url")), service, memory=memory)
    # Peticiones fuera del flujo programado: razonamiento con herramientas y skills reales de Valkiria.
    assistant = ReasoningAssistant(llm, build_toolbox(service=service, memory=memory, workflows=workflows, synthetic_app_base_url=settings.synthetic_app_base_url))
    workflows.assistant = assistant
    # Agente principal de la conversación: sabe en qué paso del flujo va cada sesión (docs/conversacion.md).
    conversation = ConversationController(service=service, workflows=workflows, assistant=assistant, memory=memory)
    orchestrator = MultiAgentOrchestrator(build_default_registry(llm=llm, audit=audit, metrics=metrics, database_executor=database_executor, automation_runner=automation_runner,
                                                                 synthetic_app_base_url=settings.synthetic_app_base_url, assistant=assistant), audit=audit, metrics=metrics, memory=memory)

    async def ask_assistant(question: str, *, actor: str, namespace: str, session_id: str | None, trace_id: str | None, extra: str = ""):
        recalled = await memory.recall(question, task="agent", namespace=namespace)
        session_context = await memory.session_context(session_id)
        context = "\n\n".join(part for part in (recall_prompt(recalled), f"SESIÓN ACTUAL:\n{session_context}" if session_context else "", extra) if part)
        answer = await assistant.ask(question, ctx=ToolContext(actor=actor, namespace=namespace, session_id=session_id, trace_id=trace_id), context=context)
        return answer, recalled

    app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins, allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["Content-Type", "X-Actor", "X-Trace-Id"])

    @app.middleware("http")
    async def trace_request(request: Request, call_next):
        trace_id = request.headers.get("X-Trace-Id", str(uuid4()))
        request.state.trace_id = trace_id
        try:
            response = await call_next(request)
        except Exception:
            event(logger, logging.ERROR, "error_no_controlado", trace_id=trace_id, method=request.method, path=request.url.path)
            raise
        response.headers["X-Trace-Id"] = trace_id
        event(logger, logging.INFO, "solicitud_completada", trace_id=trace_id, method=request.method, path=request.url.path, status=response.status_code)
        return response

    @app.exception_handler(WorkflowNotFound)
    async def workflow_not_found_handler(request: Request, exc: WorkflowNotFound):
        return _error_response("workflow_not_found", "El flujo no existe.", getattr(request.state, "trace_id", str(uuid4())), 404)

    @app.exception_handler(WorkflowConflict)
    async def workflow_conflict_handler(request: Request, exc: WorkflowConflict):
        return _error_response("workflow_conflict", str(exc), getattr(request.state, "trace_id", str(uuid4())), 409)

    @app.exception_handler(ConcurrentModification)
    async def workflow_concurrency_handler(request: Request, exc: ConcurrentModification):
        return _error_response("workflow_concurrent_modification", "Otro proceso actualizó el flujo; vuelve a consultarlo.", getattr(request.state, "trace_id", str(uuid4())), 409)

    @app.exception_handler(ApplicationError)
    async def application_error_handler(request: Request, exc: ApplicationError):
        return _error_response(exc.code, exc.message, getattr(request.state, "trace_id", str(uuid4())), exc.status_code, exc.details)

    @app.exception_handler(PolicyViolation)
    async def policy_error_handler(request: Request, exc: PolicyViolation):
        return _error_response("policy_violation", str(exc), getattr(request.state, "trace_id", str(uuid4())), 400)

    @app.exception_handler(LLMProviderError)
    async def llm_error_handler(request: Request, exc: LLMProviderError):
        return _error_response(exc.code, str(exc), getattr(request.state, "trace_id", str(uuid4())), 503)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        return _error_response("request_validation_error", "La solicitud no cumple el contrato de entrada.", getattr(request.state, "trace_id", str(uuid4())), 422, {"fields": exc.errors()})

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException):
        detail = str(exc.detail) if isinstance(exc.detail, str) else "La solicitud no pudo completarse."
        return _error_response("http_error", detail, getattr(request.state, "trace_id", str(uuid4())), exc.status_code)

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception):
        trace_id = getattr(request.state, "trace_id", str(uuid4()))
        event(logger, logging.ERROR, "excepcion_interna", trace_id=trace_id, error_type=type(exc).__name__)
        return _error_response("internal_error", "Ocurrió un error interno. Consulta el trace_id con el equipo de soporte.", trace_id, 500)

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "valkiria", "mode": settings.mode, "environment": settings.environment, "model": settings.llm_model, "agents": orchestrator.registry.names(), "tools": [t.name for t in assistant.toolbox.all()], "database_profile": settings.db_profile, "release_mode": settings.release_mode, "direct_commit": settings.direct_commit, "automation_execute": settings.automation_execute,
                "memory": {"enabled": memory.enabled, "persistent": memory.persistent}}

    @app.post("/v1/agent/execute")
    async def execute_agent_request(req: AgentExecutionReq, request: Request, x_actor: str = Header(default="anonymous")):
        result = await orchestrator.run(req.request, actor=x_actor, trace_id=request.state.trace_id, session_id=req.session_id, namespace=req.namespace)
        return result.model_dump()

    @app.post("/v1/chat")
    async def chat(req: ChatReq, request: Request, x_actor: str = Header(default="anonymous")):
        trace_id = request.state.trace_id
        session_id = req.session_id or str(uuid4())
        if not (req.message and req.message.strip()) and not req.action:
            raise HTTPException(422, "message_or_action_required")
        try:
            return await conversation.handle(session_id=session_id, message=req.message, action=req.action, actor=x_actor, namespace=req.namespace,
                                             trace_id=trace_id, workflow_id=req.workflow_id)
        except (LLMProviderError, TimeoutError) as exc:
            # RT-04: error claro con trace_id; la conversación y el flujo se conservan y se puede reintentar. La conversación no se rompe.
            event(logger, logging.WARNING, "chat_llm_no_disponible", trace_id=trace_id, error=getattr(exc, "code", type(exc).__name__))
            reply = ("Disculpa, no pude procesar tu mensaje porque el modelo no respondió a tiempo. Tu conversación y la historia en curso se conservan; "
                     f"¿lo intentamos de nuevo? (Referencia: {trace_id})")
        # Frontera de la conversación: ningún error interno debe romper el chat ni exponer detalles.
        except Exception as exc:  # noqa: BLE001
            event(logger, logging.ERROR, "chat_error_interno", trace_id=trace_id, error_type=type(exc).__name__)
            reply = ("Disculpa, tuve un problema interno al procesar tu mensaje. No se perdió nada de lo que llevamos; "
                     f"¿lo intentamos de nuevo o prefieres reformularlo? (Referencia: {trace_id})")
        return {"intent": "error", "reply": reply, "retryable": True, "trace_id": trace_id, "session_id": session_id, "actions": [], "flow": None,
                "story": None, "split": [], "assumptions": [], "changes": None}

    @app.post("/v1/assistant/ask")
    async def assistant_ask(req: AssistantReq, request: Request, x_actor: str = Header(default="anonymous")):
        answer, recalled = await ask_assistant(req.question, actor=x_actor, namespace=req.namespace, session_id=req.session_id, trace_id=request.state.trace_id)
        if req.session_id:
            await memory.add_turn(req.session_id, "user", req.question)
            await memory.add_turn(req.session_id, "assistant", answer.answer, tools=answer.tools_used)
        return answer.model_dump(mode="json") | {"memory": {"recalled": describe(recalled)}}

    @app.get("/v1/assistant/capabilities")
    async def assistant_capabilities():
        return capability_summary(assistant.toolbox) | {"catalogo": [{"name": t.name, "kind": t.kind, "title": t.title, "description": t.description,
                                                                       "params": [{"name": p.name, "type": p.type, "required": p.required, "choices": list(p.choices)} for p in t.params]}
                                                                      for t in assistant.toolbox.all()]}

    @app.get("/v1/memory/sessions/{session_id}")
    async def memory_session(session_id: str):
        session = await memory.session(session_id)
        if not session:
            raise HTTPException(404, "memory_session_not_found")
        return session.model_dump(mode="json")

    @app.delete("/v1/memory/sessions/{session_id}")
    async def forget_session(session_id: str):
        if not await memory.short_term.clear(session_id):
            raise HTTPException(404, "memory_session_not_found")
        return {"deleted": session_id}

    @app.get("/v1/memory/long-term")
    async def long_term_memory(q: str | None = None, namespace: str = "default", kind: str | None = None, limit: int = 50):
        records = await memory.long_term.search(q, namespace=namespace, kinds=[kind] if kind else None, limit=max(1, min(limit, 200)))
        return {"namespace": namespace, "records": [r.model_dump(mode="json") for r in records]}

    @app.post("/v1/memory/long-term", status_code=201)
    async def add_long_term_memory(req: MemoryRecordReq, x_actor: str = Header(default="anonymous")):
        record, created = await memory.long_term.remember(kind=req.kind, content=req.content, source="api:manual", actor=x_actor, namespace=req.namespace, tags=req.tags)
        return {"created": created, "record": record.model_dump(mode="json")}

    @app.delete("/v1/memory/long-term/{record_id}")
    async def forget_long_term_memory(record_id: str):
        if not await memory.long_term.forget(record_id):
            raise HTTPException(404, "memory_record_not_found")
        return {"deleted": record_id}

    @app.post("/v1/stories", response_model=UserStory)
    async def create(req: FreeReq, x_actor: str = Header(default="anonymous")):
        return await service.create_story(req.requirement, x_actor)

    @app.post("/v1/stories/{story_id}/invest")
    async def invest(story_id: str, x_actor: str = Header(default="anonymous")):
        story = await stories.get(story_id)
        if not story:
            raise HTTPException(404, "story_not_found")
        return await service.evaluate_invest(story, x_actor)

    @app.post("/v1/stories/{story_id}/test-matrix")
    async def matrix(story_id: str, x_actor: str = Header(default="anonymous")):
        story = await stories.get(story_id)
        if not story:
            raise HTTPException(404, "story_not_found")
        return await service.generate_matrix(story, x_actor)

    @app.post("/v1/stories/{story_id}/risk")
    async def risk(story_id: str, x_actor: str = Header(default="anonymous")):
        story = await stories.get(story_id)
        if not story:
            raise HTTPException(404, "story_not_found")
        return await service.assess_risk(story, x_actor)

    @app.post("/v1/automation/batches/generate")
    async def generate_automation_batch(req: AutomationBatchReq, x_actor: str = Header(default="anonymous")):
        result = create_automation_batch(cases=req.cases, framework=req.framework, platform=req.platform, repository=req.repository, base_branch=req.base_branch, matrix_status=req.matrix_status)
        if result.get("status") == "requires_split":
            return result
        result["actor"] = x_actor
        await batches.save(result)
        return result

    @app.post("/v1/test-cases/export")
    async def export_test_cases(req: TestCaseExportReq):
        return export_test_cases_to_excel(req.cases, story_id=req.story_id)

    @app.post("/v1/automation/tools/select")
    async def select_automation_tool(req: ToolSelectionReq):
        return select_execution_tool(req.platform, req.preferred_tool)

    @app.get("/v1/automation/batches/{batch_id}")
    async def get_automation_batch(batch_id: str):
        result = await batches.get(batch_id)
        if not result:
            raise HTTPException(404, "automation_batch_not_found")
        return result

    @app.post("/v1/automation/batches/{batch_id}/execute")
    async def execute_automation_batch(batch_id: str, request: Request, x_actor: str = Header(default="anonymous")):
        batch = await batches.get(batch_id)
        if not batch:
            raise HTTPException(404, "automation_batch_not_found")
        if batch.get("execution_tool") != "playwright":
            raise HTTPException(409, "configured_execution_tool_is_not_available_in_this_runner")
        if automation_runner is None:
            raise HTTPException(409, "playwright_runner_not_enabled")
        results = await automation_runner.run(base_url=settings.synthetic_app_base_url, cases=batch.get("cases", []))
        failed = any(item.get("status") == "fail" for item in results)
        execution_id = str(uuid4())
        report = build_evidence_report(
            execution_id=execution_id,
            title="Valkiria · Evidencia de automatización Playwright",
            output_format="pdf",
            fields={
                "batch_id": batch_id,
                "trace_id": request.state.trace_id,
                "tool": "playwright",
                "status": "failed" if failed else "passed",
                "cases": len(results),
            },
            logs=[f"{item['case_id']}={item['status']}" for item in results],
        )
        await reports.save(report)
        result = {"id": execution_id, "batch_id": batch_id, "status": "failed" if failed else "completed", "actor": x_actor, "trace_id": request.state.trace_id, "case_ids": batch["case_ids"], "results": results, "delivery": "pull_request_only", "direct_commit": False, "pr_required": True, "report_generated": True, "report_id": report["id"]}
        await executions.save(result)
        batch["status"] = "executed"
        await batches.save(batch)
        return result

    @app.post("/v1/database/tools/suggest")
    async def database_tools(req: DatabaseToolReq):
        return suggest_database_tool(req.engine, req.language, req.environment)

    @app.post("/v1/database/scripts/execute")
    async def database_execute(req: DatabaseExecutionReq, request: Request, x_actor: str = Header(default="anonymous")):
        if req.environment.lower() == "production" or settings.mode != "synthetic":
            result, report = execute_database_script(engine=req.engine, environment=req.environment, script=req.script, case_id=req.case_id, output_format=req.output_format, actor=x_actor)
        else:
            if req.profile != settings.db_profile:
                raise PolicyViolation("database_profile_not_allowed")
            result, report = database_executor.execute(script=req.script, case_id=req.case_id, trace_id=request.state.trace_id, output_format=req.output_format)
        await executions.save(result)
        if report:
            await reports.save(report)
        return result

    @app.get("/v1/database/executions/{execution_id}")
    async def database_execution(execution_id: str):
        result = await executions.get(execution_id)
        if not result:
            raise HTTPException(404, "database_execution_not_found")
        return result

    @app.get("/v1/reports/{report_id}")
    async def evidence_report(report_id: str):
        report = await reports.get(report_id)
        if not report:
            raise HTTPException(404, "evidence_report_not_found")
        return report

    @app.get("/v1/reports/{report_id}/download")
    async def download_evidence_report(report_id: str):
        report = await reports.get(report_id)
        if not report:
            raise HTTPException(404, "evidence_report_not_found")
        encoding = "latin-1" if report["format"] == "pdf" else "utf-8"
        return Response(
            content=report["content"].encode(encoding),
            media_type=report["media_type"],
            headers={"Content-Disposition": f'attachment; filename="{report["filename"]}"'},
        )

    @app.get("/v1/workflows/capabilities")
    async def workflow_capabilities():
        return {"capabilities": [{"key": c.key, "hu": c.hu, "title": c.title, "requires": [{"artifact": r.artifact, "approved": r.approved} for r in c.requires],
                                  "optional": list(c.optional), "inputs": list(c.inputs), "approvable": c.approvable, "available": c.available} for c in CAPABILITIES.values()]}

    @app.post("/v1/workflows")
    async def start_workflow(req: WorkflowStartReq, request: Request, x_actor: str = Header(default="anonymous")):
        _check_goals(req.goals)
        if not req.request and not req.goals:
            raise HTTPException(422, "request_or_goals_required")
        if req.request and not req.goals and not detect_goals(req.request) and route_message(req.request) == "assistant":
            # No es trabajo del flujo: se responde con herramientas en lugar de forzar una HU.
            answer, _ = await ask_assistant(req.request, actor=x_actor, namespace=str(req.params.get("namespace") or "default"), session_id=None, trace_id=request.state.trace_id)
            return {"id": None, "status": answer.status, "answer": answer.model_dump(mode="json")}
        state, current = await workflows.start(request=req.request, goals=req.goals, params=req.params, actor=x_actor, trace_id=request.state.trace_id)
        return view(state, current)

    @app.get("/v1/workflows/{workflow_id}")
    async def get_workflow(workflow_id: str):
        return view(*await workflows.get(workflow_id))

    @app.post("/v1/workflows/{workflow_id}/requests")
    async def continue_workflow(workflow_id: str, req: WorkflowStartReq, x_actor: str = Header(default="anonymous")):
        _check_goals(req.goals)
        return view(*await workflows.request(workflow_id, request=req.request, goals=req.goals, params=req.params, actor=x_actor))

    @app.post("/v1/workflows/{workflow_id}/approvals")
    async def approve_workflow_artifact(workflow_id: str, req: WorkflowApprovalReq, x_actor: str = Header(default="anonymous")):
        return view(*await workflows.approve(workflow_id, artifact=req.artifact, version=req.version, content_hash=req.content_hash, decision=req.decision,
                                             actor=x_actor, comment=req.comment, suggestions=req.suggestions, assumptions_confirmed=req.assumptions_confirmed))

    @app.put("/v1/workflows/{workflow_id}/artifacts/{artifact}")
    async def edit_workflow_artifact(workflow_id: str, artifact: str, req: WorkflowEditReq, x_actor: str = Header(default="anonymous")):
        return view(*await workflows.edit(workflow_id, artifact=artifact, payload=req.payload, actor=x_actor))

    @app.post("/v1/workflows/{workflow_id}/resume")
    async def resume_workflow(workflow_id: str):
        return view(*await workflows.resume(workflow_id))

    @app.get("/v1/audit")
    async def audit_log():
        return {"events": [e.model_dump(mode="json") for e in audit.events]}

    @app.get("/v1/metrics")
    async def metric_log():
        return {"metrics": [m.model_dump(mode="json") for m in metrics.items]}

    # Sirve el frontend desde el mismo origen que la API: sin puertos ni CORS adicionales.
    frontend_dir = Path(settings.frontend_dir) if settings.frontend_dir else DEFAULT_FRONTEND_DIR
    frontend_index = frontend_dir / "index.html"
    if frontend_index.is_file():
        @app.get("/", include_in_schema=False)
        async def frontend():
            return FileResponse(frontend_index)

        if (frontend_dir / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=frontend_dir / "assets"), name="assets")

    return app
