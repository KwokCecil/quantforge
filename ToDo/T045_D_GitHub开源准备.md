# T045 GitHub开源准备

> **状态**：pending
> **所属阶段**：Phase 4
> **创建日期**：2026-05-28
> **完成日期**：（完成后填写）

## 一、前置依赖

- 无。

## 二、目标

将 QuantForge 安全开源到 GitHub，**保留指导文档和 ToDo 以展示 AI Agent 工程化实践**。
1. 不泄露密钥、令牌、个人邮箱
2. 代码 + 文档完整可运行
3. git 历史干净（单 commit）

## 三、复制范围

在 quantforge 同级新建 `quantforge-oss/`，复制以下内容：

### 3.1 复制（全部）

| 目录/文件 | 说明 |
|-----------|------|
| `core/` | 核心引擎 |
| `strategies/` | 策略实现 |
| `indicators/` | 技术指标 |
| `data_sources/` | 数据源适配 |
| `monitors/` | 实盘监控适配器 |
| `research/` | 研究与分析 |
| `tests/` | 自动化测试 |
| `tools/` | 工具函数 |
| `config/` | 策略 JSON 配置 + 标的池 |
| `指导文档/` | 工程文档（需清理敏感信息） |
| `ToDo/` | 施工文档（需清理敏感信息） |
| `tokens/_templates/` | 密钥模板 |
| `tokens/__init__.py` | 包标识 |
| `main_backtest.py` | 回测入口 |
| `main_monitor.py` | 实盘监控入口 |
| `main_preflight.py` | 预检入口 |
| `main_validate.py` | 验证管道入口 |
| `README.md` | 项目说明 |
| `LICENSE` | MIT |
| `requirements.txt` | 依赖清单 |
| `.gitignore` | 需调整 |
| `__init__.py` | 根包标识 |
| `backtest_result.png` | 回测曲线图 |
| `remote_cmd.py` | 远程维护脚本（需脱敏） |
| `remote_cmd.bat` | remote_cmd 启动器 |
| `git_pull.py` | Git 代码同步脚本（需脱敏：gitee→github） |
| `git_merge_and_push.bat` | dev→main squash merge 自动化 |
| `fix_task.bat` | Windows 任务计划器修复 |
| `monitor.bat` | 实盘监控主启动流程 |
| `send_error_log.py` | 错误日志推送（WeChatNotifier） |
| `position/` | 持仓目录（放 `.gitkeep` + 示例模板） |
| `data/` | 数据缓存目录（放 `.gitkeep`） |
| `logs/` | 日志目录（放 `.gitkeep`） |
| `results/` | 回测产物目录（放 `.gitkeep`） |
| `ci_check.bat` | 本地CI检查脚本 |

### 3.2 不复制

| 文件/目录 | 原因 |
|-----------|------|
| `tokens/wechat_webhook.py` | 真实密钥 |
| `tokens/小熊同学token.py` | 真实密钥 |
| `tokens/email_config.py` | 真实邮箱 + 授权码 |
| `.trae/` | IDE 配置 |
| `.mpm-data/` | MPM 索引 |
| `.venv/` | 虚拟环境 |
| `dev-log.md` | 开发日志 |
| `SYSTEM_PROMPT.md` | AI Prompt |
| `_MPM_PROJECT_RULES.md` | AI 规则 |

## 四、敏感信息清理清单

复制完成后，在 `quantforge-oss/` 中逐文件处理：

### 4.1 指导文档/1.03_跨设备开发运维指南.md（重写）

**问题**：全文以私有仓库为背景，含令牌明文、组织名、个人邮箱。

**处理**：重写为 GitHub 公开仓库版本。保留以下核心结构：
- 分支策略（main/dev 分离）
- 开发机/运行机工作流
- tokens 模板方案
- AI 辅助开发的 Git 协作规范

替换内容：
- 所有私有仓库地址 → `github.com/<username>/quantforge.git`
- 令牌认证章节 → GitHub 的 SSH/Token 认证说明
- 删除"仓库必须私有"等表述
- 删除令牌明文（已脱敏）
- 删除组织名
- 删除个人邮箱
- 将"九、仓库安全"章节改为"九、开源安全注意事项"

### 4.2 指导文档/5.01_使用指南.md

- L14: 私有仓库地址 → `github.com/<username>/quantforge.git`
- L40: 同上

### 4.3 remote_cmd.py（脱敏）

将真实持仓数据替换为占位示例：

- `_sync_position()` 中的持仓字典 → 改为注释示例：
  ```python
  # 示例格式：
  # "588000": {"shares": 10000, "avg_cost": 1.000, "high_watermark": 1.100, "prev_close": 1.050}
  ```
- 目标日期 `date(2026, 6, 2)` → `date(2099, 1, 1)`（永远不会触发）
- `free_capital` → `0`

保留完整代码结构（git sync、pip install、WeChatNotifier），体现远程维护能力。

### 4.4 git_pull.py（脱敏）

- 函数名 `pull_from_gitee` → `pull_from_remote`
- 函数文档字符串和日志中的 "Gitee" → "远端仓库"
- 调用处 `pull_from_gitee(...)` → `pull_from_remote(...)`

保留完整代码结构（retry 机制、commit diff 展示、WeChatNotifier 推送）。

### 4.5 position/ 目录

- 创建 `position/.gitkeep`（空文件，保留目录结构）
- 创建 `position/position.json.example`，内容为示例格式：
  ```json
  {
    "free_capital": 0,
    "last_update": "2099-01-01",
    "588000": {
      "shares": 0,
      "avg_cost": 0,
      "high_watermark": 0,
      "prev_close": 0
    }
  }
  ```

