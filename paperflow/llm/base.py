"""Base LLM provider interface."""
import json
from typing import Optional, Dict, Any, Tuple
from openai import OpenAI
from paperflow.config import get_config
from paperflow.logging_utils import get_logger
from paperflow.schemas import LLMCallRecord
from paperflow.cache import cache

logger = get_logger(__name__)


class BaseLLMProvider:
    """Base class for LLM providers. Uses OpenAI-compatible API."""

    def __init__(self, provider_name: str):
        self.config = get_config()
        self.provider_name = provider_name
        self.provider_config = self.config.llm.providers.get(provider_name)
        if not self.provider_config:
            raise ValueError(f"Provider '{provider_name}' not found in llm.providers config")

        api_key = self.provider_config.api_key
        base_url = self.provider_config.resolved_base_url

        if not api_key:
            raise ValueError(
                f"API key not found for {provider_name}. "
                f"Set {self.provider_config.api_key_env} in .env"
            )

        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=self.config.llm.request_timeout_seconds,
            max_retries=self.config.llm.max_retries,
        )

    def get_model(self, tier: str = "balanced") -> str:
        return self.provider_config.resolved_model(tier) or self.provider_config.resolved_model("balanced") or self.provider_config.models.get("balanced", "")

    def chat(
        self,
        messages: list,
        model: str = "",
        task: str = "",
        zotero_key: str = "",
        json_mode: bool = True,
        max_tokens: int = 4096,
    ) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        """
        Send a chat completion request.
        Returns (raw_content, parsed_json_or_None).
        Saves LLMCallRecord to cache.
        """
        if not model:
            model = self.get_model("balanced")

        kwargs = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        raw_content = None
        parsed = None
        error_msg = ""
        success = False

        try:
            response = self.client.chat.completions.create(**kwargs)
            raw_content = response.choices[0].message.content

            # Cleanup markdown code block wrappers
            if raw_content:
                cleaned = raw_content.strip()
                if cleaned.startswith("```json"):
                    cleaned = cleaned[7:]
                elif cleaned.startswith("```"):
                    cleaned = cleaned[3:]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                cleaned = cleaned.strip()

                if json_mode:
                    try:
                        parsed = json.loads(cleaned)
                        success = True
                    except json.JSONDecodeError:
                        # Try JSON repair
                        from paperflow.utils import try_repair_json
                        repaired = try_repair_json(cleaned)
                        if repaired:
                            try:
                                parsed = json.loads(repaired)
                                success = True
                            except json.JSONDecodeError:
                                error_msg = "JSON parse failed after repair"
                                logger.warning(f"JSON repair failed for {zotero_key}/{task}")
                        else:
                            error_msg = "JSON parse and repair failed"
                            logger.warning(f"JSON parse failed for {zotero_key}/{task}")
                else:
                    success = True
        except Exception as e:
            error_msg = str(e)
            logger.error(f"LLM call failed [{self.provider_name}/{model}]: {e}")

        # Save call record
        record = LLMCallRecord(
            task=task,
            provider=self.provider_name,
            model=model,
            prompt_preview=messages[-1].get("content", "")[:200] if messages else "",
            raw_response=(raw_content or "")[:2000],
            parsed_json=parsed,
            success=success,
            error=error_msg,
        )
        if zotero_key:
            cache.save_json(zotero_key, f"raw/llm_{task}.json", record.model_dump())

        return raw_content, parsed
