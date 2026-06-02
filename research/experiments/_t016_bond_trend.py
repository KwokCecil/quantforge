"""T016 前期探索：30年国债ETF (511010) 趋势择时回测

用 MA 双均线 + ROC 动量做趋势跟踪。
策略：MA(20)>MA(60) 且 ROC(22)>0 → 持有；否则 → 空仓/货币。
单标的双向（要么全仓、要么空仓），无杠杆。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from loguru import logger

from quantforge.data_sources.sina_feed import SinaFinanceFeed
from quantforge.core.data_feed import DataRequest


def backtest_511010(ma_fast=20, ma_slow=60, roc_n=22, roc_threshold=0.0):
    feed = SinaFinanceFeed()
    response = feed.get_data(DataRequest(codes=["511010"], data_type="daily_k", start="2018-01-01", end="2026-05-20"))
    df = response.bar_data.get("511010")
    if df is None or df.empty:
        logger.error("511010 无数据")
        return

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    close = df["close"]

    df["ma_fast"] = close.rolling(ma_fast).mean()
    df["ma_slow"] = close.rolling(ma_slow).mean()
    df["roc"] = close / close.shift(roc_n) - 1

    df["signal_ma"] = (df["ma_fast"] > df["ma_slow"]).astype(int)
    df["signal_roc"] = (df["roc"] > roc_threshold).astype(int)
    df["signal"] = (df["signal_ma"] & df["signal_roc"]).astype(int)
    df["signal"] = df["signal"].shift(1).fillna(0)

    df["daily_ret"] = close.pct_change()
    df["strategy_ret"] = df["daily_ret"] * df["signal"]

    df["benchmark_nv"] = (1 + df["daily_ret"]).cumprod()
    df["strategy_nv"] = (1 + df["strategy_ret"]).cumprod()

    bm_total = df["benchmark_nv"].iloc[-1] - 1
    st_total = df["strategy_nv"].iloc[-1] - 1

    years = (df["date"].iloc[-1] - df["date"].iloc[0]).days / 365.25
    bm_annual = (1 + bm_total) ** (1 / years) - 1
    st_annual = (1 + st_total) ** (1 / years) - 1

    peak = df["strategy_nv"].cummax()
    dd = (peak - df["strategy_nv"]) / peak
    max_dd = dd.max()

    st_ret = df["strategy_ret"]
    sharpe = (st_ret.mean() / st_ret.std() * np.sqrt(252)) if st_ret.std() > 0 else 0

    trades = (df["signal"].diff().abs() == 1).sum()

    print(f"=== 511010 国债ETF 趋势择时回测 ===")
    print(f"参数: MA({ma_fast},{ma_slow}) + ROC({roc_n})>{roc_threshold}")
    print(f"区间: {df['date'].iloc[0].date()} ~ {df['date'].iloc[-1].date()} ({years:.1f}年)")
    print()
    print(f"{'':>15} {'策略':>12} {'基准(持有)':>12}")
    print(f"{'总收益':>15} {st_total:>11.1%}  {bm_total:>11.1%}")
    print(f"{'年化收益':>15} {st_annual:>11.1%}  {bm_annual:>11.1%}")
    print(f"{'最大回撤':>15} {max_dd:>11.1%}")
    print(f"{'Sharpe':>15} {sharpe:>11.2f}")
    print(f"{'交易次数':>15} {trades:>11}")
    print(f"{'换手率/年':>15} {trades/years:>11.1f}")
    in_market = df["signal"].mean()
    print(f"{'持仓比例':>15} {in_market:>11.1%}")

    return {
        "st_total": st_total, "st_annual": st_annual,
        "bm_total": bm_total, "bm_annual": bm_annual,
        "max_dd": max_dd, "sharpe": sharpe,
        "trades": trades, "in_market": in_market,
    }


if __name__ == "__main__":
    for ma_fast, ma_slow in [(5, 20), (10, 30), (20, 60)]:
        for roc_n in [10, 22]:
            backtest_511010(ma_fast=ma_fast, ma_slow=ma_slow, roc_n=roc_n)
            print()
