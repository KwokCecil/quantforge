# T020 RSI 单因子替代 ROC 动量策略

> **状态**：deprecated — 被 T025 全周期验证推翻（RSI≥60 在 2018-2026 全周期 0 笔交易触发）  
> **所属阶段**：Phase 4 — 策略增强（已废弃）  
> **摘要**：RSI 单因子替代 ROC 的方案在短周期（2023-2025）Sharpe=0.95 vs Baseline 1.50 已不如 ROC，在全周期（2018-2026）彻底失效——0 笔交易。RSI 有界(0~100) vs ROC 无界(±∞)是结构性劣势。  
> **创建日期**：2026-05-05  
> **完成日期**：2026-05-05

## 一、前置依赖

- **T012** 完成：FactorLab 发现 RSI(14) ICIR=0.83 >> ROC(22) ICIR=0.36
- **T005** 完成：inv_vol_weight 有效，保持启用
- RSIIndicator 已在 T012 Step1 中实现

## 二、目标

用 **RSI(14) 替代 ROC(22)** 作为动量策略的主力排名因子和信号因子，验证是否优于当前 ROC 单因子 Baseline（Sharpe=1.37，收益 79.6%）。

## 三、实施计划

### Step 1：扩展配置

在 `ROCConfig` 中新增：

```python
primary_factor: str = "ROC"       # 主力排名因子: "ROC" | "RSI"
rsi_buy_threshold: float = 60.0   # RSI买入阈值（高于此值看多）
rsi_sell_threshold: float = 40.0  # RSI卖出阈值（低于此值看空）
```

### Step 2：实现 RSI 单因子决策流

在 `roc_momentum.py` 中新增 `_produce_rsifactor_decisions()`：

- 排名：按 RSI(14) 降序排序（替代 ROC 排序）
- 买入信号：RSI >= rsi_buy_threshold
- 卖出信号：RSI <= rsi_sell_threshold
- 保持 MA_PRICE_CROSS、STRICT_BUY 等辅助条件不变
- 保持 inv_vol_weight 加权
- 保持 RankingResolver 信号加权逻辑

### Step 3：回测验证（单次，先看默认值）

| 配置 | 排名因子 | 买入阈值 | 
|------|---------|---------|
| Baseline | ROC(22) | ROC >= 20 |
| RSI_default | RSI(14) | RSI >= 60 |

### Step 4：RSI 阈值扫描

如果 RSI_default 表现有潜力，对 rsi_buy_threshold 在 50~80 范围扫描（步长 5），找到最优阈值。

### Step 5：结论

- RSI 是否在 Sharpe/收益上优于 ROC？
- 如优于：确认最优参数组合
- 如不优于：如实记录，ROC 保持主力

### Step 3：回测结果（自建回测循环，7标的，2023-08~2025-06，本金 40k）

| 配置 | 收益率 | Sharpe | 回撤 | 交易 |
|------|--------|--------|------|------|
| **Baseline_ROC_22** | **81.5%** | **1.50** | **11.4%** | 15 |
| RSI_buy60 (最优RSI) | 48.4% | 0.95 | 25.5% | 61 |
| RSI_buy55 | 25.6% | 0.57 | 30.7% | 82 |
| RSI_buy65 | 3.2% | 0.19 | 21.8% | 43 |
| RSI_buy50 | 31.4% | 0.63 | 37.3% | 71 |
| RSI_buy45 | 41.3% | 0.75 | 31.3% | 64 |

**结论：RSI 单因子在所有阈值下均不如 ROC(22)。**

- ROC(22) Sharpe=1.50 碾压 RSI best=0.95
- RSI 交易频率高 4-5x（频繁进出 → 更多佣金/滑点）
- RSI 回撤 2-3x（均值回归特性导致过早卖出，错过主升浪）
- 虽然 T012 发现 RSI ICIR=0.83 > ROC ICIR=0.36，但 IC 衡量的是跨截面预测力（33只中谁更好），而非时序择时能力。在这 7 只精选标的上，ROC 的持久动量信号远优于 RSI 的短期震荡信号。

### 最终结论

**ROC(22) 仍然是动量策略的最优主力因子。无需替换。**

## 四、验收标准

- [x] `_produce_rsifactor_decisions` 合成数据测试通过
- [x] 新增配置项可正常导入
- [x] RSI vs ROC 回测对比完成（含多阈值扫描）
- [x] 结论记录清楚：RSI 不优于 ROC

## 五、相关文件

| 文件 | 类型 | 说明 |
|------|------|------|
| `strategies/_configs/roc_config.py` | 修改 | 新增 primary_factor, rsi_factor_buy/sell |
| `strategies/roc_momentum.py` | 修改 | 新增 `_produce_rsifactor_decisions()` + `_evaluate_rsi()` |
| `临时文件/T020_final_compare.py` | 新建 | 正确的 RSI vs ROC 回测对比脚本 |

## 六、备注

- RSI 值域 0~100 vs ROC 值域约 -100~100，阈值语义不同。RSI=60 大致相当于"偏强"，RSI=70≈"很强"
- RSI 有均值回归特性——长时间高位后会自然回落，即使趋势未结束。这可能导致过早卖出
- **ICIR 高 ≠ 策略收益高**：这是本次最重要的认识。IC 衡量截面 rank 能力，但策略收益取决于时序择时 + 集中持仓结构
- **动量策略研究线完整结论**：
  - T005：多周期 ROC 投票 → 失败（共线性）
  - T012：多指标投票（ROC+RSI+MACD）→ 无效（共线性）
  - T020：RSI 单因子替代 ROC → 失败（时序择时弱）
  - **ROC(22) + inv_vol_weight = 动量策略最优解**
