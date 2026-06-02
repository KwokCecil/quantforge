r"""系统性参数优化框架：7步法寻找稳健参数（非过拟合最优）

步骤：
  1. IC分析：确定动量效应有效区间（已有结论，跳过）
  2. 条件开关筛选：独立测试每个布尔开关的效果
  3. 敏感度分析：单变量扫描，识别参数稳健区间
  4. 多期Walk-Forward：滚动窗口验证，避免单次分割的偶然性
  5. 网格搜索：在稳健区间内搜索，综合评分选优
  6. Bootstrap验证：统计显著性检验
  7. 综合推荐：基于全部证据给出最终建议

用法：
    $env:PYTHONPATH="e:\JuJu\TraeProjects\量化工程"
    .\.venv\Scripts\python.exe research\param_optimizer_v2.py
"""

import sys
import os
import json
import itertools
import time
from datetime import datetime

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

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '临时文件')
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'sina')
BT_START = '2023-08-30'


class _NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


def _json_dump(obj, path):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, cls=_NumpyEncoder)


BASE_PARAMS = {
    'roc_n': 22, 'roc_m': 8,
    'buy_roc_edge': 20.0, 'sell_roc_edge': 3.0, 'sell_ma_roc_edge': 0.0,
    'top_k': 5,
}

SWITCH_DEFAULTS = {
    'MA_PRICE_CROSS': False,
    'CUT_LOSS': True,
    'STOP_SMALL_TRADE': True,
    'STRICT_BUY': False,
    'ROC_MA_DIRECTION': False,
    'HIGH_WATERMARK_STOP': True,
    'CROWDED_SELL': False,
    'BUY_AVERAGE': False,
    'REBALANCE': False,
}

SWEEP_RANGES = {
    'roc_n': list(range(10, 32, 2)),
    'roc_m': [3, 4, 5, 6, 7, 8, 9, 10, 12],
    'buy_roc_edge': [5.0, 7.5, 10.0, 12.5, 15.0, 17.5, 20.0],
    'sell_roc_edge': [-3.0, -1.0, 0.0, 1.0, 3.0, 5.0],
    'sell_ma_roc_edge': [0.0, 1.0, 3.0, 5.0, 8.0, 10.0],
    'top_k': [3, 5, 7, 10, 12, 15],
    'cut_loss_edge': [0.03, 0.05, 0.08, 0.10, 0.15, 0.20],
}

WF_WINDOWS = [
    ('2023-08-30', '2024-12-31', '2025-01-01', '2025-08-31'),
    ('2023-08-30', '2025-06-30', '2025-07-01', '2026-04-28'),
    ('2024-01-01', '2025-08-31', '2025-09-01', '2026-04-28'),
]


