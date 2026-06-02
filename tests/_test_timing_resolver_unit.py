# @layer: unit
"""TimingResolver 单元测试：enter/exit映射、bond_etf对端、rebalance阈值、止损。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
import pandas as pd
from quantforge.core.data_feed import DataResponse
from quantforge.core.decision import Decision, DecisionType
from quantforge.core.resolver import TimingResolver

NOW = datetime.now()


def _make_bar_data(codes, prices):
    dates = pd.date_range("2026-05-01", periods=5, freq="B")
    bar = {}
    for code, price in zip(codes, prices):
        bar[code] = pd.DataFrame({
            "date": dates, "open": [price] * 5,
            "high": [price * 1.01] * 5, "low": [price * 0.99] * 5,
            "close": [price] * 5, "volume": [1000000] * 5,
        })
    return DataResponse(bar_data=bar, macro_data={})


def _d_timing(code, direction, weight=1.0):
    return Decision(DecisionType.TIMING, NOW, "test", code, direction, weight)


def test_enter_full():
    decisions = [_d_timing("510300", "enter", 1.0)]
    r = TimingResolver()
    targets = r.resolve(decisions, {}, 40000)
    assert len(targets) == 1
    assert targets[0].code == "510300"
    assert targets[0].target_weight == 1.0


def test_enter_with_bond_etf():
    decisions = [_d_timing("510300", "enter", 0.6)]
    r = TimingResolver(bond_etf="511010")
    targets = r.resolve(decisions, {}, 40000)

    codes = {t.code: t.target_weight for t in targets}
    assert abs(codes["510300"] - 0.6) < 0.01
    assert abs(codes["511010"] - 0.4) < 0.01


def test_exit_with_bond_etf():
    decisions = [_d_timing("510300", "exit")]
    r = TimingResolver(bond_etf="511010")
    targets = r.resolve(decisions, {"510300": {"shares": 100}}, 40000)

    codes = {t.code: t.target_weight for t in targets}
    assert abs(codes["510300"] - 0.0) < 0.01
    assert abs(codes["511010"] - 1.0) < 0.01


def test_exit_no_bond_etf():
    decisions = [_d_timing("510300", "exit")]
    r = TimingResolver()
    targets = r.resolve(decisions, {}, 40000)

    assert len(targets) == 1
    assert targets[0].code == "510300"
    assert targets[0].target_weight == 0.0


def test_hold_no_action():
    decisions = [_d_timing("510300", "hold")]
    r = TimingResolver()
    targets = r.resolve(decisions, {}, 40000)
    assert len(targets) == 0, f"hold不应产出target: {targets}"


def test_rebalance_trigger():
    """当前持有80%, enter 40%, 偏差超阈值 → 触发调仓"""
    data = _make_bar_data(["510300", "511010"], [2.0, 1.0])
    decisions = [_d_timing("510300", "enter", 0.4)]
    positions = {
        "510300": {"shares": 20000, "avg_cost": 1.5, "high_watermark": 2.0},
        "511010": {"shares": 10000, "avg_cost": 0.8, "high_watermark": 1.0},
    }

    r = TimingResolver(bond_etf="511010", rebalance_threshold=0.30)
    targets = r.resolve(decisions, positions, 40000, data)

    codes = {t.code: t.target_weight for t in targets}
    assert abs(codes.get("510300", 0) - 0.4) < 0.01, f"510300 weight异常: {codes}"
    assert abs(codes.get("511010", 0) - 0.6) < 0.01, f"511010 weight异常: {codes}"


def test_rebalance_no_trigger():
    """偏差未超阈值 → 直接enter"""
    data = _make_bar_data(["510300", "511010"], [2.0, 1.0])
    decisions = [_d_timing("510300", "enter", 0.6)]
    positions = {
        "510300": {"shares": 10000, "avg_cost": 2.0, "high_watermark": 2.0},
        "511010": {"shares": 10000, "avg_cost": 1.0, "high_watermark": 1.0},
    }

    r = TimingResolver(bond_etf="511010", rebalance_threshold=0.30)
    targets = r.resolve(decisions, positions, 40000, data)

    codes = {t.code: t.target_weight for t in targets}
    assert abs(codes.get("510300", 0) - 0.6) < 0.01


def test_stop_loss_in_timing():
    """择时enter + B触发高水位止损 → B被卖出"""
    data = _make_bar_data(["510300", "B"], [2.0, 1.6])
    decisions = [_d_timing("510300", "enter", 1.0)]
    positions = {
        "510300": {"shares": 100},
        "B": {"shares": 100, "avg_cost": 1.5, "high_watermark": 2.0},
    }

    r = TimingResolver(high_watermark_stop_edge=0.15)
    targets = r.resolve(decisions, positions, 40000, data)

    sold = [t for t in targets if t.target_weight == 0.0]
    assert any(t.code == "B" for t in sold), f"B应被止损: {sold}"
    assert any(t.code == "510300" and t.target_weight > 0 for t in targets)


if __name__ == "__main__":
    test_enter_full();          print("PASS test_enter_full")
    test_enter_with_bond_etf(); print("PASS test_enter_with_bond_etf")
    test_exit_with_bond_etf();  print("PASS test_exit_with_bond_etf")
    test_exit_no_bond_etf();    print("PASS test_exit_no_bond_etf")
    test_hold_no_action();      print("PASS test_hold_no_action")
    test_rebalance_trigger();   print("PASS test_rebalance_trigger")
    test_rebalance_no_trigger(); print("PASS test_rebalance_no_trigger")
    test_stop_loss_in_timing(); print("PASS test_stop_loss_in_timing")
    print("\nALL 8 TESTS PASSED")
