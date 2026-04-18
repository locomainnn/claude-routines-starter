#!/usr/bin/env python3
"""
Content Analyzer - LinkedIn Research DB Processor

Queries recent posts from the Notion Research DB and prepares them for
analysis. The actual scoring and relevance classification is done inside
your Claude Routine (which calls Claude with your skills attached).

This script is a helper: it knows how to read the DB, filter unanalyzed
posts, and update them with analysis results. Your Routine's prompt
points Claude at this script, calls it, and uses the output.

Environment variables (required):
  NOTION_API_KEY         - Notion integration token
  NOTION_RESEARCH_DB_ID  - Database ID for the LinkedIn Research DB

Environment variables (optional):
  NOTION_POSTS_DB_ID     - If set, high-relevance posts are added as Ideas
  DIGEST_DIR             - Where to write weekly digest .md files
                           Default: ./digests

Usage:
  python3 content_analyzer.py [--dry-run] [--limit N]
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx


def _load_env() -> None:
    """Load .env file from script directory if it exists."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())


_load_env()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

NOTION_API_VERSION = "2022-06-28"
NOTION_BASE = "https://api.notion.com/v1"

# These tags live in the Notion "Hook Type" select. Change the set names
# in setup_notion_db.py if you want a different taxonomy.
VALID_HOOK_TYPES = {"provocatie", "resultaat", "persoonlijk", "probleem", "curiosity", "waarde"}
VALID_RELEVANCE = {"hoog", "medium", "laag"}

DUTCH_MONTHS = {
    1: "Januari", 2: "Februari", 3: "Maart", 4: "April",
    5: "Mei", 6: "Juni", 7: "Juli", 8: "Augustus",
    9: "September", 10: "Oktober", 11: "November", 12: "December",
}

# Digest output directory - configurable via env var
_default_digest_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "digests")
DIGEST_DIR = Path(os.environ.get("DIGEST_DIR", _default_digest_dir)).expanduser()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("content_analyzer")

# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        log.error("Missing required environment variable: %s", name)
        sys.exit(1)
    return value


# ---------------------------------------------------------------------------
# Notion helpers
# ---------------------------------------------------------------------------


def notion_headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    }


def query_unanalyzed_posts(
    client: httpx.Client,
    api_key: str,
    database_id: str,
) -> list[dict]:
    """Query Notion for posts from the past 7 days where Relevance is empty."""
    seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")

    payload = {
        "filter": {
            "and": [
                {
                    "property": "Posted At",
                    "date": {
                        "on_or_after": seven_days_ago,
                    },
                },
                {
                    "property": "Relevance",
                    "select": {
                        "is_empty": True,
                    },
                },
            ]
        },
        "sorts": [
            {
                "property": "Posted At",
                "direction": "descending",
            }
        ],
    }

    url = f"{NOTION_BASE}/databases/{database_id}/query"
    headers = notion_headers(api_key)
    pages: list[dict] = []
    has_more = True
    start_cursor = None

    while has_more:
        if start_cursor:
            payload["start_cursor"] = start_cursor

        for attempt in range(3):
            resp = client.post(url, headers=headers, json=payload, timeout=30)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 5))
                log.warning("Notion 429, retrying in %ds...", retry_after)
                time.sleep(retry_after)
                continue
            resp.raise_for_status()
            break
        else:
            raise RuntimeError("Notion query failed after 3 retries (429)")

        data = resp.json()
        pages.extend(data.get("results", []))
        has_more = data.get("has_more", False)
        start_cursor = data.get("next_cursor")

    return pages


def extract_post_data(page: dict) -> dict:
    """Extract relevant fields from a Notion page object."""
    props = page.get("properties", {})
    page_id = page["id"]

    # Author - rich_text or title property
    author = _get_text_prop(props, "Author")
    if not author:
        author = _get_title_prop(props, "Name") or _get_title_prop(props, "Title") or "Unknown"

    # Content
    content = _get_text_prop(props, "Content") or _get_text_prop(props, "Post Content") or ""

    # URL
    url = _get_url_prop(props, "URL") or _get_url_prop(props, "Link") or ""

    # Engagement
    likes = _get_number_prop(props, "Likes") or 0
    comments = _get_number_prop(props, "Comments") or 0

    # Posted At
    posted_at = _get_date_prop(props, "Posted At") or ""

    return {
        "page_id": page_id,
        "author": author,
        "content": content,
        "url": url,
        "likes": likes,
        "comments": comments,
        "posted_at": posted_at,
    }


def _get_text_prop(props: dict, name: str) -> str:
    prop = props.get(name)
    if not prop:
        return ""
    prop_type = prop.get("type", "")
    if prop_type == "rich_text":
        parts = prop.get("rich_text", [])
        return "".join(p.get("plain_text", "") for p in parts)
    return ""


