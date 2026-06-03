import fed


def test_treasury_component_direction():
    # Falling 2Y = market pricing easier policy = dovish/supportive (positive).
    easing = {"rates": {"2Y Treasury Yield": {"pct_change": -2.0}}}
    tightening = {"rates": {"2Y Treasury Yield": {"pct_change": 3.0}}}
    assert fed.treasury_component(easing)["score"] > 0
    assert fed.treasury_component(tightening)["score"] < 0
    assert fed.treasury_component(easing)["active"] is True
    # No rate data → inactive, neutral.
    none = fed.treasury_component({})
    assert none["active"] is False and none["score"] == 0.0


def test_inflation_component_inactive():
    inf = fed.inflation_component()
    assert inf["active"] is False
    assert inf["score"] == 0.0


def test_communications_inactive_without_text():
    c = fed.communications_component([])
    assert c["active"] is False and c["n"] == 0


def test_communications_active_with_text():
    c = fed.communications_component(["The Fed cut rates and signaled further easing"])
    assert c["active"] is True and c["n"] == 1
    assert -1.0 <= c["score"] <= 1.0


def test_score_renormalizes_to_treasury_only():
    # Only the Treasury component is active on a normal (no-Fed-text) day, so it
    # carries the whole score despite its nominal 50% weight.
    md = {"rates": {"2Y Treasury Yield": {"pct_change": -3.0}}}
    out = fed.fed_expectations_score(md, fed_titles=[])
    assert out["detail"]["active_components"] == ["treasury"]
    assert out["detail"]["weights_used"] == {"treasury": 1.0}
    assert out["score"] == 1.0          # full dovish: -(-3)/3 clamped to +1
    assert out["label"] == "Dovish"


def test_score_no_active_components_is_neutral():
    out = fed.fed_expectations_score({}, fed_titles=[])
    assert out["score"] == 0.0
    assert out["detail"]["active_components"] == []


def test_score_blends_treasury_and_comms():
    # Both active → renormalized over 0.50 + 0.25, i.e. 2/3 treasury, 1/3 comms.
    md = {"rates": {"2Y Treasury Yield": {"pct_change": -3.0}}}  # treasury = +1
    out = fed.fed_expectations_score(md, fed_titles=["Fed holds rates steady"])
    w = out["detail"]["weights_used"]
    assert set(out["detail"]["active_components"]) == {"treasury", "communications"}
    assert abs(w["treasury"] - 2 / 3) < 1e-3
    assert abs(w["communications"] - 1 / 3) < 1e-3
    assert -1.0 <= out["score"] <= 1.0


def test_fed_label_bands():
    assert fed.fed_label(0.5) == "Dovish"
    assert fed.fed_label(0.2) == "Leaning dovish"
    assert fed.fed_label(0.0) == "Neutral"
    assert fed.fed_label(-0.2) == "Leaning hawkish"
    assert fed.fed_label(-0.5) == "Hawkish"
