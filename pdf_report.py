"""
pdf_report.py
-------------
Builds the full daily briefing as styled HTML and renders it to a downloadable
PDF (via xhtml2pdf). The PDF carries everything — sentiment dashboard, a clearly
titled divergence alert, a highlighted Top Gainers block, Top News with
summaries, the market price tables, and the trend charts — so the emailed
attachment is the complete report in one file.

Changes render as coloured +/- text (green/red) rather than ▲/▼ glyphs, which
the PDF core fonts don't include.
"""

import os
import markdown as md

SECTION_LABELS = {
    "indices": "Major Indices",
    "stocks": "Key Stocks",
    "commodities": "Commodities & Crypto",
    "fx": "FX Rates",
    "rates": "Interest Rates",
}

CSS = """
@page { size: A4; margin: 1.5cm; }
body { font-family: Helvetica, Arial, sans-serif; color: #222; font-size: 10.5pt; }
h1 { color: #14223b; font-size: 20pt; margin: 0 0 2px 0; }
h2 { color: #14223b; font-size: 13pt; border-bottom: 1.5px solid #d6deea;
     padding-bottom: 3px; margin-top: 16px; }
.subtitle { color: #667; font-size: 10pt; margin-bottom: 6px; }
.score { font-size: 15pt; font-weight: bold; }
table { width: 100%; border-collapse: collapse; margin-top: 3px; }
th, td { text-align: left; padding: 1.5px 5px; font-size: 8.5pt; }
th { background: #14223b; color: #fff; }
tr:nth-child(even) td { background: #f3f6fa; }
.pos { color: #1a7f37; font-weight: bold; }
.neg { color: #c0392b; font-weight: bold; }
.box { padding: 8px 12px; margin: 8px 0; border-radius: 4px; }
.gainers { background: #eaf7ee; border-left: 5px solid #1a7f37; }
.divergence { background: #fff4e5; border-left: 5px solid #e67e22; }
.news-item { margin-bottom: 8px; }
.news-title { font-weight: bold; }
.news-src { color: #888; font-size: 8.5pt; }
.news-sum { color: #444; font-size: 9pt; }
.note { background: #fdecea; border-left: 5px solid #c0392b; padding: 6px 10px; }
/* Charts sit on the dashboard page, so keep them small enough to fit together. */
img { width: 12cm; }
"""


def _chg(value, pct=False):
    """Coloured signed number cell content."""
    if value is None:
        return "<span>n/a</span>"
    cls = "pos" if value >= 0 else "neg"
    txt = f"{value:+.2f}%" if pct else f"{value:+,.2f}"
    return f'<span class="{cls}">{txt}</span>'


def _colored(text: str, value) -> str:
    """Wrap arbitrary text in a green/red span by the sign of `value`.

    Near-zero (|value| < 0.005, i.e. rounds to 0.00) and None render plain, so a
    neutral reading isn't dressed up as bullish or bearish.
    """
    if value is None or abs(value) < 0.005:
        return text
    cls = "pos" if value > 0 else "neg"
    return f'<span class="{cls}">{text}</span>'


def _market_tables(market_data: dict) -> str:
    parts = []
    for key, label in SECTION_LABELS.items():
        rows = market_data.get(key) or {}
        body = ""
        for name, q in rows.items():
            if not isinstance(q, dict) or "error" in q:
                continue
            price = f"{q.get('price'):,.2f}" if q.get("price") is not None else "n/a"
            body += (f"<tr><td>{name}</td><td>{price}</td>"
                     f"<td>{_chg(q.get('change'))}</td>"
                     f"<td>{_chg(q.get('pct_change'), pct=True)}</td></tr>")
        if body:
            parts.append(
                f"<b>{label}</b><table><tr><th>Instrument</th><th>Price</th>"
                f"<th>Change</th><th>% Change</th></tr>{body}</table>")
    return "".join(parts)


def _gainers_block(gainers: list) -> str:
    if not gainers:
        return ""
    rows = ""
    for g in gainers:
        price = f"{g['price']:,.2f}" if g.get("price") is not None else "n/a"
        rows += (f"<tr><td>{g.get('name','')} ({g.get('symbol','')})</td>"
                 f"<td>{price}</td><td>{_chg(g.get('pct_change'), pct=True)}</td></tr>")
    return (f'<h2>Top Gainers</h2><div class="box gainers">'
            f'<table><tr><th>Company</th><th>Price</th><th>% Change</th></tr>'
            f'{rows}</table></div>')


def _news_block(news: list) -> str:
    if not news:
        return ""
    items = ""
    for n in news:
        summary = n.get("summary") or ""
        sum_html = f'<div class="news-sum">{summary}</div>' if summary else ""
        items += (f'<div class="news-item"><span class="news-title">{n["title"]}</span>'
                  f' <span class="news-src">— {n.get("source","")}</span>{sum_html}</div>')
    return f"<h2>Top News</h2>{items}"


def _sentiment_label(score):
    """Lazy import to avoid a hard dependency cycle at module load."""
    if score is None:
        return "—"
    import sentiment
    return sentiment.label_for(score)