def _get_title_prop(props: dict, name: str) -> str:
    prop = props.get(name)
    if not prop:
        return ""
    if prop.get("type") == "title":
        parts = prop.get("title", [])
        return "".join(p.get("plain_text", "") for p in parts)
    return ""


def _get_url_prop(props: dict, name: str) -> str:
    prop = props.get(name)
    if not prop:
        return ""
    if prop.get("type") == "url":
        return prop.get("url") or ""
    return ""


def _get_number_prop(props: dict, name: str) -> int:
    prop = props.get(name)
    if not prop:
        return 0
    if prop.get("type") == "number":
        return prop.get("number") or 0
    return 0


def _get_date_prop(props: dict, name: str) -> str:
    prop = props.get(name)
    if not prop:
        return ""
    if prop.get("type") == "date":
        date_obj = prop.get("date")
        if date_obj:
            return date_obj.get("start", "")
    return ""


def update_notion_page(
    client: httpx.Client,
    api_key: str,
    page_id: str,
    analysis: dict,
) -> None:
    """Update a Notion page with analysis results."""
    hook_type = analysis.get("hook_type", "")
    relevance = analysis.get("relevance", "")
    framework = analysis.get("framework", "")

    properties: dict = {}

    if hook_type in VALID_HOOK_TYPES:
        properties["Hook Type"] = {"select": {"name": hook_type}}

    if relevance in VALID_RELEVANCE:
        properties["Relevance"] = {"select": {"name": relevance}}

    if framework:
        properties["Tags"] = {"multi_select": [{"name": framework}]}

    if not properties:
        log.warning("No valid properties to update for page %s", page_id)
        return

    url = f"{NOTION_BASE}/pages/{page_id}"
    headers = notion_headers(api_key)
    payload = {"properties": properties}

    for attempt in range(3):
        resp = client.patch(url, headers=headers, json=payload, timeout=30)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 5))
            log.warning("Notion 429 on update, retrying in %ds...", retry_after)
            time.sleep(retry_after)
            continue
        resp.raise_for_status()
        break
    else:
        raise RuntimeError(f"Notion update failed after 3 retries for page {page_id}")
    log.info("Updated Notion page %s", page_id)


# ---------------------------------------------------------------------------
# Posts DB: create Ideas from high-relevance research
# ---------------------------------------------------------------------------


def create_idea_in_posts_db(
    client: httpx.Client,
    api_key: str,
    posts_db_id: str,
    post: dict,
    analysis: dict,
) -> str | None:
    """Create an Idea entry in the LinkedIn Posts DB for a high-relevance research post.

    Returns the created page URL, or None on failure.
    """
    author = post.get("author", "Unknown")
    repurpose = analysis.get("repurpose_angle", "")
    why = analysis.get("why_it_works", "")
    hook_type = analysis.get("hook_type", "")
    url = post.get("url", "")
    research_page_id = post.get("page_id", "")

    # Build title: "Research: {author} - {hook_type}"
    title = f"Research: {author}"
    if hook_type:
        title += f" - {hook_type}"
    title = title[:50]

    # Build notes with context
    notes_parts = []
    if repurpose:
        notes_parts.append(f"Repurpose angle: {repurpose}")
    if why:
        notes_parts.append(f"Waarom het werkt: {why}")
    if url:
        notes_parts.append(f"Bron: {url}")
    notes_text = "\n".join(notes_parts)

    now = datetime.now(timezone.utc)
    month_name = DUTCH_MONTHS[now.month]

    date_str = now.strftime("%Y-%m-%d")

    properties: dict = {
        "Title": {"title": [{"text": {"content": title}}]},
        "Status": {"status": {"name": "Idea"}},
        "Maand": {"select": {"name": month_name}},
        "Datum": {"date": {"start": date_str}},
    }

    if notes_text:
        properties["Notes"] = {
            "rich_text": [{"text": {"content": notes_text[:2000]}}],
        }

    # Set Research Source relation to the research post
    if research_page_id:
        properties["Research Source"] = {
            "relation": [{"id": research_page_id}],
        }

    payload = {
        "parent": {"database_id": posts_db_id},
        "properties": properties,
    }

    headers = notion_headers(api_key)

    for attempt in range(3):
        resp = client.post(
            f"{NOTION_BASE}/pages",
            headers=headers,
            json=payload,
            timeout=30,
        )
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 5))
            log.warning("Notion 429 on idea creation, retrying in %ds...", retry_after)
            time.sleep(retry_after)
            continue
        if resp.status_code == 200:
            page = resp.json()
            page_url = page.get("url", "")
            log.info("Created Idea in Posts DB: %s -> %s", title, page_url)
            return page_url
        else:
            log.error(
                "Failed to create Idea in Posts DB: %s - %s",
                resp.status_code,
                resp.text[:300],
            )
            return None

    log.error("Failed to create Idea after 3 retries (429)")
    return None


