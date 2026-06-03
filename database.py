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
import csv
import sqlite3
import datetime

# Reconfigure stdout to UTF-8 so symbols like ▲/▼ don't crash on Windows (cp1252)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_HERE = os.path.dirname(os.path.abspath(__file__))
# The SQLite DB is a LOCAL CACHE (binary, gitignored). The committed source of
# truth is the pair of CSV files below — text, so Git can merge them cleanly and
# the daily workflow never hits a binary merge conflict.
DB_PATH = os.path.join(_HERE, "market_data.db")
QUOTES_CSV = os.path.join(_HERE, "history_quotes.csv")
SUMMARIES_CSV = os.path.join(_HERE, "history_summaries.csv")

QUOTE_COLS = ["run_date", "section", "name", "price", "change", "pct_change"]
SUMMARY_COLS = ["run_date", "sentiment", "confidence", "score", "summary"]


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
                score      INTEGER,
                summary    TEXT
            );
            """
        )
        # Add the score column to pre-existing databases that lack it.
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(summaries)")]
        if "score" not in cols:
            conn.execute("ALTER TABLE summaries ADD COLUMN score INTEGER")


# ── CSV source-of-truth (text, git-mergeable) ──────────────────────────────────

def export_csv() -> None:
    """Dump both tables to CSV — the committed, git-mergeable source of truth."""
    with connect() as conn:
        q = conn.execute(
            f"SELECT {', '.join(QUOTE_COLS)} FROM quotes ORDER BY run_date, name"
        ).fetchall()
        s = conn.execute(
            f"SELECT {', '.join(SUMMARY_COLS)} FROM summaries ORDER BY run_date"
        ).fetchall()
    for path, cols, rows in [(QUOTES_CSV, QUOTE_COLS, q),
                             (SUMMARIES_CSV, SUMMARY_COLS, s)]:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(cols)
            w.writerows([tuple(r[c] for c in cols) for r in rows])


def ensure_loaded() -> None:
    """If the local DB cache is empty but CSVs exist, rebuild the DB from them.

    Lets the (gitignored) .db be reconstructed from the committed CSVs — e.g. on
    a fresh CI runner — so the DB never needs to be committed.
    """
    init_db()
    with connect() as conn:
        has_rows = conn.execute("SELECT 1 FROM quotes LIMIT 1").fetchone()
        if has_rows or not os.path.exists(QUOTES_CSV):
            return
        for path, table, cols in [(QUOTES_CSV, "quotes", QUOTE_COLS),
                                   (SUMMARIES_CSV, "summaries", SUMMARY_COLS)]:
            if not os.path.exists(path):
                continue
            with open(path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                placeholders = ", ".join("?" * len(cols))
                conn.executemany(
                    f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) "
                    f"VALUES ({placeholders})",
                    [tuple(row[c] or None for c in cols) for row in reader],
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


def save_run(market_data: dict, summary: str, run_date: str = None,
             sentiment: str = None, confidence: str = None,
             score: int = None) -> None:
    """Persist one run's quotes and summary. Re-running the same day overwrites.

    If sentiment/confidence aren't supplied (structured), fall back to scraping
    them from the summary text.
    """
    ensure_loaded()  # rebuild the DB cache from CSV first if needed
    if run_date is None:
        run_date = datetime.date.today().isoformat()

    if sentiment is None and confidence is None:
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
            "(run_date, sentiment, confidence, score, summary) VALUES (?, ?, ?, ?, ?)",
            (run_date, sentiment, confidence, score, summary),
        )

    # Re-export the CSV source of truth so the committed history stays current.
    export_csv()


# ── Backfill (real historical prices, for testing trends) ──────────────────────

def backfill_prices(period: str = "1y", days: int = None) -> int:
    """Populate the quotes table with real historical closes from Yahoo Finance.

    Pulls `period` of history (default 1 year) so the trend charts are useful
    immediately instead of building up over days. `days` optionally caps how
    many of the most recent sessions to keep (None = all in the period).
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
                hist = yf.Ticker(symbol).history(period=period)
                closes = hist["Close"].dropna()
                # Keep all sessions (need one extra prior close for the change calc).
                recent = closes if days is None else closes.tail(days + 1)
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
    export_csv()  # keep the committed source of truth in sync
    return written


# ── Reading / trend report ─────────────────────────────────────────────────────

def sentiment_history(limit: int = 14) -> list:
    ensure_loaded()
    with connect() as conn:
        rows = conn.execute(
            "SELECT run_date, sentiment, confidence, score FROM summaries "
            "ORDER BY run_date DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return list(reversed(rows))


def price_history(name: str, limit: int = 14) -> list:
    ensure_loaded()
    with connect() as conn:
        rows = conn.execute(
            "SELECT run_date, price, pct_change FROM quotes "
            "WHERE name = ? ORDER BY run_date DESC LIMIT ?",
            (name, limit),
        ).fetchall()
    return list(reversed(rows))


def print_report(name: str = "S&P 500") -> None:
    if not (os.path.exists(DB_PATH) or os.path.exists(QUOTES_CSV)):
        print("No history yet — run market_summary.py at least once first.")
        return
    ensure_loaded()

    print("Sentiment history")
    print("─" * 50)
    rows = sentiment_history()
    if not rows:
        print("  (no summaries recorded yet)")
    for r in rows:
        sentiment = r["sentiment"] or "—"
        conf = f" ({r['confidence']})" if r["confidence"] else ""
        score = f"  [{r['score']:+d}]" if r["score"] is not None else ""
        print(f"  {r['run_date']}  {sentiment}{conf}{score}")

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
