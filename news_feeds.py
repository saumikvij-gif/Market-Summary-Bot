"""
news_feeds.py
-------------
Pulls financial news headlines from public RSS feeds (news outlets + the Federal
Reserve + Reddit finance subreddits) and prints them to the console. No Reddit API
key or app required. Also exposes gather_headlines()/build_headline_block()/
split_headlines() used by the main pipeline; sentiment scoring lives in
sentiment.py.

Usage:
    python news_feeds.py

Optional:
    NEWS_LIMIT  - max headlines to show per feed (default: 8)
"""

import os
import re
import html
import time
import calendar
import datetime
import requests
import feedparser
from dotenv import load_dotenv

from utils import force_utf8

# Load environment variables from a local .env file if present (no-op in CI)
load_dotenv()

force_utf8()

# ── Configuration ───────────────────────────────────────────────────────────

LIMIT = int(os.environ.get("NEWS_LIMIT", "8"))
REQUEST_TIMEOUT = 15  # seconds

# Drop headlines older than this many hours. The score is a SAME-DAY read, but
# feeds (especially Reddit "hot" and Investing.com) carry items days old; those
# stale headlines pollute today's tone. Entries with no parseable date are kept
# (better to include than to silently drop a whole undated feed). Default 24h —
# tight enough to keep the read genuinely same-day now that the broadened sector
# queries surface plenty of fresh news. (Note: on the morning after a weekend or
# holiday the prior session's news is already >24h old; override via
# NEWS_MAX_AGE_HOURS=48 if a post-gap run looks thin.)
MAX_AGE_HOURS = int(os.environ.get("NEWS_MAX_AGE_HOURS", "24"))

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
    # Investing.com / foreign-bourse template recaps — not US-market relevant and
    # they dominated the standout-headline picks ("Morocco stocks lower at close
    # of trade; Moroccan All Shares down 0.11%"). These templates are distinctive.
    "at close of trade",
    "all shares",
    "stocks higher at close",
    "stocks lower at close",
    "stocks close higher",
    "stocks close lower",
]


def is_boilerplate(title: str) -> bool:
    """True if the title looks like a recurring/pinned thread or feed-template noise."""
    low = title.lower()
    return any(pat in low for pat in SKIP_PATTERNS)


# ── US-market relevance gate ──────────────────────────────────────────────────
# The composite's news component is a SAME-DAY read of *US* market mood, but the
# general outlet feeds (Yahoo / MarketWatch / CNBC / Investing.com) also carry
# foreign-bourse recaps and off-topic items that drag the score around with noise
# unrelated to the US tape (the "Morocco stocks lower at close…" class). A
# headline is kept only if it carries a STRONG US-market signal: a US index, a
# macro/Fed/rates term, a tracked US mega-cap, or a US-relevant cross-asset move.
# Note we deliberately do NOT treat a bare "stocks"/"shares" as sufficient — that
# alone lets foreign "<country> stocks…" headlines through; a US item from these
# outlets almost always also names an index, the Fed, a macro print, or a company.
#
# Applied to the outlet feeds only. The Fed feed is exempt (we want all policy
# text for the communications component), and the finance subreddits are exempt
# (inherently US-retail-focused, social tone, and weighted 0 in the composite).
US_MARKET_TERMS = [
    # US indices / venues
    r"s&p", r"s & p", r"\bspx\b", r"nasdaq", r"\bdow\b", r"dow jones",
    r"russell\b", r"wall street", r"wall st\b", r"\bnyse\b",
    r"u\.s\.? stocks", r"\bus stocks", r"american stocks",
    # macro / policy / rates
    r"\bfed\b", r"federal reserve", r"\bfomc\b", r"powell", r"interest rate",
    r"rate cut", r"rate hike", r"rate decision", r"\binflation\b", r"\bcpi\b",
    r"\bpce\b", r"\bppi\b", r"jobs report", r"jobs data", r"payrolls?\b",
    r"unemployment", r"jobless", r"\bgdp\b", r"treasury", r"\byields?\b",
    r"recession", r"tariff", r"\bearnings\b", r"consumer confidence",
    r"retail sales", r"\beconom(y|ic)\b",
    # tracked US mega-caps
    r"\bnvidia\b", r"\bmicrosoft\b", r"\bapple\b", r"\bamazon\b", r"\bmeta\b",
    r"\bfacebook\b", r"\bgoogle\b", r"alphabet", r"\btesla\b", r"broadcom",
    r"\bamd\b", r"\bintel\b", r"\bmicron\b", r"\bnetflix\b", r"jpmorgan",
    # US-relevant cross-asset
    r"\bbitcoin\b", r"\bethereum\b", r"\bcrypto", r"\bcrude\b", r"oil prices",
    r"\bgold\b", r"\bdollar\b", r"\bipo\b", r"\bdividend",
]
_US_MARKET_RE = re.compile("|".join(US_MARKET_TERMS), re.IGNORECASE)


