from crypto_trading.core.strategy import Strategy
from crypto_trading.strategies.ma_crossover import MACrossoverStrategy
from crypto_trading.strategies.rsi_reversal import RSIReversalStrategy

STRATEGY_REGISTRY: dict[str, type[Strategy]] = {
    "ma_crossover": MACrossoverStrategy,
    "rsi_reversal": RSIReversalStrategy,
}


def list_strategies() -> list[str]:
    return list(STRATEGY_REGISTRY.keys())


def get_strategy(name: str, symbols: list[str], params: dict) -> Strategy:
    cls = STRATEGY_REGISTRY.get(name)
    if cls is None:
        available = ", ".join(STRATEGY_REGISTRY.keys())
        raise ValueError(f"Unknown strategy '{name}'. Available: {available}")
    return cls(symbols=symbols, params=params)
