# T010 自动化 CI 测试骨架

> **状态**：completed
> **所属阶段**：Phase 4 — 系统完善
> **创建日期**：2026-05-05
> **完成日期**：2026-05-31
> **完成日期**：（完成后填写）

## 一、前置依赖

- T001~T008 全部完成，已有 8 个 `_test_*.py` 验证脚本
- T002 暴露了关键风险：numpy dtype 与 pandas 3.x 类型不兼容的 bug 如果在自动化测试中被发现，修复时间会从"数小时反向工程"缩短到"立即定位"

## 二、目标

为已有 `_test_*.py` 脚本建立统一运行入口（`run_all_tests.py`），每次提交前手动运行确认无回归。另为核心模块补上关键的类型/边界单元测试。

## 三、实施计划

### Step 1：创建 `run_all_tests.py` 统一入口 ✅ 已完成

已实现 `tests/run_all_tests.py`，设计要点：

- **自动发现**：`glob("_test_*.py")` + `glob("_verify_*.py")`，新增测试文件零配置自动纳入
- **分层区分**：读取每个文件首行 `# @layer: unit|contract|integration|e2e` 自动识别快慢
- **默认模式**（`python run_all_tests.py`）：只跑 unit + contract（10个测试，~10s）
- **全量模式**（`python run_all_tests.py --all`）：跑全部（14个测试，~25s）
- **按层过滤**（`python run_all_tests.py --layers unit,contract`）：指定层

未来新增任何 `_test_*.py` 只需遵循命名约定 + `@layer` 标记，无需修改 `run_all_tests.py`。

### Step 2：为核心模块补单元测试 ✅ 已完成（由 T027 落地）

T027 已创建 6 个单元测试文件覆盖以下模块（全部使用合成数据，<1s 运行）：

| 模块 | 测试重点 | 文件 |
|------|---------|------|
| `executor.py` | BE 买入/卖出/追加/整手/资金不足/高水位 11场景 | `tests/_test_executor_unit.py` |
| `resolver.py` | RankingResolver 4权重+2止损+top_k_sell 12场景 | `tests/_test_resolver_unit.py` |
| `resolver.py` | MacroOverlayResolver CDR映射+EMA平滑 13场景 | `tests/_test_macro_resolver_unit.py` |
| `resolver.py` | TimingResolver enter/exit+bond_etf 8场景 | `tests/_test_timing_resolver_unit.py` |
| `technical.py` | 8种Indicator + MA/REF底层函数 10场景 | `tests/_test_indicator_unit.py` |
| `decision.py`/`config.py` | Decision默认值/Config往返 3场景 | `tests/_test_decision_unit.py` |

### Step 3：确保现有测试脚本可重复运行 ⚠️ 部分完成

检查每个 `_test_*.py` 是否满足：
- 可独立运行（`python _test_xxx.py` 直接跑）
- 依赖数据有兜底（缓存文件缺失时优雅跳过而非崩溃）
- 无硬编码路径（使用相对于脚本自身的路径）

## 四、验收标准

- [x] `run_all_tests.py` 能遍历执行所有 `_test_*.py` 并统计通过/失败（14/14 PASS）
- [x] 核心模块（indicator / resolver / executor / decision）各有 ≥ 2 个单元测试
- [x] 所有单元测试使用合成数据，运行时间 < 5 秒
- [x] 现有 `_test_*.py` 全部可独立运行且通过
- [x] `run_all_tests.py` 默认模式运行 10.1s（不含网络），`--all` 模式 24.5s

## 五、相关文件

| 文件 | 类型 | 说明 |
|------|------|------|
| `tests/run_all_tests.py` | **新建** | 统一测试入口（glob自动发现+@layer分层） |
| `tests/_test_executor_unit.py` | 新建 | Executor 单元测试 11场景 |
| `tests/_test_resolver_unit.py` | 新建 | RankingResolver 单元测试 12场景 |
| `tests/_test_macro_resolver_unit.py` | 新建 | MacroOverlayResolver 单元测试 13场景 |
| `tests/_test_timing_resolver_unit.py` | 新建 | TimingResolver 单元测试 8场景 |
| `tests/_test_indicator_unit.py` | 新建 | 底层指标函数单元测试 10场景 |
| `tests/_test_decision_unit.py` | 新建 | Decision/Config 单元测试 3场景 |
| `ToDo/T027_D_核心模块单元测试.md` | 施工文档 | 本任务与 T027 互补 |

## 六、备注

- 不是 CI/CD 级（不上 GitHub Actions），就是手动跑的本地脚本
- 涉及网络/缓存的 `_test_*.py` 在 `run_all_tests.py` 中可加 `--skip-slow` 参数跳过
- 优先级：indicator > resolver > executor > decision
