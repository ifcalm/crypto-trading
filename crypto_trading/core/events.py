"""Domain events and async EventBus.

Lightweight DDD-lite: events carry the data, handlers react to them.
No heavy framework — just types + asyncio.Queue.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from crypto_trading.core.types import OHLCV, Order, Portfolio, Position, Signal

log = logging.getLogger(__name__)

Handler = Callable[..., Any]


# ─── domain events ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class DomainEvent:
    timestamp: datetime = field(default_factory=lambda: datetime.utcnow())


@dataclass(frozen=True)
class BarArrived(DomainEvent):
    symbol: str = ""
    bar: OHLCV | None = None


@dataclass(frozen=True)
class SignalGenerated(DomainEvent):
    symbol: str = ""
    signal: Signal | None = None


@dataclass(frozen=True)
class SignalRejected(DomainEvent):
    symbol: str = ""
    signal: Signal | None = None
    reason: str = ""


@dataclass(frozen=True)
class OrderRequested(DomainEvent):
    symbol: str = ""
    signal: Signal | None = None
    portfolio: Portfolio | None = None


@dataclass(frozen=True)
class OrderExecuted(DomainEvent):
    symbol: str = ""
    order: Order | None = None
    signal: Signal | None = None


@dataclass(frozen=True)
class PositionOpened(DomainEvent):
    symbol: str = ""
    position: Position | None = None
    order: Order | None = None
    leverage: int = 1


@dataclass(frozen=True)
class PositionClosed(DomainEvent):
    symbol: str = ""
    position: Position | None = None
    order: Order | None = None
    pnl: Decimal = Decimal("0")


@dataclass(frozen=True)
class PortfolioUpdated(DomainEvent):
    equity: Decimal = Decimal("0")
    free_balance: Decimal = Decimal("0")
    drawdown: Decimal = Decimal("0")


# ─── event bus ───────────────────────────────────────────────────────────


class EventBus:
    """Async publish-subscribe event bus.

    Each event type maps to a list of async handlers.
    Handlers run concurrently; one handler's exception does not crash the bus.
    """

    def __init__(self) -> None:
        self._handlers: dict[type[DomainEvent], list[Handler]] = {}
        self._pending: set[asyncio.Task[None]] = set()

    def subscribe(self, event_type: type[DomainEvent], handler: Handler) -> None:
        """Register an async handler for an event type."""
        self._handlers.setdefault(event_type, []).append(handler)

    async def publish(self, event: DomainEvent) -> None:
        """Fire all handlers for this event type. Awaits completion of all handlers."""
        handlers = self._handlers.get(type(event), [])
        if not handlers:
            return

        tasks: list[asyncio.Task[None]] = []
        for h in handlers:
            task = asyncio.create_task(self._safe_invoke(h, event))
            tasks.append(task)
            self._pending.add(task)

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _safe_invoke(self, handler: Handler, event: DomainEvent) -> None:
        try:
            result = handler(event)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            log.exception("Event handler %s failed for %s", handler.__name__, type(event).__name__)
