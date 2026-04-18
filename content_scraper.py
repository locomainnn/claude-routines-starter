#!/usr/bin/env python3
"""
Content Scraper - LinkedIn Post Scraper for the Claude Routines Starter

Scrapes LinkedIn posts from creator profiles and search queries via Apify,
saves results to a Notion database with deduplication.

Usage:
    python3 content_scraper.py                  # Full run (profiles + search)
    python3 content_scraper.py --dry-run        # Print what would be saved
    python3 content_scraper.py --profiles-only  # Only scrape creator profiles
    python3 content_scraper.py --search-only    # Only scrape search queries

Environment variables (required):
    APIFY_TOKEN           - Apify API token
    NOTION_API_KEY        - Notion integration API key
    NOTION_RESEARCH_DB_ID - Notion database ID for LinkedIn Research

Environment variables (optional):
    WATCHLIST_PATH - Path to your creator watchlist markdown file
                     Default: ./watchlist.md
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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
# Configuration
# ---------------------------------------------------------------------------

# Watchlist path is configurable via env var, with a sane default.
# Supports ~ expansion for home-relative paths.
_default_watchlist = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchlist.md")
WATCHLIST_PATH = Path(os.environ.get("WATCHLIST_PATH", _default_watchlist)).expanduser()

APIFY_PROFILE_ENDPOINT = (
    "https://api.apify.com/v2/acts/harvestapi~linkedin-profile-posts/run-sync-get-dataset-items"
)
APIFY_SEARCH_ENDPOINT = (
    "https://api.apify.com/v2/acts/harvestapi~linkedin-post-search/run-sync-get-dataset-items"
)

PROFILE_CONFIG = {
    "maxPosts": 1,
    "maxComments": 0,
    "maxReactions": 0,
    "postedLimit": "week",
    "scrapeComments": False,
    "scrapeReactions": False,
}

SEARCH_CONFIG = {
    "maxPosts": 5,
    "maxReactions": 0,
    "postedLimit": "week",
    "scrapeComments": False,
    "scrapeReactions": False,
    "sortBy": "relevance",
}

# Apify can be slow - generous timeouts and retries
APIFY_TIMEOUT = 120  # seconds per request
APIFY_MAX_RETRIES = 3
APIFY_RETRY_DELAY = 10  # seconds between retries

NOTION_API_VERSION = "2022-06-28"
NOTION_TIMEOUT = 30
NOTION_MAX_RETRIES = 3
NOTION_RETRY_DELAY = 5

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("content-scraper")

# ---------------------------------------------------------------------------
# Watchlist parser
# ---------------------------------------------------------------------------


def parse_watchlist(path: Path) -> tuple[list[str], list[str]]:
    """Parse the creator watchlist markdown file.

    Supports both formats:
      - Bullet list: "- https://www.linkedin.com/in/slug — note"
      - Table rows: "| Name | linkedin.com/in/slug | ... |"

    Search queries can live under either "## Search Queries" or "## Zoekqueries".
    They can be listed as bullets ("- query") or inside a fenced code block.

    Returns:
        Tuple of (linkedin_urls, search_queries)
    """
    if not path.exists():
        log.error("Watchlist file not found: %s", path)
        log.error("Set WATCHLIST_PATH in your .env, or copy watchlist.example.md to watchlist.md")
        sys.exit(1)

    text = path.read_text(encoding="utf-8")

    urls: list[str] = []
    seen_urls: set[str] = set()

    # Match linkedin.com/in/slug anywhere (bullets, tables, inline text)
    url_pattern = re.compile(
        r"(?:https?://)?(?:www\.)?linkedin\.com/in/([A-Za-z0-9_\-%.]+)",
        re.IGNORECASE,
    )
    for match in url_pattern.finditer(text):
        slug = match.group(1).strip().rstrip("/")
        full_url = f"https://www.linkedin.com/in/{slug}"
        if full_url not in seen_urls:
            seen_urls.add(full_url)
            urls.append(full_url)

    # Extract search queries — look under "## Search Queries" or "## Zoekqueries"
    queries: list[str] = []
    in_queries = False
    in_code_block = False
    for line in text.splitlines():
        stripped = line.strip()
        lower = stripped.lower()

        # Section header detection
        if lower.startswith("## search queries") or lower.startswith("## zoekqueries"):
            in_queries = True
            continue
        if in_queries and stripped.startswith("## "):
            break  # Hit next section

        if not in_queries:
            continue

        # Toggle code block state
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue

        if in_code_block:
            if stripped:
                queries.append(stripped)
            continue

        # Bullet item: "- query"
        if stripped.startswith("- "):
            query = stripped[2:].strip()
            if (query.startswith('"') and query.endswith('"')) or (
                query.startswith("'") and query.endswith("'")
            ):
                query = query[1:-1]
            if query:
                queries.append(query)

    log.info("Parsed watchlist: %d creator URLs, %d search queries", len(urls), len(queries))
    return urls, queries


# ---------------------------------------------------------------------------
# Apify API
# ---------------------------------------------------------------------------


def apify_request(
    client: httpx.Client,
    endpoint: str,
    token: str,
    payload: dict,
    label: str,
) -> list[dict]:
    """Make an Apify run-sync request with retries.

    Returns list of result items, or empty list on failure.
    """
    for attempt in range(1, APIFY_MAX_RETRIES + 1):
        try:
            log.info("Apify request [%s] attempt %d/%d", label, attempt, APIFY_MAX_RETRIES)
            resp = client.post(
                endpoint,
                params={"token": token},
                json=payload,
                timeout=APIFY_TIMEOUT,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                if isinstance(data, list):
                    log.info("Apify [%s]: got %d items", label, len(data))
                    return data
                log.warning("Apify [%s]: unexpected response type: %s", label, type(data))
                return []
            elif resp.status_code == 402:
                log.error("Apify [%s]: insufficient credits (402). Skipping.", label)
                return []
            elif resp.status_code >= 500:
                log.warning("Apify [%s]: server error %d, retrying...", label, resp.status_code)
            else:
                log.error("Apify [%s]: HTTP %d - %s", label, resp.status_code, resp.text[:500])
                return []
        except httpx.TimeoutException:
            log.warning("Apify [%s]: timeout after %ds, retrying...", label, APIFY_TIMEOUT)
        except httpx.HTTPError as e:
            log.warning("Apify [%s]: HTTP error: %s, retrying...", label, e)

        if attempt < APIFY_MAX_RETRIES:
            time.sleep(APIFY_RETRY_DELAY)

    log.error("Apify [%s]: all %d attempts failed", label, APIFY_MAX_RETRIES)
    return []


def scrape_profiles(client: httpx.Client, token: str, urls: list[str]) -> list[dict]:
    """Scrape recent posts from creator profiles."""
    all_posts = []
    for url in urls:
        payload = {**PROFILE_CONFIG, "profileUrls": [url]}
        posts = apify_request(
            client, APIFY_PROFILE_ENDPOINT, token, payload, label=url.split("/")[-1]
        )
        for post in posts:
            post["_source"] = "creator-watchlist"
        all_posts.extend(posts)
    return all_posts


def scrape_search(client: httpx.Client, token: str, queries: list[str]) -> list[dict]:
    """Scrape posts from search queries."""
    all_posts = []
    for query in queries:
        payload = {**SEARCH_CONFIG, "searchQuery": query}
        posts = apify_request(
            client, APIFY_SEARCH_ENDPOINT, token, payload, label=f"search:{query[:40]}"
        )
        for post in posts:
            post["_source"] = "search-query"
        all_posts.extend(posts)
    return all_posts


# ---------------------------------------------------------------------------
# Post normalization
# ---------------------------------------------------------------------------


def normalize_post(raw: dict) -> Optional[dict]:
    """Normalize an Apify post result into our standard format.

    Returns None if the post is missing critical data.
    """
    # Apify returns different field names depending on the actor
    content = raw.get("text") or raw.get("content") or raw.get("commentary") or ""
    post_url = raw.get("linkedinUrl") or raw.get("postUrl") or raw.get("url") or raw.get("shareUrl") or ""
    author_url = raw.get("authorProfileUrl") or raw.get("authorUrl") or ""

    # Author name - handle both flat string and nested dict
    author_name = raw.get("authorName", "")
    if isinstance(raw.get("author"), dict):
        if not author_name:
            author_name = raw["author"].get("name", "")
        if not author_url:
            author_url = raw["author"].get("linkedinUrl", "") or raw["author"].get("profileUrl", "") or raw["author"].get("url", "")

    # Engagement - handle both flat and nested (engagement.likes) format
    engagement = raw.get("engagement", {})
    likes = raw.get("likesCount") or raw.get("totalReactionCount") or raw.get("numLikes") or engagement.get("likes") or 0
    comments_count = raw.get("commentsCount") or raw.get("numComments") or engagement.get("comments") or 0
    posted_at_raw = raw.get("postedAt") or raw.get("postedDate") or raw.get("publishedAt") or ""
    # Handle nested postedAt (e.g. {"date": "2026-04-08"})
    if isinstance(posted_at_raw, dict):
        posted_at = posted_at_raw.get("date", "") or posted_at_raw.get("datetime", "")
    else:
        posted_at = posted_at_raw

    if not content or not post_url:
        return None

    # Ensure author_url is a full URL
    if author_url and not author_url.startswith("http"):
        author_url = "https://www." + author_url.lstrip("/")

    # Clean content (remove excessive whitespace but keep structure)
    content = content.strip()

    # Build title: "AuthorName - first 60 chars..."
    title_content = content.replace("\n", " ")[:60]
    if len(content) > 60:
        title_content += "..."
    title = f"{author_name} - {title_content}" if author_name else title_content

    # Parse date to ISO format
    iso_date = None
    if posted_at:
        iso_date = _parse_date(posted_at)

    return {
        "title": title[:200],  # Notion title limit safety
        "content": content,
        "url": post_url,
        "author_url": author_url,
        "author_name": author_name,
        "likes": int(likes) if likes else 0,
        "comments": int(comments_count) if comments_count else 0,
        "source": raw.get("_source", "creator-watchlist"),
        "posted_at": iso_date,
    }


def _parse_date(date_str: str) -> Optional[str]:
    """Try to parse a date string into ISO format."""
    if not date_str:
        return None

    # Already ISO-like
    if re.match(r"\d{4}-\d{2}-\d{2}", date_str):
        # Ensure it's a valid ISO date with time
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return dt.isoformat()
        except (ValueError, TypeError):
            return date_str[:10]

    # Try common formats
    for fmt in [
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%B %d, %Y",
        "%b %d, %Y",
        "%d/%m/%Y",
        "%m/%d/%Y",
    ]:
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue

    # Relative time (e.g., "2h ago", "1d ago") - return None, Notion will skip
    log.debug("Could not parse date: %s", date_str)
    return None


# ---------------------------------------------------------------------------
# Notion API
# ---------------------------------------------------------------------------


def notion_headers(api_key: str) -> dict:
    """Build Notion API headers."""
    return {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    }


def get_existing_urls(client: httpx.Client, api_key: str, db_id: str) -> set[str]:
    """Fetch all existing post URLs from the Notion database for dedup.

    Uses pagination to get all entries.
    """
    headers = notion_headers(api_key)
    existing = set()
    has_more = True
    start_cursor = None

    while has_more:
        payload: dict = {
            "page_size": 100,
            "filter": {
                "property": "URL",
                "url": {"is_not_empty": True},
            },
        }
        if start_cursor:
            payload["start_cursor"] = start_cursor

        for attempt in range(1, NOTION_MAX_RETRIES + 1):
            try:
                resp = client.post(
                    f"https://api.notion.com/v1/databases/{db_id}/query",
                    headers=headers,
                    json=payload,
                    timeout=NOTION_TIMEOUT,
                )
                if resp.status_code in (200, 201):
                    data = resp.json()
                    for page in data.get("results", []):
                        url_prop = page.get("properties", {}).get("URL", {})
                        url_val = url_prop.get("url")
                        if url_val:
                            existing.add(url_val)
                    has_more = data.get("has_more", False)
                    start_cursor = data.get("next_cursor")
                    break
                elif resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "2"))
                    log.warning("Notion rate limit, waiting %ds...", retry_after)
                    time.sleep(retry_after)
                else:
                    log.error("Notion query failed: HTTP %d - %s", resp.status_code, resp.text[:500])
                    has_more = False
                    break
            except httpx.HTTPError as e:
                log.warning("Notion query error: %s (attempt %d)", e, attempt)
                if attempt < NOTION_MAX_RETRIES:
                    time.sleep(NOTION_RETRY_DELAY)
                else:
                    has_more = False

    log.info("Found %d existing URLs in Notion DB for dedup", len(existing))
    return existing


def create_notion_page(
    client: httpx.Client,
    api_key: str,
    db_id: str,
    post: dict,
) -> bool:
    """Create a single page in the Notion database. Returns True on success."""
    headers = notion_headers(api_key)

    # Build properties
    properties: dict = {
        "Name": {
            "title": [{"text": {"content": post["title"]}}],
        },
        "URL": {
            "url": post["url"] if post["url"] else None,
        },
        "Author URL": {
            "url": post["author_url"] if post["author_url"] else None,
        },
        "Likes": {
            "number": post["likes"],
        },
        "Comments": {
            "number": post["comments"],
        },
        "Source": {
            "select": {"name": post["source"]},
        },
    }

    # Content - Notion rich_text blocks have a 2000 char limit per block
    content_text = post["content"]
    content_blocks = []
    for i in range(0, len(content_text), 2000):
        content_blocks.append({"text": {"content": content_text[i : i + 2000]}})
    if content_blocks:
        properties["Content"] = {"rich_text": content_blocks}

    # Posted At (only if we have a valid date)
    if post.get("posted_at"):
        properties["Posted At"] = {
            "date": {"start": post["posted_at"]},
        }

    payload = {
        "parent": {"database_id": db_id},
        "properties": properties,
    }

    for attempt in range(1, NOTION_MAX_RETRIES + 1):
        try:
            resp = client.post(
                "https://api.notion.com/v1/pages",
                headers=headers,
                json=payload,
                timeout=NOTION_TIMEOUT,
            )
            if resp.status_code in (200, 201):
                return True
            elif resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", "2"))
                log.warning("Notion rate limit, waiting %ds...", retry_after)
                time.sleep(retry_after)
            elif resp.status_code == 400:
                log.error(
                    "Notion create failed (400) for '%s': %s",
                    post["title"][:50],
                    resp.text[:500],
                )
                return False
            else:
                log.error(
                    "Notion create failed (%d) for '%s': %s",
                    resp.status_code,
                    post["title"][:50],
                    resp.text[:300],
                )
                if attempt < NOTION_MAX_RETRIES:
                    time.sleep(NOTION_RETRY_DELAY)
        except httpx.HTTPError as e:
            log.warning("Notion create error: %s (attempt %d)", e, attempt)
            if attempt < NOTION_MAX_RETRIES:
                time.sleep(NOTION_RETRY_DELAY)

    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scrape LinkedIn posts from creator profiles and search queries, save to Notion."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be saved without calling Notion.",
    )
    parser.add_argument(
        "--profiles-only",
        action="store_true",
        help="Only scrape creator profiles, skip search queries.",
    )
    parser.add_argument(
        "--search-only",
        action="store_true",
        help="Only scrape search queries, skip creator profiles.",
    )
    args = parser.parse_args()

    if args.profiles_only and args.search_only:
        log.error("Cannot use --profiles-only and --search-only together.")
        return 1

    # Check env vars
    apify_token = os.environ.get("APIFY_TOKEN", "").strip()
    notion_key = os.environ.get("NOTION_API_KEY", "").strip()
    notion_db = os.environ.get("NOTION_RESEARCH_DB_ID", "").strip()

    if not apify_token:
        log.error("APIFY_TOKEN environment variable is not set.")
        return 1
    if not args.dry_run:
        if not notion_key:
            log.error("NOTION_API_KEY environment variable is not set.")
            return 1
        if not notion_db:
            log.error("NOTION_RESEARCH_DB_ID environment variable is not set.")
            return 1

    # Parse watchlist
    log.info("Using watchlist: %s", WATCHLIST_PATH)
    urls, queries = parse_watchlist(WATCHLIST_PATH)

    if not urls and not queries:
        log.error("No creator URLs or search queries found in watchlist.")
        return 1

    # Scrape
    all_posts: list[dict] = []

    with httpx.Client() as client:
        if not args.search_only and urls:
            log.info("--- Scraping %d creator profiles ---", len(urls))
            profile_posts = scrape_profiles(client, apify_token, urls)
            all_posts.extend(profile_posts)
            log.info("Profile scraping done: %d raw posts", len(profile_posts))

        if not args.profiles_only and queries:
            log.info("--- Scraping %d search queries ---", len(queries))
            search_posts = scrape_search(client, apify_token, queries)
            all_posts.extend(search_posts)
            log.info("Search scraping done: %d raw posts", len(search_posts))

    if not all_posts:
        log.warning("No posts scraped from Apify. Nothing to save.")
        # This is not necessarily an error - could be no new posts in 48h
        return 0

    # Normalize
    normalized = []
    for raw_post in all_posts:
        post = normalize_post(raw_post)
        if post:
            normalized.append(post)
        else:
            log.debug("Skipping post with missing content or URL")

    # Dedup within this batch (by URL)
    seen_urls: set[str] = set()
    unique_posts = []
    for post in normalized:
        if post["url"] not in seen_urls:
            seen_urls.add(post["url"])
            unique_posts.append(post)

    log.info(
        "Normalized: %d posts -> %d unique (removed %d batch duplicates)",
        len(normalized),
        len(unique_posts),
        len(normalized) - len(unique_posts),
    )

    if args.dry_run:
        log.info("=== DRY RUN - would save %d posts ===", len(unique_posts))
        for i, post in enumerate(unique_posts, 1):
            print(f"\n--- Post {i}/{len(unique_posts)} ---")
            print(f"  Title:     {post['title']}")
            print(f"  URL:       {post['url']}")
            print(f"  Author:    {post['author_name']}")
            print(f"  Likes:     {post['likes']}")
            print(f"  Comments:  {post['comments']}")
            print(f"  Source:    {post['source']}")
            print(f"  Posted At: {post['posted_at'] or 'unknown'}")
            print(f"  Content:   {post['content'][:120]}...")
        return 0

    # Dedup against Notion
    with httpx.Client() as client:
        existing_urls = get_existing_urls(client, notion_key, notion_db)
        new_posts = [p for p in unique_posts if p["url"] not in existing_urls]

        log.info(
            "After Notion dedup: %d new posts (%d already exist)",
            len(new_posts),
            len(unique_posts) - len(new_posts),
        )

        if not new_posts:
            log.info("No new posts to save. All posts already in Notion.")
            return 0

        # Save to Notion
        success_count = 0
        fail_count = 0
        for i, post in enumerate(new_posts, 1):
            log.info(
                "Saving post %d/%d: %s",
                i,
                len(new_posts),
                post["title"][:60],
            )
            if create_notion_page(client, notion_key, notion_db, post):
                success_count += 1
            else:
                fail_count += 1
            # Small delay to avoid rate limiting
            if i < len(new_posts):
                time.sleep(0.35)

    log.info(
        "Done. Saved %d/%d posts to Notion (%d failed).",
        success_count,
        len(new_posts),
        fail_count,
    )

    if fail_count > 0 and success_count == 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
