---
name: "git"
description: "Git操作规范：提交格式、分支策略、AI行为边界。当你需要git add/commit/status/diff/branch时自动遵循此规范。"
---

# Git 操作规范

> **适用范围**：本项目所有 Git 操作。
> **自动触发**：当 Agent 执行 `git add`、`git commit`、`git status`、`git log`、`git diff`、`git branch` 时，自动遵循本规范。

---

## 一、提交格式

```
<type>: <简述>
```

| Type | 用途 |
|------|------|
| `feat` | 新功能 |
| `fix` | 修复 bug |
| `refactor` | 重构（不改变外部行为） |
| `style` | 格式调整（空格、换行、import排序等） |
| `test` | 测试相关 |
| `chore` | 杂务（配置、依赖更新、构建脚本等） |
| `docs` | 文档 |

**要求**：简述用中文。示例：`fix: multiprocessing args 传参错误导致 TypeError`

---

## 二、原子提交原则（强制）

1. **一个 commit = 一个变更**：每次 commit 只包含一个逻辑主题的改动。不要把不相关的改动混入同一次提交。
2. **完成后立即提交**：完成独立功能、修复 bug、重构模块后立即提交。
3. **提交前自查**：commit message 能不能准确描述**所有**改动？不能就拆。

---

## 三、Commit 纪律（强制）

### 禁止操作

- **禁止 `git add -A` / `git add .` / `git add --all`**：必须精确指定文件列表。

```powershell
# 正确：逐个指定文件
git add path/to/file1.py path/to/file2.json; git commit -m "fix: xxx"

# 错误：批量添加整个工作区
git add -A && git commit -m "xxx"
git add . && git commit -m "xxx"
```

### 提交前必做

1. 先 `git status` 确认改动了哪些文件
2. 只 add 与本次变更直接相关的文件
3. 不把用户其他未提交修改卷入提交

---

## 四、AI 行为边界

### 白名单（Agent 可自动执行）

| 命令 | 说明 |
|------|------|
| `git status` | 查看工作区状态 |
| `git log` | 查看提交历史 |
| `git diff` | 查看差异 |
| `git branch` | 查看分支 |
| `git add <精确文件列表>` | 暂存指定文件 |
| `git commit -m "xxx"` | 提交 |

### 黑名单（必须人工确认才能执行）

| 命令 | 说明 |
|------|------|
| `git push` | 绝不自动推送 |
| `git merge` | 绝不自动合并到 main/master |
| `git checkout main/master` | 绝不自动切主分支 |

---

## 五、分支策略

| 分支 | 用途 |
|------|------|
| `main` | 稳定可运行版本，运行机只拉此分支 |
| `dev` | 日常开发分支，AI 辅助编码在此进行 |

---

## 六、Phase 完成后合并流程

> ⚠️ AI 不自动执行此流程，需人工手动操作。

```bash
git checkout dev
git add . && git commit -m "feat: Phase X 完成"
git checkout main
git merge dev
git push origin main
git checkout dev
```

---

## 七、PowerShell 注意事项

Windows 下 Git 命令在 PowerShell 中执行，**不支持 `&&` 语法**，请用 `;` 串联：

```powershell
# 正确
git add file.py; git commit -m "fix: xxx"

# 错误（PowerShell 不支持）
git add file.py && git commit -m "fix: xxx"
```