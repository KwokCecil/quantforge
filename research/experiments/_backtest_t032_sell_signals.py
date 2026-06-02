r"""T032 辅助信号卖出端 — AB回测 + 被打断交易对比

运行: $env:PYTHONPATH='e:\JuJu\TraeProjects\量化工程'; .venv/Scripts/python.exe research/_backtest_t032_sell_signals.py

产出: 回测对比表 + 被打断交易盈亏差分析
"""
import os, sys, copy, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collections import defaultdict
import pandas as pd
import numpy as np
from loguru import logger

from quantforge.core.data_feed import CachedDataFeed
from quantforge.core.executor import BacktestExecutor
from quantforge.core.resolver import RankingResolver
from quantforge.data_sources.sina_feed import SinaFinanceFeed
from quantforge.core.backtest_core import run_backtest
from quantforge.strategies.factory import create_config
from quantforge.strategies.roc_momentum import ROCStrategy

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
logger.remove()
logger.add(sys.stdout, level='WARNING', format='{time:HH:mm:ss} | {level:<7} | {message}')

# 基线：tech_growth 配置
BASE_CONFIG = create_config("roc_momentum", "tech_growth")

def run_one(label, overrides):
    """运行一组配置，返回 (annual_return, sharpe, max_dd, total_return, n_trades, trade_log)。"""
    cfg = copy.deepcopy(BASE_CONFIG)
    for k, v in overrides.items():
        setattr(cfg, k, v)

    strategy = ROCStrategy(cfg)

    resolver = RankingResolver(
        top_k=cfg.top_k,
        weight_method='equal',
        high_watermark_stop_edge=cfg.high_watermark_stop_edge if cfg.HIGH_WATERMARK_STOP else float('inf'),
        cut_loss_edge=cfg.cut_loss_edge if cfg.CUT_LOSS else float('inf'),
        top_k_sell=False,
    )
    executor = BacktestExecutor(initial_capital=cfg.initial_capital, rebalance=False)

    data_feed = CachedDataFeed(source=SinaFinanceFeed(), cache_dir=os.path.join(_BASE, 'data', 'sina'))
    start = cfg.start_date or "2018-01-01"
    end = cfg.end_date or "2026-05-20"
    data_feed.update_cache(codes=cfg.codes, data_type='daily_k', start=start, end=end)

    results = run_backtest(
        strategy=strategy, resolver=resolver, executor=executor,
        data_feed=data_feed, codes=list(cfg.codes), start=start, end=end, benchmark_code=None,
    )
    if not results or not results.get('net_values'):
        return None

    nv = pd.DataFrame(results['net_values'])
    tr = (nv['total_value'].iloc[-1] / cfg.initial_capital) - 1
    y = len(nv) / 252
    ar = (1 + tr) ** (1 / y) - 1 if y > 0 else 0
    dret = nv['net_value'].pct_change().dropna()
    sr = (dret.mean() / dret.std()) * np.sqrt(252) if dret.std() > 0 else 0
    nv['peak'] = nv['total_value'].cummax()
    dd = ((nv['peak'] - nv['total_value']) / nv['peak']).max()
    sells = [t for t in results['trade_log'] if t['action'] == 'sell']
    return ar, tr, sr, dd, len(sells), results['trade_log']


def compute_pnl(trade_log):
    """FIFO 计算逐笔盈亏。返回 [(code, pnl, entry_price, exit_price), ...]"""
    bq = defaultdict(list)
    pnl_list = []
    for t in trade_log:
        if t['action'] == 'buy':
            bq[t['code']].append({'s': t['shares'], 'p': t['actual_price'], 'date': t['date']})
        elif t['action'] == 'sell':
            rem = t['shares']
            pnl = 0
            matched = []
            for h in list(bq.get(t['code'], [])):
                if rem <= 0:
                    break
                m = min(rem, h['s'])
                pnl += m * (t['actual_price'] - h['p'])
                matched.append({'shares': m, 'entry_price': h['p']})
                h['s'] -= m
                rem -= m
                if h['s'] <= 0:
                    bq[t['code']].pop(0)
            entry_p = matched[0]['entry_price'] if matched else 0
            pnl_list.append((t['code'], pnl, entry_p, t['actual_price'], t.get('reason', '')))
    return pnl_list


def analyze_interrupted(sell_reasons_log, baseline_log):
    """分析被辅助信号打断的交易。

    在 sell_reasons_log 中找被辅助信号（放量/ATR/背离/RSI）打断的卖出，
    在 baseline_log 中找到同一笔买入对应的卖出，比较盈亏差。
    """
    # 解析 baseline 的 buys → 按 code+entry_date 建索引
    baseline_buys = defaultdict(list)
    for t in baseline_log:
        if t['action'] == 'buy':
            baseline_buys[(t['code'], t['date'][:10])].append(t)

    # 扫描 sell_reasons 中被打断的
    interrupted = []
    sell_signal_buys = defaultdict(list)
    for t in sell_reasons_log:
        if t['action'] == 'buy':
            sell_signal_buys[(t['code'], t['date'][:10])].append(t)

    for t in sell_reasons_log:
        if t['action'] != 'sell':
            continue
        reason = t.get('reason', '')
        # 检查是否被辅助信号打断
        is_interrupted = any(kw in reason for kw in ['放量卖出', 'ATR扩张卖出', 'MACD顶背离卖出', 'RSI>80止盈'])
        if not is_interrupted:
            continue

        code = t['code']
        date = t['date'][:10]
        shares = t['shares']
        price = t['actual_price']

        # 在 baseline 中找到同一标的同一天附近有没有另外的卖出
        # baseline 同一个买入可能在不同时间卖出
        interrupted.append({
            'code': code, 'date': date, 'price': price,
            'shares': shares, 'reason': reason,
        })
    return interrupted


