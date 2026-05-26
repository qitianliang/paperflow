"""Un-archive test Notion pages so they can be used in the workflow test."""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
from paperflow.notion_client import NotionClientWrapper

notion = NotionClientWrapper()
resp = notion.query_database(filter={
    "property": "Zotero Key",
    "rich_text": {"contains": ""}
})
pages = resp.get("results", []) if resp else []
test_keys = {"CS7HSLJS", "8Q4FPYRN"}

# First query with the actual Notion API to get ALL pages (including archived)
# notion_client SDK query doesn't return archived by default, but archive status is in the data
# Actually we need to search for them. Let me just query all and try.
try:
    all_pages = notion.client.search(
        query="",
        filter={"property": "object", "value": "page"},
        start_cursor=None,
        page_size=100,
    ).get("results", [])
except Exception as e:
    print(f"Search failed: {e}, trying database query...")
    all_pages = notion.query_database().get("results", [])

found = 0
for p in all_pages:
    props = p.get("properties", {})
    try:
        zk = props.get("Zotero Key", {}).get("rich_text", [{}])[0].get("plain_text", "?")
    except (IndexError, KeyError, TypeError):
        zk = "?"
    if zk in test_keys:
        pid = p["id"]
        archived = p.get("archived", False)
        if archived:
            print(f"  Un-archiving {zk} ({pid})...")
            try:
                notion.client.pages.update(page_id=pid, archived=False)
                print(f"    Done!")
            except Exception as e:
                print(f"    FAILED: {e}")
        else:
            print(f"  {zk} is already live")
        found += 1

if found < len(test_keys):
    print(f"\nNote: Found {found}/{len(test_keys)} test pages in Notion.")
    print("Run `paperflow run-screening` to recreate them if needed.")
print("Done!")