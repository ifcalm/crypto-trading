from pathlib import Path

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ExchangeConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BINANCE_")

    api_key: str = ""
    secret_key: str = ""
    testnet: bool = False
    proxy: str = ""  # e.g. http://127.0.0.1:7890


class HyperliquidConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HYPERLIQUID_")

    private_key: str = Field(default="", env="HYPERLIQUID_PRIVATE_KEY")
    wallet_address: str = ""
    testnet: bool = False
    vault_address: str = ""


class TradingConfig(BaseSettings):
    symbols: list[str] = ["BTC/USDT"]
    timeframes: list[str] = ["1h"]
    default_leverage: int = 1


class DataConfig(BaseSettings):
    parquet_dir: str = "data/parquet"
    database_url: str = "sqlite+aiosqlite:///data/trading.db"


class ScreenerConfig(BaseSettings):
    enabled: bool = False
    min_volume_usdt: float = 10_000_000
    quote_currency: str = "USDT"
    max_symbols: int = 20


class RiskConfig(BaseSettings):
    max_drawdown_pct: float = 0.2
    max_position_pct: float = 0.1
    max_open_positions: int = 5
    min_confidence: float = 0.5
    max_leverage: int = 3


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    market_type: str = "futures"
    exchange: ExchangeConfig = Field(default_factory=ExchangeConfig)
    hyperliquid: HyperliquidConfig = Field(default_factory=HyperliquidConfig)
    trading: TradingConfig = Field(default_factory=TradingConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    screener: ScreenerConfig = Field(default_factory=ScreenerConfig)
    strategy_params: dict = Field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: str = "config.yaml") -> "Settings":
        config_path = Path(path)
        if not config_path.exists():
            return cls()

        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

        exchange_data = data.pop("exchange", {})
        trading_data = data.pop("trading", {})
        data_data = data.pop("data", {})
        risk_data = data.pop("risk", {})
        screener_data = data.pop("screener", {})

        return cls(
            market_type=data.pop("market_type", "futures"),
            exchange=ExchangeConfig(**exchange_data.get("binance", {})),
            hyperliquid=HyperliquidConfig(**exchange_data.get("hyperliquid", {})),
            trading=TradingConfig(**trading_data),
            data=DataConfig(**data_data),
            risk=RiskConfig(**risk_data),
            screener=ScreenerConfig(**screener_data),
            strategy_params=data.get("strategy_params", {}),
        )


def load_settings(config_path: str | None = None) -> Settings:
    path = config_path or "config.yaml"
    return Settings.from_yaml(path)
