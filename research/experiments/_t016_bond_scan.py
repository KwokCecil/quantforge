"""T016 国债趋势择时：纯MA / 纯ROC / 持有 对比扫描"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd, numpy as np
from quantforge.data_sources.sina_feed import SinaFinanceFeed
from quantforge.core.data_feed import DataRequest

feed = SinaFinanceFeed()
response = feed.get_data(DataRequest(codes=["511010"], data_type="daily_k",
                                     start="2018-01-01", end="2026-05-20"))
df = response.bar_data.get("511010").copy()
df["date"] = pd.to_datetime(df["date"])
df = df.sort_values("date").reset_index(drop=True)
close = df["close"]

yrs = (df["date"].iloc[-1] - df["date"].iloc[0]).days / 365.25
bm_total = (1 + close.pct_change()).cumprod().iloc[-1] - 1
print(f"持有: 总{bm_total:.1%} 年化{(1+bm_total)**(1/yrs)-1:.1%}\n")

for label, gen_signal in [
    ("MA(20)", lambda: close > close.rolling(20).mean()),
    ("MA(60)", lambda: close > close.rolling(60).mean()),
    ("MA(120)", lambda: close > close.rolling(120).mean()),
    ("ROC(10)>0", lambda: close / close.shift(10) - 1 > 0),
    ("ROC(22)>0", lambda: close / close.shift(22) - 1 > 0),
    ("ROC(60)>0", lambda: close / close.shift(60) - 1 > 0),
]:
    sig = gen_signal().astype(int).shift(1).fillna(0)
    sret = close.pct_change() * sig
    nv = (1 + sret).cumprod()
    total = nv.iloc[-1] - 1
    ann = (1 + total) ** (1 / yrs) - 1
    peak = nv.cummax()
    dd = (peak - nv) / peak
    sharpe = sret.mean() / sret.std() * np.sqrt(252) if sret.std() > 0 else 0
    trades = (sig.diff().abs() == 1).sum()
    print(f"{label:>12s}: 总{total:>6.1%} 年{ann:>5.1%} DD{dd.max():>5.1%} Sharpe{sharpe:>5.2f} 交易{trades:>3d} 持仓{sig.mean():>.0%}")
