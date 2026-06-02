# @layer: unit
"""ADXIndicator 单元测试：上涨趋势ADX>20 / 震荡ADX<20 / +DI>-DI / 缺数据"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from quantforge.indicators.technical import ADXIndicator


def _make_df(high_low_close_seq, n=100) -> pd.DataFrame:
    """构造含 open/high/low/close/vol 的 DataFrame"""
    dates = pd.date_range('2020-01-01', periods=n, freq='B')
    df = pd.DataFrame({
        'date': dates,
        'open': [100.0] * n,
        'high': [100.0] * n,
        'low': [100.0] * n,
        'close': [100.0] * n,
        'vol': [10000.0] * n,
    })
    for i, (h, l, c) in enumerate(high_low_close_seq):
        if i < n:
            df.loc[i, 'high'] = h
            df.loc[i, 'low'] = l
            df.loc[i, 'close'] = c
            df.loc[i, 'open'] = c  # 简化：open=close
    return df


def test_adx_uptrend():
    """持续上涨趋势中 ADX 应 > 20"""
    n = 50
    seq = []
    c = 100.0
    for i in range(n):
        c += 0.3
        seq.append((c + 0.2, c - 0.1, c))
    df = _make_df(seq, n=n)
    ind = ADXIndicator(n=14)
    result = ind.compute(df, n=14)
    adx = result['adx'].dropna().iloc[-1]
    assert adx > 20, f"上涨趋势ADX应>20, 实际{adx:.1f}"


def test_adx_sideways():
    """横盘震荡中 ADX 应 < 20"""
    n = 50
    seq = [(100.2, 99.8, 100.0) for _ in range(n)]
    df = _make_df(seq, n=n)
    ind = ADXIndicator(n=14)
    result = ind.compute(df, n=14)
    adx = result['adx'].dropna().iloc[-1]
    assert adx < 20, f"横盘ADX应<20, 实际{adx:.1f}"


def test_plus_di_greater_in_uptrend():
    """上涨趋势中 +DI > -DI"""
    n = 50
    seq = []
    c = 100.0
    for i in range(n):
        c += 0.3
        seq.append((c + 0.2, c - 0.1, c))
    df = _make_df(seq, n=n)
    ind = ADXIndicator(n=14)
    result = ind.compute(df, n=14)
    pdi = result['pdi'].dropna().iloc[-1]
    mdi = result['mdi'].dropna().iloc[-1]
    assert pdi > mdi, f"上涨趋势+pDI应>-DI, pDI={pdi:.1f} mDI={mdi:.1f}"


def test_minus_di_greater_in_downtrend():
    """下跌趋势中 -DI > +DI"""
    n = 50
    seq = []
    c = 100.0
    for i in range(n):
        c -= 0.3
        seq.append((c + 0.1, c - 0.2, c))
    df = _make_df(seq, n=n)
    ind = ADXIndicator(n=14)
    result = ind.compute(df, n=14)
    pdi = result['pdi'].dropna().iloc[-1]
    mdi = result['mdi'].dropna().iloc[-1]
    assert mdi > pdi, f"下跌趋势-DI应>+DI, pDI={pdi:.1f} mDI={mdi:.1f}"


def test_adx_adx_column_exists():
    """ADXIndicator 产出 adx/pdi/mdi 三列"""
    n = 50
    seq = [(100.2, 99.8, 100.0) for _ in range(n)]
    df = _make_df(seq, n=n)
    ind = ADXIndicator(n=14)
    result = ind.compute(df, n=14)
    for col in ['adx', 'pdi', 'mdi']:
        assert col in result.columns, f"缺少列: {col}"


def test_adx_short_data():
    """数据不足时不崩溃"""
    df = _make_df([], n=10)
    ind = ADXIndicator(n=14)
    result = ind.compute(df, n=14)
    assert 'adx' in result.columns
    # 前14天为NaN，不崩溃即可
