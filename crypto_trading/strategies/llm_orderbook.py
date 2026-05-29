"""Two-layer LLM orderbook strategy.

Strategic layer (15min): Determine market direction (LONG/SHORT/HOLD)
Tactical layer (5min):  Time entries when direction is active

See docs/ORDERBOOK_STRATEGY.md for the full design rationale.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal

from crypto_trading.core.strategy import Strategy
from crypto_trading.core.types import OHLCV, OrderBookSnapshot, OrderSide, OrderType, Signal
from crypto_trading.indicators.orderbook import compute_orderbook_signal
from crypto_trading.strategies.llm_client import LLMClient

DIRECTION_PROMPT = """You are a crypto market microstructure analyst. Analyze orderbook depth data to determine short-term market direction (next 15 minutes).

## Input explanation
- **orderbook_snapshot**: Current top 5 bid/ask levels with quantities
- **imbalance**: (bid_vol - ask_vol) / total_vol. Positive = buying pressure
- **imbalance_trend**: Slope of imbalance over last 10 snapshots. Positive = buying pressure increasing
- **near_touch_imbalance**: Imbalance within 0.5% of mid price — near-touch liquidity
- **bid_wall / ask_wall**: Large concentrated resting orders at a single price level
- **spread_bps**: Spread in basis points (1 bp = 0.01%)
- **ohlcv_context**: Recent 15-min candles with OHLCV

## How to analyze
- Increasing bid depth + positive imbalance trend → building buy pressure → LONG
- Increasing ask depth + negative imbalance trend → building sell pressure → SHORT
- Large bid wall near price may act as support; large ask wall as resistance
- Thin near-touch liquidity on one side may indicate imminent price move through that side
- Narrowing spread often precedes a move; widening spread suggests uncertainty

## Output format
Reply ONLY with a JSON object, no other text:
{"direction": "LONG", "confidence": 0.75, "reasoning": "Imbalance shifting positive with bid wall support at 50000"}

direction must be: LONG, SHORT, or HOLD
confidence: 0.0 (random) to 1.0 (certain)
reasoning: one sentence explaining the signal
"""

ENTRY_PROMPT = """You are a crypto execution timing analyst. You have a confirmed market direction and need to find the best entry.

## Context
- **direction**: The confirmed market direction from strategic analysis
- **orderbook_snapshot**: Current top 5 bid/ask levels
- **imbalance**: Current volume imbalance
- **imbalance_trend**: Recent imbalance trend
- **near_touch_imbalance**: Near-touch liquidity balance

## How to analyze
- LONG entry: Favorable when ask side shows weakness (thin ask levels, ask volume decreasing, imbalance shifting positive). Avoid if large ask wall is absorbing all buy pressure.
- SHORT entry: Favorable when bid side shows weakness (thin bid levels, bid volume decreasing). Avoid if large bid wall is providing strong support.
- If the microstructure contradicts the strategic direction, HOLD and wait for a better entry.
- Reduce size when signals are mixed; increase when microstructure strongly aligns with direction.

## Output format
Reply ONLY with a JSON object, no other text:
{"action": "ENTER", "size_modifier": 1.0, "reasoning": "Ask side thinning — good LONG entry"}

