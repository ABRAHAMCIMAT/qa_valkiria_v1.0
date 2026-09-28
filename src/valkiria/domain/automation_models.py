from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4
from pydantic import BaseModel, Field

class AutomationBatch(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    framework: str
    platform: str
    repository: str
    base_branch: str = "main"
    matrix_status: str
    case_ids: list[str] = Field(min_length=1, max_length=15)
    scripts: dict[str, str] = {}
    traceability: bool = True
    pattern: str = "page-object-model"
    data_externalized: bool = True
    delivery: str = "pull_request_only"
    direct_commit: bool = False
    pr_required: bool = True
    status: str = "draft"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class DatabaseExecution(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    engine: str
    environment: str
    case_id: str
    status: str
    blocked: bool
    static_analysis: dict[str, Any] = {}
    logs: list[str] = []
    report_id: UUID | None = None
    report_generated: bool = False
    actor: str = "anonymous"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
