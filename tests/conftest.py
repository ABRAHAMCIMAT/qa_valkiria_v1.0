import pytest

from valkiria.infrastructure.settings import Settings


@pytest.fixture(autouse=True)
def ignore_local_dotenv(request, monkeypatch):
    """Las unitarias no dependen del .env de cada máquina; la E2E sí puede usarlo."""
    if "e2e" not in request.node.path.parts:
        monkeypatch.setitem(Settings.model_config, "env_file", None)
