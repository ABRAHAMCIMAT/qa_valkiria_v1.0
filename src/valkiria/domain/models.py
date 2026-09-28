from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


def now() -> datetime:
    return datetime.now(UTC)


class Status(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHED = "published"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CaseType(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    EDGE = "edge"


class ArtifactType(str, Enum):
    STORY = "story"
    INVEST = "invest"
    TEST_MATRIX = "test_matrix"
    RISK = "risk"
    PIPELINE = "pipeline"
    PERFORMANCE = "performance"
    AUTOMATION = "automation"


class AcceptanceCriterion(BaseModel):
    id: str
    text: str = Field(min_length=1, max_length=5000)


class UserStory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=3, max_length=300)
    description: str = Field(min_length=1, max_length=10000)
    business_rules: list[str] = Field(default_factory=list, max_length=30)
    acceptance_criteria: list[AcceptanceCriterion] = Field(min_length=1, max_length=30)
    status: Status = Status.DRAFT
    version: int = 1
    generated_by_ai: bool = True


class InvestCriterion(BaseModel):
    name: str
    status: str
    justification: str
    suggestion: str | None = None


class InvestEvaluation(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    story_id: UUID
    criteria: list[InvestCriterion] = Field(min_length=6, max_length=6)
    model: str
    prompt_version: str
    created_at: datetime = Field(default_factory=now)


class TestCase(BaseModel):
    __test__ = False  # Modelo de dominio, no una clase de pytest.

    id: str
    criterion_id: str
    scenario: str
    preconditions: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    expected_result: str
    priority: str = "medium"
    type: CaseType


class TestMatrix(BaseModel):
    __test__ = False  # Modelo de dominio, no una clase de pytest.

    id: UUID = Field(default_factory=uuid4)
    story_id: UUID
    cases: list[TestCase] = Field(max_length=30)
    status: Status = Status.DRAFT


class RiskAssessment(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    story_id: UUID
    level: RiskLevel
    justification: str
    mitigation: str
    defect_history_considered: bool = False
    execution_order: int = 0


class AuditEvent(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    trace_id: str
    actor: str
    action: str
    artifact_type: ArtifactType | None = None
    artifact_id: str | None = None
    version: int | None = None
    outcome: str
    timestamp: datetime = Field(default_factory=now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Metric(BaseModel):
    name: str
    value: float
    unit: str
    trace_id: str
    timestamp: datetime = Field(default_factory=now)
