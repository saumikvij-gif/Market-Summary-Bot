"""
reddit_news.py
--------------
Pulls financial news headlines from public RSS feeds (financial news outlets +
Reddit finance subreddits), prints them to the console, then sends them to
Claude for a market-sentiment read (bullish / bearish / neutral) with themes
and key drivers. No Reddit API key or app required.

Usage:
    python reddit_news.py

Required environment variable (for the sentiment step):
    ANTHROPIC_API_KEY  - your Anthropic API key (loaded from .env)

Optional:
    NEWS_LIMIT  - max headlines to show per feed (default: 8)
    NO_SENTIMENT - set to "1" to skip the Claude analysis and just list headlines
"""

import os
import sys
import datetime
import requests
import feedparser
import anthropic
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
        })
        if len(entries) >= limit:
            break
    return entries


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


# ── Sentiment analysis (Claude) ─────────────────────────────────────────────────

SENTIMENT_SYSTEM_PROMPT = """\
You are a financial market sentiment analyst. You are given a batch of news
headlines and Reddit post titles from finance communities. Assess the overall
market mood they convey. Be aware that Reddit finance communities (especially
wallstreetbets) use sarcasm, slang, and irony — interpret tone accordingly.
Be concise, neutral, and concrete. Use markdown.
"""


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


def generate_sentiment(headlines: dict) -> str:
    """Send headlines to Claude and return a sentiment analysis."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return ("(Skipped sentiment analysis: ANTHROPIC_API_KEY is not set. "
                "Add it to your .env file.)")

    block = build_headline_block(headlines)
    if not block.strip():
        return "(No headlines were collected, so there is nothing to analyze.)"

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    user_message = (
        "Here are today's financial news headlines and Reddit post titles. "
        "Provide:\n"
        "1. An overall market sentiment: Bullish, Bearish, or Neutral, with a "
        "confidence (low/medium/high).\n"
        "2. 3–5 recurring themes or topics driving the mood.\n"
        "3. The specific headlines that most influenced your read.\n"
        "4. A one-sentence takeaway.\n\n"
        + block
    )

    message = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1024,
        system=SENTIMENT_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    return message.content[0].text


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"Financial News Roundup — {now}")
    print(f"Showing up to {LIMIT} headlines per source")

    headlines = {}
    collect_section("FINANCIAL NEWS OUTLETS", NEWS_FEEDS, headlines)
    collect_section("REDDIT", REDDIT_FEEDS, headlines)

    if os.environ.get("NO_SENTIMENT") == "1":
        print("\n(Sentiment analysis skipped via NO_SENTIMENT=1)\n")
        return

    print("\n\nAnalyzing sentiment with Claude…")
    analysis = generate_sentiment(headlines)

    divider = "═" * 70
    print(f"\n{divider}")
    print("  MARKET SENTIMENT ANALYSIS")
    print(divider)
    print(analysis)
    print()


if __name__ == "__main__":
    main()
