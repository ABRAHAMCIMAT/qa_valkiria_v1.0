from dataclasses import dataclass
from typing import Any
@dataclass
class CloudAdapter:
    provider: str
    async def provision_isolated_performance_job(self, config: dict[str, Any]) -> dict[str, Any]:
        # Reference implementation: production adapters must enforce quotas, private networking and TTL.
        return {'provider':self.provider,'status':'planned','isolation':'private-network','limits':config.get('limits',{}),'ttl_minutes':config.get('ttl_minutes',60)}
AWS=CloudAdapter('aws'); AZURE=CloudAdapter('azure'); GCP=CloudAdapter('gcp'); IBM=CloudAdapter('ibm')
