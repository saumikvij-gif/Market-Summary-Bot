"""
sector_watch.py
---------------
AI-stack "Sector Watch": a professional-style, multi-metric read of 8 thesis
baskets. The headline score is built to DESCRIBE the session — did the sector
rise or fall today — with the other metrics adding texture, the way a PM reads a
sector:

    Daily move        70%  (the basket's median constituent move this session —
                            the headline "did the sector go up or down today",
                            so the label tracks the day's actual direction)
    Breadth           15%  (% of the basket above its 20/50-day MAs — how broadly
                            the move participated: whole sector or a few names?)
    News sentiment    15%  (FinBERT on a dedicated Google News search, aggregated
                            per constituent so one name's volume can't dominate)
    Reddit             0%  (disabled reserve — RSS 'hot' is a poor same-day proxy)

Relative strength (the basket's move vs its benchmark — tech baskets vs the Nasdaq
100, the rest vs the S&P) is computed and shown as a CONTEXT COLUMN, but is not a
score input: being a relative read it would otherwise flip the headline against the
day's actual move, e.g. calling a sector that fell 4% "Bullish" merely for
outperforming a crashing market.

Each metric is normalized to [-1, 1] and blended (renormalized over whatever is
available) into a per-sector score + label. This is DISPLAY-ONLY — it does not
feed the composite market score. Everything is fail-safe.

The basket's headline move is the MEDIAN of its constituents, not the mean: the
baskets are small (4–10 names) and equal-weighted, so a single outlier (e.g. one
name up 30% on an upgrade) would otherwise drag the whole sector's reported move
and its relative-strength read. The median is the typical stock's move; breadth
still separately captures how broadly the basket moved.
"""

import re

import requests
import feedparser

import sentiment
from utils import clamp as _clamp

TIMEOUT = 15
# Per-CONSTITUENT sampling cap on the sector news read: each basket scores at most
# NEWS_PER_COMPANY fresh headlines per constituent company. A sample that size is
# plenty to gauge one name's mood, and it bounds FinBERT cost now that queries pull
# the full company-news flow (~90 headlines/basket). This is NOT a basket-wide cap
# — every constituent is still represented equally; see _company_news_score.
NEWS_PER_COMPANY = 8

# How a sector's sub-metrics blend into its overall score. The day's ABSOLUTE move
# dominates (0.70) so the label describes that session's direction — validated at
# ~95% absolute-direction match vs the prior relative/momentum-led blend's 71%
# (which mislabelled reversal and crash days). breadth + news add texture; Reddit is
# a disabled reserve (0). Relative strength is computed and shown as a context COLUMN
# but is deliberately NOT a score input, so it can't flip the headline against the
# day's actual move. Available sub-metrics are renormalized, so a 0-weight metric
# never contributes to the score.
SECTOR_METRIC_WEIGHTS = {
    "move":    0.70,
    "breadth": 0.15,
    "news":    0.15,
    "reddit":  0.00,
}
# ±2.5% median session move = a full ±1 move signal. A 2-3% day is a decisive
# session for these high-beta baskets, so it pins the label firmly bullish/bearish,
# while a sub-1% drift reads as the mild move it is.
MOVE_FULL_SCALE_PCT = 2.5
# 3.5% outperformance vs the benchmark = a full ±1 relative-strength reading. Used
# only for the displayed Rel. Str. column (RS is not a score input); kept wide
# because these high-beta AI/semis baskets routinely move several % vs the S&P.
RS_FULL_SCALE_PCT = 3.5

# Output calibration. Blending several sub-metrics (each already in [-1,1]) and
# renormalizing AVERAGES them toward the mean, so even a high-conviction sector
# reads timidly — directionally right but compressed toward 0 (a sector the
# signals all call bearish lands at -0.2 instead of -0.5). This de-compresses the
# final blended score with a sign-preserving power curve, sign(x)*|x|**EXP with
# EXP<1: small/mid magnitudes get pushed out toward the rails, ±1 stays ±1, and
# the SIGN never changes (calibration sharpens conviction, it can't flip a call).
# A single global knob — far less overfit-prone than per-sector/per-weight tuning,
# and it targets a real mathematical artifact. Should be re-fit once daily ground-
# truth data accumulates; 1.0 disables it.
SCORE_CALIBRATION_EXP = 0.65

