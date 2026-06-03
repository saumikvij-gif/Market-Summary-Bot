"""
fed.py
------
The **Fed Expectations Score** — a market-driven read of where monetary-policy
expectations moved today. It is blended into the daily sentiment composite at a
10% weight (see sentiment.WEIGHTS) and is, like every other component, a
DESCRIPTIVE same-day reading, not a forecast.

The score is a renormalized blend of three components. On any given day only the
*active* components contribute; inactive ones drop out and the active weights are
renormalized to sum to 1, so the score is never diluted by a stale 0.

  1. Treasury  (50% — ACTIVE every trading day)
     The 2-Year Treasury yield is the market's cleanest proxy for the expected
     path of the Fed funds rate over the next ~2 years. Its DAILY MOVE is the
     signal: a falling 2Y = the market pricing *easier* policy = supportive for
     equities (positive / dovish); a rising 2Y = tighter policy (negative /
     hawkish). A ±FED_FULL_SCALE_PCT move in the yield maps to a full ±1.

  2. Communications  (25% — ACTIVE only when fresh Fed text is present)
     On FOMC decision days (~8/yr), plus speeches and minutes, the Fed publishes
     formal text. When our Fed feed carries such text we score it with FinBERT
     (finance-aware), reading hawkish/dovish tone directly. On the ~245 other
     trading days there is no fresh Fed text, so this component is INACTIVE and
     drops out of the blend entirely (rather than injecting a meaningless 0).

  3. Inflation surprise  (25% — INACTIVE: documented hook, never contributes)
     The intended signal is (actual CPI/PCE − consensus forecast): a downside
     inflation surprise is dovish/supportive, an upside surprise hawkish. The
     CONSENSUS forecast is the hard part — those survey numbers (Bloomberg /
     Reuters / Investing.com) are proprietary with no free, reliable feed, so
     this component ships permanently INACTIVE. The hook is wired here so it can
     be switched on the day a data source is added; until then it contributes
     nothing and its 25% is renormalized away.

Why the 2Y lives here but the 10Y lives in the market component (no double-count):
  The 2Y tracks the expected Fed-funds PATH — that is a pure policy-expectations
  read, so it belongs to the Fed score. The 10Y tracks long-run growth +
  inflation + term premium — a broad financial-conditions read, which belongs to
  the market component's `rates` sub-signal. The two are correlated through the
  yield curve, but they carry different information, so each is used exactly once
  in the place it belongs. That is the deliberate Fed↔market connection: Fed
  expectations (2Y) feed the Fed score, while their spillover into broad
  conditions (10Y) feeds the market score.
"""

# Component weights within the Fed score (renormalized over active components).
TREASURY_WEIGHT = 0.50
COMMS_WEIGHT = 0.25
INFLATION_WEIGHT = 0.25

# A ±3% move in the 2-Year yield is treated as a full ±1 Treasury signal.
FED_FULL_SCALE_PCT = 3.0

# Flip to True only once a free consensus-forecast source is wired in (see above).
INFLATION_ENABLED = False


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _pct_of(section: dict, name: str):
    """pct_change for one instrument in a section, or None if missing/errored."""
    q = (section or {}).get(name, {})
    return q.get("pct_change") if isinstance(q, dict) and "error" not in q else None


# ── Components ───────────────────────────────────────────────────────────────────

def treasury_component(market_data: dict) -> dict:
    """2-Year Treasury yield move → [-1, 1]. Falling yield = dovish = positive."""
    rate = _pct_of(market_data.get("rates", {}), "2Y Treasury Yield")
    if rate is None:
        return {"active": False, "score": 0.0, "rate_pct": None}
    score = _clamp(-rate / FED_FULL_SCALE_PCT)   # yield down = easier policy = +
    return {"active": True, "score": round(score, 4), "rate_pct": rate}


def communications_component(fed_titles: list) -> dict:
    """FinBERT tone of fresh Fed text → [-1, 1]. Inactive when no text present.

    Active only on days the Fed feed carries text (FOMC statements, minutes,
    speeches — roughly 8 events a year). Scored with the news engine (FinBERT
    when available, else VADER), so dovish language reads positive.
    """
    titles = [t for t in (fed_titles or []) if t]
    if not titles:
        return {"active": False, "score": 0.0, "n": 0, "engine": None}
    # Lazy import avoids a circular dependency (sentiment imports fed).
    import sentiment
    engine = sentiment.news_engine()
    scores = [sentiment.score_text(t, engine) for t in titles]
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


# ── Blend ────────────────────────────────────────────────────────────────────────

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
    """Blend the three components, renormalized over whichever are active today.

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

    explanation = _explain(label, treasury, comms, inflation, weights_used)

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


def _explain(label, treasury, comms, inflation, weights_used) -> str:
    parts = [f"Fed Expectations: {label}."]
    if treasury.get("active") and treasury.get("rate_pct") is not None:
        dir_word = ("fell" if treasury["rate_pct"] < 0 else
                    "rose" if treasury["rate_pct"] > 0 else "was flat")
        parts.append(f"2Y Treasury {dir_word} {treasury['rate_pct']:+.2f}% "
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


if __name__ == "__main__":
    import json
    import market_summary as ms
    import reddit_news
    import sentiment  # noqa: F401  (ensures the scoring engine is importable)

    data = ms.fetch_all_data()
    heads = reddit_news.gather_headlines(limit=8)
    _, _, fed_titles = sentiment._split_headlines(heads)
    print(json.dumps(fed_expectations_score(data, fed_titles), indent=2))
