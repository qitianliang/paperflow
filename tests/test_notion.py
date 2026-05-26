"""Live Notion diagnostic for the configured topic and data source."""
from paperflow.notion_client import NotionClientWrapper


def main() -> None:
    notion = NotionClientWrapper()
    data_source = notion.client.data_sources.retrieve(data_source_id=notion.data_source_id)
    properties = sorted(data_source.get("properties", {}).keys())
    pages = notion.list_topic_pages()
    print(f"Data source: {notion.data_source_id}")
    print(f"Topic: {notion.topic_tag}")
    print(f"Properties ({len(properties)}): {properties}")
    print(f"Topic pages: {len(pages)}")


if __name__ == "__main__":
    main()
