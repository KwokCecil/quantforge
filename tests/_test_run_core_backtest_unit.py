# @layer: unit
"""run_core_backtest 路径覆盖测试。

覆盖正常路径 + 所有失败分支（短路/零净值/异常/空结果），
验证指标计算结构完整性和过滤逻辑正确性。
所有测试通过 monkey-patch run_backtest 避免网络依赖。
"""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from unittest.mock import patch, MagicMock
from loguru import logger

from quantforge.main_backtest import run_core_backtest, _make_weight_method
from quantforge.strategies._configs.roc_config import ROCConfig

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)
logger.remove()
logger.add(sys.stderr, level="WARNING")


def _make_config(**overrides):
    defaults = dict(
        codes=['159915', '510050'],
        code_names={'159915': 'A', '510050': 'B'},
        start_date='2023-01-03',
        end_date='2023-03-31',
        buy_roc_edge=20.0,
        top_k=2,
        roc_n=22,
        sell_roc_edge=3.0,
        HIGH_WATERMARK_STOP=True,
        high_watermark_stop_edge=0.10,
        CUT_LOSS=True,
        cut_loss_edge=0.08,
        TOP_K_SELL=False,
        initial_capital=40000.0,
    )
    defaults.update(overrides)
    return ROCConfig(**defaults)


def _make_mock_results(n_days=50, noise_level=0.001):
    """构造模拟回测结果，带可控噪声。"""
    np.random.seed(123)
    returns = np.random.normal(0.0005, noise_level, n_days - 1)
    nv_raw = [1.0]
    for r in returns:
        nv_raw.append(nv_raw[-1] * (1 + r))

    net_values = [
        {'net_value': float(v), 'date': f'2023-{1+i//30:02d}-{1+i%30:02d}',
         'cash': 40000.0, 'total_value': 40000.0 * v}
        for i, v in enumerate(nv_raw)
    ]

    return {
        'net_values': net_values,
        'trade_log': [
            {'action': 'buy', 'date': '2023-02-01', 'code': '159915',
             'shares': 1000, 'price': 1.5, 'actual_price': 1.5015,
             'commission': 0.38, 'reason': 'ROC>20'},
            {'action': 'sell', 'date': '2023-02-15', 'code': '159915',
             'shares': 500, 'price': 1.6, 'actual_price': 1.5984,
             'commission': 0.40, 'reason': 'rebalance'},
        ],
        'total_commission': 0.78,
        'benchmark_series': None,
    }


# ============================================================
# 测试用例
# ============================================================

def test_run_core_normal_path():
    """正常路径：模拟回测成功，验证返回结构完整且指标合理。"""
    config = _make_config()
    mock_results = _make_mock_results(n_days=50, noise_level=0.008)

    with patch('quantforge.main_backtest.run_backtest', return_value=mock_results):
        result = run_core_backtest(config, skip_cache_refresh=True)

    assert result is not None, "正常路径应返回结果"
    assert 'net_values' in result
    assert 'sharpe' in result
    assert 'sortino' in result
    assert 'total_return' in result
    assert 'max_drawdown' in result
    assert 'calmar' in result
    assert 'trade_count' in result
    assert 'total_commission' in result
    assert 'trade_log' in result
    assert 'daily_returns' in result

    assert len(result['net_values']) == 50
    assert abs(result['total_commission'] - 0.0) < 0.01, \
        f"total_commission: {result['total_commission']}（mock 未执行 executor）"
    assert result['trade_count'] == 1  # 1条sell记录

    logger.success("  ✅ 正常路径：返回结构完整")


def test_run_core_short_data():
    """短路路径：< 30 天净值数据 → 返回 None。"""
    config = _make_config()
    mock_results = _make_mock_results(n_days=20)

    with patch('quantforge.main_backtest.run_backtest', return_value=mock_results):
        result = run_core_backtest(config, skip_cache_refresh=True)

    assert result is None, f"<30天应返回None, 实际={type(result)}"
    logger.success("  ✅ 短路：<30天 → None")


def test_run_core_zero_initial_nv():
    """零净值路径：nv[0] <= 0 → 返回 None。"""
    config = _make_config()
    mock_results = _make_mock_results(n_days=50)
    mock_results['net_values'][0]['net_value'] = 0.0

    with patch('quantforge.main_backtest.run_backtest', return_value=mock_results):
        result = run_core_backtest(config, skip_cache_refresh=True)

    assert result is None, "nv[0]==0 应返回None"
    logger.success("  ✅ 零净值：nv[0]=0 → None")


def test_run_core_negative_initial_nv():
    """负净值路径：nv[0] < 0 → 返回 None。"""
    config = _make_config()
    mock_results = _make_mock_results(n_days=50)
    mock_results['net_values'][0]['net_value'] = -1.0

    with patch('quantforge.main_backtest.run_backtest', return_value=mock_results):
        result = run_core_backtest(config, skip_cache_refresh=True)

    assert result is None, "nv[0]<0 应返回None"
    logger.success("  ✅ 负净值：nv[0]<0 → None")


