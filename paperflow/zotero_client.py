import re
from typing import List, Optional, Dict, Any
from pyzotero import zotero
from paperflow.config import get_config
from paperflow.schemas import PaperMetadata
from paperflow.logging_utils import get_logger

logger = get_logger(__name__)

class ZoteroClient:
    def __init__(self):
        config = get_config()
        self.zot = zotero.Zotero(
            config.zotero.user_id,
            config.zotero.library_type,
            config.zotero.api_key
        )

    def get_collection_items(self, collection_id: str) -> List[Dict[str, Any]]:
        logger.info(f"Fetching items from Zotero collection {collection_id}...")
        try:
            items = self.zot.everything(self.zot.collection_items(collection_id))
            parent_count = sum(1 for item in items if not item.get("data", {}).get("parentItem"))
            logger.info(
                f"Fetched {len(items)} Zotero records: {parent_count} top-level papers "
                f"and {len(items) - parent_count} child attachments/notes"
            )
            return items
        except Exception as e:
            logger.error(f"Failed to fetch Zotero items: {e}")
            return []

    def parse_item(self, item: Dict[str, Any], items: List[Dict[str, Any]]) -> Optional[PaperMetadata]:
        data = item.get('data', {})
        item_type = data.get('itemType')
        if item_type in ['attachment', 'note']:
            return None

        zotero_key = data.get('key')
        title = data.get('title', '')

        # Parse authors
        creators = data.get('creators', [])
        author_names = []
        for c in creators:
            if 'lastName' in c:
                name = c.get('lastName', '')
                if 'firstName' in c:
                    name = f"{c.get('firstName', '')} {name}"
                author_names.append(name)
            elif 'name' in c:
                author_names.append(c.get('name'))
        authors = ", ".join(author_names)

        # Parse year
        date_str = data.get('date', '')
        year = None
        if date_str:
            match = re.search(r'\d{4}', date_str)
            if match:
                year = int(match.group(0))

        venue = data.get('publicationTitle', data.get('proceedingsTitle', data.get('university', '')))

        doi = data.get('DOI', '')
        url = data.get('url', '')
        # Enhance venue and year with LLM
        venue, year = self._enhance_venue_and_year(title, venue, year, url)
        abstract = data.get('abstractNote', '')
        tags = [t.get('tag') for t in data.get('tags', []) if t.get('tag')]

        # Zotero link
        library_id = self.zot.library_id
        library_type = "users" if self.zot.library_type == "user" else "groups"
        zotero_link = f"zotero://select/{library_type}/{library_id}/items/{zotero_key}"

        # Citation Key (Better BibTeX)
        citation_key = ""
        extra = data.get('extra', '')
        if extra:
            match = re.search(r'Citation Key:\s*([^\s]+)', extra)
            if match:
                citation_key = match.group(1)
        if not citation_key:
            citation_key = zotero_key

        # PDF Attachment
        has_pdf = False
        pdf_attachment_key = ""

        # Find child attachments
        for child in items:
            child_data = child.get('data', {})
            if child_data.get('parentItem') == zotero_key and child_data.get('itemType') == 'attachment':
                if child_data.get('contentType') == 'application/pdf':
                    has_pdf = True
                    pdf_attachment_key = child_data.get('key')
                    break

        return PaperMetadata(
            zotero_key=zotero_key,
            title=title,
            authors=authors,
            year=year,
            venue=venue,
            doi=doi,
            url=url,
            citation_key=citation_key,
            zotero_link=zotero_link,
            abstract=abstract,
            tags=tags,
            has_pdf=has_pdf,
            pdf_attachment_key=pdf_attachment_key
        )

    def _enhance_venue_and_year(self, title: str, venue: str, year: Optional[int], url: str) -> tuple[str, Optional[int]]:
        """Use LLM to enhance venue and year information."""
        try:
            from paperflow.venue_enhancer import VenueYearEnhancer

            enhancer = VenueYearEnhancer()
            enhanced_venue, enhanced_year, confidence = enhancer.enhance(
                title=title,
                current_venue=venue,
                current_year=year,
                url=url
            )

            logger.info(
                f"Enhanced venue/year for '{title[:50]}...': "
                f"venue={enhanced_venue}, year={enhanced_year}, confidence={confidence:.2f}"
            )
            return enhanced_venue, enhanced_year

        except Exception as e:
            logger.warning(f"Failed to enhance venue/year for '{title[:50]}...': {e}")
            return venue, year
