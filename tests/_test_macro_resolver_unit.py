# @layer: unit
"""MacroOverlayResolver 单元测试：CDR分段映射、ERP修正、趋势过滤、EMA平滑、权重融合。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
import pandas as pd
import numpy as np
from quantforge.core.data_feed import DataResponse
from quantforge.core.decision import Decision, DecisionType
from quantforge.core.resolver import MacroOverlayResolver

NOW = datetime.now()


def _make_bar_data(codes, prices, extra_cols=None):
    dates = pd.date_range("2026-05-01", periods=100, freq="B")
    bar = {}
    for code, price in zip(codes, prices):
        df = pd.DataFrame({
            "date": dates, "open": [price] * 100,
            "high": [price * 1.01] * 100, "low": [price * 0.99] * 100,
            "close": [price] * 100, "volume": [1000000] * 100,
        })
        bar[code] = df
    return DataResponse(bar_data=bar, macro_data={})


def _d_timing(erp=0, percentile=50):
    return Decision(DecisionType.TIMING, NOW, "macro", "", "", 0, 0,
                    indicator_values={"erp": erp, "percentile": percentile})


def _d_enter(code, priority=0, weight=0.5):
    return Decision(DecisionType.ROTATION, NOW, "test", code, "enter", weight, priority)


# ========== CDR 分段映射 ==========

def test_cdr_percentile_50():
    r = MacroOverlayResolver()
    cdr = r._percentile_to_cdr(50)
    assert abs(cdr - 0.50) < 0.01, f"CDR(50%)=0.50: {cdr}"


def test_cdr_percentile_0():
    r = MacroOverlayResolver()
    cdr = r._percentile_to_cdr(0)
    assert abs(cdr - 0.0) < 0.01, f"CDR(0%)=0.0: {cdr}"


def test_cdr_percentile_100():
    r = MacroOverlayResolver()
    cdr = r._percentile_to_cdr(100)
    assert abs(cdr - 1.0) < 0.01, f"CDR(100%)=1.0: {cdr}"


def test_cdr_percentile_20():
    r = MacroOverlayResolver()
    cdr = r._percentile_to_cdr(20)
    assert abs(cdr - 0.20) < 0.01, f"CDR(20%)=0.20: {cdr}"


# ========== ERP 绝对值修正 ==========

def test_erp_abs_cap():
    r = MacroOverlayResolver(erp_abs_min=-5.0)
    capped = r._apply_erp_abs_cap(0.8, -10.0)
    assert abs(capped - 0.4) < 0.01, f"CDR应折半: {capped}"


def test_erp_abs_no_cap():
    r = MacroOverlayResolver(erp_abs_min=-5.0)
    result = r._apply_erp_abs_cap(0.8, 0.0)
    assert abs(result - 0.8) < 0.01, f"CDR不变: {result}"


# ========== 趋势过滤 ==========

def test_trend_filter_bear():
    """close < MA50 → CDR cap到0.5"""
    data = _make_bar_data(["510300"], [2.0])
    # 前50天高价，后50天低价 → close[-1] < MA50
    data.bar_data["510300"].loc[:49, "close"] = 3.0
    data.bar_data["510300"].loc[50:, "close"] = 2.0

    r = MacroOverlayResolver(trend_ma=50)
    trend_ok = r._check_trend(data, "510300")
    # MA50 ≈ (50*3 + 50*2)/100 = 2.5，close=2.0 < 2.5 → False
    assert not trend_ok

    capped = r._apply_trend_cap(0.8, False)
    assert abs(capped - 0.5) < 0.01


def test_trend_filter_bull():
    """close[-1] > MA(最后50天) → 不cap"""
    data = _make_bar_data(["510300"], [3.0])
    data.bar_data["510300"].loc[:49, "close"] = 2.0
    # 最后一天价格拉高 → > MA50
    data.bar_data["510300"].iloc[-1, data.bar_data["510300"].columns.get_loc("close")] = 4.0

    r = MacroOverlayResolver(trend_ma=50)
    trend_ok = r._check_trend(data, "510300")
    assert trend_ok


# ========== EMA 平滑 ==========

def test_ema_smooth_init():
    r = MacroOverlayResolver(cdr_smooth_alpha=0.3)
    assert not r._cdr_initialized
    result = r._smooth_cdr(0.8)
    assert abs(result - 0.8) < 0.001
    assert r._cdr_initialized


def test_ema_smooth_convergence():
    r = MacroOverlayResolver(cdr_smooth_alpha=0.3)
    r._smooth_cdr(0.5)  # 初始化 state=0.5
    result = r._smooth_cdr(1.0)
    expected = 0.3 * 1.0 + 0.7 * 0.5  # = 0.65
    assert abs(result - expected) < 0.001, f"EMA: {result} != {expected}"


# ========== 权重融合 ==========

def test_weight_fusion():
    """ROTATION weight × CDR → final_weight"""
    data = _make_bar_data(["A", "B"], [2.0, 3.0])
    decisions = [
        _d_timing(erp=0, percentile=80),
        _d_enter("A", 0, 0.5),
        _d_enter("B", 1, 0.5),
    ]

    r = MacroOverlayResolver(top_k=2, min_position_pct=0.01, cdr_smooth_alpha=1.0)
    targets = r.resolve(decisions, {}, 40000, data)

    buy_targets = [t for t in targets if t.target_weight > 0]
    assert len(buy_targets) >= 2, f"应2个买入: {buy_targets}"
    for t in buy_targets:
        assert t.target_weight < 0.5, f"权重应被CDR缩减: {t.target_weight}"


def test_min_position_filter():
    """weight < min_position_pct → 被过滤"""
    decisions = [_d_timing(erp=0, percentile=10), _d_enter("A", 0, 0.03)]

    r = MacroOverlayResolver(top_k=3, min_position_pct=0.05, cdr_smooth_alpha=1.0)
    targets = r.resolve(decisions, {}, 40000)

    buy_targets = [t for t in targets if t.target_weight > 0 and t.code != "CASH"]
    assert len(buy_targets) == 0, f"小额应被过滤: {buy_targets}"


def test_defensive_code():
    """防御仓位配defensive_code"""
    decisions = [_d_timing(erp=0, percentile=50)]

    r = MacroOverlayResolver(defensive_code="510310", cdr_smooth_alpha=1.0)
    targets = r.resolve(decisions, {}, 40000)

    defensive = [t for t in targets if t.code == "510310"]
    assert len(defensive) == 1, f"应有防御仓位: {targets}"


if __name__ == "__main__":
    test_cdr_percentile_50();        print("PASS test_cdr_percentile_50")
    test_cdr_percentile_0();         print("PASS test_cdr_percentile_0")
    test_cdr_percentile_100();       print("PASS test_cdr_percentile_100")
    test_cdr_percentile_20();        print("PASS test_cdr_percentile_20")
    test_erp_abs_cap();              print("PASS test_erp_abs_cap")
    test_erp_abs_no_cap();           print("PASS test_erp_abs_no_cap")
    test_trend_filter_bear();        print("PASS test_trend_filter_bear")
    test_trend_filter_bull();        print("PASS test_trend_filter_bull")
    test_ema_smooth_init();          print("PASS test_ema_smooth_init")
    test_ema_smooth_convergence();   print("PASS test_ema_smooth_convergence")
    test_weight_fusion();            print("PASS test_weight_fusion")
    test_min_position_filter();      print("PASS test_min_position_filter")
    test_defensive_code();           print("PASS test_defensive_code")
    print("\nALL 13 TESTS PASSED")
