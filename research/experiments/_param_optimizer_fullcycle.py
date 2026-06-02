r"""全周期参数稳健性重验证 v2（2018-2026）— 真正 Walk-Forward 版

v2 变更（基于用户反馈）:
  1. Walk-Forward 改为真正的训练+验证模式:
     每个窗口在训练区间上网格搜索最优参数 → 应用到验证区间
  2. 生产基线严格对照 tech_growth.json: buy_roc_edge=20.0 (原错误写为15.0)
  3. 敏感度分析基线修正为正确的生产参数
  4. v2.1: WF 扩展搜索空间 roc_n[18,20,22,24,26] × buy[12.5,15,17.5,20] × top_k[3,5,7] = 60组合/窗口
     并行执行: ProcessPoolExecutor, max_workers=10

分析顺序:
  1. Walk-Forward (真正训练+验证, ~305次回测, 并行)
  2. 参数敏感度 (~53次回测)
  3. 网格搜索 (~60次回测)
  4. 成本敏感性 (5次回测)
  5. IC/ICIR 矩阵扫描

用法:
    .\.venv\Scripts\python.exe research\_param_optimizer_fullcycle.py          # 全部运行
    .\.venv\Scripts\python.exe research\_param_optimizer_fullcycle.py --only wf  # 仅Walk-Forward
"""

import sys
import os
import json
import itertools
import time
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np
import pandas as pd
from loguru import logger

# 抑制 DEBUG 日志以加速回测（每ETF每日均产生日志）
logger.remove()
logger.add(sys.stderr, level="INFO")

from quantforge.core.data_feed import CachedDataFeed
from quantforge.data_sources.sina_feed import SinaFinanceFeed
from quantforge.strategies._configs.roc_config import ROCConfig
from quantforge.main_backtest import run_core_backtest
from quantforge.strategies.factory import create_config

# 33只科技/成长方向ETF（与 TECH_GROWTH_CODES 一致）
TECH_GROWTH_CODES = [
    "515880", "159245", "159839", "512690", "159851", "515170",
    "159915", "510300", "588000", "159531", "501021",
    "513050", "159813", "159770", "159819", "516520",
    "159993", "501089", "159996", "513060", "159899",
    "516780", "516020",
    "159922", "512100", "513970", "515950",
    "159824", "561910", "159840", "515790", "516160", "159731",
]
from quantforge.strategies.roc_momentum import ROCStrategy
from quantforge.core.resolver import RankingResolver
from quantforge.core.executor import BacktestExecutor
from quantforge.core.backtest_core import run_backtest

# ============================================================
# 配置
# ============================================================
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '临时文件')
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'sina')
BT_START = '2018-01-01'
BT_END = '2026-04-28'
BENCHMARK = '399006'

# 生产基线参数 (严格对照 tech_growth.json)
PROD_PARAMS = {
    'roc_n': 22,
    'roc_m': 8,
    'buy_roc_edge': 20.0,
    'sell_roc_edge': 3.0,
    'sell_ma_roc_edge': 0.0,
    'top_k': 5,
    'cut_loss_edge': 0.08,
    'high_watermark_stop_edge': 0.10,
    'initial_capital': 40000.0,
}

# Walk-Forward 训练期网格搜索空间（每窗口在此空间内寻优）
WF_SEARCH_SPACE = {
    'roc_n': [22, 26],
    'buy_roc_edge': [17.5, 20.0],
    'top_k': [5, 7],
}

# WF 扩展搜索空间（覆盖最优峰值区 + 高原 + 分散维度，真正的参数寻优）
WF_SEARCH_SPACE_EXPANDED = {
    'roc_n': [18, 20, 22, 24, 26],
    'buy_roc_edge': [12.5, 15.0, 17.5, 20.0],
    'top_k': [3, 5, 7],
}
# = 5 × 4 × 3 = 60 组合/窗口 × 5 窗口 = 300 训练 + 5 验证 = 305 回测

# 敏感度扫描范围（与短周期版一致，便于对比）
SENSITIVITY_SWEEP = {
    'roc_n': list(range(10, 34, 2)),
    'roc_m': [3, 4, 5, 6, 7, 8, 9, 10, 12],
    'buy_roc_edge': [5.0, 7.5, 10.0, 12.5, 15.0, 17.5, 20.0, 25.0],
    'sell_roc_edge': [-3.0, -1.0, 0.0, 1.0, 3.0, 5.0],
    'sell_ma_roc_edge': [0.0, 1.0, 3.0, 5.0, 8.0, 10.0],
    'top_k': [3, 5, 7, 10, 12, 15],
    'cut_loss_edge': [0.03, 0.05, 0.08, 0.10, 0.15, 0.20],
}

