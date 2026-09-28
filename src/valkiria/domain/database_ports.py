from __future__ import annotations

from typing import Any, Protocol


class DatabaseExecutor(Protocol):
    """Puerto para ejecutar scripts únicamente contra perfiles autorizados."""

    engine: str

    def execute(self, *, script: str, case_id: str, trace_id: str, output_format: str = "pdf") -> tuple[dict[str, Any], dict[str, Any] | None]:
        ...
