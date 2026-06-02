"""参数优化工具：敏感度分析 + 网格搜索 + Walk-Forward验证

用法：
    $env:PYTHONPATH="e:\JuJu\TraeProjects\量化工程"
    .\.venv\Scripts\python.exe research\param_optimizer.py
"""

import sys
import os
import json
import itertools
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from loguru import logger

from quantforge.core.data_feed import CachedDataFeed, DataRequest
from quantforge.data_sources.sina_feed import SinaFinanceFeed
from quantforge.strategies._configs.roc_config import ROCConfig
from quantforge.strategies.roc_momentum import ROCStrategy
from quantforge.core.resolver import RankingResolver
from quantforge.core.executor import BacktestExecutor
from quantforge.core.backtest_core import run_backtest
from quantforge.core.backtest_support import BacktestAnalyzer

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '临时文件')
os.makedirs(OUTPUT_DIR, exist_ok=True)


def _run_backtest(config: ROCConfig) -> dict:
    data_feed = CachedDataFeed(
        source=SinaFinanceFeed(),
        cache_dir=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'sina')
    )
    request = DataRequest(
        codes=config.codes,
        data_type=config.data_type,
        start=config.start_date,
        end=config.end_date,
    )
    response = data_feed.get_data(request)

    strategy = ROCStrategy(config)
    resolver = RankingResolver(
        top_k=config.top_k,
        weight_method='equal' if config.BUY_AVERAGE else 'signal_weight',
        high_watermark_stop_edge=config.high_watermark_stop_edge,
        cut_loss_edge=config.cut_loss_edge,
    )
    executor = BacktestExecutor(
        initial_capital=config.initial_capital,
        rebalance=config.REBALANCE,
        stop_small_trade=config.STOP_SMALL_TRADE,
        skip_small_trade_limit=config.skip_small_trade_limit,
    )
    engine = BacktestEngine(strategy, resolver, executor, data_feed)
    result = engine.run(config)

    if result.net_values:
        nv = np.array([v['net_value'] for v in result.net_values])
        daily_returns = np.diff(nv) / nv[:-1]
        sharpe = np.mean(daily_returns) / np.std(daily_returns) * np.sqrt(252) if np.std(daily_returns) > 0 else 0
        max_dd = 0
        peak = nv[0]
        for v in nv:
            if v > peak:
                peak = v
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd
        total_return = nv[-1] / nv[0] - 1
        trade_count = len([t for t in result.trade_log if t['action'] == 'sell'])
    else:
        sharpe = 0
        max_dd = 0
        total_return = 0
        trade_count = 0

    return {
        'sharpe': round(sharpe, 4),
        'total_return': round(total_return, 4),
        'max_drawdown': round(max_dd, 4),
        'trade_count': trade_count,
        'total_commission': round(executor.total_commission, 2),
        'total_slippage': round(executor.total_slippage, 2),
    }


