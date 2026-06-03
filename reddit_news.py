"""
reddit_news.py
--------------
Pulls financial news headlines from public RSS feeds (financial news outlets +
Reddit finance subreddits) and prints them to the console. No Reddit API key or
app required. Also exposes gather_headlines()/build_headline_block() used by the
main pipeline; sentiment scoring lives in sentiment.py.

Usage:
    python reddit_news.py

Optional:
    NEWS_LIMIT  - max headlines to show per feed (default: 8)
"""

import os
import re
import sys
import html
import datetime
import requests
import feedparser
from dotenv import load_dotenv

# Load environment variables from a local .env file if present (no-op in CI)
load_dotenv()

# Reconfigure stdout to UTF-8 so symbols don't crash on Windows (cp1252)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ── Configuration ───────────────────────────────────────────────────────────

LIMIT = int(os.environ.get("NEWS_LIMIT", "8"))
REQUEST_TIMEOUT = 15  # seconds

# Recurring boilerplate / pinned threads that aren't real news (case-insensitive).
SKIP_PATTERNS = [
    "daily discussion",
    "daily general discussion",
    "advice thread",
    "rate my portfolio",
    "what are your moves",
    "weekly earnings thread",
    "moves tomorrow",
    "scam reminder",
    "megathread",
    "weekend discussion",
]


def is_boilerplate(title: str) -> bool:
    """True if the title looks like a recurring/pinned thread, not news."""
    low = title.lower()
    return any(pat in low for pat in SKIP_PATTERNS)

# A browser-like User-Agent keeps Reddit's RSS from returning 403.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36"
}

# Financial news outlet RSS feeds (no auth required)
NEWS_FEEDS = {
    "Fed (Monetary Policy)": "https://www.federalreserve.gov/feeds/press_monetary.xml",
    "Yahoo Finance":         "https://finance.yahoo.com/news/rssindex",
    "MarketWatch (Top)":     "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "CNBC (Finance)":        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664",
    "Investing.com":         "https://www.investing.com/rss/news_25.rss",
}

# Reddit subreddits exposed as public .rss feeds
REDDIT_FEEDS = {
    "r/wallstreetbets": "https://www.reddit.com/r/wallstreetbets/hot.rss",
    "r/stocks":         "https://www.reddit.com/r/stocks/hot.rss",
    "r/investing":      "https://www.reddit.com/r/investing/hot.rss",
    "r/StockMarket":    "https://www.reddit.com/r/StockMarket/hot.rss",
}


# ── Fetching ─────────────────────────────────────────────────────────────────

def fetch_feed(url: str, limit: int = LIMIT) -> list:
    """Fetch and parse one RSS feed; return a list of entry dicts, or []."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"  ⚠️  Could not fetch feed: {exc}")
        return []

    parsed = feedparser.parse(resp.content)
    entries = []
    for e in parsed.entries:
        title = (e.get("title") or "").strip()
        if not title or is_boilerplate(title):
            continue
        entries.append({
            "title": title,
            "link":  (e.get("link") or "").strip(),
            "summary": _clean_summary(e.get("summary") or e.get("description") or ""),
        })
        if len(entries) >= limit:
            break
    return entries


def _clean_summary(raw: str, max_len: int = 280) -> str:
    """Strip HTML tags/entities from an RSS summary and truncate."""
    text = re.sub(r"<[^>]+>", " ", raw)          # drop tags
    text = html.unescape(text)                    # decode &amp; etc.
    text = re.sub(r"\s+", " ", text).strip()      # collapse whitespace
    if len(text) > max_len:
        text = text[:max_len].rsplit(" ", 1)[0] + "…"
    return text


def get_top_news(count: int = 5, per_source: int = 2) -> list:
    """Top news articles (title + summary) across the news outlets, round-robin.

    Returns a list of {source, title, summary, link}. Reddit/Fed feeds are
    excluded — outlet feeds carry real article summaries.
    """
    outlets = {k: v for k, v in NEWS_FEEDS.items() if "fed" not in k.lower()}
    per_outlet = {name: fetch_feed(url, limit=per_source)
                  for name, url in outlets.items()}
    news, i = [], 0
    while len(news) < count and any(i < len(v) for v in per_outlet.values()):
        for name, entries in per_outlet.items():
            if i < len(entries) and len(news) < count:
                e = entries[i]
                news.append({"source": name, "title": e["title"],
                             "summary": e.get("summary", ""), "link": e["link"]})
        i += 1
    return news


# ── Output ─────────────────────────────────────────────────────────────────────

def print_feed(name: str, entries: list) -> None:
    divider = "─" * 70
    print(f"\n{divider}")
    print(f"  {name}  ({len(entries)} headlines)")
    print(divider)

    if not entries:
        print("  (no headlines)")
        return

    for i, e in enumerate(entries, 1):
        print(f"{i:>2}. {e['title']}")
        if e["link"]:
            print(f"    {e['link']}")


def collect_section(header: str, feeds: dict, store: dict) -> None:
    """Print a section's feeds and stash their headlines into `store`."""
    print(f"\n\n========== {header} ==========")
    for name, url in feeds.items():
        entries = fetch_feed(url)
        print_feed(name, entries)
        store[name] = [e["title"] for e in entries if e["title"]]


def gather_headlines(feeds: dict = None, limit: int = LIMIT) -> dict:
    """Fetch headlines into a {source: [titles]} dict without printing.

    Useful for importing into other scripts (e.g. the market summary). By
    default it pulls from both the news outlets and the Reddit feeds. `limit`
    caps the number of headlines kept per source.
    """
    if feeds is None:
        feeds = {**NEWS_FEEDS, **REDDIT_FEEDS}
    store = {}
    for name, url in feeds.items():
        entries = fetch_feed(url, limit=limit)
        store[name] = [e["title"] for e in entries if e["title"]]
    return store


# ── Headline block (reused by the main pipeline) ────────────────────────────────

def build_headline_block(headlines: dict) -> str:
    """Flatten the collected headlines into a single text block for the model."""
    parts = []
    for source, titles in headlines.items():
        if not titles:
            continue
        parts.append(f"### {source}")
        parts.extend(f"- {t}" for t in titles)
        parts.append("")
    return "\n".join(parts)


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"Financial News Roundup — {now}")
    print(f"Showing up to {LIMIT} headlines per source")

    headlines = {}
    collect_section("FINANCIAL NEWS OUTLETS", NEWS_FEEDS, headlines)
    collect_section("REDDIT", REDDIT_FEEDS, headlines)
    print()


if __name__ == "__main__":
    main()