def test_run_core_empty_results():
    """空结果路径：run_backtest 返回空 → None。"""
    config = _make_config()

    with patch('quantforge.main_backtest.run_backtest', return_value={}):
        result = run_core_backtest(config, skip_cache_refresh=True)

    assert result is None, "空结果应返回None"
    logger.success("  ✅ 空结果：{} → None")


def test_run_core_no_net_values():
    """无 net_values 路径：返回无 net_values 键 → None。"""
    config = _make_config()

    with patch('quantforge.main_backtest.run_backtest', return_value={'trade_log': []}):
        result = run_core_backtest(config, skip_cache_refresh=True)

    assert result is None, "无net_values应返回None"
    logger.success("  ✅ 无 net_values → None")


def test_run_core_exception():
    """异常路径：run_backtest 抛异常 → 返回 None。"""
    config = _make_config()

    with patch('quantforge.main_backtest.run_backtest', side_effect=RuntimeError("模拟异常")):
        result = run_core_backtest(config, skip_cache_refresh=True)

    assert result is None, "异常应返回None"
    logger.success("  ✅ 异常：RuntimeError → None")


def test_run_core_skip_cache_refresh():
    """skip_cache_refresh=True 时不调用 update_cache。"""
    config = _make_config()
    mock_results = _make_mock_results(n_days=50)

    with patch('quantforge.main_backtest.CachedDataFeed') as mock_feed_cls:
        mock_feed = MagicMock()
        mock_feed.get_data.return_value = MagicMock()
        mock_feed_cls.return_value = mock_feed

        with patch('quantforge.main_backtest.run_backtest', return_value=mock_results):
            run_core_backtest(config, skip_cache_refresh=True)

        mock_feed.update_cache.assert_not_called()

    logger.success("  ✅ skip_cache_refresh=True 未调用 update_cache")


def test_run_core_metric_computation():
    """指标计算验证：手工计算与 run_core_backtest 输出一致。"""
    config = _make_config()
    mock_results = _make_mock_results(n_days=50, noise_level=0.008)

    with patch('quantforge.main_backtest.run_backtest', return_value=mock_results):
        result = run_core_backtest(config, skip_cache_refresh=True)

    nv = np.array([v['net_value'] for v in mock_results['net_values']])
    daily_returns = np.diff(nv) / nv[:-1]

    # 手工计算每个指标（与 run_core_backtest 一致）
    sharpe = np.mean(daily_returns) / np.std(daily_returns) * np.sqrt(252) if np.std(daily_returns) > 0 else 0

    max_dd = 0.0
    peak = nv[0]
    for v in nv:
        if v > peak:
            peak = v
        dd = (peak - v) / peak
        if dd > max_dd:
            max_dd = dd

    neg_returns = daily_returns[daily_returns < 0]
    sortino = (np.mean(daily_returns) / np.std(neg_returns) * np.sqrt(252)
               if len(neg_returns) > 0 and np.std(neg_returns) > 0 else 0)

    total_return = float(nv[-1] / nv[0] - 1)
    trade_count = len([t for t in mock_results['trade_log'] if t['action'] == 'sell'])
    calmar = total_return / max_dd if max_dd > 0 else 0

    assert abs(result['sharpe'] - round(float(sharpe), 4)) < 0.001, \
        f"sharpe: {result['sharpe']} vs {sharpe:.4f}"
    assert abs(result['sortino'] - round(float(sortino), 4)) < 0.001, \
        f"sortino: {result['sortino']} vs {sortino:.4f}"
    assert abs(result['total_return'] - round(total_return, 4)) < 0.0001, \
        f"total_return: {result['total_return']} vs {total_return:.4f}"
    assert abs(result['max_drawdown'] - round(float(max_dd), 4)) < 0.0001, \
        f"max_dd: {result['max_drawdown']} vs {max_dd:.4f}"
    assert abs(result['calmar'] - round(float(calmar), 4)) < 0.0001, \
        f"calmar: {result['calmar']} vs {calmar:.4f}"
    assert result['trade_count'] == trade_count

    logger.success("  ✅ 指标计算与手工验算一致")


def test_make_weight_method():
    """_make_weight_method 辅助函数测试。"""
    config_equal = _make_config(inverse_vol_weight=False, BUY_AVERAGE=True)
    config_signal = _make_config(inverse_vol_weight=False, BUY_AVERAGE=False)
    config_inv_vol = _make_config(inverse_vol_weight=True, BUY_AVERAGE=False)

    assert _make_weight_method(config_equal) == 'equal'
    assert _make_weight_method(config_signal) == 'signal_weight'
    assert _make_weight_method(config_inv_vol) == 'inverse_vol'

    logger.success("  ✅ _make_weight_method 三种模式正确")


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("run_core_backtest 路径覆盖测试")
    logger.info("=" * 60)
    try:
        test_run_core_normal_path()
        test_run_core_short_data()
        test_run_core_zero_initial_nv()
        test_run_core_negative_initial_nv()
        test_run_core_empty_results()
        test_run_core_no_net_values()
        test_run_core_exception()
        test_run_core_skip_cache_refresh()
        test_run_core_metric_computation()
        test_make_weight_method()
        logger.success("\n全部 10 项通过 ✅")
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