# T024 长历史数据源——ETF K线 5~10+年

> **状态**：done
> **所属阶段**：Phase 1（宏观-动量融合）辅助
> **并行安全**：是（不修改核心文件，仅新增数据源模块）
> **占用文件**：`data_sources/sina_feed.py`（新建）、`tests/_test_macro_overlay.py`（更新数据源引用）
> **创建日期**：2026-05-09
> **完成日期**：2026-05-09

## 一、背景

T023 CDR机制在2023-2026年牛市中因ERP持续高位（P≥94%）无分化。需要更长历史（覆盖一轮完整牛熊，如2016-2026）才能验证CDR在熊市的降仓效果。

当前 `AutoStockFeed`（autostock.cn）只能拿到 ≈3年数据。`akshare.fund_etf_hist_em()` 连通性不稳定（ConnectionError）。

## 二、调研结果

| 方案 | 数据源 | 结果 |
|------|--------|------|
| C | baostock | free tier仅80行（2026年起），无用 |
| A | akshare `fund_etf_hist_em` | ConnectionError（东财API被封） |
| B | akshare `fund_etf_hist_sina` | 返回0行 |
| E | efinance | ConnectionError（也是东财API） |
| D | tushare | 未测试（需token） |
| **F** | **新浪财经API** | **✅ 3000行，2013-12 ~ 2026-05** |

**胜出方案：新浪财经API** — 直接调用 `money.finance.sina.com.cn`，免费、无需注册、瞬时响应。

- 数据范围：2013-12 ~ 至今（~13年）
- 字段：date, open, high, low, close, volume
- 复权：价格天然前复权（年度收益与真实沪深300吻合）
- SH ETF用 `sh` 前缀，SZ ETF用 `sz` 前缀（自动检测）

## 三、实施

### 已完成

1. ✅ 创建 `data_sources/sina_feed.py` —— `SinaFinanceFeed` 实现 `DataFeed` 接口
2. ✅ 注册到 `data_sources/__init__.py`
3. ✅ 更新 `tests/_test_macro_overlay.py`：
   - 替换 `AutoStockFeed` → `SinaFinanceFeed`
   - 回测区间默认扩展为 `2018-01-01 ~ 2026-04-30`（2019个交易日）
4. ✅ 运行完整AB对比回测

### 全周期回测结果（2018-2026）

| 指标 | ROC独立 | Bond独立 | 融合MACRO |
|------|---------|----------|-----------|
| 年化收益 | -10.3% | 2.4% | -9.2% |
| 最大回撤 | 85.0% | 40.2% | 81.5% |
| Sharpe | -0.28 | 0.23 | -0.25 |
| 交易次数 | 60 | 4 | 56 |

### 关键发现

1. **ROC策略全周期溃败**：动量轮动在长周期（含2018/2022熊市+震荡期）严重失效。短周期（2023-2026）54%年化是牛市的幸存者偏差。
2. **BondYield仅在融合上下文中失活**：独立运行时4笔交易，但在MacroOverlayStrategy中产生了"无TIMING决策"警告——这是BUG，需要排查 `MacroOverlayStrategy.produce_decisions()`。
3. **CDR验证未达成**：因BondYield在融合中无输出，CDR恒为0，融合=纯现金+微量ROC，无仓位调节效果。

## 四、验收标准

- [x] `SinaFinanceFeed` 能返回 510300 至少5年的日线数据（实际：2510行，2016-2026）
- [x] 回测覆盖至少一个完整牛熊周期（2018-2026含2018熊市+2022熊市）
- [ ] CDR 在熊市周期中 ≤ 0.5（未达成——BondYield在融合上下文中无输出）
- [ ] 融合策略 Sharpe > ROC独立（未达成——CDR机制未生效）

## 五、遗留问题

1. **BondYield在融合中不产出TIMING决策**：需排查 `MacroOverlayStrategy.produce_decisions()` 中BondYield子策略的数据传递或调用逻辑
2. **ROC全周期失效**：动量策略在长周期需要更强的风控（止损、波动率加权、市场状态过滤），纯ROC轮动不够