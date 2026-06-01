"""Remove test-topic outputs without destroying other research topics by default.

Supports two modes:
1. Topic-scoped cleanup (default): archive Notion pages matching current topic,
   delete local cache/staging/notes for current topic.
2. Collection-scoped cleanup (--collection): fetch Zotero collection to get all
   Zotero Keys, then archive ALL Notion pages containing those keys (across ALL
   topics), plus delete local outputs.

The --collection mode is designed for thorough test cleanup when a collection
has been synced under multiple topics."""
import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from paperflow.cache import cache
from paperflow.config import get_config
from paperflow.notion_client import NotionClientWrapper


def remove_tree(path: str) -> None:
    if os.path.exists(path):
        shutil.rmtree(path)
    os.makedirs(path, exist_ok=True)
    print(f"Deleted local output: {path}")


def archive_pages_by_keys(notion: NotionClientWrapper, keys: set, all_topics: bool = False) -> int:
    """Archive Notion pages whose Zotero Key is in the given set.

    If all_topics=True, search across all topics (ignore topic filter).
    If all_topics=False, only search within current topic.
    """
    if not keys:
        return 0

    archived = 0
    for key in keys:
        if all_topics:
            # Search by Zotero Key only, no topic restriction
            response = notion.query_database(
                filter={"property": "Zotero Key", "rich_text": {"equals": key}}
            )
        else:
            # Topic-scoped search
            response = notion.query_database(filter={
                "and": [
                    {"property": "Zotero Key", "rich_text": {"equals": key}},
                    notion.topic_filter(),
                ]
            })

        for page in response.get("results", []):
            page_id = page["id"]
            try:
                notion.archive_page(page_id)
                archived += 1
                props = page.get("properties", {})
                title = props.get("Title", {}).get("title", [{}])[0].get("plain_text", "")
                tags = props.get("Topic", {}).get("multi_select", [])
                tag_names = ", ".join(t.get("name", "") for t in tags) if tags else "untagged"
                print(f"Archived [{key}] '{title}' (topics: {tag_names})")
            except Exception as e:
                print(f"Failed to archive page {page_id} for key {key}: {e}")
    return archived


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clean up test data: archive Notion pages and delete local files."
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="Required before deleting/archiving outputs."
    )
    parser.add_argument(
        "--collection", type=str, default="",
        help="Zotero collection ID. If provided, archives ALL Notion pages with matching Zotero Keys across ALL topics."
    )
    parser.add_argument(
        "--include-legacy-cache", action="store_true",
        help="Delete old unscoped cache/staging items."
    )
    args = parser.parse_args()

    if not args.execute:
        raise SystemExit(
            "Dry guard: pass --execute to archive Notion pages and delete files.\n"
            "Examples:\n"
            "  python scripts/cleanup_test.py --execute          # topic-scoped cleanup\n"
            "  python scripts/cleanup_test.py --execute --collection 52MFN99M  # collection-scoped cleanup"
        )

    config = get_config()
    notion = NotionClientWrapper()

    keys = set()
    all_topics = bool(args.collection)

    if args.collection:
        # Collection-scoped: fetch Zotero collection to get all keys
        from paperflow.zotero_client import ZoteroClient
        client = ZoteroClient()
        items = client.get_collection_items(args.collection)
        for item in items:
            metadata = client.parse_item(item, items)
            if metadata:
                keys.add(metadata.zotero_key)
        print(f"Collection {args.collection} has {len(keys)} papers. Will archive across ALL topics.")
    else:
        # Topic-scoped: get keys from current topic pages
        pages = notion.list_topic_pages()
        for page in pages:
            props = page.get("properties", {})
            rich_text = props.get("Zotero Key", {}).get("rich_text", [])
            if rich_text:
                keys.add(rich_text[0].get("plain_text", ""))
        print(f"Topic '{config.project.topic_label}' has {len(keys)} papers. Will archive within current topic.")

    # Archive Notion pages
    archived = archive_pages_by_keys(notion, keys, all_topics=all_topics)
    print(f"Archived {archived} Notion page(s).")

    # Delete local outputs
    remove_tree(cache.base_dir)
    remove_tree(os.path.join(config.translation.paths.staging_dir, config.project.topic_slug))
    remove_tree(os.path.join(config.translation.paths.output_dir, config.project.topic_slug))
    note_dir = os.path.join(
        config.obsidian.vault_path,
        config.obsidian.literature_note_dir,
        config.project.topic_slug,
    )
    if config.obsidian.vault_path:
        remove_tree(note_dir)

    # Legacy cleanup
    if args.include_legacy_cache:
        legacy_roots = (
            os.path.join("data", "cache"),
            config.translation.paths.staging_dir,
            os.path.join("..", "data", "cache"),
            os.path.join("..", "data", "translation_staging"),
            config.translation.paths.output_dir,
        )
        for key in keys:
            for root in legacy_roots:
                path = os.path.join(root, key)
                if os.path.isdir(path):
                    shutil.rmtree(path)
                    print(f"Deleted legacy output: {path}")

        if config.obsidian.vault_path:
            notes_root = os.path.join(config.obsidian.vault_path, config.obsidian.literature_note_dir)
            for root, _, files in os.walk(notes_root):
                if os.path.normpath(root).startswith(os.path.normpath(note_dir)):
                    continue
                for filename in files:
                    if not filename.lower().endswith(".md"):
                        continue
                    path = os.path.join(root, filename)
                    try:
                        with open(path, "r", encoding="utf-8") as handle:
                            content = handle.read()
                    except (OSError, UnicodeDecodeError):
                        continue
                    if any(key and key in content for key in keys):
                        os.remove(path)
                        print(f"Deleted legacy Obsidian note: {path}")

    print("Cleanup completed.")
    print("\n[NOTE] Notion archive is a logical delete (pages go to Trash).")
    print("       If your Notion database has a 'unique_id' field, the counter")
    print("       will NOT reset after cleanup. To reset unique_id numbering,")
    print("       you must manually empty Trash in the Notion UI.")
    print("       (Notion API does not support permanent deletion.)")


if __name__ == "__main__":
    main()
