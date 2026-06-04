"""
sentiment.py
------------
Computes a daily, reproducible market-sentiment composite for US equities and
returns a Joywin-style dashboard dict. This REPLACES the LLM's subjective score
as the number stored in the DB and plotted on the daily chart.

Composite components and default weights:
    Market data : 70%   (a blend of equity moves, sector breadth, VIX, and the
                         10Y Treasury yield — see MARKET_SUBWEIGHTS)
    News         : 20%   (headline NLP sentiment)
    Reddit       :  0%   (DISABLED — see note below; component still computed for
                         display but contributes nothing to the score)
    Fed          : 10%   (the Fed Expectations Score — front-end T-Bill move +
                         FinBERT tone of fresh Fed communications; see the Fed
                         Expectations sub-model below)

The market component carries the most weight because it is the only piece with a
validated, faithful same-day read of the tape (same-day corr ≈ 0.97 vs the index
move — see validate_sentiment.py); news/Reddit headline-NLP is a noisier, small-
sample signal, so it is trimmed. Reddit is weighted 0 for now: public RSS "hot"
feeds are a poor same-day proxy (hot posts persist for days) — the weight will be
restored once a proper social feed (Reddit/X/Threads API) is wired in.

This is a DESCRIPTIVE recap of how the market traded today — every input is a
same-day reading. It is not a forecast (validated: no next-day predictive edge).
A short EMA (SMOOTHING_SPAN) of the daily composite is also reported as a trend,
because the raw same-day score is intrinsically jumpy (it tracks a near-random
daily return); the smoothed line is the more readable day-over-day signal.

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
import json
import functools
import datetime

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from utils import clamp as _clamp, force_utf8

force_utf8()

# "hybrid" (default — FinBERT for formal news/Fed, VADER for Reddit) or "vader"
# (all sources use VADER). FinBERT needs transformers+torch (requirements-ml.txt)
# and loads lazily; if those deps/model aren't available it safely falls back to
# VADER, so a run never breaks even without the ML extras installed.
SENTIMENT_ENGINE = os.environ.get("SENTIMENT_ENGINE", "hybrid").lower()

# ── Tunable weights and normalization constants ────────────────────────────────

WEIGHTS = {"market": 0.70, "news": 0.20, "reddit": 0.00, "fed": 0.10}

# Span (in trading days) for the EMA trend line on the daily composite. The raw
# same-day score whipsaws (it faithfully tracks a near-random daily return), so a
# short EMA is surfaced alongside it as the readable day-over-day signal.
SMOOTHING_SPAN = 5

# A ±2% average move in the big indices is treated as a full ±1 equity signal.
EQUITY_FULL_SCALE_PCT = 2.0
# A ±15% VIX move is treated as a full ±1 (inverted) volatility signal.
VIX_FULL_SCALE_PCT = 15.0
# A ±5% move in the 10Y yield is treated as a full ±1 (inverted) rates signal.
RATES_FULL_SCALE_PCT = 5.0

# The market score is a blend of four distinct dimensions. If some data is
# missing (e.g. no sector data on a backfilled history day), the present
# sub-signals are renormalized so the weights still sum to 1.
MARKET_SUBWEIGHTS = {
    "equities": 0.40,   # magnitude of the big index moves (S&P + Nasdaq)
    "breadth":  0.25,   # how broadly sectors are participating (advance/decline)
    "vix":      0.20,   # volatility / fear (inverted)
    "rates":    0.15,   # 10Y Treasury yield change (inverted)
}

# Thresholds for the +1/0/-1 discrete classification, per engine. VADER's
# compound clusters near 0, so a small band works; FinBERT pushes probabilities
# harder, so it needs a wider neutral band.
THRESHOLDS = {
    "vader":   (0.05, -0.05),
    "finbert": (0.15, -0.15),
}

_vader = SentimentIntensityAnalyzer()
_finbert = None  # None = not loaded yet; False = unavailable; else a pipeline


# ── Helpers ────────────────────────────────────────────────────────────────────

def ema(values: list, span: int = SMOOTHING_SPAN):
    """Exponential moving average of a numeric series (oldest-first).

    Returns the final EMA value (the smoothed 'today'), or None for an empty
    series. A shorter span tracks the raw score more closely; a longer span is
    smoother. Used to surface a readable trend over the jumpy daily composite.
    """
    vals = [v for v in (values or []) if v is not None]
    if not vals:
        return None
    alpha = 2.0 / (span + 1)
    e = vals[0]
    for v in vals[1:]:
        e = alpha * v + (1 - alpha) * e
    return e


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


@functools.lru_cache(maxsize=4096)
def score_text(text: str, engine: str = "vader") -> float:
    """Sentiment polarity in [-1, 1]. engine='finbert' uses FinBERT (formal text),
    'vader' uses VADER (social text). FinBERT falls back to VADER if unavailable.
    FinBERT polarity = P(positive) - P(negative).

    Cached on (text, engine) so each unique headline is scored once per run —
    the standout-headline pass reuses the news component's scores instead of
    re-running the (slow) FinBERT inference."""
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
    """Mean *continuous* polarity over titles → (score in [-1,1], counts).

    Averaging the continuous score (not discrete +1/0/-1) preserves magnitude
    and avoids the score collapsing to exactly 0.0 when bullish and bearish
    headline counts happen to balance. Counts are still reported for display.
    """
    kept = [t for t in titles if t]
    if not kept:
        return 0.0, {"bullish": 0, "neutral": 0, "bearish": 0}
    scores = [score_text(t, engine) for t in kept]
    # Reuse classify() for the discrete bucketing so the threshold logic lives in
    # exactly one place. score_text is lru-cached, so this adds no extra inference.
    labels = [classify(t, engine) for t in kept]
    counts = {"bullish": sum(1 for l in labels if l == 1),
              "neutral": sum(1 for l in labels if l == 0),
              "bearish": sum(1 for l in labels if l == -1)}
    return round(sum(scores) / len(scores), 4), counts


# ── Component scores ────────────────────────────────────────────────────────────

def _pct_of(section: dict, name: str):
    """pct_change for one instrument in a section, or None if missing/errored."""
    q = (section or {}).get(name, {})
    return q.get("pct_change") if isinstance(q, dict) and "error" not in q else None


def _sector_breadth(sectors: dict):
    """Advance/decline breadth across sector ETFs in [-1, 1], or None if absent."""
    moves = [q.get("pct_change") for q in (sectors or {}).values()
             if isinstance(q, dict) and "error" not in q and q.get("pct_change") is not None]
    if not moves:
        return None, None
    up = sum(1 for m in moves if m > 0)
    down = sum(1 for m in moves if m < 0)
    breadth = (up - down) / len(moves)          # already in [-1, 1]
    return round(breadth, 4), {"up": up, "down": down, "total": len(moves)}


def market_component(market_data: dict) -> dict:
    """Blend equities, sector breadth, VIX, and rates into a market score.

    Each sub-signal is normalized to [-1, 1]; present signals are combined with
    MARKET_SUBWEIGHTS, renormalized over whatever data is available.
    """
    idx = market_data.get("indices", {})
    sp = _pct_of(idx, "S&P 500")
    nq = _pct_of(idx, "Nasdaq 100")
    vix = _pct_of(idx, "VIX")
    rate = _pct_of(market_data.get("rates", {}), "10Y Treasury Yield")
    breadth, breadth_detail = _sector_breadth(market_data.get("sectors", {}))

    # Build each sub-signal as (weight, normalized_value) when its data exists.
    signals = {}
    equities = [v for v in (sp, nq) if v is not None]
    if equities:
        signals["equities"] = _clamp((sum(equities) / len(equities)) / EQUITY_FULL_SCALE_PCT)
    if breadth is not None:
        signals["breadth"] = breadth
    if vix is not None:
        signals["vix"] = _clamp(-vix / VIX_FULL_SCALE_PCT)        # VIX up = bearish
    if rate is not None:
        signals["rates"] = _clamp(-rate / RATES_FULL_SCALE_PCT)   # yields up = headwind

    # Weighted blend, renormalized over the sub-signals actually present.
    total_w = sum(MARKET_SUBWEIGHTS[k] for k in signals) or 1.0
    score = _clamp(sum(MARKET_SUBWEIGHTS[k] * v for k, v in signals.items()) / total_w)

    return {
        "score": round(score, 4),
        "detail": {
            "sp500_pct": sp, "nasdaq_pct": nq, "vix_pct": vix,
            "rate_10y_pct": rate, "breadth": breadth,
            "sectors": breadth_detail,
            "subscores": {k: round(v, 4) for k, v in signals.items()},
        },
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


def fed_component(market_data: dict, fed_titles: list = None) -> dict:
    """The Fed Expectations Score — the composite's 4th component.

    A renormalized blend of the front-end T-Bill move (active daily), the FinBERT
    tone of any fresh Fed communications (active on event days), and an inflation-
    surprise hook (inactive — no free consensus data). The full sub-model lives
    in the "Fed Expectations sub-model" section below.
    """
    result = fed_expectations_score(market_data, fed_titles)
    return {"score": result["score"], "label": result["label"],
            "detail": result["detail"]}


# ── Fed Expectations sub-model ───────────────────────────────────────────────────
# The Fed score is a market-driven read of where monetary-policy expectations
# moved today — a DESCRIPTIVE same-day reading, not a forecast. It is a
# renormalized blend of three sub-components; only the *active* ones contribute on
# a given day (inactive ones drop out so the score is never diluted by a stale 0):
#
#   1. Treasury (50%, active every trading day) — the 13-week T-Bill yield (^IRX)
#      is the front of the curve: almost pure policy-rate expectation, near-zero
#      term premium, so it overlaps least with the 10Y used in market_component's
#      `rates` sub-signal. Its DAILY MOVE in basis points is the signal (a falling
#      front-end yield = easier policy priced = dovish/supportive = +). Scaling on
#      the bp move (not the % change of the level) keeps it level-independent.
#      (A 2Y would be the obvious proxy, but Yahoo has no reliable free 2Y feed —
#      the old 2YY=F future froze for days and faked a flat signal; ^IRX is live.)
#   2. Communications (25%, active only when fresh Fed text is present) — on FOMC
#      days/speeches/minutes (~8/yr) we score the text with the news engine
#      (FinBERT when available); the other ~245 days it's inactive.
#   3. Inflation surprise (25%, permanently INACTIVE) — (actual − consensus) CPI/
#      PCE; the consensus feed is proprietary with no free source, so this is a
#      wired-but-dormant hook (flip INFLATION_ENABLED once a feed exists).
#
# Why the front-end yield lives here but the 10Y lives in market_component: the
# front-end bill tracks the expected Fed-funds PATH (pure policy expectations →
# Fed score); the 10Y tracks long-run growth + inflation + term premium (broad
# financial conditions → market score). Correlated via the curve, but distinct
# information, so each is used exactly once in the place it belongs.

# The rates key (and Yahoo ticker, in market_summary.RATES) for the front-end
# Treasury yield used as the Fed-policy-path proxy. One name, used everywhere.
FED_RATE_KEY = "13-Week T-Bill Yield"

# Sub-component weights within the Fed score (renormalized over active components).
TREASURY_WEIGHT = 0.50
COMMS_WEIGHT = 0.25
INFLATION_WEIGHT = 0.25

# A ±15bp move in the front-end yield is treated as a full ±1 Treasury signal.
# (~a sizeable front-end repricing day; ordinary daily noise is 1–5bp.)
FED_FULL_SCALE_BP = 15.0

# Flip to True only once a free consensus-forecast source is wired in (see above).
INFLATION_ENABLED = False


def _change_of(section: dict, name: str):
    """Absolute level change for one instrument, or None if missing/errored.

    For a yield ticker (e.g. ^IRX) this is the change in the yield itself, in
    percentage points — multiply by 100 for basis points.
    """
    q = (section or {}).get(name, {})
    return q.get("change") if isinstance(q, dict) and "error" not in q else None


def treasury_component(market_data: dict) -> dict:
    """Front-end Treasury yield move (basis points) → [-1, 1].

    Falling yield = market pricing easier policy = dovish/supportive (+).
    """
    change = _change_of(market_data.get("rates", {}), FED_RATE_KEY)
    if change is None:
        return {"active": False, "score": 0.0, "move_bp": None, "name": FED_RATE_KEY}
    move_bp = round(change * 100, 1)             # yield change (pp) → basis points
    score = _clamp(-move_bp / FED_FULL_SCALE_BP)  # yield down = easier policy = +
    return {"active": True, "score": round(score, 4), "move_bp": move_bp,
            "name": FED_RATE_KEY}


def communications_component(fed_titles: list) -> dict:
    """FinBERT tone of fresh Fed text → [-1, 1]. Inactive when no text present.

    Active only on days the Fed feed carries text (FOMC statements, minutes,
    speeches — roughly 8 events a year). Scored with the news engine (FinBERT
    when available, else VADER), so dovish language reads positive.
    """
    titles = [t for t in (fed_titles or []) if t]
    if not titles:
        return {"active": False, "score": 0.0, "n": 0, "engine": None}
    engine = news_engine()
    scores = [score_text(t, engine) for t in titles]
    score = _clamp(sum(scores) / len(scores))
    return {"active": True, "score": round(score, 4), "n": len(titles), "engine": engine}


def inflation_component() -> dict:
    """Inflation-surprise hook — permanently inactive without consensus data.

    Returns a fixed inactive result. Wire a (actual − consensus) feed in here and
    set INFLATION_ENABLED = True to switch it on; until then it never contributes.
    """
    return {
        "active": bool(INFLATION_ENABLED),
        "score": 0.0,
        "note": "inactive — no free consensus-forecast feed",
    }


def fed_label(score: float) -> str:
    if score > 0.3:
        return "Dovish"
    if score >= 0.1:
        return "Leaning dovish"
    if score > -0.1:
        return "Neutral"
    if score >= -0.3:
        return "Leaning hawkish"
    return "Hawkish"


def fed_expectations_score(market_data: dict, fed_titles: list = None) -> dict:
    """Blend the three Fed sub-components, renormalized over whichever are active.

    Returns:
        {
          "score": float in [-1, 1],        # the Fed Expectations Score
          "label": str,                     # Dovish … Hawkish
          "detail": {
            "treasury": {...}, "communications": {...}, "inflation": {...},
            "active_components": [str], "weights_used": {component: renorm weight},
            "explanation": str,
          },
        }
    """
    treasury = treasury_component(market_data)
    comms = communications_component(fed_titles)
    inflation = inflation_component()

    # (name, full weight, component-dict) for every component that is active today.
    candidates = [
        ("treasury", TREASURY_WEIGHT, treasury),
        ("communications", COMMS_WEIGHT, comms),
        ("inflation", INFLATION_WEIGHT, inflation),
    ]
    active = [(name, w, c) for name, w, c in candidates if c.get("active")]

    total_w = sum(w for _, w, _ in active)
    if total_w:
        score = _clamp(sum(w * c["score"] for _, w, c in active) / total_w)
        weights_used = {name: round(w / total_w, 4) for name, w, _ in active}
    else:
        score = 0.0
        weights_used = {}
    score = round(score, 4)
    label = fed_label(score)

    explanation = _fed_explain(label, treasury, comms, inflation, weights_used)

    return {
        "score": score,
        "label": label,
        "detail": {
            "treasury": treasury,
            "communications": comms,
            "inflation": inflation,
            "active_components": [name for name, _, _ in active],
            "weights_used": weights_used,
            "explanation": explanation,
        },
    }


def _fed_explain(label, treasury, comms, inflation, weights_used) -> str:
    parts = [f"Fed Expectations: {label}."]
    if treasury.get("active") and treasury.get("move_bp") is not None:
        move_bp = treasury["move_bp"]
        # Deadband: a sub-1bp move is rounding noise, not a real shift.
        dir_word = ("fell" if move_bp <= -0.5 else
                    "rose" if move_bp >= 0.5 else "was flat")
        name = treasury.get("name", FED_RATE_KEY)
        amount = f" {move_bp:+.0f}bp" if abs(move_bp) >= 0.5 else ""
        parts.append(f"{name} {dir_word}{amount} "
                     f"(weight {weights_used.get('treasury', 0):.0%}).")
    if comms.get("active"):
        parts.append(f"Fed communications ({comms['n']} item"
                     f"{'s' if comms['n'] != 1 else ''}, {comms.get('engine')}) "
                     f"scored {comms['score']:+.2f} "
                     f"(weight {weights_used.get('communications', 0):.0%}).")
    else:
        parts.append("No fresh Fed text today — communications component inactive.")
    if not inflation.get("active"):
        parts.append("Inflation-surprise component inactive (no free consensus data).")
    return " ".join(parts)


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
    sec = d.get("sectors")
    if sec and sec.get("total"):
        parts.append(f"Sector breadth: {sec['up']}/{sec['total']} sectors up.")
    if d.get("rate_10y_pct") is not None:
        parts.append(f"10Y yield {d['rate_10y_pct']:+.1f}%.")
    parts.append(f"News headlines read {label_for(news['score']).lower()}.")
    # Only mention Reddit when it actually counts — it's weighted 0 for now.
    if WEIGHTS.get("reddit", 0) > 0:
        parts.append(f"Reddit sentiment is {label_for(reddit['score']).lower()}.")
    treasury = fed["detail"].get("treasury", {})
    move_bp = treasury.get("move_bp")
    if treasury.get("active") and move_bp is not None:
        # Describe the front-end yield move itself (with a deadband for rounding
        # noise) rather than attributing the whole Fed score — which may be
        # driven by communications — to the Treasury leg.
        tone = ("eased" if move_bp <= -0.5 else
                "tightened" if move_bp >= 0.5 else "held steady")
        name = treasury.get("name", "front-end yields")
        amount = f" ({name} {move_bp:+.0f}bp)" if abs(move_bp) >= 0.5 else ""
        parts.append(f"Fed-policy expectations {tone}{amount}.")
    comms = fed["detail"].get("communications", {})
    if comms.get("active"):
        parts.append(f"Fresh Fed communications read {label_for(comms['score']).lower()}.")
    parts.append(f"Today's overall market tone: {overall_label}.")
    return " ".join(parts)


# A headline must be at least this long to be eligible as a "standout" — short
# fragments score erratically and read as noise when surfaced on their own.
_MIN_STANDOUT_LEN = 30


def _extreme_headlines(news_titles: list, reddit_titles: list) -> dict:
    """Most bullish and most bearish single headline by polarity, or {}.

    Scores each headline with the engine appropriate to its source. Prefers
    substantive (non-fragment) headlines for the standout picks, falling back to
    the full set only if nothing clears the length bar — so the feature surfaces
    a real headline rather than a stray fragment.
    """
    eng = news_engine()
    scored = [(t, score_text(t, eng)) for t in news_titles if t]
    scored += [(t, score_text(t, "vader")) for t in reddit_titles if t]
    if not scored:
        return {}
    eligible = [s for s in scored if len(s[0]) >= _MIN_STANDOUT_LEN] or scored
    bull = max(eligible, key=lambda x: x[1])
    bear = min(eligible, key=lambda x: x[1])
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


def build_dashboard(market_data: dict, headlines: dict, run_date: str = None,
                    prior_scores: list = None) -> dict:
    """Compute the full Joywin-style sentiment dashboard as a dict.

    prior_scores: recent *raw* composite scores in [-1, 1], oldest-first and
    EXCLUDING today. When given, an EMA trend (smoothed_score / smoothed_label)
    is computed over prior_scores + today's score and added to the dashboard —
    the readable day-over-day signal over the jumpy daily number. Omit it and the
    smoothed fields mirror the raw score (no history available yet).
    """
    if run_date is None:
        run_date = datetime.date.today().isoformat()

    import news_feeds
    news_titles, reddit_titles, fed_titles = news_feeds.split_headlines(headlines)

    market = market_component(market_data)
    news = news_component(news_titles)
    reddit = reddit_component(reddit_titles)
    fed = fed_component(market_data, fed_titles)   # Fed Expectations Score

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

    # EMA trend over the recent raw composite (prior days + today). Falls back to
    # the raw score when no history is supplied yet.
    smoothed = round(ema(list(prior_scores or []) + [overall]), 4)
    smoothed_label = label_for(smoothed)

    return {
        "date": run_date,
        "overall_score": overall,
        "label": label,
        "smoothed_score": smoothed,
        "smoothed_label": smoothed_label,
        "smoothing_span": SMOOTHING_SPAN,
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
        "## Market Tone — Today's Session",
        "",
        "_A recap of how the market traded today — not a forecast._",
        "",
        f"**Today's Score:** {dash['overall_score']:+.2f}  →  **{dash['label']}**",
    ]
    if dash.get("smoothed_score") is not None:
        span = dash.get("smoothing_span", SMOOTHING_SPAN)
        lines.append(
            f"**Trend ({span}-day EMA):** {dash['smoothed_score']:+.2f}  →  "
            f"**{dash['smoothed_label']}**  "
            f"<sub>(smoothed — the readable day-over-day signal)</sub>")
    lines += [
        "",
        "| Component | Weight | Score |",
        "| --- | --- | --- |",
        f"| Market data | {dash['weights']['market']:.0%} | {dash['market_score']:+.2f} |",
        f"| News headlines | {dash['weights']['news']:.0%} | {dash['news_score']:+.2f} |",
        f"| Reddit | {dash['weights']['reddit']:.0%} | {dash['reddit_score']:+.2f} |",
        f"| Fed (rate expectations) | {dash['weights']['fed']:.0%} | {dash['fed_score']:+.2f} |",
        "",
        f"_{dash['summary_text']}_",
    ]

    news_engine = (dash.get("components", {}).get("news", {}) or {}).get("engine")
    if news_engine:
        lines += ["", f"<sub>News scored with: {news_engine}</sub>"]

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
    import news_feeds

    data = ms.fetch_all_data()
    heads = news_feeds.gather_headlines(limit=8)
    dash = build_dashboard(data, heads)
    print(json.dumps(dash, indent=2))
    print("\n" + render_dashboard_md(dash))
