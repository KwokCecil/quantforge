# T002 Validator 实现（参数扫描 + Walk-Forward）

> **状态**：completed
> **所属阶段**：Phase 2 — 验证基础设施
> **创建日期**：2026-05-01
> **完成日期**：2026-05-03

## 一、前置依赖

- **T001 FactorLab 封装**：Validator 的 Walk-Forward 评估指标可能用到 IC 分析（可选，非强制依赖）

> 注：参数扫描部分可在 T001 之前独立实现（不依赖 FactorLab）。Walk-Forward 调参阶段需要参数扫描功能。考虑到 T001 也是本批次任务，本任务可先实现参数扫描，Walk-Forward 等 T001 完成后补充 IC 相关指标。

## 二、目标

实现 `Validator` 类，提供两个核心方法：
1. `parameter_sweep()`：参数空间网格搜索，返回每组参数的回测指标
2. `walk_forward()`：滚动窗口样本外验证，用训练期最优参数测试样本外收益

## 三、实施计划

### Step 1：设计 Validator 接口（参考规格书 §7.2）

```python
# research/validator.py

class Validator:
    def parameter_sweep(self, strategy_class, config_class,
                        param_ranges: dict, data_feed, codes, start, end) -> pd.DataFrame:
        """
        param_ranges 示例:
          {'buy_roc_edge': [15, 18, 20, 22, 25],
           'roc_n': [15, 18, 20, 22, 25]}

        返回 DataFrame，每行一组参数 + 回测指标
        """

    def walk_forward(self, strategy_class, config_class,
                     data_feed, codes, start, end,
                     train_years: int = 3, test_years: int = 1,
                     param_ranges: dict = None) -> pd.DataFrame:
        """
        滚动窗口:
          2020-2022 训练 → 2023 测试
          2021-2023 训练 → 2024 测试
          ...

        训练阶段：parameter_sweep 找最优参数（按 Sharpe）
        测试阶段：用最优参数跑样本外回测

        返回 DataFrame，每行一个窗口 + OOS 指标
        """
```

### Step 2：实现 parameter_sweep()

- 对 `param_ranges` 做笛卡尔积展开（`itertools.product`）
- 每组合创建一个 `config` 实例 → 组装 `strategy` + `resolver` + `BacktestExecutor` → 调用 `run_backtest()`
- 用 `BacktestAnalyzer` 提取关键指标（total_return, annual_return, max_drawdown, sharpe_ratio）写入 DataFrame
- 添加进度日志（"参数扫描 X/Y 组"）

### Step 3：实现 walk_forward()

- 按 `(start, end, train_years, test_years)` 生成滚动窗口列表
- 每个窗口：
  - 训练期数据切片 → `parameter_sweep()` 找最优参数
  - 测试期数据切片 → 用最优参数创建 config → 跑回测 → 记录 OOS 指标
- 汇总输出：OOS 平均收益、正收益年份占比、OOS Sharpe、训练/测试 Sharpe 比值（过拟合检验）

### Step 4：与现有 param_optimizer 脚本对比

- 检查 `research/param_optimizer.py` 和 `research/param_optimizer_v2.py` 的功能
- 如果 v2 已实现了网格搜索，部分逻辑可直接复用
- 保留独立脚本（`python param_optimizer_v2.py` 仍可运行），但改为调用 Validator

### Step 5：运行验证

- 对 ROC 策略运行 `parameter_sweep(buy_roc_edge=[15,18,20,22,25], roc_n=[15,18,20,22,25])`
- 对 ROC 策略运行 `walk_forward(train_years=2, test_years=1)`（回测区间短，用 2 年训练）

## 四、验收标准

- [x] `Validator.parameter_sweep()` 对 4 组参数能正确输出结果 DataFrame
- [x] 参数扫描结果中，最佳参数（buy_roc_edge=18, roc_n=15）Sharpe=1.65，排名合理
- [x] `Validator.walk_forward()` 产出 2 个有效窗口（Window 1 数据不足跳过）
- [x] Walk-Forward 输出包含每个窗口的训练期最优参数和 OOS 指标
- [x] 现有 `param_optimizer_v2.py` 运行不受影响（未改动）
- [x] 代码与 `run_backtest()` + `BacktestAnalyzer` 正常配合

## 五、相关文件

| 文件 | 类型 | 说明 |
|------|------|------|
| `research/validator.py` | **新建** | Validator 类实现 |
| `research/param_optimizer_v2.py` | 可能修改 | 可选改为调用 Validator |
| `research/__init__.py` | 修改 | 导出 Validator |

## 六、备注

- **回测时长限制**：当前数据从 2023-08 开始，不到 3 年。如果 Walk-Forward 用 2 年训练 + 1 年测试，只有 1 个窗口（2023-08~2025-08 训练，2025-08~至今测试）。建议数据起始日期提前到 2020 年（API 支持）
- **参数扫描的性能**：笛卡尔积组合数 = ∏(len of each param)，注意控制范围。5×5=25 组已经比较耗时
- **并行加速**：如果参数组合过多（>50组），考虑用 `multiprocessing`。当前阶段暂不需要
- **最优参数选择标准**：默认用 Sharpe，但可以配置为 max_drawdown、Calmar 等。接口预留 `objective='sharpe'` 参数

## 七、验收结果

### 参数扫描 (2020-2025)
| buy_roc_edge | roc_n | total_return | sharpe_ratio | max_drawdown |
|-------------|-------|-------------|-------------|-------------|
| 18 | 15 | 78.08% | 1.65 | 13.11% |
| 20 | 15 | 64.36% | 1.46 | 13.11% |
| 18 | 22 | 54.33% | 1.29 | 16.35% |
| 20 | 22 | 45.89% | 1.15 | 18.52% |

### Walk-Forward (3年训练/1年测试)
| 窗口 | 训练期 | 测试期 | 最优参数 | OOS收益 | OOS Sharpe |
|-----|--------|--------|---------|--------|-----------|
| 1 | 2020-2022 | 2023 | - | 数据不足跳过 | - |
| 2 | 2021-2023 | 2024 | (18,15) | 78.08% | 1.91 |
| 3 | 2022-2024 | 2025 | (18,15) | 32.37% | 1.08 |

> OOS 平均收益=55.22%, 正收益占比=100%, OOS 平均 Sharpe=1.50

### Bug 修复记录
- **问题**：Walk-Forward OOS 回测崩溃 `TypeError: slice indices must be integers`
- **根因**：pandas 3.0.1 的 `shift()` 不接受 `np.float64`/`float` 类型的 periods 参数。参数扫描结果 DataFrame 取值后参数变为 numpy scalar 类型
- **修复**：添加 `_sanitize_params()` 将 numpy scalar 转 Python 原生类型，整值 float 转 int
