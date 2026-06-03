import sectors


def test_render_md_handles_missing_values():
    rows = [
        {"sector": "Semiconductors / Compute", "move_pct": 1.08,
         "news_score": 0.4, "reddit_score": -0.5},
        {"sector": "Memory (DRAM/NAND/HBM)", "move_pct": None,
         "news_score": None, "reddit_score": None},
    ]
    md = sectors.render_md(rows)
    assert "Sector Watch" in md
    assert "Semiconductors / Compute" in md
    assert "+1.08%" in md
    assert "n/a" in md            # missing move renders as n/a
    assert sectors.render_md([]) == ""


def test_reddit_sentiment_keyword_match():
    titles = ["Nvidia GPU demand is insane", "weekend off-topic chat"]
    score, n = sectors._reddit_sentiment(["gpu", "nvidia"], titles)
    assert n == 1                 # only the first title matches
    score, n = sectors._reddit_sentiment(["gpu"], [])
    assert (score, n) == (None, 0)
