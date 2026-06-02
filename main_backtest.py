r"""回测入口脚本。组装 DataFeed + Strategy + Resolver + Executor，运行回测并输出分析报告。

运行方式：.\.venv\Scripts\python.exe main_backtest.py [--preset default]
         .\.venv\Scripts\python.exe main_backtest.py --preset conservative
前置条件：tokens/ 目录中已配置真实密钥文件（从 _templates/ 复制并填值）
"""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from loguru import logger

from quantforge.core.data_feed import CachedDataFeed, DataRequest
from quantforge.core.executor import BacktestExecutor
from quantforge.core.resolver import RankingResolver
from quantforge.core.style_rotator import StyleRotator, RotationScheduler
from quantforge.core.backtest_support import BacktestAnalyzer
from quantforge.core.backtest_core import run_backtest
from quantforge.core.data_feed import create_cached_feed
from quantforge.core.resolver import make_ranking_resolver
from quantforge.data_sources.sina_feed import SinaFinanceFeed
from quantforge.strategies.factory import create_strategy, create_config
from quantforge.strategies.roc_momentum import ROCStrategy
from quantforge.tools.log_format import format_no_exception, format_exception_chain_str
from quantforge.tools.time_utils import get_trading_dates

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 日志配置：INFO日志全量记录，ERROR日志含异常链追踪
os.makedirs(os.path.join(_BASE_DIR, 'logs'), exist_ok=True)
logger.remove()
logger.add(os.path.join(_BASE_DIR, 'logs', 'log.txt'), level='INFO',
           format=format_no_exception,
           encoding='utf-8', enqueue=True,
           rotation="1 MB", retention="6 months")
logger.add(os.path.join(_BASE_DIR, 'logs', 'log_error.txt'), level='ERROR',
           format=format_exception_chain_str,
           encoding='utf-8', enqueue=True,
           backtrace=True, diagnose=True)
logger.add(sys.stdout, level='INFO',
           format='<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}',
           enqueue=True)


def _make_weight_method(config):
    if config.inverse_vol_weight:
        return 'inverse_vol'
    elif config.BUY_AVERAGE:
        return 'equal'
    else:
        return 'signal_weight'


def run_core_backtest(config, skip_cache_refresh=False, commission_rate=None, slippage=None):
    """核心回测引擎。给定ROCConfig，运行完整回测并返回标准化指标。

    供研究脚本复用，确保与生产回测逻辑完全一致。
    内部处理：策略创建、缓存刷新、Resolver/Executor组装、回测执行、指标计算。

    Args:
        config: ROCConfig 配置对象
        skip_cache_refresh: 批量回测场景设为 True，由调用方统一刷新缓存后复用
        commission_rate: 佣金率（覆盖 BacktestExecutor 默认值万2.5）
        slippage: 滑点（覆盖 BacktestExecutor 默认值 0.1%）

    Returns:
        dict: {net_values, sharpe, sortino, total_return, max_drawdown,
               calmar, trade_count, total_commission, trade_log,
               benchmark_series, daily_returns}
        None if backtest failed
    """
    strategy = ROCStrategy(config)
    data_feed = create_cached_feed(SinaFinanceFeed, os.path.join(_BASE_DIR, 'data', 'sina'))

    resolver = make_ranking_resolver(config, _make_weight_method(config))
    executor_kwargs = dict(
        initial_capital=config.initial_capital, rebalance=config.REBALANCE,
        stop_small_trade=config.STOP_SMALL_TRADE, skip_small_trade_limit=config.skip_small_trade_limit,
    )
    if commission_rate is not None:
        executor_kwargs['commission_rate'] = commission_rate
    if slippage is not None:
        executor_kwargs['slippage'] = slippage
    executor = BacktestExecutor(**executor_kwargs)

    if not skip_cache_refresh:
        data_feed.update_cache(codes=config.codes, data_type=config.data_type, start=config.start_date, end=config.end_date)

    codes = list(config.codes)

    try:
        results = run_backtest(strategy=strategy, resolver=resolver, executor=executor,
                               data_feed=data_feed, codes=codes,
                               start=config.start_date, end=config.end_date,
                               benchmark_code=config.benchmark_code)
    except Exception as e:
        logger.warning(f"核心回测异常: {e}")
        return None

    if not results or not results.get('net_values') or len(results['net_values']) < 30:
        return None

    nv = np.array([v['net_value'] for v in results['net_values']])
    if nv[0] <= 0:
        return None

    trade_log = results.get('trade_log', [])
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
    sortino = np.mean(daily_returns) / np.std(neg_returns) * np.sqrt(252) if len(neg_returns) > 0 and np.std(neg_returns) > 0 else 0

    total_return = float(nv[-1] / nv[0] - 1)
    trade_count = len([t for t in trade_log if t['action'] == 'sell'])
    calmar = total_return / max_dd if max_dd > 0 else 0

    return {
        'net_values': nv,
        'sharpe': round(float(sharpe), 4),
        'sortino': round(float(sortino), 4),
        'total_return': round(total_return, 4),
        'max_drawdown': round(float(max_dd), 4),
        'calmar': round(float(calmar), 4),
        'trade_count': trade_count,
        'total_commission': round(float(executor.total_commission), 2),
        'trade_log': trade_log,
        'benchmark_series': results.get('benchmark_series'),
        'daily_returns': daily_returns,
        '_executor': executor,
    }


