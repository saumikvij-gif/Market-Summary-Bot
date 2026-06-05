import sector_watch


def test_render_md_handles_missing_values():
    rows = [
        {"sector": "Semiconductors / Compute", "move_pct": 1.08,
         "rel_strength": 0.9, "breadth_pct": 80, "news_score": 0.4,
         "score": 0.5, "label": "Bullish"},
        {"sector": "Memory (DRAM/NAND/HBM)", "move_pct": None,
         "rel_strength": None, "breadth_pct": None, "news_score": None,
         "score": 0.0, "label": "Neutral"},
    ]
    md = sector_watch.render_md(rows)
    assert "Sector Watch" in md
    assert "Semiconductors / Compute" in md
    assert "+1.08%" in md
    assert "80% trend" in md       # breadth rendered
    assert "Bullish" in md
    assert "n/a" in md             # missing values render as n/a
    assert sector_watch.render_md([]) == ""


def test_median_robust_to_outlier():
    # One +30% name shouldn't define a basket whose other members are flat-ish.
    assert sector_watch._median([0.1, 0.2, 0.3, 30.0]) == (0.2 + 0.3) / 2
    assert sector_watch._median([1.0, -1.0, 5.0]) == 1.0   # odd count → middle value


def test_volume_score_floors_at_zero():
    # The volume sub-metric only confirms direction with ABOVE-average volume;
    # below-average volume must contribute 0, never an opposite-sign signal.
    # This mirrors the formula in build_sector_watch: clamp(max(0, ratio-1))*dir.
    def vol_score(avg_ratio, direction):
        return sector_watch._clamp(max(0.0, avg_ratio - 1.0)) * direction
    assert vol_score(1.5, +1) > 0          # heavy volume up day → confirms up
    assert vol_score(1.5, -1) < 0          # heavy volume down day → confirms down
    assert vol_score(0.6, -1) == 0.0       # light-volume down day → NO anti-signal
    assert vol_score(0.6, +1) == 0.0       # light-volume up day → NO anti-signal


def test_rel_strength_nan_safe():
    nan = float("nan")
    # Normal case: real delta + bounded score.
    score, delta = sector_watch._rel_strength(1.0, -1.0)   # basket +1 vs bench -1
    assert delta == 2.0 and 0 < score <= 1.0
    # NaN/None benchmark or basket → drops out entirely (no "nan%", no clamp-to-1).
    assert sector_watch._rel_strength(1.0, nan) == (None, None)
    assert sector_watch._rel_strength(nan, -1.0) == (None, None)
    assert sector_watch._rel_strength(1.0, None) == (None, None)
    assert sector_watch._finite(nan) is False
    assert sector_watch._finite(0.0) is True


def test_rs_full_scale_widened():
    # Widened so high-beta baskets don't pin relative strength to ±1 constantly.
    assert sector_watch.RS_FULL_SCALE_PCT >= 3.0


def test_reddit_sentiment_keyword_match():
    titles = ["Nvidia GPU demand is insane", "weekend off-topic chat"]
    score, n = sector_watch._reddit_sentiment(["gpu", "nvidia"], titles)
    assert n == 1                 # only the first title matches
    score, n = sector_watch._reddit_sentiment(["gpu"], [])
    assert (score, n) == (None, 0)
