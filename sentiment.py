"""
sentiment.py
------------
Computes a daily, reproducible market-sentiment composite for US equities and
returns a Joywin-style dashboard dict. This REPLACES the LLM's subjective score
as the number stored in the DB and plotted on the daily chart.

Composite components and default weights:
    Market data : 55%   (S&P 500 + Nasdaq % change, VIX % change inverted)
    News         : 35%   (headline NLP sentiment)
    Reddit       :  5%   (subreddit post-title NLP sentiment — usually neutral)
    Fed          :  5%   (hawkish/dovish tone of Fed statements)

Sentiment engines (right tool per source):
  * News + Fed (formal text) → FinBERT, which is finance-aware — when opted in
    via SENTIMENT_ENGINE and its deps (requirements-ml.txt) are installed.
  * Reddit (social/slang)     → VADER, which was built for social media and
    reads slang/sarcasm better than FinBERT.
Default is "hybrid" (FinBERT news/Fed + VADER Reddit). FinBERT loads lazily and
safely falls back to VADER if its deps/model aren't available, so a run never
breaks. Set SENTIMENT_ENGINE=vader to force VADER everywhere (lightweight).

Standalone:
    python sentiment.py        # fetch live data + headlines, print the dashboard JSON
"""

import os
import sys
import json
import datetime

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# "hybrid" (default — FinBERT for formal news/Fed, VADER for Reddit) or "vader"
# (all sources use VADER). FinBERT needs transformers+torch (requirements-ml.txt)
# and loads lazily; if those deps/model aren't available it safely falls back to
# VADER, so a run never breaks even without the ML extras installed.
SENTIMENT_ENGINE = os.environ.get("SENTIMENT_ENGINE", "hybrid").lower()

# ── Tunable weights and normalization constants ────────────────────────────────

WEIGHTS = {"market": 0.55, "news": 0.35, "reddit": 0.05, "fed": 0.05}

# A ±2% average move in the big indices is treated as a full ±1 equity signal.
EQUITY_FULL_SCALE_PCT = 2.0
# A ±15% VIX move is treated as a full ±1 (inverted) volatility signal.
VIX_FULL_SCALE_PCT = 15.0
# Within the market score: how much equities vs. the VIX matter.
EQUITY_VS_VIX = (0.70, 0.30)

# Thresholds for the +1/0/-1 discrete classification, per engine. VADER's
# compound clusters near 0, so a small band works; FinBERT pushes probabilities
# harder, so it needs a wider neutral band.
THRESHOLDS = {
    "vader":   (0.05, -0.05),
    "finbert": (0.15, -0.15),
}

# Require at least this many Fed keyword hits before the tone score can reach
# full scale — keeps a single stray match from swinging fed_score to ±1.
FED_FULL_SCALE_HITS = 4

# Keyword lexicon for Fed tone. Hawkish (tightening) is bearish for equities;
# dovish (easing) is bullish.
HAWKISH = ["hike", "raise rates", "tighten", "restrictive", "inflation",
           "higher for longer", "rate increase"]
DOVISH = ["cut", "ease", "easing", "accommodative", "lower rates", "stimulus",
          "rate reduction", "dovish"]

_vader = SentimentIntensityAnalyzer()
_finbert = None  # None = not loaded yet; False = unavailable; else a pipeline


# ── Helpers ────────────────────────────────────────────────────────────────────

def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _get_finbert():
    """Lazily load the FinBERT pipeline; return False (once) if unavailable."""
    global _finbert
    if _finbert is None:
        try:
            from transformers import pipeline
            _finbert = pipeline("text-classification",
                                model="ProsusAI/finbert", top_k=None)
            print("  (using FinBERT for sentiment)")
        except Exception as exc:  # missing deps / model / no network
            print(f"  ⚠️  FinBERT unavailable ({exc}); using VADER instead.")
            _finbert = False
    return _finbert


def _finbert_available() -> bool:
    """True only if FinBERT is opted into AND its pipeline loads."""
    if SENTIMENT_ENGINE not in ("finbert", "hybrid", "auto"):
        return False
    return bool(_get_finbert())


def news_engine() -> str:
    """Engine for formal news/Fed text: FinBERT when available, else VADER."""
    return "finbert" if _finbert_available() else "vader"


def _vader_score(text: str) -> float:
    return _vader.polarity_scores(text)["compound"]


def score_text(text: str, engine: str = "vader") -> float:
    """Sentiment polarity in [-1, 1]. engine='finbert' uses FinBERT (formal text),
    'vader' uses VADER (social text). FinBERT falls back to VADER if unavailable.
    FinBERT polarity = P(positive) - P(negative)."""
    text = text or ""
    if engine == "finbert":
        clf = _get_finbert()
        if clf:
            try:
                out = clf(text[:512])               # truncate to model limit
                scores = out[0] if out and isinstance(out[0], list) else out
                probs = {r["label"].lower(): r["score"] for r in scores}
                return probs.get("positive", 0.0) - probs.get("negative", 0.0)
            except Exception as exc:
                print(f"  ⚠️  FinBERT scoring failed ({exc}); using VADER for this item.")
    return _vader_score(text)


