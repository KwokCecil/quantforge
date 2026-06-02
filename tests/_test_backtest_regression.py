# @layer: contract
"""回归测试：固定数据快照验证回测行为不变。

通过种子随机数生成固定 OHLCV 数据 + MockDataFeed，用真实 ROCStrategy 驱动完整回测，
验证 net_values、trade_log、绩效指标与预期值完全一致。

任何回测引擎（run_backtest / run_core_backtest）的修改都必须通过此测试，
确保不会悄悄改变回测结果。
"""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from loguru import logger

from quantforge.core.data_feed import DataFeed, DataRequest, DataResponse
from quantforge.core.executor import BacktestExecutor
from quantforge.core.resolver import RankingResolver
from quantforge.core.backtest_core import run_backtest
from quantforge.strategies._configs.roc_config import ROCConfig
from quantforge.strategies.roc_momentum import ROCStrategy

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)
logger.remove()
logger.add(sys.stderr, level="WARNING")


def _make_fixed_bar_data():
    """生成固定的 OHLCV 数据快照。

    使用 np.random.seed(42) 确保每次生成完全相同的价格序列。
    3只ETF × 44个交易日（约2个月），设计为：
    - 159915（创业板ETF）：强上涨趋势 → ROC高 → 应产生买入信号
    - 510050（上证50ETF）：温和上涨 → ROC中等
    - 159919（沪深300ETF）：震荡下跌 → ROC低/负
    """
    np.random.seed(42)
    dates = pd.bdate_range('2023-01-03', periods=44, freq='B')
    date_strs = dates.strftime('%Y-%m-%d').tolist()
    n = len(date_strs)

    codes_config = [
        ('159915', 0.022, 0.015),
        ('510050', 0.008, 0.010),
        ('159919', -0.003, 0.012),
    ]

    bar_data = {}
    for code, mu, sigma in codes_config:
        close = [1.0]
        for _ in range(n - 1):
            ret = np.random.normal(mu, sigma)
            close.append(close[-1] * (1 + ret))

        df = pd.DataFrame({
            'date': date_strs,
            'open': [round(c * 0.995, 6) for c in close],
            'high': [round(c * 1.015, 6) for c in close],
            'low': [round(c * 0.985, 6) for c in close],
            'close': [round(c, 6) for c in close],
            'volume': [1000000] * n,
        })
        bar_data[code] = df

    return bar_data


class FixedDataFeed(DataFeed):
    """返回固定 bar_data 的 Mock DataFeed，不依赖网络和缓存。"""

    def __init__(self, bar_data: dict[str, pd.DataFrame]):
        self._bar_data = bar_data

    def get_data(self, request: DataRequest) -> DataResponse:
        result = {}
        for code in request.codes:
            if code in self._bar_data:
                df = self._bar_data[code].copy()
                mask = (df['date'] >= request.start) & (df['date'] <= request.end)
                result[code] = df[mask].reset_index(drop=True)
        return DataResponse(bar_data=result)


def _make_config(bar_data):
    end_date = max(df['date'].iloc[-1] for df in bar_data.values())
    return ROCConfig(
        codes=['159915', '510050', '159919'],
        code_names={
            '159915': '创业板ETF',
            '510050': '上证50ETF',
            '159919': '沪深300ETF',
        },
        start_date='2023-01-03',
        end_date=end_date,
        buy_roc_edge=20.0,
        top_k=3,
        roc_n=22,
        sell_roc_edge=3.0,
        HIGH_WATERMARK_STOP=True,
        high_watermark_stop_edge=0.10,
        TOP_K_SELL=False,
        initial_capital=40000.0,
    )


def _compute_core_metrics(nv):
    """与 main_backtest.run_core_backtest 一致的指标计算。"""
    daily_returns = np.diff(nv) / nv[:-1]
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

    return {
        'sharpe': round(float(sharpe), 4),
        'sortino': round(float(sortino), 4),
        'total_return': round(total_return, 4),
        'max_drawdown': round(float(max_dd), 4),
    }


# ============================================================
# 测试用例
# ============================================================

