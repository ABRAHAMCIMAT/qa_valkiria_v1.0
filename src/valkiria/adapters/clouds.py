from dataclasses import dataclass
from typing import Any


@dataclass
class CloudAdapter:
    provider: str

    async def provision_isolated_performance_job(self, config: dict[str, Any]) -> dict[str, Any]:
        # Implementación de referencia: el adaptador real usa Azure Load Testing en un grupo de recursos aislado,
        # con cuotas, red privada y TTL (ver HU-008B).
        return {"provider": self.provider, "status": "planned", "service": "azure-load-testing", "isolation": "dedicated-resource-group", "limits": config.get("limits", {}), "ttl_minutes": config.get("ttl_minutes", 60)}


# Azure es la única plataforma de despliegue soportada.
AZURE = CloudAdapter("azure")
