# Crypto Trading 项目文档

基于 Python 的加密货币量化交易系统，支持币安现货 + 永续合约，覆盖数据获取、策略回测、模拟盘、实盘交易全流程。

## 目录

- [核心功能](#核心功能)
- [环境要求](#环境要求)
- [安装](#安装)
- [快速开始](#快速开始)
- [CLI 命令详解](#cli-命令详解)
- [配置文件](#配置文件)
- [策略开发](#策略开发)
- [风控系统](#风控系统)
- [Web 可视化界面](#web-可视化界面)
- [项目架构](#项目架构)

---

## 核心功能

| 模块 | 功能 |
|---|---|
| 数据层 | 通过 Binance REST API 拉取历史 OHLCV 数据，Parquet 格式存储，支持增量更新和去重 |
| 回测引擎 | bar-by-bar 事件驱动回放，模拟保证金、资金费率结算、手续费，输出 Sharpe/Sortino/Calmar 等指标 |
| 实时数据 | WebSocket 订阅 Binance kline 流，实时推送完成的 K 线 |
| 策略框架 | 统一的 `on_bar(symbol, bar) → Signal` 接口，回测和实盘共用同一份策略代码 |
| 风控系统 | 可插拔的规则链：最大回撤、仓位上限、最大持仓数、置信度过滤、杠杆上限 |
| 模拟盘 | PaperBroker 在实时数据上模拟成交（支持滑点），零资金风险验证策略 |
| 实盘 | LiveBroker 通过 ccxt 向币安真实下单，支持测试网和主网，含订单状态轮询和异常重试 |
| Web UI | Streamlit 界面：回测配置与结果可视化、数据下载、K 线预览 |

**支持的市场：**

| | 现货 | 永续合约 |
|---|---|---|
| 交易方向 | 买入 / 卖出 | 做多(开/平) / 做空(开/平) |
| 杠杆 | 无 | 1x–125x |
| 保证金 | 全额 | 初始保证金 + 维持保证金 |
| 资金费率 | 无 | 每 8 小时结算 |
| 减仓单 | 无 | reduce_only |

---

## 环境要求

- Python >= 3.11
- 币安账户（实盘需要 API Key，拉数据和模拟盘不需要）
- （中国大陆用户）HTTP 代理，用于访问 Binance API

---

## 安装

```bash
git clone <repo-url>
cd crypto-trading

# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -e ".[dev]"
```

如需 DEX 支持（后续）：

```bash
pip install -e ".[dex]"
```

---

## 快速开始

### 1. 配置

```bash
cp .env.example .env
```

编辑 `.env`，填入 API 密钥（仅实盘需要）：

```ini
BINANCE_API_KEY=your_api_key_here
BINANCE_SECRET_KEY=your_secret_key_here
BINANCE_TESTNET=true  # 先用测试网
```

编辑 `config.yaml`（完整选项见[配置文件](#配置文件)章节）：

```yaml
market_type: futures

exchange:
  binance:
    testnet: false
    proxy: "http://127.0.0.1:7890"  # 代理地址

trading:
  symbols:
    - BTC/USDT
  timeframes:
    - 1h
  default_leverage: 1
```

### 2. 下载数据

```bash
crypto-trading fetch -s BTC/USDT -t 1h --since 2024-01-01
```

### 3. 回测策略

```bash
crypto-trading backtest --strategy ma_crossover -s BTC/USDT \
  --start 2024-01-01 --end 2025-01-01 --capital 10000
```

### 4. 模拟盘

```bash
crypto-trading paper --strategy ma_crossover --symbols BTC/USDT
```

### 5. 实盘（测试网）

```bash
# 先在 .env 中配置 BINANCE_TESTNET=true
crypto-trading live --strategy ma_crossover --symbols BTC/USDT
```

### 6. Web 界面

```bash
crypto-trading ui
# 浏览器打开 http://localhost:8501
```

---

## CLI 命令详解

### `fetch` — 下载历史数据

```
crypto-trading fetch [OPTIONS]
```

| 参数 | 简写 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `--symbol` | `-s` | 是 | — | 交易对，如 `BTC/USDT`、`ETH/USDT` |
| `--timeframe` | `-t` | 否 | `1h` | K 线周期：`1m` `5m` `15m` `1h` `4h` `1d` |
| `--since` | — | 否 | 365 天前 | 起始日期，ISO 格式如 `2024-01-01` |
| `--until` | — | 否 | 当前时间 | 结束日期 |
| `--proxy` | — | 否 | 配置文件值 | HTTP 代理，如 `http://127.0.0.1:7890` |
| `--config` | `-c` | 否 | `config.yaml` | 配置文件路径 |

**示例：**

```bash
# 下载 BTC 过去一年的 1 小时线
crypto-trading fetch -s BTC/USDT -t 1h --since 2024-01-01

# 下载 ETH 近 30 天的 5 分钟线，使用代理
crypto-trading fetch -s ETH/USDT -t 5m --since 2026-04-21 --proxy http://127.0.0.1:7890

# 下载多个交易对（多次执行）
crypto-trading fetch -s BTC/USDT -t 1h --since 2024-01-01
crypto-trading fetch -s ETH/USDT -t 1h --since 2024-01-01
```

数据存储在 `data/parquet/` 目录下，按交易对和时间周期分文件。重复 fetch 会自动增量更新，不会产生重复数据。

---

### `backtest` — 策略回测

```
crypto-trading backtest [OPTIONS]
```

**必填参数：**

| 参数 | 简写 | 说明 |
|---|---|---|
| `--strategy` | — | 策略名称，当前可用：`ma_crossover`、`rsi_reversal` |
| `--symbol` | `-s` | 交易对 |
| `--start` | — | 开始日期，ISO 格式 `2024-01-01` |
| `--end` | — | 结束日期，ISO 格式 |

**可选参数：**

| 参数 | 简写 | 默认值 | 说明 |
|---|---|---|---|
| `--timeframe` | `-t` | `1h` | K 线周期 |
| `--capital` | — | `10000` | 初始资金（USDT） |
| `--market` | `-m` | `futures` | 市场类型：`spot` 或 `futures` |
| `--leverage` | `-l` | `1` | 杠杆倍数（仅合约） |
| `--config` | `-c` | `config.yaml` | 配置文件路径 |

**示例：**

```bash
# 现货回测
crypto-trading backtest --strategy ma_crossover -s BTC/USDT \
  --start 2024-01-01 --end 2025-01-01 --market spot

# 合约 3 倍杠杆回测
crypto-trading backtest --strategy rsi_reversal -s ETH/USDT \
  --start 2024-06-01 --end 2025-06-01 -m futures -l 3 --capital 5000
```

**输出内容包括：**
- 总收益率、最终权益、总手续费、交易次数
- Sharpe Ratio、Sortino Ratio、Calmar Ratio
- 最大回撤及回撤区间
- 胜率、盈亏比
- 权益曲线（ASCII 图表）
- 最近 20 笔交易明细

---

### `paper` — 模拟盘交易

```
crypto-trading paper [OPTIONS]
```

使用**实时 WebSocket 数据**驱动策略，在 PaperBroker 中模拟成交。不涉及真实资金，但使用真实的当前价格（含可配置滑点）。

| 参数 | 简写 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `--strategy` | — | 是 | — | 策略名称 |
| `--symbols` | `-s` | 是 | — | 交易对列表，逗号分隔 |
| `--timeframes` | `-t` | 否 | `1h` | K 线周期 |
| `--capital` | — | 否 | `10000` | 初始资金 |
| `--market` | `-m` | 否 | `futures` | 市场类型 |
| `--leverage` | `-l` | 否 | `1` | 杠杆倍数 |
| `--proxy` | — | 否 | 配置文件值 | HTTP 代理 |
| `--config` | `-c` | 否 | `config.yaml` | 配置文件路径 |

**示例：**

```bash
# 单币种模拟盘
crypto-trading paper --strategy ma_crossover --symbols BTC/USDT

# 多币种模拟盘
crypto-trading paper --strategy rsi_reversal --symbols BTC/USDT,ETH/USDT -t 15m -l 3
```

运行后每收到一个 K 线就调用一次策略，产生的交易打印在终端。按 `Ctrl+C` 停止。

---

### `live` — 实盘交易

```
crypto-trading live [OPTIONS]
```

参数与 `paper` 完全一致。额外要求：`.env` 中配置了 API Key。

- 如果 `.env` 中 `BINANCE_TESTNET=true`，连接币安测试网
- 否则连接主网，真实资金交易

启动时会显示红色警告文字，确认后开始运行。

---

### `strategies` — 查看可用策略

```bash
crypto-trading strategies
```

输出所有已注册的策略名称。

---

### `ui` — 启动 Web 界面

```bash
crypto-trading ui              # 默认 http://localhost:8501
crypto-trading ui -p 8080      # 自定义端口
crypto-trading ui --host 0.0.0.0  # 允许局域网访问
```

---

## 配置文件

### `config.yaml` — 完整选项

```yaml
# 市场类型：spot（现货）或 futures（合约）
market_type: futures

exchange:
  binance:
    # 是否使用测试网
    testnet: false
    # HTTP 代理（中国大陆用户需要）
    proxy: "http://127.0.0.1:7890"

trading:
  # 默认交易对列表
  symbols:
    - BTC/USDT
    - ETH/USDT
  # 默认 K 线周期
  timeframes:
    - 1h
  # 默认杠杆倍数
  default_leverage: 1

data:
  # 数据存储目录
  parquet_dir: data/parquet
  # 数据库连接
  database_url: sqlite+aiosqlite:///data/trading.db

# 策略参数（传递给 Strategy.params）
strategy_params:
  ma_crossover:
    fast_period: 20    # 快均线周期
    slow_period: 50    # 慢均线周期
  rsi_reversal:
    period: 14          # RSI 计算周期
    oversold: 30        # 超卖阈值
    overbought: 70      # 超买阈值

# 风控参数
risk:
  max_drawdown_pct: 0.2       # 最大回撤 20% 时停止
  max_position_pct: 0.1       # 单笔最大占权益 10%
  max_open_positions: 5       # 最多同时持有 5 个仓位
  min_confidence: 0.5         # 最低信号置信度
  max_leverage: 3             # 最大杠杆倍数
```

### `.env` — 密钥（不提交 Git）

```ini
BINANCE_API_KEY=your_api_key_here
BINANCE_SECRET_KEY=your_secret_key_here
BINANCE_TESTNET=true
```

---

## 策略开发

### 策略接口

所有策略继承 `Strategy` 基类，只需实现一个方法：

```python
from crypto_trading.core.strategy import Strategy
from crypto_trading.core.types import OHLCV, Signal, OrderSide

class MyStrategy(Strategy):
    async def on_bar(self, symbol: str, bar: OHLCV) -> Signal | None:
        # 返回 Signal 表示交易信号，返回 None 表示不交易
        ...
```

### 内置辅助方法

策略基类提供了 bar 历史管理：

```python
# 获取最近 N 个 bar
bars = self._get_bars(symbol, count=20)

# 获取最近 N 个收盘价（list[float]）
closes = self._get_closes(symbol, count=14)

# 策略参数（来自 config.yaml 的 strategy_params）
fast_period = self.params.get("fast_period", 20)
```

### Signal 字段说明

```python
@dataclass
class Signal:
    symbol: str        # 交易对
    side: OrderSide    # BUY 或 SELL（合约中 BUY=做多, SELL=做空）
    amount: Decimal    # 交易数量（合约中是合约张数的等值 USDT）
    confidence: float  # 置信度 0.0-1.0，低于风控阈值的信号会被过滤
    reduce_only: bool  # 仅平仓（合约专用）
    leverage: int      # 杠杆倍数
```

### 注册新策略

在 `crypto_trading/strategies/` 下创建 `.py` 文件，然后在 `strategies/__init__.py` 中注册：

```python
from crypto_trading.strategies.my_strategy import MyStrategy

STRATEGY_REGISTRY: dict[str, type[Strategy]] = {
    "ma_crossover": MACrossoverStrategy,
    "rsi_reversal": RSIReversalStrategy,
    "my_strategy": MyStrategy,  # 新增
}
```

### 完整策略示例：布林带策略

```python
from decimal import Decimal
from crypto_trading.core.strategy import Strategy
from crypto_trading.core.types import OHLCV, OrderSide, OrderType, Signal

class BollingerStrategy(Strategy):
    """布林带均值回归 — 价格触下轨买入，触上轨卖出。"""

    @property
    def _period(self) -> int:
        return int(self.params.get("period", 20))

    @property
    def _std_multiplier(self) -> float:
        return float(self.params.get("std_multiplier", 2.0))

    def _bollinger(self, closes: list[float]) -> tuple[float, float, float] | None:
        n = self._period
        if len(closes) < n:
            return None
        sma = sum(closes[-n:]) / n
        variance = sum((c - sma) ** 2 for c in closes[-n:]) / n
        std = variance ** 0.5
        upper = sma + self._std_multiplier * std
        lower = sma - self._std_multiplier * std
        return lower, sma, upper

    async def on_bar(self, symbol: str, bar: OHLCV) -> Signal | None:
        closes = self._get_closes(symbol, self._period + 1)
        bands = self._bollinger(closes)
        if bands is None:
            return None
        lower, sma, upper = bands
        price = float(bar.close)

        if price <= lower:
            return Signal(symbol=symbol, side=OrderSide.BUY,
                          amount=Decimal("0.01"), confidence=0.7)
        elif price >= upper:
            return Signal(symbol=symbol, side=OrderSide.SELL,
                          amount=Decimal("0.01"), confidence=0.7)
        return None
```

在 `config.yaml` 中添加参数：

```yaml
strategy_params:
  bollinger:
    period: 20
    std_multiplier: 2.0
```

---

## 风控系统

风控以**规则链**的方式运行。每个信号依次经过所有规则检查，任意规则拒绝则丢弃信号。

### 可用规则

| 规则 | 类名 | 配置键 | 行为 |
|---|---|---|---|
| 最大回撤 | `MaxDrawdownRule` | `max_drawdown_pct` | 当前回撤超过阈值时拒绝所有信号 |
| 仓位上限 | `PositionSizeRule` | `max_position_pct` | 将信号数量截断到权益的 N% |
| 最大持仓数 | `MaxOpenPositionsRule` | `max_open_positions` | 同时持有的币种数超过上限时拒绝新开仓 |
| 最小置信度 | `MinConfidenceRule` | `min_confidence` | 置信度低于阈值的信号直接丢弃 |
| 杠杆上限 | `MaxLeverageRule` | `max_leverage` | 信号杠杆超过上限时拒绝 |

### 自定义规则

```python
from crypto_trading.risk.rules import RiskRule
from crypto_trading.core.types import Signal, Portfolio

class NoWeekendRule(RiskRule):
    """周末不交易。"""

    name = "no_weekend"

    def check(self, signal: Signal, portfolio: Portfolio) -> Signal:
        if signal.timestamp.weekday() >= 5:
            raise RiskRuleViolation("No trading on weekends")
        return signal
```

注册到 RiskManager：

```python
from crypto_trading.risk.manager import RiskManager

risk_manager = RiskManager([
    MaxDrawdownRule(0.2),
    PositionSizeRule(0.1),
    NoWeekendRule(),  # 自定义规则
])
```

---

## Web 可视化界面

启动：

```bash
crypto-trading ui
```

### 页面功能

**Home 页面：**
- 当前市场配置概览
- 已有数据统计（日期范围、数据天数）
- 已注册策略及参数

**Backtest 页面：**
- 侧边栏：策略选择、交易对、日期范围、资金、杠杆、风控参数调整
- 运行后展示：指标卡片、Plotly 权益曲线、交易明细表

**Data 页面：**
- 数据下载（选交易对 + 日期范围 + 代理，点击下载）
- 已存储数据概览表
- K 线预览（最近 90 天 Candlestick 图）

---

## 项目架构

```
crypto_trading/
│
├── core/                     # 抽象层——所有模块的共同基础
│   ├── types.py              # dataclass 和 enum，Decimal 做价格
│   ├── exchange.py           # Exchange 抽象接口（async）
│   ├── strategy.py           # Strategy 基类
│   └── errors.py             # 领域异常
│
├── config/
│   └── settings.py           # pydantic-settings，读 config.yaml + .env
│
├── data/                     # 数据层
│   ├── store.py              # ParquetStore —— 读写 Parquet OHLCV 数据
│   ├── fetcher.py            # 历史数据 REST 拉取 + 增量更新
│   ├── stream.py             # StreamBuilder —— 自定义周期 K 线聚合
│   ├── database.py           # SQLAlchemy 异步引擎
│   └── models.py             # ORM 模型
│
├── exchanges/                # 交易所实现
│   ├── binance.py            # BinanceExchange（ccxt 封装，现货+合约）
│   └── binance_ws.py         # WebSocket kline 实时流
│
├── strategies/               # 策略
│   ├── ma_crossover.py       # 均线交叉
│   └── rsi_reversal.py       # RSI 均值回归
│
├── backtest/                 # 回测
│   ├── engine.py             # bar-by-bar 回放引擎
│   ├── metrics.py            # 指标计算
│   └── reporter.py           # 控制台输出
│
├── risk/                     # 风控
│   ├── rules.py              # 规则类
│   └── manager.py            # 规则链管理器
│
├── execution/                # 执行
│   ├── broker.py             # Broker 抽象
│   ├── paper_broker.py       # 模拟成交
│   └── live_broker.py        # 真实下单
│
├── live/
│   └── runner.py             # 事件循环：WS → Strategy → Risk → Broker
│
├── cli/
│   └── main.py               # Typer CLI 入口
│
├── web/                      # Streamlit 可视化界面
│   ├── app.py                # 主页
│   └── pages/
│       ├── 1_backtest.py     # 回测页面
│       └── 2_data.py         # 数据管理页面
│
└── tests/                    # 测试
    ├── test_data/
    │   └── test_store.py
    └── test_execution/
        └── test_risk.py
```

### 数据流

```
                    ┌─────────────┐
                    │  Binance    │
                    │  REST / WS  │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
        fetch_ohlcv    kline WS    create_order
              │            │            │
              ▼            ▼            ▼
         ParquetStore  StreamBuilder  LiveBroker
              │            │
              └─────┬──────┘
                    ▼
              OHLCV bar
                    │
                    ▼
            Strategy.on_bar()
                    │
                    ▼
               Signal | None
                    │
                    ▼
              RiskManager
                    │
                    ▼
              Broker (Paper/Live)
                    │
                    ▼
                 Order
```

### 回测 vs 模拟盘 vs 实盘

| | 回测 | 模拟盘 | 实盘 |
|---|---|---|---|
| 数据来源 | Parquet 文件 | WebSocket 实时 | WebSocket 实时 |
| 成交方式 | bar close 价 | 实时价 + 滑点 | 币安真实成交 |
| 资金 | 虚拟 | 虚拟 | 真实 |
| 手续费 | 模拟扣除 | 模拟扣除 | 实际扣除 |
| 适用场景 | 策略验证、参数优化 | 策略稳定性验证 | 真正赚钱 |
