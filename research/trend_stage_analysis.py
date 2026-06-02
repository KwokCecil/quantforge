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
RESULT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results", "T028_aux_signals")

config = create_config("roc_momentum", PRESET)
CODES = config.codes
START = config.start_date
END = config.end_date
BUY_ROC_EDGE = config.buy_roc_edge
SELL_ROC_EDGE = config.sell_roc_edge
SELL_MA_ROC_EDGE = config.sell_ma_roc_edge
ROC_N = config.roc_n
ROC_M = config.roc_m
THRESHOLD_1_3 = BUY_ROC_EDGE * 1.3

# 退出条件（tech_growth默认：仅sell_roc_edge，关闭MA_DIRECTION和CROSS_MAROC）
# 如果需要分析这些条件的过滤效果，可分别开启后重跑
ROC_MA_DIRECTION = False
ROC_CROSS_MAROC_SELL = False

print(f"配置: {PRESET}")
print(f"标的: {CODES}")
print(f"区间: {START} ~ {END}")
print(f"买入ROC阈值: {BUY_ROC_EDGE}  早期上限: {THRESHOLD_1_3}")
print(f"卖出ROC阈值: {SELL_ROC_EDGE}")
print(f"ROC_N={ROC_N}  ROC_M={ROC_M}")

# === 数据加载 ===
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
feed = CachedDataFeed(
    source=SinaFinanceFeed(),
    cache_dir=os.path.join(_BASE_DIR, "data", "sina"),
)
feed.update_cache(codes=CODES, data_type="daily_k", start=START, end=END)
response = feed.get_data(DataRequest(codes=CODES, data_type="daily_k", start=START, end=END))

# === 指标计算：每个标的独立跑一次 ===
roc_ind = ROCIndicator(n=ROC_N, m=ROC_M)
rsi_ind = RSIIndicator(n=14)
macd_ind = MACDIndicator(fast=12, slow=26, signal=9)

code_dfs = {}
for code in CODES:
    df = response.bar_data.get(code)
    if df is None or df.empty:
        print(f"⚠ 跳过 {code}：无数据")
        continue
    df = df.copy()
    df = roc_ind.compute(df, n=ROC_N, m=ROC_M)
    df = rsi_ind.compute(df, n=14)
    df = macd_ind.compute(df, fast=12, slow=26, signal=9)
    code_dfs[code] = df

# === 构建矩阵 [N_CODES × N_DATES] ===
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
dif_mat = np.full((N_CODES, N_DATES), np.nan)
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
            dif_mat[ci, di] = row.get('dif', np.nan)
            close_mat[ci, di] = row.get('close', np.nan)

# === 识别买入信号并分类 ===
signals = []

for di in range(N_DATES):
    for ci in range(N_CODES):
        roc_val = roc_mat[ci, di]
        if np.isnan(roc_val) or roc_val < BUY_ROC_EDGE:
            continue
        maroc_val = maroc_mat[ci, di]
        if np.isnan(maroc_val):
            continue
        prev_maroc = maroc_mat[ci, di - 1] if di > 0 else maroc_val

        # 阶段分类
        if roc_val < THRESHOLD_1_3:
            stage = "早期"
        elif maroc_val > prev_maroc:
            stage = "中期"
        else:
            stage = "晚期"

        signals.append({
            'date': all_dates[di],
            'code': CODES[ci],
            'stage': stage,
            'roc': round(roc_val, 2),
            'maroc': round(maroc_val, 2),
            'roc_maroc_diff': round(roc_val - maroc_val, 2),
            'rsi': round(rsi_mat[ci, di], 1) if not np.isnan(rsi_mat[ci, di]) else None,
            'dif': round(dif_mat[ci, di], 4) if not np.isnan(dif_mat[ci, di]) else None,
            'di': di,
            'ci': ci,
        })

print(f"\n共识别 {len(signals)} 个买入信号")