# Walk-Forward 窗口（全周期非重叠，每窗约2.5年训练 + 1.5年验证）
WF_WINDOWS_FULL = [
    ('2018-01-01', '2020-06-30', '2020-07-01', '2021-12-31'),   # W1: 含2018熊市训练, 2019-2020牛验证
    ('2019-01-01', '2021-06-30', '2021-07-01', '2023-06-30'),   # W2: 含2020牛训练, 2022熊验证
    ('2020-01-01', '2022-06-30', '2022-07-01', '2024-06-30'),   # W3: 含2022熊训练, 2023-2024恢复验证
    ('2021-01-01', '2023-12-31', '2024-01-01', '2025-06-30'),   # W4: 震荡市训练, 2024-2025验证
    ('2022-01-01', '2024-06-30', '2024-07-01', '2026-04-28'),   # W5: 含反弹训练, 近期验证
]

# 成本情景
COST_SCENARIOS = [
    {'name': '当前(万2.5)', 'commission_rate': 0.00025, 'slippage': 0.001},
    {'name': '万1.5', 'commission_rate': 0.00015, 'slippage': 0.001},
    {'name': '万1.0', 'commission_rate': 0.00010, 'slippage': 0.001},
    {'name': '万2.5+滑点0.2%', 'commission_rate': 0.00025, 'slippage': 0.002},
    {'name': '万5+滑点0.2%', 'commission_rate': 0.00050, 'slippage': 0.002},
]

# 网格搜索范围
GRID_RANGES_FULL = {
    'roc_n': [18, 20, 22, 24],
    'roc_m': [8],  # 固定（smre=0时不影响）
    'buy_roc_edge': [10.0, 12.5, 15.0, 17.5, 20.0],
    'sell_ma_roc_edge': [0.0],
    'top_k': [5, 7, 10],
}


class _NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (np.integer,)): return int(obj)
        if isinstance(obj, (np.floating,)): return float(obj)
        if isinstance(obj, (np.bool_,)): return bool(obj)
        if isinstance(obj, np.ndarray): return obj.tolist()
        if hasattr(obj, 'tolist'): return obj.tolist()
        return super().default(obj)


def _json_dump(obj, path):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, cls=_NumpyEncoder)


def _make_config_full(start_date, end_date, **overrides):
    """从生产配置 tech_growth.json 出发，叠加日期和参数覆盖"""
    config = create_config("roc_momentum", "tech_growth")
    config.start_date = start_date
    if end_date:
        config.end_date = end_date
    for k, v in overrides.items():
        setattr(config, k, v)
    return config


def _run_backtest_full(config: ROCConfig, skip_cache_refresh: bool = False) -> dict:
    """运行回测并返回关键标量指标 —— 统一通过 main_backtest.run_core_backtest"""
    result = run_core_backtest(config, skip_cache_refresh=skip_cache_refresh)
    if result is None:
        return {'sharpe': 0, 'total_return': 0, 'max_drawdown': 0, 'calmar': 0, 'trade_count': 0}
    return {
        'sharpe': result['sharpe'],
        'sortino': result.get('sortino', 0),
        'total_return': result['total_return'],
        'max_drawdown': result['max_drawdown'],
        'calmar': result['calmar'],
        'trade_count': result['trade_count'],
        'total_commission': result.get('total_commission', 0),
    }


def _pre_refresh_cache():
    """预刷新全部ETF缓存，单线程执行一次，避免批量回测时并发Sina请求。
    
    后续所有 _run_backtest_full 调用传 skip_cache_refresh=True 即可直接从磁盘缓存读取。
    """
    logger.info("预刷新缓存: 33只ETF, 单线程...")
    t0 = time.time()
    data_feed = CachedDataFeed(source=SinaFinanceFeed(), cache_dir=DATA_DIR)
    data_feed.update_cache(codes=TECH_GROWTH_CODES, data_type='fund', start=BT_START, end=BT_END)
    logger.info(f"预刷新完成: {time.time() - t0:.0f}s")


# ============================================================
# 分析 1: Walk-Forward — 真正训练+验证版
# ============================================================

def _run_single_backtest(args):
    """模块级 Worker 函数，供 ProcessPoolExecutor 并行调用。
    
    Args:
        args: (start, end, rn, buy, tk, base_params) 元组
    
    Returns:
        dict: 包含参数+回测结果的字典
    """
    start, end, rn, buy, tk, base_params = args
    params = {**base_params, 'roc_n': rn, 'buy_roc_edge': buy, 'top_k': tk}
    cfg = _make_config_full(start, end, **params)
    r = _run_backtest_full(cfg, skip_cache_refresh=True)
    score = _composite_score(r['sharpe'], r['max_drawdown'], r['calmar'], r['trade_count'])
    return {
        'roc_n': rn, 'buy_roc_edge': buy, 'top_k': tk,
        'sharpe': r['sharpe'], 'total_return': r['total_return'],
        'max_drawdown': r['max_drawdown'], 'trade_count': r['trade_count'],
        'score': round(score, 4),
    }


