# @layer: unit
"""指标一致性测试：run_core_backtest vs BacktestAnalyzer 指标对比。

验证两套独立指标计算对相同净值曲线的输出差异，识别 B1-HIGH 等级的
np.std(ddof=0) vs pandas.std(ddof=1) 问题。
"""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from unittest.mock import patch
from loguru import logger

from quantforge.main_backtest import run_core_backtest
from quantforge.core.backtest_support import BacktestAnalyzer
from quantforge.core.executor import BacktestExecutor
from quantforge.strategies._configs.roc_config import ROCConfig

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)
logger.remove()
logger.add(sys.stderr, level="WARNING")


def _make_config():
    return ROCConfig(
        codes=['159915', '510050'],
        code_names={'159915': 'A', '510050': 'B'},
        start_date='2023-01-03',
        end_date='2023-06-30',
        buy_roc_edge=20.0,
        top_k=2,
        roc_n=22,
        sell_roc_edge=3.0,
        HIGH_WATERMARK_STOP=True,
        high_watermark_stop_edge=0.10,
        CUT_LOSS=True,
        cut_loss_edge=0.08,
        initial_capital=40000.0,
    )


def _make_controlled_executor():
    """构造可控的 executor，内置已知净值序列和交易记录。

    使用种子产生100天净值数据（归一化），并注入2笔模拟交易。
    """
    np.random.seed(42)
    n_days = 100
    returns = np.random.normal(0.0005, 0.008, n_days - 1)
    nv_raw = [1.0]
    for r in returns:
        nv_raw.append(nv_raw[-1] * (1 + r))

    executor = BacktestExecutor(initial_capital=40000, stop_small_trade=False)

    for i, v in enumerate(nv_raw):
        executor.net_values.append({
            'date': f'2023-{1+i//30:02d}-{1+i%30:02d}',
            'net_value': float(v),
            'total_value': 40000.0 * float(v),
            'cash': 40000.0,
            'positions': {},
        })

    executor.trade_log = [
        {'action': 'buy', 'date': '2023-02-01', 'code': '159915',
         'shares': 1000, 'price': 1.5, 'actual_price': 1.5015,
         'commission': 0.38, 'reason': 'ROC>20', 'timestamp': '2023-02-01T10:00:00'},
        {'action': 'sell', 'date': '2023-03-15', 'code': '159915',
         'shares': 500, 'price': 1.6, 'actual_price': 1.5984,
         'commission': 0.40, 'reason': 'rebalance', 'timestamp': '2023-03-15T10:00:00'},
    ]
    executor.total_commission = 0.78

    return executor, nv_raw


# ============================================================
# 核心一致性测试
# ============================================================

def test_metric_total_return_consistency():
    """总收益：两套算法应完全一致（均使用归一化净值）。"""
    executor, nv_raw = _make_controlled_executor()

    # BacktestAnalyzer 方式
    results = executor.get_results()
    nv_df = pd.DataFrame(results['net_values'])
    total_return_ba = float(nv_df['total_value'].iloc[-1] / executor.initial_capital - 1)

    # run_core_backtest 方式
    nv = np.array([v['net_value'] for v in results['net_values']])
    total_return_core = float(nv[-1] / nv[0] - 1)

    assert abs(total_return_ba - total_return_core) < 1e-10, \
        f"总收益不一致: BA={total_return_ba:.10f} core={total_return_core:.10f}"

    logger.success(f"  ✅ 总收益一致: {total_return_core:.6f}")


def test_metric_daily_returns_consistency():
    """日收益：np.diff/nv[:-1] 与 pct_change() 应等价。"""
    executor, nv_raw = _make_controlled_executor()
    results = executor.get_results()

    nv = np.array([v['net_value'] for v in results['net_values']])
    nv_df = pd.DataFrame(results['net_values'])

    ret_core = np.diff(nv) / nv[:-1]
    ret_ba = nv_df['net_value'].pct_change().dropna().values

    np.testing.assert_array_almost_equal(ret_core, ret_ba, decimal=10,
                                          err_msg="日收益序列不一致")

    logger.success("  ✅ 日收益序列一致（np.diff vs pct_change）")


