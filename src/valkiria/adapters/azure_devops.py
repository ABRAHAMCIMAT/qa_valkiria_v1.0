from typing import Any
class SafeAzureDevOpsAdapter:
    """Port boundary. Implement with project-scoped OAuth/PAT from a secret manager; never log tokens."""
    async def publish_story(self, story, *, project: str, mapping: dict[str,str], approval_id: str) -> dict[str,Any]:
        if not approval_id: raise ValueError('explicit_approval_required')
        return {'status':'preview','project':project,'work_item_type':'User Story','fields':{mapping.get('title','System.Title'):story.title},'requires_commit_approval':True}
    async def validate_pipeline(self, yaml_text: str) -> dict[str,Any]:
        return {'valid':bool(yaml_text.strip()),'automatic_commit':False}