def _grid_search_on_period_parallel(start, end, search_space, base_params, max_workers=10):
    """并行版：在指定区间内网格搜索最优参数组合。
    
    使用 ProcessPoolExecutor 并行执行所有组合的回测。
    """
    rn_vals = search_space['roc_n']
    buy_vals = search_space['buy_roc_edge']
    tk_vals = search_space['top_k']
    
    combos = list(itertools.product(rn_vals, buy_vals, tk_vals))
    tasks = [(start, end, rn, buy, tk, base_params) for rn, buy, tk in combos]
    
    logger.info(f"  并行回测: {len(tasks)} 组合 × {max_workers} workers")
    t0 = time.time()
    
    results = []
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_run_single_backtest, task): task for task in tasks}
        for i, future in enumerate(as_completed(futures)):
            results.append(future.result())
            if (i + 1) % 10 == 0 or (i + 1) == len(tasks):
                elapsed = time.time() - t0
                eta = elapsed / (i + 1) * (len(tasks) - i - 1) if i + 1 < len(tasks) else 0
                logger.info(f"  进度: {i+1}/{len(tasks)} ({elapsed:.0f}s, ETA {eta:.0f}s)")
    
    results.sort(key=lambda x: x['score'], reverse=True)
    best = results[0]
    elapsed = time.time() - t0
    logger.info(f"  并行搜索完成: {elapsed:.0f}s ({elapsed/60:.1f}min)")
    return best, results


def _grid_search_on_period(start, end, search_space, base_params):
    """在指定区间内网格搜索最优参数组合
    
    返回: (best_params, best_score, all_results)
    """
    rn_vals = search_space['roc_n']
    buy_vals = search_space['buy_roc_edge']
    tk_vals = search_space['top_k']
    
    combos = list(itertools.product(rn_vals, buy_vals, tk_vals))
    results = []
    
    for rn, buy, tk in combos:
        params = {**base_params, 'roc_n': rn, 'buy_roc_edge': buy, 'top_k': tk}
        cfg = _make_config_full(start, end, **params)
        r = _run_backtest_full(cfg, skip_cache_refresh=True)
        score = _composite_score(r['sharpe'], r['max_drawdown'], r['calmar'], r['trade_count'])
        results.append({
            'roc_n': rn, 'buy_roc_edge': buy, 'top_k': tk,
            'sharpe': r['sharpe'], 'total_return': r['total_return'],
            'max_drawdown': r['max_drawdown'], 'trade_count': r['trade_count'],
            'score': round(score, 4),
        })
    
    # 按综合评分排序
    results.sort(key=lambda x: x['score'], reverse=True)
    best = results[0]
    return best, results
    
    
def run_walkforward_full():
    search_space = WF_SEARCH_SPACE_EXPANDED
    n_combos = len(search_space['roc_n']) * len(search_space['buy_roc_edge']) * len(search_space['top_k'])
    
    logger.info("=" * 70)
    logger.info("分析1: Walk-Forward (真正训练+验证) - 5窗口 × 每窗口网格优化(并行)")
    logger.info("=" * 70)
    logger.info(f"搜索空间: roc_n={search_space['roc_n']}, "
                f"buy={search_space['buy_roc_edge']}, top_k={search_space['top_k']}")
    logger.info(f"每窗口 {n_combos} 组合, 共 5 窗口, 总计 {n_combos * 5} 训练回测")
    logger.info(f"并行: ProcessPoolExecutor, max_workers=10")

    wf_results = []
    t0 = time.time()
    
    for wi, (ts, te, vs, ve) in enumerate(WF_WINDOWS_FULL):
        logger.info(f"\n--- W{wi+1}: 训练 {ts}~{te}, 验证 {vs}~{ve} ---")
        
        # 训练阶段：在训练区间上并行网格搜索最优参数
        best_params, search_results = _grid_search_on_period_parallel(
            ts, te, search_space, PROD_PARAMS, max_workers=10
        )
        
        logger.info(f"  训练最优: roc_n={best_params['roc_n']}, buy={best_params['buy_roc_edge']}, "
                    f"top_k={best_params['top_k']}, 训练Sharpe={best_params['sharpe']:.4f}, "
                    f"训练DD={best_params['max_drawdown']:.2%}")
        
        # 验证阶段：用训练最优参数在验证区间上回测
        test_params = {k: best_params[k] for k in ['roc_n', 'buy_roc_edge', 'top_k']}
        cfg_test = _make_config_full(vs, ve, **test_params)
        r_test = _run_backtest_full(cfg_test, skip_cache_refresh=True)
        
        logger.info(f"  验证结果: Sharpe={r_test['sharpe']:.4f}, Return={r_test['total_return']:.2%}, "
                    f"DD={r_test['max_drawdown']:.2%}")
        
        window_result = {
            'window': f"W{wi+1}",
            'train_period': f"{ts}~{te}",
            'test_period': f"{vs}~{ve}",
            'best_train_params': test_params,
            'train_sharpe': best_params['sharpe'],
            'train_return': best_params['total_return'],
            'train_dd': best_params['max_drawdown'],
            'train_score': best_params['score'],
            'train_search_results': search_results,  # 保留完整的训练搜索记录
            'test_sharpe': r_test['sharpe'],
            'test_return': r_test['total_return'],
            'test_dd': r_test['max_drawdown'],
            'test_trade_count': r_test['trade_count'],
        }
        wf_results.append(window_result)
    
    # 汇总
    train_sharpes = [r['train_sharpe'] for r in wf_results]
    test_sharpes = [r['test_sharpe'] for r in wf_results]
    avg_train = np.mean(train_sharpes)
    avg_test = np.mean(test_sharpes)
    test_positive = sum(1 for s in test_sharpes if s > 0) / len(test_sharpes)
    
    logger.info(f"\n{'='*40}")
    logger.info(f"Walk-Forward 汇总:")
    logger.info(f"  平均训练Sharpe: {avg_train:.4f}")
    logger.info(f"  平均验证Sharpe: {avg_test:.4f}")
    logger.info(f"  验证正Sharpe率: {test_positive:.0%}")
    logger.info(f"  各窗口最优参数:")
    for r in wf_results:
        logger.info(f"    {r['window']}: roc_n={r['best_train_params']['roc_n']}, "
                    f"buy={r['best_train_params']['buy_roc_edge']}, "
                    f"top_k={r['best_train_params']['top_k']}, "
                    f"训练Sharpe={r['train_sharpe']:.4f} → 验证Sharpe={r['test_sharpe']:.4f}")
    
    elapsed = time.time() - t0
    logger.info(f"Walk-Forward 耗时: {elapsed:.0f}s ({elapsed/60:.1f}min)")
    
    summary = {
        'search_space': search_space,
        'windows': wf_results,
        'avg_train_sharpe': round(avg_train, 4),
        'avg_test_sharpe': round(avg_test, 4),
        'test_positive_rate': round(test_positive, 4),
        'production_params': PROD_PARAMS,
    }
    
    _json_dump(summary, os.path.join(OUTPUT_DIR, 'fullcycle_wf_results.json'))
    return summary


