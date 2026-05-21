# crypto-trading

Python 加密货币量化交易系统。

## 功能

- 币安现货 + 永续合约交易
- 历史数据拉取与存储 (Parquet)
- 回测引擎 (bar-by-bar 回放)
- 模拟盘 / 实盘交易
- 风控管理

## 快速开始

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env  # 填入 API key
```

## 使用

```bash
crypto-trading fetch --symbol BTC/USDT --timeframe 1h
crypto-trading backtest --strategy ma_crossover --start 2024-01-01 --end 2025-01-01
crypto-trading paper --strategy ma_crossover
crypto-trading live --strategy ma_crossover
```

## 配置

编辑 `config.yaml` 设置交易对、时间周期、策略参数、风控阈值。
