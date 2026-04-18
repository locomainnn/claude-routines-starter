# Setup Guide

Full walkthrough of setting up the Claude Routines Starter. Takes about 15-30 minutes if this is your first time.

## Prerequisites

Before you start, make sure you have:

- **Python 3.9+** installed (`python3 --version`)
- **git** installed
- A **Claude subscription** (Pro or Max)
- An **Apify account** (https://apify.com — free tier is enough to start)
- A **Notion workspace** with an integration you can create

## Step 1 — Clone and install

```bash
git clone https://github.com/locomainnn/claude-routines-starter
cd claude-routines-starter
pip install -r requirements.txt
```

## Step 2 — Apify API token

1. Go to https://console.apify.com/account/integrations
2. Copy your Personal API token
3. Save it for step 4

## Step 3 — Notion integration

1. Go to https://www.notion.so/my-integrations
2. Click "New integration"
3. Name it (e.g., "Claude Routines")
4. Select your workspace, give it Read/Write/Insert permissions
5. Copy the "Internal Integration Secret" — this is your `NOTION_API_KEY`

Now create a parent page in Notion where the research DB will live:

1. Create a new page in Notion (e.g., "LinkedIn Research")
2. Click the "..." menu → Connections → add your new integration
3. Copy the page ID from the URL: `notion.so/LinkedIn-Research-XXXXXXXX` — the `XXXXXXXX` part is the ID

## Step 4 — Configure `.env`

```bash
cp .env.example .env
```

Edit `.env`:

```
APIFY_TOKEN=apify_api_xxxxxxxxx
NOTION_API_KEY=secret_xxxxxxxxx
NOTION_PARENT_PAGE_ID=xxxxxxxxxxxx
```

Leave `NOTION_RESEARCH_DB_ID` empty for now — the next step fills it in.

## Step 5 — Create the Notion DB

```bash
python3 setup_notion_db.py
```

The script creates a database under your parent page with all the right properties (Name, Content, URL, Likes, Comments, Hook Type, Relevance, etc.) and auto-updates your `.env` with the new DB ID.

## Step 6 — Configure your watchlist

```bash
cp watchlist.example.md watchlist.md
```

Edit `watchlist.md` — add the LinkedIn profile URLs of creators you want to monitor. Start with 5-10. Add search queries at the bottom for trending topics in your niche.

## Step 7 — Test the scraper

Dry run first (no writes to Notion):

```bash
python3 content_scraper.py --dry-run
```

If that looks good, run it for real:

```bash
python3 content_scraper.py
```

Check your Notion Research DB — you should see the scraped posts appearing.

## Step 8 — Test the analyzer

```bash
python3 content_analyzer.py --dry-run --limit 3
```

This analyzes 3 posts without writing back. If it looks good:

```bash
python3 content_analyzer.py
```

## Step 9 — Schedule as a Claude Routine

Open the Claude desktop app:

1. Go to Routines → New Routine
2. Configure:
   - **Trigger:** `Every Monday and Friday at 13:00`
   - **Prompt:** `Run the content scraper at /path/to/claude-routines-starter/content_scraper.py, then run the analyzer. Send me a summary of the weekly digest via Telegram.`
   - **Skills:** bash execution, file read
3. Save and test with "Run now"

## Troubleshooting

- **Apify timeout** — Apify scrapers can be slow. The script retries 3 times with 10s delays. If it still fails, try `--profiles-only` or `--search-only` to split the work.
- **Notion "archived page" error** — make sure your integration is actually connected to the parent page (Connections menu).
- **Rate limits** — Notion rate-limits at ~3 req/sec. The script paces automatically.

## Need help?

If you get stuck, or want this set up for your specific business (with Telegram notifications, CRM integration, custom watchlists tuned to your ICP), book a call:

[Plan a call →](https://your-agenda-link-here)
