import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from datetime import datetime

import numpy as np
import pandas as pd

from quantforge.core.data_feed import CachedDataFeed, DataRequest
from quantforge.data_sources.sina_feed import SinaFinanceFeed
from quantforge.indicators.technical import ROCIndicator, RSIIndicator, MACDIndicator
from quantforge.strategies.factory import create_config

# === 配置 ===
PRESET = "tech_growth"
RESULT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results")

config = create_config("roc_momentum", PRESET)
CODES = config.codes
START = config.start_date
END = config.end_date
BUY_ROC_EDGE = config.buy_roc_edge
ROC_N = config.roc_n
ROC_M = config.roc_m

SELL_ROC_EDGES = list(range(1, 21))        # 1 ~ 20（跳过0，sell_roc=0退化为永不卖出）
SELL_MA_ROC_EDGES = [0, -3, -5, -8, -10]   # MAROC卖出阈值

CURRENT_SELL_ROC = config.sell_roc_edge
CURRENT_SELL_MA = config.sell_ma_roc_edge

print(f"配置: {PRESET}  标的: {len(CODES)}个  区间: {START} ~ {END}")
print(f"当前退出: sell_roc={CURRENT_SELL_ROC}  sell_ma_roc={CURRENT_SELL_MA}")
print(f"扫描范围: sell_roc x sell_ma_roc = {len(SELL_ROC_EDGES)} x {len(SELL_MA_ROC_EDGES)} = {len(SELL_ROC_EDGES)*len(SELL_MA_ROC_EDGES)} 组")

# === 数据加载（同 T028） ===
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
feed = CachedDataFeed(
    source=SinaFinanceFeed(),
    cache_dir=os.path.join(_BASE_DIR, "data", "sina"),
)
feed.update_cache(codes=CODES, data_type="daily_k", start=START, end=END)
response = feed.get_data(DataRequest(codes=CODES, data_type="daily_k", start=START, end=END))

roc_ind = ROCIndicator(n=ROC_N, m=ROC_M)
rsi_ind = RSIIndicator(n=14)
macd_ind = MACDIndicator(fast=12, slow=26, signal=9)

code_dfs = {}
for code in CODES:
    df = response.bar_data.get(code)
    if df is None or df.empty:
        continue
    df = df.copy()
    df = roc_ind.compute(df, n=ROC_N, m=ROC_M)
    df = rsi_ind.compute(df, n=14)
    df = macd_ind.compute(df, fast=12, slow=26, signal=9)
    code_dfs[code] = df

all_dates_set = set()
for df in code_dfs.values():
    all_dates_set.update(df['date'].tolist())
all_dates = sorted(all_dates_set)
date_to_idx = {d: i for i, d in enumerate(all_dates)}
N_DATES = len(all_dates)
N_CODES = len(CODES)

roc_mat = np.full((N_CODES, N_DATES), np.nan)
maroc_mat = np.full((N_CODES, N_DATES), np.nan)
rsi_mat = np.full((N_CODES, N_DATES), np.nan)
close_mat = np.full((N_CODES, N_DATES), np.nan)

for ci, code in enumerate(CODES):
    df = code_dfs.get(code)
    if df is None:
        continue
    for _, row in df.iterrows():
        d = row['date']
        if d in date_to_idx:
            di = date_to_idx[d]
            roc_mat[ci, di] = row.get('roc', np.nan)
            maroc_mat[ci, di] = row.get('maroc', np.nan)
            rsi_mat[ci, di] = row.get('rsi', np.nan)
            close_mat[ci, di] = row.get('close', np.nan)

# === 识别买入信号（只做一次） ===
signals = []

for di in range(N_DATES):
    for ci in range(N_CODES):
        roc_val = roc_mat[ci, di]
        if np.isnan(roc_val) or roc_val < BUY_ROC_EDGE:
            continue
        maroc_val = maroc_mat[ci, di]
        if np.isnan(maroc_val):
            continue
        signals.append({
            'date': all_dates[di],
            'code': CODES[ci],
            'roc': roc_val,
            'maroc': maroc_val,
            'rsi': rsi_mat[ci, di],
            'di': di,
            'ci': ci,
        })

print(f"\n共识别 {len(signals)} 个买入信号，开始扫描退出阈值...")

# === 扫描 ===
results = []

total_combos = len(SELL_ROC_EDGES) * len(SELL_MA_ROC_EDGES)
done = 0

