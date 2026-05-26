"""Consolidated Notion API tests — query, properties, raw HTTP."""
import os
import httpx
from dotenv import load_dotenv
load_dotenv()

token = os.getenv("NOTION_TOKEN")
db_id = os.getenv("NOTION_DATABASE_ID")
headers = {
    "Authorization": f"Bearer {token}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}


def test_query_raw():
    """Query database via raw httpx."""
    print("=== test_query_raw ===")
    resp = httpx.post(
        f"https://api.notion.com/v1/databases/{db_id}/query",
        headers=headers,
        json={},
    )
    print(f"Status: {resp.status_code}")
    if resp.status_code == 200:
        results = resp.json().get("results", [])
        print(f"Results: {len(results)}")
        if results:
            props = list(results[0].get("properties", {}).keys())
            print(f"First page properties: {props[:10]}")
    else:
        print(f"Error: {resp.text[:300]}")


def test_query_notion_client():
    """Query via notion_client SDK."""
    print("\n=== test_query_notion_client ===")
    from notion_client import Client
    client = Client(auth=token)
    try:
        r = client.request(path=f"databases/{db_id}/query", method="POST", body={})
        results = r.get("results", [])
        print(f"Results: {len(results)}")
        if results:
            props = list(results[0].get("properties", {}).keys())
            print(f"First page properties: {props[:10]}")
    except Exception as e:
        print(f"Error: {type(e).__name__}: {e}")


def test_db_properties():
    """Read and display database properties."""
    print("\n=== test_db_properties ===")
    resp = httpx.get(
        f"https://api.notion.com/v1/databases/{db_id}",
        headers=headers,
    )
    db = resp.json()
    props = list(db.get("properties", {}).keys())
    print(f"Properties: {props}")


if __name__ == "__main__":
    print(f"DB ID: {db_id}")
    test_query_raw()
    test_query_notion_client()
    test_db_properties()