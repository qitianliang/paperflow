import os
import json
from typing import Dict, Any
from paperflow.config import get_config
from paperflow.schemas import PaperMetadata, SpeedCard
from paperflow.logging_utils import get_logger

logger = get_logger(__name__)

class ObsidianExporter:
    def __init__(self):
        self.config = get_config()
        self.vault_path = os.environ.get(self.config.obsidian.vault_path_env, "")
        self.notes_dir = os.path.join(
            self.vault_path,
            self.config.obsidian.literature_note_dir,
            self.config.project.topic_slug,
        )
        prompt_paths = ["prompts/obsidian_note.md", "paperflow/prompts/obsidian_note.md", "../prompts/obsidian_note.md"]
        self.prompt_path = None
        for path in prompt_paths:
            if os.path.exists(path):
                self.prompt_path = path
                break
        if not self.prompt_path:
            raise FileNotFoundError(f"Could not find obsidian_note.md in any of: {prompt_paths}")

    def cache_context(self) -> Dict[str, str]:
        with open(self.prompt_path, "r", encoding="utf-8") as f:
            template = f.read()
        route = self.config.llm.routing.get("obsidian_note")
        provider_name = route.provider if route else self.config.llm.default_provider
        tier = route.model_tier if route else "balanced"
        provider = self.config.llm.providers.get(provider_name)
        return {
            "template": template,
            "provider": provider_name,
            "base_url": provider.resolved_base_url if provider else "",
            "model": provider.resolved_model(tier) if provider else "",
        }

    def format_list(self, items: list) -> str:
        if not items:
            return "无"
        return "\n".join([f"- {item}" for item in items])

    def export_note(self, metadata: PaperMetadata, speed_card: SpeedCard, deep_read: Dict[str, Any], mono_pdf: str, dual_pdf: str) -> bool:
        if not self.vault_path:
            logger.warning("Obsidian vault path is not configured. Skipping export.")
            return False

        os.makedirs(self.notes_dir, exist_ok=True)

        with open(self.prompt_path, "r", encoding="utf-8") as f:
            template = f.read()

        # Basic substitutions
        note_content = template.replace("{{title}}", metadata.title.replace('"', '\\"'))
        note_content = note_content.replace("{{authors}}", metadata.authors)
        note_content = note_content.replace("{{year}}", str(metadata.year or ""))
        note_content = note_content.replace("{{venue}}", metadata.venue)
        note_content = note_content.replace("{{doi}}", metadata.doi)
        note_content = note_content.replace("{{url}}", metadata.url)
        note_content = note_content.replace("{{zotero_key}}", metadata.zotero_key)
        note_content = note_content.replace("{{citation_key}}", metadata.citation_key)
        note_content = note_content.replace("{{status}}", speed_card.ai_suggestion)

        # PDF links (Obsidian syntax)
        note_content = note_content.replace("{{original_pdf_link}}", f"[{metadata.title} (Original)]({metadata.zotero_link})" if metadata.zotero_link else "无")
        note_content = note_content.replace("{{translated_dual_pdf_link}}", f"[[{os.path.basename(dual_pdf)}]]" if dual_pdf else "未生成")
        note_content = note_content.replace("{{translated_mono_pdf_link}}", f"[[{os.path.basename(mono_pdf)}]]" if mono_pdf else "未生成")

        from paperflow.llm.router import LLMRouter
        router = LLMRouter()
        client, model = router.get_client_and_model("obsidian_note")

        # Prepare inputs for LLM
        prompt = note_content.replace("{{metadata}}", metadata.model_dump_json(indent=2))
        prompt = prompt.replace("{{speed_card_json}}", speed_card.model_dump_json(indent=2))
        prompt = prompt.replace("{{deep_read_json}}", json.dumps(deep_read, indent=2, ensure_ascii=False))

        logger.info(f"Requesting Obsidian note formatting for '{metadata.title}'...")
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You are an Obsidian Markdown formatter. Output raw markdown only. Do not wrap in ```markdown blocks."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=self.config.llm.max_output_tokens,
            )

            final_markdown = response.choices[0].message.content
            if final_markdown.startswith("```markdown"):
                final_markdown = final_markdown[11:]
            if final_markdown.endswith("```"):
                final_markdown = final_markdown[:-3]

            safe_title = "".join(c for c in metadata.title if c.isalnum() or c in " -_")[:60].strip()
            if metadata.citation_key and metadata.citation_key != metadata.zotero_key:
                filename = f"{metadata.citation_key} - {safe_title}.md"
            else:
                first_author = metadata.authors.split(",")[0].split(" ")[-1].strip() if metadata.authors else "Unknown"
                year_str = str(metadata.year) if metadata.year else ""
                filename = f"{first_author}{year_str} - {safe_title}.md"
            # Truncate filename if too long
            if len(filename) > 120:
                filename = filename[:116] + ".md"

            file_path = os.path.join(self.notes_dir, filename)

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(final_markdown.strip())

            logger.info(f"Successfully exported Obsidian note to {file_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to generate Obsidian note: {e}")
            return False
