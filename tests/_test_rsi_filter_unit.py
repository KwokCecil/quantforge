# @layer: unit
"""RSI买入过滤单元测试：验证 RSI>=60禁止买入，RSI<60允许买入。

T028发现RSI<60时买入信号胜率80.4%、盈亏+6.4%，RSI>=60禁止买入。
本测试用人工构造DataFrame验证_evaluate和_produce_singlefactor_decisions的正确性。
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np

from quantforge.core.data_feed import DataResponse
from quantforge.core.decision import DecisionType
from quantforge.indicators.technical import ROCIndicator, RSIIndicator, MAIndicator
from quantforge.strategies.roc_momentum import ROCStrategy
from quantforge.strategies._configs.roc_config import ROCConfig

# ============================================================
# 辅助：构造带ROC/MAROC/RSI的测试DataFrame
# ============================================================

ROCN = 22
ROCM = 8
RSIN = 14


def _make_df(close_series, rsi_series=None):
    """构造足够长的DataFrame（≥31行），确保ROC(22)+MAROC(8)有有效值。

    若rsi_series给出，将rsi列预先写入DataFrame；否则由RSIIndicator.compute自然产生。
    """
    rows = []
    for i, c in enumerate(close_series):
        row = {'close': float(c), 'date': f'2020-01-{i+1:02d}'}
        if rsi_series is not None:
            row['rsi'] = float(rsi_series[i])
        rows.append(row)
    df = pd.DataFrame(rows)
    df = ROCIndicator(n=ROCN, m=ROCM).compute(df, n=ROCN, m=ROCM)
    return df


def _strategy(rsi_enabled=False, rsi_below=60.0):
    cfg = ROCConfig(
        strategy_name="test",
        codes=["TEST01"],
        data_type="daily_k",
        start_date="2020-01-01",
        buy_roc_edge=15.0,
        sell_roc_edge=3.0,
        sell_ma_roc_edge=0.0,
        rsi_enhance_enabled=rsi_enabled,
        rsi_enhance_below=rsi_below,
    )
    s = ROCStrategy(cfg)
    # 单因子路径
    s._roc_indicator = ROCIndicator(n=ROCN, m=ROCM)
    s._ma_indicator = MAIndicator(periods=[22])
    if rsi_enabled:
        s._rsi_indicator = RSIIndicator(n=RSIN)
        s._config.rsi_period = RSIN
    return s


# ============================================================
# _evaluate 向买入信号测试
# ============================================================

def test_rsi_below_threshold_allows_buy():
    """RSI=45 < 60 → direction='enter'"""
    s = _strategy(rsi_enabled=True, rsi_below=60.0)
    ind = {'roc': 25.0, 'maroc': 20.0, 'prev_roc': 18.0, 'prev_maroc': 19.0,
           'close': 10.0, 'ma': 9.0, 'rsi': 45.0}
    direction, weight, reason = s._evaluate('TEST01', 25.0, ind, {})
    assert direction == 'enter', f"Expected enter, got {direction}, reason={reason}"
    assert 'RSI' not in reason, f"RSI should not appear in reason when allowed: {reason}"


def test_rsi_above_threshold_blocks_buy():
    """RSI=75 >= 60 → direction='hold'"""
    s = _strategy(rsi_enabled=True, rsi_below=60.0)
    ind = {'roc': 25.0, 'maroc': 20.0, 'prev_roc': 18.0, 'prev_maroc': 19.0,
           'close': 10.0, 'ma': 9.0, 'rsi': 75.0}
    direction, weight, reason = s._evaluate('TEST01', 25.0, ind, {})
    assert direction == 'hold', f"Expected hold, got {direction}, reason={reason}"
    assert '禁止买入' in reason, f"Reason should contain 禁止买入: {reason}"


def test_rsi_exactly_at_threshold_blocks_buy():
    """RSI=60 == threshold → direction='hold'"""
    s = _strategy(rsi_enabled=True, rsi_below=60.0)
    ind = {'roc': 25.0, 'maroc': 20.0, 'prev_roc': 18.0, 'prev_maroc': 19.0,
           'close': 10.0, 'ma': 9.0, 'rsi': 60.0}
    direction, weight, reason = s._evaluate('TEST01', 25.0, ind, {})
    assert direction == 'hold', f"Expected hold at threshold, got {direction}"


def test_rsi_filter_disabled_no_effect():
    """rsi_enhance_enabled=False → RSI=75 不影响买入"""
    s = _strategy(rsi_enabled=False)
    ind = {'roc': 25.0, 'maroc': 20.0, 'prev_roc': 18.0, 'prev_maroc': 19.0,
           'close': 10.0, 'ma': 9.0, 'rsi': 75.0}
    direction, weight, reason = s._evaluate('TEST01', 25.0, ind, {})
    assert direction == 'enter', f"Expected enter when filter off, got {direction}"


def test_rsi_missing_data_does_not_crash():
    """ind中没有'rsi'键 → 不触发过滤，正常买入"""
    s = _strategy(rsi_enabled=True, rsi_below=60.0)
    ind = {'roc': 25.0, 'maroc': 20.0, 'prev_roc': 18.0, 'prev_maroc': 19.0,
           'close': 10.0, 'ma': 9.0}
    direction, weight, reason = s._evaluate('TEST01', 25.0, ind, {})
    assert direction == 'enter', f"Expected enter when rsi missing, got {direction}"


# ============================================================
# 卖出逻辑不受RSI影响
# ============================================================

def test_rsi_filter_does_not_block_exit():
    """已持仓+ROC低于卖出阈值 → 即使RSI<60也要卖出"""
    s = _strategy(rsi_enabled=True, rsi_below=60.0)
    positions = {'TEST01': {'shares': 100, 'avg_cost': 5.0}}
    ind = {'roc': 1.0, 'maroc': 2.0, 'rsi': 30.0}
    direction, weight, reason = s._evaluate('TEST01', 1.0, ind, positions)
    assert direction == 'exit', f"Expected exit, got {direction}, reason={reason}"


def test_rsi_filter_does_not_block_maroc_exit():
    """已持仓+MAROC低于sell_ma_roc_edge → 即使RSI低也要卖出"""
    cfg = ROCConfig(
        strategy_name="test", codes=["TEST01"], data_type="daily_k",
        start_date="2020-01-01", buy_roc_edge=15.0, sell_roc_edge=3.0,
        sell_ma_roc_edge=5.0,
        rsi_enhance_enabled=True, rsi_enhance_below=60.0,
    )
    s = ROCStrategy(cfg)
    s._roc_indicator = ROCIndicator(n=ROCN, m=ROCM)
    s._ma_indicator = MAIndicator(periods=[22])
    s._rsi_indicator = RSIIndicator(n=RSIN)
    s._config.rsi_period = RSIN

    positions = {'TEST01': {'shares': 100, 'avg_cost': 5.0}}
    ind = {'roc': 8.0, 'maroc': 3.0, 'rsi': 25.0}
    direction, weight, reason = s._evaluate('TEST01', 8.0, ind, positions)
    assert direction == 'exit', f"Expected exit, got {direction}, reason={reason}"


# ============================================================
# 完整决策管线测试（_produce_singlefactor_decisions）
# ============================================================

def test_singlefactor_pipeline_rsi_filter():
    """完整管线：持续上涨 → 自然高RSI(>70) → 应被禁止买入"""
    n_rows = 35
    close_series = [10.0 + i * 0.3 for i in range(n_rows)]
    df = _make_df(close_series)
    data = DataResponse(bar_data={'TEST01': df}, macro_data={})

    s = _strategy(rsi_enabled=True, rsi_below=60.0)
    s._rsi_indicator = RSIIndicator(n=RSIN)
    s._config.rsi_period = RSIN

    decisions = s.produce_decisions(data, {})
    enter_decisions = [d for d in decisions if d.direction == 'enter']
    assert len(enter_decisions) == 0, \
        f"Expected 0 enter (RSI high), got {len(enter_decisions)}: {[d.reason for d in enter_decisions]}"


def test_singlefactor_pipeline_rsi_allow_no_crash():
    """完整管线：不崩溃即可。具体RSI值由真实数据决定，逻辑正确性由_evaluate单测保证。"""
    n_rows = 35
    close_series = [10.0] * (n_rows - 1) + [20.0]
    df = _make_df(close_series)
    data = DataResponse(bar_data={'TEST01': df}, macro_data={})

    s = _strategy(rsi_enabled=True, rsi_below=60.0)
    s._rsi_indicator = RSIIndicator(n=RSIN)
    s._config.rsi_period = RSIN

    decisions = s.produce_decisions(data, {})
    # 不崩溃就算通过
    assert isinstance(decisions, list), f"Expected list of decisions, got {type(decisions)}"


# ============================================================
# 集成：短回测对比（验证实际交易差异）
# ============================================================

def test_integration_short_backtest_diff():
    """3个月回测对比：RSI过滤前后的交易次数差异"""
    import pytest
    from quantforge.core.executor import BacktestExecutor
    from quantforge.core.resolver import RankingResolver
    from quantforge.strategies.factory import create_config, create_strategy

    preset = "tech_growth"
    start = "2025-01-01"
    end = "2025-03-31"

    cfg_b = create_config("roc_momentum", preset)
    codes = cfg_b.codes[:5]
    strategy_b = create_strategy("roc_momentum", preset)

    from quantforge.core.backtest_core import run_backtest
    from quantforge.core.data_feed import CachedDataFeed
    from quantforge.data_sources.sina_feed import SinaFinanceFeed

    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    feed = CachedDataFeed(
        source=SinaFinanceFeed(),
        cache_dir=os.path.join(BASE_DIR, "data", "sina"),
    )
    try:
        feed.update_cache(codes=codes, data_type="daily_k", start=start, end=end)
    except Exception as e:
        pytest.skip(f"网络不可用，跳过集成测试: {e}")

    resolver_b = RankingResolver(
        top_k=cfg_b.top_k,
        weight_method='signal_weight',
        top_k_sell=True,
    )
    exec_b = BacktestExecutor(initial_capital=cfg_b.initial_capital)
    result_b = run_backtest(strategy_b, resolver_b, exec_b, feed, codes, start, end)

    # RSI 过滤
    strategy_r = create_strategy("roc_momentum", preset)
    strategy_r._config.rsi_enhance_enabled = True
    strategy_r._config.rsi_enhance_below = 60.0
    from quantforge.indicators.technical import RSIIndicator
    strategy_r._rsi_indicator = RSIIndicator(n=14)
    strategy_r._config.rsi_period = 14

    resolver_r = RankingResolver(
        top_k=cfg_b.top_k,
        weight_method='signal_weight',
        top_k_sell=True,
    )
    exec_r = BacktestExecutor(initial_capital=cfg_b.initial_capital)
    result_r = run_backtest(strategy_r, resolver_r, exec_r, feed, codes, start, end)

    trades_b = len(result_b.get('trade_log', []))
    trades_r = len(result_r.get('trade_log', []))

    print(f"  基线交易次数: {trades_b}")
    print(f"  RSI过滤交易次数: {trades_r}")
    assert trades_r <= trades_b, \
        f"RSI过滤应减少(或等于)交易次数: baseline={trades_b}, filtered={trades_r}"
    print(f"  PASS: integration_backtest")


if __name__ == "__main__":
    test_rsi_below_threshold_allows_buy();   print("PASS test_rsi_below_threshold_allows_buy")
    test_rsi_above_threshold_blocks_buy();   print("PASS test_rsi_above_threshold_blocks_buy")
    test_rsi_exactly_at_threshold_blocks_buy(); print("PASS test_rsi_exactly_at_threshold")
    test_rsi_filter_disabled_no_effect();    print("PASS test_rsi_filter_disabled_no_effect")
    test_rsi_missing_data_does_not_crash();  print("PASS test_rsi_missing_data_does_not_crash")
    test_rsi_filter_does_not_block_exit();   print("PASS test_rsi_filter_does_not_block_exit")
    test_rsi_filter_does_not_block_maroc_exit(); print("PASS test_rsi_filter_does_not_block_maroc_exit")
    test_singlefactor_pipeline_rsi_filter(); print("PASS test_singlefactor_pipeline_rsi_filter")
    test_singlefactor_pipeline_rsi_allow_no_crash(); print("PASS test_singlefactor_pipeline_allow")
    test_integration_short_backtest_diff();  print("PASS test_integration_short_backtest_diff")
    print("\n所有测试通过")
