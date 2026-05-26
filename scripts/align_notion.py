import os
import json
from dotenv import load_dotenv
import httpx
from notion_client import Client
from paperflow.config import get_config

load_dotenv()
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
config = get_config()

client = Client(
    auth=NOTION_TOKEN,
    notion_version="2025-09-03",
    client=httpx.Client(trust_env=config.notion.use_environment_proxy, timeout=60.0),
)

expected_properties = {
    # Base
    "Title": {"title": {}},
    "Authors": {"rich_text": {}},
    "Year": {"number": {"format": "number"}},
    "Venue": {"rich_text": {}},
    "DOI": {"rich_text": {}},
    "URL": {"url": {}},
    "Zotero Key": {"rich_text": {}},
    "Citation Key": {"rich_text": {}},
    "Zotero Link": {"url": {}},
    "Local PDF Path": {"rich_text": {}},
    "PDF Status": {
        "select": {
            "options": [
                {"name": "Has PDF", "color": "green"},
                {"name": "No PDF", "color": "red"},
                {"name": "Unknown", "color": "gray"}
            ]
        }
    },

    # Screening
    "Topic": {"multi_select": {}},
    "Method": {"rich_text": {}},
    "Dataset": {"rich_text": {}},
    "Code URL": {"url": {}},
    "AI Suggestion": {
        "select": {
            "options": [
                {"name": "Must Read", "color": "red"},
                {"name": "Scan", "color": "yellow"},
                {"name": "Park", "color": "blue"},
                {"name": "Exclude", "color": "gray"}
            ]
        }
    },
    "Human Decision": {
        "select": {
            "options": [
                {"name": "Unreviewed", "color": "gray"},
                {"name": "Must Read", "color": "red"},
                {"name": "Scan", "color": "yellow"},
                {"name": "Park", "color": "blue"},
                {"name": "Exclude", "color": "default"}
            ]
        }
    },
    "Status": {
        "select": {
            "options": [
                {"name": "Collected", "color": "default"},
                {"name": "Speed Card Done", "color": "blue"},
                {"name": "Need Human Review", "color": "yellow"},
                {"name": "Must Read Confirmed", "color": "red"},
                {"name": "Translation Queued", "color": "purple"},
                {"name": "Translation Done", "color": "green"},
                {"name": "Deep Reading Done", "color": "blue"},
                {"name": "Exported to Obsidian", "color": "orange"},
                {"name": "Park", "color": "default"},
                {"name": "Exclude", "color": "gray"}
            ]
        }
    },
    "Confidence": {
        "select": {
            "options": [
                {"name": "High", "color": "green"},
                {"name": "Medium", "color": "yellow"},
                {"name": "Low", "color": "red"}
            ]
        }
    },
    "Priority Score": {"number": {"format": "number"}},
    "Relevance Score": {"number": {"format": "number"}},
    "Novelty Score": {"number": {"format": "number"}},
    "Reproducibility Score": {"number": {"format": "number"}},
    "One-line Innovation": {"rich_text": {}},
    "Research Problem": {"rich_text": {}},
    "Summary CN": {"rich_text": {}},
    "Summary EN": {"rich_text": {}},
    "Key Evidence": {"rich_text": {}},
    "Risk / Need Check": {"rich_text": {}},
    "AI Raw JSON": {"rich_text": {}},
    "Last AI Update": {"date": {}},

    # Translation
    "Translation Needed": {"checkbox": {}},
    "Translation Status": {
        "select": {
            "options": [
                {"name": "Not Needed", "color": "default"},
                {"name": "Queued", "color": "blue"},
                {"name": "Running", "color": "yellow"},
                {"name": "Done", "color": "green"},
                {"name": "Failed", "color": "red"},
                {"name": "Skipped", "color": "gray"}
            ]
        }
    },
    "Translation Engine": {
        "select": {
            "options": [
                {"name": "pdf2zh", "color": "blue"},
                {"name": "pdf2zh_next", "color": "purple"}
            ]
        }
    },
    "Translation Service": {"rich_text": {}},
    "Translated Dual PDF": {"rich_text": {}},
    "Translated Mono PDF": {"rich_text": {}},
    "Translation Error": {"rich_text": {}},
    "Translation Updated At": {"date": {}},
    "Translation Retry Count": {"number": {"format": "number"}},
    "Zotero Attachment Status": {
        "select": {
            "options": [
                {"name": "Not Attached", "color": "default"},
                {"name": "Attached", "color": "green"},
                {"name": "Failed", "color": "red"},
                {"name": "Skipped", "color": "gray"}
            ]
        }
    },
    "Zotero Attachment Error": {"rich_text": {}},

    # Obsidian
    "Obsidian Note Path": {"rich_text": {}},
    "Exported At": {"date": {}}
}

def align_database():
    print(f"Fetching data source schema for database {NOTION_DATABASE_ID}...")
    db = client.databases.retrieve(database_id=NOTION_DATABASE_ID)
    data_source_id = config.notion.data_source_id or db.get("data_sources", [])[0]["id"]
    data_source = client.data_sources.retrieve(data_source_id=data_source_id)
    current_properties = data_source.get("properties", {})

    properties_to_update = {}

    for prop_name, prop_schema in expected_properties.items():
        if prop_name not in current_properties:
            print(f"Missing property: {prop_name}, will be added.")
            properties_to_update[prop_name] = prop_schema
        else:
            # For select properties, we might want to sync options, but the Notion API
            # might complain if we try to change existing option IDs or colors.
            # To be safe and simple, we'll just log that it's there.
            # print(f"Property exists: {prop_name}")
            # Notion API allows creating new select options by just updating the schema
            curr_type = current_properties[prop_name]["type"]
            expected_type = list(prop_schema.keys())[0]
            if curr_type != expected_type and curr_type != "title":
                 print(f"Type mismatch for {prop_name}: expected {expected_type}, got {curr_type}")
            elif expected_type == "select":
                curr_options = current_properties[prop_name]["select"]["options"]
                curr_option_names = {opt["name"] for opt in curr_options}
                expected_options = prop_schema["select"].get("options", [])
                missing_options = [opt for opt in expected_options if opt["name"] not in curr_option_names]
                if missing_options:
                    print(f"Adding missing options to {prop_name}: {[o['name'] for o in missing_options]}")
                    merged_options = curr_options + missing_options
                    properties_to_update[prop_name] = {"select": {"options": merged_options}}

    if properties_to_update:
        print("Updating database with missing properties/options...")
        try:
            client.data_sources.update(
                data_source_id=data_source_id,
                properties=properties_to_update
            )
            print("Database updated successfully.")
        except Exception as e:
            print(f"Error updating database: {e}")
            # Fallback: update properties one by one
            print("Trying to update properties one by one...")
            for prop_name, prop_val in properties_to_update.items():
                 try:
                      client.data_sources.update(
                           data_source_id=data_source_id,
                           properties={prop_name: prop_val}
                      )
                      print(f"Updated {prop_name} successfully.")
                 except Exception as ex:
                      print(f"Failed to update {prop_name}: {ex}")
    else:
        print("Database schema is already aligned.")

if __name__ == "__main__":
    align_database()
