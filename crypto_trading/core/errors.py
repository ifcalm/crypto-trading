class TradingError(Exception):
    """Base exception for all trading-related errors."""


class ExchangeError(TradingError):
    """Error originating from the exchange (network, rate limit, etc.)."""


class OrderError(TradingError):
    """Order placement or management error."""


class InsufficientBalanceError(OrderError):
    """Not enough balance to place the order."""


class RiskRuleViolation(TradingError):  # noqa: N818
    """Signal rejected by a risk rule."""


class DataError(TradingError):
    """Data fetch or storage error."""


class ConfigurationError(TradingError):
    """Invalid configuration."""
