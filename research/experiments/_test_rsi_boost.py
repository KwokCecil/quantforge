# @layer: e2e
"""T028 RSI增强 AB回测：对比 开启/关闭 RSI权重增强"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from datetime import datetime

from quantforge.core.data_feed import CachedDataFeed, DataRequest
from quantforge.data_sources.sina_feed import SinaFinanceFeed
from quantforge.core.backtest_core import run_backtest
from quantforge.core.executor import BacktestExecutor
from quantforge.core.resolver import RankingResolver
from quantforge.strategies.factory import create_config, create_strategy

PRESET = "tech_growth"
START = "2018-01-01"
END = "2026-04-30"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
feed = CachedDataFeed(source=SinaFinanceFeed(), cache_dir=os.path.join(BASE_DIR, "data", "sina"))

base_config = create_config("roc_momentum", PRESET)
codes = base_config.codes

feed.update_cache(codes=codes, data_type="daily_k", start=START, end=END)

# 基线（不开 RSI 增强）
strategy_base = create_strategy("roc_momentum", PRESET)
resolver_base = RankingResolver(
    top_k=base_config.top_k,
    weight_method=base_config.weight_method if hasattr(base_config, 'weight_method') else 'signal_weight',
    high_watermark_stop_edge=0.10 if base_config.HIGH_WATERMARK_STOP else float('inf'),
    cut_loss_edge=base_config.cut_loss_edge if base_config.CUT_LOSS else float('inf'),
    top_k_sell=True,
)
executor_base = BacktestExecutor(initial_capital=base_config.initial_capital)

print("=== 基线（RSI增强关闭）===")
result_base = run_backtest(
    strategy=strategy_base, resolver=resolver_base, executor=executor_base,
    data_feed=feed, codes=codes, start=START, end=END,
)

# RSI 增强
from quantforge.strategies._configs.roc_config import ROCConfig
from quantforge.strategies.roc_momentum import ROCStrategy

cfg = create_config("roc_momentum", PRESET)

strategy_rsi = create_strategy("roc_momentum", PRESET)
strategy_rsi._config.rsi_enhance_enabled = True
strategy_rsi._config.rsi_enhance_below = 60.0
from quantforge.indicators.technical import RSIIndicator
strategy_rsi._rsi_indicator = RSIIndicator(n=14)
strategy_rsi._config.rsi_period = 14

resolver_rsi = RankingResolver(
    top_k=base_config.top_k,
    weight_method='signal_weight',
    high_watermark_stop_edge=0.10 if base_config.HIGH_WATERMARK_STOP else float('inf'),
    cut_loss_edge=base_config.cut_loss_edge if base_config.CUT_LOSS else float('inf'),
    top_k_sell=True,
)
executor_rsi = BacktestExecutor(initial_capital=base_config.initial_capital)

print("\n=== RSI过滤（RSI>=60禁止买入）===")
result_rsi = run_backtest(
    strategy=strategy_rsi, resolver=resolver_rsi, executor=executor_rsi,
    data_feed=feed, codes=codes, start=START, end=END,
)

print("\n" + "=" * 60)
print("对比结果")
print("=" * 60)

for label, r in [("基线", result_base), ("RSI增强", result_rsi)]:
    print(f"{label}: 总收益={r.get('total_return',r.get('net_value',0)-1):.1%}  "
          f"年化={r.get('annual_return',0):.1%}  "
          f"Sharpe={r.get('sharpe',0):.2f}  回撤={r.get('max_drawdown',0):.1%}  "
          f"交易={r.get('total_trades',0)}")