# ============================================================
# 分析 2: 参数敏感度 全周期重做
# ============================================================
def run_sensitivity_full():
    logger.info("\n" + "=" * 70)
    logger.info("分析2: 参数敏感度 全周期重做")
    logger.info("=" * 70)

    all_results = {}
    t0 = time.time()

    for param_name, values in SENSITIVITY_SWEEP.items():
        logger.info(f"\n--- 扫描参数: {param_name} ---")
        results = []
        for val in values:
            cfg = _make_config_full(BT_START, None, **{param_name: val})
            r = _run_backtest_full(cfg, skip_cache_refresh=True)
            results.append({'value': val, **r})
            logger.info(f"  {param_name}={val}: Sharpe={r['sharpe']:.4f}, Return={r['total_return']:.2%}, "
                        f"DD={r['max_drawdown']:.2%}, Trades={r['trade_count']}")

        sharpe_vals = [r['sharpe'] for r in results]
        nonzero = [s for s in sharpe_vals if s != 0]
        cv = np.std(nonzero) / abs(np.mean(nonzero)) if len(nonzero) > 1 and np.mean(nonzero) != 0 else 0

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
        logger.info(f"  高原检测: {'是' if is_plateau else '否'} - {len(near_best)}/{len(results)}")

    elapsed = time.time() - t0
    logger.info(f"\n敏感度分析耗时: {elapsed:.0f}s ({elapsed/60:.1f}min)")
    _json_dump(all_results, os.path.join(OUTPUT_DIR, 'fullcycle_sensitivity.json'))
    return all_results


# ============================================================
# 分析 3: 网格搜索 全周期重做
# ============================================================
def _composite_score(sharpe, max_dd, calmar, trade_count):
    if trade_count < 5: return -999
    dd_penalty = max(0, max_dd - 0.40) * 5
    return sharpe - dd_penalty


