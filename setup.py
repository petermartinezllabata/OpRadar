"""
OpRadar setup — creates all required Notion database properties if they do not exist.
Safe to run multiple times: existing properties are skipped, not overwritten.

Usage:
    python setup.py
"""

import os
import sys

from dotenv import load_dotenv
from notion_client import Client


REQUIRED_PROPERTIES = {
    "URL": {"rich_text": {}},
    "Status": {
        "select": {
            "options": [
                {"name": "Pending"},
                {"name": "Assessed"},
                {"name": "Fetch Failed"},
            ]
        }
    },
    "Organization": {"rich_text": {}},
    "Type": {
        "select": {
            "options": [
                {"name": "Consultancy"},
                {"name": "Full-time"},
                {"name": "Roster"},
            ]
        }
    },
    "Deadline": {"date": {}},
    "Notes": {"rich_text": {}},
    "Recommendation": {
        "select": {
            "options": [
                {"name": "Strong Apply"},
                {"name": "Worth Reviewing"},
                {"name": "Maybe"},
                {"name": "Skip"},
            ]
        }
    },
    "Overall Score": {"number": {}},
    "Technical Fit": {"number": {}},
    "Thematic Fit": {"number": {}},
    "Modality Fit": {"number": {}},
    "Compensation Fit": {"number": {}},
    "Geographic Fit": {"number": {}},
    "Deadline Practicality": {"number": {}},
    "Strategic Value": {"number": {}},
    "Why It Matches": {"rich_text": {}},
    "Main Risks / Gaps": {"rich_text": {}},
    "Suggested Positioning": {"rich_text": {}},
    "Countries": {"rich_text": {}},
    "Career Categories": {"rich_text": {}},
    "Days Left": {"number": {}},
    "Date Posted": {"date": {}},
    "LOE Min": {"number": {}},
    "LOE Max": {"number": {}},
    "LOE Notes": {"rich_text": {}},
}


def main() -> None:
    load_dotenv()

    notion_token = os.environ.get("NOTION_TOKEN")
    database_id = os.environ.get("NOTION_DATABASE_ID")

    if not notion_token or not database_id:
        print("Error: NOTION_TOKEN and NOTION_DATABASE_ID must be set in .env")
        sys.exit(1)

    notion = Client(auth=notion_token)

    print(f"Connecting to Notion database {database_id} ...")
    db = notion.databases.retrieve(database_id=database_id)
    existing = db.get("properties", {})

    to_create = {}
    for name, definition in REQUIRED_PROPERTIES.items():
        if name in existing:
            print(f"  [skip]    {name}")
        else:
            to_create[name] = definition
            print(f"  [create]  {name}")

    if to_create:
        notion.databases.update(database_id=database_id, properties=to_create)
        print(f"\nCreated {len(to_create)} property(ies).")
    else:
        print("\nAll properties already exist — nothing to do.")

    print("OpRadar setup complete.")


if __name__ == "__main__":
    main()