# ============================================================
logger.warning("T032 卖出信号 AB 回测\n")

# 先跑 baseline（需要网络，可能较慢）
logger.warning("运行 baseline...")
bl = run_one("baseline", {})
if not bl:
    logger.error("Baseline 回测失败"); sys.exit(1)
_, _, _, _, _, baseline_log = bl

logger.warning("baseline 完成，运行各配置...")

configs = [
    ("baseline", {}),
    ("vol_sell_1.5", {"volume_sell_enabled": True, "volume_sell_spike_ratio": 1.5}),
    ("atr_sell_1.5", {"atr_expansion_sell_enabled": True}),
    ("macd_div_sell", {"macd_divergence_sell_enabled": True}),
    ("rsi_sell_80", {"rsi_sell_enabled": True}),
    ("vol+atr", {"volume_sell_enabled": True, "atr_expansion_sell_enabled": True}),
    ("vol+atr+macd", {"volume_sell_enabled": True, "atr_expansion_sell_enabled": True, "macd_divergence_sell_enabled": True}),
    ("full+rsi", {"volume_sell_enabled": True, "atr_expansion_sell_enabled": True,
                  "macd_divergence_sell_enabled": True, "rsi_sell_enabled": True}),
]

print(f"\n{'配置':<18} {'年化':>8} {'总收益':>8} {'Sharpe':>7} {'回撤':>7} {'交易':>5}")
print("-" * 65)

results_cache = {}
for label, overrides in configs:
    if label == "baseline":
        ar, tr, sr, dd, nt, _ = bl
    else:
        logger.warning(f"  运行 {label}...")
        ret = run_one(label, overrides)
        if not ret:
            print(f"{label:<18} {'FAIL':>8}")
            continue
        ar, tr, sr, dd, nt, trade_log = ret
        results_cache[label] = (trade_log, overrides)
    print(f"{label:<18} {ar:>7.1%} {tr:>7.1%} {sr:>+6.2f} {dd:>6.1%} {nt:>5d}")

# ============================================================
# 被打断交易对比分析
# ============================================================
print(f"\n=== 被打断交易对比分析 ===")

for label, (trade_log, overrides) in sorted(results_cache.items()):
    interrupted = analyze_interrupted(trade_log, baseline_log)
    if not interrupted:
        print(f"  {label}: 无辅助信号触发的卖出（信号密度极低 / 未触发）")
        continue

    # 计算这些被打断交易的总盈亏
    bq = defaultdict(list)
    total_pnl = 0
    interrupt_count = 0
    sell_sig_keys = {'放量卖出', 'ATR扩张卖出', 'MACD顶背离卖出', 'RSI>80止盈'}
    for t in trade_log:
        if t['action'] == 'buy':
            bq.setdefault(t['code'], []).append({'s': t['shares'], 'p': t['actual_price']})
        elif t['action'] == 'sell':
            rsn = t.get('reason', '')
            if any(kw in rsn for kw in sell_sig_keys):
                rem = t['shares']
                pnl = 0
                for h in list(bq.get(t['code'], [])):
                    if rem <= 0: break
                    m = min(rem, h['s']); pnl += m * (t['actual_price'] - h['p'])
                    h['s'] -= m; rem -= m
                    if h['s'] <= 0: bq[t['code']].pop(0)
                total_pnl += pnl
                interrupt_count += 1

    print(f"  {label}: {interrupt_count}笔被打断, 被打断交易盈亏={total_pnl:+.0f}")

# ============================================================
# 被打断 vs baseline 同一笔买入的盈亏差
# ============================================================
print(f"\n=== 被打断 vs 若等到原始ROC退出 的盈亏差 ===")
# 在 baseline 中，按买入批次+退出顺序匹配
for label, (trade_log, overrides) in sorted(results_cache.items()):
    # 收集 baseline 中所有 sell：按 code 和 FIFO 顺序
    bl_sells_by_code = defaultdict(list)
    for t in baseline_log:
        if t['action'] == 'sell':
            bl_sells_by_code[t['code']].append(t)

    # 收集 sell_config 中被辅助信号打断的 sell
    interrupted_sells = []
    sell_sig_keys = {'放量卖出', 'ATR扩张卖出', 'MACD顶背离卖出', 'RSI>80止盈'}
    for t in trade_log:
        if t['action'] == 'sell' and any(kw in t.get('reason', '') for kw in sell_sig_keys):
            interrupted_sells.append(t)

    if not interrupted_sells:
        continue

    pnl_diff = 0
    n = 0
    for t in interrupted_sells:
        code = t['code']
        bl_sells = bl_sells_by_code.get(code, [])
        # 这个比较不精确（因为 baseline 可能多成交了其他标的），
        # 但作为近似分析：如果这个信号下少了一笔买入+卖出，差多少
        n += 1

    print(f"  {label}: {len(interrupted_sells)}笔被打断 (需逐笔精确对比, 此处仅统计数量)")

print()
logger.warning("完成")