def is_us_market_relevant(title: str) -> bool:
    """True if the headline bears on the US market's same-day tone (US index,
    macro/Fed term, tracked mega-cap, or cross-asset move). Permissive by intent —
    better to keep a borderline US item than gut the sample; foreign-bourse recaps
    and off-topic items fail the gate."""
    return bool(_US_MARKET_RE.search(title or ""))


def _norm_title(title: str) -> str:
    """Normalized dedup key: lowercased, punctuation/whitespace collapsed. Lets the
    same wire story carried by two outlets collapse to one entry so it isn't
    double-counted in the score or shown twice in the briefing."""
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


# Public alias so other modules (e.g. sector_watch) can reuse the dedup key
# without reaching into a private name.
norm_title = _norm_title


def _wants_us_gate(name: str) -> bool:
    """The US-relevance gate applies to general outlet feeds only — not the Fed
    feed (all policy text wanted) and not the r/ finance subreddits (social, 0%)."""
    low = name.lower()
    return ("fed" not in low) and not low.startswith("r/")


def _is_stale(entry, now_ts: float = None) -> bool:
    """True if the entry has a timestamp older than MAX_AGE_HOURS.

    Undated entries return False (kept) — some feeds omit dates entirely, and
    dropping them would silently gut those sources.
    """
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    if not t:
        return False
    now_ts = time.time() if now_ts is None else now_ts
    # feedparser's *_parsed times are UTC struct_times → timegm, not mktime.
    age_hours = (now_ts - calendar.timegm(t)) / 3600.0
    return age_hours > MAX_AGE_HOURS


# Public alias so other modules (e.g. sector_watch) can reuse the staleness check
# without reaching into a private name.
is_stale = _is_stale

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

def fetch_feed(url: str, limit: int = LIMIT, require_us_relevance: bool = False) -> list:
    """Fetch and parse one RSS feed; return a list of entry dicts, or [].

    When require_us_relevance is set, headlines that don't carry a US-market
    signal (see is_us_market_relevant) are dropped BEFORE the per-feed cap, so we
    keep up to `limit` *US-relevant* headlines rather than filling the slots with
    foreign-bourse / off-topic items.
    """
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
        if not title or is_boilerplate(title) or _is_stale(e):
            continue
        if require_us_relevance and not is_us_market_relevant(title):
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
    per_outlet = {name: fetch_feed(url, limit=per_source, require_us_relevance=True)
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
    seen = set()   # cross-source dedup: first outlet to carry a story keeps it
    for name, url in feeds.items():
        entries = fetch_feed(url, limit=limit,
                             require_us_relevance=_wants_us_gate(name))
        titles = []
        for e in entries:
            t = e["title"]
            if not t:
                continue
            key = _norm_title(t)
            if key in seen:
                continue
            seen.add(key)
            titles.append(t)
        store[name] = titles
    return store


# ── Headline routing (by source) ────────────────────────────────────────────────

def split_headlines(headlines: dict) -> tuple:
    """Split a {source: [titles]} dict into (news, reddit, fed) title lists.

    Routing is by source name, which this module owns (see NEWS_FEEDS /
    REDDIT_FEEDS): an "r/…" source is Reddit, a source naming the Fed is Fed
    communications, everything else is general news. The sentiment composite
    weights each list differently, so the split has to happen before scoring.
    """
    news, reddit, fed = [], [], []
    for source, titles in (headlines or {}).items():
        key = source.lower()
        if key.startswith("r/"):
            reddit.extend(titles)
        elif "fed" in key:
            fed.extend(titles)
        else:
            news.extend(titles)
    return news, reddit, fed


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
