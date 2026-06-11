"""
history.py
----------
CSV-backed history for the market summary bot — no SQLite. The dataset is tiny
(~250 days x ~18 instruments), so two committed CSV files are the single source
of truth and all querying is done in memory.

    history_quotes.csv     - one row per (date, instrument): price, change, pct_change
    history_summaries.csv  - one row per date: sentiment, score, summary

Used by market_summary.py to record each run. Can also be run directly to print
a trend report:

    python history.py                # recent sentiment + S&P 500 history
    python history.py "Nvidia"       # price history for a specific instrument
"""

import os
import sys
import csv
import datetime

from utils import force_utf8

force_utf8()

_HERE = os.path.dirname(os.path.abspath(__file__))
QUOTES_CSV = os.path.join(_HERE, "history_quotes.csv")
SUMMARIES_CSV = os.path.join(_HERE, "history_summaries.csv")
# Internal-only sector-watch snapshot: one row per (date, sector) capturing the
# day's sub-signals + blended score. NOT part of the briefing/email — it's a
# private accumulator (committed alongside the other history CSVs so it survives
# the ephemeral Action runner) that lets us later fit the metric weights and the
# calibration exponent against real forward returns instead of one hand-labelled
# day. Forward returns aren't stored: they're recomputed from Yahoo when we fit.
SECTOR_CSV = os.path.join(_HERE, "sector_history.csv")

QUOTE_COLS = ["run_date", "section", "name", "price", "change", "pct_change"]
SUMMARY_COLS = ["run_date", "sentiment", "score", "summary"]
SECTOR_COLS = ["run_date", "sector", "score", "label", "move_pct", "rel_strength",
               "breadth_pct", "news_score", "momentum_pct", "reddit_score",
               "constituents", "benchmark"]


# ── CSV read/write helpers ─────────────────────────────────────────────────────

def _read(path: str) -> list:
    """Return the CSV rows as a list of dicts (string values), or [] if absent."""
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write(path: str, cols: list, rows: list) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow(["" if r.get(c) is None else r.get(c) for c in cols])


def _f(x):
    """Parse a CSV cell to float, or None."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _i(x):
    """Parse a CSV cell to int, or None."""
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


# ── Writing ──────────────────────────────────────────────────────────────────

def save_run(market_data: dict, summary: str, run_date: str = None,
             sentiment: str = None, score: int = None) -> None:
    """Persist one run's quotes and summary. Re-running a date overwrites it."""
    if run_date is None:
        run_date = datetime.date.today().isoformat()

    # Upsert quotes, keyed by (run_date, name).
    quotes = {(r["run_date"], r["name"]): r for r in _read(QUOTES_CSV)}
    for section, instruments in market_data.items():
        for name, q in instruments.items():
            if not isinstance(q, dict) or "error" in q:
                continue
            quotes[(run_date, name)] = {
                "run_date": run_date, "section": section, "name": name,
                "price": q.get("price"), "change": q.get("change"),
                "pct_change": q.get("pct_change"),
            }
    _write(QUOTES_CSV, QUOTE_COLS,
           sorted(quotes.values(), key=lambda r: (r["run_date"], r["name"])))

    # Upsert the summary, keyed by run_date.
    summaries = {r["run_date"]: r for r in _read(SUMMARIES_CSV)}
    summaries[run_date] = {"run_date": run_date, "sentiment": sentiment,
                           "score": score, "summary": summary}
    _write(SUMMARIES_CSV, SUMMARY_COLS,
           sorted(summaries.values(), key=lambda r: r["run_date"]))


def save_sector_watch(rows: list, run_date: str = None) -> None:
    """Persist one run's Sector-Watch snapshot (internal only; never emailed).

    Upserts one row per (run_date, sector) so re-running a date overwrites it,
    matching save_run's quotes behaviour. `rows` is build_sector_watch's output.
    """
    if not rows:
        return
    if run_date is None:
        run_date = datetime.date.today().isoformat()
    snap = {(r["run_date"], r["sector"]): r for r in _read(SECTOR_CSV)}
    for r in rows:
        snap[(run_date, r["sector"])] = {
            "run_date": run_date, "sector": r.get("sector"),
            "score": r.get("score"), "label": r.get("label"),
            "move_pct": r.get("move_pct"), "rel_strength": r.get("rel_strength"),
            "breadth_pct": r.get("breadth_pct"), "news_score": r.get("news_score"),
            "momentum_pct": r.get("momentum_pct"), "reddit_score": r.get("reddit_score"),
            "constituents": r.get("constituents"), "benchmark": r.get("benchmark"),
        }
    _write(SECTOR_CSV, SECTOR_COLS,
           sorted(snap.values(), key=lambda r: (r["run_date"], r["sector"])))


