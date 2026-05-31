"""Orderbook-derived indicators for LLM and rule-based strategies.

Each indicator function takes a list of OrderBookSnapshot (chronological order)
and returns computed values.
"""

from decimal import Decimal
from typing import Any

from crypto_trading.core.types import OrderBookSnapshot

OrderWall = tuple[float, float]


def compute_imbalance(snapshot: OrderBookSnapshot) -> float:
    """Bid/ask volume imbalance in [-1, 1]. Positive = more bids (buy pressure)."""
    return float(snapshot.imbalance)


def compute_imbalance_history(
    snapshots: list[OrderBookSnapshot],
) -> list[float]:
    """Imbalance time series."""
    return [float(s.imbalance) for s in snapshots]


def compute_imbalance_trend(
    snapshots: list[OrderBookSnapshot],
    window: int = 10,
) -> float | None:
    """Slope of imbalance over the last `window` snapshots. Positive = increasing buy pressure."""
    vals = compute_imbalance_history(snapshots)[-window:]
    if len(vals) < 2:
        return None
    n = len(vals)
    x_mean = (n - 1) / 2
    y_mean = sum(vals) / n
    num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(vals))
    den = sum((i - x_mean) ** 2 for i in range(n))
    if den == 0:
        return 0.0
    return num / den


def compute_spread_history(
    snapshots: list[OrderBookSnapshot],
) -> list[float]:
    """Spread percentage time series."""
    return [float(s.spread_pct) * 100 for s in snapshots]


def compute_depth_imbalance_at_level(
    snapshot: OrderBookSnapshot,
    depth_pct: float = 0.01,  # 1% from mid
) -> float:
    """Imbalance of resting orders within depth_pct of mid price.
    Captures near-touch liquidity rather than the full book.
    """
    if snapshot.mid_price == 0:
        return 0.0

    lo = snapshot.mid_price * (Decimal("1") - Decimal(str(depth_pct)))
    hi = snapshot.mid_price * (Decimal("1") + Decimal(str(depth_pct)))

    bid_vol = sum(level.quantity for level in snapshot.bids if level.price >= lo)
    ask_vol = sum(level.quantity for level in snapshot.asks if level.price <= hi)
    total = bid_vol + ask_vol
    if total == 0:
        return 0.0
    return float((bid_vol - ask_vol) / total)


def detect_order_walls(
    snapshot: OrderBookSnapshot,
    threshold_pct: float = 0.05,
) -> dict[str, OrderWall | None]:
    """Detect large quote walls.

    A "wall" is a single price level with quantity > threshold_pct * total side volume.
    Returns dict with "bid_wall" and "ask_wall" — each is (price, quantity) or None.
    """
    result: dict[str, OrderWall | None] = {"bid_wall": None, "ask_wall": None}

    total_bid = float(snapshot.bid_volume)
    for level in snapshot.bids:
        if total_bid > 0 and float(level.quantity) / total_bid > threshold_pct:
            result["bid_wall"] = (float(level.price), float(level.quantity))
            break

    total_ask = float(snapshot.ask_volume)
    for level in snapshot.asks:
        if total_ask > 0 and float(level.quantity) / total_ask > threshold_pct:
            result["ask_wall"] = (float(level.price), float(level.quantity))
            break

    return result


def compute_orderbook_signal(
    current: OrderBookSnapshot,
    history: list[OrderBookSnapshot],
) -> dict[str, Any]:
    """Compute a comprehensive signal dict from orderbook state.

    Designed to be serialized into an LLM prompt context.
    """
    # Imbalance
    imb = float(current.imbalance)
    imb_trend = compute_imbalance_trend(history + [current], window=10) if history else None
    near_imb = compute_depth_imbalance_at_level(current, depth_pct=0.005)

    # Spread
    spread_bps = float(current.spread_pct) * 10000

    # Walls
    walls = detect_order_walls(current)
    bid_wall = walls["bid_wall"]
    ask_wall = walls["ask_wall"]

    # Best bid/ask
    best_bid = float(current.best_bid)
    best_ask = float(current.best_ask)
    mid = float(current.mid_price)

    # Top 5 levels depth
    top5_bids = sum(float(level.quantity) for level in current.bids[:5])
    top5_asks = sum(float(level.quantity) for level in current.asks[:5])

    return {
        "symbol": current.symbol,
        "timestamp": current.timestamp.isoformat(),
        "mid_price": mid,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread_bps": round(spread_bps, 1),
        "imbalance": round(imb, 4),
        "imbalance_trend": round(imb_trend, 6) if imb_trend is not None else None,
        "near_touch_imbalance": round(near_imb, 4),
        "bid_wall": (
            {"price": round(bid_wall[0], 2), "qty": round(bid_wall[1], 4)} if bid_wall else None
        ),
        "ask_wall": (
            {"price": round(ask_wall[0], 2), "qty": round(ask_wall[1], 4)} if ask_wall else None
        ),
        "top5_bid_qty": round(top5_bids, 4),
        "top5_ask_qty": round(top5_asks, 4),
        "total_bid_qty": round(float(current.bid_volume), 4),
        "total_ask_qty": round(float(current.ask_volume), 4),
        "top5_bids": [
            {"price": float(level.price), "qty": float(level.quantity)}
            for level in current.bids[:5]
        ],
        "top5_asks": [
            {"price": float(level.price), "qty": float(level.quantity)}
            for level in current.asks[:5]
        ],
    }
