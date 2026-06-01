import json
import os
import hashlib
from datetime import datetime
from typing import Optional
from paperflow.config import get_config
from paperflow.llm.router import LLMRouter
from paperflow.schemas import SpeedCard, PaperMetadata
from paperflow.logging_utils import get_logger
from paperflow.utils import sample_long_text, try_repair_json
from paperflow.cache import cache

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

    def assessment_signature(self, metadata: PaperMetadata) -> str:
        """Track inputs whose changes require a fresh AI assessment."""
        route = self.config.llm.routing.get("speed_card")
        provider_name = route.provider if route else self.config.llm.default_provider
        tier = route.model_tier if route else "balanced"
        provider = self.config.llm.providers.get(provider_name)
        payload = {
            "prompt": self.load_prompt_template(),
            "schema": SpeedCard.model_json_schema(),
            "topic": self.config.project.resolved_research_topic,
            "metadata": metadata.model_dump(),
            "pdf": {
                "pages": self.config.pdf.max_pages_for_speed_card,
                "chars": self.config.pdf.max_chars_for_speed_card,
                "sections": self.config.pdf.sample_sections,
                "full_text": self.config.pdf.speed_card_full_text,
            },
            "screening": {
                "weights": self.config.screening.score_weights,
                "thresholds": self.config.screening.suggestion_thresholds,
            },
            "provider": provider_name,
            "base_url": provider.resolved_base_url if provider else "",
            "model": provider.resolved_model(tier) if provider else "",
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _validate_novelty_evidence(self, speed_card: SpeedCard) -> SpeedCard:
        """如果 novelty_score ≥ 4 但缺乏技术机制证据，自动降级。"""
        if speed_card.novelty_score >= 4:
            has_technical_evidence = any(
                keyword in evidence.lower()
                for evidence in speed_card.key_evidence
                for keyword in [
                    "algorithm", "mechanism", "architecture", "formulation",
                    "derivation", "framework", "model", "approach",
                    "methodology", "design", "scheme", "pipeline",
                    "theoretical", "proof", "analysis",
                ]
            )
            if not has_technical_evidence:
                original = speed_card.novelty_score
                speed_card.novelty_score = min(3, speed_card.novelty_score)
                speed_card.risk_need_check.append(
                    f"novelty_score 因缺乏技术机制证据而被从 {original} 降至 {speed_card.novelty_score}，"
                    f"请人工复核。"
                )
                if speed_card.confidence == "High":
                    speed_card.confidence = "Medium"
        return speed_card

    def _enforce_confidence_consistency(self, speed_card: SpeedCard) -> SpeedCard:
        """低置信度时，禁止给出最高分。"""
        if speed_card.confidence == "Low":
            for field in (
                "topic_relevance_score", "method_relevance_score",
                "data_relevance_score", "novelty_score", "reproducibility_score",
            ):
                if getattr(speed_card, field) == 5:
                    setattr(speed_card, field, 4)
                    speed_card.risk_need_check.append(
                        f"{field} 因置信度为 Low 而被从 5 降至 4。"
                    )
        return speed_card

    def _check_cross_dimension_consistency(self, speed_card: SpeedCard) -> SpeedCard:
        """检查各维度评分之间的一致性。"""
        if speed_card.novelty_score >= 4 and speed_card.reproducibility_score <= 2:
            speed_card.risk_need_check.append(
                "novelty_score 高但 reproducibility_score 低，创新性声明缺乏充分验证。"
            )
        if speed_card.novelty_score >= 4 and speed_card.method_relevance_score <= 2:
            speed_card.risk_need_check.append(
                "novelty_score 高但 method_relevance_score 低，可能为领域边缘创新。"
            )
        return speed_card

    def normalize_decision(self, speed_card: SpeedCard, has_full_text: bool = True) -> SpeedCard:
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
        if not speed_card.key_evidence:
            speed_card.confidence = "Low"
        elif not has_full_text and speed_card.confidence == "High":
            speed_card.confidence = "Medium"
        if not speed_card.risk_need_check:
            speed_card.risk_need_check = ["Verify the main claims and evidence manually."]
            if speed_card.confidence == "High":
                speed_card.confidence = "Medium"

        # 后处理校验
        speed_card = self._validate_novelty_evidence(speed_card)
        speed_card = self._enforce_confidence_consistency(speed_card)
        speed_card = self._check_cross_dimension_consistency(speed_card)

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
        if speed_card.ai_suggestion == "Must Read" and speed_card.confidence == "Low":
            speed_card.ai_suggestion = "Scan"
        return speed_card

    def card_from_data(self, data: dict, has_full_text: bool = True) -> SpeedCard:
        """Accept legacy/model data, enforce enum and numeric output contract."""
        normalized = dict(data)
        list_fields = (
            "summary_en", "summary_zh", "technical_details", "main_contributions",
            "limitations", "future_work", "key_evidence", "risk_need_check",
            "claimed_contributions", "actual_evidence", "skepticism_flags",
            "novelty_validation",
        )
        for field in list_fields:
            value = normalized.get(field)
            if isinstance(value, str):
                normalized[field] = [value] if value.strip() else []
            elif value is None:
                normalized[field] = []
        score_fields = (
            "topic_relevance_score", "method_relevance_score",
            "data_relevance_score", "novelty_score", "reproducibility_score",
        )
        for field in score_fields:
            try:
                normalized[field] = max(1, min(5, int(normalized.get(field, 1))))
            except (TypeError, ValueError):
                normalized[field] = 1
        if normalized.get("confidence") not in ("High", "Medium", "Low"):
            normalized["confidence"] = "Low"
        normalized["priority_score"] = 1.0
        normalized["ai_suggestion"] = "Scan"
        return self.normalize_decision(SpeedCard(**normalized), has_full_text=has_full_text)

    def _build_skepticism_prompt(self) -> str:
        """构建反方视角提示，让 LLM 在评估前先做批判性思考。"""
        return """
在给出最终评分之前，请先完成以下 "审稿人挑战"：

1. 假设这篇论文的创新性被夸大了，找出 3 个可能的弱点。
2. 如果去掉所有 "首次"、"开创性" 等形容词，该工作的技术实质是什么？
3. 这个方法是否只是已有技术的简单组合或参数调优？
4. 实验设计是否足以支撑作者的核心声明？

只有在通过上述挑战后，才能给出 novelty_score ≥ 4 的评分。
"""

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

        # Prepare paper text for LLM: full-text mode vs sampled mode.
        if self.config.pdf.speed_card_full_text:
            # Full-text mode: feed the entire extracted text without sampling.
            truncated_text = paper_text if paper_text else "No full text available. Rely on abstract."
            logger.info(
                f"Speed Card full-text mode: feeding {len(truncated_text)} chars "
                f"(original {len(paper_text or '')} chars, no sampling)"
            )
        else:
            # Sample long papers so conclusions are not discarded by head-only truncation.
            max_chars = self.config.pdf.max_chars_for_speed_card
            truncated_text = (
                sample_long_text(paper_text, max_chars, self.config.pdf.sample_sections)
                if paper_text else "No full text available. Rely on abstract."
            )
            logger.info(
                f"Speed Card sampled mode: feeding {len(truncated_text)} chars "
                f"(original {len(paper_text or '')} chars, "
                f"{self.config.pdf.sample_sections}-section sampling)"
            )
        prompt = prompt.replace("{{paper_text}}", truncated_text)

        # 追加反方视角提示
        prompt += "\n\n" + self._build_skepticism_prompt()

        log_title = metadata.title.encode("ascii", errors="backslashreplace").decode("ascii")
        logger.info(f"Requesting Speed Card for '{log_title}'")

        client, model = self.router.get_client_and_model("speed_card")

        # Save prompt audit cache so we can inspect what the LLM actually saw.
        audit = {
            "full_prompt": prompt,
            "prompt_length": len(prompt),
            "paper_text_original_length": len(paper_text) if paper_text else 0,
            "paper_text_fed_length": len(truncated_text),
            "full_text_mode": self.config.pdf.speed_card_full_text,
            "sampling_used": not self.config.pdf.speed_card_full_text,
            "sample_sections": self.config.pdf.sample_sections if not self.config.pdf.speed_card_full_text else None,
            "max_chars_config": self.config.pdf.max_chars_for_speed_card,
            "model": model,
            "timestamp": datetime.now().isoformat(),
        }
        cache.save_json(metadata.zotero_key, "speed_card_prompt.json", audit)

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
            return self.card_from_data(data, has_full_text=bool(paper_text))

        except Exception as e:
            logger.error(f"Failed to generate speed card for {metadata.zotero_key}: {e}")
            return None
