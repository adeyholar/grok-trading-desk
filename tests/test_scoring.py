import pytest

from src.models import Stock
from src.stocks import stock_scoring as ss

STOCK = Stock(symbol="ACME", price=50, prev_close=46, avg_volume=2e6, volume=6e6, market_cap=5e9)
OPEN_PULSE = {"go_signal": 1.0}

CLEAN_RADAR = {"sentiment_score": 1.0, "news_momentum": 1.0, "controversy": 0.0}
CLEAN_INSIDER = {
    "insider_buying": 1.0, "insider_selling": 0.0,
    "institutional_flow": 1.0, "cluster_buying": True,
}
STRONG_ANALYST = {"fundamentals_score": 1.0, "technicals_score": 1.0}


def test_stock_perfect_inputs_score_one_and_buy():
    result = ss.score_stock(STOCK, STRONG_ANALYST, CLEAN_RADAR, CLEAN_INSIDER, OPEN_PULSE)
    assert result["score"] == 1.0
    assert result["buy"] is True


def test_stock_controversy_veto_at_the_boundary():
    just_under = ss.score_stock(STOCK, STRONG_ANALYST, {**CLEAN_RADAR, "controversy": 0.7},
                                CLEAN_INSIDER, OPEN_PULSE)
    assert just_under["vetoed"] is False   # 0.7 is not > 0.7

    just_over = ss.score_stock(STOCK, STRONG_ANALYST, {**CLEAN_RADAR, "controversy": 0.71},
                               CLEAN_INSIDER, OPEN_PULSE)
    assert just_over["reason"] == "veto_controversy"


def test_stock_insider_veto_needs_both_conditions():
    heavy_selling_but_also_buying = {**CLEAN_INSIDER, "insider_selling": 0.9, "insider_buying": 0.5}
    assert ss.score_stock(STOCK, STRONG_ANALYST, CLEAN_RADAR, heavy_selling_but_also_buying,
                          OPEN_PULSE)["vetoed"] is False

    dumping = {**CLEAN_INSIDER, "insider_selling": 0.81, "insider_buying": 0.19}
    assert ss.score_stock(STOCK, STRONG_ANALYST, CLEAN_RADAR, dumping,
                          OPEN_PULSE)["reason"] == "veto_insider_selling"


def test_stock_insider_veto_boundary_is_exclusive():
    edge = {**CLEAN_INSIDER, "insider_selling": 0.8, "insider_buying": 0.2}
    assert ss.score_stock(STOCK, STRONG_ANALYST, CLEAN_RADAR, edge, OPEN_PULSE)["vetoed"] is False


def test_stock_paused_market_veto():
    result = ss.score_stock(STOCK, STRONG_ANALYST, CLEAN_RADAR, CLEAN_INSIDER, {"go_signal": 0.1})
    assert result["reason"] == "veto_market_paused"


def test_stock_pessimistic_fallbacks_are_vetoed():
    from src.stocks.analyst import Analyst
    from src.stocks.insider import Insider
    from src.stocks.market_pulse import MarketPulse
    from src.stocks.radar import Radar

    result = ss.score_stock(
        STOCK, Analyst({}).fallback(), Radar({}).fallback(),
        Insider({}).fallback(), MarketPulse({}).fallback(),
    )
    assert result["buy"] is False and result["vetoed"] is True


def test_stock_news_score_subtracts_controversy():
    assert ss.news_score(CLEAN_RADAR) == 1.0
    assert ss.news_score({**CLEAN_RADAR, "controversy": 0.5}) == pytest.approx(0.75)
    assert ss.news_score({"sentiment_score": 0.0, "news_momentum": 0.0, "controversy": 0.5}) == 0.0


def test_stock_insider_score_rewards_clusters_and_punishes_selling():
    assert ss.insider_score(CLEAN_INSIDER) == 1.0
    assert ss.insider_score({**CLEAN_INSIDER, "cluster_buying": False}) == pytest.approx(0.85)
    assert ss.insider_score({"insider_buying": 0.0, "insider_selling": 1.0}) == 0.0


def test_stock_missing_keys_default_pessimistically():
    result = ss.score_stock(STOCK, {}, {}, {}, {})
    assert result["vetoed"] is True   # missing controversy defaults to 1.0
    assert result["buy"] is False


def test_stock_custom_weights_change_the_verdict():
    mediocre_fundamentals = {"fundamentals_score": 0.0, "technicals_score": 1.0}
    fundamentals_heavy = {**ss.DEFAULT_WEIGHTS, "fundamentals": 0.9, "technicals": 0.02,
                          "news_sentiment": 0.02, "insider": 0.03, "pulse": 0.03}
    result = ss.score_stock(STOCK, mediocre_fundamentals, CLEAN_RADAR, CLEAN_INSIDER,
                            OPEN_PULSE, weights=fundamentals_heavy)
    assert result["buy"] is False