### 4.6 data/、logs/、results/ 目录

在三个目录下各创建 `.gitkeep`（空文件），保留目录结构，展示基础设施完整性：
```
data/.gitkeep
logs/.gitkeep
results/.gitkeep
```

### 4.7 ToDo/T045_D_GitHub开源准备.md（本文档）

复制到 `quantforge-oss/` 前，将本文档中的敏感信息脱敏：
- 令牌明文 → `（已脱敏）`
- 个人邮箱 → `user@example.com`
- 组织名 → `<org-name>`
- 私有仓库地址 → `github.com/<username>/quantforge.git`

### 4.8 全局检查

复制并清理完成后，在 `quantforge-oss/` 中执行：

```powershell
Get-ChildItem -Recurse -Include *.py,*.md,*.json,*.txt | Select-String "敏感关键词模式" -SimpleMatch
```

必须返回 0 结果。
> 注：模板文件中的 `YOUR_EMAIL@163.com` / `RECEIVER_EMAIL@qq.com` 是占位符，不在此列。

## 五、.gitignore 调整

当前 `.gitignore` 已覆盖大部分排除项。复制到 `quantforge-oss/` 后需做以下调整：

**删除以下行**：
```gitignore
config/                          # 现在要开源
remote_cmd.py                    # 脱敏后开源
send_error_log.py                # 干净，开源
remote_cmd.bat                   # 干净，开源
fix_task.bat                     # 干净，开源
git_pull.py                      # 脱敏后开源
git_merge_and_push.bat           # 干净，开源
monitor.bat                      # 干净，开源
```

**修改**（目录结构保留，内容排除）：
```gitignore
# 改前：
position/
data/
logs/
results/

# 改后：
position/*.json
!position/position.json.example
data/*
!data/.gitkeep
logs/*
!logs/.gitkeep
results/*
!results/.gitkeep
```

**确认保留**：
```gitignore
tokens/小熊同学token.py
tokens/wechat_webhook.py
tokens/email_config.py
```

**追加**：
```gitignore
# === 开源排除（新增） ===
.trae/
```

## 六、实施步骤

### 第一步：创建并复制

```powershell
cd E:\JuJu\TraeProjects\量化工程\quantforge
mkdir ..\quantforge-oss
```

按 3.1 清单复制所有文件/目录到 `quantforge-oss/`。

### 第二步：清理敏感信息

按第四节清单，逐文件处理：
1. 重写 `指导文档/1.03_跨设备开发运维指南.md`
2. 修改 `指导文档/5.01_使用指南.md`（2处 gitee URL）
3. 脱敏 `remote_cmd.py`（持仓→占位，日期→2099年）
4. 脱敏 `git_pull.py`（gitee→github）
5. 创建 `position/.gitkeep` + `position/position.json.example`
6. 创建 `data/.gitkeep`、`logs/.gitkeep`、`results/.gitkeep`
7. 清理 `ToDo/T045_D_GitHub开源准备.md`（本文档，脱敏后复制）
8. 全局搜索确认零残留

### 第三步：调整 .gitignore

1. 删除以下排除行：`config/`、`remote_cmd.py`、`send_error_log.py`、`remote_cmd.bat`、`fix_task.bat`、`git_pull.py`、`git_merge_and_push.bat`、`monitor.bat`
2. `position/` → `position/*.json` + `!position/position.json.example`
3. `data/` → `data/*` + `!data/.gitkeep`（同理 logs/、results/）
4. 追加 `.trae/` 排除行
5. 确认 tokens 真实文件仍被排除

### 第四步：验证

```powershell
cd ..\quantforge-oss
.venv\Scripts\python.exe tests\run_all_tests.py
```

### 第五步：git init + 推送

```powershell
cd ..\quantforge-oss
git init
git config user.name "<GitHub用户名>"
git config user.email "<GitHub noreply邮箱>"
git add .
git commit -m "init: QuantForge - A股ETF多因子量化交易系统"
```

GitHub 创建空仓库后：
```powershell
git remote add origin https://github.com/<username>/quantforge.git
git branch -M main
git push -u origin main
```

### 第六步：最终验证

另找目录 clone GitHub 仓库，确认：
- `git log` 只有 1 个 commit
- 目录结构完整（含 指导文档/、ToDo/、config/）
- tokens/ 下只有 `_templates/` 和 `__init__.py`
- 全局搜索敏感关键词零命中
- 测试能跑

## 七、验收标准

- [ ] GitHub 仓库 `git log` 只有 1 个干净 commit
- [ ] `指导文档/` 和 `ToDo/` 在仓库中且内容完整
- [ ] `config/` 在仓库中且不含个人敏感参数
- [ ] `tokens/` 下只有模板文件，无真实密钥
- [ ] 全文搜索敏感关键词零命中
- [ ] `tests/run_all_tests.py` 通过（unit + contract）
- [ ] `main_backtest.py` 能正常运行
- [ ] README.md 中 git clone 地址正确

## 八、备注

- 开源后开发仍在原 Gitee 私有仓库进行，定期将成熟代码同步到 GitHub
- 指导文档中原本"Gitee 跨设备开发运维"内容需改写为通用版本，突出 AI Agent 工程化流程
- `1.03_跨设备开发运维指南.md` 是展示 AI 协作规范的核心文档，重写时保留其结构价值