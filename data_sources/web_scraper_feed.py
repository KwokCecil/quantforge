"""爬虫数据源：中债国债收益率 + 集思录 QDII 溢价率"""
import json
import re
from datetime import datetime
from typing import Any, Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup
from loguru import logger

from quantforge.core.data_feed import DataFeed, DataRequest, DataResponse
from quantforge.tools.decorators import retry

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}
_TIMEOUT = 15

# 中债收益率曲线 API 地址（2026-05 验证可用）
_CHINABOND_BASE = "https://yield.chinabond.com.cn"
_CHINABOND_CURVE_TREE = f"{_CHINABOND_BASE}/cbweb-mn/yc/queryTree?locale=zh_CN"
_CHINABOND_CURVE_DETAIL = f"{_CHINABOND_BASE}/cbweb-mn/yc/ycDetail"
# 中债国债曲线父节点固定ID（从 queryTree 获取）
_CHINABOND_GZ_PARENT_ID = "8a8b2ca048459f7b014845b49aa30001"
_CHINABOND_MAIN_PAGE = f"{_CHINABOND_BASE}/cbweb-mn/yield_main?locale=zh_CN"

# 集思录 QDII 数据地址
_JISILU_QDII_API = "https://www.jisilu.cn/data/qdii/qdii_list/"
_JISILU_QDII_PAGE = "https://www.jisilu.cn/data/qdii/"