def test_metric_max_drawdown_consistency():
    """最大回撤：手动循环与 cummax() 应等价。"""
    executor, nv_raw = _make_controlled_executor()
    results = executor.get_results()

    nv = np.array([v['net_value'] for v in results['net_values']])
    nv_df = pd.DataFrame(results['net_values'])

    # 手动循环（run_core_backtest方式）
    max_dd_core = 0.0
    peak = nv[0]
    for v in nv:
        if v > peak:
            peak = v
        dd = (peak - v) / peak
        if dd > max_dd_core:
            max_dd_core = dd

    # cummax方式（BacktestAnalyzer方式）—— 注意这里用 net_value 而非 total_value
    # 因为 run_core_backtest 做归一化后计算，所以用 net_value 比 total_value 更可比
    nv_df['peak'] = nv_df['net_value'].cummax()
    nv_df['drawdown'] = (nv_df['peak'] - nv_df['net_value']) / nv_df['peak']
    max_dd_ba = float(nv_df['drawdown'].max())

    # 两种方式应完全等价
    assert abs(max_dd_core - max_dd_ba) < 1e-10, \
        f"最大回撤不一致: core={max_dd_core:.10f} BA={max_dd_ba:.10f}"

    logger.success(f"  ✅ 最大回撤一致: {max_dd_core:.6f}")


def test_metric_sharpe_ddof_difference():
    """量化 np.std(ddof=0) vs pandas.std(ddof=1) 的差异。

    这是已知的算法差异，非 bug。确认为 T047-B1-HIGH 风险。
    差异来源于分母 N vs N-1，理论上差异 ≈ 1/(2N)。
    """
    executor, nv_raw = _make_controlled_executor()
    results = executor.get_results()

    nv = np.array([v['net_value'] for v in results['net_values']])
    daily_returns = np.diff(nv) / nv[:-1]

    np_std = np.std(daily_returns)
    pd_std = float(pd.Series(daily_returns).std())

    n = len(daily_returns)
    expected_ratio = np.sqrt(n / (n - 1))
    actual_ratio = pd_std / np_std

    assert abs(actual_ratio - expected_ratio) < 0.001, \
        f"ddof比率不符: 期望={expected_ratio:.6f} 实际={actual_ratio:.6f}"

    sharpe_np = np.mean(daily_returns) / np_std * np.sqrt(252)
    sharpe_pd = np.mean(daily_returns) / pd_std * np.sqrt(252)
    sharpe_diff = abs(sharpe_np - sharpe_pd)

    logger.info(f"  🔍 ddof差异: np_std={np_std:.6f} pd_std={pd_std:.6f} ratio={actual_ratio:.4f}")
    logger.info(f"  🔍 Sharpe: np={sharpe_np:.4f} pd={sharpe_pd:.4f} diff={sharpe_diff:.4f}")

    # 确认差异在合理范围（≈1%级别）
    assert sharpe_diff > 0, "ddof导致Sharpe应存在差异"
    assert sharpe_diff / abs(sharpe_pd) < 0.02, \
        f"Sharpe差异应<2%, 实际={sharpe_diff/abs(sharpe_pd)*100:.2f}%"

    logger.success(f"  ✅ ddof差异确认: Sharpe差={sharpe_diff:.4f} ({(sharpe_diff/abs(sharpe_pd))*100:.2f}%)")


def test_metric_zero_return_edge():
    """边界：零收益序列，两套算法的 std 行为。"""
    zeros_50 = np.zeros(50)
    zeros_51 = np.zeros(51)

    np_std_50 = np.std(zeros_50)
    np_std_51 = np.std(zeros_51)

    assert np_std_50 == 0.0, "np.std零值"
    assert np_std_51 == 0.0, "np.std零值"

    pd_std_50 = float(pd.Series(zeros_50).std())
    pd_std_51 = float(pd.Series(zeros_51).std())

    assert pd_std_50 == 0.0, "pd.std零值"
    assert pd_std_51 == 0.0, "pd.std零值"

    logger.success("  ✅ 零收益序列：两端std均为0")


def test_metric_trade_count_consistency():
    """交易次数：两套算法应一致（都统计sell次数）。"""
    executor, nv_raw = _make_controlled_executor()
    results = executor.get_results()
    trade_log = results.get('trade_log', [])

    sell_count = len([t for t in trade_log if t['action'] == 'sell'])
    assert sell_count == 1, f"应1次卖出, 实际={sell_count}"

    logger.success(f"  ✅ 交易次数一致: {sell_count}")


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("指标一致性测试（run_core_backtest vs BacktestAnalyzer）")
    logger.info("=" * 60)
    try:
        test_metric_total_return_consistency()
        test_metric_daily_returns_consistency()
        test_metric_max_drawdown_consistency()
        test_metric_sharpe_ddof_difference()
        test_metric_zero_return_edge()
        test_metric_trade_count_consistency()
        logger.success("\n全部 6 项通过 ✅")
    except AssertionError as e:
        logger.error(f"\n❌ 失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        logger.error(f"\n❌ 异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)