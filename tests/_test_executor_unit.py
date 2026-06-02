# @layer: unit
"""BacktestExecutor 单元测试：买入、卖出、追加、整手、资金不足降级、成本计算。"""
import os, sys, copy
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from quantforge.core.data_feed import DataResponse
from quantforge.core.resolver import TargetPosition
from quantforge.core.executor import BacktestExecutor


def _make_bar_data(codes, prices, n_days=5):
    dates = pd.date_range("2026-05-01", periods=n_days, freq="B")
    bar = {}
    for code, price in zip(codes, prices):
        bar[code] = pd.DataFrame({
            "date": dates, "open": [price] * n_days,
            "high": [price * 1.01] * n_days, "low": [price * 0.99] * n_days,
            "close": [price] * n_days, "volume": [1000000] * n_days,
        })
    return DataResponse(bar_data=bar, macro_data={})


def _est_shares(money, price):
    return int(money / (price * 1.001) / 100) * 100


# ========== 买入场景 ==========

def test_buy_new_codes():
    """空仓买入5只等权标的"""
    codes = ["A", "B", "C", "D", "E"]
    prices = [1.5, 2.0, 3.0, 4.0, 5.0]
    data = _make_bar_data(codes, prices)
    targets = [TargetPosition(code=c, target_weight=0.2, reason="buy") for c in codes]

    be = BacktestExecutor(initial_capital=40000, stop_small_trade=False)
    be.execute(copy.deepcopy(targets), data)

    assert len(be.positions) == 5, f"应5个持仓: {len(be.positions)}"
    assert be.cash > 0, f"应有找零, cash={be.cash}"
    assert be.cash < 40000
    assert be.total_commission > 0
    assert be.total_slippage > 0

    for code, price in zip(codes, prices):
        assert code in be.positions, f"{code}未建仓"
        assert be.positions[code]["shares"] == _est_shares(8000, price)


def test_buy_full_capital():
    """全仓买入1个标的"""
    data = _make_bar_data(["X"], [2.0])
    targets = [TargetPosition(code="X", target_weight=1.0, reason="all_in")]

    be = BacktestExecutor(initial_capital=40000, stop_small_trade=False)
    be.execute(copy.deepcopy(targets), data)

    expected_shares = _est_shares(40000, 2.0)
    assert expected_shares > 0
    assert be.positions["X"]["shares"] == expected_shares

    expected_cash = 40000 - expected_shares * 2.0 * 1.001 - max(expected_shares * 2.0 * 1.001 * 0.00025, 0)
    assert abs(be.cash - expected_cash) < 0.01, f"cash偏差: {be.cash} vs {expected_cash}"


def test_buy_insufficient_cash():
    """资金不足一手 → 被_buy内部调整跳过"""
    data = _make_bar_data(["X"], [10.0])
    targets = [TargetPosition(code="X", target_weight=1.0, reason="buy")]

    be = BacktestExecutor(initial_capital=1000, stop_small_trade=False)
    be.execute(copy.deepcopy(targets), data)

    assert len(be.positions) == 0, f"不足一手应空仓: {be.positions}"


def test_buy_insufficient_cash_adjust():
    """资金只够2手 → 买入2手"""
    data = _make_bar_data(["X"], [10.0])
    targets = [TargetPosition(code="X", target_weight=1.0, reason="buy")]

    be = BacktestExecutor(initial_capital=3000, stop_small_trade=False)
    be.execute(copy.deepcopy(targets), data)

    assert be.positions["X"]["shares"] == 200, f"应买200股: {be.positions}"


# ========== 卖出场景 ==========

def test_sell_all():
    """全部卖出 → 持仓删除"""
    data = _make_bar_data(["A"], [2.0])
    be = BacktestExecutor(initial_capital=40000, stop_small_trade=False)

    shares = _est_shares(40000, 2.0)
    be.positions["A"] = {"shares": shares, "avg_cost": 1.5, "high_watermark": 2.0}
    old_cash = be.cash

    targets = [TargetPosition(code="A", target_weight=0.0, reason="exit")]
    be.execute(copy.deepcopy(targets), data)

    assert "A" not in be.positions, f"A未清除: {be.positions}"
    assert be.cash > old_cash, f"cash应增加: {be.cash} > {old_cash}"


# ========== 调仓减仓 ==========

