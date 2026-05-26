import json
import os
from typing import Optional, Dict, Any
from paperflow.config import get_config
from paperflow.llm.router import LLMRouter
from paperflow.schemas import PaperMetadata, SpeedCard
from paperflow.logging_utils import get_logger
from paperflow.utils import sample_long_text, try_repair_json

logger = get_logger(__name__)

class DeepReader:
    def __init__(self):
        self.config = get_config()
        self.router = LLMRouter()
        prompt_paths = ["prompts/deep_read.md", "paperflow/prompts/deep_read.md", "../prompts/deep_read.md"]
        self.prompt_path = None
        for path in prompt_paths:
            if os.path.exists(path):
                self.prompt_path = path
                break
        if not self.prompt_path:
            raise FileNotFoundError(f"Could not find deep_read.md in any of: {prompt_paths}")

    def generate_deep_read(self, metadata: PaperMetadata, speed_card: SpeedCard, paper_text: str) -> Optional[Dict[str, Any]]:
        with open(self.prompt_path, "r", encoding="utf-8") as f:
            template = f.read()

        research_topic = self.config.project.resolved_research_topic
        meta_json = metadata.model_dump_json(indent=2)
        speed_card_json = speed_card.model_dump_json(indent=2)

        max_chars = self.config.pdf.max_chars_for_deep_read
        truncated_text = (
            sample_long_text(paper_text, max_chars, self.config.pdf.sample_sections)
            if paper_text else "No full text available."
        )

        prompt = template.replace("{{research_topic}}", research_topic)
        prompt = prompt.replace("{{metadata}}", meta_json)
        prompt = prompt.replace("{{speed_card_json}}", speed_card_json)
        prompt = prompt.replace("{{paper_text}}", truncated_text)

        client, model = self.router.get_client_and_model("deep_read")

        logger.info(f"Requesting Deep Read for '{metadata.title}' using {model}...")
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You are a professional academic research assistant. Output valid JSON only."},
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
            return data

        except Exception as e:
            logger.error(f"Failed to generate deep read for {metadata.zotero_key}: {e}")
            return None