def _run_rotation_backtest(config):
    rotator = StyleRotator(config)
    cfg_agg = create_config("roc_momentum", config.sr_aggressive_preset)
    cfg_def = create_config("roc_momentum", config.sr_defensive_preset)

    strategy_agg = create_strategy("roc_momentum", config.sr_aggressive_preset)
    strategy_def = create_strategy("roc_momentum", config.sr_defensive_preset)

    all_codes = sorted(set(cfg_agg.codes + cfg_def.codes))
    logger.info(f"轮动模式: 进攻={config.sr_aggressive_preset}({len(cfg_agg.codes)}只) "
                f"防守={config.sr_defensive_preset}({len(cfg_def.codes)}只) "
                f"合并={len(all_codes)}只")

    data_feed = create_cached_feed(SinaFinanceFeed, os.path.join(_BASE_DIR, 'data', 'sina'))
    data_feed.update_cache(codes=all_codes, data_type=config.data_type, start=config.start_date, end=config.end_date)

    full_response = data_feed.get_data(DataRequest(codes=all_codes, data_type=config.data_type,
                                                    start=config.start_date, end=config.end_date))
    for code in all_codes:
        if code not in full_response.bar_data or full_response.bar_data[code].empty:
            logger.error(f"缺少数据: {code}")
            return None

    benchmark_raw = full_response.bar_data[config.sr_benchmark].copy()
    if benchmark_raw.empty:
        logger.error(f"基准 {config.sr_benchmark} 数据为空")
        return None
    benchmark_dates = benchmark_raw['date'].tolist()

    aligned = {}
    for code in all_codes:
        df = full_response.bar_data[code].copy()
        aligned_df = df.set_index('date').reindex(benchmark_dates)
        aligned_df = aligned_df.ffill().reset_index()
        for col in aligned_df.columns:
            if col != 'date' and aligned_df[col].isna().any():
                first_valid = aligned_df[col].first_valid_index()
                if first_valid is not None:
                    aligned_df.loc[:first_valid - 1, col] = aligned_df.loc[first_valid, col]
        aligned[code] = aligned_df

    benchmark_df = aligned[config.sr_benchmark]

    trading_dates = get_trading_dates(config.start_date, config.end_date)
    all_dates = set(benchmark_df['date'].tolist())
    trading_dates = [d for d in trading_dates if d in all_dates]

    resolver_agg = make_ranking_resolver(cfg_agg, _make_weight_method(cfg_agg))
    resolver_def = make_ranking_resolver(cfg_def, _make_weight_method(cfg_def))

    scheduler = RotationScheduler(
        rotator=rotator,
        strategy_agg=strategy_agg, resolver_agg=resolver_agg, codes_agg=cfg_agg.codes,
        strategy_def=strategy_def, resolver_def=resolver_def, codes_def=cfg_def.codes,
        benchmark_df=benchmark_df, trading_dates=trading_dates,
        preset_agg=config.sr_aggressive_preset, preset_def=config.sr_defensive_preset,
    )

    executor = BacktestExecutor(initial_capital=config.initial_capital, rebalance=config.REBALANCE,
                                 stop_small_trade=config.STOP_SMALL_TRADE,
                                 skip_small_trade_limit=config.skip_small_trade_limit)

    results = run_backtest(
        strategy=strategy_agg, resolver=resolver_agg, executor=executor,
        data_feed=data_feed, codes=all_codes,
        start=config.start_date, end=config.end_date,
        benchmark_code=config.benchmark_code,
        rotation_scheduler=scheduler,
        preloaded_bar_data=aligned,
    )

    if not results or not results.get('net_values'):
        logger.error("轮动回测失败")
        return None

    agg_count = sum(1 for v in scheduler.schedule.values() if v == config.sr_aggressive_preset)
    logger.info(f"轮动回测完成: 进攻天数={agg_count}/{len(trading_dates)}")

    return executor, results.get('benchmark_series')


