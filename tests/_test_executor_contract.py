# @layer: contract
"""BacktestExecutor 与 LiveExecutor 契约测试：给定相同输入，行为必须一致。

覆盖本次修复的两个核心 bug：
  1. 已有持仓时买入逻辑：目标市值 = 新增资金 + 现有市值（而非减）
  2. 资金分配基准：用循环外快照而非循环内递减值
"""
import os, sys, copy
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from loguru import logger

from quantforge.core.data_feed import DataResponse
from quantforge.core.resolver import TargetPosition
from quantforge.core.executor import BacktestExecutor, LiveExecutor

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)
logger.remove()
logger.add(sys.stderr, level="INFO")


def _make_bar_data(codes, prices, n_days=5):
    dates = pd.date_range("2026-05-01", periods=n_days, freq="B")
    bar = {}
    for code, price in zip(codes, prices):
        bar[code] = pd.DataFrame({
            "date": dates,
            "open": [price] * n_days,
            "high": [price * 1.01] * n_days,
            "low": [price * 0.99] * n_days,
            "close": [price] * n_days,
            "volume": [1000000] * n_days,
        })
    return DataResponse(bar_data=bar, macro_data={})


def _assert_eq(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: 实际={actual}, 期望={expected}")


def _le_holdings(le):
    """LiveExecutor 纯持仓（排除 free_capital 和 last_update）"""
    return {k: v for k, v in le.positions.items()
            if k not in ("free_capital", "last_update")}


def _le_setup(le):
    """LiveExecutor 构造后清空持仓，避免读到真实 position.json"""
    le.positions = {"free_capital": le.initial_capital, "last_update": ""}


def test_new_codes_buy():
    """空仓买入5只新标的：两者行为应完全一致。"""
    codes = ["A", "B", "C", "D", "E"]
    prices = [1.5, 2.0, 3.0, 4.0, 5.0]
    data = _make_bar_data(codes, prices)

    targets = [TargetPosition(code=c, target_weight=0.2, reason="buy") for c in codes]

    be = BacktestExecutor(initial_capital=40000, stop_small_trade=False)
    le = LiveExecutor(initial_capital=40000, dry_run=True)
    _le_setup(le)

    be.execute(copy.deepcopy(targets), data)
    le.execute(copy.deepcopy(targets), data)

    be_pos = be.get_positions()
    le_pos = _le_holdings(le)

    _assert_eq(round(be.cash, 2), round(le.available_capital(), 2), "cash")
    _assert_eq(sorted(be_pos.keys()), sorted(le_pos.keys()), "持仓代码")
    for code in be_pos:
        _assert_eq(be_pos[code]["shares"], le_pos[code]["shares"], f"{code} 股数")
    _assert_eq(len(be.trade_log), len(le_pos), "交易笔数")

    logger.success("  ✅ 空仓买入一致")


def test_existing_positions_add():
    """已有持仓时追买：两者目标市值算法应一致。"""
    codes = ["X", "Y", "Z"]
    prices = [2.0, 1.0, 3.0]
    data = _make_bar_data(codes, prices)

    targets = [
        TargetPosition(code="X", target_weight=0.5, reason="add"),
        TargetPosition(code="Y", target_weight=0.3, reason="add"),
        TargetPosition(code="Z", target_weight=0.2, reason="add"),
    ]

    be = BacktestExecutor(initial_capital=40000, stop_small_trade=False)
    le = LiveExecutor(initial_capital=40000, dry_run=True)

    be.positions = {"X": {"shares": 1000, "avg_cost": 1.8, "high_watermark": 2.0}}
    be.cash = 30000
    le.positions = {"X": {"shares": 1000, "avg_cost": 1.8, "high_watermark": 2.0},
                    "free_capital": 30000, "last_update": ""}

    be.execute(copy.deepcopy(targets), data)
    le.execute(copy.deepcopy(targets), data)

    be_pos = be.get_positions()
    le_pos = _le_holdings(le)

    _assert_eq(round(be.cash, 2), round(le.available_capital(), 2), "cash")
    _assert_eq(sorted(be_pos.keys()), sorted(le_pos.keys()), "持仓代码")
    for code in be_pos:
        _assert_eq(be_pos[code]["shares"], le_pos[code]["shares"], f"{code} 股数")

    logger.success("  ✅ 已有持仓追买一致")


def test_no_trade_when_shares_unchanged():
    """整手取整后股数不变时双方都不交易。"""
    codes = ["P"]
    prices = [100.0]
    data = _make_bar_data(codes, prices)

    targets = [TargetPosition(code="P", target_weight=1.0, reason="hold")]

    be = BacktestExecutor(initial_capital=40000, stop_small_trade=False)
    le = LiveExecutor(initial_capital=40000, dry_run=True)

    be.positions = {"P": {"shares": 300, "avg_cost": 95.0, "high_watermark": 100.0}}
    be.cash = 10000
    le.positions = {"P": {"shares": 300, "avg_cost": 95.0, "high_watermark": 100.0},
                    "free_capital": 10000, "last_update": ""}

    be.execute(copy.deepcopy(targets), data)
    le.execute(copy.deepcopy(targets), data)

    _assert_eq(len(be.trade_log), 0, "Backtest交易数")
    le_h = _le_holdings(le)
    _assert_eq(len(le_h), 1, "Live 持仓数")
    _assert_eq(le_h["P"]["shares"], 300, "Live P股数")
    logger.success("  ✅ 整手不变时双方跳过")


def test_sell_all():
    """全部卖出：双方行为一致。"""
    codes = ["S1", "S2"]
    prices = [5.0, 10.0]
    data = _make_bar_data(codes, prices)

    targets = [
        TargetPosition(code="S1", target_weight=0.0, reason="sell"),
        TargetPosition(code="S2", target_weight=0.0, reason="sell"),
    ]

    be = BacktestExecutor(initial_capital=40000, stop_small_trade=False)
    le = LiveExecutor(initial_capital=40000, dry_run=True)

    be.positions = {
        "S1": {"shares": 2000, "avg_cost": 4.5, "high_watermark": 5.5},
        "S2": {"shares": 1000, "avg_cost": 8.0, "high_watermark": 11.0},
    }
    be.cash = 0
    le.positions = {
        "S1": {"shares": 2000, "avg_cost": 4.5, "high_watermark": 5.5},
        "S2": {"shares": 1000, "avg_cost": 8.0, "high_watermark": 11.0},
        "free_capital": 0, "last_update": "",
    }

    be.execute(copy.deepcopy(targets), data)
    le.execute(copy.deepcopy(targets), data)

    assert abs(be.cash - le.available_capital()) < 1.0, \
        f"卖出后cash: be={be.cash:.2f}, le={le.available_capital():.2f}"

    _assert_eq(len(be.get_positions()), 0, "Backtest 应为空仓")
    _assert_eq(len(_le_holdings(le)), 0, "Live 应为空仓")
    logger.success("  ✅ 全部卖出一致")


def test_dry_run_does_not_write():
    """dry_run=True 不写 position.json。"""
    import json, tempfile
    fd, tmpfile = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        initial = {"free_capital": 10000, "last_update": "", "Q": {"shares": 100, "avg_cost": 10.0, "high_watermark": 12.0}}
        with open(tmpfile, "w", encoding="utf-8") as f:
            json.dump(initial, f, ensure_ascii=False, indent=2)

        data = _make_bar_data(["Q", "R"], [10.0, 5.0])
        targets = [TargetPosition(code="R", target_weight=1.0, reason="buy")]

        le = LiveExecutor(position_file=tmpfile, initial_capital=10000, dry_run=True)
        le.execute(targets, data)

        with open(tmpfile, "r", encoding="utf-8") as f:
            saved = json.load(f)
        assert saved == initial, f"dry_run=True 但文件被修改: free_capital={saved.get('free_capital')}"
        logger.success("  ✅ dry_run 不写文件")
    finally:
        os.unlink(tmpfile)


def test_e2e_complex():
    """复合场景：部分卖出+部分加仓+新仓买入。"""
    codes = ["M1", "M2", "M3", "M4"]
    prices = [2.0, 1.5, 3.0, 4.0]
    data = _make_bar_data(codes, prices)

    targets = [
        TargetPosition(code="M1", target_weight=0.0, reason="sell"),
        TargetPosition(code="M2", target_weight=0.4, reason="add"),
        TargetPosition(code="M3", target_weight=0.4, reason="add"),
        TargetPosition(code="M4", target_weight=0.2, reason="new"),
    ]

    be = BacktestExecutor(initial_capital=40000, stop_small_trade=False)
    le = LiveExecutor(initial_capital=40000, dry_run=True)

    be.positions = {
        "M1": {"shares": 2000, "avg_cost": 1.8, "high_watermark": 2.2},
        "M2": {"shares": 1000, "avg_cost": 1.3, "high_watermark": 1.6},
    }
    be.cash = 5000
    le.positions = {
        "M1": {"shares": 2000, "avg_cost": 1.8, "high_watermark": 2.2},
        "M2": {"shares": 1000, "avg_cost": 1.3, "high_watermark": 1.6},
        "free_capital": 5000, "last_update": "",
    }

    be.execute(copy.deepcopy(targets), data)
    le.execute(copy.deepcopy(targets), data)

    be_pos = be.get_positions()
    le_pos = _le_holdings(le)

    assert abs(be.cash - le.available_capital()) < 2.0, \
        f"cash: be={be.cash:.2f}, le={le.available_capital():.2f}"

    _assert_eq(sorted(be_pos.keys()), sorted(le_pos.keys()), "持仓代码")
    for code in be_pos:
        _assert_eq(be_pos[code]["shares"], le_pos[code]["shares"], f"{code} 股数")

    logger.success("  ✅ 复合场景一致")


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("BacktestExecutor vs LiveExecutor 契约测试")
    logger.info("=" * 60)
    try:
        test_new_codes_buy()
        test_existing_positions_add()
        test_no_trade_when_shares_unchanged()
        test_sell_all()
        test_dry_run_does_not_write()
        test_e2e_complex()
        logger.success("\n全部 6 项通过 ✅")
    except AssertionError as e:
        logger.error(f"\n❌ 失败: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"\n❌ 异常: {e}")
        import traceback; traceback.print_exc()
        sys.exit(1)
