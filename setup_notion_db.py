#!/usr/bin/env python3
"""
Create the LinkedIn Research Notion database with all required properties.

Usage:
    1. Set NOTION_API_KEY in .env (or as env var)
    2. Set NOTION_PARENT_PAGE_ID in .env (the Notion page where the DB should live)
    3. Run: python3 setup_notion_db.py

The script will:
- Create the database with all required properties
- Auto-update your .env with NOTION_RESEARCH_DB_ID
"""

import json
import os
import sys

import httpx

NOTION_API_VERSION = "2022-06-28"
NOTION_BASE = "https://api.notion.com/v1"


def load_env():
    """Load .env file if it exists."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())


def main():
    load_env()

    api_key = os.environ.get("NOTION_API_KEY")
    parent_page_id = os.environ.get("NOTION_PARENT_PAGE_ID")

    if not api_key:
        print("ERROR: NOTION_API_KEY not set. Add it to .env or export it.")
        sys.exit(1)

    if not parent_page_id:
        print("ERROR: NOTION_PARENT_PAGE_ID not set.")
        print("Go to the Notion page where you want the database, copy the page ID from the URL.")
        print("Add it to .env as: NOTION_PARENT_PAGE_ID=xxxxx")
        sys.exit(1)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    }

    payload = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [{"type": "text", "text": {"content": "LinkedIn Research"}}],
        "properties": {
            "Name": {"title": {}},
            "Content": {"rich_text": {}},
            "URL": {"url": {}},
            "Author URL": {"url": {}},
            "Likes": {"number": {"format": "number"}},
            "Comments": {"number": {"format": "number"}},
            "Source": {
                "select": {
                    "options": [
                        {"name": "creator-watchlist", "color": "blue"},
                        {"name": "search-query", "color": "green"},
                    ]
                }
            },
            "Posted At": {"date": {}},
            "Hook Type": {
                "select": {
                    "options": [
                        {"name": "provocatie", "color": "red"},
                        {"name": "resultaat", "color": "green"},
                        {"name": "persoonlijk", "color": "yellow"},
                        {"name": "probleem", "color": "orange"},
                        {"name": "curiosity", "color": "purple"},
                        {"name": "waarde", "color": "blue"},
                    ]
                }
            },
            "Relevance": {
                "select": {
                    "options": [
                        {"name": "hoog", "color": "green"},
                        {"name": "medium", "color": "yellow"},
                        {"name": "laag", "color": "gray"},
                    ]
                }
            },
            "Tags": {"multi_select": {"options": []}},
        },
    }

    print("Creating LinkedIn Research database in Notion...")

    with httpx.Client() as client:
        resp = client.post(
            f"{NOTION_BASE}/databases",
            headers=headers,
            json=payload,
            timeout=30,
        )

        if resp.status_code != 200:
            print(f"ERROR: Notion API returned {resp.status_code}")
            print(resp.text)
            sys.exit(1)

        data = resp.json()
        db_id = data["id"]

    print()
    print("=" * 60)
    print("  DATABASE CREATED SUCCESSFULLY")
    print("=" * 60)
    print(f"  Database ID: {db_id}")
    print()
    print("  Add this to your .env:")
    print(f"  NOTION_RESEARCH_DB_ID={db_id}")
    print("=" * 60)

    # Auto-update .env if possible
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            content = f.read()

        if "NOTION_RESEARCH_DB_ID" not in content:
            with open(env_path, "a") as f:
                f.write(f"\nNOTION_RESEARCH_DB_ID={db_id}\n")
            print("\n  Auto-added to .env!")
        else:
            # Replace existing placeholder or old value
            import re
            content = re.sub(
                r"^NOTION_RESEARCH_DB_ID=.*$",
                f"NOTION_RESEARCH_DB_ID={db_id}",
                content,
                flags=re.MULTILINE,
            )
            with open(env_path, "w") as f:
                f.write(content)
            print("\n  Auto-updated in .env!")


if __name__ == "__main__":
    main()