# ---------------------------------------------------------------------------
# Weekly digest
# ---------------------------------------------------------------------------


def generate_digest(posts_with_analysis: list[dict], dry_run: bool) -> str:
    """Generate a weekly digest markdown file."""
    today = datetime.now(timezone.utc)
    iso_cal = today.isocalendar()
    year = iso_cal[0]
    week = iso_cal[1]
    date_str = today.strftime("%Y-%m-%d")

    total = len(posts_with_analysis)
    high_posts = [
        p for p in posts_with_analysis if p["analysis"].get("relevance") == "hoog"
    ]
    high_count = len(high_posts)

    # Compute most common hook type
    hook_counts: dict[str, int] = {}
    total_likes = 0
    for p in posts_with_analysis:
        ht = p["analysis"].get("hook_type", "")
        if ht:
            hook_counts[ht] = hook_counts.get(ht, 0) + 1
        total_likes += p["post"].get("likes", 0)

    most_common = max(hook_counts, key=hook_counts.get) if hook_counts else "n/a"
    avg_likes = round(total_likes / total, 1) if total > 0 else 0

    lines = [
        "---",
        f"date: {date_str}",
        "tags: [linkedin, research, weekly-digest, content-machine]",
        "---",
        f"# Weekly Research Digest - Week {week}",
        "",
        f"{total} posts geanalyseerd, {high_count} hoog relevant.",
        "",
    ]

    if high_posts:
        lines.append("## Top Posts (hoog relevant)")
        lines.append("")
        for p in high_posts:
            post = p["post"]
            analysis = p["analysis"]
            lines.append(f"### {post['author']} - {analysis.get('hook_type', 'n/a')}")
            lines.append(f"**Waarom het werkt:** {analysis.get('why_it_works', '-')}")
            lines.append(f"**Repurpose angle:** {analysis.get('repurpose_angle', '-')}")
            lines.append(
                f"**Engagement:** {post.get('likes', 0)} likes, "
                f"{post.get('comments', 0)} comments"
            )
            if post.get("url"):
                lines.append(f"[Link]({post['url']})")
            lines.append("")

    lines.append("## Patronen deze week")
    lines.append(f"- Meest voorkomende hook type: {most_common}")
    lines.append(f"- Gemiddelde engagement: {avg_likes} likes")
    lines.append("")

    content = "\n".join(lines)
    filename = f"{year}-W{week:02d}.md"
    filepath = DIGEST_DIR / filename

    if dry_run:
        log.info("[DRY RUN] Would write digest to %s", filepath)
        log.info("--- Digest preview ---\n%s", content)
    else:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content, encoding="utf-8")
        log.info("Digest written to %s", filepath)

    return str(filepath)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Query Notion Research DB, prepare posts for routine analysis, write digest"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Query posts but don't update Notion or write digest",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Only process first N posts (0 = all)",
    )
    args = parser.parse_args()

    # Load env vars
    notion_key = _require_env("NOTION_API_KEY")
    db_id = _require_env("NOTION_RESEARCH_DB_ID")
    posts_db_id = os.environ.get("NOTION_POSTS_DB_ID", "")

    if not posts_db_id:
        log.info("NOTION_POSTS_DB_ID not set - high-relevance posts won't be added as Ideas")

    log.info("Starting content analyzer%s", " (dry run)" if args.dry_run else "")

    with httpx.Client() as client:
        log.info("Querying Notion database %s for unanalyzed posts...", db_id)
        try:
            pages = query_unanalyzed_posts(client, notion_key, db_id)
        except httpx.HTTPStatusError as exc:
            log.error("Notion API error: %s - %s", exc.response.status_code, exc.response.text)
            sys.exit(1)
        except httpx.RequestError as exc:
            log.error("Notion request failed: %s", exc)
            sys.exit(1)

        log.info("Found %d unanalyzed posts from the past 7 days", len(pages))

        if not pages:
            log.info("Nothing to analyze. Exiting.")
            return

        posts = [extract_post_data(page) for page in pages]

        if args.limit > 0:
            posts = posts[: args.limit]
            log.info("Limited to %d posts", len(posts))

        # Output posts as JSON for the Claude Routine to consume.
        # The routine is expected to analyze each post (hook_type, relevance,
        # framework, why_it_works, repurpose_angle) and call update_notion_page()
        # + create_idea_in_posts_db() + generate_digest() with the results.
        output = {
            "posts_to_analyze": posts,
            "count": len(posts),
            "hook_types": sorted(VALID_HOOK_TYPES),
            "relevance_levels": sorted(VALID_RELEVANCE),
        }

        print(json.dumps(output, indent=2, default=str))

    log.info("Content analyzer finished. Pass the output to your Claude Routine for analysis.")


if __name__ == "__main__":
    main()
