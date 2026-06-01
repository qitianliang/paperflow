import time
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
import httpx
from notion_client import Client
from paperflow.config import get_config
from paperflow.schemas import PaperMetadata, SpeedCard
from paperflow.logging_utils import get_logger

logger = get_logger(__name__)

MAX_RETRIES = 3
RETRY_DELAY = 2.0  # seconds, doubles each retry
AI_DETAILS_TITLE = "AI Details (paperflow)"
NOTION_TEXT_LIMIT = 1900


def _retry_on_network_error(func):
    """Decorator: retry up to MAX_RETRIES on network/SSL errors with exponential backoff."""
    from functools import wraps
    @wraps(func)
    def wrapper(*args, **kwargs):
        last_exc = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_exc = e
                msg = str(e)
                # Only retry on network-level errors (SSL, timeout, connection)
                is_network = any(kw in msg for kw in (
                    "SSL", "ssl", "UNEXPECTED_EOF", "Timeout", "timeout",
                    "ConnectionError", "RemoteDisconnected", "ProtocolError",
                    "ReadError", "ConnectError"
                ))
                if not is_network or attempt >= MAX_RETRIES:
                    break
                delay = RETRY_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    f"Notion API network error (attempt {attempt}/{MAX_RETRIES}), "
                    f"retrying in {delay:.1f}s: {msg[:120]}"
                )
                time.sleep(delay)
        raise last_exc
    return wrapper


