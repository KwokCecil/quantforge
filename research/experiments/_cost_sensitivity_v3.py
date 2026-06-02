"""成本敏感性分析 v3 — 统一回测入口

5组成本参数组合：佣金率 × 滑点的全因子扫描。
使用 run_core_backtest 统一入口，确保与生产回测逻辑完全一致。

用法: .venv\Scripts\python.exe research\_cost_sensitivity_v3.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies.factory import create_config
from main_backtest import run_core_backtest
from core.data_feed import CachedDataFeed
from data_sources.sina_feed import SinaFinanceFeed
from loguru import logger

CONFIG_PRESET = "tech_growth"
CODES = [
    '159939', '159915', '159928', '159949', '159967', '510050', '510300',
    '510500', '512100', '512580', '512690', '512880', '515050', '515250',
    '515790', '516510', '518880', '561910', '562800', '563000', '588000',
    '588050', '588080', '588200', '588400',
    '159766', '159780', '159781', '159845', '159869', '159995', '512070', '515700',
]
BT_START = "2018-01-01"
BT_END = "2026-05-29"
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'sina')

SCENARIOS = [
    ("当前(万2.5)",     0.00025, 0.001),
    ("万1.5",           0.00015, 0.001),
    ("万1.0",           0.00010, 0.001),
    ("万2.5+滑点0.2%",  0.00025, 0.002),
    ("万5+滑点0.2%",    0.00050, 0.002),
]


def pre_refresh_cache():
    logger.info("预刷新缓存: 33只ETF, 单线程...")
    t0 = time.time()
    data_feed = CachedDataFeed(source=SinaFinanceFeed(), cache_dir=DATA_DIR)
    data_feed.update_cache(codes=CODES, data_type='fund', start=BT_START, end=BT_END)
    logger.info(f"预刷新完成: {time.time() - t0:.0f}s")


def run_scenario(label, commission_rate, slippage):
    config = create_config("roc_momentum", CONFIG_PRESET)
    config.start_date = BT_START
    config.end_date = BT_END
    result = run_core_backtest(config, skip_cache_refresh=True,
                               commission_rate=commission_rate, slippage=slippage)
    if result is None:
        return {'label': label, 'error': True}
    return {
        'label': label,
        'sharpe': result['sharpe'],
        'sortino': result['sortino'],
        'total_return': result['total_return'],
        'max_drawdown': result['max_drawdown'],
        'calmar': result['calmar'],
        'trade_count': result['trade_count'],
        'total_commission': result['total_commission'],
    }


def main():
    pre_refresh_cache()

    results = []
    for label, comm, slip in SCENARIOS:
        logger.info(f"运行: {label} (佣金={comm}, 滑点={slip})")
        r = run_scenario(label, comm, slip)
        results.append(r)
        if r.get('error'):
            logger.error(f"  {label}: 回测失败!")
        else:
            logger.info(f"  Sharpe={r['sharpe']:.4f}, 收益={r['total_return']*100:.1f}%, "
                        f"DD={r['max_drawdown']*100:.2f}%, 交易={r['trade_count']}, "
                        f"佣金={r['total_commission']:.2f}")

    print("\n" + "=" * 80)
    print("成本敏感性分析结果 (v3 — 统一回测入口)")
    print("=" * 80)
    print(f"{'情景':<20} {'佣金率':>8} {'滑点':>8} {'Sharpe':>8} {'收益':>8} {'DD':>8} {'交易':>6} {'总佣金':>10}")
    print("-" * 80)
    for r in results:
        if r.get('error'):
            print(f"{r['label']:<20} {'ERROR':>8}")
        else:
            print(f"{r['label']:<20} {SCENARIOS[results.index(r)][1]:>8.5f} "
                  f"{SCENARIOS[results.index(r)][2]:>8.3f} "
                  f"{r['sharpe']:>8.4f} {r['total_return']*100:>7.1f}% "
                  f"{r['max_drawdown']*100:>7.2f}% {r['trade_count']:>6} "
                  f"{r['total_commission']:>10.2f}")

    baseline = results[0]
    if not baseline.get('error'):
        print("\n--- 相对基线变化 ---")
        for r in results[1:]:
            if r.get('error'):
                continue
            d_sharpe = r['sharpe'] - baseline['sharpe']
            d_return = (r['total_return'] - baseline['total_return']) * 100
            d_dd = (r['max_drawdown'] - baseline['max_drawdown']) * 100
            print(f"  {r['label']:<20} Sharpe {d_sharpe:+.4f}, "
                  f"收益 {d_return:+.1f}%, DD {d_dd:+.2f}%")


if __name__ == "__main__":
    main()