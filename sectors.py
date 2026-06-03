"""
sectors.py
----------
AI-stack "Sector Watch": for each of 8 thesis baskets, compute the day's average
price move (from a basket of representative stocks) and a news-sentiment read
(Google News RSS per sector, scored with the sentiment engine), plus a
best-effort Reddit read by keyword-tagging the gathered subreddit titles.

This is a DISPLAY-ONLY breakdown — it does not feed the composite score (which
uses whole-market SPDR breadth). Everything is fail-safe: any fetch/scoring
failure degrades to None rather than blocking the briefing.
"""

import requests
import feedparser

import sentiment

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36"
}
TIMEOUT = 15
NEWS_PER_SECTOR = 8

# Each basket: representative tickers (for the price move), a Google News search
# query (for news sentiment), and keywords (to tag Reddit titles).
SECTORS = {
    "Hyperscalers & Neoclouds": {
        "tickers": ["GOOGL", "MSFT", "AMZN", "ORCL", "CRWV", "NBIS"],
        "query": '(hyperscaler OR "data center" OR cloud) (Microsoft OR Amazon OR Google OR CoreWeave OR Oracle)',
        "keywords": ["hyperscaler", "cloud", "azure", "aws", "coreweave", "data center", "gcp"],
    },
    "Memory (DRAM/NAND/HBM)": {
        "tickers": ["MU", "WDC", "SNDK", "STX"],
        "query": '(memory chip OR DRAM OR NAND OR HBM) (Micron OR SanDisk OR "Western Digital")',
        "keywords": ["dram", "nand", "hbm", "memory chip", "micron"],
    },
    "Semiconductors / Compute": {
        "tickers": ["NVDA", "AMD", "INTC", "ARM", "QCOM", "TSM"],
        "query": '(semiconductor OR GPU OR CPU) (Nvidia OR AMD OR Intel OR Arm OR TSMC)',
        "keywords": ["semiconductor", "gpu", "cpu", "chip", "nvidia", "amd", "intel"],
    },
    "Networking / Interconnect": {
        "tickers": ["AVGO", "MRVL", "ANET", "LITE", "COHR", "APH"],
        "query": '(networking OR optical interconnect OR "switch silicon") (Broadcom OR Marvell OR Arista OR Coherent)',
        "keywords": ["networking", "interconnect", "optical", "switch", "ethernet", "broadcom", "arista"],
    },
    "SaaS": {
        "tickers": ["CRM", "NOW", "SNOW", "DDOG", "ADBE", "PLTR"],
        "query": '(SaaS OR "enterprise software") (Salesforce OR ServiceNow OR Snowflake OR Datadog)',
        "keywords": ["saas", "software", "subscription"],
    },
    "Banking": {
        "tickers": ["JPM", "BAC", "WFC", "GS", "C"],
        "query": '(bank OR banking OR "loan default") (JPMorgan OR "Bank of America" OR "Goldman Sachs" OR "Wells Fargo")',
        "keywords": ["bank", "banking", "lender", "loan", "credit", "default"],
    },
    "Consumer": {
        "tickers": ["WMT", "COST", "NKE", "MCD", "HD"],
        "query": '(consumer spending OR retail) (Walmart OR Costco OR Nike OR "McDonald\'s" OR "Home Depot")',
        "keywords": ["consumer", "retail", "spending", "shopper"],
    },
    "Pharma / Healthcare": {
        "tickers": ["LLY", "JNJ", "MRK", "PFE", "UNH", "ABBV"],
        "query": '(pharma OR healthcare OR drug OR FDA) ("Eli Lilly" OR Pfizer OR Merck OR UnitedHealth)',
        "keywords": ["pharma", "drug", "fda", "healthcare", "biotech"],
    },
}


def _fetch_prices(tickers: list) -> dict:
    """Batched daily % change for many tickers in one request. {ticker: pct}."""
    import yfinance as yf
    data = yf.download(tickers, period="5d", progress=False, group_by="ticker")
    out = {}
    for t in tickers:
        try:
            closes = data[t]["Close"].dropna()
            if len(closes) >= 2 and closes.iloc[-2]:
                out[t] = round((closes.iloc[-1] / closes.iloc[-2] - 1) * 100, 2)
        except Exception:
            pass
    return out


def _news_sentiment(query: str) -> tuple:
    """(avg sentiment, n headlines) from a Google News RSS search, scored by the
    news engine (FinBERT in hybrid mode). Returns (None, 0) on failure/empty."""
    url = ("https://news.google.com/rss/search?q="
           + requests.utils.quote(query) + "&hl=en-US&gl=US&ceid=US:en")
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    titles = [e.get("title", "").strip()
              for e in feedparser.parse(resp.content).entries[:NEWS_PER_SECTOR]]
    titles = [t for t in titles if t]
    if not titles:
        return None, 0
    engine = sentiment.news_engine()
    avg = sum(sentiment.score_text(t, engine) for t in titles) / len(titles)
    return round(avg, 4), len(titles)


def _reddit_sentiment(keywords: list, reddit_titles: list) -> tuple:
    """Best-effort: VADER sentiment of Reddit titles matching the sector keywords."""
    matched = [t for t in (reddit_titles or [])
               if any(k in t.lower() for k in keywords)]
    if not matched:
        return None, 0
    avg = sum(sentiment.score_text(t, "vader") for t in matched) / len(matched)
    return round(avg, 4), len(matched)


def build_sector_watch(reddit_titles: list = None) -> list:
    """Return a list of per-sector dicts: move %, news sentiment, reddit sentiment."""
    all_tickers = sorted({t for cfg in SECTORS.values() for t in cfg["tickers"]})
    try:
        prices = _fetch_prices(all_tickers)
    except Exception as exc:
        print(f"  ⚠️  Could not fetch sector prices: {exc}")
        prices = {}

    rows = []
    for name, cfg in SECTORS.items():
        moves = [prices[t] for t in cfg["tickers"] if t in prices]
        avg_move = round(sum(moves) / len(moves), 2) if moves else None
        try:
            news_score, news_n = _news_sentiment(cfg["query"])
        except Exception as exc:
            print(f"  ⚠️  Sector news failed for {name}: {exc}")
            news_score, news_n = None, 0
        reddit_score, reddit_n = _reddit_sentiment(cfg["keywords"], reddit_titles)
        rows.append({
            "sector": name, "move_pct": avg_move,
            "news_score": news_score, "news_n": news_n,
            "reddit_score": reddit_score, "reddit_n": reddit_n,
            "constituents": len(moves),
        })
    return rows


def render_md(rows: list) -> str:
    """Markdown rendering of the sector watch (for the data block / Claude)."""
    if not rows:
        return ""
    lines = ["### Sector Watch (AI Stack)"]
    for r in rows:
        move = f"{r['move_pct']:+.2f}%" if r["move_pct"] is not None else "n/a"
        news = sentiment.label_for(r["news_score"]) if r["news_score"] is not None else "—"
        red = sentiment.label_for(r["reddit_score"]) if r["reddit_score"] is not None else "—"
        lines.append(f"- {r['sector']}: {move}  |  news: {news}  |  reddit: {red}")
    return "\n".join(lines)
