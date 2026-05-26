import os
import json
from typing import List, Dict, Any
from paperflow.config import get_config
from paperflow.logging_utils import get_logger
from paperflow.notion_client import NotionClientWrapper
from paperflow.pdf_locator import PDFLocator

logger = get_logger(__name__)

class TranslationQueue:
    def __init__(self):
        self.config = get_config()
        self.notion = NotionClientWrapper()
        self.locator = PDFLocator()
        self.queue_file = os.path.join(
            self.config.translation.paths.staging_dir,
            self.config.project.topic_slug,
            "queue.json",
        )
        os.makedirs(os.path.dirname(self.queue_file), exist_ok=True)

    def load_queue(self) -> List[Dict[str, Any]]:
        if os.path.exists(self.queue_file):
            try:
                with open(self.queue_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    def save_queue(self, queue: List[Dict[str, Any]]):
        with open(self.queue_file, "w", encoding="utf-8") as f:
            json.dump(queue, f, indent=2, ensure_ascii=False)

    def build_queue(self):
        """Fetches Must Read papers, locates their PDFs, and builds the queue."""
        papers = self.notion.get_must_read_papers()
        logger.info(f"Found {len(papers)} 'Must Read' papers pending translation.")

        current_queue = self.load_queue()
        queued_keys = {item["zotero_key"] for item in current_queue}

        added_count = 0

        for paper in papers:
            props = paper.get("properties", {})

            # Extract fields safely
            try:
                zotero_key = props.get("Zotero Key", {}).get("rich_text", [])[0].get("text", {}).get("content", "")
            except IndexError:
                continue

            if not zotero_key or zotero_key in queued_keys:
                continue

            pdf_status = props.get("PDF Status", {}).get("select", {}).get("name", "")
            if pdf_status != "Has PDF":
                logger.warning(f"Paper {zotero_key} marked as Must Read but has no PDF. Skipping.")
                continue

            # Need to get attachment_key from cache
            from paperflow.cache import cache
            metadata = cache.load_json(zotero_key, "metadata.json")
            attachment_key = metadata.get("pdf_attachment_key", "") if metadata else ""

            try:
                notion_local_path = props.get("Local PDF Path", {}).get("rich_text", [])[0].get("text", {}).get("content", "")
            except IndexError:
                notion_local_path = ""

            staged_path = self.locator.locate_and_stage_pdf(zotero_key, attachment_key, notion_local_path)

            if staged_path:
                queue_item = {
                    "zotero_key": zotero_key,
                    "page_id": paper["id"],
                    "staged_path": staged_path,
                    "status": "pending"
                }
                current_queue.append(queue_item)
                queued_keys.add(zotero_key)

                # Update Notion to Queued
                self.notion.update_translation_status(paper["id"], "Queued")
                added_count += 1
            else:
                self.notion.update_translation_status(paper["id"], "Failed", error_msg="Could not locate or download PDF")

        self.save_queue(current_queue)
        logger.info(f"Added {added_count} papers to the translation queue.")
        return current_queue
