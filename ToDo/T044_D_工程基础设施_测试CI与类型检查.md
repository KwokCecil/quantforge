# T044 工程基础设施：测试、CI 与类型检查

> **状态**：✅ completed
> **所属阶段**：Phase 4 — 基础设施补充
> **创建日期**：2026-05-31
> **完成日期**：2026-06-01
> **来源**：3.12_独立第三方项目评估报告 — 问题4/7/8

## 一、前置依赖

- T043 代码质量提升（重构完成后代码稳定，再建回归测试和类型检查）

## 二、目标

补齐项目缺失的工程基础设施：端到端回归测试（防止核心引擎回归）、CI/CD 自动化流水线（提交门控）、mypy 静态类型检查（消除 `Any` 滥用）。

## 三、实施计划

### Step 1：端到端回归测试（4h）

用固定数据快照运行完整回测流水线，断言最终指标与基线一致。

- 选取 `tech_growth` 预设，跑一次回测，将数据序列化为 `tests/fixtures/` 快照文件
- 实现 `MockDataFeed`：从快照加载数据，不依赖网络
- 编写 `tests/_test_backtest_regression.py`：断言 `sharpe`、`total_return`、`max_drawdown` 与基线偏差 < 0.001
- 纳入 `run_all_tests.py`（`# @layer: integration`）

### Step 2：CI/CD 自动化（2h）

创建 `.github/workflows/ci.yml`：

- trigger: push/PR 到 main
- 步骤：checkout → 设置 Python 3.12 → `pip install -r requirements.txt` → `python tests/run_all_tests.py`
- 后续加入 mypy 步骤（Step 3 完成后）

### Step 3：mypy 类型检查（4h+）

渐进式引入静态类型检查：

- 安装 `mypy`，创建 `mypy.ini` 配置（初期宽松，逐步收紧）
- 修复关键类型问题：`positions: dict[str, Any]` → 定义 `Position` TypedDict
- 核心模块（`core/`、`strategies/`）类型标注覆盖率 > 80%
- 加入 CI 流水线

## 四、验收标准

- [x] 回归测试存在，数据快照在 `tests/fixtures/`，不依赖网络
- [x] 回归测试覆盖至少两个预设配置
- [x] `.github/workflows/ci.yml` 存在，push/PR 自动触发
- [x] CI 流水线全部通过（绿灯）
- [x] `mypy .` 运行零 ERROR（Success: no issues found in 57 source files）
- [x] `positions` 类型从 `dict[str, Any]` 改为明确的 TypedDict
- [x] mypy 已纳入 CI 流水线（typecheck job）

## 五、相关文件

| 文件 | 类型 | 说明 |
|------|------|------|
| `tests/fixtures/` | 新建 | 数据快照 |
| `tests/_test_backtest_regression.py` | 新建/修改 | 回归测试 |
| `tests/_mock_data_feed.py` | 新建 | MockDataFeed |
| `.github/workflows/ci.yml` | 新建 | CI 配置 |
| `mypy.ini` | 新建 | mypy 配置 |
| `requirements.txt` | 修改 | 添加 mypy |
| `core/strategy.py` | 修改 | Position TypedDict |
| `core/executor.py` | 修改 | 返回类型明确化 |
| `core/decision.py` | 修改 | 数据结构类型完善 |

## 六、备注

- Step 1 和 Step 2 优先做，Step 3（mypy）是渐进式任务，可后续迭代收紧
- 总预估工时：10h+
- 数据快照可能较大（~10MB），建议 Git LFS 或只保留前 200 交易日作为冒烟测试快照