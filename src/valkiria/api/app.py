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
from valkiria.application.use_cases import ValkiriaService
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
from valkiria.providers.openai_compatible import LLMProviderError, OpenAICompatibleLLM


def _error_response(code: str, message: str, trace_id: str, status_code: int, details: dict[str, Any] | None = None) -> JSONResponse:
    body: dict[str, Any] = {"error": {"code": code, "message": message, "trace_id": trace_id}}
    if details:
        body["error"]["details"] = details
    return JSONResponse(status_code=status_code, content=body, headers={"X-Trace-Id": trace_id})


FRONTEND_INDEX = Path(__file__).resolve().parents[3] / "frontend" / "index.html"


class AgentExecutionReq(BaseModel):
    request: str = Field(min_length=10, max_length=20000)


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
    message: str = Field(min_length=1, max_length=20000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)
    story_id: str | None = None


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


def create_app():
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
    llm = OpenAICompatibleLLM(settings.llm_base_url, settings.llm_model, settings.llm_api_key)
    database_executor = build_synthetic_executor(settings.db_profile, settings.synthetic_database_url) if settings.mode == "synthetic" else None
    automation_runner = PlaywrightRunner(settings.automation_headless, settings.automation_timeout_seconds) if settings.automation_execute and settings.automation_runner == "playwright" else None
    service = ValkiriaService(llm, audit, metrics, stories)
    orchestrator = MultiAgentOrchestrator(build_default_registry(llm=llm, audit=audit, metrics=metrics, database_executor=database_executor, automation_runner=automation_runner, synthetic_app_base_url=settings.synthetic_app_base_url), audit=audit, metrics=metrics)

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
        return {"status": "ok", "service": "valkiria", "mode": settings.mode, "environment": settings.environment, "model": settings.llm_model, "agents": ["intake", "grounding", "generation", "evaluation", "database", "automation", "approval", "release", "operations"], "database_profile": settings.db_profile, "release_mode": settings.release_mode, "direct_commit": settings.direct_commit, "automation_execute": settings.automation_execute}

    @app.post("/v1/agent/execute")
    async def execute_agent_request(req: AgentExecutionReq, request: Request, x_actor: str = Header(default="anonymous")):
        result = await orchestrator.run(req.request, actor=x_actor, trace_id=request.state.trace_id)
        return result.model_dump()

    @app.post("/v1/chat")
    async def chat(req: ChatReq, x_actor: str = Header(default="anonymous")):
        current = await stories.get(req.story_id) if req.story_id else None
        return await service.converse(req.message, [t.model_dump() for t in req.history], current, x_actor)

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

    @app.get("/v1/audit")
    async def audit_log():
        return {"events": [e.model_dump(mode="json") for e in audit.events]}

    @app.get("/v1/metrics")
    async def metric_log():
        return {"metrics": [m.model_dump(mode="json") for m in metrics.items]}

    # Sirve el frontend desde el mismo origen que la API: sin puertos ni CORS adicionales.
    if FRONTEND_INDEX.is_file():
        @app.get("/", include_in_schema=False)
        async def frontend():
            return FileResponse(FRONTEND_INDEX)

        if (FRONTEND_INDEX.parent / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=FRONTEND_INDEX.parent / "assets"), name="assets")

    return app
