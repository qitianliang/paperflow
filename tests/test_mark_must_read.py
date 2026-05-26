"""Mark some papers as Must Read in Notion for testing."""
import os
from dotenv import load_dotenv
load_dotenv()

from paperflow.cache import cache
from paperflow.notion_client import NotionClientWrapper

notion = NotionClientWrapper()

# Check which papers have PDFs
collection_keys = ['8Q4FPYRN','CS7HSLJS','4TMC5EMF','4UL928FC','VEFH32ZB','MPFY9QA9','H5VJMTW7','F2PWVZQY','U58CUEE3','AH7TP9IW']

must_read_keys = []
for key in collection_keys:
    meta = cache.load_json(key, 'metadata.json')
    if meta:
        has_pdf = meta.get('has_pdf', False)
        title = meta.get('title', '?')[:50]
        print(f'{key}: has_pdf={has_pdf}, title={title}')
        if has_pdf:
            must_read_keys.append(key)

print(f'\nPapers with PDFs that can be marked Must Read: {len(must_read_keys)}')

# Mark first 2 papers with PDFs as "Must Read" for testing
for key in must_read_keys[:2]:
    page = notion.find_page_by_zotero_key(key)
    if page:
        notion.client.pages.update(
            page_id=page['id'],
            properties={
                'Human Decision': {'select': {'name': 'Must Read'}},
                'Status': {'select': {'name': 'Must Read Confirmed'}},
            }
        )
        print(f'Marked {key} as Must Read')
    else:
        print(f'No Notion page found for {key}')
