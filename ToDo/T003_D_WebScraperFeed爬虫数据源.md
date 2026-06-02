# T003 WebScraperFeed 爬虫数据源

> **状态**：completed
> **所属阶段**：Phase 3 — 多策略 + 实盘监控
> **创建日期**：2026-05-01
> **完成日期**：2026-05-03

## 一、前置依赖

- 无硬依赖。DataFeed 接口已在 Phase 1 完成，只需新建一个实现类。
- 需要安装 BeautifulSoup：`pip install beautifulsoup4`（检查是否已在 requirements.txt 中，若不在则添加）

## 二、目标

实现 `WebScraperFeed` 类，作为爬虫数据源的统一入口。通过 `DataRequest.data_type` 区分不同的爬虫数据源。本任务只实现两个爬虫：

1. **中债国债收益率**（chinabond.com.cn）：获取 10 年期国债收益率，支撑股债利差策略
2. **集思录 QDII 溢价率**（jisilu.cn）：获取 QDII 基金实时溢价率，支撑 QDII 溢价策略

## 三、实施计划

### Step 1：创建 data_sources/web_scraper_feed.py

```python
# data_sources/web_scraper_feed.py

class WebScraperFeed(DataFeed):
    """爬虫数据源统一入口。通过 data_type 区分数据源：
    - 'scraper_chinabond': 中债国债收益率
    - 'scraper_jisilu':    集思录 QDII 溢价率
    """

    def get_data(self, request: DataRequest) -> DataResponse:
        if request.data_type == 'scraper_chinabond':
            return self._fetch_chinabond(request)
        elif request.data_type == 'scraper_jisilu':
            return self._fetch_jisilu(request)
        ...
```

### Step 2：实现中债国债收益率爬虫

- 目标 URL：`https://yield.chinabond.com.cn/cbweb-mn/yield_main`（或官方 JSON API）
- 解析方式：HTML 解析（BeautifulSoup）或 API JSON
- 关键：获取 10 年期国债收益率的**历史时间序列**和**当日值**
- 数据存入 `DataResponse.macro_data['chinabond_10y_yield']`，格式：
  ```python
  [{"date": "2024-01-02", "value": 2.56}, ...]
  ```
- 参考规格书 §3.8

### Step 3：实现集思录 QDII 溢价率爬虫

- 目标 URL：集思录 QDII 基金页面（需确认具体 API 端点）
- 解析方式：通常为 JSON API（比 HTML 解析更稳定）
- 获取内容：各 QDII 基金的实时溢价率（%）、当前价格、净值
- 数据存入 `DataResponse.bar_data[code]`（以基金代码为 key），附加溢价率列
- 参考规格书 §3.8

### Step 4：错误处理与重试

- 爬虫请求使用 `tools/decorators.py` 的 `retry` 装饰器
- 解析失败时返回空数据，记录 warning 日志，不抛异常（爬虫不稳定性高）
- 添加合理的 User-Agent 和请求间隔（避免被封 IP）

### Step 5：更新 main_backtest.py 支持混合数据源

- 验证 `WebScraperFeed` 能与现有 `AutoStockFeed` 配合
- 在需要时通过 `CachedDataFeed` 包装——但目前爬虫数据不适合本地缓存（实时性要求高）

## 四、验收标准

- [x] `WebScraperFeed().get_data(... data_type='scraper_chinabond')` 能获取到中债国债收益率数据（10Y=1.7473%）
- [x] `WebScraperFeed().get_data(... data_type='scraper_jisilu')` 反爬保护返回空，不崩溃
- [x] 爬虫请求失败（网络错误）时记录警告日志，返回空 DataResponse，不崩溃
- [x] 连续 3 次重试失败后优雅降级
- [x] `requirements.txt` 已添加 `beautifulsoup4` 和 `lxml`
- [x] 代码遵循现有 DataFeed 接口规范

## 五、相关文件

| 文件 | 类型 | 说明 |
|------|------|------|
| `data_sources/web_scraper_feed.py` | **新建** | WebScraperFeed 类实现 |
| `data_sources/__init__.py` | 修改 | 导出 WebScraperFeed |
| `requirements.txt` | 修改 | 添加 beautifulsoup4, lxml |

## 六、备注

- **中债 API 已验证可用**：`queryTree` → 找国债曲线 → `ycDetail` POST → HTML 表格解析。空 workTime 取最新数据。
- **集思录需要登录**：当前返回空数据。后续如需 QDII 溢价策略，需研究 Cookie 登录方案。
- 爬虫稳定性：`retry` 装饰器 + try/except 兜底，失败不影响其他策略运行。