def _sector_watch_block(rows: list) -> str:
    if not rows:
        return ""
    # Leaders first: strongest blended score at the top, laggards at the bottom —
    # the way a PM scans a sector table.
    rows = sorted(rows, key=lambda r: (r.get("score") is None, -(r.get("score") or 0)))
    body = ""
    for r in rows:
        move = _chg(r.get("move_pct"), pct=True) if r.get("move_pct") is not None else "n/a"
        rs = _chg(r.get("rel_strength"), pct=True) if r.get("rel_strength") is not None else "n/a"
        breadth = f"{r['breadth_pct']}%" if r.get("breadth_pct") is not None else "n/a"
        news = _sentiment_label(r.get("news_score"))
        # Overall: label + numeric score, coloured by sign so resolution isn't lost.
        sc = r.get("score")
        overall_txt = r.get("label", "—") + (f" ({sc:+.2f})" if sc is not None else "")
        overall = _colored(f"<b>{overall_txt}</b>", sc)
        body += (f"<tr><td>{r['sector']}</td><td>{move}</td><td>{rs}</td>"
                 f"<td>{breadth}</td><td>{news}</td><td>{overall}</td></tr>")
    return (f"<h2>Sector Watch (AI Stack)</h2>"
            f'<p class="news-src">Blended score from relative strength, breadth '
            f"(avg % of the 20/50/200-day MAs the basket trades above), news, "
            f"volume, and Reddit. Sorted strongest&rarr;weakest.</p>"
            f"<table><tr><th>Sector</th><th>Move</th><th>vs S&amp;P</th>"
            f"<th>Breadth</th><th>News</th><th>Overall</th></tr>{body}</table>")


def _divergence_block(dash: dict) -> str:
    div = dash.get("divergence")
    if not div:
        return ""
    return (f'<h2>Divergence Alert</h2>'
            f'<div class="box divergence">{div}</div>')


def _standout_block(dash: dict) -> str:
    """The day's most-bullish / most-bearish single headline (matches markdown)."""
    h = dash.get("headlines") or {}
    if not h:
        return ""
    items = ""
    b = h.get("most_bullish")
    if b:
        items += (f'<div class="news-item"><span class="pos">Most bullish '
                  f'({b["score"]:+.2f})</span> &mdash; {b["title"]}</div>')
    be = h.get("most_bearish")
    if be:
        items += (f'<div class="news-item"><span class="neg">Most bearish '
                  f'({be["score"]:+.2f})</span> &mdash; {be["title"]}</div>')
    return f'<p class="news-title">Standout headlines</p>{items}' if items else ""


def _dashboard_block(dash: dict) -> str:
    w = dash.get("weights", {})
    rows = ""
    for key, label in [("market", "Market data"), ("news", "News headlines"),
                       ("reddit", "Reddit"), ("fed", "Fed (rate expectations)")]:
        # A 0-weight component (e.g. Reddit for now) is flagged as disabled rather
        # than shown as a live-looking "0% | +0.00" row.
        disabled = w.get(key, 0) == 0
        lbl = f'{label} <span class="news-src">(disabled)</span>' if disabled else label
        rows += (f"<tr><td>{lbl}</td><td>{w.get(key,0):.0%}</td>"
                 f"<td>{_chg(dash.get(key+'_score'))}</td></tr>")
    engine = (dash.get("components", {}).get("news", {}) or {}).get("engine", "")
    eng = f' <span class="news-src">(news scored with {engine})</span>' if engine else ""
    score = dash.get("overall_score", 0)
    head = _colored(f'Today: {score:+.2f} &rarr; {dash.get("label","")}', score)
    trend = ""
    if dash.get("smoothed_score") is not None:
        span = dash.get("smoothing_span", "")
        sm = dash["smoothed_score"]
        sm_txt = _colored(f'{sm:+.2f} &rarr; {dash.get("smoothed_label","")}', sm)
        trend = (f'<p class="news-src">Trend ({span}-day EMA): <b>{sm_txt}</b>'
                 f' &mdash; smoothed, the readable day-over-day signal.</p>')
    return (f'<h2>Market Tone &mdash; Today\'s Session</h2>'
            f'<p class="news-src">A recap of how the market traded today &mdash; not a forecast.</p>'
            f'<p class="score">{head}</p>'
            f'{trend}'
            f'<table><tr><th>Component</th><th>Weight</th><th>Score</th></tr>'
            f'{rows}</table>'
            f'<p class="news-sum">{dash.get("summary_text","")}{eng}</p>'
            f'{_standout_block(dash)}')


def _charts_block(chart_paths: list) -> str:
    imgs = ""
    for p in chart_paths or []:
        if p and os.path.exists(p):
            imgs += f'<p><img src="{os.path.abspath(p)}"/></p>'
    return f"<h2>Trend Charts</h2>{imgs}" if imgs else ""


def build_html(date_str: str, prose_md: str, gainers: list, news: list,
               dashboard: dict, market_data: dict, chart_paths: list,
               stale_note: str = "", sector_watch: list = None) -> str:
    """Assemble the full briefing HTML."""
    note = f'<div class="note">{stale_note}</div>' if stale_note else ""
    prose_html = md.markdown(prose_md or "", extensions=["extra"])
    dash_html = _dashboard_block(dashboard) if dashboard else ""
    pb = '<div style="page-break-before: always;"></div>'   # one section per page
    return f"""<html><head><meta charset="utf-8"><style>{CSS}</style></head><body>
<h1>Daily Market Summary</h1>
<div class="subtitle">{date_str}</div>
{note}
{dash_html}
{_divergence_block(dashboard or {})}
{_charts_block(chart_paths)}
{pb}
{_sector_watch_block(sector_watch)}
{pb}
{_gainers_block(gainers)}
{_news_block(news)}
{pb}
<h2>Market Snapshot</h2>
{_market_tables(market_data)}
{pb}
<h2>Analyst Summary</h2>
{prose_html}
</body></html>"""


def write_pdf(html: str, path: str) -> bool:
    """Render HTML to a PDF file. Returns True on success."""
    from xhtml2pdf import pisa
    with open(path, "wb") as f:
        result = pisa.CreatePDF(html, dest=f, encoding="utf-8")
    return not result.err
