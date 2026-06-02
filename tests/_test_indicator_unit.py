# @layer: unit
"""底层技术指标辅助函数 & Indicator 类单元测试：手工验证、NaN/除零/边界。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from quantforge.indicators.technical import (MA, REF, STD, ROCIndicator, RSIIndicator,
                                              MACDIndicator, ATRIndicator, VolatilityIndicator,
                                              BOLLIndicator, MAIndicator)


def test_ma_known_values():
    result = MA([1, 2, 3, 4, 5], 3)
    assert np.isnan(result[0])
    assert np.isnan(result[1])
    assert abs(result[2] - 2.0) < 0.001
    assert abs(result[3] - 3.0) < 0.001
    assert abs(result[4] - 4.0) < 0.001


def test_ref_known_values():
    result = REF([1, 2, 3, 4, 5], 2)
    assert np.isnan(result[0])
    assert np.isnan(result[1])
    assert abs(result[2] - 1.0) < 0.001
    assert abs(result[3] - 2.0) < 0.001
    assert abs(result[4] - 3.0) < 0.001


def test_roc_indicator():
    df = pd.DataFrame({"close": [100.0, 101.0, 99.0, 102.0, 103.0]})
    ind = ROCIndicator(n=2, m=2)
    result = ind.compute(df)
    assert "roc" in result.columns
    assert "maroc" in result.columns
    # roc[2] = 100*(99-100)/100 = -1.0
    assert abs(result["roc"].iloc[2] + 1.0) < 0.1


def test_rsi_all_up():
    """连续涨20天 → RSI ≈ 100"""
    df = pd.DataFrame({"close": [float(i) for i in range(100, 120)]})
    ind = RSIIndicator(n=14)
    result = ind.compute(df)
    assert result["rsi"].iloc[-1] > 95, f"RSI应≈100: {result['rsi'].iloc[-1]}"


def test_rsi_all_down():
    """连续跌20天 → RSI ≈ 0"""
    df = pd.DataFrame({"close": [float(i) for i in range(120, 100, -1)]})
    ind = RSIIndicator(n=14)
    result = ind.compute(df)
    assert result["rsi"].iloc[-1] < 5, f"RSI应≈0: {result['rsi'].iloc[-1]}"


def test_macd_indicator():
    df = pd.DataFrame({"close": [float(i) for i in range(10, 40)]})
    ind = MACDIndicator(fast=12, slow=26, signal=9)
    result = ind.compute(df)
    assert "dif" in result.columns
    assert "dea" in result.columns
    assert "macd_bar" in result.columns
    # 持续上涨 → DIF > 0
    assert result["dif"].iloc[-1] > 0, f"DIF应>0: {result['dif'].iloc[-1]}"


def test_atr_indicator():
    df = pd.DataFrame({
        "high":  [10.0, 11.0, 10.5, 11.5, 12.0],
        "low":   [9.0, 9.5, 9.0, 10.0, 10.5],
        "close": [9.5, 10.0, 10.0, 11.0, 11.5],
        "open":  [9.5, 10.0, 10.0, 11.0, 11.5],
        "volume": [100] * 5,
    })
    ind = ATRIndicator(n=3)
    result = ind.compute(df)
    assert "atr" in result.columns
    assert result["atr"].iloc[-1] > 0


def test_volatility_indicator():
    """价格波动 → 年化波动率 > 0"""
    np.random.seed(42)
    prices = 100 + np.cumsum(np.random.randn(30) * 0.5)
    df = pd.DataFrame({"close": prices})
    ind = VolatilityIndicator(n=20)
    result = ind.compute(df)
    assert "volatility" in result.columns
    assert result["volatility"].iloc[-1] > 0, f"波动率应>0: {result['volatility'].iloc[-1]}"


def test_boll_indicator_flat():
    """全平价 → 布林带全部=价格"""
    df = pd.DataFrame({"close": [100.0] * 30, "open": [100.0] * 30,
                        "high": [100.0] * 30, "low": [100.0] * 30,
                        "volume": [100] * 30})
    ind = BOLLIndicator(n=20, p=2)
    result = ind.compute(df)
    last = result.iloc[-1]
    assert abs(last["boll_mid"] - 100.0) < 0.01
    assert abs(last["boll_upper"] - 100.0) < 0.01
    assert abs(last["boll_lower"] - 100.0) < 0.01


def test_ma_indicator():
    df = pd.DataFrame({"close": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]})
    ind = MAIndicator(periods=[3, 5])
    result = ind.compute(df)
    assert "ma3" in result.columns
    assert "ma5" in result.columns


if __name__ == "__main__":
    test_ma_known_values();       print("PASS test_ma_known_values")
    test_ref_known_values();      print("PASS test_ref_known_values")
    test_roc_indicator();         print("PASS test_roc_indicator")
    test_rsi_all_up();            print("PASS test_rsi_all_up")
    test_rsi_all_down();          print("PASS test_rsi_all_down")
    test_macd_indicator();        print("PASS test_macd_indicator")
    test_atr_indicator();         print("PASS test_atr_indicator")
    test_volatility_indicator();  print("PASS test_volatility_indicator")
    test_boll_indicator_flat();   print("PASS test_boll_indicator_flat")
    test_ma_indicator();          print("PASS test_ma_indicator")
    print("\nALL 10 TESTS PASSED")
