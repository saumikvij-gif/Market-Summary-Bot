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
import datetime

import matplotlib
matplotlib.use("Agg")  # headless backend — no display needed
import matplotlib.pyplot as plt

import database

CHARTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "charts")

# Instruments to plot on the index-trends chart.
INDEX_NAMES = ["S&P 500", "Nasdaq 100", "Dow Jones", "Russell 2000"]

# How many recent trading days the index-trends chart spans (~1 year).
INDEX_CHART_DAYS = 252


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


def chart_index_trends(limit: int = INDEX_CHART_DAYS) -> str | None:
    """Plot normalized (=100 at start) price trends for the major indices.

    Shows a wide window (default ~1 trading year) so the trend is meaningful,
    and highlights the most recent day with a marker + a dashed "today" line so
    each new run is clearly visible on the long history.
    """
    fig, ax = plt.subplots(figsize=(11, 5))
    plotted = False
    latest_date = None

    for name in INDEX_NAMES:
        rows = database.price_history(name, limit)
        pairs = [(datetime.date.fromisoformat(r["run_date"]), r["price"])
                 for r in rows if r["price"] is not None]
        if len(pairs) < 2:
            continue
        dates = [d for d, _ in pairs]
        base = pairs[0][1]
        normalized = [p / base * 100 for _, p in pairs]  # rebase to 100
        line, = ax.plot(dates, normalized, linewidth=1.6,
                        label=f"{name}  ({normalized[-1]:.1f})")
        # Emphasize the latest point in each series' colour.
        ax.scatter([dates[-1]], [normalized[-1]], color=line.get_color(),
                   s=45, zorder=5, edgecolor="white", linewidth=0.8)
        latest_date = dates[-1]
        plotted = True

    if not plotted:
        print("  (not enough price history to chart index trends yet)")
        plt.close(fig)
        return None

    ax.axhline(100, color="#888", linewidth=0.8, linestyle="--")
    # Dashed vertical marker so the most recent day stands out on the long trend.
    if latest_date is not None:
        ax.axvline(latest_date, color="#444", linewidth=0.9, linestyle=":")
        ax.annotate(f"latest\n{latest_date.isoformat()}",
                    xy=(latest_date, ax.get_ylim()[1]),
                    xytext=(-4, -4), textcoords="offset points",
                    ha="right", va="top", fontsize=8, color="#444")

    ax.set_title("Major Indices — Normalized Price Trend (window start = 100)")
    ax.set_ylabel("Indexed to 100")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left", fontsize=9)
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
