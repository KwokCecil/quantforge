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

## 回测规范
1. 统一入口：`main_backtest.py`，避免 ad-hoc 脚本
2. 统一区间：2018-01-01 ~ 至今（除非任务本身就是短期分析）
3. 统一基准：创业板指数（399006）
4. results/ 中间目录及时清理

## AI 输出规范（强制）
0. **正式输出开头必须加"锅少侠，"**（思考过程不加，只在可见的回复内容开头加）
1. 改代码后必须调 `mcp_mpm-coding_memo` 记录
2. 图表必须用 `[文件名](file:///绝对路径)` 蓝色链接输出
3. 回测必须生成走势图（净值曲线+信号标注+仓位热力图）