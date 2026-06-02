# @layer: research
"""Bootstrap验证 —— 全周期 (2018-2026)

对当前最优参数组合进行500次 Block Bootstrap 重采样，
检验 P(Sharpe>0) 是否达到 95% 置信水平。

用法：
    $env:PYTHONPATH="e:\JuJu\TraeProjects\量化工程"
    .\.venv\Scripts\python.exe research\_bootstrap_long_period.py
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from loguru import logger

from quantforge.core.data_feed import CachedDataFeed
from quantforge.data_sources.sina_feed import SinaFinanceFeed
from quantforge.strategies._configs.roc_config import ROCConfig
from quantforge.strategies.roc_momentum import ROCStrategy
from quantforge.core.resolver import RankingResolver
from quantforge.core.executor import BacktestExecutor
from quantforge.core.backtest_core import run_backtest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data", "sina")
OUTPUT_DIR = os.path.join(BASE_DIR, "临时文件")

START_DATE = "2018-01-01"
END_DATE = None  # 到今天

# tech_growth.json 的代码池 (33只科技/成长ETF)
CODES = [
    "515880", "159245", "159839", "512690", "159851", "515170",
    "159915", "510300", "588000", "159531", "501021",
    "513050", "159813", "159770", "159819", "516520",
    "159993", "501089", "159996", "513060", "159899",
    "516780", "516020",
    "159922", "512100", "513970", "515950",
    "159824", "561910", "159840", "515790", "516160", "159731",
]


def _make_config(start_date, end_date, **overrides):
    """使用当前最优参数构造配置。"""
    defaults = {
        "start_date": start_date,
        "end_date": end_date,
        "data_type": "daily_k",
        "benchmark_code": "399006",
        "initial_capital": 40000.0,
        # 最优参数
        "roc_n": 22,
        "roc_m": 8,
        "buy_roc_edge": 20.0,
        "sell_roc_edge": 3.0,
        "sell_ma_roc_edge": 0.0,
        "top_k": 5,
        # 开关（当前配置）
        "HIGH_WATERMARK_STOP": True,
        "high_watermark_stop_edge": 0.1,
        "CUT_LOSS": True,
        "cut_loss_edge": 0.08,
        "STOP_SMALL_TRADE": True,
        "skip_small_trade_limit": 2000.0,
        "REBALANCE": False,
        "BUY_AVERAGE": False,
        "STRICT_BUY": False,
        "MA_PRICE_CROSS": False,
        "ROC_MA_DIRECTION": False,
        "CROWDED_SELL": False,
        # 增强功能
        "inverse_vol_weight": True,
        "ma_period": 22,
        "ts_momentum_enabled": False,
        "rsi_enhance_enabled": True,
        "rsi_enhance_below": 60.0,
        "rsi_period": 14,
        "macd_divergence_filter_enabled": False,
        "volume_filter_enabled": False,
        "atr_filter_enabled": False,
        "atr_expansion_filter_enabled": True,
        "adx_trend_filter_enabled": True,
        "volume_sell_enabled": False,
        "atr_expansion_sell_enabled": False,
        "macd_divergence_sell_enabled": False,
        "rsi_sell_enabled": False,
        "codes": CODES,
    }
    defaults.update(overrides)
    return ROCConfig(**defaults)


def run_bootstrap():
    logger.info("=" * 70)
    logger.info("Bootstrap验证 —— 全周期 (2018-01-01 ~ 今)")
    logger.info("=" * 70)

    # 1. 运行完整回测获取日收益率序列
    logger.info("\n[1/3] 运行全周期回测...")
    config = _make_config(START_DATE, END_DATE)
    data_feed = CachedDataFeed(source=SinaFinanceFeed(), cache_dir=DATA_DIR)
    strategy = ROCStrategy(config)
    # 权重方法：与 main_backtest.py _make_weight_method 保持一致
    if config.inverse_vol_weight:
        weight_method = "inverse_vol"
    elif config.BUY_AVERAGE:
        weight_method = "equal"
    else:
        weight_method = "signal_weight"

    resolver = RankingResolver(
        top_k=config.top_k,
        weight_method=weight_method,
        high_watermark_stop_edge=config.high_watermark_stop_edge if config.HIGH_WATERMARK_STOP else float("inf"),
        cut_loss_edge=config.cut_loss_edge if config.CUT_LOSS else float("inf"),
        top_k_sell=config.TOP_K_SELL,
    )
    executor = BacktestExecutor(
        initial_capital=config.initial_capital,
        rebalance=config.REBALANCE,
        stop_small_trade=config.STOP_SMALL_TRADE,
        skip_small_trade_limit=config.skip_small_trade_limit,
    )
    result = run_backtest(
        strategy=strategy, resolver=resolver, executor=executor, data_feed=data_feed,
        codes=config.codes, start=config.start_date, end=config.end_date,
        benchmark_code=config.benchmark_code,
    )

    if not result or not result.get("net_values") or len(result["net_values"]) < 30:
        logger.error("回测数据不足，无法进行 Bootstrap 验证")
        return

    nv = np.array([v["net_value"] for v in result["net_values"]])
    daily_returns = np.diff(nv) / nv[:-1]
    n_days = len(daily_returns)

    # 计算原始指标
    original_sharpe = np.mean(daily_returns) / np.std(daily_returns) * np.sqrt(252) if np.std(daily_returns) > 0 else 0
    peak = nv[0]
    max_dd = 0
    for v in nv:
        if v > peak:
            peak = v
        dd = (peak - v) / peak
        if dd > max_dd:
            max_dd = dd
    total_return = nv[-1] / nv[0] - 1
    calmar = total_return / max_dd if max_dd > 0 else 0
    years = n_days / 252
    annual_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0

    logger.info(f"  回测天数: {n_days} ({years:.1f}年)")
    logger.info(f"  年化收益: {annual_return:.2%}")
    logger.info(f"  原始Sharpe: {original_sharpe:.4f}")
    logger.info(f"  总收益率: {total_return:.2%}")
    logger.info(f"  最大回撤: {max_dd:.2%}")
    logger.info(f"  Calmar: {calmar:.4f}")

    # 2. Block Bootstrap (500次)
    logger.info(f"\n[2/3] Block Bootstrap 500次...")
    n_bootstrap = 500
    bootstrap_sharpes = []
    block_size = max(5, n_days // 20)

    for i in range(n_bootstrap):
        indices = []
        while len(indices) < n_days:
            start_idx = np.random.randint(0, max(1, n_days - block_size + 1))
            indices.extend(range(start_idx, min(start_idx + block_size, n_days)))
        indices = indices[:n_days]
        sample_returns = daily_returns[indices]
        if np.std(sample_returns) > 0:
            bs_sharpe = np.mean(sample_returns) / np.std(sample_returns) * np.sqrt(252)
            bootstrap_sharpes.append(bs_sharpe)

    bootstrap_sharpes = np.array(bootstrap_sharpes)
    ci_lower = np.percentile(bootstrap_sharpes, 2.5)
    ci_upper = np.percentile(bootstrap_sharpes, 97.5)
    p_positive = np.mean(bootstrap_sharpes > 0)
    p_above_half = np.mean(bootstrap_sharpes > 0.5)
    p_above_one = np.mean(bootstrap_sharpes > 1.0)
    is_significant = bool(ci_lower > 0 and p_positive > 0.95)

    # 3. 输出结果
    logger.info(f"\n[3/3] Bootstrap 结果")
    logger.info(f"  Bootstrap Sharpe 均值: {np.mean(bootstrap_sharpes):.4f}")
    logger.info(f"  Bootstrap Sharpe 中位数: {np.median(bootstrap_sharpes):.4f}")
    logger.info(f"  Bootstrap Sharpe 标准差: {np.std(bootstrap_sharpes):.4f}")
    logger.info(f"  95% 置信区间: [{ci_lower:.4f}, {ci_upper:.4f}]")
    logger.info(f"  P(Sharpe > 0):    {p_positive:.2%}")
    logger.info(f"  P(Sharpe > 0.5):  {p_above_half:.2%}")
    logger.info(f"  P(Sharpe > 1.0):  {p_above_one:.2%}")
    logger.info(f"  统计显著: {'是 ✓' if is_significant else '否 ✗'} (需 CI下限>0 且 P(>0)>95%)")

    # 保存结果
    output = {
        "config": {
            "start_date": START_DATE,
            "end_date": "today",
            "codes_count": len(CODES),
            "params": {"roc_n": 22, "roc_m": 8, "buy_roc_edge": 20.0, "sell_ma_roc_edge": 0.0, "top_k": 5},
        },
        "backtest": {
            "n_days": int(n_days),
            "years": round(years, 1),
            "annual_return": round(float(annual_return), 4),
            "original_sharpe": round(float(original_sharpe), 4),
            "total_return": round(float(total_return), 4),
            "max_drawdown": round(float(max_dd), 4),
            "calmar": round(float(calmar), 4),
        },
        "bootstrap": {
            "n_bootstrap": n_bootstrap,
            "block_size": block_size,
            "mean_sharpe": round(float(np.mean(bootstrap_sharpes)), 4),
            "median_sharpe": round(float(np.median(bootstrap_sharpes)), 4),
            "std_sharpe": round(float(np.std(bootstrap_sharpes)), 4),
            "ci_lower": round(float(ci_lower), 4),
            "ci_upper": round(float(ci_upper), 4),
            "p_positive": round(float(p_positive), 4),
            "p_above_0_5": round(float(p_above_half), 4),
            "p_above_1_0": round(float(p_above_one), 4),
            "is_significant": is_significant,
        },
    }

    outpath = os.path.join(OUTPUT_DIR, "bootstrap_long_period_results.json")
    with open(outpath, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    logger.info(f"\n结果已保存: {outpath}")

    return output


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}")
    run_bootstrap()