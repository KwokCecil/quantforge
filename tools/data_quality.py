"""K线数据质量检查器。对单只ETF的DataFrame执行9项自动检查。"""

import pandas as pd
import numpy as np
from typing import Any
from loguru import logger

from quantforge.tools.time_utils import get_trading_dates

_SEV_ERROR = "🔴 错误"
_SEV_WARN = "🟡 警告"
_SEV_OK = "✅ 通过"


def check_data_quality(df: pd.DataFrame, code: str) -> dict:
    """对单只ETF的K线DataFrame执行全套质量检查。

    Args:
        df: 日K线DataFrame，必须包含 date/open/high/low/close/vol 列
        code: ETF代码

    Returns:
        dict: {code, data_length, date_start, date_end, checks: [{name, status, detail}],
               summary: {error_count, warn_count}}
    """
    results: dict[str, Any] = {"code": code, "data_length": len(df), "checks": [], "error_count": 0, "warn_count": 0}

    if df.empty:
        results["date_start"] = "N/A"
        results["date_end"] = "N/A"
        results["checks"].append({"name": "数据存在性", "status": _SEV_ERROR, "detail": "DataFrame为空"})
        results["error_count"] += 1
        results["summary"] = "❌ 空数据"
        return results

    required_cols = {"date", "open", "high", "low", "close", "vol"}
    missing = required_cols - set(df.columns)
    if missing:
        results["date_start"] = "N/A"
        results["date_end"] = "N/A"
        results["checks"].append({"name": "列完整性", "status": _SEV_ERROR, "detail": f"缺少列: {missing}"})
        results["error_count"] += 1
        results["summary"] = "❌ 列不完整"
        return results

    dates = df["date"].astype(str)
    results["date_start"] = dates.iloc[0]
    results["date_end"] = dates.iloc[-1]
    o, h, l, c, v = df["open"], df["high"], df["low"], df["close"], df["vol"]

    # === 1. 实际数据长度 ===
    _check_length(results, dates)

    # === 2. 日期连续性 ===
    _check_continuity(results, dates)

    # === 3. 重复数据 ===
    _check_duplicates(results, dates)

    # === 4. OHLC逻辑一致性 ===
    _check_ohlc_logic(results, h, l, o, c, dates)

    # === 5. 价格跳变 ===
    _check_price_jumps(results, c, dates, code)

    # === 6. 零成交/停牌 ===
    _check_zero_volume(results, v, dates)

    # === 7. 负价格 ===
    _check_negative_price(results, o, h, l, c, dates)

    # === 8. 日期排序 ===
    _check_date_order(results, dates)

    if results["error_count"] == 0 and results["warn_count"] == 0:
        results["summary"] = "✅ 全部通过"
    elif results["error_count"] == 0:
        results["summary"] = f"⚠️ {results['warn_count']}项警告"
    else:
        results["summary"] = f"❌ {results['error_count']}项错误, {results['warn_count']}项警告"

    return results


def _add(results, name, status, detail):
    results["checks"].append({"name": name, "status": status, "detail": detail})
    if status == _SEV_ERROR:
        results["error_count"] += 1
    elif status == _SEV_WARN:
        results["warn_count"] += 1


def _check_length(results, dates):
    actual_days = len(dates)
    if actual_days < 100:
        _add(results, "数据长度", _SEV_WARN, f"仅{actual_days}条，可能是新ETF或数据源不完整")
    else:
        _add(results, "数据长度", _SEV_OK, f"{actual_days} 条 ({dates.iloc[0]} ~ {dates.iloc[-1]})")


def _check_continuity(results, dates):
    try:
        if len(dates) < 2:
            _add(results, "日期连续性", _SEV_OK, "仅1条数据，无需检查")
            return

        trading_dates = set(get_trading_dates(str(dates.iloc[0]), str(dates.iloc[-1])))
        actual_dates = set(dates.tolist())
        missing = trading_dates - actual_dates
        n_missing = len(missing)

        if n_missing == 0:
            _add(results, "日期连续性", _SEV_OK, f"无缺失交易日")
        elif n_missing <= 10:
            _add(results, "日期连续性", _SEV_WARN, f"缺失{n_missing}个交易日: {sorted(missing)[:10]}")
        else:
            _add(results, "日期连续性", _SEV_ERROR, f"缺失{n_missing}个交易日，前10: {sorted(missing)[:10]}")
    except Exception as e:
        _add(results, "日期连续性", _SEV_WARN, f"检查异常: {e}")


def _check_duplicates(results, dates):
    dup_mask = dates.duplicated()
    dup_count = dup_mask.sum()
    if dup_count == 0:
        _add(results, "重复数据", _SEV_OK, "无重复日期")
    else:
        dup_dates = dates[dup_mask].unique().tolist()[:5]
        _add(results, "重复数据", _SEV_ERROR, f"{dup_count}个重复日期: {dup_dates}")


