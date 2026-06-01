import os
import yaml
from pydantic import BaseModel, Field, model_validator
from typing import Dict, Any, Optional, List
from dotenv import load_dotenv
from paperflow.utils import sanitize_filename

load_dotenv()

class AttachmentStrategyConfig(BaseModel):
    locate_original_pdf: bool = True
    upload_translated_pdf_to_zotero: bool = False
    upload_mode: str = "imported_file"
    translated_pdf_tag: str = "translated-pdf"
    translated_dual_title_suffix: str = " - dual translated"
    translated_mono_title_suffix: str = " - mono translated"

class ZoteroConfig(BaseModel):
    library_type: str = "user"
    user_id_env: str = "ZOTERO_USER_ID"
    api_key_env: str = "ZOTERO_API_KEY"
    default_collection_id_env: str = "COLLECTION_ID"
    default_collection_id: str = ""
    attachment_strategy: AttachmentStrategyConfig = AttachmentStrategyConfig()

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "")

    @property
    def user_id(self) -> str:
        return os.environ.get(self.user_id_env, "")

    @property
    def collection_id(self) -> str:
        return os.environ.get(self.default_collection_id_env, "") or self.default_collection_id

class NotionConfig(BaseModel):
    token_env: str = "NOTION_TOKEN"
    database_id_env: str = "NOTION_DATABASE_ID"
    data_source_id_env: str = "NOTION_DATA_SOURCE_ID"
    preserve_human_decision: bool = True
    use_environment_proxy: bool = True

    @property
    def token(self) -> str:
        return os.environ.get(self.token_env, "")

    @property
    def database_id(self) -> str:
        return os.environ.get(self.database_id_env, "")

    @property
    def data_source_id(self) -> str:
        return os.environ.get(self.data_source_id_env, "")

class ObsidianConfig(BaseModel):
    vault_path_env: str = "OBSIDIAN_VAULT_PATH"
    literature_note_dir: str = "Literature/Notes"
    pdf_dir: str = "Literature/PDFs"
    translated_pdf_dir: str = "Literature/PDFs/Translated"

    @property
    def vault_path(self) -> str:
        return os.environ.get(self.vault_path_env, "")

class TopicProfile(BaseModel):
    label: str = ""
    research_topic: str = ""
    collection_id_env: str = ""
    collection_id: str = ""
    notion_tag: str = ""
    obsidian_subdir: str = ""

class ProjectConfig(BaseModel):
    name: str = "paperflow"
    active_topic: str = "default"
    research_topic: str = ""
    topics: Dict[str, TopicProfile] = Field(default_factory=dict)

    @property
    def topic(self) -> TopicProfile:
        profile = self.topics.get(self.active_topic)
        if profile:
            return profile
        return TopicProfile(
            label=self.research_topic or self.active_topic,
            research_topic=self.research_topic,
            notion_tag=self.research_topic or self.active_topic,
            obsidian_subdir=self.active_topic,
        )

    @property
    def topic_label(self) -> str:
        return self.topic.notion_tag or self.topic.label or self.active_topic

    @property
    def topic_slug(self) -> str:
        value = self.topic.obsidian_subdir or self.active_topic
        return sanitize_filename(value.lower(), max_len=50) or "default"

    @property
    def resolved_research_topic(self) -> str:
        return self.topic.research_topic or self.research_topic

    @property
    def collection_id(self) -> str:
        if self.topic.collection_id_env:
            value = os.environ.get(self.topic.collection_id_env, "")
            if value:
                return value
        return self.topic.collection_id

class PdfConfig(BaseModel):
    max_pages_for_speed_card: int = 8
    max_chars_for_speed_card: int = 30000
    max_chars_for_deep_read: int = 120000
    sample_sections: int = 3
    extract_strategy: str = "simple"
    speed_card_full_text: bool = True   # True: extract all pages, skip sampling for speed card

class ScreeningConfig(BaseModel):
    max_concurrent_papers: int = Field(default=1, ge=1, le=8)
    score_weights: Dict[str, float] = Field(default_factory=lambda: {
        "topic_relevance_score": 0.35,
        "method_relevance_score": 0.25,
        "data_relevance_score": 0.15,
        "novelty_score": 0.15,
        "reproducibility_score": 0.10,
    })
    suggestion_thresholds: Dict[str, float] = Field(default_factory=lambda: {
        "must_read": 4.2,
        "scan": 3.2,
        "park": 2.2,
    })
    auto_mark_top_n: int = Field(default=0, ge=0, description="Auto-mark top N Priority Score papers as Must Read after screening")

    @model_validator(mode="after")
    def validate_scoring(self) -> "ScreeningConfig":
        required = {
            "topic_relevance_score", "method_relevance_score",
            "data_relevance_score", "novelty_score", "reproducibility_score",
        }
        if set(self.score_weights) != required:
            raise ValueError(f"score_weights must define exactly: {sorted(required)}")
        if any(weight < 0 for weight in self.score_weights.values()):
            raise ValueError("score_weights cannot be negative")
        if abs(sum(self.score_weights.values()) - 1.0) > 0.001:
            raise ValueError("score_weights must sum to 1.0")
        thresholds = self.suggestion_thresholds
        if not all(name in thresholds for name in ("must_read", "scan", "park")):
            raise ValueError("suggestion_thresholds requires must_read, scan, and park")
        if not 1 <= thresholds["park"] <= thresholds["scan"] <= thresholds["must_read"] <= 5:
            raise ValueError("suggestion_thresholds must be ordered within the 1-5 scale")
        return self

