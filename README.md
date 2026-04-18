# Claude Routines Starter

A working example of a Claude Routine that automates LinkedIn content research.

Every Monday and Friday at 13:00, a Claude Code agent:
1. Scrapes the latest posts from a list of creators via Apify
2. Analyzes each post (hook type, framework, relevance) using Claude
3. Saves everything to a Notion database
4. Generates a weekly digest + post ideas based on top performers

This repo contains the exact scripts I run for my own LinkedIn content pipeline.

## What you need

- A Claude subscription (Pro: 5 routines/day, Max: 15 routines/day)
- Apify account (free tier works to start) — for LinkedIn post scraping
- Notion workspace — to store research + ideas

## Setup (15 min)

### 1. Clone the repo

```bash
git clone https://github.com/locomainnn/claude-routines-starter
cd claude-routines-starter
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Copy the env file

```bash
cp .env.example .env
```

Fill in:
- `APIFY_TOKEN` — from https://console.apify.com/account/integrations
- `NOTION_API_KEY` — from https://www.notion.so/my-integrations
- `NOTION_PARENT_PAGE_ID` — the Notion page where the databases should live

### 4. Create the Notion databases

```bash
python3 setup_notion_db.py
```

This creates the Research DB automatically. Add the returned ID to `.env` as `NOTION_RESEARCH_DB_ID`.

### 5. Configure your creator watchlist

Copy the example and add your own creators:

```bash
cp watchlist.example.md watchlist.md
```

Edit `watchlist.md` with the LinkedIn profiles you want to monitor.

### 6. Test the scraper

```bash
python3 content_scraper.py --dry-run
```

### 7. Schedule it as a Claude Routine

Open the Claude desktop app → Routines → new routine:

- **Trigger:** Monday & Friday, 13:00
- **Prompt:** "Run the content scraper and analyzer from ~/claude-routines-starter. Send me a Telegram digest when done."
- **Skills needed:** bash execution, file read/write

## What's in this repo

| File | What it does |
|------|--------------|
| `content_scraper.py` | Scrapes LinkedIn posts from creators + search queries via Apify |
| `content_analyzer.py` | Sends scraped posts to Claude for analysis, updates Notion, writes weekly digest |
| `setup_notion_db.py` | One-time: creates the Research DB in Notion with the right schema |
| `watchlist.example.md` | Example of a creator watchlist file |
| `.env.example` | Template for your API keys |
| `docs/setup-guide.md` | Detailed setup walkthrough with screenshots |
| `docs/how-it-works.md` | Architecture explanation |

## Output example

A weekly digest is written to your vault (or any directory you configure). Example:

```markdown
# Weekly Research Digest - 2026-W16

41 posts analyzed, 8 high relevance.

## Top Posts (score >= 70)

### [Creator Name] - AI reshaped SDR role 2026
**Score:** 88/100 | **Hook:** provocation | **Relevance:** high
**Engagement:** 174 likes, 126 comments
**Analysis:** Top content: AI changes the SDR role...
```

## Want help setting this up?

This is a starter kit. Getting the full setup working — tuned to your ICP, integrated with your CRM, with Telegram notifications and Notion automation — takes experimentation. If you want to skip the trial and error and have it built specifically for your business, book a call:

[Plan a call →](https://your-agenda-link-here)

## License

MIT — see [LICENSE](LICENSE) file. Use it, modify it, ship it. No warranty.
