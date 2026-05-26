import json
import os
from typing import List, Dict, Any, Optional
from paperflow.config import get_config
from paperflow.llm.router import LLMRouter
from paperflow.schemas import SpeedCard
from paperflow.cache import cache
from paperflow.logging_utils import get_logger
from paperflow.utils import sample_long_text, try_repair_json

logger = get_logger(__name__)

class BatchComparer:
    def __init__(self):
        self.config = get_config()
        self.router = LLMRouter()
        prompt_paths = ["prompts/batch_compare.md", "paperflow/prompts/batch_compare.md", "../prompts/batch_compare.md"]
        self.prompt_path = None
        for path in prompt_paths:
            if os.path.exists(path):
                self.prompt_path = path
                break
        if not self.prompt_path:
            raise FileNotFoundError(f"Could not find batch_compare.md in any of: {prompt_paths}")

    def load_prompt_template(self) -> str:
        with open(self.prompt_path, "r", encoding="utf-8") as f:
            return f.read()

    def compare_collection(self, zotero_keys: List[str]) -> Optional[Dict[str, Any]]:
        """Compare a batch of papers and return batch-level analysis."""
        speed_cards = []
        for key in zotero_keys:
            data = cache.load_json(key, "speed_card.json")
            if data:
                data["zotero_key"] = key
                speed_cards.append(data)

        if not speed_cards:
            logger.warning("No speed cards found for comparison.")
            return None

        template = self.load_prompt_template()
        research_topic = self.config.project.resolved_research_topic
        speed_cards_json = json.dumps(speed_cards, ensure_ascii=False, indent=2)
        speed_cards_json = sample_long_text(
            speed_cards_json,
            self.config.pdf.max_chars_for_deep_read,
            self.config.pdf.sample_sections,
        )

        prompt = template.replace("{{research_topic}}", research_topic)
        prompt = prompt.replace("{{speed_cards_json}}", speed_cards_json)

        client, model = self.router.get_client_and_model("batch_compare")

        logger.info(f"Requesting batch compare for {len(speed_cards)} papers using {model}...")
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You are a helpful research assistant. Always output valid JSON only, without markdown code blocks."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                max_tokens=self.config.llm.max_output_tokens,
            )

            content = response.choices[0].message.content
            if content.startswith("```json"):
                content = content[7:]
            if content.endswith("```"):
                content = content[:-3]

            data = try_repair_json(content)
            if not data:
                raise ValueError("LLM returned invalid JSON")

            # Cache result per key
            for key in zotero_keys:
                cache.save_json(key, "batch_compare.json", data)

            return data

        except Exception as e:
            logger.error(f"Failed to generate batch compare: {e}")
            return None
