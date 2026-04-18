# How It Works

A quick look under the hood so you understand what the scripts are actually doing.

## The big picture

```
[Claude Routine, Mon/Fri 13:00]
        |
        v
[content_scraper.py] --(Apify API)--> [LinkedIn posts]
        |
        v
[Notion Research DB] <--(saves)-- scraper
        |
        v
[content_analyzer.py] --(Claude API)--> [hook type + relevance + score]
        |
        v
[Notion Research DB] <--(updates)-- analyzer
        |
        v
[Weekly digest .md] <--(writes)-- analyzer
        |
        v
[Telegram / your preferred delivery]
```

## Script 1: `content_scraper.py`

**Purpose:** Fetch LinkedIn posts from creators + search queries, save to Notion.

**What it does:**
1. Reads `watchlist.md` to find creators + search queries
2. For each creator: calls Apify's `harvestapi/linkedin-profile-posts` actor
3. For each search query: calls Apify's `harvestapi/linkedin-post-search` actor
4. Deduplicates by post URL (skips posts already in Notion)
5. Saves new posts to Notion with: author, content, URL, likes, comments, posted date

**Key config (top of file):**
- `PROFILE_CONFIG.maxPosts` — how many posts per creator (default 1)
- `SEARCH_CONFIG.maxPosts` — how many posts per search query (default 5)
- `APIFY_TIMEOUT` — seconds per request (default 120)
- `APIFY_MAX_RETRIES` — retries on failure (default 3)

**Why `maxPosts=1` for profiles?** Because the routine runs twice a week. 1 post × 2 runs = 2 per creator per week = plenty. More means higher Apify costs.

## Script 2: `content_analyzer.py`

**Purpose:** Read posts from Notion, send to Claude for analysis, update with results.

**What it does:**
1. Queries Notion Research DB for posts scraped in the last 7 days
2. For each post, sends to Claude with an analysis prompt asking for:
   - **Hook type** (provocation, result-first, personal, problem, curiosity, value)
   - **Framework used** (what structure the post follows)
   - **Why it works** (1-2 sentences)
   - **Relevance** (high/medium/low for your niche)
   - **Score** (0-100 based on engagement + relevance)
3. Updates Notion with the analysis results
4. Writes a weekly digest to `weekly-research/2026-WXX.md`
5. Optionally: generates post-idea markdown files in a Posts DB

**Key functions:**
- `_analyze_post()` — builds the Claude prompt
- `_update_notion_post()` — writes analysis back to Notion
- `_write_digest()` — generates the weekly markdown file

## Script 3: `setup_notion_db.py`

**Purpose:** One-time setup to create the Research DB with the right schema.

**What it creates:**
- A Notion database called "LinkedIn Research"
- Properties: Name, Content, URL, Author URL, Likes, Comments, Source, Posted At, Hook Type, Relevance, Tags

Run once before the first scraper run.

## The watchlist file

Plain markdown with one creator per bullet line. The scraper extracts LinkedIn profile URLs with a regex. That's it — no JSON, no YAML, keep it simple.

Adding a search query? Add a line with just the query text:

```markdown
### Search Queries
cold email
outbound automation
AI sales
```

The scraper picks these up automatically.

## Why this architecture?

**Why Apify for scraping?** LinkedIn's public API is limited, and scraping it yourself breaks ToS. Apify's `harvestapi` actors are a reliable workaround that respects rate limits and proxy rotation.

**Why Notion as the DB?** Two reasons. First, Notion is where most creators already organize content — putting research in the same place keeps workflow tight. Second, Notion has filters, views, and sharing built in, so you don't need a separate admin panel.

**Why separate scraper and analyzer?** The scraper is fast (API calls), the analyzer is slower (Claude calls, rate-limited). Splitting them means you can re-run analysis without rescraping, and if the analyzer fails at post #47, the first 46 are already saved.

**Why scheduled on Mon + Fri?** Weekend creators post on Sunday evening, weekday creators post Monday morning. Running Mon 13:00 catches that. Friday captures the week's trend. Adjust for your timezone + audience.

## Where to extend

Most common customizations:

- **Telegram notifications** — add a `_send_telegram()` call after `_write_digest()` in `content_analyzer.py`
- **Slack instead of Telegram** — same thing but with a Slack webhook
- **More analysis fields** — add to the Claude prompt in `_analyze_post()`, then add the corresponding Notion property
- **Auto-generate post drafts** — after analysis, send top 3 to Claude with your voice profile as context, write draft to a Posts DB

If you want any of these built for your specific use case, book a call.
