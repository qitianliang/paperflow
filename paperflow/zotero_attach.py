import os
from pyzotero import zotero
from paperflow.config import get_config
from paperflow.logging_utils import get_logger

logger = get_logger(__name__)

class ZoteroAttacher:
    def __init__(self):
        self.config = get_config()
        self.zot = zotero.Zotero(
            self.config.zotero.user_id,
            self.config.zotero.library_type,
            self.config.zotero.api_key
        )
        self.strategy = self.config.zotero.attachment_strategy

    def attach_pdfs(self, parent_item_key: str, mono_pdf_path: str, dual_pdf_path: str) -> bool:
        if not self.strategy.upload_translated_pdf_to_zotero:
            logger.info("Zotero attachment strategy is disabled. Skipping upload.")
            return True

        logger.info(f"Attaching translated PDFs back to Zotero item {parent_item_key}...")
        try:
            # Check existing attachments to avoid duplicates
            children = self.zot.children(parent_item_key)
            existing_tags = set()
            for child in children:
                for tag in child.get('data', {}).get('tags', []):
                    existing_tags.add(tag.get('tag'))

            tag_name = self.strategy.translated_pdf_tag

            files_to_upload = []
            if mono_pdf_path and os.path.exists(mono_pdf_path):
                files_to_upload.append(mono_pdf_path)
            if dual_pdf_path and os.path.exists(dual_pdf_path):
                files_to_upload.append(dual_pdf_path)

            for file_path in files_to_upload:
                logger.info(f"Uploading {os.path.basename(file_path)}...")
                result = self.zot.attachment_simple([file_path], parent_item_key)

                # Tag the newly uploaded attachment
                if result.get("success"):
                    for key in result["success"]:
                        item = self.zot.item(key)
                        item['data']['tags'].append({'tag': tag_name})
                        self.zot.update_item(item)
                        logger.info(f"Successfully tagged attachment {key}")
                else:
                    logger.error(f"Failed to upload {file_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to attach PDFs to Zotero: {e}")
            return False
