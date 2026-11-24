from conftest import make_signal
from models import Direction, TradeResult
from storage import StateStore


def _store(tmp_path) -> StateStore:
    return StateStore(tmp_path / "state.db")


def test_signal_roundtrip_and_dedup(tmp_path):
    store = _store(tmp_path)
    try:
        signal = make_signal()
        assert store.is_duplicate(signal) is False
        store.record_signal(signal)
        assert store.is_duplicate(signal) is True

        recent = store.recent_signals(5)
        assert len(recent) == 1
        assert recent[0]["symbol"] == "EUR/USD"
        assert recent[0]["direction"] == "BUY"
    finally:
        store.close()


def test_duplicate_insert_is_ignored(tmp_path):
    store = _store(tmp_path)
    try:
        signal = make_signal()
        store.record_signal(signal)
        store.record_signal(signal)
        assert len(store.recent_signals(10)) == 1
    finally:
        store.close()


def test_different_direction_is_not_duplicate(tmp_path):
    store = _store(tmp_path)
    try:
        store.record_signal(make_signal(direction=Direction.BUY))
        assert store.is_duplicate(make_signal(direction=Direction.SELL)) is False
    finally:
        store.close()


def test_performance_stats(tmp_path):
    store = _store(tmp_path)
    try:
        for outcome, r in (("win", 1.67), ("win", 1.67), ("loss", -1.0)):
            store.record_trade(
                TradeResult(
                    symbol="EUR/USD",
                    interval="5m",
                    direction=Direction.BUY,
                    entry_time=1,
                    exit_time=2,
                    entry=1.0,
                    exit=1.1,
                    stop_loss=0.99,
                    take_profit=1.1,
                    outcome=outcome,
                    r_multiple=r,
                )
            )
        stats = store.performance()
        assert stats["trades"] == 3
        assert stats["wins"] == 2
        assert stats["win_rate"] == 66.7
    finally:
        store.close()


def test_performance_empty_db(tmp_path):
    store = _store(tmp_path)
    try:
        stats = store.performance()
        assert stats["trades"] == 0
        assert stats["win_rate"] == 0.0
    finally:
        store.close()