def run_grid_search_full(sensitivity_results=None):
    logger.info("\n" + "=" * 70)
    logger.info("分析3: 网格搜索 全周期重做")
    logger.info("=" * 70)

    rn_vals = GRID_RANGES_FULL['roc_n']
    buy_vals = GRID_RANGES_FULL['buy_roc_edge']
    tk_vals = GRID_RANGES_FULL['top_k']
    rm_vals = GRID_RANGES_FULL['roc_m']
    smre_vals = GRID_RANGES_FULL['sell_ma_roc_edge']

    combos = [(rn, rm, buy, smre, tk) for rn in rn_vals for rm in rm_vals
              for buy in buy_vals for smre in smre_vals for tk in tk_vals]
    logger.info(f"总组合数: {len(combos)}")

    grid_results = []
    t0 = time.time()
    for i, (rn, rm, buy, smre, tk) in enumerate(combos):
        config = _make_config_full(BT_START, None, roc_n=rn, roc_m=rm, buy_roc_edge=buy,
                                   sell_ma_roc_edge=smre, top_k=tk)
        r = _run_backtest_full(config, skip_cache_refresh=True)
        r['roc_n'] = rn; r['roc_m'] = rm; r['buy_roc_edge'] = buy
        r['sell_ma_roc_edge'] = smre; r['top_k'] = tk
        r['composite'] = round(_composite_score(r['sharpe'], r['max_drawdown'], r['calmar'], r['trade_count']), 4)
        grid_results.append(r)

        if (i + 1) % 5 == 0:
            elapsed = time.time() - t0
            eta = elapsed / (i + 1) * (len(combos) - i - 1)
            logger.info(f"  进度: {i+1}/{len(combos)} ({elapsed:.0f}s, ETA {eta:.0f}s)")

    grid_results.sort(key=lambda x: x['composite'], reverse=True)

    logger.info(f"\nTop 10 参数组合:")
    logger.info(f"{'roc_n':>6} {'roc_m':>6} {'buy':>6} {'smre':>6} {'top_k':>6} {'Sharpe':>8} {'Return':>8} {'DD':>8} {'Score':>8}")
    for r in grid_results[:10]:
        logger.info(f"{r['roc_n']:>6} {r['roc_m']:>6} {r['buy_roc_edge']:>6.1f} {r['sell_ma_roc_edge']:>6.1f} "
                    f"{r['top_k']:>6} {r['sharpe']:>8.4f} {r['total_return']:>8.2%} {r['max_drawdown']:>8.2%} {r['composite']:>8.4f}")

    elapsed = time.time() - t0
    logger.info(f"网格搜索耗时: {elapsed:.0f}s ({elapsed/60:.1f}min)")
    _json_dump(grid_results, os.path.join(OUTPUT_DIR, 'fullcycle_gridsearch.json'))
    return grid_results


# ============================================================
# 分析 4: 成本敏感性 全周期重做
# ============================================================
def run_cost_sensitivity_full():
    logger.info("\n" + "=" * 70)
    logger.info("分析4: 成本敏感性 全周期重做")
    logger.info("=" * 70)

    results = []
    for scenario in COST_SCENARIOS:
        config = _make_config_full(BT_START, None)
        # 手动设置成本参数
        config = ROCConfig(
            start_date=BT_START,
            codes=TECH_GROWTH_CODES,
            benchmark_code=BENCHMARK,
            **{**PROD_PARAMS,
               'inverse_vol_weight': True, 'rsi_enhance_enabled': True, 'rsi_enhance_below': 60.0,
               'atr_expansion_filter_enabled': True, 'adx_trend_filter_enabled': True,
               'HIGH_WATERMARK_STOP': True, 'CUT_LOSS': True, 'STOP_SMALL_TRADE': True,
               'STRICT_BUY': False, 'ROC_MA_DIRECTION': False, 'ROC_CROSS_MAROC_SELL': False,
               'ts_momentum_enabled': False, 'multi_factor': False, 'voting_enabled': False,
               'guzhai_licha_enabled': False, 'CROWDED_SELL': False, 'BUY_AVERAGE': False,
               'REBALANCE': False, 'MA_PRICE_CROSS': False,
            }
        )

        data_feed = CachedDataFeed(source=SinaFinanceFeed(), cache_dir=DATA_DIR)
        strategy = ROCStrategy(config)
        resolver = RankingResolver(
            top_k=config.top_k,
            weight_method='inverse_vol',
            high_watermark_stop_edge=config.high_watermark_stop_edge,
            cut_loss_edge=config.cut_loss_edge,
        )
        executor = BacktestExecutor(
            initial_capital=config.initial_capital,
            commission_rate=scenario['commission_rate'],
            slippage=scenario['slippage'],
            stop_small_trade=True,
            skip_small_trade_limit=2000.0,
        )

        try:
            result = run_backtest(
                strategy=strategy, resolver=resolver, executor=executor, data_feed=data_feed,
                codes=config.codes, start=config.start_date, end=BT_END,
                benchmark_code=config.benchmark_code,
            )
        except Exception as e:
            logger.warning(f"成本情景 {scenario['name']} 异常: {e}")
            results.append({'scenario': scenario['name'], 'error': str(e)})
            continue

        if not result or not result.get('net_values'):
            results.append({'scenario': scenario['name'], 'sharpe': 0, 'total_return': 0, 'max_drawdown': 0})
            continue

        nv = np.array([v['net_value'] for v in result['net_values']])
        daily_returns = np.diff(nv) / nv[:-1]
        sharpe = np.mean(daily_returns) / np.std(daily_returns) * np.sqrt(252) if np.std(daily_returns) > 0 else 0
        total_return = nv[-1] / nv[0] - 1
        max_dd = 0; peak = nv[0]
        for v in nv:
            if v > peak: peak = v
            dd = (peak - v) / peak
            if dd > max_dd: max_dd = dd
        trade_count = len([t for t in result.get('trade_log', []) if t['action'] == 'sell'])

        r = {
            'scenario': scenario['name'],
            'commission_rate': scenario['commission_rate'],
            'slippage': scenario['slippage'],
            'sharpe': round(sharpe, 4),
            'total_return': round(total_return, 4),
            'max_drawdown': round(max_dd, 4),
            'trade_count': trade_count,
            'total_commission': round(executor.total_commission, 2),
            'total_slippage': round(executor.total_slippage, 2),
        }
        results.append(r)
        logger.info(f"  {scenario['name']}: Sharpe={sharpe:.4f}, Return={total_return:.2%}, "
                    f"DD={max_dd:.2%}, 佣金={executor.total_commission:.0f}, 滑点={executor.total_slippage:.0f}")

    _json_dump(results, os.path.join(OUTPUT_DIR, 'fullcycle_cost.json'))
    return results