def _check_ohlc_logic(results, h, l, o, c, dates):
    errors = []
    # high < low
    hl_err = (h < l).sum()
    if hl_err > 0:
        bad = dates[h < l].tolist()[:3]
        errors.append(f"high<low: {hl_err}处 ({bad})")

    # open 不在 [low, high]
    o_range_err = ((o < l) | (o > h)).sum()
    if o_range_err > 0:
        bad = dates[(o < l) | (o > h)].tolist()[:3]
        errors.append(f"open超出范围: {o_range_err}处 ({bad})")

    # close 不在 [low, high]
    c_range_err = ((c < l) | (c > h)).sum()
    if c_range_err > 0:
        bad = dates[(c < l) | (c > h)].tolist()[:3]
        errors.append(f"close超出范围: {c_range_err}处 ({bad})")

    # 零值（除上市首日）
    zero_o = ((o == 0) | o.isna()).sum()
    zero_c = ((c == 0) | c.isna()).sum()
    if zero_o > 0 or zero_c > 0:
        errors.append(f"零/空值: open={zero_o}处 close={zero_c}处")

    if not errors:
        _add(results, "OHLC逻辑", _SEV_OK, "一致")
    else:
        _add(results, "OHLC逻辑", _SEV_ERROR, "; ".join(errors))


def _check_price_jumps(results, c, dates, code):
    if len(c) < 2:
        _add(results, "价格跳变", _SEV_OK, "数据不足")
        return

    ret = c.pct_change()
    ret = ret.iloc[1:]  # 第一天无前值

    up_jumps = ret > 0.20
    down_jumps = ret < -0.15

    # IPO首日排除
    ipo_date = dates.iloc[0]
    up_jumps = up_jumps & (dates.iloc[1:].values != ipo_date)
    down_jumps = down_jumps & (dates.iloc[1:].values != ipo_date)

    n_up = up_jumps.sum()
    n_down = down_jumps.sum()

    if n_up == 0 and n_down == 0:
        _add(results, "价格跳变", _SEV_OK, "无异常跳变")
    else:
        details = []
        if n_up > 0:
            up_dates = dates.iloc[1:][up_jumps].tolist()[:3]
            up_vals = ret[up_jumps].tolist()[:3]
            details.append(f"涨幅>20%: {n_up}处 {list(zip(up_dates, [f'{v:.1%}' for v in up_vals]))}")
        if n_down > 0:
            down_dates = dates.iloc[1:][down_jumps].tolist()[:3]
            down_vals = ret[down_jumps].tolist()[:3]
            details.append(f"跌幅<-15%: {n_down}处 {list(zip(down_dates, [f'{v:.1%}' for v in down_vals]))}")

        known_events = {"2020-02-03", "2018-10-11", "2024-10-08", "2024-09-30"}
        has_known = any(d in known_events for d in dates.iloc[1:][up_jumps | down_jumps].tolist())
        note = " (含已知宏观事件日)" if has_known else ""

        _add(results, "价格跳变", _SEV_WARN, "; ".join(details) + note)


def _check_zero_volume(results, v, dates):
    if v is None or len(v) == 0:
        _add(results, "零成交量", _SEV_OK, "无数据")
        return

    zero_vol = (v == 0) | v.isna()
    n_zero = zero_vol.sum()

    if n_zero == 0:
        _add(results, "零成交量", _SEV_OK, "无零成交日")
    elif n_zero <= 5:
        zero_dates = dates[zero_vol].tolist()
        _add(results, "零成交量", _SEV_WARN, f"{n_zero}天零成交 (可能停牌): {zero_dates}")
    else:
        _add(results, "零成交量", _SEV_WARN, f"{n_zero}天零成交，前5: {dates[zero_vol].tolist()[:5]}")


def _check_negative_price(results, o, h, l, c, dates):
    neg_cols = []
    for name, col in [("open", o), ("high", h), ("low", l), ("close", c)]:
        if col is not None and len(col) > 0:
            neg_mask = col < 0
            if neg_mask.sum() > 0:
                neg_cols.append(f"{name}: {neg_mask.sum()}处 ({dates[neg_mask].tolist()[:3]})")

    if not neg_cols:
        _add(results, "负价格", _SEV_OK, "无负价格")
    else:
        _add(results, "负价格", _SEV_ERROR, "; ".join(neg_cols))


def _check_date_order(results, dates):
    is_sorted = dates.is_monotonic_increasing
    if is_sorted:
        _add(results, "日期排序", _SEV_OK, "升序正确")
    else:
        bad_idx = []
        for i in range(1, len(dates)):
            if dates.iloc[i] <= dates.iloc[i - 1]:
                bad_idx.append(i)
        _add(results, "日期排序", _SEV_ERROR, f"乱序位置: {bad_idx[:5]}，可用.sort_values('date')修复")


def run_batch_quality(dfs: dict[str, pd.DataFrame]) -> list[dict]:
    """批量检查多个ETF。

    Args:
        dfs: {code: DataFrame} 字典

    Returns:
        list[dict]: 每只ETF的检查结果
    """
    return [check_data_quality(df, code) for code, df in dfs.items()]


def print_quality_report(results: list[dict]):
    """格式化打印质量检查报告。"""
    for r in results:
        print(f"\n检查 {r['code']}:")
        for check in r["checks"]:
            print(f"  {check['status']:<8} {check['name']:<12} {check['detail']}")
        print(f"  ═══ {r['summary']}")
