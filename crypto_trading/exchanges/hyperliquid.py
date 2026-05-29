"""Hyperliquid exchange adapter — implements the Exchange interface using the HL SDK.

Hyperliquid is a purpose-built L1 for derivatives trading. Its Python SDK provides
a REST API (Info + Exchange) and a WebSocket subscription system.

Key differences from Binance:
- Auth uses an Ethereum private key (no API key/secret)
- Symbol naming: "BTC" not "BTC/USDT"
- Market orders use market_open/market_close, not order(limit_px=0)
- Taker fee: 0.025% (0.00025), maker rebate ~0.01%
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import eth_account
from hyperliquid.exchange import Exchange as HLExchange
from hyperliquid.info import Info as HLInfo
from hyperliquid.utils import constants as hl_constants
from hyperliquid.utils.signing import OrderType as HLOrderType

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

# ─── symbol mapping ────────────────────────────────────────────────────


def _to_hl_symbol(symbol: str) -> str:
    """BTC/USDT -> BTC"""
    return symbol.split("/")[0].upper()


def _from_hl_symbol(hl_name: str) -> str:
    """BTC -> BTC/USDT"""
    return f"{hl_name.upper()}/USDT"


def _to_decimal(value, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    return Decimal(str(value))


# ─── order type mapping ─────────────────────────────────────────────────

_LIMIT = HLOrderType(limit={"tif": "Gtc"}, trigger=None)
_IOC = HLOrderType(limit={"tif": "Ioc"}, trigger=None)  # market-ish


def _to_hl_order_type(ot: OrderType) -> HLOrderType:
    if ot == OrderType.LIMIT:
        return _LIMIT
    if ot == OrderType.MARKET:
        return _IOC
    # Hyperliquid doesn't natively support stop-loss/take-profit as order types;
    # those are handled via trigger orders. Fall back to limit for now.
    return _LIMIT


# ─── order status mapping ───────────────────────────────────────────────


def _parse_order_status(raw: dict) -> OrderStatus:
    status = raw.get("status", "")
    # Hyperliquid statuses: "open", "filled", "canceled", "rejected"
    if status == "open":
        return OrderStatus.OPEN
    if status == "filled":
        return OrderStatus.CLOSED
    if status == "canceled":
        return OrderStatus.CANCELED
    if status == "rejected":
        return OrderStatus.REJECTED
    return OrderStatus.PENDING


# ─── exchange adapter ───────────────────────────────────────────────────


class HyperliquidExchange(Exchange):
    """Hyperliquid perpetual futures exchange.

    Auth uses an Ethereum private key. Set env HYPERLIQUID_PRIVATE_KEY,
    or pass private_key + wallet_address explicitly.

    market_type is always "futures" — Hyperliquid's spot is separate
    and this adapter targets perps.
    """

    def __init__(
        self,
        private_key: str = "",
        wallet_address: str = "",
        market_type: str = "futures",
        testnet: bool = False,
        vault_address: str | None = None,
    ):
        self._market_type = market_type if market_type in ("futures", "spot") else "futures"
        self._testnet = testnet

        base_url = hl_constants.TESTNET_API_URL if testnet else hl_constants.MAINNET_API_URL

        if not private_key:
            raise ValueError("Hyperliquid requires a private_key for order signing")

        self._address = wallet_address or eth_account.Account.from_key(private_key).address
        self._info = HLInfo(base_url, skip_ws=True)
        self._exchange = HLExchange(
            wallet=self._address,
            secret_key=private_key,
            base_url=base_url,
            vault_address=vault_address,
        )

        # Cache asset metadata for quick lookups
        self._meta_cache: dict | None = None
        self._sz_decimals: dict[str, int] = {}

    # ─── properties ──────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "hyperliquid"

    @property
    def market_type(self) -> str:
        return self._market_type

    @property
    def trading_fee(self) -> Decimal:
        # Hyperliquid taker: 2.5 bps, maker: -0.2 bps (rebate)
        return Decimal("0.00025")

    # ─── market data ─────────────────────────────────────────────────

    async def _ensure_meta(self) -> dict:
        if self._meta_cache is None:
            self._meta_cache = self._info.meta()
            for asset in self._meta_cache["universe"]:
                name = asset["name"]
                self._sz_decimals[name] = int(asset.get("szDecimals", 0))
        return self._meta_cache

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        since: datetime | None = None,
        limit: int = 500,
    ) -> list[OHLCV]:
        try:
            await self._ensure_meta()
            hl_name = _to_hl_symbol(symbol)
            start_ms = int(since.timestamp() * 1000) if since else 0
            end_ms = int(datetime.now(UTC).timestamp() * 1000)

            raw = self._info.candles_snapshot(hl_name, timeframe, start_ms, end_ms)

            if not raw:
                return []

            bars = []
            for candle in raw[-limit:]:
                bars.append(
                    OHLCV(
                        timestamp=datetime.fromtimestamp(candle["t"] / 1000, tz=UTC).replace(
                            tzinfo=None
                        ),
                        open=Decimal(str(candle["o"])),
                        high=Decimal(str(candle["h"])),
                        low=Decimal(str(candle["l"])),
                        close=Decimal(str(candle["c"])),
                        volume=Decimal(str(candle["v"])),
                        symbol=symbol,
                    )
                )
            return bars
        except Exception as e:
            raise ExchangeError(f"Failed to fetch OHLCV for {symbol}: {e}") from e

    async def fetch_ticker(self, symbol: str) -> Ticker:
        try:
            await self._ensure_meta()
            mids = self._info.all_mids()
            hl_name = _to_hl_symbol(symbol)
            mid = _to_decimal(mids.get(hl_name, 0))

            # Hyperliquid all_mids returns mark price; bid/ask from L2
            l2 = self._info.l2_snapshot(hl_name)
            best_bid = (
                _to_decimal(l2["levels"][0][0]["px"])
                if l2.get("levels") and l2["levels"][0]
                else mid
            )
            best_ask = (
                _to_decimal(l2["levels"][1][0]["px"])
                if l2.get("levels") and len(l2["levels"]) > 1 and l2["levels"][1]
                else mid
            )

            return Ticker(
                symbol=symbol,
                bid=best_bid,
                ask=best_ask,
                last=mid,
                volume=_to_decimal("0"),  # volume not in mids
                timestamp=datetime.now(UTC).replace(tzinfo=None),
            )
        except Exception as e:
            raise ExchangeError(f"Failed to fetch ticker for {symbol}: {e}") from e

    async def fetch_tickers(self, symbols: list[str] | None = None) -> list[Ticker]:
        try:
            await self._ensure_meta()
            mids = self._info.all_mids()

            tickers = []
            for hl_name, mid in mids.items():
                sym = _from_hl_symbol(hl_name)
                if symbols and sym not in symbols:
                    continue
                mid_d = _to_decimal(mid)
                tickers.append(
                    Ticker(
                        symbol=sym,
                        bid=mid_d,
                        ask=mid_d,
                        last=mid_d,
                        volume=_to_decimal("0"),
                        timestamp=datetime.now(UTC).replace(tzinfo=None),
                    )
                )
            return tickers
        except Exception as e:
            raise ExchangeError(f"Failed to fetch tickers: {e}") from e

    async def fetch_markets(self) -> list[dict]:
        try:
            meta = await self._ensure_meta()
            result = []
            for asset in meta["universe"]:
                result.append(
                    {
                        "symbol": _from_hl_symbol(asset["name"]),
                        "base": asset["name"],
                        "quote": "USDC",
                        "max_leverage": asset.get("maxLeverage", 0),
                        "sz_decimals": asset.get("szDecimals", 0),
                    }
                )
            return result
        except Exception as e:
            raise ExchangeError(f"Failed to fetch markets: {e}") from e

    async def fetch_balance(self) -> dict[str, Balance]:
        try:
            state = self._info.user_state(self._address)
            margin = state.get("marginSummary", {})
            usd = float(margin.get("accountValue", "0"))

            return {
                "USDC": Balance(
                    asset="USDC",
                    total=_to_decimal(str(usd)),
                    free=_to_decimal(str(usd)),
                    used=_to_decimal("0"),
                )
            }
        except Exception as e:
            raise ExchangeError(f"Failed to fetch balance: {e}") from e

    # ─── orders ──────────────────────────────────────────────────────

    async def create_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        amount: Decimal,
        price: Decimal | None = None,
        stop_price: Decimal | None = None,
        reduce_only: bool = False,
        params: dict | None = None,
    ) -> Order:
        try:
            hl_name = _to_hl_symbol(symbol)
            is_buy = side == OrderSide.BUY

            if order_type == OrderType.MARKET:
                slippage = float(params.get("slippage", 0.05) if params else 0.05)
                if is_buy:
                    result = self._exchange.market_open(
                        hl_name,
                        is_buy=True,
                        sz=float(amount),
                        px=float(price) if price else None,
                        slippage=slippage,
                    )
                else:
                    result = self._exchange.market_close(
                        hl_name,
                        sz=float(amount),
                        px=float(price) if price else None,
                        slippage=slippage,
                    )
            else:
                result = self._exchange.order(
                    name=hl_name,
                    is_buy=is_buy,
                    sz=float(amount),
                    limit_px=float(price) if price else 0,
                    order_type=_to_hl_order_type(order_type),
                    reduce_only=reduce_only,
                )

            return self._parse_order_result(result, symbol, side, order_type, amount)
        except Exception as e:
            raise OrderError(f"Failed to create order for {symbol}: {e}") from e

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        try:
            hl_name = _to_hl_symbol(symbol)
            self._exchange.cancel(hl_name, int(order_id))
            return True
        except Exception:
            return False

    async def fetch_order(self, order_id: str, symbol: str) -> Order:
        try:
            result = self._info.query_order_by_oid(self._address, int(order_id))
            return self._parse_order_result(result, symbol)
        except Exception as e:
            raise OrderError(f"Failed to fetch order {order_id}: {e}") from e

    async def fetch_open_orders(self, symbol: str | None = None) -> list[Order]:
        try:
            raw_orders = self._info.open_orders(self._address)
            orders = []
            for raw in raw_orders:
                coin = raw.get("coin", "")
                sym = _from_hl_symbol(coin)
                if symbol and sym != symbol:
                    continue
                orders.append(self._parse_order_result(raw, sym))
            return orders
        except Exception as e:
            raise ExchangeError(f"Failed to fetch open orders: {e}") from e

    async def fetch_my_trades(
        self,
        symbol: str,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[Order]:
        try:
            fills = self._info.user_fills(self._address)
            trades = []
            for f in fills[-limit:]:
                coin = f.get("coin", "")
                sym = _from_hl_symbol(coin)
                if sym != symbol:
                    continue
                oid = str(f.get("oid", ""))
                trades.append(
                    Order(
                        id=oid,
                        exchange_id=oid,
                        symbol=sym,
                        side=OrderSide.BUY
                        if f.get("dir", "").startswith("B") or f.get("dir") == "Open Long"
                        else OrderSide.SELL,
                        type=OrderType.MARKET,
                        amount=_to_decimal(f.get("sz", 0)),
                        price=_to_decimal(f.get("px", 0)),
                        filled=_to_decimal(f.get("sz", 0)),
                        status=OrderStatus.CLOSED,
                        cost=_to_decimal(f.get("notional", 0)),
                        fee={"cost": float(_to_decimal(f.get("fee", 0))), "currency": "USDC"},
                        timestamp=datetime.fromtimestamp(f["time"] / 1000, tz=UTC).replace(
                            tzinfo=None
                        ),
                    )
                )
            return trades
        except Exception as e:
            raise ExchangeError(f"Failed to fetch trades for {symbol}: {e}") from e

    # ─── positions ───────────────────────────────────────────────────

    async def fetch_positions(self) -> list[Position]:
        if self._market_type == "spot":
            return []
        try:
            state = self._info.user_state(self._address)
            positions = []
            for pos in state.get("assetPositions", []):
                pos_info = pos.get("position", {})
                coin = pos_info.get("coin", "")
                size = float(pos_info.get("szi", "0"))
                if size == 0:
                    continue

                sym = _from_hl_symbol(coin)
                entry_px = _to_decimal(pos_info.get("entryPx", 0))
                leverage_val = int(pos_info.get("leverage", {}).get("value", 1))
                upnl = _to_decimal(pos_info.get("unrealizedPnl", 0))
                liq_px = (
                    _to_decimal(pos_info.get("liquidationPx"))
                    if pos_info.get("liquidationPx")
                    else None
                )

                positions.append(
                    Position(
                        symbol=sym,
                        side=PositionSide.LONG if size > 0 else PositionSide.SHORT,
                        quantity=_to_decimal(str(abs(size))),
                        entry_price=entry_px,
                        mark_price=entry_px,  # Hyperliquid doesn't give mark directly in user_state
                        leverage=leverage_val if leverage_val > 0 else 1,
                        margin=_to_decimal(pos_info.get("marginUsed", "0")),
                        unrealized_pnl=upnl,
                        liquidation_price=liq_px,
                    )
                )
            return positions
        except Exception as e:
            raise ExchangeError(f"Failed to fetch positions: {e}") from e

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        try:
            hl_name = _to_hl_symbol(symbol)
            self._exchange.update_leverage(leverage, hl_name, is_cross=True)
        except Exception as e:
            raise ExchangeError(f"Failed to set leverage for {symbol}: {e}") from e

    async def set_margin_mode(self, symbol: str, mode: str) -> None:
        hl_name = _to_hl_symbol(symbol)
        # Re-set leverage with appropriate cross/isolated flag
        # get current leverage first, or default to 1
        is_cross = mode.lower() != "isolated"
        try:
            self._exchange.update_leverage(1, hl_name, is_cross=is_cross)
        except Exception as e:
            raise ExchangeError(f"Failed to set margin mode for {symbol}: {e}") from e

    async def fetch_funding_rate(self, symbol: str) -> Decimal:
        try:
            await self._ensure_meta()
            # Meta includes funding info per asset
            for asset in self._meta_cache["universe"]:
                if asset["name"].upper() == _to_hl_symbol(symbol).upper():
                    return _to_decimal(asset.get("funding", 0))
            return Decimal("0")
        except Exception:
            return Decimal("0")

    # ─── helpers ─────────────────────────────────────────────────────

    def _parse_order_result(
        self,
        raw: dict,
        symbol: str,
        side: OrderSide | None = None,
        order_type: OrderType | None = None,
        amount: Decimal | None = None,
    ) -> Order:
        # Derive side from raw dict if not provided
        if side is None:
            raw_side = raw.get("side", "")
            if raw_side == "B" or str(raw_side).startswith("B"):
                side = OrderSide.BUY
            else:
                side = OrderSide.SELL

        if order_type is None:
            raw_type = raw.get("orderType", "")
            if "limit" in str(raw_type).lower() or raw.get("limitPx"):
                order_type = OrderType.LIMIT
            else:
                order_type = OrderType.MARKET

        sz = _to_decimal(raw.get("sz", 0))
        if amount is None:
            amount = sz

        status_text = raw.get("status", "open")
        if status_text == "open":
            status = OrderStatus.OPEN
        elif status_text == "filled":
            status = OrderStatus.CLOSED
        elif status_text == "canceled":
            status = OrderStatus.CANCELED
        else:
            status = OrderStatus.PENDING

        oid = str(raw.get("oid", raw.get("orderId", "")))
        filled = _to_decimal(raw.get("filled", sz if status == OrderStatus.CLOSED else 0))
        price_val = _to_decimal(raw.get("limitPx", raw.get("px", 0)))

        return Order(
            id=oid,
            exchange_id=oid,
            symbol=symbol,
            side=side,
            type=order_type,
            amount=amount if amount > 0 else sz,
            price=price_val if price_val > 0 else None,
            filled=filled,
            remaining=max(Decimal("0"), amount - filled),
            status=status,
            reduce_only=raw.get("reduceOnly", False),
            cost=filled * price_val if filled > 0 and price_val > 0 else Decimal("0"),
            timestamp=datetime.now(UTC).replace(tzinfo=None),
            last_update=datetime.now(UTC).replace(tzinfo=None),
        )

    async def close(self) -> None:
        # Hyperliquid SDK doesn't have persistent connections in the REST client
        pass
