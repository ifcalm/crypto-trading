# 加密货币盈利方式全集

> 整理时间：2026-05-29 | 基于全网搜索 + 实际可执行性筛选

---

## 一、资金费率套利（Delta 中性）

**原理**：现货买入 → 永续合约等量做空 → 每 8 小时收资金费率，方向性风险为零。

| 指标 | 数据 |
|---|---|
| 年化收益 | 10%–30%（牛市），熊市压缩至 4% 以下 |
| 启动资金 | 1 万 USDT+ |
| 执行难度 | ★★ |
| 最优组合 | Binance 现货 + Hyperliquid 永续（费率差最大） |
| 工具 | Binance 原生套利机器人 / Passivbot / 自建 Python |

### 2025 年新变化

- **Hyperliquid 股票永续**：NVDA、MSTR 等合约周末持续收资金费，年化 30%–62%
- **Pendle Boros**：支持 YU 代币锁定固定资金费率，消除波动风险
- **协议自动化**：Liminal（Hyperliquid 上）、Solstice Finance，存入 USDC 自动挖收益，Sharpe > 7.0
- **Ethena**：首创 CEX 资金费率 → sUSDe 收益模型，已规模化

⚠️ **风险**：市场饱和导致年化已压至 4% 以下。极端行情下费率可能转负。

### 执行清单

1. 监控 Binance/Hyperliquid/Bybit/OKX 资金费率
2. 计算净收益（扣除手续费、滑点、借贷成本）
3. 选择执行方式：手动 / Binance 套利机器人 / 自建 Python / 协议存款
4. 杠杆 ≤ 2x，保证金率 ≤ 45%
5. 费率转负或基差翻转时立即平仓

---

## 二、做市（Market Making）

**原理**：同时在买卖两侧挂限价单，赚取价差 + 平台流动性奖励。

### 策略分级

| 策略 | 年化 | 资金 | 难度 | 说明 |
|---|---|---|---|---|
| 基础做市 | 20%–80% | $1k–$10k | ★★★ | 双边挂单吃价差 |
| Avellaneda-Stoikov | 更稳定 | $10k+ | ★★★★ | 数学模型动态调价，管理库存风险 |
| 做市商资格套利 | 60%–120% | 20 万 USDT+ | ★★★★ | Maker 费率负值（你赚钱）+ 资金费双收 |
| Polymarket 做市 | $700–800/天 | $10k+ | ★★★ | 预测市场双边挂单，流动性奖励近 3 倍 |

### 开源框架

- **Hummingbot**：PMM Simple V2 策略，模板化
- **OctoBot**：15+ 交易所，内置套利保护
- **Freqtrade**：策略类框架 + 回测 + 超参优化

### Avellaneda-Stoikov 关键参数

```yaml
risk:
  max_position: 22.0          # 单币最大持仓
  max_notional: 75.0          # 最大名义价值
  max_drawdown_pct: 7.7       # 回撤熔断
order_management:
  quote_size_base: 11.0       # 每单数量
  min_spread_bps: 10          # 最小价差 0.1%
  max_spread_bps: 50          # 最大价差 0.5%
  target_spread_pct: 0.1      # 目标价差
performance:
  throttle_ms: 10             # 下单间隔
  replace_after_ms: 500       # 500ms 替换订单
```

### 必备风控

- **Kill Switch**：回撤超限 / 断连 → 全撤 + 全平
- **每日亏损熔断**：日亏 5% 自停
- **波动率缩放**：高波动时自动缩减敞口
- **API Key 隔离**：只开交易权限，关提币，IP 白名单

---

## 三、跨所价差套利（搬砖）

### 策略清单

| 策略 | 年化 | 资金 | 难度 | 说明 |
|---|---|---|---|---|
| 现货搬砖 | 15%–80% | 3 万 USDT+ | ★★ | Binance↔OKX 低价买/高价卖 + 自动提币 |
| 永续基差套利 | 18%–60% | 5 万 USDT+ | ★★★ | Binance vs Hyperliquid 基差 |
| CEX↔DEX 价差 | 20%–200% | 5 万 USDT+ | ★★★ | Arbitrum/Base 链上套利 |
| 三角套利 | 不定 | 1 万 USDT+ | ★★★ | 单一所内 A→B→C→A 汇率定价偏差 |
| 稳定币脱锚 | 单次 5%–300% | 10 万 USDT+ | ★★ | USDT/USDC/crvUSD 脱锚到 0.97 以下→全仓抄底 |

### 最肥三条线

1. Binance ↔ OKX
2. Binance ↔ Bybit
3. Bybit ↔ Hyperliquid

### 工具

- CCXT 搬砖机器人
- 3money / coinglass 监控价差
- 闪电贷（链上原子套利）

⚠️ 机构资本涌入，套利窗口从分钟级压缩到秒级。