# Each basket: representative tickers, a Google News query, and Reddit keywords.
# NB: distinct from market_summary.SECTORS (the 11 SPDR ETFs used for breadth) —
# these are the AI-stack thesis baskets this module reports on.
SECTOR_BASKETS = {
    "Hyperscalers & Neoclouds": {
        "tickers": ["GOOGL", "MSFT", "AMZN", "META", "ORCL", "CRWV", "NBIS"],
        "query": '(Microsoft OR Amazon OR Google OR Alphabet OR Meta OR Oracle OR CoreWeave OR Nebius OR hyperscaler OR "data center" OR "cloud computing")',
        "keywords": ["hyperscaler", "cloud", "azure", "aws", "coreweave", "data center", "gcp"],
        "companies": {
            "Alphabet":  ["google", "alphabet", "googl"],
            "Microsoft": ["microsoft", "msft", "azure"],
            "Amazon":    ["amazon", "amzn", "aws"],
            "Meta":      ["meta", "facebook"],
            "Oracle":    ["oracle", "orcl"],
            "CoreWeave": ["coreweave", "crwv"],
            "Nebius":    ["nebius", "nbis"],
        },
    },
    "Memory (DRAM/NAND/HBM)": {
        "tickers": ["MU", "WDC", "SNDK", "STX"],
        "query": '(Micron OR SanDisk OR "Western Digital" OR Seagate OR DRAM OR NAND OR HBM OR "memory chip")',
        "keywords": ["dram", "nand", "hbm", "memory chip", "micron"],
        "companies": {
            "Micron":          ["micron", "mu"],
            "Western Digital": ["western digital", "wdc"],
            "SanDisk":         ["sandisk", "sndk"],
            "Seagate":         ["seagate", "stx"],
        },
    },
    "Semiconductors / Compute": {
        "tickers": ["NVDA", "AMD", "ARM", "INTC", "QCOM", "TSM", "AVGO", "MRVL", "SMCI", "ANET"],
        "query": '(Nvidia OR AMD OR Intel OR Arm OR Qualcomm OR TSMC OR Broadcom OR Marvell OR semiconductor OR "AI chip")',
        "keywords": ["semiconductor", "gpu", "cpu", "chip", "nvidia", "amd", "intel"],
        "companies": {
            "Nvidia":     ["nvidia", "nvda"],
            "AMD":        ["amd"],
            "Arm":        ["arm"],
            "Intel":      ["intel", "intc"],
            "Qualcomm":   ["qualcomm", "qcom"],
            "TSMC":       ["tsmc", "taiwan semiconductor", "tsm"],
            "Broadcom":   ["broadcom", "avgo"],
            "Marvell":    ["marvell", "mrvl"],
            "Super Micro":["super micro", "supermicro", "smci"],
            "Arista":     ["arista", "anet"],
        },
    },
    "Networking / Interconnect": {
        "tickers": ["AVGO", "MRVL", "ANET", "LITE", "COHR", "APH", "CIEN"],
        "query": '(Broadcom OR Marvell OR Arista OR Coherent OR Lumentum OR Ciena OR Amphenol OR "optical networking" OR "switch silicon")',
        "keywords": ["networking", "interconnect", "optical", "switch", "ethernet", "broadcom", "arista"],
        "companies": {
            "Broadcom": ["broadcom", "avgo"],
            "Marvell":  ["marvell", "mrvl"],
            "Arista":   ["arista", "anet"],
            "Lumentum": ["lumentum", "lite"],
            "Coherent": ["coherent", "cohr"],
            "Amphenol": ["amphenol", "aph"],
            "Ciena":    ["ciena", "cien"],
        },
    },
    "SaaS": {
        "tickers": ["CRM", "NOW", "SNOW", "DDOG", "MDB", "NET", "HUBS", "PLTR", "ADBE"],
        "query": '(Salesforce OR ServiceNow OR Snowflake OR Datadog OR Cloudflare OR MongoDB OR HubSpot OR Palantir OR Adobe OR SaaS OR "enterprise software")',
        "keywords": ["saas", "software", "subscription"],
        "companies": {
            "Salesforce": ["salesforce", "crm"],
            "ServiceNow": ["servicenow"],
            "Snowflake":  ["snowflake"],
            "Datadog":    ["datadog", "ddog"],
            "MongoDB":    ["mongodb", "mdb"],
            "Cloudflare": ["cloudflare"],
            "HubSpot":    ["hubspot"],
            "Palantir":   ["palantir", "pltr"],
            "Adobe":      ["adobe", "adbe"],
        },
    },
    "Banking": {
        "tickers": ["JPM", "BAC", "WFC", "GS", "MS", "C", "USB", "PNC"],
        "query": '(JPMorgan OR "Bank of America" OR "Goldman Sachs" OR "Wells Fargo" OR "Morgan Stanley" OR Citigroup OR "U.S. Bancorp" OR PNC OR "big banks")',
        "keywords": ["bank", "banking", "lender", "loan", "credit", "default"],
        "companies": {
            "JPMorgan":        ["jpmorgan", "jp morgan", "jpm"],
            "Bank of America": ["bank of america", "bofa"],
            "Wells Fargo":     ["wells fargo", "wfc"],
            "Goldman Sachs":   ["goldman sachs", "goldman"],
            "Morgan Stanley":  ["morgan stanley"],
            "Citigroup":       ["citigroup", "citibank", "citi"],
            "U.S. Bancorp":    ["bancorp", "u.s. bank", "usb"],
            "PNC":             ["pnc"],
        },
    },
    "Consumer": {
        "tickers": ["WMT", "COST", "NKE", "MCD", "HD", "TGT", "LOW", "SBUX"],
        "query": '(Walmart OR Costco OR Nike OR "McDonald\'s" OR "Home Depot" OR Target OR Starbucks OR "Lowe\'s" OR "consumer spending" OR "retail sales")',
        "keywords": ["consumer", "retail", "spending", "shopper"],
        "companies": {
            "Walmart":     ["walmart", "wmt"],
            "Costco":      ["costco"],
            "Nike":        ["nike", "nke"],
            "McDonald's":  ["mcdonald"],
            "Home Depot":  ["home depot"],
            "Target":      ["target"],
            "Lowe's":      ["lowe"],
            "Starbucks":   ["starbucks", "sbux"],
        },
    },
    "Pharma / Healthcare": {
        "tickers": ["LLY", "JNJ", "MRK", "PFE", "UNH", "ABBV", "AMGN", "BMY"],
        "query": '("Eli Lilly" OR Pfizer OR Merck OR UnitedHealth OR "Johnson & Johnson" OR AbbVie OR Amgen OR "Bristol Myers" OR pharma OR FDA)',
        "keywords": ["pharma", "drug", "fda", "healthcare", "biotech"],
        "companies": {
            "Eli Lilly":          ["eli lilly", "lilly", "lly"],
            "Johnson & Johnson":  ["johnson & johnson", "johnson and johnson", "j&j", "jnj"],
            "Merck":              ["merck", "mrk"],
            "Pfizer":             ["pfizer", "pfe"],
            "UnitedHealth":       ["unitedhealth", "unitedhealthcare", "unh"],
            "AbbVie":             ["abbvie", "abbv"],
            "Amgen":              ["amgen", "amgn"],
            "Bristol Myers":      ["bristol myers", "bristol-myers", "bmy"],
        },
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


def _calibrate(score):
    """De-compress a blended score toward the rails to counter blend shrinkage.
    Sign-preserving: sign(x)*|x|**SCORE_CALIBRATION_EXP (EXP<1 expands mid-range
    conviction; EXP=1 is a no-op). Never flips a sign. None passes through."""
    if score is None:
        return None
    s = _clamp(score)
    return round(_clamp((1.0 if s >= 0 else -1.0) * abs(s) ** SCORE_CALIBRATION_EXP), 4)


def _move_score(move_pct):
    """Normalize a basket's session move (%) to [-1, 1], or None if missing/NaN.
    ±MOVE_FULL_SCALE_PCT in a session = full ±1, so the label tracks the day's
    actual direction (this is the dominant sub-metric)."""
    if not _finite(move_pct):
        return None
    return _clamp(move_pct / MOVE_FULL_SCALE_PCT)


def _median(values: list):
    """Median of a non-empty numeric list (mean of the two middle values if even)."""
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


# Moving-average windows blended into each stock's trend strength. 20- and 50-day
# only — the 200-day was dropped so breadth reacts to a multi-week regime change
# instead of staying pinned high through a selloff just because a long prior run
# keeps prices above their 200-DMA. A name can clear its 200-DMA yet have rolled
# over on the faster 20/50, which is the turn we want breadth to catch.
BREADTH_MAS = (20, 50)


def _fetch_history(tickers: list):
    """Batched ~1 year of (raw) Close + Volume for all tickers (one request).

    auto_adjust=False keeps the RAW close — the price Yahoo's site shows and the
    basis of its change% — so a basket's move matches Yahoo (and the benchmark)
    rather than a dividend/split-adjusted series. A year is plenty for the 50-day
    MA used in the breadth blend.
    """
    import yfinance as yf
    return yf.download(tickers, period="1y", progress=False,
                       group_by="ticker", auto_adjust=False)


def _per_ticker_metrics(data, ticker: str):
    """Return (move_pct, trend_strength, last_date) for one ticker, or None.
    last_date is the ISO date of the latest non-NaN close, used to detect tickers
    whose history lags a session so their move can be refreshed.

    trend_strength = fraction of the 20/50-day MAs the price is above, in [0, 1]
    (only MAs with enough history are counted).
    """
    try:
        df = data[ticker]
        closes = df["Close"].dropna()
        if len(closes) < 2:
            return None
        last = closes.iloc[-1]
        move = (last / closes.iloc[-2] - 1) * 100
        flags = [1 if last > closes.tail(w).mean() else 0
                 for w in BREADTH_MAS if len(closes) >= w]
        strength = (sum(flags) / len(flags)) if flags else None
        last_date = closes.index[-1].date().isoformat()
        return (round(float(move), 2), strength, last_date)
    except Exception:
        return None


def _yahoo_quote(ticker_symbol: str):
    """(change_pct, last_price) straight from Yahoo's quote (regularMarketChangePercent
    / regularMarketPrice), or (None, None). Used to refresh a ticker whose daily-
    history bar lags a session, so its move AND breadth are the current session —
    consistent with the benchmark and Yahoo's site."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker_symbol).info
        pct, price = info.get("regularMarketChangePercent"), info.get("regularMarketPrice")
        return (round(float(pct), 2) if pct is not None else None,
                float(price) if price is not None else None)
    except Exception:
        return None, None


def _trend_strength_with(closes, live_price):
    """Breadth for a refreshed ticker: fraction of the 20/50-day MAs the live
    price sits above, with the live price appended to the historical closes so the
    MA windows include the current session (matching _per_ticker_metrics)."""
    if live_price is None:
        return None
    vals = [float(c) for c in closes] + [float(live_price)]
    flags = [1 if live_price > (sum(vals[-w:]) / w) else 0
             for w in BREADTH_MAS if len(vals) >= w]
    return (sum(flags) / len(flags)) if flags else None


def _company_news_score(titles: list, companies: dict, scorer, cap: int = NEWS_PER_COMPANY) -> tuple:
    """Equal-weight news score across a basket's CONSTITUENTS, so one company's
    news *volume* can't dominate the basket.

    A naive mean over every article breaks when, say, an earnings day floods one
    name with dozens of stories: the basket then reads as that one company's day,
    not the sector's. Instead — mirroring how the basket's MOVE is a median across
    equal-weighted constituents — we route each headline to every constituent it
    names, score each constituent as the mean of its own (capped) headlines, then
    take the mean across constituents. Oracle's 80 articles collapse to a single
    Oracle vote. Headlines naming no constituent form one 'sector' pseudo-
    constituent (so genuinely sector-wide news still counts, once).

    `titles` are unscored headlines; `companies` maps a constituent name to its
    lowercase aliases, matched as whole words (so the ticker "MU" won't hit
    "museum"); `scorer` maps a title → polarity. Each bucket keeps at most `cap`
    headlines and ONLY those are scored — a sample that size gauges a name's mood
    well and bounds FinBERT cost. Returns (score in [-1, 1] or None, detail dict).
    """
    pats = {name: re.compile(r"\b(" + "|".join(re.escape(a) for a in aliases) + r")\b", re.I)
            for name, aliases in (companies or {}).items()}
    buckets, theme = {}, []
    for t in titles:
        hit = [name for name, pat in pats.items() if pat.search(t)]
        if hit:
            for name in hit:
                b = buckets.setdefault(name, [])
                if len(b) < cap:                 # per-constituent sampling cap
                    b.append(t)
        elif len(theme) < cap:
            theme.append(t)
    # Score only the headlines we kept (scorer is cached, so titles shared across
    # buckets cost nothing extra).
    score_of = {t: scorer(t) for t in {x for b in buckets.values() for x in b} | set(theme)}
    means = [sum(score_of[t] for t in b) / len(b) for b in buckets.values() if b]
    if theme:                                    # unattributed sector-wide items = one vote
        means.append(sum(score_of[t] for t in theme) / len(theme))
    if not means:
        return None, {"companies": 0, "themed": 0, "scored": len(score_of)}
    return round(sum(means) / len(means), 4), {
        "companies": len(buckets), "themed": len(theme), "scored": len(score_of)}


def _news_sentiment(query: str, companies: dict = None) -> tuple:
    """(news score, n_headlines) from a Google News RSS search, FinBERT-scored.

    Reuses the main pipeline's staleness and boilerplate filters (news_feeds) so
    the same foreign-bourse template junk / days-old items that are kept out of
    the composite can't leak into a sector's news read either. Near-duplicate
    stories carried by several outlets are also collapsed: Google News appends
    " - Publisher" to each title, so the same wire story from two outlets would
    otherwise both count — we dedup on the headline minus that publisher suffix.

    The query pulls the full company-news flow (constituents OR sector theme), and
    the score is aggregated PER CONSTITUENT (see _company_news_score) with a per-
    constituent sampling cap so one name's volume can't dominate and FinBERT cost
    stays bounded. Without a `companies` map it falls back to a plain mean.
    """
    import news_feeds
    url = ("https://news.google.com/rss/search?q="
           + requests.utils.quote(query) + "&hl=en-US&gl=US&ceid=US:en")
    resp = requests.get(url, headers=news_feeds.HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    titles, seen = [], set()
    for e in feedparser.parse(resp.content).entries:
        t = (e.get("title") or "").strip()
        if not t or news_feeds.is_boilerplate(t) or news_feeds.is_stale(e):
            continue
        key = news_feeds.norm_title(t.rsplit(" - ", 1)[0])   # drop " - Publisher"
        if key in seen:
            continue
        seen.add(key)
        titles.append(t)
    if not titles:
        return None, 0
    engine = sentiment.news_engine()
    if companies:
        score, _ = _company_news_score(titles, companies,
                                       lambda t: sentiment.score_text(t, engine))
        return score, len(titles)
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
    # benchmark. For any ticker whose history bar is stale, refresh its MOVE and
    # BREADTH from Yahoo's live quote.
    present = [m for m in metrics.values() if m]
    target_date = max((m[2] for m in present), default=None)
    if target_date:
        for t, m in metrics.items():
            if not (m and m[2] != target_date):
                continue
            ymove, yprice = _yahoo_quote(t)
            if ymove is None:
                continue
            strength = m[1]
            try:                       # recompute breadth against the live price
                fresh = _trend_strength_with(data[t]["Close"].dropna(), yprice)
                if fresh is not None:
                    strength = fresh
            except Exception:
                pass
            metrics[t] = (ymove, strength, target_date)

    rows = []
    for name, cfg in SECTOR_BASKETS.items():
        present = [metrics[t] for t in cfg["tickers"] if metrics.get(t)]
        moves = [m[0] for m in present]
        # Median (not mean) so one outlier name can't define the basket's move.
        basket_move = round(_median(moves), 2) if moves else None

        # Breadth: average per-stock trend strength (% of 20/50 MAs the price
        # is above), across the basket, mapped to [-1, 1].
        strengths = [m[1] for m in present if m[1] is not None]
        breadth_frac = (sum(strengths) / len(strengths)) if strengths else None
        breadth_score = (2 * breadth_frac - 1) if breadth_frac is not None else None

        # Relative strength: sector move vs its benchmark (tech → Nasdaq, else S&P).
        # Display-only context column, NOT a score input. NaN-safe: a missing/NaN
        # benchmark just shows n/a.
        use_nasdaq = name in NASDAQ_BASKETS
        bench_move = nasdaq_move if use_nasdaq else sp_move
        bench_label = "Nasdaq" if use_nasdaq else "S&P"
        _, rs_delta = _rel_strength(basket_move, bench_move)

        try:
            news_score, _ = _news_sentiment(cfg["query"], cfg.get("companies"))
        except Exception as exc:
            print(f"  ⚠️  Sector news failed for {name}: {exc}")
            news_score = None
        reddit_score, _ = _reddit_sentiment(cfg["keywords"], reddit_titles)

        # Blend available sub-metrics, renormalized over their weights. The day's
        # absolute move is the dominant driver so the label describes the session.
        move_score = _move_score(basket_move)
        parts = {"move": move_score, "breadth": breadth_score,
                 "news": news_score, "reddit": reddit_score}
        avail = {k: v for k, v in parts.items() if v is not None}
        total_w = sum(SECTOR_METRIC_WEIGHTS[k] for k in avail) or 1.0
        blended = _clamp(sum(SECTOR_METRIC_WEIGHTS[k] * v for k, v in avail.items()) / total_w)
        # De-compress the blend so conviction isn't lost to mean-shrinkage.
        score = _calibrate(blended)

        rows.append({
            "sector": name, "move_pct": basket_move,
            "rel_strength": rs_delta,
            "benchmark": bench_label,
            "breadth_pct": round(breadth_frac * 100) if breadth_frac is not None else None,
            "news_score": news_score,
            "score": score, "label": sentiment.label_for(score),
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
