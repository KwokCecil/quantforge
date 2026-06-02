"""按AC自相关筛选标的池 + 大盘过滤，重新回测"""
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import pandas as pd
import numpy as np
from quantforge.tools.json_tool import read_fund_data

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "sina")
from quantforge.config.universes.code_names import ETF_NAME_MAP

# === 计算每只ETF的AC（日收益自相关）===
print("=== 全60只ETF 自相关排名 ===\n")
results = []
for fname in os.listdir(CACHE_DIR):
    if not fname.startswith('fund_data_') or not fname.endswith('.json'):
        continue
    code = fname.replace('fund_data_', '').replace('.json', '')
    data = read_fund_data(CACHE_DIR, code)
    if not data:
        continue
    df = pd.DataFrame(data)
    if 'close' not in df.columns or len(df) < 252:
        continue
    close = pd.to_numeric(df['close'], errors='coerce').dropna()
    if len(close) < 252:
        continue
    ret_1d = close.pct_change(1).dropna()
    ret_20d = close.pct_change(20).dropna()
    ac = ret_1d.autocorr()
    sharpe_20 = ret_20d.mean() / ret_20d.std() * np.sqrt(12) if ret_20d.std() > 0 else 0
    name = ETF_NAME_MAP.get(code, code)
    results.append((code, name, ac, sharpe_20))

df_ac = pd.DataFrame(results, columns=['code','name','ac','sharpe_20'])
df_ac = df_ac.sort_values('ac')

print(f"{'代码':<10} {'名称':<14} {'AC':>7} {'20d Sharpe':>10} {'分类':>10}")
print("-" * 60)

momentum_codes = []
reversal_codes = []
neutral_codes = []

for _, row in df_ac.iterrows():
    if row['ac'] < -0.02:
        cat = "均值回归"
        reversal_codes.append(row['code'])
    elif row['ac'] > 0.02:
        cat = "动量型"
        momentum_codes.append(row['code'])
    else:
        cat = "中性"
        neutral_codes.append(row['code'])
    print(f"{row['code']:<10} {row['name']:<14} {row['ac']:>+7.4f} {row['sharpe_20']:>+10.3f} {cat:>10}")

print(f"\n均值回归（AC<-0.02）: {len(reversal_codes)}只")
print(f"中性（-0.02~0.02）: {len(neutral_codes)}只")
print(f"动量型（AC>0.02）: {len(momentum_codes)}只")

print(f"\n=== 排除均值回归后的动量池 ===")
print(f"排除: {[r for r in sorted(reversal_codes)]}")
print(f"保留: {len(momentum_codes) + len(neutral_codes)}只")

# === Baseline 在AC动量池上的盈亏统计 ===
with open('results/T025_baseline/run_20260509_152002/trades.json', 'r', encoding='utf-8') as f:
    records = json.load(f)

df_trades = pd.DataFrame(records)
df_trades['date'] = pd.to_datetime(df_trades['date'])
df_trades = df_trades.sort_values(['date', 'timestamp'])
df_trades['in_momentum_pool'] = df_trades['code'].apply(lambda c: c not in reversal_codes)

# 按是否在动量池统计
print(f"\n=== Baseline 交易按AC分池 ===")
for pool, label in [(True, '动量池'), (False, '均值回归池')]:
    mask = df_trades['in_momentum_pool'] == pool
    t = df_trades[mask]
    buys = (t['action'] == 'buy').sum()
    sells = (t['action'] == 'sell').sum()
    print(f"  {label}: 买{buys} 卖{sells}")

# FIFO盈亏
from collections import defaultdict
queues = defaultdict(list)
pnl_by_pool = {True: 0, False: 0}

for i, row in df_trades.iterrows():
    if row['action'] == 'buy':
        queues[row['code']].append((row['shares'], row['actual_price']))
    else:
        remaining = row['shares']
        pnl = 0.0
        while remaining > 0 and queues[row['code']]:
            held_shares, held_price = queues[row['code']][0]
            matched = min(remaining, held_shares)
            pnl += matched * (row['actual_price'] - held_price)
            if matched >= held_shares:
                queues[row['code']].pop(0)
            else:
                queues[row['code']][0] = (held_shares - matched, held_price)
            remaining -= matched
        pnl_by_pool[row['in_momentum_pool']] += pnl

print(f"\n  动量池总盈亏: {pnl_by_pool[True]:+.0f}")
print(f"  均值回归池总盈亏: {pnl_by_pool[False]:+.0f}")
