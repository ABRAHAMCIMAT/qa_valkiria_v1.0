from __future__ import annotations

import json
from typing import Any

import httpx

from valkiria.infrastructure.logging import event, get_logger


class LLMProviderError(RuntimeError):
    """Error técnico sin exposición de credenciales ni cuerpo completo del proveedor."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class OpenAICompatibleLLM:
    def __init__(self, base_url: str, model_name: str, api_key: str | None = None, timeout_seconds: float = 90):
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.logger = get_logger("valkiria.llm")

    async def generate_json(self, *, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        payload = {"model": self.model_name, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], "temperature": 0.1, "response_format": {"type": "json_object"}}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
                response.raise_for_status()
                body = response.json()
                content = body["choices"][0]["message"]["content"]
                result = content if isinstance(content, dict) else json.loads(content)
                if not isinstance(result, dict):
                    raise TypeError("La respuesta del LLM no es un objeto JSON.")
                return result
        except httpx.TimeoutException as exc:
            event(self.logger, 40, "timeout_llm", model=self.model_name)
            raise LLMProviderError("llm_timeout", "El modelo LLM excedió el tiempo permitido.") from exc
        except httpx.HTTPStatusError as exc:
            event(self.logger, 40, "error_http_llm", model=self.model_name, status=exc.response.status_code)
            raise LLMProviderError("llm_http_error", "El proveedor LLM devolvió un error HTTP.") from exc
        except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            event(self.logger, 40, "respuesta_llm_invalida", model=self.model_name, error_type=type(exc).__name__)
            raise LLMProviderError("llm_invalid_response", "El proveedor LLM devolvió una respuesta inválida.") from exc


class LlamaProvider(OpenAICompatibleLLM):
    pass


class DeepSeekProvider(OpenAICompatibleLLM):
    pass


class QwenProvider(OpenAICompatibleLLM):
    pass
