import os
import shutil
from typing import Dict, Tuple

from paperflow.cache import cache
from paperflow.config import Config, get_config
from paperflow.logging_utils import get_logger

logger = get_logger(__name__)


class TranslationArtifacts:
    """Publish translated PDFs for downstream consumers and record their paths."""

    def __init__(self, config: Config = None, cache_store=None):
        self.config = config or get_config()
        self.cache = cache_store or cache

    def _published_path(self, zotero_key: str, source_path: str) -> str:
        vault_path = self.config.obsidian.vault_path
        if not vault_path or not source_path:
            return ""
        return os.path.join(
            vault_path,
            self.config.obsidian.translated_pdf_dir,
            self.config.project.topic_slug,
            zotero_key,
            os.path.basename(source_path),
        )

    def _publish_file(self, zotero_key: str, source_path: str) -> str:
        if not source_path or not os.path.exists(source_path):
            return ""
        destination = self._published_path(zotero_key, source_path)
        if not destination:
            return source_path
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        if os.path.abspath(source_path) != os.path.abspath(destination):
            shutil.copy2(source_path, destination)
        return destination

    def publish(self, zotero_key: str, mono_pdf: str, dual_pdf: str) -> Tuple[str, str]:
        published_mono = mono_pdf
        published_dual = dual_pdf
        if self.config.translation.behavior.update_obsidian_links:
            published_mono = self._publish_file(zotero_key, mono_pdf) or mono_pdf
            published_dual = self._publish_file(zotero_key, dual_pdf) or dual_pdf
        manifest = {
            "source_mono_pdf": mono_pdf,
            "source_dual_pdf": dual_pdf,
            "published_mono_pdf": published_mono,
            "published_dual_pdf": published_dual,
        }
        self.cache.save_json(zotero_key, "translation_artifacts.json", manifest)
        logger.info(f"Recorded translated artifacts for {zotero_key}")
        return published_mono, published_dual

    def resolve(self, zotero_key: str, mono_pdf: str = "", dual_pdf: str = "") -> Tuple[str, str]:
        manifest: Dict[str, str] = self.cache.load_json(zotero_key, "translation_artifacts.json") or {}
        published_mono = manifest.get("published_mono_pdf", "")
        published_dual = manifest.get("published_dual_pdf", "")
        if published_mono and os.path.exists(published_mono):
            mono_pdf = published_mono
        if published_dual and os.path.exists(published_dual):
            dual_pdf = published_dual
        return mono_pdf, dual_pdf