for sell_roc in SELL_ROC_EDGES:
    for sell_ma_roc in SELL_MA_ROC_EDGES:
        done += 1
        returns = []
        holding_days = []
        is_win_list = []

        # 按阶段分组快照
        stage_returns = {"早期": [], "中期": [], "晚期": []}
        THRESHOLD_1_3 = BUY_ROC_EDGE * 1.3

        if done % 20 == 0:
            print(f"  进度: {done}/{total_combos} ({sell_roc=} {sell_ma_roc=})")

        for sig in signals:
            ci = sig['ci']
            di = sig['di']
            entry_close = close_mat[ci, di]
            exit_di = di

            # 阶段分类（基于买入时状态）
            maroc_at_entry = sig['maroc']
            prev_maroc = maroc_mat[ci, di - 1] if di > 0 else maroc_at_entry
            roc_at_entry = sig['roc']
            if roc_at_entry < THRESHOLD_1_3:
                stage = "早期"
            elif maroc_at_entry > prev_maroc:
                stage = "中期"
            else:
                stage = "晚期"

            for fwd_di in range(di + 1, N_DATES):
                fwd_roc = roc_mat[ci, fwd_di]
                fwd_maroc = maroc_mat[ci, fwd_di]
                if np.isnan(fwd_roc):
                    continue

                triggered = False

                if sell_roc > 0 and fwd_roc < sell_roc:
                    triggered = True

                if not triggered and sell_ma_roc < 0 and not np.isnan(fwd_maroc) and fwd_maroc < sell_ma_roc:
                    triggered = True

                if triggered:
                    exit_di = fwd_di
                    break

            if exit_di == di:
                exit_close = close_mat[ci, -1] if not np.isnan(close_mat[ci, -1]) else entry_close
            else:
                exit_close = close_mat[ci, exit_di] if not np.isnan(close_mat[ci, exit_di]) else entry_close

            ret = (exit_close / entry_close - 1) * 100 if entry_close > 0 else 0
            days = exit_di - di

            returns.append(ret)
            holding_days.append(days)
            is_win_list.append(ret > 0)
            stage_returns[stage].append(ret)

        arr = np.array(returns)
        results.append({
            'sell_roc_edge': sell_roc,
            'sell_ma_roc_edge': sell_ma_roc,
            'signal_count': len(signals),
            'win_rate': np.mean(is_win_list) * 100,
            'avg_return': np.mean(arr),
            'med_return': np.median(arr),
            'max_return': np.max(arr),
            'min_return': np.min(arr),
            'std_return': np.std(arr),
            'avg_hold_days': np.mean(holding_days),
            'hold_0_5': sum(1 for d in holding_days if 0 <= d <= 5) / max(len(holding_days), 1) * 100,
            'hold_5_10': sum(1 for d in holding_days if 5 < d <= 10) / max(len(holding_days), 1) * 100,
            'hold_10_20': sum(1 for d in holding_days if 10 < d <= 20) / max(len(holding_days), 1) * 100,
            'hold_20_40': sum(1 for d in holding_days if 20 < d <= 40) / max(len(holding_days), 1) * 100,
            'hold_40p': sum(1 for d in holding_days if d > 40) / max(len(holding_days), 1) * 100,
            'early_avg': np.mean(stage_returns["早期"]) if stage_returns["早期"] else 0,
            'mid_avg': np.mean(stage_returns["中期"]) if stage_returns["中期"] else 0,
            'late_avg': np.mean(stage_returns["晚期"]) if stage_returns["晚期"] else 0,
        })

df = pd.DataFrame(results)

# === 排名 ===
df['score'] = df['avg_return'] * df['win_rate'] / 100  # 综合分：盈亏 × 胜率

print("\n" + "=" * 80)
print("TOP 15（按盈亏×胜率综合分排序）")
print("=" * 80)

top15 = df.nlargest(15, 'score')
for _, row in top15.iterrows():
    marker = " ← 当前" if (row['sell_roc_edge'] == CURRENT_SELL_ROC and row['sell_ma_roc_edge'] == CURRENT_SELL_MA) else ""
    print(f"ROC<{row['sell_roc_edge']:>5.0f}  MAROC<{row['sell_ma_roc_edge']:>5.0f}  "
          f"胜率{row['win_rate']:>5.1f}%  盈亏{row['avg_return']:>+5.1f}%  "
          f"持有{row['avg_hold_days']:>4.0f}天  分{row['score']:>5.1f}{marker}")

# === 按 sell_roc_edge 聚合（取最优 sell_ma_roc） ===
print("\n" + "=" * 80)
print("sell_roc_edge 最优维度（每组取最优 sell_ma_roc）")
print("=" * 80)

