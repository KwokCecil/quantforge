---
name: "python-env"
description: "Python虚拟环境路径、PYTHONPATH、pip安装、命令白名单。当需要运行Python脚本或安装依赖时自动遵循。"
---

# Python 环境规范

> **自动触发**：Agent 执行任何 Python 相关命令时，自动遵循本规范。

---

## 一、虚拟环境（强制）

**必须使用项目级 `.venv`，禁止使用全局 Python。**

```powershell
# 运行 Python 脚本
.\.venv\Scripts\python.exe <脚本名>

# 安装依赖
.\.venv\Scripts\pip.exe install -r requirements.txt
.\.venv\Scripts\pip.exe install <包名>
```

---

## 二、工作目录

- 代码目录（Git 仓库根目录）：`quantforge/`
- 所有命令的 cwd 必须设为 `quantforge/`（即本目录）

---

## 三、PYTHONPATH

- 运行 quantforge 模块时，PYTHONPATH 应设为 quantforge 的**父目录**
- 使 `from quantforge.xxx import yyy` 能正常导入
- 通常 cd 到 `quantforge/` 后直接运行即可，无需手动设

---

## 四、命令白名单

以下命令 Agent 可自动执行，无需 `requires_approval`：

| 类别 | 命令 |
|------|------|
| Python 运行 | `.\.venv\Scripts\python.exe` |
| 依赖安装 | `.\.venv\Scripts\pip.exe install` |
| Git 操作 | `git add` / `git commit` / `git status` / `git log` / `git diff` / `git branch` |
| 文件查看 | `ls` / `cat` / `head` / `tail` / `Get-Content` |
| 目录操作 | `mkdir` |
| 回测脚本 | `main_backtest.py` |
| 优化脚本 | `param_optimizer_v2.py` |

## 五、黑名单（必须人工确认）

- `git push`
- `git merge`（合并到 main）
- 删除文件
- 修改 `tokens/` 下的文件
- 修改 `.gitignore`

---

## 六、常见错误

```powershell
# ❌ 错误：用了全局 Python
python main_monitor.py

# ❌ 错误：路径写错了
.venv\Scripts\python.exe main_monitor.py   # 少了个点

# ✅ 正确
.\.venv\Scripts\python.exe main_monitor.py
```
