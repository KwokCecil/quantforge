import time
from datetime import datetime

import pandas as pd
import requests
from loguru import logger

try:
    from quantforge.core.data_feed import DataFeed, DataRequest, DataResponse
except ModuleNotFoundError:
    from core.data_feed import DataFeed, DataRequest, DataResponse

_KLINE_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_TIMEOUT = 15
_RETRY_DELAY = 1.0
_MAX_RETRIES = 3


def _detect_exchange(code: str) -> str:
    """根据ETF代码推测交易所前缀。5开头/588/512等→sh，159等→sz"""
    code = str(code)
    if code.startswith("159") or code.startswith("16"):
        return "sz"
    return "sh"


def _fetch_sina_kline_raw(code: str) -> list:
    prefix = _detect_exchange(code)
    symbol = f"{prefix}{code}"
    params = {"symbol": symbol, "scale": "240", "ma": "no", "datalen": "3000"}
    headers = {"User-Agent": _UA, "Referer": "https://finance.sina.com.cn/"}

    last_error = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.get(_KLINE_URL, params=params, headers=headers, timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if data:
                return data
            else:
                logger.warning(f"{code} 新浪返回空数据 (attempt {attempt+1})")
                return []
        except Exception as e:
            last_error = e
            logger.debug(f"{code} 请求失败 (attempt {attempt+1}): {e}")
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_DELAY)

    raise RuntimeError(f"{code} 新浪K线请求全部失败: {last_error}")


def _parse_sina_response(raw_data: list) -> pd.DataFrame:
    records = []
    for d in raw_data:
        records.append({
            "date": d["day"],
            "open": float(d["open"]),
            "high": float(d["high"]),
            "low": float(d["low"]),
            "close": float(d["close"]),
            "vol": float(d["volume"]),
        })
    df = pd.DataFrame(records)
    df["date"] = df["date"].astype(str)
    df = df.sort_values("date").reset_index(drop=True)
    return df


def _supplement_today_sina(df: pd.DataFrame, code: str) -> pd.DataFrame:
    today = datetime.now().strftime("%Y-%m-%d")
    if not df.empty and df["date"].iloc[-1] == today:
        return df

    try:
        import akshare as ak
        df_today = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=today, end_date=today, adjust="qfq")
        if not df_today.empty:
            new_row = pd.DataFrame([{
                "date": today,
                "open": float(df_today["开盘"].iloc[0]),
                "high": float(df_today["最高"].iloc[0]),
                "low": float(df_today["最低"].iloc[0]),
                "close": float(df_today["收盘"].iloc[0]),
                "vol": float(df_today["成交量"].iloc[0]),
            }])
            df = pd.concat([df, new_row], ignore_index=True)
            df = df.sort_values("date").reset_index(drop=True)
            logger.info(f"{code} 已补充当日数据: {today}")
    except Exception:
        logger.debug(f"{code} 补充当日数据失败，跳过")
    return df


class SinaFinanceFeed(DataFeed):
    """新浪财经K线数据源。免费、无需注册、历史数据覆盖2013年至今（~13年）。"""

    def get_data(self, request: DataRequest) -> DataResponse:
        response = DataResponse()
        start = request.start
        end = request.end

        for code in request.codes:
            try:
                raw_data = _fetch_sina_kline_raw(code)
                df = _parse_sina_response(raw_data)

                if request.data_type == "daily_k":
                    df = _supplement_today_sina(df, code)

                if not df.empty:
                    mask = (df["date"] >= start) & (df["date"] <= end)
                    df = df[mask].reset_index(drop=True)

                response.bar_data[code] = df
                logger.debug(f"{code}: {len(df)} 条记录 ({start} ~ {end})")
            except Exception as e:
                logger.warning(f"{code} 数据获取失败: {e}")
                response.bar_data[code] = pd.DataFrame()

        return response