# ── Backfill (real historical prices, for testing trends) ──────────────────────

def backfill_prices(period: str = "1y", days: int = None) -> int:
    """Populate history_quotes.csv with real historical closes from Yahoo Finance.

    Pulls `period` of history (default 1 year) so the trend charts are useful
    immediately. `days` optionally caps the most recent sessions kept.
    Returns the number of (day, instrument) rows written.
    """
    import yfinance as yf
    import market_summary as ms

    sections = {"indices": ms.INDICES, "stocks": ms.STOCKS,
                "commodities": ms.COMMODITIES, "fx": ms.FX}

    quotes = {(r["run_date"], r["name"]): r for r in _read(QUOTES_CSV)}
    written = 0
    for section, tickers in sections.items():
        for name, symbol in tickers.items():
            print(f"  Backfilling {name} ({symbol})…")
            hist = yf.Ticker(symbol).history(period=period)
            closes = hist["Close"].dropna()
            recent = closes if days is None else closes.tail(days + 1)
            prev = None
            for ts, close in recent.items():
                if prev is not None:
                    change = float(close) - float(prev)
                    pct = (change / float(prev) * 100) if prev else 0.0
                    d = ts.date().isoformat()
                    quotes[(d, name)] = {
                        "run_date": d, "section": section, "name": name,
                        "price": round(float(close), 2),
                        "change": round(change, 2) or 0.0,
                        "pct_change": round(pct, 2) or 0.0,
                    }
                    written += 1
                prev = close
    _write(QUOTES_CSV, QUOTE_COLS,
           sorted(quotes.values(), key=lambda r: (r["run_date"], r["name"])))
    return written


# ── Reading / trend report ─────────────────────────────────────────────────────

def sentiment_history(limit: int = 14) -> list:
    """Most recent `limit` summary rows, oldest-first."""
    rows = sorted(_read(SUMMARIES_CSV), key=lambda r: r["run_date"])[-limit:]
    return [{"run_date": r["run_date"], "sentiment": r["sentiment"] or None,
             "score": _i(r["score"])}
            for r in rows]


def price_history(name: str, limit: int = 14) -> list:
    """Most recent `limit` price rows for one instrument, oldest-first."""
    rows = sorted((r for r in _read(QUOTES_CSV) if r["name"] == name),
                  key=lambda r: r["run_date"])[-limit:]
    return [{"run_date": r["run_date"], "price": _f(r["price"]),
             "pct_change": _f(r["pct_change"])} for r in rows]


def print_report(name: str = "S&P 500") -> None:
    if not os.path.exists(QUOTES_CSV):
        print("No history yet — run market_summary.py at least once first.")
        return

    print("Sentiment history")
    print("─" * 50)
    rows = sentiment_history()
    if not rows:
        print("  (no summaries recorded yet)")
    for r in rows:
        sentiment = r["sentiment"] or "—"
        score = f"  [{r['score']:+d}]" if r["score"] is not None else ""
        print(f"  {r['run_date']}  {sentiment}{score}")

    print(f"\n{name} price history")
    print("─" * 50)
    prices = price_history(name)
    if not prices:
        print(f"  (no data recorded for '{name}')")
    for r in prices:
        pct = r["pct_change"]
        arrow = "▲" if (pct or 0) >= 0 else "▼"
        print(f"  {r['run_date']}  {r['price']:>12,.2f}  {arrow} {pct:+.2f}%")
    print()


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--backfill":
        n = backfill_prices()
        print(f"\n✅ Backfilled {n} rows into {os.path.basename(QUOTES_CSV)}\n")
        print_report()
    else:
        print_report(args[0] if args else "S&P 500")
