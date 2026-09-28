import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from valkiria.infrastructure.settings import Settings


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    # Aísla las pruebas de cualquier .env local y de variables VALKIRIA_* del entorno.
    monkeypatch.chdir(tmp_path)
    for name in list(os.environ):
        if name.startswith("VALKIRIA_"):
            monkeypatch.delenv(name)


def build(**kwargs):
    return Settings(_env_file=None, **kwargs)


def test_safe_defaults():
    settings = build()
    assert settings.mode == "synthetic"
    assert settings.allow_production is False
    assert settings.direct_commit is False
    assert settings.secret("synthetic_database_url") is None


def test_environment_variables_are_read_at_instantiation(monkeypatch):
    monkeypatch.setenv("VALKIRIA_LLM_MODEL", "modelo-prueba")
    monkeypatch.setenv("VALKIRIA_AUTOMATION_EXECUTE", "true")
    monkeypatch.setenv("VALKIRIA_ALLOWED_ORIGINS", "http://a.test, http://b.test")
    settings = build()
    assert settings.llm_model == "modelo-prueba"
    assert settings.automation_execute is True
    assert settings.allowed_origins == ["http://a.test", "http://b.test"]


def test_env_file_is_loaded_and_environment_wins(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("VALKIRIA_LLM_MODEL=desde-archivo\nVALKIRIA_RELEASE_MODE=desde-archivo\n", encoding="utf-8")
    monkeypatch.setenv("VALKIRIA_RELEASE_MODE", "desde-entorno")
    settings = Settings(_env_file=env_file)
    assert settings.llm_model == "desde-archivo"
    assert settings.release_mode == "desde-entorno"


def test_secrets_are_masked_in_repr(monkeypatch):
    monkeypatch.setenv("VALKIRIA_SYNTHETIC_DATABASE_URL", "postgresql+psycopg://u:clave-secreta@db:5432/x")
    settings = build()
    assert "clave-secreta" not in repr(settings)
    assert settings.secret("synthetic_database_url").endswith("@db:5432/x")


def test_empty_secret_is_treated_as_unset(monkeypatch):
    monkeypatch.setenv("VALKIRIA_LLM_API_KEY", "")
    assert build().secret("llm_api_key") is None


def test_production_requires_explicit_opt_in():
    with pytest.raises(ValidationError):
        build(environment="production")
    assert build(environment="production", allow_production=True).environment == "production"


def test_env_example_matches_settings_fields():
    example = Path(__file__).resolve().parents[1] / ".env.example"
    keys = {line.split("=", 1)[0].removeprefix("VALKIRIA_").lower() for line in example.read_text(encoding="utf-8").splitlines() if line.startswith("VALKIRIA_")}
    assert keys == set(Settings.model_fields)