class DeepReadSelectionConfig(BaseModel):
    enabled: bool = False
    top_n: int = Field(default=10, ge=0)
    include_human_must_read: bool = True

class CacheConfig(BaseModel):
    enabled: bool = True
    force_refresh: bool = False

class LoggingConfig(BaseModel):
    level: str = "INFO"

class LLMProviderConfig(BaseModel):
    """Configuration for a single LLM provider.

    Each field supports env-var override with yaml fallback:
      base_url   → $LLM_BASE_URL  (if set) else yaml value
      models.*   → $LLM_MODEL_{TIER} (if set) else yaml value
    """
    api_key_env: str = ""
    base_url: Optional[str] = None
    models: Dict[str, str] = Field(default_factory=lambda: {"cheap": "", "balanced": "", "strong": ""})

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "")

    @property
    def resolved_base_url(self) -> Optional[str]:
        return os.environ.get("LLM_BASE_URL") or self.base_url

    def resolved_model(self, tier: str) -> str:
        env_key = f"LLM_MODEL_{tier.upper()}"
        return os.environ.get(env_key) or self.models.get(tier, "")


class LLMTaskRoute(BaseModel):
    """Route a task to a provider + model tier."""
    provider: str = "deepseek"
    model_tier: str = "balanced"


class LLMConfig(BaseModel):
    """Unified LLM configuration — single source of truth for all AI models."""
    default_provider: str = "deepseek"
    fallback_provider: str = "openai"
    providers: Dict[str, LLMProviderConfig] = Field(default_factory=dict)
    routing: Dict[str, LLMTaskRoute] = Field(default_factory=dict)
    request_timeout_seconds: float = 120.0
    max_retries: int = 2
    max_output_tokens: int = 12000

    def resolve_model(self, task: str) -> tuple:
        """Resolve (provider_name, model_name) for a task using routing config."""
        route = self.routing.get(task, LLMTaskRoute())
        provider = self.providers.get(route.provider)
        if not provider:
            provider = self.providers.get(self.default_provider, LLMProviderConfig())
        model = provider.models.get(route.model_tier, "")
        return route.provider, model, provider


class TranslationPdf2zhConfig(BaseModel):
    executable_env: str = "PDF2ZH_EXECUTABLE"
    executable_default: str = "pdf2zh"
    source_lang: str = "en"
    target_lang: str = "zh"
    service: str = "openai"
    threads: int = 2
    timeout_minutes: int = 90
    max_concurrent_papers: int = 1
    use_custom_prompt: bool = True
    prompt_file: str = "prompts/pdf2zh_translate_prompt.md"
    config_file: str = "data/pdf2zh_config.json"
    output_types: List[str] = Field(default_factory=lambda: ["mono", "dual"])
    # Reference LLM provider instead of duplicating models
    llm_provider: str = "deepseek"
    model_tier: str = "balanced"


class TranslationPathsConfig(BaseModel):
    staging_dir: str = "data/translation_staging"
    output_dir: str = "Literature/PDFs/Translated"


class TranslationBehaviorConfig(BaseModel):
    copy_original_pdf: bool = True
    skip_if_translated_exists: bool = True
    overwrite_existing: bool = False
    max_retries: int = 2
    update_notion: bool = True
    update_obsidian_links: bool = True
    attach_back_to_zotero: bool = False


class TranslationTriggerConfig(BaseModel):
    human_decision: str = "Must Read"
    pdf_status: str = "Has PDF"


class TranslationConfig(BaseModel):
    enabled: bool = True
    trigger: TranslationTriggerConfig = TranslationTriggerConfig()
    pdf2zh: TranslationPdf2zhConfig = TranslationPdf2zhConfig()
    paths: TranslationPathsConfig = TranslationPathsConfig()
    behavior: TranslationBehaviorConfig = TranslationBehaviorConfig()


class Config(BaseModel):
    project: ProjectConfig = ProjectConfig()
    zotero: ZoteroConfig = ZoteroConfig()
    notion: NotionConfig = NotionConfig()
    obsidian: ObsidianConfig = ObsidianConfig()
    pdf: PdfConfig = PdfConfig()
    screening: ScreeningConfig = ScreeningConfig()
    deep_read_selection: DeepReadSelectionConfig = DeepReadSelectionConfig()
    llm: LLMConfig = LLMConfig()
    translation: TranslationConfig = TranslationConfig()
    cache: CacheConfig = CacheConfig()
    logging: LoggingConfig = LoggingConfig()
    raw_config: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def load(cls, config_path: str = "config.yaml") -> "Config":
        paths_to_try = [config_path, "paperflow/config.yaml", "../config.yaml"]
        actual_path = None
        for path in paths_to_try:
            if os.path.exists(path):
                actual_path = path
                break

        if not actual_path:
            raise FileNotFoundError(f"Config file not found. Tried: {', '.join(paths_to_try)}")

        with open(actual_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        config = cls(**data)
        config.raw_config = data
        return config

# Global singleton
_config_instance = None

def get_config() -> Config:
    global _config_instance
    if _config_instance is None:
        try:
            _config_instance = Config.load()
        except Exception as e:
            import logging
            logging.warning(f"[WARNING] Failed to load config: {e}. Using empty defaults.")
            _config_instance = Config()
    return _config_instance
