from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Raíz del repositorio cuando se ejecuta desde el código fuente; en una instalación
# empaquetada el archivo no existe y pydantic-settings lo ignora sin error.
PROJECT_ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    """Configuración segura; los requests nunca reciben DSN ni credenciales.

    Precedencia: variables de entorno > `.env` del directorio actual > `.env` de la raíz del repo > valores por defecto.
    """

    model_config = SettingsConfigDict(
        env_prefix="VALKIRIA_",
        env_file=(PROJECT_ROOT_ENV, ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_base_url: str = "http://localhost:11434/v1"
    llm_model: str = "qwen2.5:7b"
    llm_api_key: SecretStr | None = None
    allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:3000"])
    mode: str = "synthetic"
    environment: str = "qa"
    allow_production: bool = False
    db_profile: str = "synthetic_postgresql"
    db_engine: str = "postgresql"
    synthetic_database_url: SecretStr | None = None
    automation_runner: str = "playwright"
    automation_execute: bool = False
    automation_headless: bool = True
    automation_timeout_seconds: int = Field(default=30, gt=0, le=600)
    release_mode: str = "preview"
    direct_commit: bool = False
    pr_required: bool = True
    synthetic_app_base_url: str = "http://localhost:8090"
    # Vacío: usa `frontend/` del repositorio. En contenedores apunta a la copia empaquetada.
    frontend_dir: str = ""

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("llm_api_key", "synthetic_database_url", mode="before")
    @classmethod
    def _empty_as_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def _block_production(self) -> Settings:
        if self.environment.lower() == "production" and not self.allow_production:
            raise ValueError("VALKIRIA_ENVIRONMENT=production requiere VALKIRIA_ALLOW_PRODUCTION=true")
        return self

    def secret(self, name: str) -> str | None:
        value = getattr(self, name)
        return value.get_secret_value() if value is not None else None
