from __future__ import annotations

from threading import RLock
from typing import List

from omni_analyst.config.env import load_env_file
from omni_analyst.models.contracts import ModelProviderConfig, ModelProviderKind


class ModelProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, ModelProviderConfig] = {}
        self._lock = RLock()

    def register(self, config: ModelProviderConfig) -> ModelProviderConfig:
        with self._lock:
            self._providers[config.provider_id] = config
            return config

    def get(self, provider_id: str) -> ModelProviderConfig:
        with self._lock:
            if provider_id not in self._providers:
                raise KeyError(f"Unknown model provider: {provider_id}")
            return self._providers[provider_id]

    def list(self) -> List[ModelProviderConfig]:
        with self._lock:
            return list(self._providers.values())


def build_default_model_provider_registry() -> ModelProviderRegistry:
    load_env_file()
    registry = ModelProviderRegistry()
    for config in [
        ModelProviderConfig(
            provider_id="openai",
            kind=ModelProviderKind.OPENAI,
            display_name="OpenAI",
            default_model="gpt-4o-mini",
            base_url="https://api.openai.com/v1",
            api_key_env="OPENAI_API_KEY",
        ),
        ModelProviderConfig(
            provider_id="azure_openai",
            kind=ModelProviderKind.AZURE_OPENAI,
            display_name="Azure OpenAI",
            default_model="gpt-4o",
            api_key_env="AZURE_OPENAI_API_KEY",
            options={
                "endpoint_env": "AZURE_OPENAI_ENDPOINT",
                "api_version_env": "AZURE_OPENAI_API_VERSION",
                "api_version": "2024-12-01-preview",
                "deployment_env": "AZURE_OPENAI_DEPLOYMENT",
            },
        ),
        ModelProviderConfig(
            provider_id="anthropic",
            kind=ModelProviderKind.ANTHROPIC,
            display_name="Anthropic Claude",
            default_model="claude-3-5-sonnet-latest",
            base_url="https://api.anthropic.com/v1",
            api_key_env="ANTHROPIC_API_KEY",
        ),
        ModelProviderConfig(
            provider_id="google_gemini",
            kind=ModelProviderKind.GOOGLE_GEMINI,
            display_name="Google Gemini",
            default_model="gemini-2.0-flash",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            api_key_env="GOOGLE_API_KEY",
        ),
        ModelProviderConfig(
            provider_id="aws_bedrock",
            kind=ModelProviderKind.AWS_BEDROCK,
            display_name="AWS Bedrock",
            default_model="anthropic.claude-3-5-sonnet-20240620-v1:0",
            api_key_env=None,
            options={"region_env": "AWS_REGION"},
        ),
        ModelProviderConfig(
            provider_id="huggingface",
            kind=ModelProviderKind.HUGGINGFACE,
            display_name="Hugging Face Inference",
            default_model="mistralai/Mistral-7B-Instruct-v0.3",
            base_url="https://api-inference.huggingface.co/models",
            api_key_env="HUGGINGFACE_API_KEY",
        ),
        ModelProviderConfig(
            provider_id="ollama",
            kind=ModelProviderKind.OLLAMA,
            display_name="Ollama",
            default_model="llama3.1",
            base_url="http://localhost:11434",
            api_key_env=None,
        ),
        ModelProviderConfig(
            provider_id="openai_compatible",
            kind=ModelProviderKind.OPENAI_COMPATIBLE,
            display_name="OpenAI-Compatible Provider",
            default_model="local-model",
            base_url="http://localhost:8001/v1",
            api_key_env="OPENAI_COMPATIBLE_API_KEY",
        ),
        ModelProviderConfig(
            provider_id="local_echo",
            kind=ModelProviderKind.LOCAL_ECHO,
            display_name="Local Echo Test Provider",
            default_model="echo",
            enabled=True,
        ),
    ]:
        registry.register(config)
    return registry
