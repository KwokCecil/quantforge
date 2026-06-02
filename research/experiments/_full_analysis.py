"""T025 Baseline 完整交易分析 — FIFO盈亏 + 标的名称"""
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import pandas as pd
from collections import defaultdict

with open('results/T025_baseline/run_20260509_152002/trades.json','r',encoding='utf-8') as f:
    records = json.load(f)

df = pd.DataFrame(records)
df['date'] = pd.to_datetime(df['date'])
df = df.sort_values(['date', 'timestamp']).reset_index(drop=True)

# FIFO 计算每笔卖出的盈亏
queues = defaultdict(list)  # code -> [(shares, actual_price)]
sell_pnls = []

for i, row in df.iterrows():
    if row['action'] == 'buy':
        queues[row['code']].append((row['shares'], row['actual_price']))
        df.at[i, 'pnl'] = 0
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
        df.at[i, 'pnl'] = pnl

# === 标的总览（代码+名称+盈亏+胜率） ===
code_stats = defaultdict(lambda: {'name': '', 'buys': 0, 'sells': 0, 'pnl': 0, 'wins': 0, 'losses': 0})
for i, row in df.iterrows():
    if row['action'] == 'buy':
        code_stats[row['code']]['buys'] += 1
        if not code_stats[row['code']]['name']:
            code_stats[row['code']]['name'] = row.get('name', '')
    elif row['action'] == 'sell':
        code_stats[row['code']]['sells'] += 1
        p = row['pnl']
        code_stats[row['code']]['pnl'] += p
        if p > 0: code_stats[row['code']]['wins'] += 1
        elif p < 0: code_stats[row['code']]['losses'] += 1

print(f"=== T025 Baseline 交易总览 ({len(df)}笔) ===")
print(f"  买入: {(df['action']=='buy').sum()}  卖出: {(df['action']=='sell').sum()}")
print(f"  总盈亏: {df['pnl'].sum():+.0f}")
print()

print(f"{'代码':<10} {'名称':<14} {'买入':>4} {'卖出':>4} {'盈亏':>8} {'胜':>3} {'负':>3}")
print("-" * 60)
for code in sorted(code_stats.keys()):
    s = code_stats[code]
    print(f"{code:<10} {s['name']:<14} {s['buys']:>4} {s['sells']:>4} {s['pnl']:>+8.0f} {s['wins']:>3} {s['losses']:>3}")

# === 年度盈亏 ===
df['year'] = df['date'].dt.year
print(f"\n=== 年度盈亏 ===")
for yr, grp in df.groupby('year'):
    pnl = grp['pnl'].sum()
    buys = (grp['action']=='buy').sum()
    sells = (grp['action']=='sell').sum()
    print(f"  {yr}: 买{buys:>2} 卖{sells:>2}  盈亏{pnl:>+10.0f}")

# === 卖出原因统计 ===
print(f"\n=== 卖出原因汇总 ===")
sell_df = df[df['action'] == 'sell'].copy()
# 归类
def classify(reason):
    if '成本止损' in reason: return '成本止损'
    if '高水位' in reason: return '高水位止损'
    if 'ROC=' in reason: return 'ROC信号卖出'
    return reason

sell_df['category'] = sell_df['reason'].apply(classify)
for cat, grp in sell_df.groupby('category'):
    count = len(grp)
    avg_loss = grp['pnl'].mean()
    total = grp['pnl'].sum()
    print(f"  {cat}: {count}笔  总盈亏{total:>+10.0f}  平均{avg_loss:>+8.0f}/笔")

# === 最大盈亏单笔 ===
print(f"\n=== 最大亏损 Top10 ===")
worst = sell_df.sort_values('pnl').head(10)
for i, row in worst.iterrows():
    print(f"  {row['date'].strftime('%Y-%m-%d')} 卖 {row['code']:<8} {row['name']:<14} "
          f"px={row['price']:.3f} 盈亏={row['pnl']:+.0f}  [{row['reason'][:50]}")

print(f"\n=== 最大盈利 Top10 ===")
best = sell_df.sort_values('pnl', ascending=False).head(10)
for i, row in best.iterrows():
    print(f"  {row['date'].strftime('%Y-%m-%d')} 卖 {row['code']:<8} {row['name']:<14} "
          f"px={row['price']:.3f} 盈亏={row['pnl']:+.0f}  [{row['reason'][:50]}")

# === 持仓天数统计 ===
print(f"\n=== 持仓天数统计（买→卖间隔，按卖出日期对应买入日期）===")
hold_days = []
buy_dates = {}  # code -> [ (date, shares, price) ]
for i, row in df.iterrows():
    if row['action'] == 'buy':
        if row['code'] not in buy_dates:
            buy_dates[row['code']] = []
        buy_dates[row['code']].append((row['date'], row['shares'], row['actual_price']))
    else:
        remaining = row['shares']
        while remaining > 0 and buy_dates.get(row['code']):
            bd, bs, bp = buy_dates[row['code']][0]
            matched = min(remaining, bs)
            hold_days.append((row['date'] - bd).days)
            if matched >= bs:
                buy_dates[row['code']].pop(0)
            else:
                buy_dates[row['code']][0] = (bd, bs - matched, bp)
            remaining -= matched

if hold_days:
    import numpy as np
    hd = np.array(hold_days)
    print(f"  平均: {hd.mean():.0f}天  中位: {np.median(hd):.0f}天  最短: {hd.min()}  最长: {hd.max()}")
