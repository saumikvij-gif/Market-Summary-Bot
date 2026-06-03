import sentiment


def test_classify_bullish_bearish():
    assert sentiment.classify("Stocks surge to record highs on strong earnings") == 1
    assert sentiment.classify("Markets crash amid recession fears and heavy losses") == -1


def test_label_for_thresholds():
    assert sentiment.label_for(0.5) == "Bullish"
    assert sentiment.label_for(0.2) == "Slightly Bullish"
    assert sentiment.label_for(0.0) == "Neutral"
    assert sentiment.label_for(-0.2) == "Slightly Bearish"
    assert sentiment.label_for(-0.5) == "Bearish"


def test_market_component_direction():
    bull = {"indices": {"S&P 500": {"pct_change": 1.0},
                        "Nasdaq 100": {"pct_change": 1.5},
                        "VIX": {"pct_change": -5.0}}}
    bear = {"indices": {"S&P 500": {"pct_change": -2.0},
                        "Nasdaq 100": {"pct_change": -2.5},
                        "VIX": {"pct_change": 12.0}}}
    assert sentiment.market_component(bull)["score"] > 0
    assert sentiment.market_component(bear)["score"] < 0


def test_market_component_clamped():
    extreme = {"indices": {"S&P 500": {"pct_change": 50.0},
                           "Nasdaq 100": {"pct_change": 50.0},
                           "VIX": {"pct_change": -90.0}}}
    assert -1.0 <= sentiment.market_component(extreme)["score"] <= 1.0


def test_fed_component_dampened():
    # A single dovish keyword must not swing the score to +1.
    r = sentiment.fed_component(["Fed signals a rate cut"])
    assert 0 < r["score"] <= 0.5
    assert sentiment.fed_component(["Minutes of the FOMC meeting"])["score"] == 0.0


def test_split_headlines():
    news, reddit, fed = sentiment._split_headlines({
        "Yahoo Finance": ["a"],
        "r/stocks": ["b"],
        "Fed (Monetary Policy)": ["c"],
    })
    assert news == ["a"] and reddit == ["b"] and fed == ["c"]


def test_build_dashboard_shape():
    md = {"indices": {"S&P 500": {"pct_change": 0.5},
                      "Nasdaq 100": {"pct_change": 0.6},
                      "VIX": {"pct_change": -1.0}}}
    heads = {"Yahoo Finance": ["Stocks rally on strong jobs report"],
             "r/stocks": ["thoughts?"],
             "Fed (Monetary Policy)": ["Fed signals a rate cut"]}
    dash = sentiment.build_dashboard(md, heads, run_date="2026-01-02")
    for key in ("date", "overall_score", "label", "market_score",
                "news_score", "reddit_score", "fed_score", "summary_text"):
        assert key in dash
    assert dash["date"] == "2026-01-02"
    assert -1.0 <= dash["overall_score"] <= 1.0
    assert dash["label"] == sentiment.label_for(dash["overall_score"])
