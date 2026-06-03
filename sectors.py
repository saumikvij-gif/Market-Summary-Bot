"""
sectors.py
----------
AI-stack "Sector Watch": a professional-style, multi-metric read of 8 thesis
baskets. For each basket it measures not just the average move, but *how* the
sector is behaving underneath — the way a PM reads a sector:

    Relative strength 30%  (sector move vs the S&P — leading or lagging?)
    Breadth           25%  (% of the basket above its 50-day MA — is the WHOLE
                            sector participating, or are a few names carrying it?)
    News sentiment    25%  (FinBERT on a dedicated Google News search)
    Volume            10%  (today's basket volume vs its 20-day average, signed
                            by direction — conviction behind the move)
    Reddit            10%  (best-effort: VADER on keyword-tagged subreddit titles)

Each metric is normalized to [-1, 1] and blended (renormalized over whatever is
available) into a per-sector score + label. This is DISPLAY-ONLY — it does not
feed the composite market score. Everything is fail-safe.
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

# How a sector's sub-metrics blend into its overall score. Reddit is only 5%
# (weak/sparse signal); the freed weight goes to relative strength (price/market).
SECTOR_METRIC_WEIGHTS = {
    "rel_strength": 0.35,
    "breadth":      0.25,
    "news":         0.25,
    "volume":       0.10,
    "reddit":       0.05,
}
RS_FULL_SCALE_PCT = 2.0   # 2% outperformance vs the S&P = a full ±1 RS signal

# Each basket: representative tickers, a Google News query, and Reddit keywords.
SECTORS = {
    "Hyperscalers & Neoclouds": {
        "tickers": ["GOOGL", "MSFT", "AMZN", "META", "ORCL", "CRWV", "NBIS"],
        "query": '(hyperscaler OR "data center" OR cloud) (Microsoft OR Amazon OR Google OR CoreWeave OR Oracle)',
        "keywords": ["hyperscaler", "cloud", "azure", "aws", "coreweave", "data center", "gcp"],
    },
    "Memory (DRAM/NAND/HBM)": {
        "tickers": ["MU", "WDC", "SNDK", "STX"],
        "query": '(memory chip OR DRAM OR NAND OR HBM) (Micron OR SanDisk OR "Western Digital")',
        "keywords": ["dram", "nand", "hbm", "memory chip", "micron"],
    },
    "Semiconductors / Compute": {
        "tickers": ["NVDA", "AMD", "ARM", "INTC", "QCOM", "TSM", "AVGO", "MRVL", "SMCI", "ANET"],
        "query": '(semiconductor OR GPU OR CPU OR "AI chip") (Nvidia OR AMD OR Intel OR Arm OR TSMC)',
        "keywords": ["semiconductor", "gpu", "cpu", "chip", "nvidia", "amd", "intel"],
    },
    "Networking / Interconnect": {
        "tickers": ["AVGO", "MRVL", "ANET", "LITE", "COHR", "APH", "CIEN"],
        "query": '(networking OR "optical interconnect" OR "switch silicon") (Broadcom OR Marvell OR Arista OR Coherent)',
        "keywords": ["networking", "interconnect", "optical", "switch", "ethernet", "broadcom", "arista"],
    },
    "SaaS": {
        "tickers": ["CRM", "NOW", "SNOW", "DDOG", "MDB", "NET", "HUBS", "PLTR", "ADBE"],
        "query": '(SaaS OR "enterprise software") (Salesforce OR ServiceNow OR Snowflake OR Datadog OR Cloudflare)',
        "keywords": ["saas", "software", "subscription"],
    },
    "Banking": {
        "tickers": ["JPM", "BAC", "WFC", "GS", "MS", "C", "USB", "PNC"],
        "query": '(bank OR banking OR "loan default") (JPMorgan OR "Bank of America" OR "Goldman Sachs" OR "Wells Fargo")',
        "keywords": ["bank", "banking", "lender", "loan", "credit", "default"],
    },
    "Consumer": {
        "tickers": ["WMT", "COST", "NKE", "MCD", "HD", "TGT", "LOW", "SBUX"],
        "query": '(consumer spending OR retail) (Walmart OR Costco OR Nike OR "McDonald\'s" OR "Home Depot")',
        "keywords": ["consumer", "retail", "spending", "shopper"],
    },
    "Pharma / Healthcare": {
        "tickers": ["LLY", "JNJ", "MRK", "PFE", "UNH", "ABBV", "AMGN", "BMY"],
        "query": '(pharma OR healthcare OR drug OR FDA) ("Eli Lilly" OR Pfizer OR Merck OR UnitedHealth)',
        "keywords": ["pharma", "drug", "fda", "healthcare", "biotech"],
    },
}


def _clamp(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


# Moving-average windows blended into each stock's trend strength. Using 20/50/
# 200 (not just 50) spreads breadth out so it doesn't saturate at 100% in a
# broad uptrend — a stock can be above its 200-DMA but below its 20-DMA.
BREADTH_MAS = (20, 50, 200)


def _fetch_history(tickers: list):
    """Batched ~1 year of Close + Volume for all tickers (one request).

    A year is enough for the 200-day MA used in the breadth blend.
    """
    import yfinance as yf
    return yf.download(tickers, period="1y", progress=False, group_by="ticker")


def _per_ticker_metrics(data, ticker: str):
    """Return (move_pct, trend_strength, vol_ratio) for one ticker, or None.

    trend_strength = fraction of the 20/50/200-day MAs the price is above, in
    [0, 1] (only MAs with enough history are counted).
    """
    try:
        df = data[ticker]
        closes = df["Close"].dropna()
        vols = df["Volume"].dropna()
        if len(closes) < 2:
            return None
        last = closes.iloc[-1]
        move = (last / closes.iloc[-2] - 1) * 100
        flags = [1 if last > closes.tail(w).mean() else 0
                 for w in BREADTH_MAS if len(closes) >= w]
        strength = (sum(flags) / len(flags)) if flags else None
        vol_ratio = (vols.iloc[-1] / vols.tail(20).mean()) if len(vols) >= 5 and vols.tail(20).mean() else None
        return round(float(move), 2), strength, (round(float(vol_ratio), 2) if vol_ratio else None)
    except Exception:
        return None


def _news_sentiment(query: str) -> tuple:
    """(avg sentiment, n) from a Google News RSS search, FinBERT-scored."""
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
    return round(sum(sentiment.score_text(t, engine) for t in titles) / len(titles), 4), len(titles)


def _reddit_sentiment(keywords: list, reddit_titles: list) -> tuple:
    matched = [t for t in (reddit_titles or []) if any(k in t.lower() for k in keywords)]
    if not matched:
        return None, 0
    return round(sum(sentiment.score_text(t, "vader") for t in matched) / len(matched), 4), len(matched)


def build_sector_watch(reddit_titles: list = None, sp_move: float = None) -> list:
    """Per-sector multi-metric read. sp_move is the S&P 500 daily % (for RS)."""
    all_tickers = sorted({t for cfg in SECTORS.values() for t in cfg["tickers"]})
    try:
        data = _fetch_history(all_tickers)
        metrics = {t: _per_ticker_metrics(data, t) for t in all_tickers}
    except Exception as exc:
        print(f"  ⚠️  Could not fetch sector history: {exc}")
        metrics = {}

    rows = []
    for name, cfg in SECTORS.items():
        present = [metrics[t] for t in cfg["tickers"] if metrics.get(t)]
        moves = [m[0] for m in present]
        avg_move = round(sum(moves) / len(moves), 2) if moves else None

        # Breadth: average per-stock trend strength (% of 20/50/200 MAs the price
        # is above), across the basket, mapped to [-1, 1].
        strengths = [m[1] for m in present if m[1] is not None]
        breadth_frac = (sum(strengths) / len(strengths)) if strengths else None
        breadth_score = (2 * breadth_frac - 1) if breadth_frac is not None else None

        # Relative strength: sector move vs the S&P.
        rs_score = None
        if avg_move is not None and sp_move is not None:
            rs_score = _clamp((avg_move - sp_move) / RS_FULL_SCALE_PCT)

        # Volume: above-average volume confirms the day's direction.
        vol_ratios = [m[2] for m in present if m[2] is not None]
        vol_score = None
        if vol_ratios and avg_move is not None:
            avg_ratio = sum(vol_ratios) / len(vol_ratios)
            direction = 1 if avg_move > 0 else -1 if avg_move < 0 else 0
            vol_score = _clamp((avg_ratio - 1.0)) * direction

        try:
            news_score, news_n = _news_sentiment(cfg["query"])
        except Exception as exc:
            print(f"  ⚠️  Sector news failed for {name}: {exc}")
            news_score, news_n = None, 0
        reddit_score, reddit_n = _reddit_sentiment(cfg["keywords"], reddit_titles)

        # Blend available sub-metrics, renormalized over their weights.
        parts = {"rel_strength": rs_score, "breadth": breadth_score,
                 "news": news_score, "volume": vol_score, "reddit": reddit_score}
        avail = {k: v for k, v in parts.items() if v is not None}
        total_w = sum(SECTOR_METRIC_WEIGHTS[k] for k in avail) or 1.0
        score = round(_clamp(sum(SECTOR_METRIC_WEIGHTS[k] * v for k, v in avail.items()) / total_w), 4)

        rows.append({
            "sector": name, "move_pct": avg_move,
            "rel_strength": round(avg_move - sp_move, 2) if (avg_move is not None and sp_move is not None) else None,
            "breadth_pct": round(breadth_frac * 100) if breadth_frac is not None else None,
            "news_score": news_score, "news_n": news_n,
            "reddit_score": reddit_score, "reddit_n": reddit_n,
            "score": score, "label": sentiment.label_for(score),
            "constituents": len(present),
        })
    return rows


def render_md(rows: list) -> str:
    """Markdown rendering of the sector watch (for the data block / Claude)."""
    if not rows:
        return ""
    lines = ["### Sector Watch (AI Stack)"]
    for r in rows:
        move = f"{r['move_pct']:+.2f}%" if r["move_pct"] is not None else "n/a"
        rs = f"{r['rel_strength']:+.2f}%" if r["rel_strength"] is not None else "n/a"
        breadth = f"{r['breadth_pct']}% trend" if r["breadth_pct"] is not None else "n/a"
        lines.append(f"- {r['sector']}: {move} (vs S&P {rs}) | breadth {breadth} "
                     f"| **{r['label']}** ({r['score']:+.2f})")
    return "\n".join(lines)
