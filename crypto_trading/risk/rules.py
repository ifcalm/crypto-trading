from abc import ABC, abstractmethod
from decimal import Decimal

from crypto_trading.core.errors import RiskRuleViolation
from crypto_trading.core.types import Portfolio, Signal


class RiskRule(ABC):
    @abstractmethod
    def check(self, signal: Signal, portfolio: Portfolio) -> Signal:
        """Validate and potentially adjust a signal. Raises RiskRuleViolation on rejection."""

    @property
    @abstractmethod
    def name(self) -> str: ...


class MaxDrawdownRule(RiskRule):
    """Reject all signals if portfolio drawdown exceeds threshold."""

    def __init__(self, max_drawdown_pct: float):
        self.max_drawdown_pct = max_drawdown_pct

    @property
    def name(self) -> str:
        return "max_drawdown"

    def check(self, signal: Signal, portfolio: Portfolio) -> Signal:
        if float(portfolio.current_drawdown) >= self.max_drawdown_pct:
            raise RiskRuleViolation(
                f"Drawdown {float(portfolio.current_drawdown):.2%} exceeds "
                f"max {self.max_drawdown_pct:.2%}"
            )
        return signal


class PositionSizeRule(RiskRule):
    """Cap position size to a percentage of portfolio equity."""

    def __init__(self, max_position_pct: float):
        self.max_position_pct = max_position_pct

    @property
    def name(self) -> str:
        return "position_size"

    def check(self, signal: Signal, portfolio: Portfolio) -> Signal:
        if portfolio.total_equity <= 0:
            raise RiskRuleViolation("Portfolio equity is zero or negative")

        max_amount = portfolio.total_equity * Decimal(str(self.max_position_pct))

        if signal.amount > max_amount:
            signal.amount = max_amount

        if signal.amount <= 0:
            raise RiskRuleViolation("Position size capped to zero")

        return signal


class MaxOpenPositionsRule(RiskRule):
    """Reject if max open positions would be exceeded."""

    def __init__(self, max_positions: int):
        self.max_positions = max_positions

    @property
    def name(self) -> str:
        return "max_open_positions"

    def check(self, signal: Signal, portfolio: Portfolio) -> Signal:
        current_count = len(portfolio.positions)
        if signal.symbol not in portfolio.positions:
            if current_count >= self.max_positions:
                raise RiskRuleViolation(
                    f"Max open positions ({self.max_positions}) reached"
                )
        return signal


class MinConfidenceRule(RiskRule):
    """Reject signals with confidence below threshold."""

    def __init__(self, min_confidence: float):
        self.min_confidence = min_confidence

    @property
    def name(self) -> str:
        return "min_confidence"

    def check(self, signal: Signal, portfolio: Portfolio) -> Signal:
        if signal.confidence < self.min_confidence:
            raise RiskRuleViolation(
                f"Signal confidence {signal.confidence:.2f} below "
                f"minimum {self.min_confidence:.2f}"
            )
        return signal


class MaxLeverageRule(RiskRule):
    """Reject if signal leverage would exceed max allowed leverage."""

    def __init__(self, max_leverage: int):
        self.max_leverage = max_leverage

    @property
    def name(self) -> str:
        return "max_leverage"

    def check(self, signal: Signal, portfolio: Portfolio) -> Signal:
        if signal.leverage > self.max_leverage:
            raise RiskRuleViolation(
                f"Leverage {signal.leverage}x exceeds max {self.max_leverage}x"
            )
        return signal
