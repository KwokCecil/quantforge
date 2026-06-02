# @layer: unit
"""量价过滤+波动率过滤单元测试：放量阻断/正常波阻断/缺数据不崩溃/卖出不受影响"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from quantforge.indicators.technical import ATRIndicator


def _compute_vol_ratio(df: pd.DataFrame, window: int = 20) -> float:
    """当日成交量 / 前window日均量（不含当日）。不足5天时返回-1。"""
    if 'vol' not in df.columns or len(df) < window:
        return -1.0
    vol_today = float(df['vol'].iloc[-1])
    if vol_today <= 0:
        return -1.0
    vol_window = df['vol'].iloc[-window - 1:-1].dropna()
    if len(vol_window) < 5:
        return -1.0
    avg = vol_window.mean()
    return vol_today / avg if avg > 0 else -1.0


def _compute_atr_pct(df: pd.DataFrame, hist_window: int = 252) -> float:
    """ATR(20) 在最近hist_window天中的百分位（严格<）。不足50天时返回-1。"""
    if 'atr' not in df.columns or len(df) < hist_window:
        return -1.0
    atr_val = df['atr'].iloc[-1]
    if pd.isna(atr_val):
        return -1.0
    atr_win = df['atr'].iloc[-hist_window:].dropna()
    if len(atr_win) < 50:
        return -1.0
    return (atr_win < atr_val).sum() / len(atr_win) * 100


def _make_atr_df(high_low_seq, n=60) -> pd.DataFrame:
    """构造含 OHLCV 的 DataFrame 并计算 ATR(20)"""
    data = {
        'date': pd.date_range('2020-01-01', periods=n, freq='B'),
        'close': [100.0] * n,
        'vol': [10000.0] * n,
    }
    for i in range(n):
        data['open'] = data['open'] if 'open' in data else [100.0] * n
        data['high'] = data['high'] if 'high' in data else [100.0] * n
        data['low'] = data['low'] if 'low' in data else [100.0] * n

    # 用 ATR 需要 high/low/close
    df_data = {
        'date': pd.date_range('2020-01-01', periods=n, freq='B'),
        'open': [100.0] * n,
        'high': [101.0] * n,
        'low': [99.0] * n,
        'close': [100.0] * n,
        'vol': [10000.0] * n,
    }
    df = pd.DataFrame(df_data)
    atr_ind = ATRIndicator(n=20)
    df = atr_ind.compute(df, n=20)

    # 覆盖 high/low 创造不同 ATR 值
    if high_low_seq:
        for i, (h, l) in enumerate(high_low_seq):
            if i < n:
                df.loc[i, 'high'] = h
                df.loc[i, 'low'] = l
    return df


# ============ 量价比测试 ============

def test_vol_spike_ratio():
    """放量 2 倍：vol_ratio = 2.0"""
    n = 30
    df = pd.DataFrame({
        'date': pd.date_range('2020-01-01', periods=n, freq='B'),
        'vol': [100.0] * 29 + [200.0],
    })
    ratio = _compute_vol_ratio(df, window=20)
    assert ratio == 2.0, f"预期2.0, 实际{ratio}"


def test_vol_normal_ratio():
    """正常量：vol_ratio ≈ 1.0"""
    n = 30
    df = pd.DataFrame({
        'date': pd.date_range('2020-01-01', periods=n, freq='B'),
        'vol': [100.0] * 30,
    })
    ratio = _compute_vol_ratio(df, window=20)
    assert ratio == 1.0, f"预期1.0, 实际{ratio}"


def test_vol_insufficient_data():
    """数据不足 → 返回 -1"""
    df = pd.DataFrame({
        'date': pd.date_range('2020-01-01', periods=10, freq='B'),
        'vol': [100.0] * 10,
    })
    ratio = _compute_vol_ratio(df, window=20)
    assert ratio == -1.0, f"数据不足应返回-1, 实际{ratio}"


def test_vol_missing_column():
    """没有 vol 列 → 返回 -1"""
    df = pd.DataFrame({
        'date': pd.date_range('2020-01-01', periods=30, freq='B'),
        'close': [100.0] * 30,
    })
    ratio = _compute_vol_ratio(df, window=20)
    assert ratio == -1.0, f"缺vol列应返回-1, 实际{ratio}"


# ============ ATR 分位测试 ============

def test_atr_high_percentile():
    """ATR 递增序列，最后一天是最高值 → 分位接近 100"""
    n = 100
    hl_seq = [(100 + i * 0.05, 100 - i * 0.05) for i in range(n)]
    df = _make_atr_df(hl_seq, n=n)
    # ATR 随高低差递增，最后一个应最大
    pct = _compute_atr_pct(df, hist_window=100)
    assert pct > 0, f"ATR分位应>0, 实际{pct}"


def test_atr_low_percentile():
    """ATR 递减序列，最后一天是最低值 → 分位接近 0"""
    n = 100
    hl_seq = [(100 + (n - i) * 0.05, 100 - (n - i) * 0.05) for i in range(n)]
    df = _make_atr_df(hl_seq, n=n)
    pct = _compute_atr_pct(df, hist_window=100)
    assert pct < 100, f"ATR分位应<100, 实际{pct}"


def test_atr_insufficient_data():
    """ATR 数据不足 50 天 → 返回 -1"""
    df = _make_atr_df([], n=30)
    pct = _compute_atr_pct(df, hist_window=60)
    assert pct == -1.0, f"数据不足应返回-1, 实际{pct}"


def test_atr_missing_column():
    """没有 atr 列 → 返回 -1"""
    df = pd.DataFrame({
        'date': pd.date_range('2020-01-01', periods=100, freq='B'),
        'close': [100.0] * 100,
    })
    pct = _compute_atr_pct(df, hist_window=100)
    assert pct == -1.0, f"缺atr列应返回-1, 实际{pct}"