class NotionClientWrapper:
    def __init__(self):
        config = get_config()
        http_client = httpx.Client(trust_env=config.notion.use_environment_proxy, timeout=60.0)
        self.client = Client(
            auth=config.notion.token,
            notion_version="2025-09-03",
            client=http_client,
        )
        self.database_id = config.notion.database_id
        self.data_source_id = config.notion.data_source_id
        if not self.data_source_id:
            database = self._retrieve_database()
            sources = database.get("data_sources", [])
            if not sources:
                raise ValueError(f"No Notion data source found in database {self.database_id}")
            self.data_source_id = sources[0]["id"]
        self.preserve_human_decision = config.notion.preserve_human_decision
        self.topic_tag = config.project.topic_label
        self.deep_read_selection = config.deep_read_selection
        self._page_index: Optional[Dict[str, Dict[str, Any]]] = None

    @_retry_on_network_error
    def _retrieve_database(self) -> Dict[str, Any]:
        return self.client.databases.retrieve(database_id=self.database_id)

    @_retry_on_network_error
    def _update_page(self, page_id: str, properties: Dict[str, Any]) -> Dict[str, Any]:
        return self.client.pages.update(page_id=page_id, properties=properties)

    @_retry_on_network_error
    def _create_page(self, properties: Dict[str, Any]) -> Dict[str, Any]:
        return self.client.pages.create(
            parent={"type": "data_source_id", "data_source_id": self.data_source_id},
            properties=properties,
        )

    @_retry_on_network_error
    def archive_page(self, page_id: str) -> Dict[str, Any]:
        try:
            return self.client.pages.update(page_id=page_id, archived=True)
        except Exception as e:
            if "archived" in str(e).lower():
                logger.warning(f"Page {page_id} is already archived, skipping.")
                return {}
            raise

    @_retry_on_network_error
    def query_database(self, filter: Dict[str, Any] = None, sorts: List[Dict[str, Any]] = None, start_cursor: str = "") -> Dict[str, Any]:
        """Query the Notion database with given filter."""
        body: Dict[str, Any] = {}
        if filter:
            body["filter"] = filter
        if sorts:
            body["sorts"] = sorts
        if start_cursor:
            body["start_cursor"] = start_cursor
        return self.client.request(
            path=f"data_sources/{self.data_source_id}/query",
            method="POST",
            body=body
        )

    def find_page_by_zotero_key(self, zotero_key: str) -> Optional[Dict[str, Any]]:
        if self._page_index is not None:
            return self._page_index.get(zotero_key)
        try:
            response = self.query_database(filter={
                "and": [
                    {"property": "Zotero Key", "rich_text": {"equals": zotero_key}},
                    self.topic_filter(),
                ]
            })
            results = response.get("results", [])
            if results:
                return results[0]
            return None
        except Exception as e:
            logger.error(f"Failed to query Notion: {e}")
            return None

    def prime_page_index(self) -> None:
        """Load pages once so collection sync does not issue one Notion query per paper."""
        index: Dict[str, Dict[str, Any]] = {}
        cursor = ""
        while True:
            response = self.query_database(filter=self.topic_filter(), start_cursor=cursor)
            for page in response.get("results", []):
                rich_text = page.get("properties", {}).get("Zotero Key", {}).get("rich_text", [])
                if rich_text:
                    key = rich_text[0].get("plain_text") or rich_text[0].get("text", {}).get("content", "")
                    if key:
                        index[key] = page
            if not response.get("has_more"):
                break
            cursor = response.get("next_cursor") or ""
        self._page_index = index
        logger.info(f"Indexed {len(index)} existing Notion papers")

    def topic_filter(self) -> Dict[str, Any]:
        return {"property": "Topic", "multi_select": {"contains": self.topic_tag}}

    def query_topic_database(self, filter: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        scoped_filter = self.topic_filter()
        if filter:
            scoped_filter = {"and": [self.topic_filter(), filter]}
        return self.query_database(filter=scoped_filter)

    def list_topic_pages(self, filter: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        scoped_filter: Dict[str, Any] = self.topic_filter()
        if filter:
            scoped_filter = {"and": [self.topic_filter(), filter]}
        pages: List[Dict[str, Any]] = []
        cursor = ""
        while True:
            response = self.query_database(filter=scoped_filter, start_cursor=cursor)
            pages.extend(response.get("results", []))
            if not response.get("has_more"):
                return pages
            cursor = response.get("next_cursor") or ""

    def get_deep_read_papers(self) -> List[Dict[str, Any]]:
        """Select papers for deep read.

        Priority:
        1. Human Decision = Must Read (always included)
        2. If deep_read_selection.enabled=True, additional top_n ranked papers
           (excluding those already selected as Must Read)
        """
        pages = self.list_topic_pages()
        selected: List[Dict[str, Any]] = []
        selected_ids = set()

        # 1. Always include Human Decision = Must Read
        for page in pages:
            decision = page.get("properties", {}).get("Human Decision", {}).get("select") or {}
            if decision.get("name") == "Must Read":
                selected.append(page)
                selected_ids.add(page["id"])

        must_read_count = len(selected)

        # 2. Optional: add top_n ranked papers from remaining (non-Must-Read)
        if self.deep_read_selection.enabled and self.deep_read_selection.top_n:
            scored = [
                page for page in pages
                if page["id"] not in selected_ids
                and page.get("properties", {}).get("Priority Score", {}).get("number") is not None
            ]
            scored.sort(
                key=lambda page: page["properties"]["Priority Score"]["number"],
                reverse=True,
            )
            for page in scored[:self.deep_read_selection.top_n]:
                selected.append(page)
                selected_ids.add(page["id"])

        logger.info(
            f"Selected {len(selected)} papers for deep read "
            f"(Must Read={must_read_count}, "
            f"auto top_n={self.deep_read_selection.top_n if self.deep_read_selection.enabled else 0})"
        )
        return selected

    @staticmethod
    def _rich_text(value: str) -> Dict[str, Any]:
        return {"rich_text": [{"text": {"content": (value or "")[:NOTION_TEXT_LIMIT]}}]}

    def _speed_card_markdown(self, metadata: PaperMetadata, speed_card: SpeedCard) -> str:
        def bullets(items: List[str]) -> str:
            return "\n".join(f"- {item}" for item in items) if items else "- Unknown"

        return (
            f"## AI Speed Card\n\n"
            f"**Paper:** {metadata.title}\n\n"
            f"**Suggestion:** {speed_card.ai_suggestion} | **Confidence:** {speed_card.confidence} | "
            f"**Priority:** {speed_card.priority_score}\n\n"
            f"### Summary\n{bullets(speed_card.summary_zh or speed_card.summary_en)}\n\n"
            f"### Innovation\n{speed_card.one_line_innovation or 'Unknown'}\n\n"
            f"### Research Problem\n{speed_card.research_problem or 'Unknown'}\n\n"
            f"### Core Method\n{speed_card.core_method or 'Unknown'}\n\n"
            f"### Dataset / Baselines\n"
            f"- Dataset: {speed_card.dataset or 'Unknown'}\n"
            f"- Baselines: {speed_card.baselines or 'Unknown'}\n\n"
            f"### Main Evidence\n{bullets(speed_card.key_evidence)}\n\n"
            f"### Risks To Check\n{bullets(speed_card.risk_need_check)}\n\n"
            f"### Contributions\n{bullets(speed_card.main_contributions)}\n\n"
            f"### Limitations\n{bullets(speed_card.limitations)}\n"
        )

    @_retry_on_network_error
    def sync_ai_details(self, page_id: str, metadata: PaperMetadata, speed_card: SpeedCard) -> None:
        """Replace the managed expandable AI block while retaining human page content."""
        children = self.client.blocks.children.list(block_id=page_id).get("results", [])
        for block in children:
            if block.get("type") != "toggle":
                continue
            text = block.get("toggle", {}).get("rich_text", [])
            title = text[0].get("plain_text", "") if text else ""
            if title == AI_DETAILS_TITLE:
                self.client.blocks.delete(block_id=block["id"])

        markdown = self._speed_card_markdown(metadata, speed_card)
        chunks = [markdown[i:i + NOTION_TEXT_LIMIT] for i in range(0, len(markdown), NOTION_TEXT_LIMIT)]
        detail_children = [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": chunk}}]},
            }
            for chunk in chunks
        ]
        self.client.blocks.children.append(
            block_id=page_id,
            children=[{
                "object": "block",
                "type": "toggle",
                "toggle": {
                    "rich_text": [{"type": "text", "text": {"content": AI_DETAILS_TITLE}}],
                    "children": detail_children,
                },
            }],
        )
    def upsert_paper(self, metadata: PaperMetadata, speed_card: Optional[SpeedCard] = None):
        existing_page = self.find_page_by_zotero_key(metadata.zotero_key)

        properties = {
            "Title": {"title": [{"text": {"content": metadata.title}}]},
            "Zotero Key": {"rich_text": [{"text": {"content": metadata.zotero_key}}]},
            "Authors": {"rich_text": [{"text": {"content": metadata.authors}}]},
            "Venue": {"rich_text": [{"text": {"content": metadata.venue}}]},
            "DOI": {"rich_text": [{"text": {"content": metadata.doi}}]},
            "Citation Key": {"rich_text": [{"text": {"content": metadata.citation_key}}]},
            "PDF Status": {"select": {"name": "Has PDF" if metadata.has_pdf else "No PDF"}},
            "Topic": {"multi_select": [{"name": self.topic_tag}]},
        }

        if metadata.url:
            properties["URL"] = {"url": metadata.url}
        if metadata.zotero_link:
            properties["Zotero Link"] = {"url": metadata.zotero_link}
        if metadata.year:
            properties["Year"] = {"number": metadata.year}

        # Apply Speed Card data if available
        if speed_card:
            properties["AI Suggestion"] = {"select": {"name": speed_card.ai_suggestion}}
            properties["Confidence"] = {"select": {"name": speed_card.confidence}}
            properties["Priority Score"] = {"number": speed_card.priority_score}
            properties["One-line Innovation"] = {"rich_text": [{"text": {"content": speed_card.one_line_innovation}}]}
            properties["Research Problem"] = self._rich_text(speed_card.research_problem)
            properties["Method"] = self._rich_text(speed_card.core_method)
            properties["Dataset"] = self._rich_text(speed_card.dataset)
            properties["Key Evidence"] = self._rich_text("\n".join(speed_card.key_evidence))
            properties["Risk / Need Check"] = self._rich_text("\n".join(speed_card.risk_need_check))
            properties["Relevance Score"] = {"number": speed_card.topic_relevance_score}
            properties["Novelty Score"] = {"number": speed_card.novelty_score}
            properties["Reproducibility Score"] = {"number": speed_card.reproducibility_score}
            properties["Last AI Update"] = {"date": {"start": datetime.now(timezone.utc).isoformat()}}
            properties["Status"] = {"select": {"name": "Speed Card Done"}}
            if speed_card.code_url.lower().startswith(("http://", "https://")):
                properties["Code URL"] = {"url": speed_card.code_url}
            # Summary CN/EN
            if speed_card.summary_zh:
                properties["Summary CN"] = {"rich_text": [{"text": {"content": "\n".join(speed_card.summary_zh)}}]}
            if speed_card.summary_en:
                properties["Summary EN"] = {"rich_text": [{"text": {"content": "\n".join(speed_card.summary_en)}}]}
        elif not existing_page:
            properties["Status"] = {"select": {"name": "Collected"}}

        # Handle human decision preservation
        if existing_page and self.preserve_human_decision:
            existing_decision = existing_page.get("properties", {}).get("Human Decision", {}).get("select")
            if existing_decision and existing_decision.get("name") and existing_decision.get("name") != "Unreviewed":
                # Do not override existing non-unreviewed decision
                pass
            else:
                properties["Human Decision"] = {"select": {"name": "Unreviewed"}}
        elif not existing_page:
             properties["Human Decision"] = {"select": {"name": "Unreviewed"}}

        try:
            if existing_page:
                page_id = existing_page["id"]
                page = self._update_page(page_id, properties)
                logger.info(f"Updated Notion page for {metadata.zotero_key}")
            else:
                page = self._create_page(properties)
                logger.info(f"Created Notion page for {metadata.zotero_key}")
            if self._page_index is not None:
                self._page_index[metadata.zotero_key] = page
            if speed_card:
                self.sync_ai_details(page["id"], metadata, speed_card)
        except Exception as e:
            err_msg = str(e)
            # If property validation error, retry with problematic properties removed
            if "is not a property that exists" in err_msg:
                logger.warning(f"Property validation error, retrying with safe properties only: {e}")
                safe_properties = {k: v for k, v in properties.items()
                                   if k in ("Title", "Zotero Key", "Authors", "Venue", "DOI", "Topic",
                                            "Citation Key", "PDF Status", "Status", "Year",
                                            "Human Decision", "AI Suggestion", "Confidence",
                                            "Priority Score", "One-line Innovation")}
                try:
                    if existing_page:
                        page = self._update_page(existing_page["id"], safe_properties)
                    else:
                        page = self._create_page(safe_properties)
                    if speed_card:
                        self.sync_ai_details(page["id"], metadata, speed_card)
                    logger.info(f"Upserted Notion page for {metadata.zotero_key} with safe properties")
                except Exception as e2:
                    logger.error(f"Failed upsert even with safe properties for {metadata.zotero_key}: {e2}")
                    raise
            else:
                logger.error(f"Failed to upsert Notion page for {metadata.zotero_key}: {e}")
                raise

    def get_must_read_papers(self) -> List[Dict[str, Any]]:
        """Fetch all papers where Human Decision is 'Must Read' and Translation Status is not Done/Queued."""
        logger.info("Fetching 'Must Read' papers from Notion...")
        must_reads = []
        try:
            has_more = True
            next_cursor = None

            while has_more:
                response = self.query_database(
                    filter={
                        "and": [
                            self.topic_filter(),
                            {
                                "property": "Human Decision",
                                "select": {
                                    "equals": "Must Read"
                                }
                            },
                            {
                                "property": "Translation Status",
                                "select": {
                                    "does_not_equal": "Done"
                                }
                            }
                        ]
                    },
                    start_cursor=next_cursor or ""
                )
                must_reads.extend(response.get("results", []))

                has_more = response.get("has_more", False)
                next_cursor = response.get("next_cursor")

            return must_reads
        except Exception as e:
            logger.error(f"Failed to fetch Must Read papers from Notion: {e}")
            return []

    def update_translation_status(self, page_id: str, status: str, mono_pdf: str = "", dual_pdf: str = "", error_msg: str = ""):
        properties = {
            "Translation Status": {"select": {"name": status}}
        }
        if mono_pdf:
            properties["Translated Mono PDF"] = {"rich_text": [{"text": {"content": mono_pdf}}]}
        if dual_pdf:
            properties["Translated Dual PDF"] = {"rich_text": [{"text": {"content": dual_pdf}}]}
        if error_msg:
            properties["Translation Error"] = {"rich_text": [{"text": {"content": error_msg}}]}

        if status == "Done":
             properties["Status"] = {"select": {"name": "Translation Done"}}
        elif status == "Queued":
             properties["Status"] = {"select": {"name": "Translation Queued"}}

        try:
            self._update_page(page_id, properties)
            logger.info(f"Updated Notion page {page_id} translation status to {status}")
        except Exception as e:
            logger.error(f"Failed to update translation status in Notion: {e}")
