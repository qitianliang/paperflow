"""Remove test-topic outputs without destroying other research topics by default."""
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="Required before deleting/archiving outputs.")
    parser.add_argument("--include-legacy-cache", action="store_true", help="Delete old unscoped cache/staging items.")
    args = parser.parse_args()
    if not args.execute:
        raise SystemExit("Dry guard: pass --execute to archive current-topic Notion pages and delete files.")

    config = get_config()
    notion = NotionClientWrapper()
    pages = notion.list_topic_pages()
    keys = set()
    for page in pages:
        props = page.get("properties", {})
        rich_text = props.get("Zotero Key", {}).get("rich_text", [])
        if rich_text:
            keys.add(rich_text[0].get("plain_text", ""))
        notion.archive_page(page["id"])
    print(f"Archived {len(pages)} Notion pages for topic '{config.project.topic_label}'")

    if args.include_legacy_cache:
        for key in keys:
            legacy_pages = notion.query_database(filter={
                "property": "Zotero Key",
                "rich_text": {"equals": key},
            }).get("results", [])
            for page in legacy_pages:
                tags = page.get("properties", {}).get("Topic", {}).get("multi_select", [])
                if not tags:
                    notion.archive_page(page["id"])
                    print(f"Archived legacy untagged Notion page: {key}")

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


if __name__ == "__main__":
    main()
