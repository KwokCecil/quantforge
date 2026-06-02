# @layer: unit
"""monitors/_shared.py 单元测试：build_decision_report / report_roc_signals"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quantforge.core.decision import Decision, DecisionType
from quantforge.monitors._shared import (
    build_decision_report,
    report_roc_signals,
    _label,
    _roc_line,
)

NOW = datetime(2026, 5, 19, 14, 40, 0)

CODE_NAMES = {
    "159915": "创业板",
    "512880": "证券ETF",
    "510050": "50ETF",
    "510300": "300ETF",
    "159949": "创业板50",
}


def _d(direction, reason, code, priority=0, roc=0.0, maroc=0.0):
    return Decision(
        decision_type=DecisionType.ROTATION,
        timestamp=NOW,
        reason=reason,
        target_code=code,
        direction=direction,
        weight=roc if direction == "enter" else 0.0,
        priority=priority,
        strategy_name="ROC_Momentum",
        indicator_values={"roc": roc, "maroc": maroc},
    )


# ============================================================
# build_decision_report
# ============================================================

def test_build_report_mixed_decisions():
    """混合场景：enter + 被拦截hold + 观望hold + exit"""
    decisions = [
        _d("enter", "ROC=8.50>=5.0", "159915", 0, 8.5, 3.2),
        _d("enter", "ROC=6.20>=5.0", "512880", 1, 6.2, 2.1),
        _d("hold", "ROC=5.50>=5.0; RSI=78.0>=60 禁止买入", "510050", 2, 5.5, 1.8),
        _d("hold", "ROC=4.80 在观望区间", "159949", 3, 4.8, 1.5),
        _d("exit", "ROC=-2.10 < 卖出阈值-2.0", "510300", 4, -2.1, -1.8),
    ]
    report = build_decision_report(decisions, "ROC_Momentum", 14, 40, CODE_NAMES,
                                   held_codes={"510300"})

    assert "ROC_Momentum 决策报告 (14:40)" in report
    assert "159915 创业板" in report
    assert "512880 证券ETF" in report
    assert "── 买入信号 (2) ──" in report
    assert "── 买入被拦截 (1) ──" in report
    assert "── 在观望区间 (1) ──" in report
    assert "── 卖出信号 (1) ──" in report
    assert "RSI=78.0>=60 禁止买入" in report
    assert "ROC=4.80 在观望区间" in report
    assert "510300 300ETF" in report
    assert "共5标的" in report
    assert "enter=2 exit=1 hold=2" in report


def test_build_report_all_hold_watching():
    """全部在观望区间，无买入信号"""
    decisions = [
        _d("hold", "ROC=3.20 在观望区间", "510300", 0, 3.2, 1.0),
        _d("hold", "ROC=2.10 在观望区间", "510050", 1, 2.1, 0.8),
    ]
    report = build_decision_report(decisions, "ROC_Momentum", 11, 0, CODE_NAMES)

    assert "── 买入信号 (0) ──" in report
    assert "── 买入被拦截 (0) ──" in report
    assert "── 在观望区间 (2) ──" in report
    assert "── 卖出信号 (0) ──" in report
    assert "共2标的" in report
    assert "enter=0 exit=0 hold=2" in report


def test_build_report_blocked_only():
    """只有被拦截的，没有通过的"""
    decisions = [
        _d("hold", "ROC=6.00>=5.0; 均线穿越:价格<均线", "159915", 0, 6.0, 2.5),
        _d("hold", "ROC=5.50>=5.0; MACD顶背离:价格新高但DIF未确认 禁止买入", "512880", 1, 5.5, 2.0),
        _d("hold", "ROC=5.20>=5.0; 放量过滤: vol_ratio=2.5>=2.0 禁止买入", "510050", 2, 5.2, 1.8),
        _d("hold", "ROC=3.10 在观望区间", "159949", 3, 3.1, 0.5),
    ]
    report = build_decision_report(decisions, "ROC_Momentum", 14, 40, CODE_NAMES)

    assert "── 买入被拦截 (3) ──" in report
    assert "均线穿越:价格<均线" in report
    assert "MACD顶背离" in report
    assert "放量过滤" in report
    assert "拦截明细: MACD背离1, 均线过滤1, 放量过滤1" in report


def test_build_report_with_held_positions():
    """持仓中的标的应有盈亏信息"""
    decisions = [
        _d("hold", "持仓中无卖出触发", "159915", 0, 8.5, 3.2),
    ]
    positions = {
        "159915": {"shares": 10000, "avg_cost": 0.950, "high_watermark": 1.050},
    }
    from quantforge.core.data_feed import DataResponse
    import pandas as pd
    bar_data = {
        "159915": pd.DataFrame([{"close": 1.020, "date": "2026-05-19"}])
    }
    response = DataResponse(bar_data=bar_data, macro_data={}, metadata={})

    report = build_decision_report(decisions, "ROC_Momentum", 14, 40, CODE_NAMES,
                                   held_codes={"159915"}, positions=positions,
                                   response=response)

    assert "── 持仓观望 (1) ──" in report
    assert "盈亏" in report
    assert "10000股" in report


def test_build_report_empty():
    """空决策列表"""
    report = build_decision_report([], "ROC_Momentum", 10, 15, CODE_NAMES)
    assert "ROC_Momentum 决策报告 (10:15)" in report
    assert "共0标的" in report


# ============================================================
# report_roc_signals 增强
# ============================================================

class _MockNotifier:
    def __init__(self):
        self.calls = []

    def notify(self, title, content):
        self.calls.append((title, content))


def test_report_no_signals_sends_brief():
    """无交易信号时，发送简报"""
    notifier = _MockNotifier()
    decisions = [
        _d("hold", "ROC=3.20 在观望区间", "510300", 0, 3.2, 1.0),
        _d("hold", "ROC=2.10 在观望区间", "510050", 1, 2.1, 0.8),
    ]
    report_roc_signals(notifier, decisions, "ROC_Momentum", 14, 40, CODE_NAMES)

    assert len(notifier.calls) == 1
    title, content = notifier.calls[0]
    assert "14:40" in content
    assert "今日无交易信号" in content or "无买入/卖出信号" in content


def test_report_no_signals_with_blocked():
    """有被拦截标的时，简报列出拦截明细"""
    notifier = _MockNotifier()
    decisions = [
        _d("hold", "ROC=5.50>=5.0; RSI=78.0>=60 禁止买入", "510050", 0, 5.5, 1.8),
        _d("hold", "ROC=5.20>=5.0; 均线穿越:价格<均线", "159915", 1, 5.2, 2.0),
        _d("hold", "ROC=3.20 在观望区间", "510300", 2, 3.2, 1.0),
    ]
    report_roc_signals(notifier, decisions, "ROC_Momentum", 14, 40, CODE_NAMES)

    assert len(notifier.calls) == 1
    _, content = notifier.calls[0]
    assert "拦截" in content
    assert "510050" in content
    assert "RSI" in content


def test_report_with_signals():
    """有真实信号时，正常发送完整报告"""
    notifier = _MockNotifier()
    decisions = [
        _d("enter", "ROC=8.50>=5.0", "159915", 0, 8.5, 3.2),
        _d("exit", "ROC=-2.10 < 卖出阈值-2.0", "510300", 1, -2.1, -1.8),
    ]
    report_roc_signals(notifier, decisions, "ROC_Momentum", 14, 40, CODE_NAMES,
                       held_codes={"510300"})

    assert len(notifier.calls) == 1
    _, content = notifier.calls[0]
    assert "── 卖出信号 ──" in content
    assert "── 买入信号 TOP5 ──" in content


def test_report_no_signals_sends_brief_non_trade_checkpoint():
    """非交易checkpoint无信号时也发送简报（不再沉默）"""
    notifier = _MockNotifier()
    decisions = [
        _d("hold", "ROC=3.20 在观望区间", "510300", 0, 3.2, 1.0),
    ]
    report_roc_signals(notifier, decisions, "ROC_Momentum", 11, 0, CODE_NAMES)

    assert len(notifier.calls) == 1
    _, content = notifier.calls[0]
    assert "今日无交易信号" in content or "无买入/卖出信号" in content


# ============================================================
# _label / _roc_line 已有函数回归
# ============================================================

def test_label_with_name():
    assert _label("159915", CODE_NAMES) == "159915 创业板"


def test_label_without_name():
    assert _label("999999", CODE_NAMES) == "999999"


def test_roc_line_both():
    d = _d("enter", "test", "159915", 0, 8.5, 3.2)
    result = _roc_line(d)
    assert "ROC=8.5" in result
    assert "MAROC=3.2" in result


def test_roc_line_none():
    d = Decision(DecisionType.ROTATION, NOW, "test", "", "hold")
    result = _roc_line(d)
    assert result == ""


if __name__ == "__main__":
    test_build_report_mixed_decisions();          print("PASS test_build_report_mixed_decisions")
    test_build_report_all_hold_watching();        print("PASS test_build_report_all_hold_watching")
    test_build_report_blocked_only();             print("PASS test_build_report_blocked_only")
    test_build_report_with_held_positions();      print("PASS test_build_report_with_held_positions")
    test_build_report_empty();                    print("PASS test_build_report_empty")
    test_report_no_signals_sends_brief();                    print("PASS test_report_no_signals_sends_brief")
    test_report_no_signals_with_blocked();                  print("PASS test_report_no_signals_with_blocked")
    test_report_with_signals();                             print("PASS test_report_with_signals")
    test_report_no_signals_sends_brief_non_trade_checkpoint(); print("PASS test_report_no_signals_sends_brief_non_trade_checkpoint")
    test_label_with_name();                       print("PASS test_label_with_name")
    test_label_without_name();                    print("PASS test_label_without_name")
    test_roc_line_both();                         print("PASS test_roc_line_both")
    test_roc_line_none();                         print("PASS test_roc_line_none")
    print("\nALL 13 TESTS PASSED")