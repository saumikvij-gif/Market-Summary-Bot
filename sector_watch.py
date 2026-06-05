"""
sector_watch.py
---------------
AI-stack "Sector Watch": a professional-style, multi-metric read of 8 thesis
baskets. For each basket it measures not just the average move, but *how* the
sector is behaving underneath — the way a PM reads a sector:

    Relative strength 35%  (sector move vs its benchmark — leading or lagging?
                            tech baskets vs the Nasdaq 100, the rest vs the S&P)
    Breadth           25%  (% of the basket above its 20/50/200-day MAs — is the
                            WHOLE sector participating, or a few names carrying it?)
    News sentiment    25%  (FinBERT on a dedicated Google News search)
    Volume            10%  (today's basket volume vs its 20-day average, signed
                            by direction — conviction behind the move)
    Reddit             5%  (best-effort: VADER on keyword-tagged subreddit titles)

Each metric is normalized to [-1, 1] and blended (renormalized over whatever is
available) into a per-sector score + label. This is DISPLAY-ONLY — it does not
feed the composite market score. Everything is fail-safe.

The basket's headline move is the MEDIAN of its constituents, not the mean: the
baskets are small (4–10 names) and equal-weighted, so a single outlier (e.g. one
name up 30% on an upgrade) would otherwise drag the whole sector's reported move
and its relative-strength/volume-direction reads. The median is the typical
stock's move; breadth still separately captures how broadly the basket moved.
"""

import requests
import feedparser

import sentiment
from utils import clamp as _clamp

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
# 3.5% outperformance vs the S&P = a full ±1 RS signal. Wider than the index
# full-scales because these AI/semis baskets are high-beta and routinely move
# several % vs the S&P on a busy day — a tighter scale pinned RS (the largest,
# 35%, sub-metric) to ±1 constantly, drowning out the other metrics.
RS_FULL_SCALE_PCT = 3.5

