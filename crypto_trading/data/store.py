from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

from crypto_trading.core.types import OHLCV


class ParquetStore:
    def __init__(self, base_dir: str = "data/parquet"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _get_path(self, symbol: str, timeframe: str) -> Path:
        safe_symbol = symbol.replace("/", "_")
        subdir = self.base_dir / safe_symbol
        subdir.mkdir(parents=True, exist_ok=True)
        return subdir / f"{timeframe}.parquet"

    def write_ohlcv(self, symbol: str, timeframe: str, bars: list[OHLCV]) -> int:
        if not bars:
            return 0

        new_df = pd.DataFrame([
            {
                "timestamp": b.timestamp,
                "open": float(b.open),
                "high": float(b.high),
                "low": float(b.low),
                "close": float(b.close),
                "volume": float(b.volume),
            }
            for b in bars
        ])
        new_df["timestamp"] = pd.to_datetime(new_df["timestamp"])

        path = self._get_path(symbol, timeframe)
        if path.exists():
            existing = pd.read_parquet(path)
            combined = pd.concat([existing, new_df], ignore_index=True)
            combined = combined.drop_duplicates(subset="timestamp", keep="last")
            combined = combined.sort_values("timestamp")
        else:
            combined = new_df

        combined.to_parquet(path, index=False, compression="zstd")
        return len(combined)

    def read_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[OHLCV]:
        path = self._get_path(symbol, timeframe)
        if not path.exists():
            return []

        df = pd.read_parquet(path)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        mask = (df["timestamp"] >= pd.Timestamp(start)) & (df["timestamp"] <= pd.Timestamp(end))
        df = df[mask].sort_values("timestamp")

        return [
            OHLCV(
                timestamp=row["timestamp"].to_pydatetime(),
                open=Decimal(str(row["open"])),
                high=Decimal(str(row["high"])),
                low=Decimal(str(row["low"])),
                close=Decimal(str(row["close"])),
                volume=Decimal(str(row["volume"])),
            )
            for row in df.to_dict("records")
        ]

    def get_date_range(self, symbol: str, timeframe: str) -> tuple[datetime, datetime] | None:
        path = self._get_path(symbol, timeframe)
        if not path.exists():
            return None

        df = pd.read_parquet(path, columns=["timestamp"])
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        earliest = df["timestamp"].min().to_pydatetime()
        latest = df["timestamp"].max().to_pydatetime()
        return earliest, latest
