from config import load_settings
from conftest import make_signal
from models import Direction
from telegram_sender import format_signal


def test_format_contains_key_fields():
    settings = load_settings()
    message = format_signal(make_signal(), settings)
    assert "EUR/USD" in message
    assert "BUY / LONG" in message
    assert "CALL" in message
    assert "72%" in message or "73%" in message
    assert "1.095" in message
    assert "Stop:" in message and "Target:" in message
    assert "R:R:" in message


def test_format_sell_shows_put():
    message = format_signal(
        make_signal(direction=Direction.SELL, score=-70.0, confidence=70.0), load_settings()
    )
    assert "SELL / SHORT" in message
    assert "PUT" in message


def test_format_escapes_html():
    signal = make_signal(reasons=["<script>alert(1)</script>"], symbol="A&B")
    message = format_signal(signal, load_settings())
    assert "<script>" not in message
    assert "&lt;script&gt;" in message
    assert "A&amp;B" in message


def test_format_respects_length_limit():
    signal = make_signal(reasons=[f"reason {i}" for i in range(500)])
    message = format_signal(signal, load_settings())
    assert len(message) <= 4096


def test_format_neutral_has_no_binary_side():
    signal = make_signal(direction=Direction.NEUTRAL, actionable=False, confidence=10.0)
    message = format_signal(signal, load_settings())
    assert "NEUTRAL" in message
    assert "CALL" not in message
    assert "PUT" not in message
