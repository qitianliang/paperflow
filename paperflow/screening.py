import json
import os
from typing import Optional
from paperflow.config import get_config
from paperflow.llm.router import LLMRouter
from paperflow.schemas import SpeedCard, PaperMetadata
from paperflow.logging_utils import get_logger
from paperflow.utils import sample_long_text, try_repair_json

logger = get_logger(__name__)

class Screener:
    def __init__(self):
        self.config = get_config()
        self.router = LLMRouter()
        prompt_paths = ["prompts/speed_card.md", "paperflow/prompts/speed_card.md", "../prompts/speed_card.md"]
        self.prompt_path = None
        for path in prompt_paths:
            if os.path.exists(path):
                self.prompt_path = path
                break
        if not self.prompt_path:
            raise FileNotFoundError(f"Could not find speed_card.md in any of: {prompt_paths}")

    def load_prompt_template(self) -> str:
        with open(self.prompt_path, "r", encoding="utf-8") as f:
            return f.read()

    def normalize_decision(self, speed_card: SpeedCard) -> SpeedCard:
        """Compute aggregate recommendation deterministically from bounded sub-scores."""
        weights = self.config.screening.score_weights
        score_fields = (
            "topic_relevance_score",
            "method_relevance_score",
            "data_relevance_score",
            "novelty_score",
            "reproducibility_score",
        )
        for field in score_fields:
            value = max(1, min(5, int(getattr(speed_card, field))))
            setattr(speed_card, field, value)
        speed_card.priority_score = round(
            sum(getattr(speed_card, field) * weights.get(field, 0.0) for field in score_fields),
            2,
        )
        thresholds = self.config.screening.suggestion_thresholds
        if speed_card.priority_score >= thresholds.get("must_read", 4.2):
            speed_card.ai_suggestion = "Must Read"
        elif speed_card.priority_score >= thresholds.get("scan", 3.2):
            speed_card.ai_suggestion = "Scan"
        elif speed_card.priority_score >= thresholds.get("park", 2.2):
            speed_card.ai_suggestion = "Park"
        else:
            speed_card.ai_suggestion = "Exclude"
        return speed_card

    def generate_speed_card(self, metadata: PaperMetadata, paper_text: str = "") -> Optional[SpeedCard]:
        template = self.load_prompt_template()

        # Prepare context
        research_topic = self.config.project.resolved_research_topic
        schema_info = json.dumps(SpeedCard.model_json_schema(), ensure_ascii=False, indent=2)
        meta_json = metadata.model_dump_json(indent=2)

        # Replace placeholders
        prompt = template.replace("{{research_topic}}", research_topic)
        prompt = prompt.replace("{{speed_card_schema}}", schema_info)
        prompt = prompt.replace("{{metadata}}", meta_json)
        prompt = prompt.replace("{{abstract}}", metadata.abstract or "No abstract available.")

        # Sample long papers so conclusions are not discarded by head-only truncation.
        max_chars = self.config.pdf.max_chars_for_speed_card
        truncated_text = (
            sample_long_text(paper_text, max_chars, self.config.pdf.sample_sections)
            if paper_text else "No full text available. Rely on abstract."
        )
        prompt = prompt.replace("{{paper_text}}", truncated_text)

        abstract_len = len(metadata.abstract or "")
        text_len = len(paper_text or "")
        logger.info(
            f"Requesting Speed Card for '{metadata.title}': "
            f"pdf_text={text_len} chars "
            f"(fed {len(truncated_text)} chars)"
        )

        client, model = self.router.get_client_and_model("speed_card")

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
            # Cleanup potential markdown code block artifacts just in case
            if content.startswith("```json"):
                content = content[7:]
            if content.endswith("```"):
                content = content[:-3]

            data = try_repair_json(content)
            if not data:
                raise ValueError("LLM returned invalid JSON")
            return self.normalize_decision(SpeedCard(**data))

        except Exception as e:
            logger.error(f"Failed to generate speed card for {metadata.zotero_key}: {e}")
            return None
