"""拉取美债10Y + CNY/USD汇率历史数据，缓存到 data/

数据来源: akshare
- 美国10年期国债收益率: bond_zh_us_rate
- 美元兑人民币汇率: currency_boc_sina (中行折算价)
"""
import os
import sys
import pandas as pd
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

# ========== 美债10Y ==========
def fetch_us10y():
    print("=== 拉取美债10Y ===")
    try:
        import akshare as ak
    except ImportError:
        print("akshare 未安装，尝试安装...")
        os.system(f"{sys.executable} -m pip install akshare -q")
        import akshare as ak

    # 中美10年期国债收益率对比
    df = ak.bond_zh_us_rate()
    # 列名是中文
    cn_map = {
        '日期': 'date', '中国国债收益率10年': 'CN_10Y', '美国国债收益率10年': 'US_10Y',
        '中国国债收益率2年': 'CN_2Y', '美国国债收益率2年': 'US_2Y',
        '中国国债收益率5年': 'CN_5Y', '美国国债收益率5年': 'US_5Y',
        '中国国债收益率30年': 'CN_30Y', '美国国债收益率30年': 'US_30Y',
        '中国国债收益率10年-2年': 'CN_spread_10_2', '美国国债收益率10年-2年': 'US_spread_10_2',
        '中国GDP年增率': 'CN_GDP', '美国GDP年增率': 'US_GDP',
    }
    df = df.rename(columns=cn_map)
    print(f"  美债10Y数据: {len(df)} 行, {df.date.iloc[0]} ~ {df.date.iloc[-1]}")

    if 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date'])

    # 只保留美债
    us = df[['date', 'US_10Y']].dropna()
    us = us.sort_values('date').reset_index(drop=True)
    print(f"  美债10Y: {len(us)} 行, {us.date.min().date()} ~ {us.date.max().date()}")
    print(f"  US_10Y: min={us.US_10Y.min():.2f}% max={us.US_10Y.max():.2f}% mean={us.US_10Y.mean():.2f}%")

    # 保存
    path = os.path.join(DATA_DIR, 'us10y.csv')
    us.to_csv(path, index=False)
    print(f"  已保存: {path}")
    return us


# ========== CNY/USD 汇率 ==========
def fetch_cny_usd():
    print("\n=== 拉取 CNY/USD 汇率 ===")
    try:
        import akshare as ak
    except ImportError:
        os.system(f"{sys.executable} -m pip install akshare -q")
        import akshare as ak

    # 美元人民币历史数据 (离岸CNH，从2010起)
    df = ak.forex_hist_em(symbol="USDCNH")

    # 日期/最新价
    df = df.rename(columns={'日期': 'date', '最新价': 'CNY_USD'})
    df['date'] = pd.to_datetime(df['date'])

    cny = df[['date', 'CNY_USD']].dropna()
    cny = cny.sort_values('date').reset_index(drop=True)

    print(f"  CNY/USD: {len(cny)} 行, {cny.date.min().date()} ~ {cny.date.max().date()}")
    print(f"  CNY/USD: min={cny.CNY_USD.min():.4f} max={cny.CNY_USD.max():.4f} mean={cny.CNY_USD.mean():.4f}")
    print(f"  最新: {cny.CNY_USD.iloc[-1]:.4f}")

    path = os.path.join(DATA_DIR, 'cny_usd.csv')
    cny.to_csv(path, index=False)
    print(f"  已保存: {path}")
    return cny


# ========== 数据校验 ==========
def validate(us: pd.DataFrame, cny: pd.DataFrame):
    print("\n=== 数据校验 ===")
    issues = []

    # 美债
    if us is not None and not us.empty:
        if us.US_10Y.min() < 0:
            issues.append(f"美债出现负值: {us.US_10Y.min():.2f}%")
        if us.US_10Y.max() > 10:
            issues.append(f"美债异常高: {us.US_10Y.max():.2f}%")
        print(f"  [US10Y] min={us.US_10Y.min():.2f}%  max={us.US_10Y.max():.2f}%  ({len(us)} rows)")

    # 汇率
    if cny is not None and not cny.empty:
        if cny.CNY_USD.min() < 5 or cny.CNY_USD.max() > 9:
            issues.append(f"汇率范围异常: {cny.CNY_USD.min():.2f}~{cny.CNY_USD.max():.2f}")
        print(f"  [CNY] min={cny.CNY_USD.min():.4f}  max={cny.CNY_USD.max():.4f}  ({len(cny)} rows)")

    if issues:
        for i in issues:
            print(f"  FAIL: {i}")
    else:
        print("\n[OK] 数据校验通过")


if __name__ == "__main__":
    us = fetch_us10y()
    cny = fetch_cny_usd()
    validate(us, cny)
    print("\n完成。")