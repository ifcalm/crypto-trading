import tempfile
from datetime import datetime
from decimal import Decimal

import pytest

from crypto_trading.core.types import OHLCV
from crypto_trading.data.store import ParquetStore


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield ParquetStore(base_dir=tmpdir)


def test_write_and_read_ohlcv(store: ParquetStore):
    bars = [
        OHLCV(
            timestamp=datetime(2024, 1, 1, 0, 0),
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("95"),
            close=Decimal("105"),
            volume=Decimal("1000"),
        ),
        OHLCV(
            timestamp=datetime(2024, 1, 1, 1, 0),
            open=Decimal("105"),
            high=Decimal("115"),
            low=Decimal("100"),
            close=Decimal("110"),
            volume=Decimal("1200"),
        ),
    ]

    count = store.write_ohlcv("BTC/USDT", "1h", bars)
    assert count == 2

    result = store.read_ohlcv(
        "BTC/USDT",
        "1h",
        start=datetime(2024, 1, 1),
        end=datetime(2024, 1, 2),
    )
    assert len(result) == 2
    assert result[0].open == Decimal("100")
    assert result[1].close == Decimal("110")


def test_deduplication(store: ParquetStore):
    bars = [
        OHLCV(
            timestamp=datetime(2024, 1, 1, 0, 0),
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("95"),
            close=Decimal("105"),
            volume=Decimal("1000"),
        ),
    ]
    store.write_ohlcv("BTC/USDT", "1h", bars)

    bars2 = [
        OHLCV(
            timestamp=datetime(2024, 1, 1, 0, 0),
            open=Decimal("99"),
            high=Decimal("109"),
            low=Decimal("94"),
            close=Decimal("104"),
            volume=Decimal("999"),
        ),
    ]
    store.write_ohlcv("BTC/USDT", "1h", bars2)

    result = store.read_ohlcv(
        "BTC/USDT",
        "1h",
        start=datetime(2024, 1, 1),
        end=datetime(2024, 1, 2),
    )
    assert len(result) == 1
    assert result[0].open == Decimal("99")


def test_empty_store(store: ParquetStore):
    result = store.read_ohlcv(
        "ETH/USDT",
        "1h",
        start=datetime(2024, 1, 1),
        end=datetime(2024, 1, 2),
    )
    assert result == []


def test_date_range(store: ParquetStore):
    bars = [
        OHLCV(
            timestamp=datetime(2024, 1, 1, 0, 0),
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("95"),
            close=Decimal("105"),
            volume=Decimal("1000"),
        ),
        OHLCV(
            timestamp=datetime(2024, 6, 15, 0, 0),
            open=Decimal("200"),
            high=Decimal("210"),
            low=Decimal("190"),
            close=Decimal("205"),
            volume=Decimal("2000"),
        ),
    ]
    store.write_ohlcv("BTC/USDT", "1h", bars)

    date_range = store.get_date_range("BTC/USDT", "1h")
    assert date_range is not None
    assert date_range[0] == datetime(2024, 1, 1, 0, 0)
    assert date_range[1] == datetime(2024, 6, 15, 0, 0)


def test_date_range_empty(store: ParquetStore):
    date_range = store.get_date_range("DOGE/USDT", "1d")
    assert date_range is None
