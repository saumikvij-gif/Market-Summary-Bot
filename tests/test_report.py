import report


def _dash():
    return {
        "overall_score": 0.15, "label": "Slightly Bullish",
        "market_score": 0.14, "news_score": 0.16, "reddit_score": 0.06,
        "fed_score": 0.25,
        "weights": {"market": 0.50, "news": 0.35, "reddit": 0.05, "fed": 0.10},
        "components": {"news": {"engine": "finbert"}},
        "divergence": "Prices rose but the news mood is negative.",
        "summary_text": "Test commentary.",
    }


MARKET = {"indices": {"S&P 500": {"price": 7600.0, "change": 9.8, "pct_change": 0.13}},
          "stocks": {}, "commodities": {}, "fx": {}}


def test_build_html_has_all_sections():
    html = report.build_html(
        "June 03, 2026", "## Overview\nMarkets rose.",
        [{"symbol": "LEGN", "name": "Legend Biotech", "price": 36.28, "pct_change": 42.22}],
        [{"source": "CNBC", "title": "Headline", "summary": "A summary.", "link": ""}],
        _dash(), MARKET, [],
    )
    for section in ("Daily Market Summary", "Market Tone", "Today's Session",
                    "Divergence Alert", "Top Gainers", "Top News",
                    "Market Snapshot", "Analyst Summary"):
        assert section in html
    # Price + a coloured change cell render in the snapshot.
    assert "7,600.00" in html
    assert "+0.13%" in html


def test_divergence_omitted_when_absent():
    d = _dash()
    d["divergence"] = None
    html = report.build_html("June 03, 2026", "x", [], [], d, MARKET, [])
    assert "Divergence Alert" not in html


def test_gainers_and_news_omitted_when_empty():
    html = report.build_html("June 03, 2026", "x", [], [], _dash(), MARKET, [])
    assert "Top Gainers" not in html
    assert "Top News" not in html
