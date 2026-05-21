from datetime import UTC, datetime
from decimal import Decimal

import pytest

from crypto_trading.core.errors import RiskRuleViolation
from crypto_trading.core.types import (
    OrderSide,
    OrderType,
    Portfolio,
    Position,
    PositionSide,
    Signal,
)
from crypto_trading.risk.manager import RiskManager
from crypto_trading.risk.rules import (
    MaxDrawdownRule,
    MaxLeverageRule,
    MaxOpenPositionsRule,
    MinConfidenceRule,
    PositionSizeRule,
)


def _make_portfolio(equity: Decimal = Decimal("10000"), drawdown: float = 0.0) -> Portfolio:
    return Portfolio(
        total_equity=equity,
        free_balance=equity,
        peak_equity=equity * (Decimal("1") + Decimal(str(drawdown))),
        current_drawdown=Decimal(str(drawdown)),
    )


def _make_signal(
    symbol: str = "BTC/USDT",
    side: OrderSide = OrderSide.BUY,
    amount: Decimal = Decimal("0.1"),
    confidence: float = 0.8,
    leverage: int = 1,
) -> Signal:
    return Signal(
        symbol=symbol,
        side=side,
        amount=amount,
        confidence=confidence,
        leverage=leverage,
        order_type=OrderType.MARKET,
        timestamp=datetime.now(UTC).replace(tzinfo=None),
    )


class TestMaxDrawdownRule:
    def test_pass_when_drawdown_below_threshold(self):
        rule = MaxDrawdownRule(max_drawdown_pct=0.2)
        portfolio = _make_portfolio(drawdown=0.1)
        signal = _make_signal()
        result = rule.check(signal, portfolio)
        assert result is signal

    def test_reject_when_drawdown_exceeds_threshold(self):
        rule = MaxDrawdownRule(max_drawdown_pct=0.2)
        portfolio = _make_portfolio(drawdown=0.25)
        signal = _make_signal()
        with pytest.raises(RiskRuleViolation):
            rule.check(signal, portfolio)


class TestPositionSizeRule:
    def test_passes_when_amount_within_limit(self):
        rule = PositionSizeRule(max_position_pct=0.1)
        portfolio = _make_portfolio(Decimal("10000"))
        signal = _make_signal(amount=Decimal("500"))
        result = rule.check(signal, portfolio)
        assert result.amount == Decimal("500")

    def test_caps_amount_when_too_large(self):
        rule = PositionSizeRule(max_position_pct=0.1)
        portfolio = _make_portfolio(Decimal("10000"))
        signal = _make_signal(amount=Decimal("2000"))
        result = rule.check(signal, portfolio)
        assert result.amount == Decimal("1000")

    def test_rejects_zero_equity(self):
        rule = PositionSizeRule(max_position_pct=0.1)
        portfolio = _make_portfolio(Decimal("0"))
        signal = _make_signal()
        with pytest.raises(RiskRuleViolation):
            rule.check(signal, portfolio)


class TestMaxOpenPositionsRule:
    def test_pass_when_under_limit(self):
        rule = MaxOpenPositionsRule(max_positions=3)
        portfolio = _make_portfolio()
        signal = _make_signal(symbol="ETH/USDT")
        result = rule.check(signal, portfolio)
        assert result is signal

    def test_pass_when_adding_to_existing_position(self):
        rule = MaxOpenPositionsRule(max_positions=1)
        pos = Position(
            symbol="BTC/USDT",
            side=PositionSide.LONG,
            quantity=Decimal("0.1"),
            entry_price=Decimal("50000"),
            mark_price=Decimal("50000"),
            leverage=1,
            margin=Decimal("5000"),
        )
        portfolio = _make_portfolio()
        portfolio.positions["BTC/USDT"] = pos
        portfolio.positions["ETH/USDT"] = pos
        signal = _make_signal(symbol="BTC/USDT")
        result = rule.check(signal, portfolio)
        assert result is signal

    def test_reject_when_at_limit(self):
        rule = MaxOpenPositionsRule(max_positions=2)
        pos = Position(
            symbol="BTC/USDT",
            side=PositionSide.LONG,
            quantity=Decimal("0.1"),
            entry_price=Decimal("50000"),
            mark_price=Decimal("50000"),
            leverage=1,
            margin=Decimal("5000"),
        )
        portfolio = _make_portfolio()
        portfolio.positions["BTC/USDT"] = pos
        portfolio.positions["ETH/USDT"] = pos
        signal = _make_signal(symbol="SOL/USDT")
        with pytest.raises(RiskRuleViolation):
            rule.check(signal, portfolio)


class TestMinConfidenceRule:
    def test_pass_when_confidence_above_threshold(self):
        rule = MinConfidenceRule(min_confidence=0.5)
        signal = _make_signal(confidence=0.8)
        result = rule.check(signal, _make_portfolio())
        assert result is signal

    def test_reject_when_confidence_below_threshold(self):
        rule = MinConfidenceRule(min_confidence=0.5)
        signal = _make_signal(confidence=0.3)
        with pytest.raises(RiskRuleViolation):
            rule.check(signal, _make_portfolio())


class TestMaxLeverageRule:
    def test_pass_when_leverage_within_limit(self):
        rule = MaxLeverageRule(max_leverage=5)
        signal = _make_signal(leverage=3)
        result = rule.check(signal, _make_portfolio())
        assert result is signal

    def test_reject_when_leverage_exceeds_limit(self):
        rule = MaxLeverageRule(max_leverage=3)
        signal = _make_signal(leverage=5)
        with pytest.raises(RiskRuleViolation):
            rule.check(signal, _make_portfolio())


class TestRiskManager:
    def test_applies_rules_in_order(self):
        rules = [
            PositionSizeRule(max_position_pct=0.1),
            MinConfidenceRule(min_confidence=0.5),
        ]
        manager = RiskManager(rules)
        portfolio = _make_portfolio(Decimal("10000"))
        signal = _make_signal(amount=Decimal("2000"), confidence=0.8)
        result = manager.check_signal(signal, portfolio)
        assert result is not None
        assert result.amount == Decimal("1000")

    def test_returns_none_on_rejection(self):
        rules = [MaxDrawdownRule(max_drawdown_pct=0.1)]
        manager = RiskManager(rules)
        portfolio = _make_portfolio(drawdown=0.2)
        signal = _make_signal()
        result = manager.check_signal(signal, portfolio)
        assert result is None

    def test_returns_none_when_no_rules(self):
        manager = RiskManager()
        signal = _make_signal()
        result = manager.check_signal(signal, _make_portfolio())
        assert result is signal