# ============================================================
# 分析 5: IC/ICIR 矩阵扫描 全周期重做
# ============================================================
def run_ic_analysis_full():
    logger.info("\n" + "=" * 70)
    logger.info("分析5: IC/ICIR 矩阵扫描 (2018-2026全周期)")
    logger.info("=" * 70)

    data_feed = CachedDataFeed(source=SinaFinanceFeed(), cache_dir=DATA_DIR)
    lookback_periods = [5, 10, 15, 22, 44, 66, 120]
    holding_periods = [5, 10, 22, 44, 66]

    from quantforge.core.data_feed import DataRequest
    request = DataRequest(
        codes=TECH_GROWTH_CODES,
        start=BT_START,
        end=BT_END,
        data_type="daily_k",
    )
    response = data_feed.get_data(request)

    all_data = {}
    for code, df in response.bar_data.items():
        if df is not None and not df.empty and 'close' in df.columns:
            df = df.sort_values('date')
            all_data[code] = df

    logger.info(f"成功加载 {len(all_data)}/{len(TECH_GROWTH_CODES)} 只ETF数据")

    ic_matrix = {}
    icir_matrix = {}

    for lb in lookback_periods:
        for hp in holding_periods:
            ic_vals = []
            for code, df in all_data.items():
                if len(df) < lb + hp + 5: continue
                closes = df['close'].values
                for t in range(lb, len(closes) - hp):
                    past_close = closes[t - lb]
                    if past_close <= 0: continue
                    roc = (closes[t] - past_close) / past_close * 100
                    future_close = closes[t + hp]
                    if closes[t] <= 0: continue
                    future_ret = (future_close - closes[t]) / closes[t]
                    ic_vals.append((roc, future_ret))

            if len(ic_vals) < 2:
                ic_matrix[f"{lb}x{hp}"] = 0
                icir_matrix[f"{lb}x{hp}"] = 0
                continue

            rocs = np.array([x[0] for x in ic_vals])
            rets = np.array([x[1] for x in ic_vals])

            # Spearman rank correlation
            from scipy.stats import spearmanr
            ic, pval = spearmanr(rocs, rets)

            ic_matrix[f"{lb}x{hp}"] = round(ic, 4)
            # ICIR = mean IC / std IC, approximated by daily IC stability
            icir_matrix[f"{lb}x{hp}"] = round(ic / max(0.01, np.std(rets)), 4)

            logger.info(f"  ROC({lb}), 持有{hp}天: IC={ic:.4f}")

    ic_result = {
        'lookback_periods': lookback_periods,
        'holding_periods': holding_periods,
        'ic_matrix': ic_matrix,
        'icir_matrix': icir_matrix,
        'date_range': f"{BT_START} ~ {BT_END}",
    }

    _json_dump(ic_result, os.path.join(OUTPUT_DIR, 'fullcycle_ic_analysis.json'))
    return ic_result


