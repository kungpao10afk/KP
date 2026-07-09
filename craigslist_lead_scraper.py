#!/usr/bin/env python3
"""
Craigslist lead scraper for property maintenance / management ads.

Polls a configurable list of Craigslist search URLs (see config.example.json),
extracts new listings since the last run, tags each as "offered" (someone
advertising maintenance/management services -- a competitor or potential
subcontractor) or "wanted" (someone who needs the work done -- a potential
customer), and appends new leads to a CSV file.

Be a polite scraper: this script rate-limits itself, uses a single ordinary
browser User-Agent, does not rotate proxies or solve CAPTCHAs, and stops
instead of retrying aggressively when Craigslist blocks it. Craigslist's
Terms of Use restrict automated access -- run this at low frequency
(a few times a day at most) and use the leads for direct, individual
outreach, not bulk unsolicited messaging.
"""

import argparse
import csv
import json
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import requests
from bs4 import BeautifulSoup

POST_ID_RE = re.compile(r"/(\d{6,})\.html")
CSV_FIELDS = [
    "date_scraped",
    "post_date",
    "lead_type",
    "search_name",
    "title",
    "price",
    "location",
    "url",
    "post_id",
]


def load_config(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def load_seen_ids(config: dict) -> set:
    seen = set()

    seen_file = Path(config["seen_ids_file"])
    if seen_file.exists():
        seen.update(json.loads(seen_file.read_text()))

    csv_file = Path(config["output_csv"])
    if csv_file.exists():
        with open(csv_file, newline="") as f:
            for row in csv.DictReader(f):
                pid = row.get("post_id")
                if pid:
                    seen.add(pid)

    return seen


def save_seen_ids(config: dict, seen: set) -> None:
    seen_file = Path(config["seen_ids_file"])
    seen_file.parent.mkdir(parents=True, exist_ok=True)
    seen_file.write_text(json.dumps(sorted(seen)))


def extract_post_id(url: str) -> str:
    m = POST_ID_RE.search(url or "")
    return m.group(1) if m else ""


def paginate_url(url: str, offset: int) -> str:
    parsed = urlparse(url)
    q = parse_qs(parsed.query)
    q["s"] = [str(offset)]
    new_query = urlencode(q, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def fetch(session: requests.Session, url: str, timeout: int):
    resp = session.get(url, timeout=timeout)
    if resp.status_code == 403:
        raise PermissionError(
            f"Craigslist returned 403 for {url}. This usually means the IP "
            "is temporarily rate-limited or blocked. Stop and try again "
            "later with a longer delay -- do not retry in a loop."
        )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.text


def parse_jsonld_listings(html: str) -> dict:
    """Returns {post_id: {title, url, price}} parsed from the embedded JSON-LD block."""
    soup = BeautifulSoup(html, "lxml")
    tag = soup.find("script", id="ld_searchpage_results") or soup.find(
        "script", attrs={"type": "application/ld+json"}
    )
    if not tag or not tag.string:
        return {}

    try:
        data = json.loads(tag.string)
    except (json.JSONDecodeError, TypeError):
        return {}

    graph = data.get("@graph", data if isinstance(data, list) else [data])
    items = []
    for node in graph:
        if isinstance(node, dict) and "itemListElement" in node:
            items = node["itemListElement"]
            break

    listings = {}
    for entry in items:
        item = entry.get("item", entry) if isinstance(entry, dict) else None
        if not item:
            continue

        url = item.get("url") or item.get("@id") or ""
        post_id = extract_post_id(url)
        if not post_id:
            continue

        price = ""
        offers = item.get("offers")
        if isinstance(offers, dict):
            price = offers.get("price", "")
        elif isinstance(offers, list) and offers:
            price = offers[0].get("price", "")

        listings[post_id] = {
            "title": item.get("name", "").strip(),
            "url": url,
            "price": price,
        }

    return listings


def parse_html_listings(html: str) -> dict:
    """Fallback / supplement: returns {post_id: {title, url, price, location, post_date}}."""
    soup = BeautifulSoup(html, "lxml")
    listings = {}

    containers = soup.select("li.cl-search-result, li.result-row, div.cl-search-result")
    for c in containers:
        link = c.select_one("a.cl-app-anchor, a.result-title, a.titlestring, a[href*='/d/']")
        if not link or not link.get("href"):
            continue

        url = link["href"]
        post_id = c.get("data-pid") or extract_post_id(url)
        if not post_id:
            continue

        title_el = c.select_one(".title, .result-title, .titlestring") or link
        price_el = c.select_one(".price, .result-price")
        loc_el = c.select_one(".location, .result-hood, .nearby")
        time_el = c.select_one("time")

        post_date = ""
        if time_el:
            post_date = time_el.get("datetime", "") or time_el.get_text(strip=True)

        location = loc_el.get_text(strip=True).strip("()") if loc_el else ""

        listings[post_id] = {
            "title": title_el.get_text(strip=True),
            "url": url if url.startswith("http") else url,
            "price": price_el.get_text(strip=True) if price_el else "",
            "location": location,
            "post_date": post_date,
        }

    return listings


def merge_listings(jsonld: dict, html: dict) -> dict:
    merged = {}
    for post_id in set(jsonld) | set(html):
        j = jsonld.get(post_id, {})
        h = html.get(post_id, {})
        merged[post_id] = {
            "title": j.get("title") or h.get("title", ""),
            "url": j.get("url") or h.get("url", ""),
            "price": j.get("price") or h.get("price", ""),
            "location": h.get("location", ""),
            "post_date": h.get("post_date", ""),
        }
    return merged


def keyword_ok(title: str, include: list, exclude: list) -> bool:
    lower = title.lower()
    if include and not any(k.lower() in lower for k in include):
        return False
    if exclude and any(k.lower() in lower for k in exclude):
        return False
    return True


def scrape_watch(session: requests.Session, watch: dict, config: dict, seen: set) -> list:
    new_leads = []
    max_pages = config.get("max_pages_per_watch", 3)
    timeout = config.get("request_timeout_seconds", 20)

    for page in range(max_pages):
        url = paginate_url(watch["url"], page * 120)
        print(f"  [{watch['name']}] fetching page {page + 1}: {url}")

        html = fetch(session, url, timeout)
        if not html:
            break

        jsonld_listings = parse_jsonld_listings(html)
        html_listings = parse_html_listings(html)
        listings = merge_listings(jsonld_listings, html_listings)

        if not listings:
            break

        page_new_count = 0
        for post_id, data in listings.items():
            if post_id in seen:
                continue
            if not keyword_ok(
                data["title"],
                watch.get("keywords_include", []),
                watch.get("keywords_exclude", []),
            ):
                continue

            seen.add(post_id)
            page_new_count += 1
            new_leads.append(
                {
                    "date_scraped": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "post_date": data.get("post_date", ""),
                    "lead_type": watch["lead_type"],
                    "search_name": watch["name"],
                    "title": data["title"],
                    "price": data.get("price", ""),
                    "location": data.get("location", ""),
                    "url": data["url"],
                    "post_id": post_id,
                }
            )

        if page_new_count == 0 and page > 0:
            break

        delay = random.uniform(config["min_delay_seconds"], config["max_delay_seconds"])
        time.sleep(delay)

    return new_leads


def append_leads_to_csv(csv_path: Path, leads: list) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = csv_path.exists()
    with open(csv_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not file_exists:
            writer.writeheader()
        for lead in leads:
            writer.writerow(lead)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.json", help="Path to config JSON file")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        print(
            f"Config file '{config_path}' not found. Copy config.example.json to "
            f"{config_path} and edit it first.",
            file=sys.stderr,
        )
        sys.exit(1)

    config = load_config(config_path)
    seen = load_seen_ids(config)

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": config.get("user_agent", "Mozilla/5.0"),
            "Accept-Language": "en-US,en;q=0.9",
        }
    )

    all_new_leads = []
    print(f"Starting run at {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    for watch in config["watches"]:
        try:
            new_leads = scrape_watch(session, watch, config, seen)
        except PermissionError as e:
            print(f"  STOPPED: {e}", file=sys.stderr)
            break
        except requests.RequestException as e:
            print(f"  [{watch['name']}] request failed, skipping: {e}", file=sys.stderr)
            continue

        print(f"  [{watch['name']}] {len(new_leads)} new lead(s)")
        all_new_leads.extend(new_leads)

        delay = random.uniform(config["min_delay_seconds"], config["max_delay_seconds"])
        time.sleep(delay)

    if all_new_leads:
        append_leads_to_csv(Path(config["output_csv"]), all_new_leads)
    save_seen_ids(config, seen)

    print(f"\nDone. {len(all_new_leads)} new lead(s) written to {config['output_csv']}.")
    if all_new_leads:
        print("\nNew leads this run:")
        for lead in all_new_leads:
            tag = "WANTED" if lead["lead_type"] == "wanted" else "OFFERED"
            print(f"  [{tag}] {lead['title']} - {lead['url']}")


if __name__ == "__main__":
    main()
