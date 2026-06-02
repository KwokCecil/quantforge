# 工程规范（Engineering Standards）

> **作用域**：同语言 / 同团队的 Python 项目通用。
> **使用方法**：复制到每个项目的 `.trae/rules/` 目录下，与 `project.md` 配合使用。
> **前置依赖**：核心行为原则（Think Before Coding 等四条）应已写入 IDE Agent 用户级设置，本文件不重复。

---

## 一、Git 提交规范

> **详见 skill**：`git`（提交格式、原子提交原则、commit 纪律、AI 行为边界）

---

## 二、AI 行为边界

> **详见 skill**：
> - `git` — git push/merge/checkout 主分支的边界
> - `secrets` — 密钥文件修改边界
> - `python-env` — 命令白名单/黑名单

AI 不应自动执行：`git push`、合并到主分支、修改密钥文件、修改 `.gitignore`、删除文件、修改依赖。

---

## 三、任务驱动开发流程

> **详见 skill**：`task-workflow`（施工文档模板、T编号命名、状态标记、执行纪律）

---

## 四、代码注释规范

代码中必须有**必要的精简注释**，不用太多，但要有：

### 需要注释的

- **类定义**：一行注释说明类的职责和用途
- **长函数的分块**：用注释标记逻辑段落（如 `# === 数据加载 ===`、`# === 信号生成 ===`）
- **关键参数**：说明参数的含义和取值原因（如"ROC回看期=22，基于IC扫描最优"）
- **非显而易见逻辑**：边界条件、特殊处理、公式推导等
- **算法出处**：引用公式来源或参考文档（如"ICIR = IC均值 / IC标准差"）

### 不需要注释的

- 自解释的代码（如 `close / close.shift(period) - 1`）
- 变量赋值、简单循环等
- 过度冗长的段落描述

> 原则：注释是给**一段时间后忘记代码逻辑的自己**看的，只解释"为什么这样做"和"这段做了什么"，不解释"语法是什么"。

---

## 五、代码质量

> **说明**：以下为模板，请根据项目实际使用的语言和工具填写。

### Lint & Format

<!-- 示例：ruff check --fix && ruff format -->
```
{填写 lint/format 命令}
```

### 验收先行（按场景分层）

**原则：先定义"怎样算完成"、再写代码。** 不分场景一刀切 TDD，按代码所在层级选择验收方式。

| 场景 | 验收方式 | 原因 |
|------|----------|------|
| **Bug 修复** | 先写复现测试 → 确认 test 失败 → 修复 → test 通过 | 防止再次出现 |
| **core/ 或 strategies/ 新增逻辑** | 先写单元/契约测试，再实现 | 系统骨架，错了影响全局 |
| **monitors/ 或 data_sources/ 修改** | 先写干跑验收（preflight check），再实现 | 网络依赖，单元测试覆盖有限 |
| **tools/ 新增工具函数** | 先写 docstring 用例 + 单元测试，再实现 | 工具函数独立性强 |
| **research/ 探索脚本** | 不强制，产出结论即可 | 探索性工作没有"正确输出" |

### 测试

测试运行: `python tests/run_all_tests.py`（默认 unit + contract）

#### 核心基础设施测试原则（data_feed / executor / resolver）

`core/` 层是系统骨架，错误影响全局且静默传播。该层测试须遵循：

1. **失败模式优先于成功路径**：数据完整性代码的失败路径多于成功路径。正常流程显而易见，边界才是 bug 藏身之地。

2. **Mock 外部依赖，测试内部逻辑**：隔离测试核心组件的业务逻辑（修正计算、合并策略、过期检测）。数据源、网络、文件系统 → 全部 mock，用合成 DataFrame 验证。

3. **状态变更必须显式断言**：
   - 缓存写入了吗？→ `mock_write.assert_called_once()`
   - 数据修正了吗？→ 逐行验证价格是否乘以修正系数
   - 新日期追加了吗？→ 验证日期范围是否扩展
   - 跳过更新了吗？→ 验证 DataFrame 为空

4. **回归测试：每个线上 bug 必须有对应测试**：修复时先写复现用例（test 先 FAIL），再修复代码（test 变 PASS）。

5. **时间必须可控**：涉及 `datetime.now()` 的逻辑不能用真实系统时间。使用 mock 或固定参考日期，确保测试在任何时间运行都一致。

6. **零网络、零真实文件系统**：
   - 数据源 → `MagicMock(spec=DataFeed)`
   - 缓存读写 → `patch("quantforge.core.data_feed.read_fund_data", ...)`
   - 缓存目录 → `tempfile.mkdtemp()`

#### 测试文件约定

| 项目 | 约定 |
|------|------|
| 文件名 | `_test_{模块名}_unit.py` 或 `_verify_{模块名}.py` |
| 定位 | `# @layer: unit` 为文件首行 |
| 导入路径 | `sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))` |
| 函数命名 | `def test_{场景描述}()`，docstring 说明 This-Then-Expect |
| 断言风格 | 纯 `assert`，不用 `unittest.TestCase` |

### 提交前检查清单

- [ ] Lint / Format 通过
- [ ] 测试全部通过
- [ ] 无裸 `except:`（至少用 `except Exception`）
- [ ] 异常不吞掉（`except: pass`），必须记录日志
- [ ] 无硬编码的敏感信息（密钥、token、密码等）
- [ ] 新增代码有必要的注释

---

## 六、命令白名单

> **详见 skill**：`python-env`（白名单/黑名单、正确用法）

---

## 七、文件归档规范

> **详见 skill**：`file-organization`（目录用途、文件归属决策树、清理规则、命名规则）