# ============================================================
# 生成对比报告
# ============================================================
def generate_report(wf_results, sensitivity_results, grid_results, cost_results, ic_result):
    logger.info("\n" + "=" * 70)
    logger.info("生成全周期分析报告")
    logger.info("=" * 70)

    lines = []
    lines.append("# 全周期参数稳健性重验证报告 (v2 - 真正Walk-Forward)")
    lines.append(f"\n生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"回测区间: {BT_START} ~ {BT_END}")
    lines.append(f"标的池: TECH_GROWTH_CODES (33只科技/成长ETF)")
    lines.append(f"生产基线: roc_n=22, buy_roc_edge=20.0, top_k=5 (tech_growth.json)")
    lines.append("")

    # ---- Walk-Forward ----
    lines.append("---")
    lines.append("## 一、Walk-Forward (真正训练+验证)")
    lines.append("")
    lines.append("每个窗口在训练区间上网格搜索最优参数组合，然后将最优参数应用于验证区间。")
    lines.append("")
    lines.append(f"搜索空间: roc_n={wf_results['search_space']['roc_n']}, "
                f"buy={wf_results['search_space']['buy_roc_edge']}, top_k={wf_results['search_space']['top_k']}")
    lines.append("")

    lines.append("| 窗口 | 训练区间 | 验证区间 | 训练最优参数 | 训练Sharpe | 验证Sharpe | 验证收益 | 验证DD |")
    lines.append("|------|---------|---------|-------------|-----------|-----------|---------|--------|")
    for w in wf_results['windows']:
        bp = w['best_train_params']
        params_str = f"roc_n={bp['roc_n']},buy={bp['buy_roc_edge']},top_k={bp['top_k']}"
        lines.append(f"| {w['window']} | {w['train_period']} | {w['test_period']} | {params_str} | "
                    f"{w['train_sharpe']:.4f} | {w['test_sharpe']:.4f} | "
                    f"{w['test_return']:.2%} | {w['test_dd']:.2%} |")
    lines.append("")

    lines.append(f"**Walk-Forward 汇总**:")
    lines.append(f"- 平均训练Sharpe: {wf_results['avg_train_sharpe']:.4f}")
    lines.append(f"- 平均验证Sharpe: {wf_results['avg_test_sharpe']:.4f}")
    lines.append(f"- 验证正Sharpe率: {wf_results['test_positive_rate']:.0%}")
    lines.append("")

    # ---- 敏感度 ----
    lines.append("---")
    lines.append("## 二、参数敏感度 全周期")
    lines.append("")
    for param_name, data in sensitivity_results.items():
        lines.append(f"### {param_name}")
        lines.append("")
        lines.append(f"| 值 | Sharpe | 收益率 | 最大回撤 | 交易次数 |")
        lines.append(f"|----|--------|--------|----------|----------|")
        for r in data['data']:
            lines.append(f"| {r['value']} | {r['sharpe']:.4f} | {r['total_return']:.2%} | "
                        f"{r['max_drawdown']:.2%} | {r['trade_count']} |")
        lines.append("")
        lines.append(f"- **稳定性(CV):** {data['cv']:.3f} → {'**稳健**' if data['is_robust'] else '_敏感_'}")
        lines.append(f"- **最优值:** {data['best_value']} (Sharpe={data['best_sharpe']:.4f})")
        lines.append(f"- **参数高原:** {'**是**' if data['is_plateau'] else '_否(尖峰风险)_'} "
                    f"({data['plateau_count']}/{len(data['data'])}个值接近最优)")
        lines.append("")

    # ---- 网格搜索 ----
    lines.append("---")
    lines.append("## 三、网格搜索 Top 10")
    lines.append("")
    lines.append("| 排名 | roc_n | roc_m | buy | top_k | Sharpe | 收益率 | 最大回撤 | Calmar | 综合 |")
    lines.append("|------|-------|-------|-----|-------|--------|--------|----------|--------|------|")
    for i, r in enumerate(grid_results[:10]):
        lines.append(f"| {i+1} | {r['roc_n']} | {r['roc_m']} | {r['buy_roc_edge']:.1f} | {r['top_k']} | "
                    f"{r['sharpe']:.4f} | {r['total_return']:.2%} | {r['max_drawdown']:.2%} | "
                    f"{r['calmar']:.4f} | {r['composite']:.4f} |")
    lines.append("")

    lines.append("### 综合评分公式")
    lines.append("```")
    lines.append("composite = Sharpe - max(0, DD - 40%) * 5")
    lines.append("```")
    lines.append("")

    # ---- 成本敏感性 ----
    lines.append("---")
    lines.append("## 四、成本敏感性 全周期")
    lines.append("")
    lines.append("| 情景 | 佣金率 | 滑点 | Sharpe | 收益率 | 回撤 | 交易次数 |")
    lines.append("|------|--------|------|--------|--------|------|----------|")
    for r in cost_results:
        if 'error' in r:
            lines.append(f"| {r['scenario']} | - | - | - | - | - | {r['error']} |")
        else:
            lines.append(f"| {r['scenario']} | {r['commission_rate']:.4f} | {r['slippage']:.3f} | "
                        f"{r['sharpe']:.4f} | {r['total_return']:.2%} | {r['max_drawdown']:.2%} | {r['trade_count']} |")
    lines.append("")

    # ---- IC/ICIR ----
    lines.append("---")
    lines.append("## 五、IC/ICIR 矩阵扫描 (2018-2026)")
    lines.append("")
    lookbacks = ic_result['lookback_periods']
    holdings = ic_result['holding_periods']
    ic_mat = ic_result['ic_matrix']

    lines.append("### IC 矩阵 (Spearman ρ)")
    lines.append("")
    header = "| 回看期 \\ 持有期 |" + " |".join(f" {h}天 " for h in holdings) + " |"
    lines.append(header)
    lines.append("|" + "|".join(["------"] * (len(holdings) + 1)) + "|")
    for lb in lookbacks:
        row = f"| ROC({lb}) |"
        for hp in holdings:
            key = f"{lb}x{hp}"
            val = ic_mat.get(key, 0)
            row += f" {val:.4f} |"
        lines.append(row)
    lines.append("")

    # 找最优
    best_ic_key = max(ic_mat, key=lambda k: abs(ic_mat[k]))
    best_ic_val = ic_mat[best_ic_key]
    lines.append(f"**最优IC**: ROC({best_ic_key}) = {best_ic_val:.4f}")
    lines.append("")

    report_path = os.path.join(OUTPUT_DIR, '全周期参数稳健性报告.md')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    logger.info(f"报告已保存: {report_path}")
    return report_path


# ============================================================
# 主入口
# ============================================================
if __name__ == "__main__":
    t_total = time.time()

    # 支持增量运行：跳过已有结果的步骤
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--skip-wf', action='store_true', help='跳过 Walk-Forward')
    ap.add_argument('--skip-sensitivity', action='store_true', help='跳过敏感度')
    ap.add_argument('--skip-grid', action='store_true', help='跳过网格搜索')
    ap.add_argument('--skip-cost', action='store_true', help='跳过成本敏感性')
    ap.add_argument('--skip-ic', action='store_true', help='跳过 IC/ICIR')
    ap.add_argument('--skip-report', action='store_true', help='跳过报告生成')
    ap.add_argument('--only', type=str, default='', help='只运行指定步骤: wf,sens,grid,cost,ic,report')
    args = ap.parse_args()

    wf_path = os.path.join(OUTPUT_DIR, 'fullcycle_wf_results.json')
    sens_path = os.path.join(OUTPUT_DIR, 'fullcycle_sensitivity.json')
    grid_path = os.path.join(OUTPUT_DIR, 'fullcycle_gridsearch.json')
    cost_path = os.path.join(OUTPUT_DIR, 'fullcycle_cost.json')
    ic_path = os.path.join(OUTPUT_DIR, 'fullcycle_ic_analysis.json')

    # 如果指定了 --only，忽略 skip 标志
    if args.only:
        steps = set(args.only.split(','))
        skip_wf = 'wf' not in steps
        skip_sens = 'sens' not in steps
        skip_grid = 'grid' not in steps
        skip_cost = 'cost' not in steps
        skip_ic = 'ic' not in steps
        skip_report = 'report' not in steps
    else:
        skip_wf = args.skip_wf or os.path.exists(wf_path)
        skip_sens = args.skip_sensitivity or os.path.exists(sens_path)
        skip_grid = args.skip_grid or os.path.exists(grid_path)
        skip_cost = args.skip_cost or os.path.exists(cost_path)
        skip_ic = args.skip_ic or os.path.exists(ic_path)
        skip_report = args.skip_report

    wf_results = None
    sensitivity_results = None
    grid_results = None
    cost_results = None
    ic_result = None

    # 预刷新缓存（单线程一次，后续回测均从磁盘读取，避免并发Sina请求）
    need_bt = not skip_wf or not skip_sens or not skip_grid or not skip_cost
    if need_bt:
        _pre_refresh_cache()

    # 分析1: Walk-Forward
    if not skip_wf:
        logger.info("\n\n>>> 分析1: Walk-Forward (真正训练+验证, ~305次回测)")
        wf_results = run_walkforward_full()
    else:
        logger.info("分析1: Walk-Forward 跳过（已有结果）")
        if os.path.exists(wf_path):
            wf_results = json.load(open(wf_path, 'r', encoding='utf-8'))

    # 分析2: 敏感度
    if not skip_sens:
        logger.info("\n\n>>> 分析2: 参数敏感度 (需 ~65次回测)")
        sensitivity_results = run_sensitivity_full()
    else:
        logger.info("分析2: 参数敏感度 跳过（已有结果）")
        if os.path.exists(sens_path):
            sensitivity_results = json.load(open(sens_path, 'r', encoding='utf-8'))

    # 分析3: 网格搜索
    if not skip_grid:
        logger.info("\n\n>>> 分析3: 网格搜索 (需 ~60次回测)")
        grid_results = run_grid_search_full(sensitivity_results)
    else:
        logger.info("分析3: 网格搜索 跳过（已有结果）")
        if os.path.exists(grid_path):
            grid_results = json.load(open(grid_path, 'r', encoding='utf-8'))

    # 分析4: 成本敏感性
    if not skip_cost:
        logger.info("\n\n>>> 分析4: 成本敏感性 (需 ~5次回测)")
        cost_results = run_cost_sensitivity_full()
    else:
        logger.info("分析4: 成本敏感性 跳过（已有结果）")
        if os.path.exists(cost_path):
            cost_results = json.load(open(cost_path, 'r', encoding='utf-8'))

    # 分析5: IC/ICIR
    if not skip_ic:
        logger.info("\n\n>>> 分析5: IC/ICIR 矩阵扫描")
        ic_result = run_ic_analysis_full()
    else:
        logger.info("分析5: IC/ICIR 跳过（已有结果）")
        if os.path.exists(ic_path):
            ic_result = json.load(open(ic_path, 'r', encoding='utf-8'))

    # 生成报告
    if not skip_report:
        report_path = generate_report(wf_results, sensitivity_results, grid_results, cost_results, ic_result)
    else:
        report_path = "跳过"

    elapsed = time.time() - t_total
    logger.info(f"\n{'='*70}")
    logger.info(f"全周期分析完成!")
    logger.info(f"总耗时: {elapsed:.0f}s ({elapsed/60:.1f}min)")
    logger.info(f"报告: {report_path}")
    logger.info(f"{'='*70}")