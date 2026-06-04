"""
validate_sentiment.py
----------------------
Backtests the sentiment score against real index moves, to answer: does the
score actually track / predict the market, or is it just plausible?

Important honesty caveat: only the MARKET component (70% of the composite) can
be reconstructed historically — news / Reddit / Fed headlines are not archived,
so the full composite can't be backfilled. This therefore validates the market
component, which is the dominant and only history-reconstructable piece.

It reports, over the available price history:
  • Same-day correlation  corr(score_t, return_t)   — sanity check (should be
    high, since the market score is built from same-day moves).
  • Next-day correlation   corr(score_t, return_t+1) — the real predictive test.
  • Directional hit-rate: how often sign(score_t) matches next-day direction.

    python validate_sentiment.py
"""

import math

import history
import sentiment
from utils import force_utf8

force_utf8()

INDEX = "S&P 500"  # the index whose forward return we test against


def _pearson(xs: list, ys: list) -> float:
    n = len(xs)
    if n < 2:
        return float("nan")
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    vy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return cov / (vx * vy) if vx and vy else float("nan")


def _pct_by_date(name: str) -> dict:
    """{run_date: pct_change} for one instrument, across all stored history."""
    rows = history.price_history(name, limit=10_000)
    return {r["run_date"]: r["pct_change"] for r in rows if r["pct_change"] is not None}


def main() -> None:
    sp = _pct_by_date("S&P 500")
    nq = _pct_by_date("Nasdaq 100")
    vix = _pct_by_date("VIX")

    # Dates where we have all three index moves, in chronological order.
    dates = sorted(d for d in sp if d in nq and d in vix)
    if len(dates) < 30:
        print(f"Only {len(dates)} usable days — run the backfill first "
              f"(python -c \"import history; history.backfill_prices()\").")
        return

    # Reconstruct the market-component score for each day.
    scores = {}
    for d in dates:
        md = {"indices": {"S&P 500": {"pct_change": sp[d]},
                          "Nasdaq 100": {"pct_change": nq[d]},
                          "VIX": {"pct_change": vix[d]}}}
        scores[d] = sentiment.market_component(md)["score"]

    # Pair score_t with same-day and next-day S&P returns.
    same_score, same_ret = [], []
    next_score, next_ret = [], []
    hits = 0
    for i, d in enumerate(dates):
        same_score.append(scores[d])
        same_ret.append(sp[d])
        if i + 1 < len(dates):
            nd = dates[i + 1]
            next_score.append(scores[d])
            next_ret.append(sp[nd])
            # Directional hit: does a positive/negative score precede an up/down day?
            if (scores[d] >= 0) == (sp[nd] >= 0):
                hits += 1

    same_r = _pearson(same_score, same_ret)
    next_r = _pearson(next_score, next_ret)
    hit_rate = hits / len(next_score) * 100 if next_score else float("nan")
    # Baseline: how often the index simply rose (so we can judge the hit-rate).
    up_days = sum(1 for d in dates if sp[d] >= 0) / len(dates) * 100

    print(f"Validation over {len(dates)} trading days "
          f"({dates[0]} → {dates[-1]})")
    print("─" * 64)
    print("NOTE: market component only (70% of composite); news/Reddit/Fed")
    print("      history isn't archived and can't be backfilled.")
    print()
    print(f"  Same-day corr(score_t, return_t)    : {same_r:+.3f}   "
          f"(sanity check — expected high)")
    print(f"  Next-day corr(score_t, return_t+1)  : {next_r:+.3f}   "
          f"(the real predictive test)")
    print(f"  Next-day directional hit-rate       : {hit_rate:.1f}%   "
          f"(vs {up_days:.1f}% up-day base rate)")
    print()
    if abs(next_r) < 0.1:
        print("Verdict: little/no next-day predictive power — the score is")
        print("DESCRIPTIVE (summarizes today's mood), not a forecasting signal.")
    else:
        print("Verdict: some next-day signal detected — worth investigating further.")


if __name__ == "__main__":
    main()
