# @layer: unit
"""T028 辅助信号分析：量价/ATR分位/MACD背离 核心计算逻辑单元测试"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from quantforge.indicators.technical import MACDIndicator, ATRIndicator


def _compute_vol_ratio(vol_series, idx, window=20):
    """量价比：当日成交量 / 前window日均量（不含当日）"""
    vol_start = max(0, idx - window)
    vol_window = vol_series[vol_start:idx]
    vol_window = vol_window[~np.isnan(vol_window)]
    if len(vol_window) >= 5 and vol_series[idx] > 0:
        avg_vol = np.mean(vol_window)
        return vol_series[idx] / avg_vol if avg_vol > 0 else 1.0
    return 1.0


def _compute_atr_pct(atr_series, idx, window=252):
    """ATR 历史分位：当日 ATR 在最近window天中的百分位（含当日）"""
    atr_start = max(0, idx - window)
    atr_window = atr_series[atr_start:idx + 1]
    atr_window = atr_window[~np.isnan(atr_window)]
    atr_val = atr_series[idx]
    if len(atr_window) >= 50 and not np.isnan(atr_val):
        return (np.sum(atr_window < atr_val) / len(atr_window)) * 100
    return 50.0


def _detect_macd_divergence(close_arr, dif_arr, idx, lookback=20):
    """MACD 顶背离：价格创lookback日新高但DIF未确认"""
    lb = max(0, idx - lookback)
    cw = close_arr[lb:idx + 1]
    cw = cw[~np.isnan(cw)]
    dw = dif_arr[lb:idx + 1]
    dw = dw[~np.isnan(dw)]
    if len(cw) < 2:
        return False
    price_new_high = close_arr[idx] >= np.max(cw[:-1])
    if not price_new_high or len(dw) < 2:
        return False
    return not (dif_arr[idx] >= np.max(dw[:-1]))


# ============ 量价比测试 ============

def test_vol_spike():
    """放量：当日成交量是前 20 日均量的 2 倍"""
    vol = np.array([10]*30 + [200], dtype=float)   # 前20天均10，当日200
    ratio = _compute_vol_ratio(vol, 30)
    assert ratio == 20.0, f"预期20.0, 实际{ratio}"


def test_vol_shrink():
    """缩量：当日成交量是前 20 日均量的 0.4 倍"""
    vol = np.array([100]*30 + [40], dtype=float)
    ratio = _compute_vol_ratio(vol, 30)
    assert ratio == 0.4, f"预期0.4, 实际{ratio}"


def test_vol_not_enough_history():
    """数据不足 5 天时返回 1.0"""
    vol = np.array([5, 10, 50], dtype=float)
    ratio = _compute_vol_ratio(vol, 2)
    assert ratio == 1.0, f"预期1.0, 实际{ratio}"


def test_vol_zero_avg():
    """前 20 天成交量全为 0 时返回 1.0"""
    vol = np.array([0]*30 + [100], dtype=float)
    ratio = _compute_vol_ratio(vol, 30)
    assert ratio == 1.0


# ============ ATR 分位测试 ============

def test_atr_high_percentile():
    """当日 ATR 是历史最高值 → 分位 ≈ 100"""
    atr = np.linspace(1, 10, 252)     # 1~10 递增
    atr[-1] = 100                     # 最后一天极高
    pct = _compute_atr_pct(atr, 251)
    # 100 大于所有值（严格 < 比较），所以 251/252 ≈ 99.6
    assert pct > 99, f"预期>99, 实际{pct}"


def test_atr_low_percentile():
    """当日 ATR 是历史最低值 → 分位 ≈ 0"""
    atr = np.linspace(5, 15, 200)
    atr[-1] = 0.1                     # 最后一天极低
    pct = _compute_atr_pct(atr, 199)
    assert pct < 1, f"预期<1, 实际{pct}"


def test_atr_strict_less_than():
    """严格小于比较：全部相等的序列中，分位应为 0%（没有元素 < atr_val）"""
    atr = np.full(100, 5.0)
    pct = _compute_atr_pct(atr, 99)
    assert pct == 0.0, f"全等时分位应为0, 实际{pct}"


# ============ MACD 背离测试 ============

def test_macd_no_divergence():
    """价格和DIF同步创新高 → 无背离"""
    close = np.array([10]*3 + [11, 12, 13, 14, 15, 16, 17, 18, 19, 20], dtype=float)  # 持续涨
    dif = np.array([0.5]*3 + [0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5], dtype=float)  # DIF 也涨
    result = _detect_macd_divergence(close, dif, 12, lookback=10)
    assert not result, "同步创新高应为无背离"


def test_macd_divergence_confirmed():
    """价格新高但DIF低于前期高点 → 顶背离"""
    close = np.array([10]*3 + [10, 10, 10, 10, 10, 10, 10, 10, 15], dtype=float)  # 最后一天大涨
    dif = np.array([0.5]*3 + [2.0, 1.8, 1.5, 1.2, 0.8, 0.5, 0.4, 0.4, 0.3], dtype=float)   # DIF 在跌
    result = _detect_macd_divergence(close, dif, 11, lookback=10)
    assert result, "价创新高DIF未确认应为背离"


def test_macd_not_new_high_no_div():
    """价格未创新高 → 不检测背离"""
    close = np.array([20, 19, 18, 17, 16, 15, 14, 13, 12, 11, 10, 10, 10], dtype=float)  # 持续跌
    dif = np.array([1.0]*13, dtype=float)
    result = _detect_macd_divergence(close, dif, 12, lookback=10)
    assert not result, "未创新高不应判定为背离"


# ============ 集成：用真实 ATR/MACD Indicator 验证和脚本一致 ============

def test_atr_indicator_on_synthetic():
    """ATRIndicator 在合成数据上产出非 NaN 值"""
    n = 50
    np.random.seed(42)
    data = pd.DataFrame({
        'date': pd.date_range('2020-01-01', periods=n, freq='B'),
        'open': 100 + np.cumsum(np.random.randn(n) * 0.5),
        'high': 0.0, 'low': 0.0, 'close': 0.0, 'vol': 10000,
    })
    for i in range(n):
        data.loc[i, 'high'] = data.loc[i, 'open'] + abs(np.random.randn() * 2)
        data.loc[i, 'low'] = data.loc[i, 'open'] - abs(np.random.randn() * 2)
        data.loc[i, 'close'] = data.loc[i, 'low'] + (data.loc[i, 'high'] - data.loc[i, 'low']) * np.random.random()

    atr_ind = ATRIndicator(n=20)
    result = atr_ind.compute(data, n=20)
    atr_vals = result['atr'].dropna()
    assert len(atr_vals) > 20, f"ATR 有效值应>20, 实际{len(atr_vals)}"


def test_macd_indicator_on_synthetic():
    """MACDIndicator 在合成数据上产出非 NaN DIF"""
    n = 50
    data = pd.DataFrame({
        'date': pd.date_range('2020-01-01', periods=n, freq='B'),
        'open': 10.0, 'high': 10.0, 'low': 10.0, 'close': 10 + np.arange(n) * 0.1,
        'vol': 10000,
    })
    macd_ind = MACDIndicator(fast=12, slow=26, signal=9)
    result = macd_ind.compute(data, fast=12, slow=26, signal=9)
    dif_vals = result['dif'].dropna()
    assert len(dif_vals) > 0, f"DIF 应有有效值, 实际{len(dif_vals)}"
    # 持续上涨中 DIF 应为正
    assert dif_vals.iloc[-1] > 0, f"上涨趋势DIF应为正, 实际{dif_vals.iloc[-1]:.4f}"
