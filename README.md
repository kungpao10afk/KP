# Craigslist Property Maintenance/Management Lead Scraper (Chicago)

Polls Craigslist Chicago search results for property maintenance/management
related ads and appends new listings to a CSV, so you can build a running
list of leads without manually re-checking Craigslist.

Each lead is tagged:
- **`offered`** — someone is advertising maintenance/management services
  (competitors, or potential subcontractors to bring on).
- **`wanted`** — someone (a landlord, property owner, etc.) is posting that
  they need the work done — a potential customer.

## Setup

```bash
pip install -r requirements.txt
cp config.example.json config.json
```

Edit `config.json` — the `watches` list is what actually gets scraped. Each
watch is one Craigslist search URL plus a `lead_type` and optional keyword
filters.

**The default URLs are a starting point, not guaranteed-correct.** Craigslist
occasionally renames its category codes. The reliable way to get a URL: open
`https://chicago.craigslist.org` in your browser, use the search box and
category filters the way you actually want (e.g. "skilled trade services",
"small biz ads", "real estate services", "gigs"), then copy the resulting
URL from your address bar into a watch's `"url"` field. Keep `sort=date` in
the URL so pagination/dedup works as expected.

## Run

```bash
python3 craigslist_lead_scraper.py
```

New leads are appended to `data/leads.csv` (columns: `date_scraped`,
`post_date`, `lead_type`, `search_name`, `title`, `price`, `location`,
`url`, `post_id`). Already-seen posts are tracked in `data/seen_ids.json`
plus the CSV itself, so re-running only picks up genuinely new listings —
safe to run on a schedule.

### Run on a schedule

```cron
0 */4 * * * cd /path/to/KP && /usr/bin/python3 craigslist_lead_scraper.py >> run.log 2>&1
```

Every 4 hours is plenty for a single city/keyword set — see the rate-limiting
note below.

## Dashboard

```bash
python3 dashboard.py
```

Then open `http://127.0.0.1:5050`. It's a small local Flask app that reads
`data/leads.csv` on every request (so it always reflects your latest scraper
run — just hit "Refresh" in the browser after a run, no restart needed) and
shows:

- Stat tiles: total leads, new in the last 7 days, offered vs. wanted counts.
- A leads-per-day chart (last 14 days) and a leads-by-search breakdown, both
  split by lead type.
- A sortable, filterable table (type, search, date range, title text) with
  a direct link to each Craigslist post.

It's read-only and local-only (binds to `127.0.0.1`) — nothing here talks to
Craigslist, it just visualizes what the scraper already collected.

## How it works

Craigslist search pages embed a stable `<script id="ld_searchpage_results"
type="application/ld+json">` block with structured listing data (title, URL,
price) meant for search-engine indexing. The scraper reads that first since
it's far less likely to break than CSS class names, then supplements it with
a best-effort HTML pass (`li.cl-search-result` / `li.result-row`) to pick up
post date and neighborhood, matching the two by the numeric post ID in each
listing's URL. If Craigslist changes its markup, the JSON-LD path is the one
most likely to keep working.

## Rate limiting & ground rules

Craigslist's Terms of Use restrict automated access, and they do rate-limit
or block IPs that hit the site too hard. This script is deliberately modest
about it:

- Random 4-9s delay between requests (configurable via `min_delay_seconds` /
  `max_delay_seconds`), plus a delay between watches.
- A single ordinary browser `User-Agent`, no proxy rotation, no CAPTCHA
  bypass, no header spoofing beyond a normal browser string.
- Up to `max_pages_per_watch` pages per watch (default 3), and it stops
  early once a page returns nothing new.
- On a `403` it stops the whole run immediately with a clear message rather
  than retrying — that's Craigslist telling you to back off. If you see this
  repeatedly, space out your runs further (once every several hours, not
  every few minutes).

Keep it to a handful of watches and a few runs a day, and use the leads for
direct, individual outreach — not bulk/automated messaging, and not
republishing the listings elsewhere. That's both good practice and what
keeps this on the right side of Craigslist's rules.

## Files

- `craigslist_lead_scraper.py` — the scraper.
- `dashboard.py` + `templates/dashboard.html` — the local dashboard.
- `config.example.json` — template config; copy to `config.json` (gitignored)
  and edit.
- `data/leads.csv`, `data/seen_ids.json` — generated output/state
  (gitignored — this is your local lead data, not something to commit).
