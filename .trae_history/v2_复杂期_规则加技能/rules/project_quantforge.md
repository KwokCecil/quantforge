# 项目配置 — QuantForge

> **作用域**：仅本项目（QuantForge 量化交易系统）。
> **配合文件**：`.trae/rules/engineering_standards.md`（Git规范、AI行为边界、任务流程、代码注释、文件归档等通用工程规范）。
> **前置依赖**：核心行为原则已写入 IDE Agent 用户级设置。
> **说明**：本文件仅包含 QuantForge 项目特有的配置。通用开发规范请查阅 `engineering_standards.md`。

---

## 一、项目信息

| 字段 | 值 |
|------|-----|
| 项目名称 | QuantForge |
| 项目类型 | Python 量化交易回测系统 |
| 当前阶段 | Phase 4（重构迁移期） |

---

## 二、技术栈

| 组件 | 版本/工具 |
|------|----------|
| 语言 | Python 3.x |
| 包管理 | pip（`requirements.txt`） |
| 虚拟环境 | 项目级 `.venv` |
| 测试框架 | {填写，如 pytest} |
| Lint/Format | {填写，如 ruff} |

---

## 三、环境与路径

> **详见 skill**：`python-env`（虚拟环境、命令白名单、常见错误）

- 代码目录（Git 仓库根目录）：`quantforge/`
- PYTHONPATH 为 quantforge **父目录**，使 `from quantforge.xxx import yyy` 能正常导入

---

## 四、目录结构

```
{quantforge 父目录}/
├── quantforge/               ← Git 仓库根目录 & 工作目录
│   ├── .venv/                ← 虚拟环境（不入版本控制）
│   ├── ToDo/                 ← 施工文档存放目录
│   ├── tokens/               ← 密钥文件（不入版本控制）
│   │   └── _templates/       ← 密钥模板（入版本控制）
│   ├── 指导文档/              ← 永久参考资料（不可删除）
│   ├── strategies/           ← 策略实现 + _configs/ 配置
│   ├── monitors/             ← 实盘监控适配器（xxx_monitor.py）
│   ├── core/                 ← 核心引擎（回测/实盘）
│   ├── data_sources/         ← 数据源（Sina/Auto/WebScraper）
│   ├── indicators/           ← 技术指标
│   ├── research/             ← 探索分析 + 可复用库
│   ├── tests/                ← 自动化验证（断言驱动）
│   ├── results/              ← 回测产出数据（只读，可重建）
│   ├── tools/                ← 工具函数
│   └── config/               ← 策略 JSON 配置
├── 旧工程文件/                ← 旧代码对照（在 quantforge 父目录中）
│   ├── common.py
│   └── LOOK_BACK_ROC_CLOSE_STRATAGE.py
```

### ToDo 目录

- 施工文档存放位置：`quantforge/ToDo/`
- 命名规则：`T{序号}_{状态字母}_{简短描述}.md`，三位数编号
  - `P` = 未开始，`I` = 进行中，`D` = 已完成
  - 状态变更时同步修改文件名

---

## 五、Git 工作流

> **详见 skill**：`git`（提交格式、分支策略、commit 纪律、AI 行为边界、Phase 合并流程）

---

## 六、密钥管理

> **详见 skill**：`secrets`（存放位置、模板机制、AI 禁止操作）

---

## 七、旧代码对照（重要）

旧工程文件位于 `旧工程文件/` 目录（在 quantforge 的**父目录**中），包含 `common.py` 和 `LOOK_BACK_ROC_CLOSE_STRATAGE.py`。

**行为准则**：
- 在重构时，应严格对照旧代码逻辑，生成新代码。发现问题时，记录和反馈，待确认后再修复
- 重构后必须生成有效的测试案例，验证新旧代码行为一致

**新代码相对旧代码的改进**（逻辑更合理但回测收益可能不同）：

| 改进项 | 新行为 | 旧行为 | 理由 |
|--------|--------|--------|------|
| 不在 TOP_K 的持仓 | 立即卖出 | 仅在有空间压力时才挤压卖出 | 动量策略逻辑更自洽 |
| BUY_AVERAGE | 按实际买入数量等分 | 按 TOP_K 等分 | 避免资金闲置 |

---

## 八、命令白名单

> **详见 skill**：`python-env`（白名单/黑名单、正确用法）

---

## 九、代码地图

> **详见**：`AI_CODE_MAP.md` — 项目完整代码结构地图（自动生成，`tools/generate_code_map.py` 维护）。

