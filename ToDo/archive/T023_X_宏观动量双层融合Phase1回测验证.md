# T023 宏观动量双层融合——Phase 1：MVP回测验证

> **状态**：deprecated — 已被 T030 全周期重测推翻  
> **所属阶段**：Phase 1（宏观-动量融合，已废弃）  
> **⚠️ T030 全周期重测（2026-05-18）**：用2018-2026全周期重新验证后，CDR融合在所有核心指标上均劣于纯ROC动量（收益-23.8%、回撤+11.5%、Sharpe-20.4%）。短周期的"CDR降低回撤28%"是该区间ERP持续高位(P≥94%)、CDR从未真正触发的假象。详见 [T030_D](../ToDo/T030_D_宏观动量CDR融合全周期重测.md)。  
> **T025关联**：T025在纯动量线验证了ATR+ADX趋势择时(年化+11.1%)，已替代CDR作为择时方案。  
> **创建日期**：2026-05-08  
> **完成日期**：2026-05-09

## 一、前置依赖

- T004 股债利差策略（已完成）—— BondYieldStrategy 产出 TIMING 决策
- T005 ROC多因子增强（已完成）—— ROCStrategy 产出 ROTATION 决策
- T019 回测可视化增强（已完成）—— BacktestComparator 用于对比图表
- 指导文档 [3.08_宏观动量双层决策系统设计.md](../指导文档/3.08_宏观动量双层决策系统设计.md) —— 本任务的施工蓝图

## 二、目标

在回测环境实现"CDR × ROC权重"的融合回测，通过AB对比验证：融合策略（宏观仓位调节 + 动量选品）是否优于独立策略（纯动量满仓轮动）。

核心公式：`target_weight = roc_pick.weight × CDR`，其中 `CDR = f(ERP_percentile)`。

## 三、实施计划

### 1.1 创建 MacroOverlayResolver（`core/resolver.py`）

- 继承 `Resolver` 抽象类
- `resolve()` 同时消费 `TIMING` 和 `ROTATION` 两种 Decision
- 实现 CDR 计算：`_percentile_to_cdr()`（分段线性映射）、`_smooth_cdr()`（EMA平滑）
- 实现 `_apply_erp_abs_cap()`（绝对ERP修正）、`_apply_trend_cap()`（趋势过滤）
- 产出融合后的 `TargetPosition` 列表：CDR × ROC权重 = 实际目标仓位
- 防御仓位：`CASH` 标记的 TargetPosition，权重 = 1.0 - CDR

### 1.2 创建 MacroOverlayStrategy（`strategies/macro_overlay.py`）

- 包装 `BondYieldStrategy` 和 `ROCStrategy`
- `produce_decisions()` 并行调用两个子策略，合并 decisions dict
- `get_required_data()` 合并两个子策略的数据需求
- `name` 属性：`"macro_overlay"`

### 1.3 创建 MacroOverlayConfig（`strategies/_configs/macro_overlay_config.py`）

- 复用 `bond_yield_config` 和 `roc_config` 的现有配置格式
- 新增 overlay 专属参数：`top_k=3`, `erp_abs_min=-5.0`, `erp_abs_max=8.0`, `cdr_smooth_alpha=0.3`, `defensive_code=""`, `min_position_pct=0.05`

### 1.4 AB对比回测

- 创建测试脚本 `tests/_test_macro_overlay.py`
- A组：纯 ROCStrategy + RankingResolver（基准）
- B组：MacroOverlayStrategy + MacroOverlayResolver（融合）
- C组：BondYieldStrategy + TimingResolver（对照）
- 相同时间段（2024-01-01 ~ 2025-06-01）、相同标的池、相同资金
- 输出对比：年化收益、Sharpe Ratio、最大回撤、胜率、Calmar Ratio

**AB回测结果（2024-01-01 ~ 2025-06-01, TOP_K=3）：**

| 指标 | ROC独立 | Bond独立 | **融合MACRO** | vs ROC |
|------|---------|----------|--------------|--------|
| 年化收益 | 44.2% | 13.0% | **44.6%** | +0.4% |
| 最大回撤 | 18.3% | 17.0% | **13.1%** | **-28%** ↓ |
| Sharpe | 1.29 | 0.67 | **1.39** | **+8%** ↑ |
| Calmar | 2.42 | 0.76 | **3.40** | **+40%** ↑ |
| 总收益 | 63.6% | 17.8% | **64.3%** | +0.7% |
| 交易次数 | 5 | 0 | 13 | +8 |
| 胜率 | 40.0% | 0.0% | **46.2%** | +6.2% |

**结论**：CDR宏观叠加有效降低了回撤（-28%），同时保持了ROC的高收益，Sharpe和Calmar均显著提升。

### 1.5 融合回测图表

- 复用 `BacktestComparator.plot_single_strategy()` 输出净值曲线
- 新增或复用对比图表，显示 CDR 曲线副图（在净值图下方叠加CDR变化）

## 四、验收标准

- [x] `MacroOverlayResolver` 能正确处理 TIMING(ERP分位=72%) + ROTATION(TOP3) → 产出3个 TargetPosition + 1个CASH
- [x] CDR 分段线性映射正确：ERP分位=72% → CDR ∈ [0.70, 0.90]
- [x] CDR 平滑生效：连续两日CDR变化不超过 α×|Δ|
- [x] 趋势过滤生效：MA50未确认时 CDR ≤ 0.5
- [x] 绝对ERP修正生效：erp < erp_abs_min 时 CDR 打折
- [x] AB对比回测能跑通：融合 vs 独立 vs BondYield，输出四个指标对比（Sharpe、最大回撤、年化收益、Calmar）
- [ ] 回测图表包含净值曲线 + CDR副图（图表已有，CDR副图留待Phase 2）
- [x] 最小仓位过滤生效：weight < min_position_pct 的标的被跳过

## 五、相关文件

| 文件 | 类型 | 说明 |
|------|------|------|
| `core/resolver.py` | 修改 | 新增 `MacroOverlayResolver` 类 |
| `core/__init__.py` | 修改 | 导出 `MacroOverlayResolver` |
| `strategies/macro_overlay.py` | 新建 | `MacroOverlayStrategy` 包装类 |
| `strategies/_configs/macro_overlay_config.py` | 新建 | 融合策略配置 |
| `strategies/__init__.py` | 修改 | 导出 `MacroOverlayStrategy` |
| `tests/_test_macro_overlay.py` | 新建 | AB对比回测脚本 |

## 六、备注

- `BondYieldStrategy` 和 `ROCStrategy` 完全不改，零侵入
- `run_backtest()` 不动——`MacroOverlayStrategy` 对外表现和普通 `Strategy` 一样
- CDR 的 EMA 平滑需要维护一个内部状态（`_cdr_state`），记录昨日的 CDR 值
- 趋势判断复用 `BondYieldStrategy` 已有的 MA50 逻辑，不重复实现
- `BacktestExecutor` 不需要改——`MacroOverlayResolver` 产生的 TargetPosition 和常规 resolver 格式完全一致