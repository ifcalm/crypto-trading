from datetime import datetime
from decimal import Decimal

from crypto_trading.core.types import OrderBookLevel, OrderBookSnapshot
from crypto_trading.indicators.orderbook import (
    compute_depth_imbalance_at_level,
    compute_imbalance,
    compute_imbalance_trend,
    compute_orderbook_signal,
    detect_order_walls,
)


def _make_snapshot(bids: list, asks: list) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        symbol="BTC/USDT",
        timestamp=datetime(2024, 1, 1, 12, 0),
        bids=[OrderBookLevel(Decimal(str(p)), Decimal(str(q))) for p, q in bids],
        asks=[OrderBookLevel(Decimal(str(p)), Decimal(str(q))) for p, q in asks],
    )


class TestImbalance:
    def test_neutral(self):
        ob = _make_snapshot(bids=[(100, 5)], asks=[(101, 5)])
        assert compute_imbalance(ob) == 0.0

    def test_buy_pressure(self):
        ob = _make_snapshot(bids=[(100, 8)], asks=[(101, 2)])
        assert compute_imbalance(ob) > 0

    def test_sell_pressure(self):
        ob = _make_snapshot(bids=[(100, 2)], asks=[(101, 8)])
        assert compute_imbalance(ob) < 0

    def test_empty_book(self):
        ob = _make_snapshot(bids=[], asks=[])
        assert compute_imbalance(ob) == 0.0


class TestImbalanceTrend:
    def test_flat_trend(self):
        snapshots = [
            _make_snapshot(bids=[(100, 5)], asks=[(101, 5)]) for _ in range(10)
        ]
        trend = compute_imbalance_trend(snapshots, window=10)
        assert trend is not None
        assert abs(trend) < 1e-6

    def test_increasing_buys(self):
        snapshots = []
        for i in range(10):
            bid_qty = 1 + i
            snapshots.append(_make_snapshot(bids=[(100, bid_qty)], asks=[(101, 1)]))
        trend = compute_imbalance_trend(snapshots, window=10)
        assert trend is not None
        assert trend > 0  # increasing buy pressure

    def test_insufficient_data(self):
        trend = compute_imbalance_trend([], window=10)
        assert trend is None


class TestDepthImbalance:
    def test_within_range(self):
        ob = _make_snapshot(
            bids=[(99, 3), (100, 5)],  # only 100 is within 1% of mid=100
            asks=[(101, 2), (102, 6)],  # only 101 is within 1%
        )
        imb = compute_depth_imbalance_at_level(ob, depth_pct=0.01)
        # Close to mid: bid=5 at 100, ask=2 at 101 -> (5-2)/(5+2) = 0.428
        assert imb > 0


class TestOrderWalls:
    def test_no_walls(self):
        # Each level is 1/5 = 20% of total, but threshold is 50% (high)
        ob = _make_snapshot(
            bids=[(100, 1), (99, 1), (98, 1), (97, 1), (96, 1)],
            asks=[(101, 1), (102, 1)],
        )
        walls = detect_order_walls(ob, threshold_pct=0.5)
        assert walls["bid_wall"] is None
        assert walls["ask_wall"] is None

    def test_bid_wall(self):
        # total bid vol = 10, one level has 8 -> 80% > 5% threshold
        ob = _make_snapshot(bids=[(100, 8), (99, 2)], asks=[(101, 2)])
        walls = detect_order_walls(ob, threshold_pct=0.05)
        assert walls["bid_wall"] is not None
        assert walls["bid_wall"][0] == 100.0
        assert walls["bid_wall"][1] == 8.0


class TestOrderbookSignal:
    def test_compute_signal(self):
        ob = _make_snapshot(
            bids=[(100, 3), (99, 2), (98, 1), (97, 1), (96, 1)],
            asks=[(101, 1), (102, 1), (103, 1), (104, 1), (105, 2)],
        )
        signal = compute_orderbook_signal(ob, [])
        assert signal["symbol"] == "BTC/USDT"
        assert signal["mid_price"] == 100.5
        assert signal["imbalance"] > 0  # 8 bid vs 6 ask
        assert len(signal["top5_bids"]) == 5
        assert len(signal["top5_asks"]) == 5
