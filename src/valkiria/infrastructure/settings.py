from __future__ import annotations

import os

from pydantic import BaseModel, Field


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Settings(BaseModel):
    """Configuración segura; los requests nunca reciben DSN ni credenciales."""

    llm_base_url: str = os.getenv("VALKIRIA_LLM_BASE_URL", "http://localhost:11434/v1")
    llm_model: str = os.getenv("VALKIRIA_LLM_MODEL", "qwen2.5:7b")
    llm_api_key: str | None = os.getenv("VALKIRIA_LLM_API_KEY")
    allowed_origins: list[str] = Field(default_factory=lambda: os.getenv("VALKIRIA_ALLOWED_ORIGINS", "http://localhost:3000").split(","))
    mode: str = os.getenv("VALKIRIA_MODE", "synthetic")
    environment: str = os.getenv("VALKIRIA_ENVIRONMENT", "qa")
    allow_production: bool = _env_bool("VALKIRIA_ALLOW_PRODUCTION", False)
    db_profile: str = os.getenv("VALKIRIA_DB_PROFILE", "synthetic_postgresql")
    db_engine: str = os.getenv("VALKIRIA_DB_ENGINE", "postgresql")
    synthetic_database_url: str | None = os.getenv("VALKIRIA_SYNTHETIC_DATABASE_URL")
    automation_runner: str = os.getenv("VALKIRIA_AUTOMATION_RUNNER", "playwright")
    automation_execute: bool = _env_bool("VALKIRIA_AUTOMATION_EXECUTE", False)
    automation_headless: bool = _env_bool("VALKIRIA_AUTOMATION_HEADLESS", True)
    automation_timeout_seconds: int = int(os.getenv("VALKIRIA_AUTOMATION_TIMEOUT_SECONDS", "30"))
    release_mode: str = os.getenv("VALKIRIA_RELEASE_MODE", "preview")
    direct_commit: bool = _env_bool("VALKIRIA_DIRECT_COMMIT", False)
    pr_required: bool = _env_bool("VALKIRIA_PR_REQUIRED", True)
    synthetic_app_base_url: str = os.getenv("VALKIRIA_SYNTHETIC_APP_BASE_URL", "http://localhost:8090")
