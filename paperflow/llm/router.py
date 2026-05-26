import os
from openai import OpenAI
from paperflow.config import get_config
from paperflow.logging_utils import get_logger

logger = get_logger(__name__)

class LLMRouter:
    def __init__(self):
        self.config = get_config()

    def get_client_and_model(self, task: str) -> tuple[OpenAI, str]:
        """
        Determines which provider and model to use for a given task,
        returns the configured OpenAI client and model name.
        Uses typed LLMConfig as single source of truth.
        """
        llm = self.config.llm
        route = llm.routing.get(task)
        provider_name = route.provider if route else llm.default_provider
        model_tier = route.model_tier if route else "balanced"

        provider = llm.providers.get(provider_name)
        if not provider:
            logger.warning(f"Provider '{provider_name}' not configured. Falling back to '{llm.default_provider}'.")
            provider = llm.providers.get(llm.default_provider)
            provider_name = llm.default_provider
            if not provider:
                raise ValueError(f"No LLM provider configured. Check config.yaml llm.providers.")

        model = provider.resolved_model(model_tier)
        if not model:
            model = provider.resolved_model("balanced") or provider.models.get("balanced", "")

        api_key = provider.api_key
        if not api_key:
            raise ValueError(
                f"API key not found for provider '{provider_name}'. "
                f"Set {provider.api_key_env} in .env"
            )

        client = OpenAI(
            api_key=api_key,
            base_url=provider.resolved_base_url,
            timeout=llm.request_timeout_seconds,
            max_retries=llm.max_retries,
        )
        return client, model