# === 前向查找卖出日 + 计算收益 ===
for sig in signals:
    ci = sig['ci']
    di = sig['di']
    entry_close = close_mat[ci, di]
    exit_di = di

    for fwd_di in range(di + 1, N_DATES):
        fwd_roc = roc_mat[ci, fwd_di]
        fwd_maroc = maroc_mat[ci, fwd_di]
        if np.isnan(fwd_roc):
            continue

        triggered = False
        exit_reason = ""

        if SELL_ROC_EDGE > 0 and fwd_roc < SELL_ROC_EDGE:
            triggered = True
            exit_reason = f"ROC={fwd_roc:.2f}<{SELL_ROC_EDGE}"

        if not triggered and SELL_MA_ROC_EDGE > 0 and not np.isnan(fwd_maroc) and fwd_maroc < SELL_MA_ROC_EDGE:
            triggered = True
            exit_reason = f"MAROC={fwd_maroc:.2f}<{SELL_MA_ROC_EDGE}"

        if not triggered and ROC_MA_DIRECTION:
            prev_fwd_maroc = maroc_mat[ci, fwd_di - 1] if fwd_di > 0 else fwd_maroc
            if not np.isnan(prev_fwd_maroc) and fwd_maroc <= prev_fwd_maroc:
                triggered = True
                exit_reason = "MAROC方向向下"

        if not triggered and ROC_CROSS_MAROC_SELL and not np.isnan(fwd_maroc) and fwd_roc < fwd_maroc:
            triggered = True
            exit_reason = f"ROC={fwd_roc:.2f}<MAROC={fwd_maroc:.2f}"

        if triggered:
            exit_di = fwd_di
            break

    if exit_di == di:
        exit_close = close_mat[ci, -1] if not np.isnan(close_mat[ci, -1]) else entry_close
        exit_reason = "无卖出"
    else:
        exit_close = close_mat[ci, exit_di] if not np.isnan(close_mat[ci, exit_di]) else entry_close

    ret = (exit_close / entry_close - 1) * 100 if entry_close > 0 else 0
    holding_days = exit_di - di

    # 前向窗口峰值/期末收益
    for window in [5, 10, 20]:
        peak_ret = 0.0
        final_ret = 0.0
        end_di = min(di + window, N_DATES - 1)
        for wd in range(di + 1, end_di + 1):
            p = close_mat[ci, wd]
            if not np.isnan(p) and entry_close > 0:
                rw = (p / entry_close - 1) * 100
                peak_ret = max(peak_ret, rw)
                final_ret = rw
        sig[f'window{window}_peak'] = round(peak_ret, 2)
        sig[f'window{window}_final'] = round(final_ret, 2)

    sig['holding_days'] = holding_days
    sig['return_pct'] = round(ret, 2)
    sig['exit_date'] = all_dates[exit_di] if exit_di < N_DATES else ""
    sig['exit_reason'] = exit_reason

# === DataFrame ===
df = pd.DataFrame(signals)
df['is_win'] = df['return_pct'] > 0

# === 阶段统计 ===
print("\n" + "=" * 70)
print("按趋势阶段分组统计")
print("=" * 70)

for stage in ["早期", "中期", "晚期"]:
    sub = df[df['stage'] == stage]
    cnt = len(sub)
    if cnt == 0:
        print(f"\n{stage}: 无信号")
        continue
    wr = sub['is_win'].mean() * 100
    avg_ret = sub['return_pct'].mean()
    med_ret = sub['return_pct'].median()
    mx = sub['return_pct'].max()
    mn = sub['return_pct'].min()
    avg_hold = sub['holding_days'].mean()
    print(f"\n[{stage}] 信号数={cnt}")
    print(f"  胜率={wr:.1f}%  平均盈亏={avg_ret:+.1f}%  中位数={med_ret:+.1f}%")
    print(f"  最大盈利={mx:+.1f}%  最大亏损={mn:+.1f}%")
    print(f"  平均持有={avg_hold:.0f}天")
    print(f"  平均ROC={sub['roc'].mean():.1f}  MAROC={sub['maroc'].mean():.1f}")
    for w in [5, 10, 20]:
        print(f"  前向{w}日: 峰值{sub[f'window{w}_peak'].mean():+.1f}%  期末{sub[f'window{w}_final'].mean():+.1f}%")

