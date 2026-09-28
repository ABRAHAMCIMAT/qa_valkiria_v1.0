from valkiria.api.errors import ResourceNotFoundError
from valkiria.infrastructure.logging import _safe


def test_error_tipado_no_expone_secretos():
    error = ResourceNotFoundError("historia")
    assert error.status_code == 404
    assert "historia_not_found" == error.code
    assert "secret" not in str(error).lower()


def test_redaccion_recursiva_de_contexto_sensible():
    value = _safe({"token": "abc", "nested": {"password": "xyz"}, "trace_id": "t-1"})
    assert value["token"] == "[REDACTADO]"
    assert value["nested"]["password"] == "[REDACTADO]"
    assert value["trace_id"] == "t-1"
