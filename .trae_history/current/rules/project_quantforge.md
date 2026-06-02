# QuantForge 项目配置

Python 量化交易回测系统 | Phase 4 | `.venv` 虚拟环境 | PYTHONPATH = quantforge 父目录

## MPM 项目根（重要：与 Git 根不同！）
- **MPM 项目根**：`e:\JuJu\TraeProjects\量化工程`（`.mpm-data/` 在此）
- **Git 根 / 工作目录**：`e:\JuJu\TraeProjects\量化工程\quantforge`
- ⚠️ `initialize_project` 必须显式传 `project_root="e:\JuJu\TraeProjects\量化工程"`，严禁留空（自动探测会被 quantforge/ 下的 .gitignore 误导）

## 目录结构
```
量化工程/                   ← MPM 项目根（.mpm-data 所在）
├── quantforge/            ← Git 根 & 工作目录
│   ├── strategies/        ← 策略核心逻辑
│   ├── monitors/          ← 实盘适配器（_monitor 结尾）
│   ├── core/              ← 核心引擎
│   ├── data_sources/      ← 数据源
│   ├── indicators/        ← 技术指标
│   ├── research/          ← 探索分析（无 assert，图表/报告）
│   ├── tests/             ← 自动化验证（有 assert，pass/fail）
│   ├── results/           ← 回测产物（.json/.csv，可重建）
│   ├── tools/             ← 工具函数
│   ├── config/            ← 策略 JSON 配置
│   ├── ToDo/              ← 施工文档（T{序号}_{状态}_{描述}.md）
│   ├── tokens/            ← 密钥（不入 git）
│   └── 指导文档/          ← 永久参考（不可删）
├── 简历/                  ← 非项目代码
└── 旧工程文件/            ← common.py + LOOK_BACK_ROC_CLOSE_STRATAGE.py
```

## 关键入口
- `main_backtest.py` — 回测入口（`--preset <配置名>`）
- `main_monitor.py` — 实盘监控
- `main_validate.py` — 验证管道

## 旧代码对照
重构时严格对照旧代码逻辑。发现问题记录反馈，待确认后修复。重构后必须有测试验证行为一致。

## 回测规范（强制）

### 统一入口（不可违反）
1. **所有回测必须走 `main_backtest.run_core_backtest(config)`**
   - 该函数封装了策略创建→缓存刷新→Resolver/Executor组装→回测执行→指标计算的完整流水线
   - 禁止在 research/study/临时脚本中重复实现回测逻辑（历史上因逻辑复制不一致导致多次差异事故，见 [5.13§七](file:///e:/JuJu/TraeProjects/量化工程/quantforge/指导文档/5.13_全周期参数稳健性重验证.md)）
   - 研究脚本如需回测，导入并调用此函数，而非自己造轮子
2. **配置统一从 JSON 出发**：`create_config("roc_momentum", preset)` → 再覆盖参数
   - 禁止硬编码参数字典（如 `PROD_PARAMS = {...}`），确保与生产配置同源
3. **`--core` 模式**：`main_backtest.py --core` 跳过报告/图表，仅输出核心指标 JSON
   - 供研究脚本、网格搜索、Walk-Forward 等批量回测场景使用
   - 示例：`.\.venv\Scripts\python.exe main_backtest.py --core`

### 区间与基准
1. 统一区间：2018-01-01 ~ 至今（除非任务本身就是短期分析）
2. 统一基准：创业板指数（399006）
3. results/ 中间目录及时清理

## AI 行为规范（强制）

### MPM 工具自动使用
以下是 MPM 驱动的 AI。以下工具**每次对话自动调用**，不等用户提醒。不需要用户说"启用 MPM"，默认就是开着的。

**行动前（下手前必做）：**
- `known_facts` → 查避坑经验，召回相关铁律
- `system_recall` → 查历史决策："这事以前怎么处理的？"
- `code_impact(symbol_name)` → 改函数/类前，先看影响面

**找代码（不用 grep/逐文件读）：**
- `code_search` → 按符号名定位，比 grep 准确
- `flow_trace` → 追踪业务流程上下游
- `project_map` → 迷路时看项目骨架

**行动后（改完必做）：**
- `memo` → 记录做了什么 + 为什么这么做。写之前先查最近5条 memo（system_recall），若已有内容基本相同的记录则跳过，不重复写。

**MPM 工具约束（不可违反）：**
- 禁止并发调用 MPM 工具（code_search/code_impact/flow_trace/memo 等），必须串行
- `code_search` 失败 → 必须换词重试（同义词/缩写/驼峰变体），禁止放弃
- 禁止凭记忆修改代码，必须先 code_search 定位
- 禁止 code_search 失败后直接改用 grep 替代

### 输出格式
0. **正式输出开头必须加"锅少侠，"**（思考过程不加，只在可见的回复内容开头加）
1. 改代码后必须调 `mcp_mpm-coding_memo` 记录。记录前先查最近 memo 避免重复（见上方 memo 约束）。
2. 图表必须用 `[文件名](file:///绝对路径)` 蓝色链接输出
3. 回测必须生成走势图（净值曲线+信号标注+仓位热力图）

## 规则同步规范
- `.trae/rules/` + `.trae/skills/` 是 VS Code 实际加载的生效版本（位于 Git 仓库外）
- `.trae_history/` 是规则的**只读历史存档**（位于 Git 仓库内，`quantforge/.trae_history/`）
- **禁止修改历史版本**（v3、v4 等），它们是当时状态的快照
- **规则有大改动时**（如架构重塑、新增章节超30行）：新建 `v{序号}_{描述}/` 目录，复制全部规则和技能文件进去，再覆盖更新当前版本
- 日常小改动只改 `.trae/rules/` 和 `.trae/skills/`（生效版），不同步到历史存档