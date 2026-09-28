"""Errores de aplicación y respuestas HTTP uniformes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ApplicationError(Exception):
    code: str
    message: str
    status_code: int = 400
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message


class ResourceNotFoundError(ApplicationError):
    def __init__(self, resource: str):
        super().__init__(f"{resource}_not_found", f"No se encontró el recurso: {resource}.", 404)


class ExternalServiceError(ApplicationError):
    def __init__(self, service: str):
        super().__init__("external_service_unavailable", f"El servicio externo {service} no está disponible.", 503)