agg = df.loc[df.groupby('sell_roc_edge')['score'].idxmax()].sort_values('sell_roc_edge')
for _, row in agg.iterrows():
    marker = " ← 当前" if (row['sell_roc_edge'] == CURRENT_SELL_ROC and row['sell_ma_roc_edge'] == CURRENT_SELL_MA) else ""
    print(f"ROC<{row['sell_roc_edge']:>5.0f}  +MAROC<{row['sell_ma_roc_edge']:>4.0f}  "
          f"胜率{row['win_rate']:>5.1f}%  盈亏{row['avg_return']:>+5.1f}%  "
          f"持有{row['avg_hold_days']:>4.0f}天  "
          f"分布0-5={row['hold_0_5']:.0f}% 5-10={row['hold_5_10']:.0f}% 10-20={row['hold_10_20']:.0f}% 20-40={row['hold_20_40']:.0f}% 40+={row['hold_40p']:.0f}%{marker}")

# === 当前 vs 最优 ===
print("\n" + "=" * 80)
print("当前值 vs 最优值对比")
print("=" * 80)

cur = df[(df['sell_roc_edge'] == CURRENT_SELL_ROC) & (df['sell_ma_roc_edge'] == CURRENT_SELL_MA)]
best_idx = df['score'].idxmax()
best = df.iloc[best_idx]

if not cur.empty:
    c = cur.iloc[0]
    print(f"{'指标':<20} {'当前(ROC<3)':>15} {'最优(ROC<{})'.format(int(best['sell_roc_edge'])):>15} {'变化':>10}")
    print("-" * 60)
    for key, label, fmt in [
        ('win_rate', '胜率(%)', '{:.1f}'), ('avg_return', '均盈亏(%)', '{:+.1f}'),
        ('med_return', '中位盈亏(%)', '{:+.1f}'), ('avg_hold_days', '均持有(天)', '{:.0f}'),
        ('min_return', '最大亏损(%)', '{:+.1f}'), ('score', '综合分', '{:.1f}'),
    ]:
        cv = c[key]
        bv = best[key]
        diff = bv - cv
        print(f"{label:<20} {fmt.format(cv):>15} {fmt.format(bv):>15} {diff:>+9.1f}")

# === sell_ma_roc 维度独立分析 ===
print("\n" + "=" * 80)
print("sell_ma_roc_edge 独立效果（sell_roc=最优值时各 sell_ma_roc 对比）")
print("=" * 80)

best_sell_roc = int(best['sell_roc_edge'])
ma_sub = df[df['sell_roc_edge'] == best_sell_roc].sort_values('sell_ma_roc_edge')
for _, row in ma_sub.iterrows():
    marker = ""
    print(f"MAROC<{row['sell_ma_roc_edge']:>4.0f}  "
          f"胜率{row['win_rate']:>5.1f}%  盈亏{row['avg_return']:>+5.1f}%  "
          f"持有{row['avg_hold_days']:>4.0f}天  分{row['score']:>5.1f}{marker}")

# === 持有天数分布随 sell_roc 变化 ===
print("\n" + "=" * 80)
print("关键发现：持有天数分布随 sell_roc 变化的'死区'（10-20天）比例")
print("=" * 80)

for _, row in agg.iterrows():
    dead_zone = row['hold_10_20']
    flag = " !!" if dead_zone > 25 else ""
    print(f"ROC<{int(row['sell_roc_edge']):>3}  死区比例={dead_zone:.0f}%  胜率={row['win_rate']:.1f}%  盈亏={row['avg_return']:+.1f}%{flag}")

# === 结论 ===
print("\n" + "=" * 80)
print("优化建议")
print("=" * 80)

best_roc = int(best['sell_roc_edge'])
best_ma = int(best['sell_ma_roc_edge'])
print(f"最优退出阈值: sell_roc_edge = {best_roc}, sell_ma_roc_edge = {best_ma}")
print(f"当前值 vs 最优: 胜率 {c['win_rate']:.1f}%→{best['win_rate']:.1f}%  "
      f"盈亏 {c['avg_return']:+.1f}%→{best['avg_return']:+.1f}%")
print(f"改动: config 中 sell_roc_edge: {int(CURRENT_SELL_ROC)} → {best_roc}")
if best_ma != int(CURRENT_SELL_MA):
    print(f"       config 中 sell_ma_roc_edge: {int(CURRENT_SELL_MA)} → {best_ma}")

os.makedirs(RESULT_DIR, exist_ok=True)
ts = datetime.now().strftime('%Y%m%d_%H%M')
csv_path = os.path.join(RESULT_DIR, f"exit_threshold_scan_{PRESET}_{ts}.csv")
df.to_csv(csv_path, index=False, encoding='utf-8-sig')
print(f"\n详细数据: {csv_path}")
