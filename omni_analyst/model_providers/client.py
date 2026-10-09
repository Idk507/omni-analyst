from __future__ import annotations

import os
from typing import Any, Dict, List

import httpx

from omni_analyst.config.env import load_env_file
from omni_analyst.model_providers.registry import ModelProviderRegistry
from omni_analyst.models.contracts import (
    ModelMessage,
    ModelProviderConfig,
    ModelProviderKind,
    ModelRequest,
    ModelResponse,
)


class ModelProviderError(RuntimeError):
    pass


class ModelProviderClient:
    """Provider-neutral chat completion client.

    Uses direct HTTP APIs where practical and avoids hard dependencies on provider SDKs.
    """

    def __init__(
        self,
        registry: ModelProviderRegistry,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.registry = registry
        self.transport = transport
        self.timeout_seconds = timeout_seconds

    def invoke(self, request: ModelRequest) -> ModelResponse:
        load_env_file()
        config = self.registry.get(request.provider_id)
        if not config.enabled:
            raise ModelProviderError(f"Model provider disabled: {request.provider_id}")
        if config.kind == ModelProviderKind.LOCAL_ECHO:
            return self._invoke_echo(config, request)
        if config.kind in {
            ModelProviderKind.OPENAI,
            ModelProviderKind.OPENAI_COMPATIBLE,
        }:
            return self._invoke_openai_compatible(config, request)
        if config.kind == ModelProviderKind.AZURE_OPENAI:
            return self._invoke_azure_openai(config, request)
        if config.kind == ModelProviderKind.ANTHROPIC:
            return self._invoke_anthropic(config, request)
        if config.kind == ModelProviderKind.GOOGLE_GEMINI:
            return self._invoke_gemini(config, request)
        if config.kind == ModelProviderKind.HUGGINGFACE:
            return self._invoke_huggingface(config, request)
        if config.kind == ModelProviderKind.OLLAMA:
            return self._invoke_ollama(config, request)
        if config.kind == ModelProviderKind.AWS_BEDROCK:
            return self._invoke_bedrock(config, request)
        raise ModelProviderError(f"Unsupported provider kind: {config.kind}")

    def _invoke_echo(
        self, config: ModelProviderConfig, request: ModelRequest
    ) -> ModelResponse:
        content = "\n".join(f"{message.role}: {message.content}" for message in request.messages)
        return ModelResponse(
            provider_id=config.provider_id,
            model=request.model or config.default_model,
            content=content,
            raw={"echo": True},
            usage={"prompt_messages": len(request.messages)},
        )

    def _invoke_openai_compatible(
        self, config: ModelProviderConfig, request: ModelRequest
    ) -> ModelResponse:
        model = request.model or config.default_model
        payload = {
            "model": model,
            "messages": [message.model_dump() for message in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        response = self._post_json(
            f"{self._base_url(config).rstrip('/')}/chat/completions",
            payload,
            headers=self._auth_headers(config, bearer=True),
        )
        content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
        return ModelResponse(
            provider_id=config.provider_id,
            model=model,
            content=content,
            raw=response,
            usage=response.get("usage", {}),
        )

    def _invoke_azure_openai(
        self, config: ModelProviderConfig, request: ModelRequest
    ) -> ModelResponse:
        endpoint = os.getenv(str(config.options.get("endpoint_env", "")), config.base_url or "")
        deployment = os.getenv(str(config.options.get("deployment_env", "")), request.model or config.default_model)
        api_version = os.getenv(
            str(config.options.get("api_version_env", "")),
            str(config.options.get("api_version", "2024-10-21")),
        )
        if not endpoint:
            raise ModelProviderError("Azure OpenAI endpoint is not configured")
        payload = {
            "messages": [message.model_dump() for message in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        response = self._post_json(
            f"{endpoint.rstrip('/')}/openai/deployments/{deployment}/chat/completions?api-version={api_version}",
            payload,
            headers=self._auth_headers(config, azure=True),
        )
        content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
        return ModelResponse(
            provider_id=config.provider_id,
            model=deployment,
            content=content,
            raw=response,
            usage=response.get("usage", {}),
        )

    def _invoke_anthropic(
        self, config: ModelProviderConfig, request: ModelRequest
    ) -> ModelResponse:
        model = request.model or config.default_model
        system = "\n".join(message.content for message in request.messages if message.role == "system")
        messages = [
            {"role": "assistant" if message.role == "assistant" else "user", "content": message.content}
            for message in request.messages
            if message.role != "system"
        ]
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        if system:
            payload["system"] = system
        headers = self._auth_headers(config)
        headers["anthropic-version"] = "2023-06-01"
        response = self._post_json(f"{self._base_url(config).rstrip('/')}/messages", payload, headers=headers)
        content = "".join(block.get("text", "") for block in response.get("content", []))
        return ModelResponse(provider_id=config.provider_id, model=model, content=content, raw=response, usage=response.get("usage", {}))

    def _invoke_gemini(
        self, config: ModelProviderConfig, request: ModelRequest
    ) -> ModelResponse:
        model = request.model or config.default_model
        api_key = self._api_key(config)
        if not api_key:
            raise ModelProviderError(f"Missing API key env var: {config.api_key_env}")
        contents = [
            {
                "role": "model" if message.role == "assistant" else "user",
                "parts": [{"text": message.content}],
            }
            for message in request.messages
            if message.role != "system"
        ]
        system_text = "\n".join(message.content for message in request.messages if message.role == "system")
        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_tokens,
            },
        }
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}
        response = self._post_json(
            f"{self._base_url(config).rstrip('/')}/models/{model}:generateContent?key={api_key}",
            payload,
            headers=config.headers,
        )
        candidates = response.get("candidates", [])
        parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
        content = "".join(part.get("text", "") for part in parts)
        return ModelResponse(provider_id=config.provider_id, model=model, content=content, raw=response)

    def _invoke_huggingface(
        self, config: ModelProviderConfig, request: ModelRequest
    ) -> ModelResponse:
        model = request.model or config.default_model
        prompt = self._messages_to_prompt(request.messages)
        response = self._post_json(
            f"{self._base_url(config).rstrip('/')}/{model}",
            {"inputs": prompt, "parameters": {"temperature": request.temperature, "max_new_tokens": request.max_tokens}},
            headers=self._auth_headers(config, bearer=True),
        )
        raw = {"response": response}
        if isinstance(response, list) and response:
            content = response[0].get("generated_text", "")
        else:
            content = response.get("generated_text", "") if isinstance(response, dict) else str(response)
        return ModelResponse(provider_id=config.provider_id, model=model, content=content, raw=raw)

    def _invoke_ollama(
        self, config: ModelProviderConfig, request: ModelRequest
    ) -> ModelResponse:
        model = request.model or config.default_model
        response = self._post_json(
            f"{self._base_url(config).rstrip('/')}/api/chat",
            {
                "model": model,
                "messages": [message.model_dump() for message in request.messages],
                "stream": False,
                "options": {"temperature": request.temperature, "num_predict": request.max_tokens},
            },
            headers=config.headers,
        )
        return ModelResponse(
            provider_id=config.provider_id,
            model=model,
            content=response.get("message", {}).get("content", ""),
            raw=response,
        )

    def _invoke_bedrock(
        self, config: ModelProviderConfig, request: ModelRequest
    ) -> ModelResponse:
        try:
            import boto3  # noqa: F401
        except Exception as exc:
            raise ModelProviderError(
                "AWS Bedrock requires boto3 and AWS credentials; install boto3 and configure AWS auth."
            ) from exc
        raise ModelProviderError("AWS Bedrock adapter boundary is configured but not executed in this scaffold.")

    def _post_json(self, url: str, payload: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
        with httpx.Client(transport=self.transport, timeout=self.timeout_seconds) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()

    def _auth_headers(
        self,
        config: ModelProviderConfig,
        *,
        bearer: bool = False,
        azure: bool = False,
    ) -> Dict[str, str]:
        headers = dict(config.headers)
        api_key = self._api_key(config)
        if azure and api_key:
            headers["api-key"] = api_key
        elif bearer and api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        elif api_key:
            headers["x-api-key"] = api_key
        headers.setdefault("content-type", "application/json")
        return headers

    def _api_key(self, config: ModelProviderConfig) -> str:
        return os.getenv(config.api_key_env or "", "")

    def _base_url(self, config: ModelProviderConfig) -> str:
        if not config.base_url:
            raise ModelProviderError(f"Provider {config.provider_id} has no base_url")
        return config.base_url

    def _messages_to_prompt(self, messages: List[ModelMessage]) -> str:
        return "\n".join(f"{message.role}: {message.content}" for message in messages)