action: ENTER or HOLD
size_modifier: 0.5 (half size) to 1.5 (1.5x size). 1.0 = normal
reasoning: one sentence
"""


class LLMOrderbookStrategy(Strategy):
    """Two-layer LLM-based strategy using orderbook depth data.

    Parameters:
        strategic_tf: Timeframe for direction decisions (default "15m")
        tactical_tf: Timeframe for entry decisions (default "5m")
        base_amount: Base position size in quote currency
        direction_ttl_minutes: How long a direction signal remains valid
        model: Anthropic model to use
        max_daily_calls: Max LLM calls per day (cost control)
    """

    def __init__(
        self,
        symbols: list[str],
        params: dict | None = None,
    ):
        super().__init__(symbols, params)
        p = params or {}

        self.strategic_tf = p.get("strategic_tf", "15m")
        self.tactical_tf = p.get("tactical_tf", "5m")
        self.base_amount = Decimal(str(p.get("base_amount", "0.01")))
        self.direction_ttl = int(p.get("direction_ttl_minutes", 15))
        self.model = p.get("model", "deepseek-v4-pro")
        self.max_daily_calls = int(p.get("max_daily_calls", 200))

        self.llm = LLMClient(model=self.model)

        # Orderbook history: symbol -> list of snapshots
        self._ob_history: dict[str, list[OrderBookSnapshot]] = defaultdict(list)
        self._max_ob_history = 30

        # Current direction: symbol -> dict
        self._direction: dict[str, dict] = {}

        # Track LLM call counts per day
        self._call_count = 0
        self._call_day = datetime.now(UTC).date()

    # ─── public API ───────────────────────────────────────────────────────

    def on_orderbook(self, snapshot: OrderBookSnapshot) -> None:
        """Feed an orderbook snapshot to the strategy. Call from the runner."""
        hist = self._ob_history[snapshot.symbol]
        hist.append(snapshot)
        if len(hist) > self._max_ob_history:
            self._ob_history[snapshot.symbol] = hist[-self._max_ob_history :]

    async def on_bar(self, symbol: str, bar: OHLCV) -> Signal | None:
        """Process a bar event. Routes to strategic or tactical layer based on timeframe."""
        tf = self._infer_timeframe(bar)
        if tf is None:
            return None

        # Check if we should run strategic layer
        if tf == self.strategic_tf:
            return await self._strategic_layer(symbol, bar)

        # Check if we should run tactical layer
        if tf == self.tactical_tf and symbol in self._direction:
            direction_info = self._direction[symbol]
            age = (bar.timestamp - direction_info["timestamp"]).total_seconds() / 60
            if age <= self.direction_ttl:
                return await self._tactical_layer(symbol, bar, direction_info)

        return None

    # ─── layer implementation ─────────────────────────────────────────────

    async def _strategic_layer(self, symbol: str, bar: OHLCV) -> Signal | None:
        """Determine market direction from orderbook structure."""
        ob_snapshots = self._ob_history.get(symbol, [])
        if not ob_snapshots:
            return None

        if not self._check_call_limit():
            return None

        current_ob = ob_snapshots[-1]
        ob_signal = compute_orderbook_signal(current_ob, ob_snapshots[:-1])

        # Build OHLCV context
        bars = self._get_bars(symbol, 6)
        ohlcv_context = self._format_ohlcv_context(bars)

        user_prompt = self._format_direction_prompt(ob_signal, ohlcv_context)

        try:
            result = await self.llm.analyze(
                system_prompt=DIRECTION_PROMPT,
                user_prompt=user_prompt,
                temperature=0.3,
                max_tokens=256,
            )
        except Exception:
            return None

        self._call_count += 1

        direction = result.get("direction", "HOLD")
        confidence = float(result.get("confidence", 0))
        reasoning = result.get("reasoning", "")

        if direction not in ("LONG", "SHORT"):
            self._direction.pop(symbol, None)
            return None

        # Store direction for tactical layer
        self._direction[symbol] = {
            "direction": direction,
            "confidence": confidence,
            "timestamp": bar.timestamp,
            "reasoning": reasoning,
        }

        # Strategic layer does NOT generate signals directly —
        # it only sets direction. Tactical layer does entries.
        return None

    async def _tactical_layer(
        self,
        symbol: str,
        bar: OHLCV,
        direction_info: dict,
    ) -> Signal | None:
        """Decide entry timing based on current microstructure."""
        ob_snapshots = self._ob_history.get(symbol, [])
        if not ob_snapshots:
            return None

        if not self._check_call_limit():
            return None

        current_ob = ob_snapshots[-1]
        ob_signal = compute_orderbook_signal(current_ob, ob_snapshots[:-1])

        user_prompt = self._format_entry_prompt(direction_info, ob_signal)

        try:
            result = await self.llm.analyze(
                system_prompt=ENTRY_PROMPT,
                user_prompt=user_prompt,
                temperature=0.3,
                max_tokens=256,
            )
        except Exception:
            return None

        self._call_count += 1

        action = result.get("action", "HOLD")
        size_modifier = float(result.get("size_modifier", 1.0))

        if action != "ENTER":
            return None

        side = OrderSide.BUY if direction_info["direction"] == "LONG" else OrderSide.SELL
        amount = self.base_amount * Decimal(str(max(0.5, min(1.5, size_modifier))))

        return Signal(
            symbol=symbol,
            side=side,
            amount=amount,
            confidence=direction_info.get("confidence", 0.7) * min(size_modifier, 1.0),
            order_type=OrderType.MARKET,
            metadata={
                "direction": direction_info["direction"],
                "direction_reasoning": direction_info.get("reasoning", ""),
                "entry_reasoning": result.get("reasoning", ""),
                "strategic_tf": self.strategic_tf,
                "tactical_tf": self.tactical_tf,
            },
        )

    # ─── helpers ───────────────────────────────────────────────────────────

    def _infer_timeframe(self, bar: OHLCV) -> str | None:
        """Read timeframe from bar metadata (set by BinanceWebSocket from kline data)."""
        if bar.metadata:
            tf = bar.metadata.get("timeframe")
            if tf:
                return tf
        return None

    def _check_call_limit(self) -> bool:
        """Enforce max daily LLM calls for cost control."""
        today = datetime.now(UTC).date()
        if self._call_day != today:
            self._call_count = 0
            self._call_day = today
        if self._call_count >= self.max_daily_calls:
            return False
        return True

    def _format_direction_prompt(self, ob_signal: dict, ohlcv_context: str) -> str:
        parts = [
            "## Orderbook Analysis",
            f"Symbol: {ob_signal['symbol']}",
            f"Mid Price: {ob_signal['mid_price']:.2f}",
            f"Spread: {ob_signal['spread_bps']:.1f} bps",
            f"Imbalance: {ob_signal['imbalance']:.4f}",
            f"Imbalance Trend: {ob_signal['imbalance_trend']}",
            f"Near-touch Imbalance: {ob_signal['near_touch_imbalance']:.4f}",
            f"Bid Wall: {ob_signal['bid_wall']}",
            f"Ask Wall: {ob_signal['ask_wall']}",
            "",
            "## Top 5 Bids",
        ]
        for i, level in enumerate(ob_signal.get("top5_bids", []), 1):
            parts.append(f"  {i}. Price={level['price']:.2f}  Qty={level['qty']:.4f}")

        parts.append("")
        parts.append("## Top 5 Asks")
        for i, level in enumerate(ob_signal.get("top5_asks", []), 1):
            parts.append(f"  {i}. Price={level['price']:.2f}  Qty={level['qty']:.4f}")

        parts.append("")
        parts.append("## OHLCV Context (recent 15-min bars)")
        parts.append(ohlcv_context)

        parts.append("")
        parts.append(
            "Determine market direction (LONG/SHORT/HOLD) for the next 15 minutes. "
            "Reply with JSON only."
        )
        return "\n".join(parts)

    def _format_entry_prompt(self, direction_info: dict, ob_signal: dict) -> str:
        parts = [
            f"## Strategic Direction: {direction_info['direction']}",
            f"Confidence: {direction_info.get('confidence', 'N/A')}",
            f"Reasoning: {direction_info.get('reasoning', 'N/A')}",
            "",
            "## Current Microstructure",
            f"Symbol: {ob_signal['symbol']}",
            f"Mid Price: {ob_signal['mid_price']:.2f}",
            f"Imbalance: {ob_signal['imbalance']:.4f}",
            f"Imbalance Trend: {ob_signal['imbalance_trend']}",
            f"Near-touch Imbalance: {ob_signal['near_touch_imbalance']:.4f}",
            f"Bid Wall: {ob_signal['bid_wall']}",
            f"Ask Wall: {ob_signal['ask_wall']}",
            "",
            "## Top 5 Bids",
        ]
        for i, level in enumerate(ob_signal.get("top5_bids", []), 1):
            parts.append(f"  {i}. Price={level['price']:.2f}  Qty={level['qty']:.4f}")

        parts.append("")
        parts.append("## Top 5 Asks")
        for i, level in enumerate(ob_signal.get("top5_asks", []), 1):
            parts.append(f"  {i}. Price={level['price']:.2f}  Qty={level['qty']:.4f}")

        parts.append("")
        parts.append(
            f"Given confirmed {direction_info['direction']} direction, "
            "should we ENTER now or HOLD? Reply with JSON only."
        )
        return "\n".join(parts)

    @staticmethod
    def _format_ohlcv_context(bars: list[OHLCV]) -> str:
        if not bars:
            return "No OHLCV data available"

        lines = []
        for b in bars[-6:]:
            lines.append(
                f"  {b.timestamp.strftime('%H:%M')} "
                f"O={float(b.open):.2f} H={float(b.high):.2f} "
                f"L={float(b.low):.2f} C={float(b.close):.2f} "
                f"V={float(b.volume):.2f}"
            )
        return "\n".join(lines)
