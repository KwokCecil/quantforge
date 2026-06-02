import os
from datetime import datetime, timedelta

import pandas as pd
import requests
from loguru import logger

try:
    from quantforge.core.data_feed import DataFeed, DataRequest, DataResponse
    from quantforge.tools.decorators import retry
except ModuleNotFoundError:
    from core.data_feed import DataFeed, DataRequest, DataResponse
    from tools.decorators import retry

_API_TOKEN = os.getenv("API_TOKEN", "")
if not _API_TOKEN:
    try:
        from quantforge.tokens.小熊同学token import token as _FILE_TOKEN
        _API_TOKEN = _FILE_TOKEN
    except (ModuleNotFoundError, ImportError):
        try:
            from tokens.小熊同学token import token as _FILE_TOKEN
            _API_TOKEN = _FILE_TOKEN
        except (ModuleNotFoundError, ImportError):
            _API_TOKEN = ""

_API_BASE = "https://api.autostock.cn/v1"
_KLINE_URL = f"{_API_BASE}/stock/kline/day"
_MIN_URL = f"{_API_BASE}/stock/min"
_UA = "Apifox/1.0.0 (https://apifox.com)"
_TIMEOUT = 10
_RETRIES = 3
_RETRY_DELAY = 1.0


def _get_minute_data(code: str) -> dict:
    try:
        params = {"token": _API_TOKEN, "code": code}
        resp = requests.get(_MIN_URL, params=params, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 200 or not data.get("data"):
            return {}
        d = data["data"]
        return {
            "open": float(d.get("open", 0)),
            "close": float(d.get("close", 0)),
            "high": float(d.get("high", 0)),
            "low": float(d.get("low", 0)),
            "volume": float(d.get("volume", 0)),
            "price": float(d.get("price", 0)),
        }
    except Exception as e:
        logger.warning(f"{code} 分钟数据获取失败: {e}")
        return {}


@retry(max_retries=_RETRIES, delay=_RETRY_DELAY)
def _do_fetch(code: str, start_date: str) -> list:
    params = {"token": _API_TOKEN, "code": code, "startDate": start_date, "type": "1"}
    resp = requests.get(_KLINE_URL, params=params, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 200:
        raise RuntimeError(f"API returned code={data.get('code')}, msg={data.get('msg', '')}")
    return data.get("data", [])


def _fetch_kline(code: str, start: str, end: str) -> list:
    ten_days_ago = (datetime.strptime(start, "%Y-%m-%d") - timedelta(days=10)).strftime("%Y-%m-%d")
    try:
        _do_fetch(code, ten_days_ago)
    except Exception:
        logger.debug(f"{code} 预热请求失败，继续")

    return _do_fetch(code, start)


def _parse_kline_response(raw_data: list) -> pd.DataFrame:
    df = pd.DataFrame(raw_data, columns=["date", "open", "close", "high", "low", "vol"])
    df["date"] = df["date"].astype(str)
    for col in ["open", "close", "high", "low", "vol"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_values("date").reset_index(drop=True)
    return df


def _supplement_today(df: pd.DataFrame, code: str) -> pd.DataFrame:
    today = datetime.now().strftime("%Y-%m-%d")
    if not df.empty and df["date"].iloc[-1] == today:
        return df

    minute = _get_minute_data(code)
    if minute and minute.get("open"):
        new_row = pd.DataFrame([{
            "date": today,
            "open": float(minute["open"]),
            "close": float(minute.get("price", minute["close"])),
            "high": float(minute["high"]),
            "low": float(minute["low"]),
            "vol": float(minute["volume"]),
        }])
        df = pd.concat([df, new_row], ignore_index=True)
        df = df.sort_values("date").reset_index(drop=True)
        logger.info(f"{code} 已补充当日数据: {today}")
    return df


class AutoStockFeed(DataFeed):
    def get_data(self, request: DataRequest) -> DataResponse:
        response = DataResponse()
        data_type = request.data_type
        start = request.start
        end = request.end

        for code in request.codes:
            raw_data = _fetch_kline(code, start, end)
            df = _parse_kline_response(raw_data)
            if request.data_type == "daily_k":
                df = _supplement_today(df, code)
            response.bar_data[code] = df

        return response
