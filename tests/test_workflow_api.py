from fastapi.testclient import TestClient

from valkiria.api.app import create_app
from workflow_fakes import ScriptedLLM


def client():
    return TestClient(create_app(llm=ScriptedLLM()))


def test_workflow_lifecycle_over_http():
    api = client()
    started = api.post("/v1/workflows", json={"request": "Redacta la historia de vehículos y evalúa el riesgo"}, headers={"X-Actor": "po"})
    assert started.status_code == 200
    body = started.json()
    assert body["status"] == "waiting_approval"
    approval = body["next_actions"][0]
    assert approval["type"] == "approve" and approval["artifact"] == "story"

    approved = api.post(f"/v1/workflows/{body['id']}/approvals", json={"artifact": "story", "version": approval["version"], "content_hash": approval["content_hash"], "decision": "approved"}, headers={"X-Actor": "po"})
    assert approved.json()["status"] == "completed"
    assert api.get(f"/v1/workflows/{body['id']}").json()["artifacts"]["risk"]["based_on"] == {"story": 1}


def test_workflow_errors_are_uniform():
    api = client()
    assert api.post("/v1/workflows", json={"goals": ["no_existe"]}).status_code == 422
    assert api.get("/v1/workflows/desconocido").json()["error"]["code"] == "workflow_not_found"
    body = api.post("/v1/workflows", json={"request": "Redacta la historia de vehículos", "goals": ["story"]}).json()
    stale = api.post(f"/v1/workflows/{body['id']}/approvals", json={"artifact": "story", "version": 9, "decision": "approved"})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "workflow_conflict"


def test_capabilities_expose_dependency_graph():
    capabilities = {c["key"]: c for c in client().get("/v1/workflows/capabilities").json()["capabilities"]}
    assert capabilities["risk"]["requires"] == [{"artifact": "story", "approved": True}]
    assert capabilities["matrix_sync"]["available"] is False