@retry(max_retries=3, delay=2.0)
def _http_get_json(url: str, params: Optional[dict] = None, referer: Optional[str] = None) -> dict | list:
    """HTTP GET 请求，解析 JSON 响应，失败触发 retry 重试"""
    headers = dict(_HEADERS)
    if referer:
        headers["Referer"] = referer
    resp = requests.get(url, params=params, headers=headers, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


@retry(max_retries=3, delay=2.0)
def _http_get_html(url: str, params: Optional[dict] = None, referer: Optional[str] = None) -> str:
    """HTTP GET 请求，返回 HTML 文本"""
    headers = dict(_HEADERS)
    if referer:
        headers["Referer"] = referer
    resp = requests.get(url, params=params, headers=headers, timeout=_TIMEOUT)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


class WebScraperFeed(DataFeed):
    """爬虫数据源统一入口。通过 data_type 区分数据源：

    - 'scraper_chinabond': 中债 10 年期国债收益率（macro_data，日内缓存）
    - 'scraper_jisilu':    集思录 QDII 基金溢价率（bar_data，不缓存，盘中实时变动）
    """

    def __init__(self):
        self._chinabond_cache = None
        self._chinabond_cache_date = None

    def get_data(self, request: DataRequest) -> DataResponse:
        if request.data_type == "scraper_chinabond":
            return self._fetch_chinabond(request)
        elif request.data_type == "scraper_jisilu":
            return self._fetch_jisilu(request)
        else:
            logger.warning(f"WebScraperFeed 不支持的数据类型: {request.data_type}")
            return DataResponse()

    def get_latest_price(self, code: str) -> float:
        raise NotImplementedError("WebScraperFeed 不支持实时价格查询")

    # ==================== 中债国债收益率 ====================

    def _fetch_chinabond(self, request: DataRequest) -> DataResponse:
        """获取中债 10 年期国债收益率。结果写入 macro_data['chinabond_10y_yield']。
        国债收益率每天只发布一次，同进程内日内缓存避免重复爬取。
        """
        today = datetime.today().strftime("%Y-%m-%d")

        if self._chinabond_cache is not None and self._chinabond_cache_date == today:
            response = DataResponse()
            response.macro_data["chinabond_10y_yield"] = self._chinabond_cache
            return response

        response = DataResponse()
        try:
            yield_data = self._query_chinabond_10y()
            if yield_data:
                response.macro_data["chinabond_10y_yield"] = yield_data
                self._chinabond_cache = yield_data
                self._chinabond_cache_date = today
        except Exception as e:
            logger.warning(f"中债收益率爬取失败: {e}")

        return response

    def _query_chinabond_10y(self) -> list[dict] | None:
        """查询中债国债收益率曲线，提取 10 年期收益率。

        流程：获取曲线树 → 找到中债国债曲线子节点 → POST获取曲线详情 → 解析10年期。
        """
        session = requests.Session()
        session.headers.update(_HEADERS)
        session.headers.update({"Referer": _CHINABOND_MAIN_PAGE})

        # 先访问主页建立 session
        try:
            session.get(_CHINABOND_MAIN_PAGE, timeout=_TIMEOUT)
        except Exception:
            pass

        # 获取曲线树
        tree = _http_get_json(_CHINABOND_CURVE_TREE)
        if not isinstance(tree, list):
            logger.warning("中债曲线树返回格式异常")
            return None

        # 中债国债曲线下包含多条子曲线，找到"中债国债收益率曲线"
        target_id = None
        for node in tree:
            pid = node.get("pId", "")
            name = node.get("name", "")
            if pid == _CHINABOND_GZ_PARENT_ID and "收益率曲线" in name:
                target_id = node.get("id")
                break

        if not target_id:
            logger.warning("未找到中债国债收益率曲线子节点")
            return None

        # POST 获取曲线详细数据（workTime留空取最新发布日数据）
        today = datetime.today().strftime("%Y-%m-%d")
        params = {
            "ycDefIds": target_id,
            "zblx": "txy",
            "workTime": "",  # 空值取最新
            "dxbj": "",
            "qxlx": "",
            "yqqxN": "",
            "yqqxK": "",
            "wrjxCBFlag": "0",
            "locale": "zh_CN",
        }

        try:
            resp = session.post(_CHINABOND_CURVE_DETAIL, data=params, timeout=_TIMEOUT)
            resp.raise_for_status()
            html = resp.text
        except Exception as e:
            logger.warning(f"中债曲线详情请求失败: {e}")
            return None

        return self._parse_chinabond_detail(html, today)

    @staticmethod
    def _parse_chinabond_detail(html: str, date_str: str) -> list[dict] | None:
        """从曲线详情HTML中解析收益率数据。中债返回HTML表格或JSON内嵌数据。"""
        # 尝试从HTML中提取JSON数据
        soup = BeautifulSoup(html, "lxml")

        # 查找表格数据（常见格式）
        tables = soup.find_all("table")
        for table in tables:
            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all(["td", "th"])
                texts = [c.get_text(strip=True) for c in cells]
                if len(texts) >= 2 and "10" in texts[0] and ("年" in texts[0] or "Y" in texts[0].upper()):
                    try:
                        return [{
                            "date": date_str,
                            "term": "10Y",
                            "yield": float(texts[1].replace("%", "")),
                        }]
                    except ValueError:
                        continue

        # 查找内嵌JSON
        scripts = soup.find_all("script")
        for script in scripts:
            text = script.string or ""
            if "ycItems" in text or "yield" in text.lower():
                matches = re.findall(r'"term"\s*:\s*"[^"]*10[Yy][^"]*".*?"yield"\s*:\s*([0-9.]+)', text)
                if matches:
                    return [{"date": date_str, "term": "10Y", "yield": float(matches[0])}]

        logger.warning("曲线详情中未找到 10Y 收益率")
        return None

    # ==================== 集思录 QDII 溢价率 ====================

    def _fetch_jisilu(self, request: DataRequest) -> DataResponse:
        """获取集思录 QDII 基金溢价率。结果写入 bar_data[code]"""
        response = DataResponse()

        try:
            raw_data = self._query_jisilu_qdii()
            if not raw_data:
                return response

            target_codes = set(request.codes)
            for item in raw_data:
                code = str(item.get("fund_id", "")).strip()
                if not code:
                    continue

                if target_codes and code not in target_codes:
                    continue

                df = pd.DataFrame([{
                    "code": code,
                    "name": item.get("fund_nm", ""),
                    "price": float(item.get("price", 0) or 0),
                    "nav": float(item.get("nav", 0) or 0),
                    "premium_rate": float(item.get("premium_rt", 0) or 0),
                    "update_time": item.get("apply_time", ""),
                }])
                response.bar_data[code] = df

        except Exception as e:
            logger.warning(f"集思录 QDII 爬取失败: {e}")

        return response

    def _query_jisilu_qdii(self) -> list[dict] | None:
        """查询集思录 QDII 列表数据。优先 JSON API，回退 HTML 解析。"""
        # 优先 JSON API
        try:
            data = _http_get_json(_JISILU_QDII_API, params={"___jsl": "LST_DATA"},
                                   referer=_JISILU_QDII_PAGE)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                rows = data.get("rows") or data.get("data") or []
                if isinstance(rows, list):
                    return rows
        except Exception:
            pass

        # 回退 HTML 解析
        try:
            html = _http_get_html(_JISILU_QDII_PAGE, referer=_JISILU_QDII_PAGE)
            return self._parse_qdii_html(html)
        except Exception:
            pass

        logger.warning("集思录 QDII 数据获取失败（API 和 HTML 均无法解析）")
        return None

    @staticmethod
    def _parse_qdii_html(html: str) -> list[dict] | None:
        """从集思录页面 HTML 中解析 QDII 数据（回退方案）"""
        soup = BeautifulSoup(html, "lxml")

        # 尝试从 script 标签中提取 JSON 数据
        scripts = soup.find_all("script")
        for script in scripts:
            text = script.string or ""
            if "qdii_list" in text or "LST_DATA" in text:
                match = re.search(r'LST_DATA\s*[:=]\s*(\[.*?\])\s*;', text, re.DOTALL)
                if match:
                    try:
                        return json.loads(match.group(1))
                    except json.JSONDecodeError:
                        continue

        return None
