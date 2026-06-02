---
name: "secrets"
description: "密钥文件管理规范：存放位置、模板机制、AI禁止修改。当涉及tokens/目录或密钥配置时自动遵循。"
---

# 密钥管理规范

> **自动触发**：当 Agent 触及 `tokens/` 目录、密钥导入或安全相关操作时，自动遵循。

---

## 一、存放位置

| 位置 | 内容 | 版本控制 |
|------|------|---------|
| `tokens/` | 真实密钥文件（Python .py） | ❌ 不入库（.gitignore 排除） |
| `tokens/_templates/` | 密钥模板文件（无真实值） | ✅ 入库 |

---

## 二、密钥结构

真实密钥以 Python 变量形式定义，通过 `import` 导入使用：

```python
# tokens/wechat_webhook.py（真实文件，不入库）
webhook_url = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"

# tokens/email_config.py（真实文件，不入库）
sender = "xxx@163.com"
receiver = ["xxx@163.com"]
authorization_code = "xxx"
```

```python
# 代码中导入（带容错）
try:
    from quantforge.tokens.wechat_webhook import webhook_url
except ModuleNotFoundError:
    from tokens.wechat_webhook import webhook_url
```

---

## 三、模板文件

`tokens/_templates/` 下的模板文件只包含占位符，**入库**供新设备参考：

```python
# tokens/_templates/wechat_webhook.py.template（入库，无真实值）
webhook_url = "YOUR_WEBHOOK_URL_HERE"
```

---

## 四、新设备部署

1. 从 `tokens/_templates/` 复制对应模板
2. 去除 `.template` 后缀
3. 填入真实密钥值
4. `.gitignore` 已排除，不会误提交

---

## 五、AI 行为边界（强制）

### ❌ AI 禁止

- **禁止修改 `tokens/` 下的真实密钥文件**
- **禁止读取真实密钥值后写入其他文件**
- **禁止在日志/commit message 中暴露密钥**

### ✅ AI 允许

- 创建 `tokens/_templates/` 下的模板文件（无真实值）
- 检查 `tokens/` 目录结构是否完整
- 提醒用户补充缺失的密钥文件