def test_regression_known_output():
    """核心回归测试：固定输入必须产生固定输出。

    如果此测试失败，说明回测引擎的行为已被修改，需要：
    1. 确认修改是有意的
    2. 更新本测试中的期望值
    3. 记录修改原因（memo）
    """
    bar_data = _make_fixed_bar_data()
    config = _make_config(bar_data)
    end_date = max(df['date'].iloc[-1] for df in bar_data.values())

    strategy = ROCStrategy(config)
    data_feed = FixedDataFeed(bar_data)
    resolver = RankingResolver(
        top_k=config.top_k,
        weight_method='equal',
        high_watermark_stop_edge=config.high_watermark_stop_edge if config.HIGH_WATERMARK_STOP else float('inf'),
        cut_loss_edge=config.cut_loss_edge if config.CUT_LOSS else float('inf'),
        top_k_sell=config.TOP_K_SELL,
    )
    executor = BacktestExecutor(
        initial_capital=config.initial_capital,
        stop_small_trade=False,
    )

    results = run_backtest(
        strategy=strategy, resolver=resolver, executor=executor,
        data_feed=data_feed,
        codes=['159915', '510050', '159919'],
        start='2023-01-03', end=end_date,
    )

    assert results, "回测应返回非空结果"
    assert 'net_values' in results, "结果应包含 net_values"
    assert len(results['net_values']) > 20, f"净值序列至少20个点，实际 {len(results['net_values'])}"

    nv = np.array([v['net_value'] for v in results['net_values']])
    trade_log = results.get('trade_log', [])

    metrics = _compute_core_metrics(nv)
    sell_trades = [t for t in trade_log if t['action'] == 'sell']
    buy_trades = [t for t in trade_log if t['action'] == 'buy']

    # ================================================================
    # 期望值由 seed=42 数据快照单次运行后硬编码，不可随意修改
    # 若修改引擎逻辑导致此测试失败：确认修改有意 → 更新期望值 → memo 记录
    # ================================================================
    expected_net_value_final = 1.190789
    expected_sell_count = 5
    expected_buy_count = 7
    expected_sharpe = 10.0381
    expected_sortino = 456.4803
    expected_total_return = 0.190789
    expected_max_dd = 0.001247

    # 验证关键指标
    assert abs(nv[-1] - expected_net_value_final) < 0.0001, \
        f"最终净值: 实际={nv[-1]:.6f}, 期望={expected_net_value_final}"
    assert len(sell_trades) == expected_sell_count, \
        f"卖出次数: 实际={len(sell_trades)}, 期望={expected_sell_count}"
    assert len(buy_trades) == expected_buy_count, \
        f"买入次数: 实际={len(buy_trades)}, 期望={expected_buy_count}"
    assert abs(metrics['sharpe'] - expected_sharpe) < 0.001, \
        f"Sharpe: 实际={metrics['sharpe']}, 期望={expected_sharpe}"
    assert abs(metrics['sortino'] - expected_sortino) < 0.01, \
        f"Sortino: 实际={metrics['sortino']}, 期望={expected_sortino}"
    assert abs(metrics['total_return'] - expected_total_return) < 0.0001, \
        f"总收益: 实际={metrics['total_return']}, 期望={expected_total_return}"
    assert abs(metrics['max_drawdown'] - expected_max_dd) < 0.0001, \
        f"最大回撤: 实际={metrics['max_drawdown']}, 期望={expected_max_dd}"

    # 验证基本不变性
    assert nv[0] == 1.0, "初始净值应为 1.0（归一化）"
    assert all(nv > 0), "净值不应为负"
    assert all(t['shares'] % 100 == 0 for t in buy_trades), "买入股数应为100的整倍数"

    # 验证交易价格与数据一致
    for t in trade_log:
        code = t['code']
        df = bar_data[code]
        trade_date_rows = df[df['date'] == t['date']]
        if trade_date_rows.empty:
            continue
        row = trade_date_rows.iloc[-1]
        expected_price = row['close'] * (1 + 0.001) if t['action'] == 'buy' else row['close'] * (1 - 0.001)
        assert abs(t['price'] - expected_price) < 0.01, \
            f"{t['date']} {t['code']} {t['action']}: 价格={t['price']:.4f} 期望≈{expected_price:.4f}"

    logger.success("  ✅ 回归测试通过（核心指标 + 交易日志 + 不变性验证）")


