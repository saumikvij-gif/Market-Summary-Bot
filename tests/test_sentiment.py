import news_feeds
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
    # `change` is the absolute yield move in percentage points (×100 = basis points).
    easing = {"rates": {sentiment.FED_RATE_KEY: {"change": -0.10}}}      # -10bp
    tightening = {"rates": {sentiment.FED_RATE_KEY: {"change": 0.20}}}   # +20bp
    assert sentiment.fed_component(easing)["score"] > 0
    assert sentiment.fed_component(tightening)["score"] < 0
    assert sentiment.fed_component({})["score"] == 0.0   # no data → neutral


def test_split_headlines():
    news, reddit, fed = news_feeds.split_headlines({
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


def test_weights_sum_to_one():
    # The composite weighting must always sum to 1 so the blend stays in [-1, 1].
    assert abs(sum(sentiment.WEIGHTS.values()) - 1.0) < 1e-9
    # Reddit is intentionally disabled for now (no good same-day social feed).
    assert sentiment.WEIGHTS["reddit"] == 0.0


def test_ema_smoothing():
    # Empty → None; single value → itself.
    assert sentiment.ema([]) is None
    assert sentiment.ema([0.5]) == 0.5
    # A constant series smooths to that constant.
    assert abs(sentiment.ema([0.2, 0.2, 0.2]) - 0.2) < 1e-9
    # EMA lags a step change — it sits between the old level and the new value.
    s = sentiment.ema([0.0, 0.0, 0.0, 1.0], span=5)
    assert 0.0 < s < 1.0
    # None values are ignored, not treated as zero.
    assert sentiment.ema([None, 0.5, None, 0.5]) == 0.5


def test_dashboard_smoothing_uses_prior_scores():
    md = {"indices": {"S&P 500": {"pct_change": 2.0},
                      "Nasdaq 100": {"pct_change": 2.0},
                      "VIX": {"pct_change": -5.0}}}
    # A run of bearish prior days should drag the smoothed trend below today's
    # bullish raw score (the trend lags the daily spike).
    dash = sentiment.build_dashboard(md, {}, run_date="2026-01-02",
                                     prior_scores=[-0.5, -0.5, -0.5, -0.5])
    assert dash["smoothed_score"] < dash["overall_score"]
    assert dash["smoothed_label"] == sentiment.label_for(dash["smoothed_score"])
    # With no history, the smoothed score equals the raw score.
    solo = sentiment.build_dashboard(md, {}, run_date="2026-01-02")
    assert solo["smoothed_score"] == solo["overall_score"]


def test_build_dashboard_shape():
    md = {"indices": {"S&P 500": {"pct_change": 0.5},
                      "Nasdaq 100": {"pct_change": 0.6},
                      "VIX": {"pct_change": -1.0}}}
    heads = {"Yahoo Finance": ["Stocks rally on strong jobs report"],
             "r/stocks": ["thoughts?"],
             "Fed (Monetary Policy)": ["Fed signals a rate cut"]}
    dash = sentiment.build_dashboard(md, heads, run_date="2026-01-02")
    for key in ("date", "overall_score", "label", "smoothed_score",
                "smoothed_label", "market_score", "news_score", "reddit_score",
                "fed_score", "summary_text"):
        assert key in dash
    assert dash["date"] == "2026-01-02"
    assert -1.0 <= dash["overall_score"] <= 1.0
    assert dash["label"] == sentiment.label_for(dash["overall_score"])


# ── Fed Expectations sub-model (merged in from the former fed.py) ──────────────

def test_treasury_component_direction():
    # Falling front-end yield = market pricing easier policy = dovish/supportive (+).
    # `change` is the absolute yield move in percentage points → ×100 = basis points.
    easing = {"rates": {sentiment.FED_RATE_KEY: {"change": -0.10}}}      # -10bp
    tightening = {"rates": {sentiment.FED_RATE_KEY: {"change": 0.20}}}   # +20bp
    assert sentiment.treasury_component(easing)["score"] > 0
    assert sentiment.treasury_component(tightening)["score"] < 0
    assert sentiment.treasury_component(easing)["move_bp"] == -10.0
    assert sentiment.treasury_component(easing)["active"] is True
    # No rate data → inactive, neutral.
    none = sentiment.treasury_component({})
    assert none["active"] is False and none["score"] == 0.0


def test_inflation_component_inactive():
    inf = sentiment.inflation_component()
    assert inf["active"] is False
    assert inf["score"] == 0.0


def test_communications_inactive_without_text():
    c = sentiment.communications_component([])
    assert c["active"] is False and c["n"] == 0


def test_communications_active_with_text():
    c = sentiment.communications_component(["The Fed cut rates and signaled further easing"])
    assert c["active"] is True and c["n"] == 1
    assert -1.0 <= c["score"] <= 1.0


def test_score_renormalizes_to_treasury_only():
    # Only the Treasury component is active on a normal (no-Fed-text) day, so it
    # carries the whole score despite its nominal 50% weight.
    md = {"rates": {sentiment.FED_RATE_KEY: {"change": -0.15}}}  # -15bp = full dovish
    out = sentiment.fed_expectations_score(md, fed_titles=[])
    assert out["detail"]["active_components"] == ["treasury"]
    assert out["detail"]["weights_used"] == {"treasury": 1.0}
    assert out["score"] == 1.0          # full dovish: -(-15bp)/15 clamped to +1
    assert out["label"] == "Dovish"


def test_score_no_active_components_is_neutral():
    out = sentiment.fed_expectations_score({}, fed_titles=[])
    assert out["score"] == 0.0
    assert out["detail"]["active_components"] == []


def test_score_blends_treasury_and_comms():
    # Both active → renormalized over 0.50 + 0.25, i.e. 2/3 treasury, 1/3 comms.
    md = {"rates": {sentiment.FED_RATE_KEY: {"change": -0.15}}}  # -15bp → treasury = +1
    out = sentiment.fed_expectations_score(md, fed_titles=["Fed holds rates steady"])
    w = out["detail"]["weights_used"]
    assert set(out["detail"]["active_components"]) == {"treasury", "communications"}
    assert abs(w["treasury"] - 2 / 3) < 1e-3
    assert abs(w["communications"] - 1 / 3) < 1e-3
    assert -1.0 <= out["score"] <= 1.0


def test_fed_label_bands():
    assert sentiment.fed_label(0.5) == "Dovish"
    assert sentiment.fed_label(0.2) == "Leaning dovish"
    assert sentiment.fed_label(0.0) == "Neutral"
    assert sentiment.fed_label(-0.2) == "Leaning hawkish"
    assert sentiment.fed_label(-0.5) == "Hawkish"
