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


def test_sector_breadth():
    sectors = {"A": {"pct_change": 1.0}, "B": {"pct_change": 0.5},
               "C": {"pct_change": -0.3}, "D": {"pct_change": 0.2}}
    breadth, detail = sentiment._sector_breadth(sectors)
    assert breadth == (3 - 1) / 4          # 3 up, 1 down, of 4
    assert detail == {"up": 3, "down": 1, "total": 4}
    assert sentiment._sector_breadth({}) == (None, None)


def test_market_component_rates_inverted():
    # Rising 10Y yield is a headwind → negative rates subscore.
    md = {"indices": {"S&P 500": {"pct_change": 0.0}, "Nasdaq 100": {"pct_change": 0.0}},
          "rates": {"10Y Treasury Yield": {"pct_change": 3.0}}}
    sub = sentiment.market_component(md)["detail"]["subscores"]
    assert sub["rates"] < 0


def test_market_component_renormalizes_when_sparse():
    # Indices-only (no sectors/rates) still yields a sane, in-range score.
    md = {"indices": {"S&P 500": {"pct_change": 1.0}, "Nasdaq 100": {"pct_change": 1.0},
                      "VIX": {"pct_change": -2.0}}}
    r = sentiment.market_component(md)
    assert -1.0 <= r["score"] <= 1.0
    assert "breadth" not in r["detail"]["subscores"]


def test_fed_component_rate_based():
    # Falling short rates = market pricing easier policy = supportive (positive).
    easing = {"rates": {"2Y Treasury Yield": {"pct_change": -2.0}}}
    tightening = {"rates": {"2Y Treasury Yield": {"pct_change": 3.0}}}
    assert sentiment.fed_component(easing)["score"] > 0
    assert sentiment.fed_component(tightening)["score"] < 0
    assert sentiment.fed_component({})["score"] == 0.0   # no data → neutral


def test_split_headlines():
    news, reddit, fed = sentiment._split_headlines({
        "Yahoo Finance": ["a"],
        "r/stocks": ["b"],
        "Fed (Monetary Policy)": ["c"],
    })
    assert news == ["a"] and reddit == ["b"] and fed == ["c"]


def test_divergence_detection():
    # Mood vs tape disagree (price up, news down) → a divergence note.
    assert sentiment._divergence(0.3, -0.3) is not None
    assert sentiment._divergence(-0.3, 0.3) is not None
    # Agreement → no note.
    assert sentiment._divergence(0.3, 0.3) is None
    # Too small to matter → no note.
    assert sentiment._divergence(0.05, -0.05) is None


def test_extreme_headlines():
    ex = sentiment._extreme_headlines(
        ["Stocks surge to record highs on strong earnings",
         "Markets crash amid recession fears and heavy losses"],
        ["thoughts on the dip?"],
    )
    assert ex["most_bullish"]["score"] >= ex["most_bearish"]["score"]
    assert sentiment._extreme_headlines([], []) == {}


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
