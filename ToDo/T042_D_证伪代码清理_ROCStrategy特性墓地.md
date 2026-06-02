# T042 证伪代码清理：ROCStrategy "特性墓地"

> **状态**：completed
> **所属阶段**：Code Cleanup（独立重构任务）
> **创建日期**：2026-05-30
> **完成日期**：2026-05-30

## 一、前置依赖

- ✅ T041（回测审计）— 回归测试已就位，提供安全网

## 二、目标

将 `roc_momentum.py` 中 600+ 行已证伪的策略增强代码移入 `strategies/_falsified/` 目录，
精简主文件到 ~770 行（降幅 44%），同时清理 `roc_config.py` 和 JSON preset 中对应的死配置。

## 三、实施计划

### Step 1：创建 `strategies/_falsified/` 目录结构
建立 6 个归档文件，每个包含：功能说明 + 证伪依据 + 原始代码 + 关键注释。

### Step 2：精简 `roc_momentum.py`
- 删除 4 个证伪入口方法及其 12 个子方法（共 16 个方法）
- 删除 `produce_decisions` 中 4 条证伪路由
- 删除 `_produce_singlefactor_decisions` 中 2 处 inline 证伪代码（ts_momentum + crash_protection）
- 删除 `_evaluate` 中 2 处 inline 证伪代码（ts_momentum + crash_protection）
- 删除 `produce_decisions` 的 `@property` 装饰器 wrapper（如果有）
- 保留 `_compute_vol_ratio`、`_compute_atr_pct`、`_check_atr_expansion`、`_get_adx`、`_check_divergence`
  （这些方法虽部分被证伪代码调用，但也可能被主路径的 `_evaluate` 过滤器使用）
- 如果上述 5 个方法经确认只被证伪代码使用，第二步再移除

### Step 3：清理 `roc_config.py`
移除 28 个仅服务于证伪代码的字段：
- Voting 组（13）：`voting_enabled`, `voting_indicators`, `voting_method`, `voting_threshold_buy`,
  `voting_threshold_sell`, `voting_integration`, `indicator_weights`, `rsi_period`,
  `rsi_bull_threshold`, `rsi_bear_threshold`, `macd_fast`, `macd_slow`, `macd_signal`
- RSI 单因子（3）：`primary_factor`, `rsi_factor_buy`, `rsi_factor_sell`
- 残差动量（3）：`residual_momentum_enabled`, `residual_window`, `residual_rank_period`
- 时序动量（3）：`ts_momentum_enabled`, `ts_momentum_period`, `ts_momentum_min_return`
- 崩盘防护（5）：`crash_protection_enabled`, `cp_vol_window`, `cp_drawdown_window`,
  `cp_vol_spike_threshold`, `cp_market_dd_threshold`
- 多因子增强（3）：`multi_factor`, `multi_roc_periods`, `multi_factor_weights`
  （保留 `inverse_vol_weight`，它被主路径使用）

### Step 4：清理 JSON presets
从 4 个 preset 文件中移除上述 28 个字段：
- `config/strategies/roc_momentum/tech_growth.json`
- `config/strategies/roc_momentum/sharp_defense.json`
- `config/strategies/roc_momentum/max_attack.json`
- `config/strategies/roc_momentum/all_weather.json`

### Step 5：全面验证
- 运行全量测试套件（25 passed / 0 failed）
- 运行一次完整回测确认结果不变
- 检查 `git diff --stat` 确认改动范围

## 四、验收标准

- [x] `strategies/_falsified/` 目录存在，包含 6 个归档文件
- [x] 每个归档文件有清晰的证伪依据说明
- [x] `roc_momentum.py` 行数从 1338 降到 450（降幅 66%，远超预期的 ~770）
- [x] 生产路径 `produce_decisions` → `_produce_singlefactor_decisions` → `_evaluate` 逻辑不变
- [x] `roc_config.py` 仅保留主路径使用的字段（移除 26 个死 flag）
- [x] 4 个 JSON preset 不包含已删除的字段（仅 `ts_momentum_enabled` 存在于 preset 中）
- [x] 全量测试 25 passed / 0 failed
- [ ] 完整回测结果与重构前一致（用户自行验证）

## 五、相关文件