## 十、根目录关键文件

| 文件 | 分类 | 说明 |
|------|------|------|
| `main_backtest.py` | 主入口 | 回测运行入口 |
| `main_monitor.py` | 主入口 | 实盘监控入口 |
| `main_validate.py` | 主入口 | 验证管道入口 |
| `monitor.bat` | 运维脚本 | 监控启动脚本 |
| `fix_task.bat` | 运维脚本 | 任务修复脚本 |
| `remote_cmd.bat` | 运维脚本 | 远程指令接收 |
| `remote_cmd.py` | 运维脚本 | 远程指令处理 |
| `git_pull.py` | 运维脚本 | 自动拉取代码 |
| `send_error_log.py` | 运维脚本 | 错误日志推送 |
| `position.json` | 运行时状态 | 当前持仓、成本、高水位线 |
| `requirements.txt` | 环境配置 | Python 依赖清单 |
| `.gitignore` | 环境配置 | Git 排除规则 |
| `__init__.py` | 包标识 | quantforge 包初始化 |

---

## 十一、关键目录职责与命名

### 10.1 strategies/ vs monitors/

| | `strategies/` | `monitors/` |
|---|---|---|
| **职责** | 策略核心逻辑（信号计算） | 实盘监控适配器（数据源+执行器+调度） |
| **内容** | 策略类（继承 Strategy） | 监控函数（`xxx_monitor()`），被 `main_monitor.py` 多进程调用 |
| **依赖方向** | core / indicators / data_sources | → strategies + core + data_sources |
| **运行环境** | 回测 & 实盘共享 | 仅实盘 |

**关系**：`monitors/` 是 strategies 的"实盘胶水层"——它负责把策略 + 实盘数据源 + LiveExecutor + 定时调度（CHECKPOINTS）串起来。一个 strategy 可能对应 0~1 个 monitor。

**命名规则**：`monitors/` 下的文件必须以 `_monitor` 结尾，避免与 `strategies/` 同名混淆。
- `strategies/roc_momentum.py` → `monitors/roc_momentum_monitor.py`
- `strategies/bond_yield.py` → `monitors/bond_yield_monitor.py`

### 10.2 tests/ vs research/ vs results/

> **详见**：`指导文档/3.11_tests_research_results_目录职责边界.md`

| | `tests/` | `research/` | `results/` |
|---|---|---|---|
| **一句话** | 验证正确性的自动化检查 | 探索性分析、实验、工具库 | 回测/扫描产出的数据 |
| **核心问题** | "代码还对吗？" | "这样做好不好？" | "那次跑出了什么？" |
| **内容性质** | 断言驱动，输出 pass/fail | 计算驱动，输出图表/报告/发现 | 数据驱动，只读不执行 |
| **关键特征** | 有 assert，exit code 0/1 | 无断言，结果需人解读 | 不含 .py，可随时删除重建 |
| **归属决策** | 有 assert 且独立跑 → tests/ | 无 assert / 扫描工具 / 对比分析 / 可复用库 → research/ | 是 .json/.csv 回测产物 → results/ |

#### 测试自动发现

`tests/run_all_tests.py` 自动发现所有匹配 `_test_*.py` 或 `_verify_*.py` 的文件，按文件首行 `# @layer:` 标记分层运行。**新增测试只需满足两个条件即可零配置自动纳入：**

1. **文件名** 以 `_test_` 或 `_verify_` 开头，放在 `tests/` 目录下
2. **第一行** 注明 `# @layer: unit | contract | integration | e2e`

运行方式：
```bash
python tests/run_all_tests.py              # 默认：unit + contract（~10s）
python tests/run_all_tests.py --all         # 全量（含网络测试，~25s）
python tests/run_all_tests.py --layers unit # 按层过滤
```

---

## 十二、回测规范

1. **统一入口**：回测必须通过 `main_backtest.py` 运行（`--preset <配置名>`），原则上不使用 research/ 下的 ad-hoc 回测脚本。

2. **统一区间**：回测区间必须为 **2018-01-01 ~ 至今**（长区间），除非任务本身就是短期分析（如 T028 单信号级别的统计无需全区间）。

3. **统一基准**：走势图基准使用 **创业板指数（399006）**，原因：策略标的为科技成长 ETF 池，创业板比沪深300更能代表策略的真实对标。

4. **结果管理**：`results/` 下的中间调试回测目录及时清理，只保留有结论意义的最终回测结果。