---

## 四、预测市场套利（Polymarket）

2025 年 Polymarket 处理超 9500 万笔交易，名义交易量超 215 亿美元。

### 六大策略

| 策略 | 原理 | 年化 |
|---|---|---|
| 数学套利 | 当 Yes + No < $1 时两边同时买入 | 无风险 |
| 跨平台对冲 | Polymarket vs Kalshi 同一事件价差 | 不定 |
| 高概率"债券" | 买入 >99% 概率事件，等结算 | 可达 1800% |
| 流动性狙击 | 新市场上线时抢首批流动性奖励 | 不定 |
| AI 概率建模 | ML 估算真实概率 vs 市场定价 | 不定 |
| 关联市场套利 | 相关事件联动定价偏差 | 不定 |

⚠️ 仅 0.51% 钱包实现 > $1000 盈利，零和博弈残酷。

---

## 五、量化趋势/波段交易

### 策略对比

| 策略 | 原理 | 年化 | 难度 |
|---|---|---|---|
| 趋势跟随（MA 交叉） | 快线穿慢线→顺势开仓 | 不定 | ★★ |
| 突破交易 | 价格突破关键阻力+放量 | 不定 | ★★★ |
| 动量交易（RSI） | RSI 超卖买/超买卖 | 不定 | ★★ |
| 网格交易 | 区间内自动低买高卖 | 15%–100% | ★ |
| AI/LLM 策略 | 语言模型分析盘口/新闻 | 未验证 | ★★★★ |
| 统计套利 | 相关性偏离→均值回归 | 不定 | ★★★★★ |

### 已有策略（本系统）

- MA 交叉（`ma_crossover`）
- RSI 反转（`rsi_reversal`）
- LLM 盘口策略（`llm_orderbook`）

---

## 六、被动收益

| 方式 | 年化 | 风险 | 说明 |
|---|---|---|---|
| ETH PoS 质押 | ~4.2% | 极低 | 通过 Lido/rETH |
| 稳定币存款 | 4.5%–8% | 极低 | Aave/Compound |
| BTC DCA 定投 | 历史未亏损 | 中 | 每月固定金额买入 |
| DeFi 借贷 | 不定 | 中 | 存 USDC 收利息 |
| 流动性挖矿 | 10%–50% | 中高 | Uniswap V3 LP |
| 空投交互 | 零成本 | 低 | 新协议测试网交互→领代币 |

---

## 七、策略组合建议

### 🟢 保守型
```
70% 定投主流币（BTC/ETH）+ 20% 质押收益 + 10% 资金费率套利
预期年化：10%–20%
```

### 🟡 平衡型
```
40% 资金费率套利 + 30% 跨所搬砖 + 20% 定投 + 10% 量化趋势
预期年化：15%–40%
```

### 🔴 激进型
```
50% 信息套利（Polymarket/链上）+ 30% 做市 + 20% 动量交易
预期年化：不定，高波动
```

---

## 八、对你项目的落地路径

### 第一优先级：资金费率套利

已有条件：Binance + Hyperliquid 双端适配器、风控模块。
欠缺：`FundingRateStrategy`（逻辑比现有策略更简单），约 200 行代码。

### 第二优先级：跨所价差监控

已有条件：`fetch_tickers` 两端都实现了。
欠缺：价差计算 + 通知推送，约 100 行代码。

### 第三优先级：简单做市

已有条件：风控模块、Broker 接口。
欠缺：双边挂单 + 库存管理的 `MarketMaker` 模块，约 500 行代码。

---

## 参考来源

- [Gate.io 2024-2025牛市交易策略](https://www.gate.com/zh/post/status/14585260)
- [Polymarket 2025六大赚钱模型深度报告](https://www.bitget.com/zh-CN/news/detail/12560605124250)
- [Binance 资金费率套利机器人](https://www.binance.bh/en/blog/tech/3611863022773164727)
- [Liminal - Capturing Real Yield via Funding Rate Arbitrage](https://nansen-alpha-portal-prod.web.app/articles/liminal-capturing-real-yield-via-funding-rate-arbitrage)
- [BitMEX - Collapse of Crypto Arbitrage Strategies](https://www.kucoin.com/zh-hant/news/flash/bitmex-reports-collapse-of-crypto-arbitrage-strategy-amid-market-saturation)
- [ASTER_DELTA_NEUTRAL - Python 资金费率套利](https://github.com/djienne/ASTER_DELTA_NEUTRAL)
- [Avellaneda-Stoikov Market Maker - Hyperliquid](https://github.com/niwek1/Market-Maker)
- [Hummingbot Market Making Framework](https://hummingbot.org)
- [Pendle Boros - 固定费率衍生品](https://www.chaincatcher.com/en/article/2208196)
- [链上套利策略全景](https://www.chaincatcher.com/article/2178787)
