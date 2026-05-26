import os
import shutil
from typing import Optional
from paperflow.config import get_config
from paperflow.logging_utils import get_logger
from paperflow.zotero_client import ZoteroClient

logger = get_logger(__name__)

class PDFLocator:
    def __init__(self):
        self.config = get_config()
        self.zotero_client = ZoteroClient()
        self.staging_dir = os.path.join(
            self.config.translation.paths.staging_dir,
            self.config.project.topic_slug,
        )
        os.makedirs(self.staging_dir, exist_ok=True)

    def locate_and_stage_pdf(self, zotero_key: str, attachment_key: str, notion_local_path: str = "") -> Optional[str]:
        """Locates the PDF and copies it to the staging directory."""
        if not attachment_key and not notion_local_path:
            logger.warning(f"No attachment key or local path provided for {zotero_key}.")
            return None

        staged_path = os.path.join(self.staging_dir, zotero_key, f"{zotero_key}.pdf")
        os.makedirs(os.path.dirname(staged_path), exist_ok=True)

        # If already staged, return it
        if os.path.exists(staged_path):
            logger.info(f"PDF already staged at {staged_path}")
            return staged_path

        # 1. Try Notion's local path
        if notion_local_path and os.path.exists(notion_local_path):
            try:
                shutil.copy2(notion_local_path, staged_path)
                logger.info(f"Copied PDF from Notion local path to {staged_path}")
                return staged_path
            except Exception as e:
                logger.warning(f"Failed to copy from Notion local path: {e}")

        # 2. Try Zotero default local storage (Windows/Mac common paths)
        # This is a heuristic. It's better to download via API if local is missing.
        home_dir = os.path.expanduser("~")
        zotero_storage_dir = os.path.join(home_dir, "Zotero", "storage", attachment_key)
        if os.path.exists(zotero_storage_dir):
            for file in os.listdir(zotero_storage_dir):
                if file.lower().endswith('.pdf'):
                    local_zotero_pdf = os.path.join(zotero_storage_dir, file)
                    try:
                        shutil.copy2(local_zotero_pdf, staged_path)
                        logger.info(f"Copied PDF from Zotero local storage to {staged_path}")
                        return staged_path
                    except Exception as e:
                        logger.warning(f"Failed to copy from Zotero local storage: {e}")

        # 3. Fallback: Download via Zotero API
        if attachment_key:
            logger.info(f"Downloading PDF from Zotero API for attachment {attachment_key}...")
            try:
                # pyzotero file fetching
                file_content = self.zotero_client.zot.file(attachment_key)
                with open(staged_path, 'wb') as f:
                    f.write(file_content)
                logger.info(f"Downloaded PDF via API to {staged_path}")
                return staged_path
            except Exception as e:
                logger.error(f"Failed to download PDF via Zotero API: {e}")

        logger.error(f"Could not locate or download PDF for {zotero_key}.")
        return None