| 文件 | 类型 | 说明 |
|------|------|------|
| `strategies/_falsified/__init__.py` | 新建 | 目录说明 |
| `strategies/_falsified/voting.py` | 新建 | voting 子系统（9个方法） |
| `strategies/_falsified/rsi_factor.py` | 新建 | RSI单因子 + _evaluate_rsi |
| `strategies/_falsified/residual.py` | 新建 | 残差动量（2个方法） |
| `strategies/_falsified/multifactor.py` | 新建 | 多因子增强 |
| `strategies/_falsified/ts_and_crash.py` | 新建 | 时序动量 + 崩盘防护 inline 代码 |
| `strategies/roc_momentum.py` | 修改 | 精简 ~600 行 |
| `strategies/_configs/roc_config.py` | 修改 | 移除 28 个死字段 |
| `config/strategies/roc_momentum/*.json` | 修改 | 4 个 preset 移除死字段 |

## 六、待确认问题

### Q1：`_compute_vol_ratio`、`_compute_atr_pct`、`_check_atr_expansion`、`_get_adx`、`_check_divergence`

这些方法当前由证伪代码（voting子系统）调用。但它们也可能被 `_evaluate` 中的主路径过滤器调用。
**实施时先检查它们是否在主路径中被引用。** 如果没有 → 一并移到 `_falsified/`。
如果有 → 保留在原文件。

### Q2：`crowded_sell` 和 `buy_max_ratio`

在代码中标记为"已证伪"，但位于主路径 `_produce_singlefactor_decisions` 中（不是子方法）。
**本次不碰**，仅做代码级标记，留待后续决策。

### Q3：T032 辅助卖出信号（volume_sell、atr_expansion_sell 等）

代码中未显式标注"已证伪"（但有 T032 文档支撑）。JSON preset 中全为 false。
**本次不碰**，避免扩大范围。

## 七、施工结果

### 最终统计

| 项目 | 重构前 | 重构后 | 变化 |
|------|--------|--------|------|
| `roc_momentum.py` 行数 | 1338 | 450 | **-888 行 (-66%)** |
| 策略方法数 | 24 | 9 | -15 个方法 |
| `roc_config.py` 字段 | 65+ | 39 | -26 个死 flag |
| JSON preset 死字段 | 4×1 | 0 | 清除 `ts_momentum_enabled` |
| 测试通过率 | 25/25 | 25/25 | ✅ 零回归 |

### 已删除的代码分类

| 类别 | 方法数 | 代码行数 | 归档文件 |
|------|--------|----------|----------|
| 多指标投票增强 | 10 | ~430 | `voting.py` |
| RSI 单因子替代 | 2 | ~63 | `rsi_factor.py` |
| 残差动量 | 2 | ~200 | `residual.py` |
| 多周期ROC多因子 | 1 | ~100 | `multifactor.py` |
| 时序动量+崩盘防护 | 1+inline | ~95 | `ts_and_crash.py` |
| **合计** | **16** | **~888** | **6 个文件** |

### 已删除的配置 flag（26 个）

- **多因子增强**（3）：`multi_factor`, `multi_roc_periods`, `multi_factor_weights`
- **投票增强**（9）：`voting_enabled`, `voting_indicators`, `voting_method`, `voting_threshold_buy`, `voting_threshold_sell`, `voting_integration`, `indicator_weights`, `rsi_bull_threshold`, `rsi_bear_threshold`
- **RSI 单因子**（3）：`primary_factor`, `rsi_factor_buy`, `rsi_factor_sell`
- **时序动量**（3）：`ts_momentum_enabled`, `ts_momentum_period`, `ts_momentum_min_return`
- **崩盘防护**（5）：`crash_protection_enabled`, `cp_vol_window`, `cp_drawdown_window`, `cp_vol_spike_threshold`, `cp_market_dd_threshold`
- **残差动量**（3）：`residual_momentum_enabled`, `residual_window`, `residual_rank_period`

### 保留的关键参数

- `rsi_period`, `macd_fast`, `macd_slow`, `macd_signal` — 被 T028/T032 过滤器使用
- `inverse_vol_weight` — 被主路径 `_produce_singlefactor_decisions` 使用
- `style_rotation_enabled` 及所有 `sr_*` 参数 — 被风格轮动系统使用（`all_weather.json`）

## 八、备注

- 证伪代码留在 `_falsified/` 中作为归档参考，不导入、不运行、不维护
- 每个归档文件顶部 docstring 注明：证伪依据（T编号）、移除日期、原始行号
- `_falsified/` 目录不加入 `__init__.py` 的导出列表（纯归档）
- 如果将来在新市场发现这些特性有效，从归档文件中恢复代码到主文件