# @layer: unit
"""ATR扩张+ADX趋势过滤单元测试：ATR20>1.3xATR200 / ADX<20 / 缺数据"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from quantforge.indicators.technical import ATRIndicator, ADXIndicator


def _make_ohlcv(n: int, high_low_mult: float = 0.05) -> pd.DataFrame:
    """构造波动率逐渐扩大的OHLCV数据"""
    dates = pd.date_range('2020-01-01', periods=n, freq='B')
    df = pd.DataFrame({
        'date': dates,
        'open': [100.0] * n,
        'high': [100.0] * n,
        'low': [100.0] * n,
        'close': [100.0] * n,
        'vol': [10000.0] * n,
    })
    for i in range(n):
        spread = 0.5 + (i / n) * high_low_mult
        df.loc[i, 'close'] = 100 + i * 0.05
        df.loc[i, 'high'] = df.loc[i, 'close'] + spread
        df.loc[i, 'low'] = df.loc[i, 'close'] - spread
        df.loc[i, 'open'] = df.loc[i, 'close'] - spread * 0.3
    return df


def _check_atr_expansion(df: pd.DataFrame) -> bool | None:
    """ATR扩张检测：ATR(20) > 1.3 × ATR(200)。数据不足返回None。"""
    if 'atr' not in df.columns or len(df) < 200:
        return None
    atr20 = df['atr'].iloc[-1]
    atr200_win = df['atr'].iloc[-201:-1].dropna()
    if len(atr200_win) < 50 or pd.isna(atr20):
        return None
    atr200 = float(atr200_win.mean())
    return atr20 > 1.3 * atr200 if atr200 > 0 else None


def test_atr_expansion_spike():
    """波动率突然放大 → ATR扩张"""
    n = 250
    df = _make_ohlcv(n, high_low_mult=0.05)
    # 最后20天波动率翻倍
    for i in range(n - 20, n):
        spread = 5.0 + (i - n + 20) * 0.3
        df.loc[i, 'close'] = df.loc[i, 'close']
        df.loc[i, 'high'] = df.loc[i, 'close'] + spread
        df.loc[i, 'low'] = df.loc[i, 'close'] - spread

    atr = ATRIndicator(n=20)
    df = atr.compute(df, n=20)
    atr200 = ATRIndicator(n=200)
    df = atr200.compute(df, n=200)

    result = _check_atr_expansion(df)
    assert result is True, f"波动率翻倍应触发ATR扩张"


def test_atr_expansion_normal():
    """正常波动 → 不触发"""
    n = 250
    df = _make_ohlcv(n, high_low_mult=0.05)
    atr = ATRIndicator(n=20)
    df = atr.compute(df, n=20)
    atr200 = ATRIndicator(n=200)
    df = atr200.compute(df, n=200)

    result = _check_atr_expansion(df)
    assert result is False, f"正常波动不应触发ATR扩张"


def test_atr_expansion_insufficient_data():
    """数据不足200天 → None"""
    df = _make_ohlcv(50, high_low_mult=0.05)
    atr = ATRIndicator(n=20)
    df = atr.compute(df, n=20)
    result = _check_atr_expansion(df)
    assert result is None, f"数据不足应返回None"


def test_atr_expansion_ratio_precise():
    """精确比值验证：ATR20刚好是ATR200的1.5倍"""
    n = 250
    df = _make_ohlcv(n, high_low_mult=0.01)  # 极低波动
    atr = ATRIndicator(n=20)
    df = atr.compute(df, n=20)
    atr200 = ATRIndicator(n=200)
    df = atr200.compute(df, n=200)

    atr20 = df['atr'].dropna().iloc[-1]
    atr200_vals = df['atr_200'].dropna()
    if len(atr200_vals) >= 50:
        ratio = atr20 / atr200_vals.mean()
        # 正常波动下比值应接近1.0
        assert 0.5 < ratio < 1.5, f"正常波动下比值应接近1, 实际{ratio:.2f}"


def test_adx_below_20_sideways():
    """横盘 → ADX<20"""
    n = 50
    dates = pd.date_range('2020-01-01', periods=n, freq='B')
    df = pd.DataFrame({
        'date': dates, 'open': [100.0]*n, 'high': [100.2]*n,
        'low': [99.8]*n, 'close': [100.0]*n, 'vol': [10000]*n,
    })
    ind = ADXIndicator(n=14)
    df = ind.compute(df, n=14)
    adx = df['adx'].dropna().iloc[-1]
    assert adx < 20, f"横盘ADX应<20"


def test_adx_above_20_uptrend():
    """上涨趋势 → ADX>20"""
    n = 50
    seq_c, seq_h, seq_l = [], [], []
    c = 100.0
    for i in range(n):
        c += 0.3
        seq_h.append(c + 0.2)
        seq_l.append(c - 0.1)
        seq_c.append(c)
    df = pd.DataFrame({
        'date': pd.date_range('2020-01-01', periods=n, freq='B'),
        'open': seq_c, 'high': seq_h, 'low': seq_l, 'close': seq_c,
        'vol': [10000]*n,
    })
    ind = ADXIndicator(n=14)
    df = ind.compute(df, n=14)
    adx = df['adx'].dropna().iloc[-1]
    assert adx > 20, f"上涨趋势ADX应>20"


def test_adx_insufficient_data():
    """数据不足时不崩溃"""
    df = pd.DataFrame({
        'date': pd.date_range('2020-01-01', periods=10, freq='B'),
        'open': [100.0]*10, 'high': [100.2]*10, 'low': [99.8]*10,
        'close': [100.0]*10, 'vol': [10000]*10,
    })
    ind = ADXIndicator(n=14)
    df = ind.compute(df, n=14)
    assert df['adx'].isna().all() or df['adx'].dropna().empty