def classify(text: str, engine: str = "vader") -> int:
    """Discretize a headline to +1 (bullish) / 0 (neutral) / -1 (bearish),
    using the engine's own thresholds."""
    c = score_text(text, engine)
    pos, neg = THRESHOLDS.get(engine, THRESHOLDS["vader"])
    if c >= pos:
        return 1
    if c <= neg:
        return -1
    return 0


def _avg_classified(titles: list, engine: str = "vader") -> tuple:
    """Mean of discrete classifications over titles → (score in [-1,1], counts)."""
    labels = [classify(t, engine) for t in titles if t]
    counts = {"bullish": labels.count(1),
              "neutral": labels.count(0),
              "bearish": labels.count(-1)}
    score = (sum(labels) / len(labels)) if labels else 0.0
    return round(score, 4), counts


# ── Component scores ────────────────────────────────────────────────────────────

def market_component(market_data: dict) -> dict:
    """Score from S&P 500 + Nasdaq % change and (inverted) VIX % change."""
    idx = market_data.get("indices", {})

    def pct(name):
        q = idx.get(name, {})
        return q.get("pct_change") if isinstance(q, dict) and "error" not in q else None

    sp, nq, vix = pct("S&P 500"), pct("Nasdaq 100"), pct("VIX")

    equities = [v for v in (sp, nq) if v is not None]
    equity_norm = _clamp((sum(equities) / len(equities)) / EQUITY_FULL_SCALE_PCT) if equities else 0.0
    # VIX up = fear = bearish, hence the negative sign.
    vix_norm = _clamp(-vix / VIX_FULL_SCALE_PCT) if vix is not None else 0.0

    we, wv = EQUITY_VS_VIX
    score = _clamp(we * equity_norm + wv * vix_norm)
    return {
        "score": round(score, 4),
        "detail": {"sp500_pct": sp, "nasdaq_pct": nq, "vix_pct": vix},
    }


def news_component(news_titles: list) -> dict:
    # Formal financial headlines → FinBERT (when available), else VADER.
    engine = news_engine()
    score, counts = _avg_classified(news_titles, engine)
    return {"score": score, "detail": {"n": len(news_titles), "engine": engine, **counts}}


def reddit_component(reddit_titles: list) -> dict:
    # Social/slang text → VADER, which was built for it (FinBERT misreads slang).
    score, counts = _avg_classified(reddit_titles, "vader")
    return {"score": score, "detail": {"n": len(reddit_titles), "engine": "vader", **counts}}


def fed_component(fed_titles: list) -> dict:
    """Net hawkish/dovish tone of Fed statement titles, normalized to [-1, 1]."""
    hawk = dov = 0
    for t in fed_titles:
        low = (t or "").lower()
        hawk += sum(1 for w in HAWKISH if w in low)
        dov += sum(1 for w in DOVISH if w in low)
    total = hawk + dov
    # Dovish is bullish (+), hawkish is bearish (−). Divide by at least
    # FED_FULL_SCALE_HITS so a lone keyword yields a muted score, not ±1.
    score = _clamp((dov - hawk) / max(total, FED_FULL_SCALE_HITS)) if total else 0.0
    return {"score": round(score, 4), "detail": {"hawkish": hawk, "dovish": dov}}


# ── Composite + labelling ──────────────────────────────────────────────────────

def label_for(score: float) -> str:
    if score > 0.3:
        return "Bullish"
    if score >= 0.1:
        return "Slightly Bullish"
    if score > -0.1:
        return "Neutral"
    if score >= -0.3:
        return "Slightly Bearish"
    return "Bearish"


def _split_headlines(headlines: dict) -> tuple:
    """Split a {source: [titles]} dict into (news, reddit, fed) title lists."""
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


def _commentary(overall_label, market, news, reddit, fed) -> str:
    d = market["detail"]
    parts = []
    moves = []
    if d.get("nasdaq_pct") is not None:
        moves.append(f"Nasdaq {d['nasdaq_pct']:+.1f}%")
    if d.get("sp500_pct") is not None:
        moves.append(f"S&P 500 {d['sp500_pct']:+.1f}%")
    if d.get("vix_pct") is not None:
        moves.append(f"VIX {d['vix_pct']:+.1f}%")
    if moves:
        parts.append("Equities " + ("rose" if market["score"] > 0 else
                     "fell" if market["score"] < 0 else "were mixed")
                     + " with " + ", ".join(moves) + ".")
    parts.append(f"News headlines read {label_for(news['score']).lower()}.")
    parts.append(f"Reddit sentiment is {label_for(reddit['score']).lower()}.")
    if fed["detail"]["hawkish"] or fed["detail"]["dovish"]:
        tone = ("dovish" if fed["score"] > 0 else "hawkish" if fed["score"] < 0 else "neutral")
        parts.append(f"Fed tone appears {tone}.")
    parts.append(f"Overall market sentiment: {overall_label}.")
    return " ".join(parts)