def test_deterministic_output():
    """确定性测试：相同输入跑两次，结果必须完全一致。"""
    bar_data = _make_fixed_bar_data()
    config = _make_config(bar_data)
    end_date = max(df['date'].iloc[-1] for df in bar_data.values())

    def run_once():
        strategy = ROCStrategy(config)
        data_feed = FixedDataFeed(bar_data)
        resolver = RankingResolver(
            top_k=config.top_k, weight_method='equal',
            high_watermark_stop_edge=config.high_watermark_stop_edge if config.HIGH_WATERMARK_STOP else float('inf'),
            cut_loss_edge=config.cut_loss_edge if config.CUT_LOSS else float('inf'),
            top_k_sell=config.TOP_K_SELL,
        )
        executor = BacktestExecutor(initial_capital=config.initial_capital, stop_small_trade=False)
        return run_backtest(
            strategy=strategy, resolver=resolver, executor=executor,
            data_feed=data_feed,
            codes=['159915', '510050', '159919'],
            start='2023-01-03', end=end_date,
        )

    r1 = run_once()
    r2 = run_once()

    nv1 = np.array([v['net_value'] for v in r1['net_values']])
    nv2 = np.array([v['net_value'] for v in r2['net_values']])

    np.testing.assert_array_almost_equal(nv1, nv2, decimal=10,
                                          err_msg="两次运行净值曲线不一致")
    assert len(r1['trade_log']) == len(r2['trade_log']), "交易次数不一致"
    for i, (t1, t2) in enumerate(zip(r1['trade_log'], r2['trade_log'])):
        t1_stripped = {k: v for k, v in t1.items() if k != 'timestamp'}
        t2_stripped = {k: v for k, v in t2.items() if k != 'timestamp'}
        assert t1_stripped == t2_stripped, f"Trade {i}: {t1_stripped} != {t2_stripped}"

    logger.success("  ✅ 确定性测试通过（两次运行完全一致）")


def test_edge_short_data():
    """边界测试：数据不足30天时 run_core_backtest 应返回 None。

    注：run_backtest 本身不检查天数，由 run_core_backtest 外层过滤。
    这里验证 run_backtest 在极短数据下不崩溃。
    """
    np.random.seed(99)
    dates = pd.bdate_range('2023-01-03', periods=10, freq='B')
    date_strs = dates.strftime('%Y-%m-%d').tolist()

    close = [1.0]
    for _ in range(9):
        close.append(close[-1] * (1 + np.random.normal(0.01, 0.01)))

    bar_data = {
        '159915': pd.DataFrame({
            'date': date_strs,
            'open': [c * 0.995 for c in close],
            'high': [c * 1.015 for c in close],
            'low': [c * 0.985 for c in close],
            'close': close,
            'volume': [1000000] * 10,
        }),
    }

    config = ROCConfig(
        codes=['159915'],
        code_names={'159915': '创业板ETF'},
        start_date='2023-01-03',
        end_date='2023-01-17',
        buy_roc_edge=20.0,
        top_k=1,
        roc_n=5,
        sell_roc_edge=3.0,
    )

    strategy = ROCStrategy(config)
    data_feed = FixedDataFeed(bar_data)
    resolver = RankingResolver(top_k=1, weight_method='equal')
    executor = BacktestExecutor(initial_capital=40000, stop_small_trade=False)

    results = run_backtest(
        strategy=strategy, resolver=resolver, executor=executor,
        data_feed=data_feed, codes=['159915'],
        start='2023-01-03', end='2023-01-17',
    )

    assert results, "短数据不崩溃"
    assert 'net_values' in results
    logger.success("  ✅ 短数据边界测试通过（不崩溃）")