# Each basket: representative tickers, a Google News query, and Reddit keywords.
# NB: distinct from market_summary.SECTORS (the 11 SPDR ETFs used for breadth) —
# these are the AI-stack thesis baskets this module reports on.
SECTOR_BASKETS = {
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


# Relative strength is measured against the benchmark that best fits each basket:
# the tech / AI-stack baskets track the Nasdaq 100 (their natural high-beta home),
# while the broader-economy baskets track the S&P 500.
NASDAQ_BASKETS = {
    "Hyperscalers & Neoclouds",
    "Memory (DRAM/NAND/HBM)",
    "Semiconductors / Compute",
    "Networking / Interconnect",
    "SaaS",
}


def _finite(x) -> bool:
    """True only for a real, non-NaN number (x == x is False for NaN)."""
    return isinstance(x, (int, float)) and x == x


def _rel_strength(basket_move, bench_move):
    """(rs_score in [-1,1], display delta) for a basket vs its benchmark, or
    (None, None) if either move is missing or NaN.

    Guarding on NaN — not just None — matters: a NaN benchmark move (from a
    poisoned index feed) used to pass the `is not None` check, render as
    "+nan%", and clamp to +1.0 in the blend (falsely turning every basket
    bullish). Treated as missing, relative strength simply drops out instead.
    """
    if not (_finite(basket_move) and _finite(bench_move)):
        return None, None
    delta = basket_move - bench_move
    return _clamp(delta / RS_FULL_SCALE_PCT), round(delta, 2)


def _median(values: list):
    """Median of a non-empty numeric list (mean of the two middle values if even)."""
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


# Moving-average windows blended into each stock's trend strength. Using 20/50/
# 200 (not just 50) spreads breadth out so it doesn't saturate at 100% in a
# broad uptrend — a stock can be above its 200-DMA but below its 20-DMA.
BREADTH_MAS = (20, 50, 200)


def _fetch_history(tickers: list):
    """Batched ~1 year of (raw) Close + Volume for all tickers (one request).

    auto_adjust=False keeps the RAW close — the price Yahoo's site shows and the
    basis of its change% — so a basket's move matches Yahoo (and the benchmark)
    rather than a dividend/split-adjusted series. A year is enough for the
    200-day MA used in the breadth blend.
    """
    import yfinance as yf
    return yf.download(tickers, period="1y", progress=False,
                       group_by="ticker", auto_adjust=False)


def _per_ticker_metrics(data, ticker: str):
    """Return (move_pct, trend_strength, vol_ratio, last_date) for one ticker, or
    None. last_date is the ISO date of the latest non-NaN close, used to detect
    tickers whose history lags a session so their move can be refreshed.

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
        last_date = closes.index[-1].date().isoformat()
        return (round(float(move), 2), strength,
                (round(float(vol_ratio), 2) if vol_ratio else None), last_date)
    except Exception:
        return None


def _yahoo_move(ticker_symbol: str):
    """Today's % change straight from Yahoo's own quote field
    (regularMarketChangePercent), or None. Used to refresh a ticker whose daily-
    history bar lags a session, so a basket's move is one consistent, current
    session — matching both the benchmark and Yahoo's site."""
    try:
        import yfinance as yf
        pct = yf.Ticker(ticker_symbol).info.get("regularMarketChangePercent")
        return round(float(pct), 2) if pct is not None else None
    except Exception:
        return None


def _news_sentiment(query: str) -> tuple:
    """(avg sentiment, n) from a Google News RSS search, FinBERT-scored.

    Reuses the main pipeline's staleness and boilerplate filters (news_feeds) so
    the same foreign-bourse template junk / days-old items that are kept out of
    the composite can't leak into a sector's news read either. Filtering happens
    BEFORE the per-sector cap so we keep up to NEWS_PER_SECTOR *clean* headlines.
    """
    import news_feeds
    url = ("https://news.google.com/rss/search?q="
           + requests.utils.quote(query) + "&hl=en-US&gl=US&ceid=US:en")
    resp = requests.get(url, headers=news_feeds.HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    titles = []
    for e in feedparser.parse(resp.content).entries:
        t = (e.get("title") or "").strip()
        if not t or news_feeds.is_boilerplate(t) or news_feeds.is_stale(e):
            continue
        titles.append(t)
        if len(titles) >= NEWS_PER_SECTOR:
            break
    if not titles:
        return None, 0
    engine = sentiment.news_engine()
    return round(sum(sentiment.score_text(t, engine) for t in titles) / len(titles), 4), len(titles)


def _reddit_sentiment(keywords: list, reddit_titles: list) -> tuple:
    matched = [t for t in (reddit_titles or []) if any(k in t.lower() for k in keywords)]
    if not matched:
        return None, 0
    return round(sum(sentiment.score_text(t, "vader") for t in matched) / len(matched), 4), len(matched)


def build_sector_watch(reddit_titles: list = None, sp_move: float = None,
                        nasdaq_move: float = None) -> list:
    """Per-sector multi-metric read.

    Relative strength is each basket's move vs its benchmark: the tech baskets
    (NASDAQ_BASKETS) vs the Nasdaq 100 daily % (`nasdaq_move`), the rest vs the
    S&P 500 daily % (`sp_move`). If a basket's benchmark move is unavailable, RS
    is simply omitted and the remaining sub-metrics renormalize.
    """
    all_tickers = sorted({t for cfg in SECTOR_BASKETS.values() for t in cfg["tickers"]})
    try:
        data = _fetch_history(all_tickers)
        metrics = {t: _per_ticker_metrics(data, t) for t in all_tickers}
    except Exception as exc:
        print(f"  ⚠️  Could not fetch sector history: {exc}")
        metrics = {}

    # Align every basket ticker to one consistent, current session. yfinance's
    # daily history lags a session for some names (their latest bar isn't
    # populated yet), which would otherwise blend different days into a basket's
    # median move and mis-state relative strength vs the current-session
    # benchmark. For any ticker whose history bar is stale, refresh its MOVE from
    # Yahoo's live quote (breadth/volume keep the ~unchanged history values).
    present = [m for m in metrics.values() if m]
    target_date = max((m[3] for m in present), default=None)
    if target_date:
        for t, m in metrics.items():
            if m and m[3] != target_date:
                ymove = _yahoo_move(t)
                if ymove is not None:
                    metrics[t] = (ymove, m[1], m[2], target_date)

    rows = []
    for name, cfg in SECTOR_BASKETS.items():
        present = [metrics[t] for t in cfg["tickers"] if metrics.get(t)]
        moves = [m[0] for m in present]
        # Median (not mean) so one outlier name can't define the basket's move.
        basket_move = round(_median(moves), 2) if moves else None

        # Breadth: average per-stock trend strength (% of 20/50/200 MAs the price
        # is above), across the basket, mapped to [-1, 1].
        strengths = [m[1] for m in present if m[1] is not None]
        breadth_frac = (sum(strengths) / len(strengths)) if strengths else None
        breadth_score = (2 * breadth_frac - 1) if breadth_frac is not None else None

        # Relative strength: sector move vs its benchmark (tech → Nasdaq, else S&P).
        # NaN-safe: a missing/NaN benchmark drops RS out of the blend (and shows
        # n/a) instead of poisoning the score.
        use_nasdaq = name in NASDAQ_BASKETS
        bench_move = nasdaq_move if use_nasdaq else sp_move
        bench_label = "Nasdaq" if use_nasdaq else "S&P"
        rs_score, rs_delta = _rel_strength(basket_move, bench_move)

        # Volume: above-average volume CONFIRMS the day's direction. Only
        # above-average volume contributes (floored at 0): below-average volume
        # means weak conviction → no signal, NOT an opposite one. (The old
        # `(avg_ratio-1)*direction` flipped sign on light volume, so a low-volume
        # down day scored *positive* — a confusing anti-signal. Floor fixes that.)
        vol_ratios = [m[2] for m in present if m[2] is not None]
        vol_score = None
        if vol_ratios and basket_move is not None:
            avg_ratio = sum(vol_ratios) / len(vol_ratios)
            direction = 1 if basket_move > 0 else -1 if basket_move < 0 else 0
            vol_score = _clamp(max(0.0, avg_ratio - 1.0)) * direction

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
            "sector": name, "move_pct": basket_move,
            "rel_strength": rs_delta,
            "benchmark": bench_label,
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
        bench = r.get("benchmark", "S&P")
        lines.append(f"- {r['sector']}: {move} (vs {bench} {rs}) | breadth {breadth} "
                     f"| **{r['label']}** ({r['score']:+.2f})")
    return "\n".join(lines)
