"""
charts.py
---------
Generates trend charts (PNG) from the SQLite history database so the data is
glanceable instead of row-by-row. Saves into the charts/ folder.

    python charts.py

Produces:
    charts/sentiment_trend.png  - daily sentiment score over time
    charts/index_trends.png     - normalized price trends for the major indices

Uses a non-interactive matplotlib backend so it works headless (in CI).
"""

import os

import matplotlib
matplotlib.use("Agg")  # headless backend — no display needed
import matplotlib.pyplot as plt

import database

CHARTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "charts")

# Instruments to plot on the index-trends chart.
INDEX_NAMES = ["S&P 500", "Nasdaq 100", "Dow Jones", "Russell 2000"]


def _ensure_dir() -> None:
    os.makedirs(CHARTS_DIR, exist_ok=True)


def chart_sentiment_trend(limit: int = 30) -> str | None:
    """Plot daily sentiment score (-100..100) over time."""
    rows = [r for r in database.sentiment_history(limit) if r["score"] is not None]
    if not rows:
        print("  (no sentiment scores to chart yet)")
        return None

    dates = [r["run_date"] for r in rows]
    scores = [r["score"] for r in rows]

    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.axhline(0, color="#888", linewidth=0.8)
    ax.plot(dates, scores, marker="o", color="#1f77b4", linewidth=2)
    ax.fill_between(dates, scores, 0,
                    where=[s >= 0 for s in scores], color="#2ca02c", alpha=0.15)
    ax.fill_between(dates, scores, 0,
                    where=[s < 0 for s in scores], color="#d62728", alpha=0.15)
    ax.set_ylim(-100, 100)
    ax.set_title("Market Sentiment Score Over Time")
    ax.set_ylabel("Bearish  ←  Score  →  Bullish")
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate(rotation=45)
    fig.tight_layout()

    path = os.path.join(CHARTS_DIR, "sentiment_trend.png")
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def chart_index_trends(limit: int = 30) -> str | None:
    """Plot normalized (=100 at start) price trends for the major indices."""
    fig, ax = plt.subplots(figsize=(10, 4.5))
    plotted = False

    for name in INDEX_NAMES:
        rows = database.price_history(name, limit)
        prices = [r["price"] for r in rows if r["price"] is not None]
        dates = [r["run_date"] for r in rows if r["price"] is not None]
        if len(prices) < 2:
            continue
        base = prices[0]
        normalized = [p / base * 100 for p in prices]  # rebase to 100
        ax.plot(dates, normalized, marker=".", linewidth=1.8, label=name)
        plotted = True

    if not plotted:
        print("  (not enough price history to chart index trends yet)")
        plt.close(fig)
        return None

    ax.axhline(100, color="#888", linewidth=0.8, linestyle="--")
    ax.set_title("Major Indices — Normalized Price Trend (start = 100)")
    ax.set_ylabel("Indexed to 100")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.autofmt_xdate(rotation=45)
    fig.tight_layout()

    path = os.path.join(CHARTS_DIR, "index_trends.png")
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def generate_all() -> list:
    """Generate every chart; return the list of paths actually written."""
    if not os.path.exists(database.DB_PATH):
        print("No database yet — run market_summary.py at least once first.")
        return []
    _ensure_dir()
    paths = [chart_sentiment_trend(), chart_index_trends()]
    written = [p for p in paths if p]
    for p in written:
        print(f"📈 Wrote {os.path.relpath(p)}")
    return written


if __name__ == "__main__":
    generate_all()
