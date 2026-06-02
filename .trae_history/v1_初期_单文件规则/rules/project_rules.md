# 项目规则

## Python 虚拟环境

本工程使用项目级 `.venv` 虚拟环境，路径为 `quantforge/.venv`。

- **运行 Python 脚本**：必须使用 `.venv\Scripts\python.exe`，而非系统级 `python`
  - 示例：`.\.venv\Scripts\python.exe main_backtest.py`
- **安装依赖**：必须使用 `.venv\Scripts\pip.exe`，而非系统级 `pip`
  - 示例：`.\.venv\Scripts\pip.exe install -r requirements.txt`
- **创建新虚拟环境**：`python -m venv .venv`（仅在 .venv 不存在时）

## 工作目录

- 代码目录（也是 git 仓库根目录）：`quantforge/`
- 运行命令时，cwd 应设为 `quantforge/`

## PYTHONPATH

- 运行 quantforge 模块时，PYTHONPATH 应设为 quantforge 的**父目录**
  - 即 PYTHONPATH 指向包含 quantforge 包的目录，使得 `from quantforge.xxx import yyy` 能正常工作

## Git 工作流

### 分支策略
- **main**：稳定可运行版本，运行机只拉此分支
- **dev**：日常开发分支，AI辅助编码在此进行
- 当前工作分支：dev

### 提交规范（Conventional Commits）
- `feat:` 新功能
- `fix:` 修复bug
- `refactor:` 重构
- `chore:` 杂务（配置、依赖等）

### AI 不应自动执行的操作
- **不要自动 `git push`**：需人工确认后再推送
- **不要自动合并到 main**：需人工确认 Phase 完成后操作
- **不要修改 tokens/ 真实文件**：密钥不应由 AI 修改
- **不要修改 .gitignore**：需人工确认排除规则

### Phase 完成后合并流程
```bash
git checkout dev
git add . && git commit -m "feat: Phase X 完成"
git checkout main
git merge dev
git push origin main
git checkout dev
```

## 密钥管理

- 密钥存放在 `tokens/` 目录的 Python 文件中，通过 import 导入
- 真实密钥文件不入版本控制（.gitignore 排除）
- 模板文件在 `tokens/_templates/` 中入版本控制
- 新设备部署：从模板复制并填入真实值

## 旧代码对照（重要）

旧工程文件位于 `旧工程文件/` 目录（在 quantforge 的父目录中），包含 `common.py` 和 `LOOK_BACK_ROC_CLOSE_STRATAGE.py`。

- **在重构时，应严格对照旧代码逻辑，生成新代码。在这个过程中发现问题时，应当记录和反馈，待确认后再进行修复。**
- **重构后必须生成有效的测试案例，验证新旧代码行为一致。**
- 已知的逻辑差异（已对齐）：
  - 卖出逻辑：信号卖出→止损→挤压卖出（非TOP_K持仓仅在有空间压力时才卖出）
  - 买入分配：按可用现金 × ROC权重分配（非总资产权重）
  - 卖出先于买入：先执行卖出释放现金，再执行买入
  - BUY_AVERAGE：按TOP_K等分（非实际买入数量）
  - 小额交易过滤：|trade_money| < 2000 跳过
  - 买入资金基数：循环买入时锁定初始可用现金，不因逐笔买入消耗现金而递减
  - 策略weight：直接用ROC值作为weight（归一化由Resolver统一做，而非策略层每个标的独立归一化为1.0）
  - 已持仓标的追加买入：目标份额 = (分配金额 + 已有市值) / 价格，追加 = 目标 - 已有（非简单用分配金额除以价格再减已有份额）
  - 缓存更新判断：用fund_actual_date_ranges而非fund字段判断是否需要完整更新

- 新代码相对旧代码的改进（逻辑更合理但回测收益可能不同）：
  - 不在TOP_K的持仓立即卖出（旧代码：仅在有空间压力时才挤压卖出）→ 动量策略逻辑更自洽
  - BUY_AVERAGE按实际买入数量等分（旧代码：按TOP_K等分）→ 避免资金闲置

## 命令白名单

以下命令类型无需人工确认即可执行（requires_approval=false）：
- Python 脚本运行（.venv\Scripts\python.exe）
- pip 安装依赖
- git add / git commit / git status / git log / git diff / git branch
- mkdir / ls / cat 等文件查看命令
- 回测运行（main_backtest.py）
- 参数优化运行（param_optimizer_v2.py）