# === RSI分组 ===
print("\n" + "=" * 70)
print("按RSI区间分组（含阶段细分）")
print("=" * 70)

rsi_bins = [(0, 60), (60, 70), (70, 80), (80, 999)]
for lo, hi in rsi_bins:
    sub = df[(df['rsi'] >= lo) & (df['rsi'] < hi)]
    cnt = len(sub)
    if cnt == 0:
        continue
    label = f"RSI {lo}-{int(hi)}" if hi < 999 else f"RSI >={lo}"
    print(f"\n{label}: 信号数={cnt}  胜率={sub['is_win'].mean()*100:.1f}%  盈亏={sub['return_pct'].mean():+.1f}%")
    for s in ["早期", "中期", "晚期"]:
        ss = sub[sub['stage'] == s]
        if len(ss) > 0:
            print(f"  └ {s}: {len(ss)}次  胜率{ss['is_win'].mean()*100:.0f}%  盈亏{ss['return_pct'].mean():+.1f}%")

# === ROC-MAROC差值分组 ===
print("\n" + "=" * 70)
print("按ROC-MAROC差值分组")
print("=" * 70)

diff_bins = [(-999, 5), (5, 10), (10, 20), (20, 999)]
for lo, hi in diff_bins:
    sub = df[(df['roc_maroc_diff'] >= lo) & (df['roc_maroc_diff'] < hi)]
    cnt = len(sub)
    if cnt == 0:
        continue
    label = f"差值{lo}-{hi}" if lo > -900 else f"差值<{hi}"
    if hi > 900:
        label = f"差值>={lo}"
    print(f"{label}: 信号数={cnt}  胜率={sub['is_win'].mean()*100:.1f}%  盈亏={sub['return_pct'].mean():+.1f}%")

# === 持有天数分布 ===
print("\n" + "=" * 70)
print("持有天数分布")
print("=" * 70)

day_bins = [0, 5, 10, 20, 40, 9999]
for lo, hi in zip(day_bins[:-1], day_bins[1:]):
    sub = df[(df['holding_days'] >= lo) & (df['holding_days'] < hi)]
    cnt = len(sub)
    if cnt == 0:
        continue
    wr = sub['is_win'].mean() * 100
    label = f"{int(lo)}-{int(hi)}天"
    print(f"{label}: 信号数={cnt}  胜率={wr:.1f}%  盈亏={sub['return_pct'].mean():+.1f}%")

# === 结论汇总 ===
print("\n" + "=" * 70)
print("结论汇总")
print("=" * 70)

early = df[df['stage'] == "早期"]
mid = df[df['stage'] == "中期"]
late = df[df['stage'] == "晚期"]

if len(mid) > 0 and len(early) > 0:
    ratio = mid['return_pct'].mean() / early['return_pct'].mean() if early['return_pct'].mean() != 0 else 0
    print(f"中期/早期盈亏比: {ratio:.2f}")
    if ratio > 0.8:
        print("→ 中期买入盈亏接近早期，中途追入可行")
    elif ratio > 0.4:
        print("→ 中期买入盈亏下降，可追但建议缩小仓位（如Kelly折扣）")
    else:
        print("→ 中期买入盈亏严重下降，不推荐中途追入")

if len(late) > 0:
    print(f"晚期信号数: {len(late)}, 胜率: {late['is_win'].mean()*100:.1f}%")
    if late['is_win'].mean() < 0.4:
        print("→ MAROC拐头是强卖出/过滤信号，晚期不应买入")

# === 导出 ===
os.makedirs(RESULT_DIR, exist_ok=True)
ts = datetime.now().strftime('%Y%m%d_%H%M')
csv_path = os.path.join(RESULT_DIR, f"trend_stage_{PRESET}_{ts}.csv")
df.to_csv(csv_path, index=False, encoding='utf-8-sig')
print(f"\n详细数据: {csv_path}")
