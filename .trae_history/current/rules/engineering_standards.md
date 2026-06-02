# 工程规范

## Skill 索引（规范主体在 skill 中，此处仅索引）
- `git` — 提交格式、分支策略、AI 行为边界
- `secrets` — 密钥存放、模板、AI 禁止操作
- `python-env` — 虚拟环境、命令白名单/黑名单
- `task-workflow` — 施工文档、T编号、状态标记
- `file-organization` — 目录用途、文件归属、清理规则

## 代码注释
只解释"为什么"和"做了什么"，不解释语法。类定义一行注释，长函数分段注释，关键参数说明取值原因。

## 测试
运行: `python tests/run_all_tests.py`（默认 unit + contract）

核心层测试原则：失败模式优先、Mock 外部依赖、时间可控、零网络。

测试文件首行 `# @layer: unit|contract|integration|e2e`，文件名 `_test_*_unit.py` 或 `_verify_*.py`。

提交前：无裸 `except:`、异常不吞掉、无硬编码敏感信息。