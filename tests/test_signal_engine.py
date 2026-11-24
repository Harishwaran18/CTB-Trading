from dataclasses import replace

import pytest

from config import load_settings
from models import Direction, Regime
from signal_engine import (
    compute_features,
    evaluate,
    next_higher,
    raw_score,
    regime_of,
)


@pytest.fixture(scope="module")
def settings():
    return load_settings()


def _last_two(df):
    features = compute_features(df)
    return features.iloc[-1], features.iloc[-2]


def test_next_higher_mapping():
    assert next_higher("1m") == "5m"
    assert next_higher("5m") == "15m"
    assert next_higher("15m") == "1h"
    assert next_higher("1h") == "4h"


def test_regime_of_thresholds():
    assert regime_of({"adx": 30.0}) is Regime.TRENDING
    assert regime_of({"adx": 10.0}) is Regime.RANGING
    assert regime_of({}) is Regime.RANGING


def test_uptrend_scores_bullish(uptrend, settings):
    row, prev = _last_two(uptrend)
    result = evaluate(row, prev, htf_score=40.0, settings=settings)
    assert result["direction"] is Direction.BUY
    assert result["score"] > 0
    assert 0 <= result["confidence"] <= 100


def test_downtrend_scores_bearish(downtrend, settings):
    row, prev = _last_two(downtrend)
    result = evaluate(row, prev, htf_score=-40.0, settings=settings)
    assert result["direction"] is Direction.SELL
    assert result["score"] < 0


def test_flat_market_low_conviction(flat, settings):
    row, prev = _last_two(flat)
    result = evaluate(row, prev, htf_score=0.0, settings=settings)
    assert result["confidence"] < 40
    assert result["actionable"] is False


def test_score_is_bounded(uptrend, settings):
    row, prev = _last_two(uptrend)
    for htf in (-200, -50, 0, 50, 200):
        result = evaluate(row, prev, htf_score=htf, settings=settings)
        assert -100.0 <= result["score"] <= 100.0
        assert 0.0 <= result["confidence"] <= 100.0


def test_confidence_gate_controls_actionable(uptrend, settings):
    row, prev = _last_two(uptrend)
    loose = replace(settings, min_confidence=1.0)
    strict = replace(settings, min_confidence=100.0)
    assert evaluate(row, prev, 60.0, loose)["actionable"] is True
    assert evaluate(row, prev, 60.0, strict)["actionable"] is False


def test_htf_bias_shifts_score(uptrend, settings):
    row, prev = _last_two(uptrend)
    bullish = evaluate(row, prev, htf_score=80.0, settings=settings)["score"]
    bearish = evaluate(row, prev, htf_score=-80.0, settings=settings)["score"]
    assert bullish > bearish


def test_strategy_presets_are_valid(uptrend, settings):
    row, prev = _last_two(uptrend)
    for strategy in ("adaptive", "trend", "reversion"):
        result = evaluate(row, prev, 0.0, settings, strategy=strategy)
        assert -100.0 <= result["score"] <= 100.0


def test_raw_score_is_bounded_and_signed(uptrend, settings):
    row, prev = _last_two(uptrend)
    direct = raw_score(row, prev, settings)
    folded = evaluate(row, prev, 0.0, settings)["score"]
    assert -100.0 <= direct <= 100.0
    # Diluting the base score with a zero higher-tf vote keeps its sign.
    assert (direct >= 0) == (folded >= 0)


def test_reasons_are_human_readable(uptrend, settings):
    row, prev = _last_two(uptrend)
    reasons = evaluate(row, prev, 40.0, settings)["reasons"]
    assert isinstance(reasons, list) and reasons
    assert all(isinstance(reason, str) and reason for reason in reasons)
