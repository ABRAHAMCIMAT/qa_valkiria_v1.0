import httpx
import pytest

from valkiria.providers.openai_compatible import OpenAICompatibleLLM


def make_llm(monkeypatch, captured, **kwargs):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(url=str(request.url), headers=dict(request.headers))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{\"ok\": true}"}}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    return OpenAICompatibleLLM(**kwargs)


async def test_bearer_header_by_default(monkeypatch):
    captured = {}
    llm = make_llm(monkeypatch, captured, base_url="http://ollama.test/v1", model_name="m", api_key="k1")
    assert await llm.generate_json(system="s", user="u", schema={}) == {"ok": True}
    assert captured["headers"]["authorization"] == "Bearer k1"
    assert "api-key" not in captured["headers"]


async def test_azure_openai_api_key_header(monkeypatch):
    captured = {}
    llm = make_llm(monkeypatch, captured, base_url="https://valkiria.openai.azure.com/openai/v1/", model_name="gpt-deployment", api_key="k2", auth_header="api-key")
    await llm.generate_json(system="s", user="u", schema={})
    assert captured["url"] == "https://valkiria.openai.azure.com/openai/v1/chat/completions"
    assert captured["headers"]["api-key"] == "k2"
    assert "authorization" not in captured["headers"]


@pytest.mark.parametrize("auth_header", ["authorization", "api-key"])
async def test_no_credentials_sent_without_key(monkeypatch, auth_header):
    captured = {}
    llm = make_llm(monkeypatch, captured, base_url="http://ollama.test/v1", model_name="m", auth_header=auth_header)
    await llm.generate_json(system="s", user="u", schema={})
    assert "authorization" not in captured["headers"] and "api-key" not in captured["headers"]