def sensitivity_analysis():
    logger.info("=" * 60)
    logger.info("第2步：敏感度分析")
    logger.info("=" * 60)

    base = {'roc_n': 22, 'roc_m': 8, 'buy_roc_edge': 15.0, 'sell_roc_edge': 3.0}
    sweep_ranges = {
        'roc_n': [10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30],
        'roc_m': [3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
        'buy_roc_edge': [5.0, 7.5, 10.0, 12.5, 15.0, 17.5, 20.0],
        'sell_roc_edge': [-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0],
    }

    all_results = {}

    for param_name, values in sweep_ranges.items():
        logger.info(f"\n--- 扫描参数: {param_name} ---")
        results = []
        for val in values:
            cfg = base.copy()
            cfg[param_name] = val
            config = ROCConfig(
                start_date="2023-08-30",
                roc_n=cfg['roc_n'],
                roc_m=cfg['roc_m'],
                buy_roc_edge=cfg['buy_roc_edge'],
                sell_roc_edge=cfg['sell_roc_edge'],
            )
            r = _run_backtest(config)
            results.append({'value': val, **r})
            logger.info(f"  {param_name}={val}: Sharpe={r['sharpe']}, Return={r['total_return']:.2%}, "
                        f"DD={r['max_drawdown']:.2%}, Trades={r['trade_count']}")

        all_results[param_name] = results

        sharpe_vals = [r['sharpe'] for r in results]
        if max(sharpe_vals) - min(sharpe_vals) > 0:
            stability = np.std(sharpe_vals) / np.mean(sharpe_vals) if np.mean(sharpe_vals) != 0 else float('inf')
        else:
            stability = 0
        is_robust = stability < 0.3
        logger.info(f"  稳定性: CV={stability:.3f} ({'稳健' if is_robust else '敏感'})")

    with open(os.path.join(OUTPUT_DIR, 'sensitivity_results.json'), 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    return all_results


def grid_search(sensitivity_results):
    logger.info("\n" + "=" * 60)
    logger.info("第3步：网格搜索")
    logger.info("=" * 60)

    def _find_robust_range(param_name, results, top_n=5):
        sorted_r = sorted(results, key=lambda x: x['sharpe'], reverse=True)
        top = sorted_r[:top_n]
        values = [r['value'] for r in top]
        return min(values), max(values)

    ranges = {}
    for param_name, results in sensitivity_results.items():
        lo, hi = _find_robust_range(param_name, results)
        base_val = {'roc_n': 22, 'roc_m': 8, 'buy_roc_edge': 15.0, 'sell_roc_edge': 3.0}[param_name]
        lo = min(lo, base_val)
        hi = max(hi, base_val)
        if isinstance(base_val, int):
            step = 2
            values = list(range(int(lo), int(hi) + 1, step))
        else:
            step = 2.5
            values = [round(lo + i * step, 1) for i in range(int((hi - lo) / step) + 1)]
        ranges[param_name] = values
        logger.info(f"  {param_name}: 搜索范围 {values}")

    combos = list(itertools.product(
        ranges['roc_n'], ranges['roc_m'], ranges['buy_roc_edge'], ranges['sell_roc_edge']
    ))
    logger.info(f"  总组合数: {len(combos)}")

    grid_results = []
    for i, (rn, rm, buy, sell) in enumerate(combos):
        config = ROCConfig(
            start_date="2023-08-30",
            roc_n=rn, roc_m=rm,
            buy_roc_edge=buy, sell_roc_edge=sell,
        )
        r = _run_backtest(config)
        r['roc_n'] = rn
        r['roc_m'] = rm
        r['buy_roc_edge'] = buy
        r['sell_roc_edge'] = sell
        grid_results.append(r)
        if (i + 1) % 20 == 0:
            logger.info(f"  进度: {i + 1}/{len(combos)}")

    grid_results.sort(key=lambda x: x['sharpe'], reverse=True)

    logger.info(f"\nTop 10 参数组合 (按Sharpe排序):")
    logger.info(f"{'roc_n':>6} {'roc_m':>6} {'buy':>6} {'sell':>6} {'Sharpe':>8} {'Return':>8} {'DD':>8} {'Trades':>7}")
    for r in grid_results[:10]:
        logger.info(f"{r['roc_n']:>6} {r['roc_m']:>6} {r['buy_roc_edge']:>6.1f} {r['sell_roc_edge']:>6.1f} "
                    f"{r['sharpe']:>8.4f} {r['total_return']:>8.2%} {r['max_drawdown']:>8.2%} {r['trade_count']:>7}")

    with open(os.path.join(OUTPUT_DIR, 'grid_search_results.json'), 'w', encoding='utf-8') as f:
        json.dump(grid_results, f, ensure_ascii=False, indent=2)

    return grid_results


def walk_forward_validation(grid_results):
    logger.info("\n" + "=" * 60)
    logger.info("第4步：Walk-Forward验证")
    logger.info("=" * 60)

    top5 = grid_results[:5]
    wf_results = []

    for r in top5:
        rn, rm, buy, sell = r['roc_n'], r['roc_m'], r['buy_roc_edge'], r['sell_roc_edge']

        config_train = ROCConfig(
            start_date="2023-08-30", end_date="2025-04-30",
            roc_n=rn, roc_m=rm, buy_roc_edge=buy, sell_roc_edge=sell,
        )
        train_r = _run_backtest(config_train)

        config_test = ROCConfig(
            start_date="2025-05-01", end_date="2026-04-28",
            roc_n=rn, roc_m=rm, buy_roc_edge=buy, sell_roc_edge=sell,
        )
        test_r = _run_backtest(config_test)

        sharpe_gap = abs(train_r['sharpe'] - test_r['sharpe']) / max(abs(train_r['sharpe']), 0.01)
        is_robust = test_r['sharpe'] > 0.5 and sharpe_gap < 0.5

        wf_results.append({
            'params': {'roc_n': rn, 'roc_m': rm, 'buy_roc_edge': buy, 'sell_roc_edge': sell},
            'train': train_r,
            'test': test_r,
            'sharpe_gap': round(sharpe_gap, 4),
            'is_robust': is_robust,
        })

        logger.info(f"\n  参数: roc_n={rn}, roc_m={rm}, buy={buy}, sell={sell}")
        logger.info(f"    训练集(2023.8~2025.4): Sharpe={train_r['sharpe']:.4f}, Return={train_r['total_return']:.2%}")
        logger.info(f"    验证集(2025.5~2026.4): Sharpe={test_r['sharpe']:.4f}, Return={test_r['total_return']:.2%}")
        logger.info(f"    Sharpe差距: {sharpe_gap:.2%} ({'稳健' if is_robust else '过拟合'})")

    with open(os.path.join(OUTPUT_DIR, 'walk_forward_results.json'), 'w', encoding='utf-8') as f:
        json.dump(wf_results, f, ensure_ascii=False, indent=2)

    return wf_results


def generate_report(sensitivity_results, grid_results, wf_results):
    logger.info("\n" + "=" * 60)
    logger.info("生成优化报告")
    logger.info("=" * 60)

    lines = []
    lines.append("# ROC动量策略参数优化报告")
    lines.append(f"\n生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"回测区间: 2023-08-30 ~ 2026-04-28")
    lines.append(f"交易成本: 佣金万2.5(免5) + 滑点0.1%")
    lines.append("")

    # 敏感度分析
    lines.append("## 一、敏感度分析")
    lines.append("")
    lines.append("在当前参数(roc_n=22, roc_m=8, buy=15, sell=3)附近单变量扫描：")
    lines.append("")

    for param_name, results in sensitivity_results.items():
        lines.append(f"### {param_name}")
        lines.append("")
        lines.append(f"| 值 | Sharpe | 收益率 | 最大回撤 | 交易次数 |")
        lines.append(f"|----|--------|--------|----------|----------|")
        for r in results:
            lines.append(f"| {r['value']} | {r['sharpe']:.4f} | {r['total_return']:.2%} | {r['max_drawdown']:.2%} | {r['trade_count']} |")
        lines.append("")

    # 网格搜索
    lines.append("## 二、网格搜索 Top 15")
    lines.append("")
    lines.append("| roc_n | roc_m | buy | sell | Sharpe | 收益率 | 最大回撤 | 交易次数 |")
    lines.append("|-------|-------|-----|------|--------|--------|----------|----------|")
    for r in grid_results[:15]:
        lines.append(f"| {r['roc_n']} | {r['roc_m']} | {r['buy_roc_edge']} | {r['sell_roc_edge']} | "
                      f"{r['sharpe']:.4f} | {r['total_return']:.2%} | {r['max_drawdown']:.2%} | {r['trade_count']} |")
    lines.append("")

    # Walk-Forward
    lines.append("## 三、Walk-Forward验证")
    lines.append("")
    lines.append("训练集: 2023-08-30 ~ 2025-04-30 | 验证集: 2025-05-01 ~ 2026-04-28")
    lines.append("")
    lines.append("| roc_n | roc_m | buy | sell | 训练Sharpe | 验证Sharpe | Sharpe差距 | 判定 |")
    lines.append("|-------|-------|-----|------|-----------|-----------|-----------|------|")
    for r in wf_results:
        p = r['params']
        lines.append(f"| {p['roc_n']} | {p['roc_m']} | {p['buy_roc_edge']} | {p['sell_roc_edge']} | "
                      f"{r['train']['sharpe']:.4f} | {r['test']['sharpe']:.4f} | {r['sharpe_gap']:.2%} | "
                      f"{'✅ 稳健' if r['is_robust'] else '❌ 过拟合'} |")
    lines.append("")

    # 最终建议
    robust = [r for r in wf_results if r['is_robust']]
    if robust:
        best = max(robust, key=lambda x: x['test']['sharpe'])
        p = best['params']
        lines.append("## 四、最终参数建议")
        lines.append("")
        lines.append(f"| 参数 | 当前值 | 建议值 |")
        lines.append(f"|------|--------|--------|")
        lines.append(f"| roc_n | 22 | **{p['roc_n']}** |")
        lines.append(f"| roc_m | 8 | **{p['roc_m']}** |")
        lines.append(f"| buy_roc_edge | 15.0 | **{p['buy_roc_edge']}** |")
        lines.append(f"| sell_roc_edge | 3.0 | **{p['sell_roc_edge']}** |")
        lines.append("")
        lines.append(f"验证集Sharpe: {best['test']['sharpe']:.4f}")
        lines.append(f"验证集收益率: {best['test']['total_return']:.2%}")
        lines.append(f"验证集最大回撤: {best['test']['max_drawdown']:.2%}")
    else:
        lines.append("## 四、结论")
        lines.append("")
        lines.append("所有Top5参数组合在Walk-Forward验证中均未通过稳健性检验。")
        lines.append("建议保持当前参数不变，或扩大搜索范围重新优化。")

    report_path = os.path.join(OUTPUT_DIR, '参数优化报告.md')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    logger.info(f"优化报告已保存: {report_path}")


if __name__ == "__main__":
    sensitivity_results = sensitivity_analysis()
    grid_results = grid_search(sensitivity_results)
    wf_results = walk_forward_validation(grid_results)
    generate_report(sensitivity_results, grid_results, wf_results)
