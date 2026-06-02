# T001 FactorLab 封装

> **状态**：completed
> **所属阶段**：Phase 2 — 验证基础设施
> **创建日期**：2026-05-01
> **完成日期**：2026-05-03

## 一、前置依赖

- 无硬依赖。

## 二、目标

将现有的独立 IC 分析脚本重构为标准 `FactorLab` 类，实现 `compute_ic()`、`layered_backtest()` 和 `ic_matrix_scan()` 三个核心方法。

## 三、实施计划

1. 创建 `research/factor_lab.py`，实现 FactorLab 类（3个静态方法）✅
2. 从 `ic_analysis.py` 提取核心逻辑，封装为 FactorLab ✅
3. 增加 `ic_matrix_scan()` 便捷方法 ✅
4. 修改 `ic_analysis.py` 调用 FactorLab，保持独立运行能力 ✅
5. 运行真实数据验证，确认结果正确 ✅

## 四、验收标准

- [x] `from quantforge.research.factor_lab import FactorLab` 可正常导入
- [x] `FactorLab.compute_ic()` 合成数据与真实数据均正确运行
- [x] `FactorLab.layered_backtest(n_groups=5)` 返回 group_returns、is_monotonic、spread
- [x] `FactorLab.ic_matrix_scan()` 返回 (ic_matrix, icir_matrix) 两个 DataFrame
- [x] 旧 `ic_analysis.py` 仍可直接运行（`python research/ic_analysis.py`），输出不变（33/33标的加载，完整5段分析+可视化）
- [x] 代码包含必要注释（类docstring、方法docstring、关键逻辑行注释）
- [x] 使用 quantforge 绝对导入

## 五、相关文件

| 文件 | 类型 | 说明 |
|------|------|------|
| `research/factor_lab.py` | **新建** | FactorLab 类实现 |
| `research/ic_analysis.py` | 修改 | 改为调用 FactorLab，保留 CLI 功能 |
| `research/__init__.py` | 修改 | 导出 FactorLab |

## 六、备注

- **分层回测注意**：分层回测需要足够的样本量。33个ETF在单期可能不够5组，需要跨时间池化（pooling across time）
- **IC 分析的数据预处理**：确保 forward_returns 与 factor_values 的时间对齐——因子值是 t 日，forward return 应该是 t+N 日的收益
- **不要过度设计**：L3 阶段只需要 compute_ic + layered_backtest + ic_matrix_scan，不需要因子换手率、因子相关性（那是 L3.5 的内容）

## 七、IC分析结果速查（2026-05-03 实测）

| 指标 | 值 |
|---|---|
| 数据区间 | 2020-01-01 ~ 2026-05-03 |
| 加载标的 | 33/33 |
| 最优参数 | ROC(15) + Fwd(10), IC=0.0279, ICIR=0.6548 |
| ROC(22) 当期 | IC=-0.0349, ICIR=-0.4057（注意：当前参数IC为负！） |
| ROC(22) IC归零 | 持有期=13天（动量效应短于预期） |
| 分层回测 | 五组收益非单调，ROC因子区分力有限 |

> ⚠️ 重要发现：ROC(22) 在整个数据集上的 IC 均值竟然是**负的**（-0.0349），且 ICIR=-0.4057。但 ROC(15)+Fwd(10) 组合有明显正 IC（0.0279, ICIR=0.6548）。这说明当前策略参数（roc_n=22）并不是因子本身最优的——策略的有效性可能更多来自止损和买卖阈值，而非因子预测力。后续应关注：改为 ROC(15) 是否提升回测收益。
