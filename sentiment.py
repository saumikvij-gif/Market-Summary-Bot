"""
sentiment.py
------------
Computes a daily, reproducible market-sentiment composite for US equities and
returns a Joywin-style dashboard dict. This REPLACES the LLM's subjective score
as the number stored in the DB and plotted on the daily chart.

Composite components and default weights:
    Market data : 50%   (S&P 500 + Nasdaq % change, VIX % change inverted)
    News         : 35%   (headline NLP sentiment)
    Reddit       : 10%   (subreddit post-title NLP sentiment, capped influence)
    Fed          :  5%   (hawkish/dovish tone of Fed statements)

Sentiment engine: VADER (vaderSentiment) — lightweight, deterministic, and
CI-friendly. FinBERT would be more finance-aware but needs transformers+torch
(~1GB) and is slow/fragile in CI; `score_text()` is isolated so it can be
swapped later without touching the rest of the pipeline.

Standalone:
    python sentiment.py        # fetch live data + headlines, print the dashboard JSON
"""

import sys
import json
import datetime

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ── Tunable weights and normalization constants ────────────────────────────────

WEIGHTS = {"market": 0.50, "news": 0.35, "reddit": 0.10, "fed": 0.05}

# A ±2% average move in the big indices is treated as a full ±1 equity signal.
EQUITY_FULL_SCALE_PCT = 2.0
# A ±15% VIX move is treated as a full ±1 (inverted) volatility signal.
VIX_FULL_SCALE_PCT = 15.0
# Within the market score: how much equities vs. the VIX matter.
EQUITY_VS_VIX = (0.70, 0.30)

# VADER compound thresholds for the +1/0/-1 discrete classification.
POS_THRESHOLD = 0.05
NEG_THRESHOLD = -0.05

# Require at least this many Fed keyword hits before the tone score can reach
# full scale — keeps a single stray match from swinging fed_score to ±1.
FED_FULL_SCALE_HITS = 4

# Keyword lexicon for Fed tone. Hawkish (tightening) is bearish for equities;
# dovish (easing) is bullish.
HAWKISH = ["hike", "raise rates", "tighten", "restrictive", "inflation",
           "higher for longer", "rate increase"]
DOVISH = ["cut", "ease", "easing", "accommodative", "lower rates", "stimulus",
          "rate reduction", "dovish"]

_analyzer = SentimentIntensityAnalyzer()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def score_text(text: str) -> float:
    """Return a sentiment polarity in [-1, 1] for one piece of text (VADER)."""
    return _analyzer.polarity_scores(text or "")["compound"]


def classify(text: str) -> int:
    """Discretize a headline to +1 (bullish) / 0 (neutral) / -1 (bearish)."""
    c = score_text(text)
    if c >= POS_THRESHOLD:
        return 1
    if c <= NEG_THRESHOLD:
        return -1
    return 0


def _avg_classified(titles: list) -> tuple:
    """Mean of discrete classifications over titles → (score in [-1,1], counts)."""
    labels = [classify(t) for t in titles if t]
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
    score, counts = _avg_classified(news_titles)
    return {"score": score, "detail": {"n": len(news_titles), **counts}}


def reddit_component(reddit_titles: list) -> dict:
    score, counts = _avg_classified(reddit_titles)
    return {"score": score, "detail": {"n": len(reddit_titles), **counts}}


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


def build_dashboard(market_data: dict, headlines: dict, run_date: str = None) -> dict:
    """Compute the full Joywin-style sentiment dashboard as a dict."""
    if run_date is None:
        run_date = datetime.date.today().isoformat()

    news_titles, reddit_titles, fed_titles = _split_headlines(headlines)

    market = market_component(market_data)
    news = news_component(news_titles)
    reddit = reddit_component(reddit_titles)
    fed = fed_component(fed_titles)

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
    return "\n".join(lines)


if __name__ == "__main__":
    import market_summary as ms
    import reddit_news

    data = ms.fetch_all_data()
    heads = reddit_news.gather_headlines(limit=8)
    dash = build_dashboard(data, heads)
    print(json.dumps(dash, indent=2))
    print("\n" + render_dashboard_md(dash))