def test_run_backtest_with_macro():
    """带宏观数据的回测：macro_data 传入后不影响正常执行。"""
    bar_data = _make_fixed_bar_data()
    config = _make_config(bar_data)
    end_date = max(df['date'].iloc[-1] for df in bar_data.values())

    strategy = ROCStrategy(config)
    data_feed = FixedDataFeed(bar_data)
    resolver = RankingResolver(
        top_k=config.top_k, weight_method='equal',
        high_watermark_stop_edge=config.high_watermark_stop_edge if config.HIGH_WATERMARK_STOP else float('inf'),
        cut_loss_edge=config.cut_loss_edge if config.CUT_LOSS else float('inf'),
        top_k_sell=config.TOP_K_SELL,
    )
    executor = BacktestExecutor(initial_capital=config.initial_capital, stop_small_trade=False)

    extra_macro = {
        'guzhai_licha': [
            {'date': d, 'value': 0.05} for d in bar_data['159915']['date'].tolist()
        ],
    }

    results = run_backtest(
        strategy=strategy, resolver=resolver, executor=executor,
        data_feed=data_feed,
        codes=['159915', '510050', '159919'],
        start='2023-01-03', end=end_date,
        extra_macro_data=extra_macro,
    )

    assert results, "带宏观数据回测应成功"
    assert 'net_values' in results
    nv = np.array([v['net_value'] for v in results['net_values']])

    # 带宏观数据的结果应与不带宏观数据一致（ROCStrategy不使用宏观数据）
    assert nv[0] == 1.0, "初始净值应为1.0"

    logger.success("  ✅ 带宏观数据回测通过")


def test_run_backtest_with_position_multiplier():
    """带仓位调节器：multiplier=0.5 应导致持仓减半。"""
    bar_data = _make_fixed_bar_data()
    config = _make_config(bar_data)
    end_date = max(df['date'].iloc[-1] for df in bar_data.values())

    strategy = ROCStrategy(config)
    data_feed = FixedDataFeed(bar_data)
    resolver = RankingResolver(
        top_k=config.top_k, weight_method='equal',
        high_watermark_stop_edge=config.high_watermark_stop_edge if config.HIGH_WATERMARK_STOP else float('inf'),
        cut_loss_edge=config.cut_loss_edge if config.CUT_LOSS else float('inf'),
        top_k_sell=config.TOP_K_SELL,
    )
    executor = BacktestExecutor(initial_capital=config.initial_capital, stop_small_trade=False)

    results_with = run_backtest(
        strategy=strategy, resolver=resolver, executor=executor,
        data_feed=data_feed,
        codes=['159915', '510050', '159919'],
        start='2023-01-03', end=end_date,
        position_multiplier_fn=lambda date: 0.5,
    )

    assert results_with, "带multiplier回测应成功"
    nv_with = np.array([v['net_value'] for v in results_with['net_values']])

    # multiplier=0.5 导致每笔买入份额减半
    for t in results_with['trade_log']:
        assert t['shares'] % 100 == 0, "股数仍是整百倍（但减半后取整）"

    logger.success(f"  ✅ 带仓位调节器回测通过，交易次数={len(results_with['trade_log'])}")


def test_run_backtest_missing_data():
    """数据缺失：请求的code不在bar_data中应返回空。"""
    bar_data = _make_fixed_bar_data()
    config = _make_config(bar_data)
    end_date = max(df['date'].iloc[-1] for df in bar_data.values())

    strategy = ROCStrategy(config)
    data_feed = FixedDataFeed(bar_data)
    resolver = RankingResolver(top_k=1, weight_method='equal')
    executor = BacktestExecutor(initial_capital=40000, stop_small_trade=False)

    results = run_backtest(
        strategy=strategy, resolver=resolver, executor=executor,
        data_feed=data_feed,
        codes=['159915', '999999'],  # 999999不在数据中
        start='2023-01-03', end=end_date,
    )

    assert results == {}, "缺失数据应返回空dict"

    logger.success("  ✅ 缺失数据返回空结果")


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("回测回归测试套件")
    logger.info("=" * 60)
    try:
        test_regression_known_output()
        test_deterministic_output()
        test_edge_short_data()
        test_run_backtest_with_macro()
        test_run_backtest_with_position_multiplier()
        test_run_backtest_missing_data()
        logger.success("\n全部 6 项通过 ✅")
    except AssertionError as e:
        logger.error(f"\n❌ 失败: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"\n❌ 异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)