def _extreme_headlines(news_titles: list, reddit_titles: list) -> dict:
    """Most bullish and most bearish single headline by polarity, or {}.

    Scores each headline with the engine appropriate to its source.
    """
    eng = news_engine()
    scored = [(t, score_text(t, eng)) for t in news_titles if t]
    scored += [(t, score_text(t, "vader")) for t in reddit_titles if t]
    if not scored:
        return {}
    bull = max(scored, key=lambda x: x[1])
    bear = min(scored, key=lambda x: x[1])
    return {
        "most_bullish": {"title": bull[0], "score": round(bull[1], 3)},
        "most_bearish": {"title": bear[0], "score": round(bear[1], 3)},
    }


def _divergence(market_score: float, news_score: float) -> str | None:
    """Flag when price action and news mood meaningfully disagree.

    Since the score is descriptive (validated: no next-day predictive power),
    the genuinely interesting signal is divergence — the crowd's mood pulling
    against the tape. Returns a note, or None when they broadly agree.
    """
    if abs(market_score) < 0.1 or abs(news_score) < 0.1:
        return None
    if (market_score > 0) == (news_score > 0):
        return None
    if market_score > 0:
        return ("⚠️ Divergence: prices rose but the news mood is negative — "
                "the tape is climbing a wall of worry.")
    return ("⚠️ Divergence: prices fell but the news mood is positive — "
            "headlines are upbeat against a down tape.")


def build_dashboard(market_data: dict, headlines: dict, run_date: str = None) -> dict:
    """Compute the full Joywin-style sentiment dashboard as a dict."""
    if run_date is None:
        run_date = datetime.date.today().isoformat()

    news_titles, reddit_titles, fed_titles = _split_headlines(headlines)

    market = market_component(market_data)
    news = news_component(news_titles)
    reddit = reddit_component(reddit_titles)
    fed = fed_component(fed_titles)

    # Surprise insights: divergence (mood vs tape) and the day's standout headlines.
    divergence = _divergence(market["score"], news["score"])
    extremes = _extreme_headlines(news_titles, reddit_titles)

    # Weighted composite. Each component is already in [-1, 1], so the weighted
    # sum is too (weights sum to 1.0).
    overall = (
        WEIGHTS["market"] * market["score"]
        + WEIGHTS["news"] * news["score"]
        + WEIGHTS["reddit"] * reddit["score"]
        + WEIGHTS["fed"] * fed["score"]
    )
    overall = round(_clamp(overall), 4)
    label = label_for(overall)

    return {
        "date": run_date,
        "overall_score": overall,
        "label": label,
        "market_score": market["score"],
        "news_score": news["score"],
        "reddit_score": reddit["score"],
        "fed_score": fed["score"],
        "weights": WEIGHTS,
        "components": {
            "market": market["detail"],
            "news": news["detail"],
            "reddit": reddit["detail"],
            "fed": fed["detail"],
        },
        "divergence": divergence,
        "headlines": extremes,
        "summary_text": _commentary(label, market, news, reddit, fed),
    }


def render_dashboard_md(dash: dict) -> str:
    """Render the dashboard as a markdown section for the summary document."""
    def pct100(x):
        return f"{x * 100:+.0f}"
    lines = [
        "## Market Sentiment Dashboard",
        "",
        f"**Overall Score:** {dash['overall_score']:+.2f}  →  **{dash['label']}**",
        "",
        "| Component | Weight | Score |",
        "| --- | --- | --- |",
        f"| Market data | {dash['weights']['market']:.0%} | {dash['market_score']:+.2f} |",
        f"| News headlines | {dash['weights']['news']:.0%} | {dash['news_score']:+.2f} |",
        f"| Reddit | {dash['weights']['reddit']:.0%} | {dash['reddit_score']:+.2f} |",
        f"| Fed tone | {dash['weights']['fed']:.0%} | {dash['fed_score']:+.2f} |",
        "",
        f"_{dash['summary_text']}_",
    ]

    if dash.get("divergence"):
        lines += ["", f"**{dash['divergence']}**"]

    h = dash.get("headlines") or {}
    if h:
        lines += ["", "**Standout headlines:**"]
        if h.get("most_bullish"):
            b = h["most_bullish"]
            lines.append(f"- 🟢 Most bullish ({b['score']:+.2f}): {b['title']}")
        if h.get("most_bearish"):
            b = h["most_bearish"]
            lines.append(f"- 🔴 Most bearish ({b['score']:+.2f}): {b['title']}")

    return "\n".join(lines)


if __name__ == "__main__":
    import market_summary as ms
    import reddit_news

    data = ms.fetch_all_data()
    heads = reddit_news.gather_headlines(limit=8)
    dash = build_dashboard(data, heads)
    print(json.dumps(dash, indent=2))
    print("\n" + render_dashboard_md(dash))
