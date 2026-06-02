# @layer: integration
"""T005 ROC多因子增强验证 —— 波动率加权 + 多因子 vs Baseline"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import numpy as np
import pandas as pd
from loguru import logger

from quantforge.core.data_feed import CachedDataFeed, DataRequest, DataResponse
from quantforge.core.executor import BacktestExecutor
from quantforge.core.resolver import RankingResolver
from quantforge.data_sources.sina_feed import SinaFinanceFeed
from quantforge.indicators.technical import VolatilityIndicator
from quantforge.strategies._configs.roc_config import ROCConfig
from quantforge.strategies.roc_momentum import ROCStrategy
from quantforge.tools.time_utils import get_trading_dates

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)
logger.remove()
logger.add(sys.stderr, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}")


def run_backtest(config: ROCConfig) -> dict:
    """运行一次回测，返回关键指标。根据 config 选择单因子或多因子模式。"""
    feed = CachedDataFeed(source=SinaFinanceFeed(), cache_dir=os.path.join(BASE_DIR, "data", "sina"))
    strategy = ROCStrategy(config)
    resolver = RankingResolver(
        top_k=config.top_k,
        weight_method='equal' if config.BUY_AVERAGE else
                      ('inverse_vol' if config.inverse_vol_weight else 'signal_weight'),
        high_watermark_stop_edge=config.high_watermark_stop_edge if config.HIGH_WATERMARK_STOP else float('inf'),
        cut_loss_edge=config.cut_loss_edge if config.CUT_LOSS else float('inf'),
        top_k_sell=config.TOP_K_SELL,
    )
    executor = BacktestExecutor(initial_capital=config.initial_capital)

    # 加载数据
    stock_response = feed.get_data(DataRequest(
        codes=config.codes, data_type=config.data_type,
        start=config.start_date, end=config.end_date,
    ))

    code_first_valid_count = [
        len(stock_response.bar_data.get(c, pd.DataFrame()))
        for c in config.codes
    ]

    # 回测循环
    trading_dates = get_trading_dates(config.start_date, config.end_date)
    all_codes_dates = set()
    for df in stock_response.bar_data.values():
        if not df.empty:
            all_codes_dates.update(df['date'].tolist())
    trading_dates = [d for d in trading_dates if d in all_codes_dates]

    logger.info(f"  [{config.strategy_name}] {len(trading_dates)} 个交易日")

    for i, date in enumerate(trading_dates):
        date_bar = {}
        for code in config.codes:
            df = stock_response.bar_data.get(code)
            if df is None or df.empty:
                continue
            mask = df['date'] <= date
            date_bar[code] = df[mask].reset_index(drop=True)

        date_response = DataResponse(bar_data=date_bar)
        decisions = strategy.produce_decisions(date_response, executor.get_positions())
        targets = resolver.resolve(decisions, executor.get_positions(), executor.available_capital(), date_response)
        executor.execute(targets, date_response)

    results = executor.get_results()
    nv = results.get('net_values', [])
    trade_log = results.get('trade_log', [])

    if not nv:
        return {'total_return': 0, 'sharpe': 0, 'max_dd': 0, 'trades': 0, 'valid': False}

    nv_df = pd.DataFrame(nv)
    total_return = (nv_df['total_value'].iloc[-1] / executor.initial_capital) - 1
    years = len(nv_df) / 252
    annual_return = (1 + total_return) ** (1 / max(years, 0.01)) - 1

    nv_df['peak'] = nv_df['total_value'].cummax()
    nv_df['dd'] = (nv_df['peak'] - nv_df['total_value']) / nv_df['peak']
    max_dd = nv_df['dd'].max()

    daily_returns = nv_df['net_value'].pct_change().dropna()
    sharpe = (daily_returns.mean() / daily_returns.std() * np.sqrt(252)) if len(daily_returns) > 0 and daily_returns.std() > 0 else 0

    return {
        'total_return': round(total_return * 100, 2),
        'annual_return': round(annual_return * 100, 2),
        'sharpe': round(float(sharpe), 2),
        'max_dd': round(float(max_dd) * 100, 2),
        'trades': len(trade_log),
        'valid': True,
    }


def test_volatility_indicator():
    """验证 VolatilityIndicator 在合成数据上正常工作。"""
    logger.info("=== 测试 VolatilityIndicator ===")
    ind = VolatilityIndicator(n=10)

    data = pd.DataFrame({
        'date': pd.date_range('2025-01-01', periods=30, freq='B'),
        'close': np.linspace(1.0, 1.1, 30) + np.random.randn(30) * 0.01,
    })
    result = ind.compute(data, n=10)

    assert 'volatility' in result.columns, "缺少 volatility 列"
    assert not result['volatility'].tail(5).isna().all(), "最近几行波动率不应全为NaN"
    logger.success(f"VolatilityIndicator OK: 最新波动率={result['volatility'].iloc[-1]:.4f}")


def test_all_configs():
    """对比 Baseline / InverseVol / MultiFactor / MultiFactor+InvVol。"""
    logger.info("=" * 50)
    logger.info("T005 ROC 多因子增强 —— 配置对比回测")

    common_codes = ['510300', '159915', '588000', '513050', '159819', '512690', '515170']

    configs = [
        ("Baseline", ROCConfig(
            strategy_name="baseline",
            codes=common_codes,
            start_date="2023-08-01", end_date="2025-06-30",
            buy_roc_edge=18.0, roc_n=15, roc_m=8,
            TOP_K_SELL=False, HIGH_WATERMARK_STOP=True, CUT_LOSS=False,
            multi_factor=False, inverse_vol_weight=False,
        )),
        ("InvVol", ROCConfig(
            strategy_name="invvol",
            codes=common_codes,
            start_date="2023-08-01", end_date="2025-06-30",
            buy_roc_edge=18.0, roc_n=15, roc_m=8,
            TOP_K_SELL=False, HIGH_WATERMARK_STOP=True, CUT_LOSS=False,
            multi_factor=False, inverse_vol_weight=True,
        )),
        ("MultiFactor", ROCConfig(
            strategy_name="multifactor_roc",
            codes=common_codes,
            start_date="2023-08-01", end_date="2025-06-30",
            buy_roc_edge=18.0, roc_n=15, roc_m=8,
            TOP_K_SELL=False, HIGH_WATERMARK_STOP=True, CUT_LOSS=False,
            multi_factor=True, multi_roc_periods=(5, 15, 22),
            multi_factor_weights=(0.3, 0.4, 0.3),
            inverse_vol_weight=False,
        )),
        ("Multi+InvVol", ROCConfig(
            strategy_name="multi_invvol",
            codes=common_codes,
            start_date="2023-08-01", end_date="2025-06-30",
            buy_roc_edge=18.0, roc_n=15, roc_m=8,
            TOP_K_SELL=False, HIGH_WATERMARK_STOP=True, CUT_LOSS=False,
            multi_factor=True, multi_roc_periods=(5, 15, 22),
            multi_factor_weights=(0.3, 0.4, 0.3),
            inverse_vol_weight=True,
        )),
    ]

    results = []
    for name, config in configs:
        logger.info(f"--- 运行 {name} ---")
        try:
            m = run_backtest(config)
            m['name'] = name
            results.append(m)
            logger.info(f"  收益={m['total_return']}%, Sharpe={m['sharpe']}, DD={m['max_dd']}%, 交易={m['trades']}")
        except Exception as e:
            logger.error(f"  {name} 失败: {e}")
            results.append({'name': name, 'valid': False, 'error': str(e)})

    # 对比报告
    logger.info("=" * 50)
    logger.info("对比汇总")
    logger.info(f"{'配置':<15} {'收益率':>8} {'Sharpe':>7} {'回撤':>7} {'交易':>5}")
    logger.info("-" * 50)
    for r in results:
        if r.get('valid'):
            logger.info(f"{r['name']:<15} {r['total_return']:>7.1f}% {r['sharpe']:>6.2f} {r['max_dd']:>6.1f}% {r['trades']:>5}")

    # 找出最优
    valid = [r for r in results if r.get('valid')]
    if valid:
        best = max(valid, key=lambda r: r['sharpe'])
        logger.success(f"最优配置: {best['name']} (Sharpe={best['sharpe']})")

    logger.success("\nT005 ROC 多因子增强验证完成")


if __name__ == "__main__":
    test_volatility_indicator()
    test_all_configs()
