"""
database.py
-----------
Lightweight SQLite storage for the market summary bot, so price data and
sentiment can be tracked over time.

Two tables:
    quotes     - one row per (date, instrument): price, change, pct_change
    summaries  - one row per date: parsed sentiment, confidence, full summary

Used by market_summary.py to record each run. Can also be run directly to
print a trend report:

    python database.py                # recent sentiment + S&P 500 history
    python database.py "Nvidia"       # price history for a specific instrument
"""

import os
import re
import sys
import sqlite3
import datetime

# Reconfigure stdout to UTF-8 so symbols like ▲/▼ don't crash on Windows (cp1252)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Store the DB next to this file so the path is stable regardless of cwd.
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "market_data.db")


# ── Schema ───────────────────────────────────────────────────────────────────

def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the tables if they don't already exist."""
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS quotes (
                run_date   TEXT NOT NULL,
                section    TEXT NOT NULL,
                name       TEXT NOT NULL,
                price      REAL,
                change     REAL,
                pct_change REAL,
                PRIMARY KEY (run_date, name)
            );

            CREATE TABLE IF NOT EXISTS summaries (
                run_date   TEXT PRIMARY KEY,
                sentiment  TEXT,
                confidence TEXT,
                summary    TEXT
            );
            """
        )


# ── Writing ──────────────────────────────────────────────────────────────────

def parse_sentiment(summary: str) -> tuple:
    """Best-effort extraction of (sentiment, confidence) from the summary text.

    Looks for a line like: **Overall Sentiment:** Neutral (Confidence: Moderate)
    Returns (None, None) if not found.
    """
    match = re.search(
        r"Overall Sentiment:\**\s*([^(\n]+?)\s*\(Confidence:\s*([^)]+)\)",
        summary,
        re.IGNORECASE,
    )
    if not match:
        return None, None
    return match.group(1).strip(" *"), match.group(2).strip(" *")


def save_run(market_data: dict, summary: str, run_date: str = None) -> None:
    """Persist one run's quotes and summary. Re-running the same day overwrites."""
    init_db()
    if run_date is None:
        run_date = datetime.date.today().isoformat()

    sentiment, confidence = parse_sentiment(summary)

    with connect() as conn:
        for section, instruments in market_data.items():
            for name, q in instruments.items():
                if "error" in q:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO quotes "
                    "(run_date, section, name, price, change, pct_change) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (run_date, section, name,
                     q.get("price"), q.get("change"), q.get("pct_change")),
                )
        conn.execute(
            "INSERT OR REPLACE INTO summaries "
            "(run_date, sentiment, confidence, summary) VALUES (?, ?, ?, ?)",
            (run_date, sentiment, confidence, summary),
        )


# ── Backfill (real historical prices, for testing trends) ──────────────────────

def backfill_prices(days: int = 10) -> int:
    """Populate the quotes table with real historical closes from Yahoo Finance.

    Lets you see the trend report with genuine multi-day data without waiting.
    Returns the number of (day, instrument) rows written.
    """
    import yfinance as yf
    import market_summary as ms

    init_db()
    sections = {
        "indices":     ms.INDICES,
        "stocks":      ms.STOCKS,
        "commodities": ms.COMMODITIES,
        "fx":          ms.FX,
    }

    written = 0
    with connect() as conn:
        for section, tickers in sections.items():
            for name, symbol in tickers.items():
                print(f"  Backfilling {name} ({symbol})…")
                hist = yf.Ticker(symbol).history(period="1mo")
                closes = hist["Close"].dropna()
                # Walk the most recent `days` sessions; need a prior close for change.
                recent = closes.tail(days + 1)
                prev = None
                for ts, close in recent.items():
                    if prev is not None:
                        change = float(close) - float(prev)
                        pct = (change / float(prev) * 100) if prev else 0.0
                        conn.execute(
                            "INSERT OR REPLACE INTO quotes "
                            "(run_date, section, name, price, change, pct_change) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (ts.date().isoformat(), section, name,
                             round(float(close), 2),
                             round(change, 2) or 0.0,
                             round(pct, 2) or 0.0),
                        )
                        written += 1
                    prev = close
    return written


# ── Reading / trend report ─────────────────────────────────────────────────────

def sentiment_history(limit: int = 14) -> list:
    with connect() as conn:
        rows = conn.execute(
            "SELECT run_date, sentiment, confidence FROM summaries "
            "ORDER BY run_date DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return list(reversed(rows))


def price_history(name: str, limit: int = 14) -> list:
    with connect() as conn:
        rows = conn.execute(
            "SELECT run_date, price, pct_change FROM quotes "
            "WHERE name = ? ORDER BY run_date DESC LIMIT ?",
            (name, limit),
        ).fetchall()
    return list(reversed(rows))


def print_report(name: str = "S&P 500") -> None:
    if not os.path.exists(DB_PATH):
        print("No database yet — run market_summary.py at least once first.")
        return

    print("Sentiment history")
    print("─" * 50)
    rows = sentiment_history()
    if not rows:
        print("  (no summaries recorded yet)")
    for r in rows:
        sentiment = r["sentiment"] or "—"
        conf = f" ({r['confidence']})" if r["confidence"] else ""
        print(f"  {r['run_date']}  {sentiment}{conf}")

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
        print(f"\n✅ Backfilled {n} historical price rows into {os.path.basename(DB_PATH)}\n")
        print_report()
    else:
        target = args[0] if args else "S&P 500"
        print_report(target)