def main(preset: str = "tech_growth"):
    config = create_config("roc_momentum", preset)

    if config.style_rotation_enabled:
        logger.info("启用风格轮动模式")
        return _main_rotation(preset)
    else:
        return _main_standard(config, preset)


def _main_rotation(preset: str):
    config = create_config("roc_momentum", preset)
    result = _run_rotation_backtest(config)

    if result:
        executor, benchmark_series = result
        analyzer = BacktestAnalyzer()
        benchmark_name = config.code_names.get(config.benchmark_code, config.benchmark_code)
        analysis = analyzer.analyze(
            executor,
            benchmark_series=benchmark_series,
            benchmark_name=benchmark_name,
            code_names=config.code_names,
            strategy_config=config,
            save_dir=os.path.join(_BASE_DIR, 'results'),
        )
        return analysis
    else:
        logger.error("轮动回测失败")
        return None


def _main_standard(config, preset: str):
    results = run_core_backtest(config)

    if results:
        executor = results['_executor']
        benchmark_series = results['benchmark_series']
        benchmark_name = config.code_names.get(config.benchmark_code, config.benchmark_code)
        analyzer = BacktestAnalyzer()
        analysis = analyzer.analyze(
            executor, benchmark_series=benchmark_series, benchmark_name=benchmark_name,
            code_names=config.code_names, strategy_config=config,
            save_dir=os.path.join(_BASE_DIR, 'results'),
        )
        return analysis
    else:
        logger.error("回测失败")
        return None


if __name__ == '__main__':
    preset = "tech_growth"
    core_mode = False
    if len(sys.argv) > 1:
        for arg in sys.argv[1:]:
            if arg.startswith("--preset="):
                preset = arg.split("=", 1)[1]
            elif arg == "--preset" and sys.argv.index(arg) + 1 < len(sys.argv):
                preset = sys.argv[sys.argv.index(arg) + 1]
            elif arg == "--core":
                core_mode = True

    if core_mode:
        config = create_config("roc_momentum", preset)
        logger.info(f"核心模式: preset={preset}, {config.start_date}~{config.end_date}")
        result = run_core_backtest(config)
        if result:
            # 只输出指标，不含内部对象
            output = {k: v for k, v in result.items()
                      if not k.startswith('_') and k not in ('net_values', 'trade_log', 'daily_returns', 'benchmark_series')}
            print(json.dumps(output, ensure_ascii=False, indent=2))
        else:
            print(json.dumps({"error": "回测失败"}, ensure_ascii=False))
    else:
        logger.info(f"使用预设配置: {preset}")
        main(preset)
