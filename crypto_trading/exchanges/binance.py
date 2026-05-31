from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

import ccxt.async_support as ccxt_async

from crypto_trading.core.errors import ExchangeError, OrderError
from crypto_trading.core.exchange import Exchange
from crypto_trading.core.types import (
    OHLCV,
    Balance,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    PositionSide,
    Ticker,
)


def _to_decimal(value: object | None, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    return Decimal(str(value))


_OTYPE_MAP: dict[str, OrderType] = {
    "market": OrderType.MARKET,
    "limit": OrderType.LIMIT,
    "stop": OrderType.STOP_LOSS,
    "stop_market": OrderType.STOP_LOSS,
    "stop_loss": OrderType.STOP_LOSS,
    "stop_loss_limit": OrderType.STOP_LOSS_LIMIT,
    "take_profit": OrderType.TAKE_PROFIT,
    "take_profit_market": OrderType.TAKE_PROFIT,
    "take_profit_limit": OrderType.TAKE_PROFIT_LIMIT,
    "trailing_stop_market": OrderType.STOP_LOSS,
}


def _parse_ordertype(raw_type: str) -> OrderType:
    """Parse ccxt order type string with fallback to MARKET for unknown values."""
    return _OTYPE_MAP.get(raw_type.lower(), OrderType.MARKET)


def _parse_ohlcv(raw: list[Any]) -> OHLCV:
    return OHLCV(
        timestamp=datetime.fromtimestamp(raw[0] / 1000, tz=UTC).replace(tzinfo=None),
        open=Decimal(str(raw[1])),
        high=Decimal(str(raw[2])),
        low=Decimal(str(raw[3])),
        close=Decimal(str(raw[4])),
        volume=Decimal(str(raw[5])),
    )


def _parse_ticker(raw: dict[str, Any]) -> Ticker:
    return Ticker(
        symbol=raw["symbol"],
        bid=_to_decimal(raw.get("bid")),
        ask=_to_decimal(raw.get("ask")),
        last=_to_decimal(raw.get("last")),
        volume=_to_decimal(raw.get("baseVolume") or raw.get("volume")),
        timestamp=datetime.fromtimestamp(raw["timestamp"] / 1000, tz=UTC).replace(tzinfo=None),
    )


def _parse_order(raw: dict[str, Any]) -> Order:
    return Order(
        id=raw.get("clientOrderId") or raw.get("id", ""),
        exchange_id=raw.get("id"),
        symbol=raw["symbol"],
        side=OrderSide.BUY if raw["side"] == "buy" else OrderSide.SELL,
        type=_parse_ordertype(raw.get("type", "")),
        price=_to_decimal(raw.get("price")) if raw.get("price") else None,
        stop_price=_to_decimal(raw.get("stopPrice")) if raw.get("stopPrice") else None,
        amount=_to_decimal(raw.get("amount")),
        filled=_to_decimal(raw.get("filled")),
        remaining=_to_decimal(raw.get("remaining")),
        status=OrderStatus(raw.get("status", "open")),
        cost=_to_decimal(raw.get("cost")),
        fee=raw.get("fee"),
        reduce_only=raw.get("reduceOnly", False),
        timestamp=datetime.fromtimestamp(raw["timestamp"] / 1000, tz=UTC).replace(tzinfo=None),
        last_update=datetime.now(UTC).replace(tzinfo=None),
    )


def _parse_position(raw: dict[str, Any]) -> Position:
    contracts = _to_decimal(raw.get("contracts") or raw.get("info", {}).get("positionAmt", "0"))
    entry_price = _to_decimal(raw.get("entryPrice"))
    mark_price = _to_decimal(raw.get("markPrice"))
    leverage = int(raw.get("leverage", 1))
    margin = _to_decimal(raw.get("initialMargin") or raw.get("margin", "0"))
    side = PositionSide.LONG if contracts > 0 else PositionSide.SHORT

    return Position(
        symbol=raw["symbol"],
        side=side,
        quantity=abs(contracts),
        entry_price=entry_price,
        mark_price=mark_price,
        leverage=leverage,
        margin=margin,
        unrealized_pnl=_to_decimal(raw.get("unrealizedPnl")),
        realized_pnl=_to_decimal(raw.get("realizedPnl")),
        liquidation_price=_to_decimal(raw.get("liquidationPrice"))
        if raw.get("liquidationPrice")
        else None,
    )


class BinanceExchange(Exchange):
    def __init__(
        self,
        api_key: str = "",
        secret_key: str = "",
        market_type: str = "futures",
        testnet: bool = False,
        proxy: str = "",
    ) -> None:
        self._market_type = market_type
        exchange_id = "binance"
        options: dict[str, Any] = {}

        if market_type == "futures":
            exchange_id = "binanceusdm"
            options["defaultType"] = "future"

        config: dict[str, Any] = {
            "apiKey": api_key,
            "secret": secret_key,
            "enableRateLimit": True,
            "options": options,
        }

        if proxy:
            parsed = proxy.rstrip("/")
            config.update(
                {
                    "proxies": {
                        "http": parsed,
                        "https": parsed,
                    },
                    "aiohttp_proxy": parsed,
                }
            )

        self._client: Any = getattr(ccxt_async, exchange_id)(config)

        if testnet:
            self._client.set_sandbox_mode(True)

    @property
    def name(self) -> str:
        return "binance"

    @property
    def market_type(self) -> str:
        return self._market_type

    @property
    def trading_fee(self) -> Decimal:
        if self._market_type == "futures":
            return Decimal("0.0004")  # taker fee
        return Decimal("0.001")

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        since: datetime | None = None,
        limit: int = 500,
    ) -> list[OHLCV]:
        since_ms: int | None = None
        if since is not None:
            since_ms = int(since.timestamp() * 1000)

        try:
            raw = await self._client.fetch_ohlcv(symbol, timeframe, since_ms, limit)
            return [_parse_ohlcv(r) for r in cast(list[list[Any]], raw)]
        except Exception as e:
            raise ExchangeError(f"Failed to fetch OHLCV for {symbol}: {e}") from e

    async def fetch_ticker(self, symbol: str) -> Ticker:
        try:
            raw = await self._client.fetch_ticker(symbol)
            return _parse_ticker(cast(dict[str, Any], raw))
        except Exception as e:
            raise ExchangeError(f"Failed to fetch ticker for {symbol}: {e}") from e

    async def fetch_tickers(self, symbols: list[str] | None = None) -> list[Ticker]:
        try:
            raw = await self._client.fetch_tickers(symbols)
            return [_parse_ticker(v) for v in cast(dict[str, dict[str, Any]], raw).values()]
        except Exception as e:
            raise ExchangeError(f"Failed to fetch tickers: {e}") from e

    async def fetch_markets(self) -> list[dict[str, Any]]:
        try:
            return cast(list[dict[str, Any]], await self._client.fetch_markets())
        except Exception as e:
            raise ExchangeError(f"Failed to fetch markets: {e}") from e

    async def fetch_balance(self) -> dict[str, Balance]:
        try:
            raw = cast(dict[str, Any], await self._client.fetch_balance())
            balances: dict[str, Balance] = {}

            free_dict = raw.get("free", {})
            total_dict = raw.get("total", {})
            used_dict = raw.get("used", {})
            # Futures: raw["info"] is a list of position dicts, not a balance dict
            info = raw.get("info", {})
            if isinstance(info, dict) and "balances" in info:
                for entry in cast(list[dict[str, Any]], info["balances"]):
                    asset = entry.get("asset", "")
                    free = _to_decimal(entry.get("free", 0))
                    locked = _to_decimal(entry.get("locked", 0))
                    if free > 0 or locked > 0:
                        balances[asset] = Balance(
                            asset=asset, total=free + locked, free=free, used=locked
                        )

            # Spot: use ccxt's parsed free/total/used dicts
            if not balances:
                for asset in free_dict:
                    total = _to_decimal(total_dict.get(asset, 0))
                    free = _to_decimal(free_dict.get(asset, 0))
                    used = _to_decimal(used_dict.get(asset, 0))
                    if total > 0 or free > 0:
                        balances[asset] = Balance(asset=asset, total=total, free=free, used=used)
            return balances
        except Exception as e:
            raise ExchangeError(f"Failed to fetch balance: {e}") from e

    async def create_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        amount: Decimal,
        price: Decimal | None = None,
        stop_price: Decimal | None = None,
        reduce_only: bool = False,
        params: dict[str, Any] | None = None,
    ) -> Order:
        try:
            merged_params: dict[str, Any] = params.copy() if params else {}
            if reduce_only:
                merged_params["reduceOnly"] = True

            raw = await self._client.create_order(
                symbol=symbol,
                type=order_type.value,
                side=side.value,
                amount=float(amount),
                price=float(price) if price is not None else None,
                params=merged_params,
            )
            return _parse_order(cast(dict[str, Any], raw))
        except Exception as e:
            raise OrderError(f"Failed to create order for {symbol}: {e}") from e

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        try:
            await self._client.cancel_order(order_id, symbol)
            return True
        except Exception:
            return False

    async def fetch_order(self, order_id: str, symbol: str) -> Order:
        try:
            raw = await self._client.fetch_order(order_id, symbol)
            return _parse_order(cast(dict[str, Any], raw))
        except Exception as e:
            raise OrderError(f"Failed to fetch order {order_id}: {e}") from e

    async def fetch_open_orders(self, symbol: str | None = None) -> list[Order]:
        try:
            raw = await self._client.fetch_open_orders(symbol)
            return [_parse_order(r) for r in cast(list[dict[str, Any]], raw)]
        except Exception as e:
            raise ExchangeError(f"Failed to fetch open orders: {e}") from e

    async def fetch_my_trades(
        self,
        symbol: str,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[Order]:
        try:
            since_ms: int | None = None
            if since is not None:
                since_ms = int(since.timestamp() * 1000)
            raw = await self._client.fetch_my_trades(symbol, since_ms, limit)
            return [_parse_order(r) for r in cast(list[dict[str, Any]], raw)]
        except Exception as e:
            raise ExchangeError(f"Failed to fetch trades: {e}") from e

    async def fetch_positions(self) -> list[Position]:
        if self._market_type == "spot":
            return []
        try:
            raw = await self._client.fetch_positions()
            return [
                _parse_position(p)
                for p in cast(list[dict[str, Any]], raw)
                if _to_decimal(p.get("contracts") or p.get("info", {}).get("positionAmt", "0")) != 0
            ]
        except Exception as e:
            raise ExchangeError(f"Failed to fetch positions: {e}") from e

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        if self._market_type == "futures":
            try:
                await self._client.set_leverage(leverage, symbol)
            except Exception as e:
                raise ExchangeError(f"Failed to set leverage for {symbol}: {e}") from e

    async def set_margin_mode(self, symbol: str, mode: str) -> None:
        if self._market_type == "futures":
            try:
                await self._client.set_margin_mode(mode, symbol)
            except Exception as e:
                raise ExchangeError(f"Failed to set margin mode for {symbol}: {e}") from e

    async def fetch_funding_rate(self, symbol: str) -> Decimal:
        if self._market_type == "futures":
            try:
                raw = await self._client.fetch_funding_rate(symbol)
                return Decimal(str(raw.get("fundingRate", 0)))
            except Exception:
                return Decimal("0")
        return Decimal("0")

    async def close(self) -> None:
        await self._client.close()