def test_rebalance_sell_excess():
    """调仓减仓：当前10000股，目标降至30%"""
    data = _make_bar_data(["A"], [2.0])
    be = BacktestExecutor(initial_capital=40000, stop_small_trade=False)
    be.positions["A"] = {"shares": 10000, "avg_cost": 1.5, "high_watermark": 2.0}
    be.cash = 30000  # 总资产 = 30000 + 20000 = 50000

    targets = [TargetPosition(code="A", target_weight=0.3, reason="调仓")]
    be.execute(copy.deepcopy(targets), data)

    assert "A" in be.positions, "调仓后A应仍在"
    remaining = be.positions["A"]["shares"]
    assert remaining < 10000, f"应减仓: {remaining}"
    assert remaining > 0


# ========== 追加买入 ==========

def test_追加_average_cost():
    """追加买入：加权平均成本"""
    data = _make_bar_data(["A"], [2.0])
    be = BacktestExecutor(initial_capital=40000, stop_small_trade=False)
    be.positions["A"] = {"shares": 1000, "avg_cost": 1.5, "high_watermark": 1.5}
    be.cash = 30000

    targets = [TargetPosition(code="A", target_weight=1.0, reason="追加")]
    be.execute(copy.deepcopy(targets), data)

    new_avg = be.positions["A"]["avg_cost"]
    assert new_avg > 1.5, f"avg_cost应上升: {new_avg}"
    assert new_avg < 2.001
    assert be.positions["A"]["shares"] > 1000


# ========== 高水位线 ==========

def test_high_watermark_update():
    """价格上涨 → 高水位更新"""
    data = _make_bar_data(["A"], [2.0])
    be = BacktestExecutor(initial_capital=40000, stop_small_trade=False)
    be.positions["A"] = {"shares": 1000, "avg_cost": 1.5, "high_watermark": 1.5}
    be.cash = 30000

    be.execute([], data)
    assert be.positions["A"]["high_watermark"] == 2.0


def test_high_watermark_no_update():
    """价格下跌 → 高水位保持"""
    data = _make_bar_data(["A"], [2.0])
    be = BacktestExecutor(initial_capital=40000, stop_small_trade=False)
    be.positions["A"] = {"shares": 1000, "avg_cost": 1.5, "high_watermark": 3.0}
    be.cash = 30000

    be.execute([], data)
    assert be.positions["A"]["high_watermark"] == 3.0


# ========== 净值记录 ==========

def test_net_value_recorded():
    """每次execute记录净值"""
    data = _make_bar_data(["A"], [2.0])
    be = BacktestExecutor(initial_capital=40000, stop_small_trade=False)

    targets = [TargetPosition(code="A", target_weight=1.0, reason="buy")]
    be.execute(copy.deepcopy(targets), data)

    assert len(be.net_values) == 1
    nv = be.net_values[0]
    assert "date" in nv
    assert "net_value" in nv
    assert "cash" in nv
    assert "total_value" in nv


# ========== 小额跳过 ==========

def test_small_trade_skip():
    """交易金额 < 2000 → 跳过"""
    data = _make_bar_data(["A"], [1.0])
    targets = [TargetPosition(code="A", target_weight=1.0, reason="buy")]

    be = BacktestExecutor(initial_capital=1500, stop_small_trade=True, skip_small_trade_limit=2000)
    be.execute(copy.deepcopy(targets), data)

    assert len(be.positions) == 0, f"小额应跳过: {be.positions}"


if __name__ == "__main__":
    test_buy_new_codes();       print("PASS test_buy_new_codes")
    test_buy_full_capital();    print("PASS test_buy_full_capital")
    test_buy_insufficient_cash(); print("PASS test_buy_insufficient_cash")
    test_buy_insufficient_cash_adjust(); print("PASS test_buy_insufficient_cash_adjust")
    test_sell_all();            print("PASS test_sell_all")
    test_rebalance_sell_excess(); print("PASS test_rebalance_sell_excess")
    test_追加_average_cost();   print("PASS test_追加_average_cost")
    test_high_watermark_update(); print("PASS test_high_watermark_update")
    test_high_watermark_no_update(); print("PASS test_high_watermark_no_update")
    test_net_value_recorded();  print("PASS test_net_value_recorded")
    test_small_trade_skip();    print("PASS test_small_trade_skip")
    print("\nALL 11 TESTS PASSED")
