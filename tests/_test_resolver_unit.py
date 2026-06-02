# @layer: unit
"""RankingResolver 单元测试：四种权重模式、止损、挤压/TOP_K_Sell、信号卖出。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
import pandas as pd
import numpy as np
from quantforge.core.data_feed import DataResponse
from quantforge.core.decision import Decision, DecisionType
from quantforge.core.resolver import RankingResolver


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


def _make_bar_with_vol(codes, prices, vols):
    """含 volatility 列的 bar_data"""
    dates = pd.date_range("2026-05-01", periods=5, freq="B")
    bar = {}
    for code, price, vol in zip(codes, prices, vols):
        bar[code] = pd.DataFrame({
            "date": dates, "open": [price] * 5,
            "high": [price * 1.01] * 5, "low": [price * 0.99] * 5,
            "close": [price] * 5, "volume": [1000000] * 5,
            "volatility": [vol] * 5,
        })
    return DataResponse(bar_data=bar, macro_data={})


NOW = datetime.now()


def _d_enter(code, priority=0, weight=0.2, confidence=1.0):
    return Decision(DecisionType.ROTATION, NOW, "test", code, "enter", weight, priority, confidence)


def _d_exit(code, priority=99, roc=0.0):
    return Decision(DecisionType.ROTATION, NOW, "exit", code, "exit", 0.0, priority,
                    indicator_values={"roc": roc})


# ========== 权重模式 ==========

def test_top_k_equal_weight():
    decisions = [_d_enter("A", 0), _d_enter("B", 1), _d_enter("C", 2), _d_enter("D", 3), _d_enter("E", 4)]
    r = RankingResolver(top_k=3, weight_method="equal")
    targets = r.resolve(decisions, {}, 40000)

    assert len(targets) == 3, f"应3个target: {len(targets)}"
    for t in targets:
        assert abs(t.target_weight - 1.0 / 3) < 0.001, f"{t.code} weight={t.target_weight}"


def test_top_k_signal_weight():
    decisions = [_d_enter("A", 0, 0.3), _d_enter("B", 1, 0.5), _d_enter("C", 2, 0.2)]
    r = RankingResolver(top_k=3, weight_method="signal_weight")
    targets = r.resolve(decisions, {}, 40000)

    weights = {t.code: t.target_weight for t in targets}
    assert abs(weights["A"] - 0.3) < 0.001, f"A weight={weights['A']}"
    assert abs(weights["B"] - 0.5) < 0.001
    assert abs(weights["C"] - 0.2) < 0.001


def test_kelly_weight_negative():
    """Kelly负值 → 钳位到0"""
    d = _d_enter("A", 0, 0.3, confidence=0.6)
    r = RankingResolver(top_k=3, weight_method="kelly")
    targets = r.resolve([d], {}, 40000)
    assert len(targets) == 1
    assert targets[0].target_weight == 0.0, f"Kelly负值应钳位到0: {targets[0].target_weight}"


def test_kelly_weight_positive():
    """Kelly正值"""
    d = _d_enter("A", 0, 0.5, confidence=0.8)
    r = RankingResolver(top_k=3, weight_method="kelly")
    targets = r.resolve([d], {}, 40000)
    # f* = (0.5*0.8 - 0.2)/0.5 = 0.4
    assert len(targets) == 1
    assert abs(targets[0].target_weight - 0.4) < 0.001, f"Kelly正: {targets[0].target_weight}"


def test_inverse_vol_weight():
    decisions = [_d_enter("A", 0), _d_enter("B", 1)]
    data = _make_bar_with_vol(["A", "B"], [2.0, 2.0], [0.01, 0.04])

    r = RankingResolver(top_k=2, weight_method="inverse_vol")
    targets = r.resolve(decisions, {}, 40000, data)

    weights = {t.code: t.target_weight for t in targets}
    assert abs(sum(weights.values()) - 1.0) < 0.01, f"总权重应≈1: {weights}"
    # vol=0.01 → inv=100, vol=0.04 → inv=25, A:B = 4:1
    assert weights["A"] > weights["B"], f"A应权重大于B: {weights}"


# ========== 止损 ==========

def test_high_watermark_stop():
    data = _make_bar_data(["A"], [1.6])
    positions = {"A": {"shares": 1000, "avg_cost": 1.5, "high_watermark": 2.0}}

    r = RankingResolver(high_watermark_stop_edge=0.15)
    targets = r.resolve([], positions, 40000, data)

    assert any(t.code == "A" and t.target_weight == 0.0 for t in targets), "应触发高水位止损"
    assert any("高水位止损" in t.reason for t in targets)


def test_cost_stop_loss():
    data = _make_bar_data(["A"], [1.7])
    positions = {"A": {"shares": 1000, "avg_cost": 2.0, "high_watermark": 3.0}}

    r = RankingResolver(cut_loss_edge=0.08)
    targets = r.resolve([], positions, 40000, data)

    assert any(t.code == "A" and t.target_weight == 0.0 for t in targets), "应触发成本止损"


def test_stop_skip_when_no_data():
    """data中没有A的bar_data → 不触发止损（但top_k_sell=False避免被卖出）"""
    data = _make_bar_data(["B"], [2.0])
    positions = {"A": {"shares": 1000, "avg_cost": 1.5, "high_watermark": 2.0}}

    r = RankingResolver(high_watermark_stop_edge=0.15, top_k_sell=False)
    targets = r.resolve([], positions, 40000, data)

    assert not any(t.code == "A" for t in targets), "无数据不应触发止损"


# ========== TOP_K_Sell ==========

def test_top_k_sell_immediate():
    decisions = [_d_enter("A", 0), _d_enter("B", 1)]
    positions = {"C": {"shares": 1000, "avg_cost": 1.5, "high_watermark": 2.0}}

    r = RankingResolver(top_k=2, top_k_sell=True)
    targets = r.resolve(decisions, positions, 40000)

    assert any(t.code == "C" and t.target_weight == 0.0 for t in targets), "C不在TOP_K应被卖出"


def test_top_k_sell_false_preserves():
    """top_k_sell=False: 非TOP_K持仓保留，不被立即卖出（无挤压条件）"""
    decisions = [_d_enter("E", 0), _d_enter("F", 1)]
    positions = {c: {"shares": 100, "avg_cost": 2.0, "high_watermark": 3.0}
                 for c in ["A", "B", "C", "D"]}

    r = RankingResolver(top_k=3, top_k_sell=False)
    targets = r.resolve(decisions, positions, 40000)

    sell_codes = {t.code for t in targets if t.target_weight == 0.0}
    # 无挤压压力时，A/B/C/D都不应被卖出
    assert len(sell_codes) == 0, f"无挤压不应卖出: {sell_codes}"


def test_exit_signal_overrides():
    """exit信号优先于top_k rank"""
    decisions = [_d_enter("A", 0), _d_exit("A", roc=-0.5)]
    r = RankingResolver(top_k=3, top_k_sell=True)
    targets = r.resolve(decisions, {"A": {"shares": 100}}, 40000)

    exit_targets = [t for t in targets if t.code == "A" and t.target_weight == 0.0]
    assert len(exit_targets) >= 1, "exit应覆盖enter"


def test_exit_stop_together():
    """A exit + B 高水位止损 → 两者同时输出"""
    data = _make_bar_data(["B"], [1.6])
    decisions = [_d_exit("A", roc=-0.5)]
    positions = {
        "A": {"shares": 100},
        "B": {"shares": 200, "avg_cost": 1.5, "high_watermark": 2.0},
    }

    r = RankingResolver(high_watermark_stop_edge=0.15, top_k_sell=True)
    targets = r.resolve(decisions, positions, 40000, data)

    sold = {t.code for t in targets if t.target_weight == 0.0}
    assert "A" in sold and "B" in sold, f"A和B都应卖出: {sold}"


if __name__ == "__main__":
    test_top_k_equal_weight();       print("PASS test_top_k_equal_weight")
    test_top_k_signal_weight();      print("PASS test_top_k_signal_weight")
    test_kelly_weight_negative();    print("PASS test_kelly_weight_negative")
    test_kelly_weight_positive();    print("PASS test_kelly_weight_positive")
    test_inverse_vol_weight();       print("PASS test_inverse_vol_weight")
    test_high_watermark_stop();      print("PASS test_high_watermark_stop")
    test_cost_stop_loss();           print("PASS test_cost_stop_loss")
    test_stop_skip_when_no_data();   print("PASS test_stop_skip_when_no_data")
    test_top_k_sell_immediate();     print("PASS test_top_k_sell_immediate")
    test_top_k_sell_false_preserves(); print("PASS test_top_k_sell_false_preserves")
    test_exit_signal_overrides();    print("PASS test_exit_signal_overrides")
    test_exit_stop_together();       print("PASS test_exit_stop_together")
    print("\nALL 13 TESTS PASSED")
