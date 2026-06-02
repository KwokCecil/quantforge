# @layer: e2e
"""T028 过滤开关回测：基线 vs RSI vs MACD vs 全套，产出净值曲线和绩效对比"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import copy
from loguru import logger

from quantforge.core.data_feed import CachedDataFeed, DataRequest
from quantforge.core.executor import BacktestExecutor
from quantforge.core.resolver import RankingResolver
from quantforge.data_sources.sina_feed import SinaFinanceFeed
from quantforge.core.backtest_core import run_backtest
from quantforge.core.backtest_support import BacktestAnalyzer
from quantforge.strategies.factory import create_config
from quantforge.strategies._configs.roc_config import ROCConfig

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAVE_BASE = os.path.join(_BASE_DIR, "results", "T028_filters")

# === 4 组配置 ===
PRESET = "tech_growth"
base_config = create_config("roc_momentum", PRESET)

groups = {
    "baseline": {
        "rsi_enhance_enabled": False,
        "macd_divergence_filter_enabled": False,
        "volume_filter_enabled": False,
        "atr_filter_enabled": False,
    },
    "rsi_only": {
        "rsi_enhance_enabled": True,
        "macd_divergence_filter_enabled": False,
        "volume_filter_enabled": False,
        "atr_filter_enabled": False,
    },
    "macd_only": {
        "rsi_enhance_enabled": False,
        "macd_divergence_filter_enabled": True,
        "volume_filter_enabled": False,
        "atr_filter_enabled": False,
    },
    "full": {
        "rsi_enhance_enabled": True,
        "macd_divergence_filter_enabled": True,
        "volume_filter_enabled": True,
        "atr_filter_enabled": True,
    },
}

# 日志静默到文件
logger.remove()
logger.add(sys.stdout, level='WARNING')

for name, overrides in groups.items():
    print(f"\n{'='*60}")
    print(f"回测: {name}")
    print(f"{'='*60}")

    cfg = ROCConfig(**{**base_config.to_dict(), **overrides})

    from quantforge.strategies.roc_momentum import ROCStrategy
    strategy = ROCStrategy(cfg)

    def _make_weight_method(config):
        if config.inverse_vol_weight:
            return 'inverse_vol'
        elif config.BUY_AVERAGE:
            return 'equal'
        else:
            return 'signal_weight'

    resolver = RankingResolver(
        top_k=cfg.top_k, weight_method=_make_weight_method(cfg),
        high_watermark_stop_edge=cfg.high_watermark_stop_edge if cfg.HIGH_WATERMARK_STOP else float('inf'),
        cut_loss_edge=cfg.cut_loss_edge if cfg.CUT_LOSS else float('inf'),
        top_k_sell=cfg.TOP_K_SELL,
    )
    executor = BacktestExecutor(
        initial_capital=cfg.initial_capital, rebalance=cfg.REBALANCE,
        stop_small_trade=cfg.STOP_SMALL_TRADE, skip_small_trade_limit=cfg.skip_small_trade_limit,
    )

    data_feed = CachedDataFeed(
        source=SinaFinanceFeed(),
        cache_dir=os.path.join(_BASE_DIR, 'data', 'sina'),
    )
    data_feed.update_cache(codes=cfg.codes, data_type=cfg.data_type, start=cfg.start_date, end=cfg.end_date)

    results = run_backtest(
        strategy=strategy, resolver=resolver, executor=executor,
        data_feed=data_feed, codes=cfg.codes,
        start=cfg.start_date, end=cfg.end_date,
        benchmark_code=cfg.benchmark_code,
    )

    if not results:
        print(f"  {name}: 回测失败")
        continue

    benchmark_series = results.get('benchmark_series')
    benchmark_name = cfg.code_names.get(cfg.benchmark_code, cfg.benchmark_code)
    analyzer = BacktestAnalyzer()
    save_dir = os.path.join(SAVE_BASE, name)
    analysis = analyzer.analyze(
        executor, benchmark_series=benchmark_series, benchmark_name=benchmark_name,
        code_names=cfg.code_names, strategy_config=cfg,
        save_dir=save_dir,
    )
    total_ret = analysis.get('total_return', 0)
    sharpe = analysis.get('sharpe_ratio', 0)
    max_dd = analysis.get('max_drawdown', 0)
    trade_cnt = analysis.get('trade_count', 0)
    print(f"  {name}: 总收益={total_ret:+.2%}  夏普={sharpe:.2f}  最大回撤={max_dd:.2%}  交易={trade_cnt}")

print(f"\n全部结果已保存至: {SAVE_BASE}")