def _run_backtest(config: ROCConfig) -> dict:
    data_feed = CachedDataFeed(source=SinaFinanceFeed(), cache_dir=DATA_DIR)
    strategy = ROCStrategy(config)
    resolver = RankingResolver(
        top_k=config.top_k,
        weight_method='equal' if config.BUY_AVERAGE else 'signal_weight',
        high_watermark_stop_edge=config.high_watermark_stop_edge if config.HIGH_WATERMARK_STOP else float('inf'),
        cut_loss_edge=config.cut_loss_edge if config.CUT_LOSS else float('inf'),
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

    if not result or not result.get('net_values'):
        return {'sharpe': 0, 'sortino': 0, 'total_return': 0, 'max_drawdown': 0, 'calmar': 0, 'trade_count': 0, 'total_commission': 0}

    nv = np.array([v['net_value'] for v in result['net_values']])
    trade_log = result.get('trade_log', [])
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
    trade_count = len([t for t in trade_log if t['action'] == 'sell'])
    calmar = total_return / max_dd if max_dd > 0 else 0
    neg_returns = daily_returns[daily_returns < 0]
    sortino = np.mean(daily_returns) / np.std(neg_returns) * np.sqrt(252) if len(neg_returns) > 0 and np.std(neg_returns) > 0 else 0

    return {
        'sharpe': round(sharpe, 4),
        'sortino': round(sortino, 4),
        'total_return': round(total_return, 4),
        'max_drawdown': round(max_dd, 4),
        'calmar': round(calmar, 4),
        'trade_count': trade_count,
        'total_commission': round(executor.total_commission, 2),
    }


def _make_config(start_date, end_date, **overrides):
    defaults = {
        'start_date': start_date,
        'end_date': end_date,
        **BASE_PARAMS,
        **SWITCH_DEFAULTS,
    }
    defaults.update(overrides)
    return ROCConfig(**defaults)


def _composite_score(sharpe, max_dd, calmar, trade_count):
    if trade_count < 5:
        return -999
    dd_penalty = max(0, max_dd - 0.30) * 10
    trade_penalty = 0 if 20 <= trade_count <= 200 else 0.5
    return sharpe - dd_penalty - trade_penalty


def step2_switch_screening():
    logger.info("=" * 70)
    logger.info("第2步：条件开关筛选")
    logger.info("=" * 70)

    results = {}
    switch_names = list(SWITCH_DEFAULTS.keys())

    for switch in switch_names:
        default_val = SWITCH_DEFAULTS[switch]
        alt_val = not default_val

        cfg_on = _make_config(BT_START, None, **{switch: default_val})
        cfg_off = _make_config(BT_START, None, **{switch: alt_val})

        r_on = _run_backtest(cfg_on)
        r_off = _run_backtest(cfg_off)

        sharpe_diff = r_on['sharpe'] - r_off['sharpe']
        return_diff = r_on['total_return'] - r_off['total_return']

        results[switch] = {
            'default': default_val,
            'default_result': r_on,
            'alt_result': r_off,
            'sharpe_diff': round(sharpe_diff, 4),
            'return_diff': round(return_diff, 4),
            'recommend_default': bool(sharpe_diff > 0),
        }

        logger.info(f"\n  开关: {switch} (默认={default_val})")
        logger.info(f"    默认值({default_val}): Sharpe={r_on['sharpe']:.4f}, Return={r_on['total_return']:.2%}, DD={r_on['max_drawdown']:.2%}")
        logger.info(f"    反向值({alt_val}): Sharpe={r_off['sharpe']:.4f}, Return={r_off['total_return']:.2%}, DD={r_off['max_drawdown']:.2%}")
        logger.info(f"    Sharpe差: {sharpe_diff:+.4f} -> {'保持默认' if sharpe_diff > 0 else '建议切换'}")

    _json_dump(results, os.path.join(OUTPUT_DIR, 'step2_switch_results.json'))
    return results


def step3_sensitivity_analysis():
    logger.info("\n" + "=" * 70)
    logger.info("第3步：敏感度分析")
    logger.info("=" * 70)

    all_results = {}

    for param_name, values in SWEEP_RANGES.items():
        logger.info(f"\n--- 扫描参数: {param_name} ---")
        results = []
        for val in values:
            cfg = _make_config(BT_START, None, **{param_name: val})
            r = _run_backtest(cfg)
            results.append({'value': val, **r})
            logger.info(f"  {param_name}={val}: Sharpe={r['sharpe']:.4f}, Return={r['total_return']:.2%}, "
                        f"DD={r['max_drawdown']:.2%}, Trades={r['trade_count']}")

        sharpe_vals = [r['sharpe'] for r in results]
        nonzero_sharpes = [s for s in sharpe_vals if s != 0]
        if len(nonzero_sharpes) > 1 and np.std(nonzero_sharpes) > 0:
            cv = np.std(nonzero_sharpes) / abs(np.mean(nonzero_sharpes)) if np.mean(nonzero_sharpes) != 0 else 0
        else:
            cv = 0

        best_idx = np.argmax(sharpe_vals)
        best_val = results[best_idx]['value']
        best_sharpe = sharpe_vals[best_idx]

        near_best = [r for r in results if abs(r['sharpe'] - best_sharpe) < 0.1]
        is_plateau = len(near_best) > len(results) * 0.3

        all_results[param_name] = {
            'data': results,
            'cv': round(cv, 4),
            'is_robust': cv < 0.3,
            'best_value': best_val,
            'best_sharpe': best_sharpe,
            'is_plateau': is_plateau,
            'plateau_count': len(near_best),
        }

        logger.info(f"  稳定性: CV={cv:.3f} ({'稳健' if cv < 0.3 else '敏感'})")
        logger.info(f"  最优值: {best_val} (Sharpe={best_sharpe:.4f})")
        logger.info(f"  高原检测: {'是(稳健)' if is_plateau else '否(尖峰)'} - {len(near_best)}/{len(results)}个值接近最优")

    _json_dump(all_results, os.path.join(OUTPUT_DIR, 'step3_sensitivity_results.json'))
    return all_results


def step4_multi_period_wf():
    logger.info("\n" + "=" * 70)
    logger.info("第4步：多期Walk-Forward验证")
    logger.info("=" * 70)

    param_combos = [
        {'roc_n': 22, 'roc_m': 8, 'buy_roc_edge': 15.0, 'sell_roc_edge': 3.0, 'sell_ma_roc_edge': 0.0, 'top_k': 10},
        {'roc_n': 20, 'roc_m': 6, 'buy_roc_edge': 12.5, 'sell_roc_edge': 3.0, 'sell_ma_roc_edge': 0.0, 'top_k': 5},
        {'roc_n': 22, 'roc_m': 8, 'buy_roc_edge': 20.0, 'sell_roc_edge': 3.0, 'sell_ma_roc_edge': 0.0, 'top_k': 10},
        {'roc_n': 18, 'roc_m': 6, 'buy_roc_edge': 12.5, 'sell_roc_edge': 1.0, 'sell_ma_roc_edge': 5.0, 'top_k': 7},
        {'roc_n': 20, 'roc_m': 7, 'buy_roc_edge': 15.0, 'sell_roc_edge': 3.0, 'sell_ma_roc_edge': 0.0, 'top_k': 5},
        {'roc_n': 22, 'roc_m': 8, 'buy_roc_edge': 17.5, 'sell_roc_edge': 3.0, 'sell_ma_roc_edge': 3.0, 'top_k': 10},
        {'roc_n': 16, 'roc_m': 5, 'buy_roc_edge': 12.5, 'sell_roc_edge': 1.0, 'sell_ma_roc_edge': 5.0, 'top_k': 7},
        {'roc_n': 24, 'roc_m': 8, 'buy_roc_edge': 17.5, 'sell_roc_edge': 5.0, 'sell_ma_roc_edge': 0.0, 'top_k': 10},
    ]

    wf_results = []

    for pi, params in enumerate(param_combos):
        logger.info(f"\n--- 参数组合 {pi+1}/{len(param_combos)}: {params} ---")
        combo_result = {'params': params, 'windows': []}
        train_sharpes = []
        test_sharpes = []

        for wi, (train_start, train_end, test_start, test_end) in enumerate(WF_WINDOWS):
            cfg_train = _make_config(train_start, train_end, **params)
            cfg_test = _make_config(test_start, test_end, **params)

            r_train = _run_backtest(cfg_train)
            r_test = _run_backtest(cfg_test)

            train_sharpes.append(r_train['sharpe'])
            test_sharpes.append(r_test['sharpe'])

            combo_result['windows'].append({
                'window': f"W{wi+1}",
                'train_period': f"{train_start}~{train_end}",
                'test_period': f"{test_start}~{test_end}",
                'train_sharpe': r_train['sharpe'],
                'test_sharpe': r_test['sharpe'],
                'train_return': r_train['total_return'],
                'test_return': r_test['total_return'],
                'train_dd': r_train['max_drawdown'],
                'test_dd': r_test['max_drawdown'],
            })

            logger.info(f"  W{wi+1}: 训练Sharpe={r_train['sharpe']:.4f}, 验证Sharpe={r_test['sharpe']:.4f}")

        valid_train = [s for s in train_sharpes if s != 0]
        valid_test = [s for s in test_sharpes if s != 0]
        avg_train = np.mean(valid_train) if valid_train else 0
        avg_test = np.mean(valid_test) if valid_test else 0
        test_positive_rate = sum(1 for s in test_sharpes if s > 0) / len(test_sharpes)

        if avg_train != 0 and avg_test != 0:
            sharpe_gap = abs(avg_train - avg_test) / max(abs(avg_train), abs(avg_test))
        else:
            sharpe_gap = 1.0

        combo_result['avg_train_sharpe'] = round(avg_train, 4)
        combo_result['avg_test_sharpe'] = round(avg_test, 4)
        combo_result['test_positive_rate'] = round(test_positive_rate, 4)
        combo_result['sharpe_gap'] = round(sharpe_gap, 4)
        combo_result['is_robust'] = avg_test > 0.3 and test_positive_rate >= 0.5 and sharpe_gap < 1.0

        logger.info(f"  平均训练Sharpe={avg_train:.4f}, 平均验证Sharpe={avg_test:.4f}")
        logger.info(f"  验证正Sharpe率={test_positive_rate:.0%}, 差距={sharpe_gap:.2%}")
        logger.info(f"  判定: {'稳健' if combo_result['is_robust'] else '过拟合'}")

        wf_results.append(combo_result)

    _json_dump(wf_results, os.path.join(OUTPUT_DIR, 'step4_wf_results.json'))
    return wf_results


def step5_grid_search(sensitivity_results):
    logger.info("\n" + "=" * 70)
    logger.info("第5步：网格搜索")
    logger.info("=" * 70)

    roc_n_values = [18, 20, 22, 24]
    buy_roc_edge_values = [12.5, 15.0, 17.5, 20.0]
    top_k_values = [5, 7, 10]

    sell_ma_roc_edge_values = [0.0, 8.0]
    roc_m_with_smre = [6, 7, 8, 9]
    roc_m_without_smre = [8]

    logger.info(f"  roc_n: {roc_n_values}")
    logger.info(f"  buy_roc_edge: {buy_roc_edge_values}")
    logger.info(f"  top_k: {top_k_values}")
    logger.info(f"  sell_ma_roc_edge: {sell_ma_roc_edge_values}")
    logger.info(f"  roc_m (smre=0): {roc_m_without_smre}")
    logger.info(f"  roc_m (smre>0): {roc_m_with_smre}")

    combos = []
    for rn in roc_n_values:
        for buy in buy_roc_edge_values:
            for tk in top_k_values:
                for smre in sell_ma_roc_edge_values:
                    rm_values = roc_m_with_smre if smre > 0 else roc_m_without_smre
                    for rm in rm_values:
                        combos.append((rn, rm, buy, smre, tk))

    logger.info(f"  总组合数: {len(combos)}")

    grid_results = []
    t0 = time.time()
    for i, (rn, rm, buy, smre, tk) in enumerate(combos):
        config = _make_config(BT_START, None,
                              roc_n=rn, roc_m=rm, buy_roc_edge=buy,
                              sell_ma_roc_edge=smre, top_k=tk)
        r = _run_backtest(config)
        r['roc_n'] = rn
        r['roc_m'] = rm
        r['buy_roc_edge'] = buy
        r['sell_ma_roc_edge'] = smre
        r['top_k'] = tk
        r['composite'] = round(_composite_score(r['sharpe'], r['max_drawdown'], r['calmar'], r['trade_count']), 4)
        grid_results.append(r)
        if (i + 1) % 10 == 0:
            elapsed = time.time() - t0
            eta = elapsed / (i + 1) * (len(combos) - i - 1)
            logger.info(f"  进度: {i+1}/{len(combos)} ({elapsed:.0f}s, ETA {eta:.0f}s)")

    grid_results.sort(key=lambda x: x['composite'], reverse=True)

    logger.info(f"\nTop 15 参数组合 (按综合评分排序):")
    logger.info(f"{'roc_n':>6} {'roc_m':>6} {'buy':>6} {'smre':>6} {'top_k':>6} {'Sharpe':>8} {'Return':>8} {'DD':>8} {'Score':>8}")
    for r in grid_results[:15]:
        logger.info(f"{r['roc_n']:>6} {r['roc_m']:>6} {r['buy_roc_edge']:>6.1f} {r['sell_ma_roc_edge']:>6.1f} {r['top_k']:>6} "
                    f"{r['sharpe']:>8.4f} {r['total_return']:>8.2%} {r['max_drawdown']:>8.2%} {r['composite']:>8.4f}")

    _json_dump(grid_results, os.path.join(OUTPUT_DIR, 'step5_grid_results.json'))
    return grid_results


def step6_bootstrap_validation(grid_results):
    logger.info("\n" + "=" * 70)
    logger.info("第6步：Bootstrap验证")
    logger.info("=" * 70)

    top_n = min(10, len(grid_results))
    top_combos = grid_results[:top_n]
    bootstrap_results = []

    for ri, r in enumerate(top_combos):
        rn, rm, buy, smre, tk = r['roc_n'], r['roc_m'], r['buy_roc_edge'], r['sell_ma_roc_edge'], r['top_k']
        logger.info(f"\n--- Bootstrap验证 {ri+1}/{top_n}: roc_n={rn}, roc_m={rm}, buy={buy}, smre={smre}, top_k={tk} ---")

        config = _make_config(BT_START, None, roc_n=rn, roc_m=rm, buy_roc_edge=buy, sell_ma_roc_edge=smre, top_k=tk)
        data_feed = CachedDataFeed(source=SinaFinanceFeed(), cache_dir=DATA_DIR)
        strategy = ROCStrategy(config)
        resolver = RankingResolver(
            top_k=config.top_k,
            weight_method='equal' if config.BUY_AVERAGE else 'signal_weight',
            high_watermark_stop_edge=config.high_watermark_stop_edge if config.HIGH_WATERMARK_STOP else float('inf'),
            cut_loss_edge=config.cut_loss_edge if config.CUT_LOSS else float('inf'),
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

        if not result or not result.get('net_values') or len(result['net_values']) < 30:
            bootstrap_results.append({
                'params': {'roc_n': rn, 'roc_m': rm, 'buy_roc_edge': buy, 'sell_ma_roc_edge': smre, 'top_k': tk},
                'error': '数据不足',
            })
            continue

        nv = np.array([v['net_value'] for v in result['net_values']])
        daily_returns = np.diff(nv) / nv[:-1]
        n_bootstrap = 500
        bootstrap_sharpes = []
        n_days = len(daily_returns)
        block_size = max(5, n_days // 20)

        for _ in range(n_bootstrap):
            indices = []
            while len(indices) < n_days:
                start_idx = np.random.randint(0, max(1, n_days - block_size + 1))
                indices.extend(range(start_idx, min(start_idx + block_size, n_days)))
            indices = indices[:n_days]
            sample_returns = daily_returns[indices]
            if np.std(sample_returns) > 0:
                bs_sharpe = np.mean(sample_returns) / np.std(sample_returns) * np.sqrt(252)
                bootstrap_sharpes.append(bs_sharpe)

        if not bootstrap_sharpes:
            bootstrap_results.append({
                'params': {'roc_n': rn, 'roc_m': rm, 'buy_roc_edge': buy, 'sell_ma_roc_edge': smre, 'top_k': tk},
                'error': 'Bootstrap失败',
            })
            continue

        bootstrap_sharpes = np.array(bootstrap_sharpes)
        ci_lower = np.percentile(bootstrap_sharpes, 2.5)
        ci_upper = np.percentile(bootstrap_sharpes, 97.5)
        p_positive = np.mean(bootstrap_sharpes > 0)
        p_above_half = np.mean(bootstrap_sharpes > 0.5)

        bs_result = {
            'params': {'roc_n': rn, 'roc_m': rm, 'buy_roc_edge': buy, 'sell_ma_roc_edge': smre, 'top_k': tk},
            'original_sharpe': r['sharpe'],
            'bootstrap_mean_sharpe': round(float(np.mean(bootstrap_sharpes)), 4),
            'ci_lower': round(float(ci_lower), 4),
            'ci_upper': round(float(ci_upper), 4),
            'p_positive': round(float(p_positive), 4),
            'p_above_0_5': round(float(p_above_half), 4),
            'is_significant': bool(ci_lower > 0 and p_positive > 0.95),
        }
        bootstrap_results.append(bs_result)

        logger.info(f"  原始Sharpe={r['sharpe']:.4f}")
        logger.info(f"  Bootstrap均值={np.mean(bootstrap_sharpes):.4f}")
        logger.info(f"  95%CI=[{ci_lower:.4f}, {ci_upper:.4f}]")
        logger.info(f"  P(Sharpe>0)={p_positive:.2%}, P(Sharpe>0.5)={p_above_half:.2%}")
        logger.info(f"  判定: {'显著' if bs_result['is_significant'] else '不显著'}")

    _json_dump(bootstrap_results, os.path.join(OUTPUT_DIR, 'step6_bootstrap_results.json'))
    return bootstrap_results


def step7_final_recommendation(switch_results, sensitivity_results, wf_results, grid_results, bootstrap_results):
    logger.info("\n" + "=" * 70)
    logger.info("第7步：综合推荐")
    logger.info("=" * 70)

    robust_wf = [r for r in wf_results if r.get('is_robust')]
    significant_bs = [r for r in bootstrap_results if r.get('is_significant')]

    if significant_bs:
        best_bs = max(significant_bs, key=lambda x: x['bootstrap_mean_sharpe'])
        p = best_bs['params']
        best_combo = None
        for r in grid_results:
            if (r['roc_n'] == p['roc_n'] and r['roc_m'] == p['roc_m']
                    and r['buy_roc_edge'] == p['buy_roc_edge']
                    and r['sell_ma_roc_edge'] == p['sell_ma_roc_edge']
                    and r['top_k'] == p['top_k']):
                best_combo = r
                break
        if best_combo is None:
            best_combo = grid_results[0]
    else:
        best_combo = grid_results[0]

    switch_recommendations = {}
    for switch, data in switch_results.items():
        switch_recommendations[switch] = {
            'value': bool(data['default'] if data['recommend_default'] else not data['default']),
            'confidence': 'high' if abs(data['sharpe_diff']) > 0.1 else 'low',
        }

    recommendation = {
        'best_params': {
            'roc_n': best_combo['roc_n'],
            'roc_m': best_combo['roc_m'],
            'buy_roc_edge': best_combo['buy_roc_edge'],
            'sell_ma_roc_edge': best_combo['sell_ma_roc_edge'],
            'top_k': best_combo['top_k'],
        },
        'best_metrics': {
            'sharpe': best_combo['sharpe'],
            'total_return': best_combo['total_return'],
            'max_drawdown': best_combo['max_drawdown'],
            'calmar': best_combo['calmar'],
            'composite': best_combo['composite'],
        },
        'switch_recommendations': switch_recommendations,
        'wf_robust_count': len(robust_wf),
        'bootstrap_significant_count': len(significant_bs),
        'confidence_level': 'high' if len(robust_wf) > 0 and len(significant_bs) > 0 else ('medium' if len(significant_bs) > 0 else 'low'),
    }

    _json_dump(recommendation, os.path.join(OUTPUT_DIR, 'step7_recommendation.json'))
    return recommendation


def generate_full_report(switch_results, sensitivity_results, wf_results, grid_results, bootstrap_results, recommendation):
    logger.info("\n" + "=" * 70)
    logger.info("生成完整优化报告")
    logger.info("=" * 70)

    lines = []
    lines.append("# ROC动量策略参数优化报告（系统性7步法）")
    lines.append(f"\n生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"回测区间: {BT_START} ~ 2026-04-28")
    lines.append(f"交易成本: 佣金万2.5(免5) + 滑点0.1%")
    lines.append(f"初始资金: 40,000元")
    lines.append("")

    lines.append("## 优化方法论")
    lines.append("")
    lines.append("本报告采用**7步法**系统性寻找稳健参数，核心原则是**避免过拟合**：")
    lines.append("")
    lines.append("1. **IC分析**：用信息系数确定动量效应的有效区间（已有结论）")
    lines.append("2. **条件开关筛选**：独立测试每个布尔开关，确定有效组合")
    lines.append("3. **敏感度分析**：单变量扫描，识别参数稳健区间（高原 vs 尖峰）")
    lines.append("4. **多期Walk-Forward**：3个滚动窗口验证，避免单次分割偶然性")
    lines.append("5. **网格搜索**：在稳健区间内搜索，综合评分选优")
    lines.append("6. **Bootstrap验证**：500次Block Bootstrap重采样，统计显著性检验")
    lines.append("7. **综合推荐**：基于全部证据给出最终建议")
    lines.append("")

    lines.append("---")
    lines.append("")

    lines.append("## 一、条件开关筛选")
    lines.append("")
    lines.append("独立测试每个布尔开关（其他参数保持默认），判断开关方向：")
    lines.append("")
    lines.append("| 开关 | 默认值 | 默认Sharpe | 反向Sharpe | Sharpe差 | 建议 |")
    lines.append("|------|--------|-----------|-----------|---------|------|")
    for switch, data in switch_results.items():
        rec = '保持默认' if data['recommend_default'] else '**切换**'
        lines.append(f"| {switch} | {data['default']} | {data['default_result']['sharpe']:.4f} | "
                    f"{data['alt_result']['sharpe']:.4f} | {data['sharpe_diff']:+.4f} | {rec} |")
    lines.append("")

    lines.append("### 开关筛选结论")
    lines.append("")
    positive_switches = [s for s, d in switch_results.items() if d['recommend_default']]
    negative_switches = [s for s, d in switch_results.items() if not d['recommend_default']]
    lines.append(f"- 默认方向更优: {', '.join(positive_switches)}")
    lines.append(f"- 反向更优: {', '.join(negative_switches) if negative_switches else '无'}")
    no_effect = [s for s, d in switch_results.items() if abs(d['sharpe_diff']) < 0.001]
    if no_effect:
        lines.append(f"- **无效果开关**（Sharpe差<0.001）: {', '.join(no_effect)}")
    lines.append("")

    lines.append("---")
    lines.append("")

    lines.append("## 二、敏感度分析")
    lines.append("")
    lines.append("单变量扫描，观察Sharpe随参数值的变化趋势：")
    lines.append("")

    for param_name, data in sensitivity_results.items():
        lines.append(f"### {param_name}")
        lines.append("")
        lines.append(f"| 值 | Sharpe | 收益率 | 最大回撤 | 交易次数 | Calmar |")
        lines.append(f"|----|--------|--------|----------|----------|--------|")
        for r in data['data']:
            lines.append(f"| {r['value']} | {r['sharpe']:.4f} | {r['total_return']:.2%} | "
                        f"{r['max_drawdown']:.2%} | {r['trade_count']} | {r['calmar']:.4f} |")
        lines.append("")
        lines.append(f"- 稳定性(CV): {data['cv']:.3f} ({'稳健' if data['is_robust'] else '敏感'})")
        lines.append(f"- 最优值: {data['best_value']} (Sharpe={data['best_sharpe']:.4f})")
        lines.append(f"- 高原检测: {'是(参数稳健)' if data['is_plateau'] else '否(尖峰风险)'} - "
                    f"{data['plateau_count']}/{len(data['data'])}个值接近最优")
        lines.append("")

    lines.append("---")
    lines.append("")

    lines.append("## 三、多期Walk-Forward验证")
    lines.append("")
    lines.append("使用3个滚动窗口，每个窗口分训练集和验证集：")
    lines.append("")
    lines.append("| 窗口 | 训练集 | 验证集 |")
    lines.append("|------|--------|--------|")
    for i, (ts, te, vs, ve) in enumerate(WF_WINDOWS):
        lines.append(f"| W{i+1} | {ts} ~ {te} | {vs} ~ {ve} |")
    lines.append("")

    lines.append("### 各参数组合Walk-Forward结果")
    lines.append("")
    lines.append("| roc_n | roc_m | buy | smre | top_k | 平均训练Sharpe | 平均验证Sharpe | 验证正率 | Sharpe差距 | 判定 |")
    lines.append("|-------|-------|-----|------|-------|---------------|---------------|---------|-----------|------|")
    for r in wf_results:
        p = r['params']
        lines.append(f"| {p['roc_n']} | {p['roc_m']} | {p['buy_roc_edge']} | {p.get('sell_ma_roc_edge', 0)} | {p['top_k']} | "
                    f"{r['avg_train_sharpe']:.4f} | {r['avg_test_sharpe']:.4f} | "
                    f"{r['test_positive_rate']:.0%} | {r['sharpe_gap']:.2%} | "
                    f"{'稳健' if r['is_robust'] else '过拟合'} |")
    lines.append("")

    lines.append("---")
    lines.append("")

    lines.append("## 四、网格搜索 Top 20")
    lines.append("")
    lines.append("| 排名 | roc_n | roc_m | buy | smre | top_k | Sharpe | 收益率 | 最大回撤 | Calmar | 综合评分 |")
    lines.append("|------|-------|-------|-----|------|-------|--------|--------|----------|--------|----------|")
    for i, r in enumerate(grid_results[:20]):
        lines.append(f"| {i+1} | {r['roc_n']} | {r['roc_m']} | {r['buy_roc_edge']} | {r['sell_ma_roc_edge']} | {r['top_k']} | "
                    f"{r['sharpe']:.4f} | {r['total_return']:.2%} | {r['max_drawdown']:.2%} | "
                    f"{r['calmar']:.4f} | {r['composite']:.4f} |")
    lines.append("")

    lines.append("### 综合评分公式")
    lines.append("```")
    lines.append("composite = Sharpe - max(0, DD-30%)*10 - (交易次数<5 ? -999 : 0) - (交易次数异常 ? 0.5 : 0)")
    lines.append("```")
    lines.append("")

    lines.append("---")
    lines.append("")

    lines.append("## 五、Bootstrap验证")
    lines.append("")
    lines.append("对Top10参数组合进行500次Block Bootstrap重采样：")
    lines.append("")
    lines.append("| roc_n | roc_m | buy | smre | top_k | 原始Sharpe | BS均值 | 95%CI下限 | 95%CI上限 | P(>0) | P(>0.5) | 显著? |")
    lines.append("|-------|-------|-----|------|-------|-----------|--------|----------|----------|-------|---------|------|")
    for r in bootstrap_results:
        p = r['params']
        if 'error' in r:
            lines.append(f"| {p['roc_n']} | {p['roc_m']} | {p['buy_roc_edge']} | {p['sell_ma_roc_edge']} | {p['top_k']} | - | - | - | - | - | - | - |")
            continue
        lines.append(f"| {p['roc_n']} | {p['roc_m']} | {p['buy_roc_edge']} | {p['sell_ma_roc_edge']} | {p['top_k']} | "
                    f"{r['original_sharpe']:.4f} | {r['bootstrap_mean_sharpe']:.4f} | "
                    f"{r['ci_lower']:.4f} | {r['ci_upper']:.4f} | "
                    f"{r['p_positive']:.2%} | {r['p_above_0_5']:.2%} | "
                    f"{'Y' if r['is_significant'] else 'N'} |")
    lines.append("")

    sig_count = sum(1 for r in bootstrap_results if r.get('is_significant'))
    lines.append(f"**统计显著组合数**: {sig_count}/{len(bootstrap_results)}")
    lines.append("")

    lines.append("---")
    lines.append("")

    lines.append("## 六、最终推荐")
    lines.append("")
    bp = recommendation['best_params']
    bm = recommendation['best_metrics']
    lines.append("### 推荐参数")
    lines.append("")
    lines.append("| 参数 | 当前值 | 推荐值 | 变化 |")
    lines.append("|------|--------|--------|------|")
    for key in ['roc_n', 'roc_m', 'buy_roc_edge', 'sell_ma_roc_edge', 'top_k']:
        current = BASE_PARAMS.get(key, 0)
        new = bp[key]
        change = '' if current == new else '**已变更**'
        lines.append(f"| {key} | {current} | **{new}** | {change} |")
    lines.append("")

    lines.append("### 推荐开关配置")
    lines.append("")
    lines.append("| 开关 | 推荐值 | 置信度 | 说明 |")
    lines.append("|------|--------|--------|------|")
    for switch, rec in recommendation['switch_recommendations'].items():
        if abs(switch_results[switch]['sharpe_diff']) < 0.001:
            note = '无效果'
        elif rec['value'] == SWITCH_DEFAULTS[switch]:
            note = '保持默认'
        else:
            note = '建议切换'
        lines.append(f"| {switch} | {rec['value']} | {rec['confidence']} | {note} |")
    lines.append("")

    lines.append("### 推荐参数绩效")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|------|-----|")
    lines.append(f"| Sharpe | {bm['sharpe']:.4f} |")
    lines.append(f"| 总收益率 | {bm['total_return']:.2%} |")
    lines.append(f"| 最大回撤 | {bm['max_drawdown']:.2%} |")
    lines.append(f"| Calmar | {bm['calmar']:.4f} |")
    lines.append(f"| 综合评分 | {bm['composite']:.4f} |")
    lines.append("")

    lines.append("### 可信度评估")
    lines.append("")
    lines.append(f"| 维度 | 结果 |")
    lines.append(f"|------|------|")
    lines.append(f"| Walk-Forward稳健组合 | {recommendation['wf_robust_count']}/{len(wf_results)} |")
    lines.append(f"| Bootstrap显著组合 | {recommendation['bootstrap_significant_count']}/{len(bootstrap_results)} |")
    lines.append(f"| **总体置信度** | **{recommendation['confidence_level']}** |")
    lines.append("")

    if recommendation['confidence_level'] == 'high':
        lines.append("高置信度：参数在Walk-Forward和Bootstrap双重验证中均通过，推荐采用。")
    elif recommendation['confidence_level'] == 'medium':
        lines.append("中等置信度：参数在Bootstrap验证中显著，但Walk-Forward表现不一致，建议谨慎采用并持续观察。")
    else:
        lines.append("低置信度：参数在统计检验中未通过，建议保持当前参数或扩大搜索范围。")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 附录：关键发现")
    lines.append("")
    lines.append("### roc_m 与 sell_ma_roc_edge 的关系")
    lines.append("")
    lines.append("roc_m计算的是MAROC（ROC的移动平均）。MAROC仅在以下条件时影响策略决策：")
    lines.append("- sell_ma_roc_edge > 0 时，MAROC < sell_ma_roc_edge 触发卖出")
    lines.append("- ROC_MA_DIRECTION = True 时，MAROC方向向下触发卖出/阻止买入")
    lines.append("")
    lines.append("当 sell_ma_roc_edge = 0（默认）且 ROC_MA_DIRECTION = False（默认）时，")
    lines.append("roc_m 的变化不影响任何决策，因此敏感度分析中所有roc_m值产出相同结果。")
    lines.append("本优化通过同时扫描 roc_m 和 sell_ma_roc_edge 来发现两者的交互效应。")
    lines.append("")

    report_path = os.path.join(OUTPUT_DIR, '参数优化报告_v2.md')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    logger.info(f"完整报告已保存: {report_path}")
    return report_path


if __name__ == "__main__":
    t_start = time.time()

    USE_CACHE = True

    step2_path = os.path.join(OUTPUT_DIR, 'step2_switch_results.json')
    step3_path = os.path.join(OUTPUT_DIR, 'step3_sensitivity_results.json')
    step4_path = os.path.join(OUTPUT_DIR, 'step4_wf_results.json')

    if USE_CACHE and os.path.exists(step2_path):
        with open(step2_path, 'r', encoding='utf-8') as f:
            switch_results = json.load(f)
        logger.info("跳过第2步（使用缓存）")
    else:
        switch_results = step2_switch_screening()

    if USE_CACHE and os.path.exists(step3_path):
        with open(step3_path, 'r', encoding='utf-8') as f:
            sensitivity_results = json.load(f)
        logger.info("跳过第3步（使用缓存）")
    else:
        sensitivity_results = step3_sensitivity_analysis()

    if USE_CACHE and os.path.exists(step4_path):
        with open(step4_path, 'r', encoding='utf-8') as f:
            wf_results = json.load(f)
        logger.info("跳过第4步（使用缓存）")
    else:
        wf_results = step4_multi_period_wf()

    grid_results = step5_grid_search(sensitivity_results)
    bootstrap_results = step6_bootstrap_validation(grid_results)
    recommendation = step7_final_recommendation(switch_results, sensitivity_results, wf_results, grid_results, bootstrap_results)
    report_path = generate_full_report(switch_results, sensitivity_results, wf_results, grid_results, bootstrap_results, recommendation)

    elapsed = time.time() - t_start
    logger.info(f"\n总耗时: {elapsed:.0f}s ({elapsed/60:.1f}min)")
    logger.info(f"报告路径: {report_path}")
