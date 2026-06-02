# @layer: e2e
"""T028 辅助信号分析：量价/波动率/MACD背离 —— 对 trend_stage_analysis 的 1444 个买入信号追加三维度统计"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from datetime import datetime

import numpy as np
import pandas as pd

from quantforge.core.data_feed import CachedDataFeed, DataRequest
from quantforge.data_sources.sina_feed import SinaFinanceFeed
from quantforge.indicators.technical import ROCIndicator, RSIIndicator, MACDIndicator, ATRIndicator
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

# === 辅助信号阈值（可调整） ===
VOL_SPIKE = 1.5       # 放量阈值（成交量/20日均量）
VOL_SHRINK = 0.8      # 缩量阈值
ATR_HIGH_PCT = 75     # 高波分位
ATR_LOW_PCT = 25      # 低波分位
MACD_DIV_LOOKBACK = 20  # MACD背离回看窗口

print(f"配置: {PRESET}  标的: {len(CODES)}个  区间: {START} ~ {END}")
print(f"买入ROC阈值: {BUY_ROC_EDGE}  早期上限: {THRESHOLD_1_3}")
print(f"量价: 放量>{VOL_SPIKE}x  缩量<{VOL_SHRINK}x")
print(f"波动率: 高波>{ATR_HIGH_PCT}分位  低波<{ATR_LOW_PCT}分位")
print(f"MACD背离回看: {MACD_DIV_LOOKBACK}日")

# === 数据加载 ===
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
feed = CachedDataFeed(
    source=SinaFinanceFeed(),
    cache_dir=os.path.join(_BASE_DIR, "data", "sina"),
)
feed.update_cache(codes=CODES, data_type="daily_k", start=START, end=END)
response = feed.get_data(DataRequest(codes=CODES, data_type="daily_k", start=START, end=END))

# === 指标计算 ===
roc_ind = ROCIndicator(n=ROC_N, m=ROC_M)
rsi_ind = RSIIndicator(n=14)
macd_ind = MACDIndicator(fast=12, slow=26, signal=9)
atr_ind = ATRIndicator(n=20)

code_dfs = {}
for code in CODES:
    df = response.bar_data.get(code)
    if df is None or df.empty:
        print(f"⚠ 跳过 {code}：无数据")
        continue
    df = df.copy()
    if 'vol' not in df.columns:
        print(f"⚠ 跳过 {code}：无成交量列")
        continue
    df = roc_ind.compute(df, n=ROC_N, m=ROC_M)
    df = rsi_ind.compute(df, n=14)
    df = macd_ind.compute(df, fast=12, slow=26, signal=9)
    df = atr_ind.compute(df, n=20)
    code_dfs[code] = df

# === 构建矩阵 ===
all_dates_set = set()
for df in code_dfs.values():
    all_dates_set.update(df['date'].tolist())
all_dates = sorted(all_dates_set)
date_to_idx = {d: i for i, d in enumerate(all_dates)}
N_DATES = len(all_dates)
N_CODES = len(CODES)

roc_mat = np.full((N_CODES, N_DATES), np.nan)
maroc_mat = np.full((N_CODES, N_DATES), np.nan)
close_mat = np.full((N_CODES, N_DATES), np.nan)
dif_mat = np.full((N_CODES, N_DATES), np.nan)
vol_mat = np.full((N_CODES, N_DATES), np.nan)
atr_mat = np.full((N_CODES, N_DATES), np.nan)

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
            close_mat[ci, di] = row.get('close', np.nan)
            dif_mat[ci, di] = row.get('dif', np.nan)
            vol_mat[ci, di] = row.get('vol', np.nan)
            atr_mat[ci, di] = row.get('atr', np.nan)

# === 识别买入信号（同 trend_stage_analysis） ===
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
        close_val = close_mat[ci, di]

        if roc_val < THRESHOLD_1_3:
            stage = "早期"
        elif maroc_val > prev_maroc:
            stage = "中期"
        else:
            stage = "晚期"

        # === 量价：成交量/20日均量 ===
        vol_val = vol_mat[ci, di]
        vol_start = max(0, di - 20)
        vol_window = vol_mat[ci, vol_start:di]
        vol_window = vol_window[~np.isnan(vol_window)]
        if len(vol_window) >= 5 and vol_val > 0:
            avg_vol = np.mean(vol_window)
            vol_ratio = vol_val / avg_vol if avg_vol > 0 else 1.0
        else:
            vol_ratio = 1.0

        if vol_ratio >= VOL_SPIKE:
            vol_label = "放量"
        elif vol_ratio < VOL_SHRINK:
            vol_label = "缩量"
        else:
            vol_label = "正常"

        # === 波动率：ATR(20) 历史分位 ===
        atr_val = atr_mat[ci, di]
        atr_start = max(0, di - 252)
        atr_window = atr_mat[ci, atr_start:di + 1]
        atr_window = atr_window[~np.isnan(atr_window)]
        if len(atr_window) >= 50 and not np.isnan(atr_val):
            atr_pct = (np.sum(atr_window < atr_val) / len(atr_window)) * 100
        else:
            atr_pct = 50.0

        if atr_pct >= ATR_HIGH_PCT:
            atr_label = "高波"
        elif atr_pct < ATR_LOW_PCT:
            atr_label = "低波"
        else:
            atr_label = "正常"

        # === MACD背离：价格20日新高但DIF未确认 ===
        macd_lb = max(0, di - MACD_DIV_LOOKBACK)
        close_win = close_mat[ci, macd_lb:di + 1]
        close_win = close_win[~np.isnan(close_win)]
        dif_win = dif_mat[ci, macd_lb:di + 1]
        dif_win = dif_win[~np.isnan(dif_win)]

        price_new_high = len(close_win) >= 2 and close_val >= np.max(close_win[:-1])
        if price_new_high and len(dif_win) >= 2:
            dif_new_high = dif_mat[ci, di] >= np.max(dif_win[:-1])
            macd_div = not dif_new_high
        else:
            macd_div = False

        signals.append({
            'date': all_dates[di],
            'code': CODES[ci],
            'stage': stage,
            'roc': round(roc_val, 2),
            'vol_ratio': round(vol_ratio, 2),
            'vol_label': vol_label,
            'atr_val': round(atr_val, 4) if not np.isnan(atr_val) else None,
            'atr_pct': round(atr_pct, 1),
            'atr_label': atr_label,
            'macd_divergence': macd_div,
            'di': di,
            'ci': ci,
        })

print(f"\n共识别 {len(signals)} 个买入信号")

# === 前向查找卖出日 + 计算收益（同 trend_stage_analysis） ===
for sig in signals:
    ci = sig['ci']
    di = sig['di']
    entry_close = close_mat[ci, di]
    exit_di = di

    for fwd_di in range(di + 1, N_DATES):
        fwd_roc = roc_mat[ci, fwd_di]
        if np.isnan(fwd_roc):
            continue

        if SELL_ROC_EDGE > 0 and fwd_roc < SELL_ROC_EDGE:
            exit_di = fwd_di
            break
        if SELL_MA_ROC_EDGE > 0:
            fwd_maroc = maroc_mat[ci, fwd_di]
            if not np.isnan(fwd_maroc) and fwd_maroc < SELL_MA_ROC_EDGE:
                exit_di = fwd_di
                break

    if exit_di == di:
        exit_close = close_mat[ci, -1] if not np.isnan(close_mat[ci, -1]) else entry_close
    else:
        exit_close = close_mat[ci, exit_di] if not np.isnan(close_mat[ci, exit_di]) else entry_close

    sig['holding_days'] = exit_di - di
    sig['return_pct'] = round((exit_close / entry_close - 1) * 100, 2) if entry_close > 0 else 0

df = pd.DataFrame(signals)
df['is_win'] = df['return_pct'] > 0


# === 打印分组统计 ===
def print_group(title, label_col, labels, df_group):
    print(f"\n{'=' * 70}")
    print(title)
    print(f"{'=' * 70}")
    for label in labels:
        sub = df_group[df_group[label_col] == label]
        cnt = len(sub)
        if cnt == 0:
            print(f"\n[{label}]: 无信号")
            continue
        wr = sub['is_win'].mean() * 100
        avg_ret = sub['return_pct'].mean()
        med_ret = sub['return_pct'].median()
        avg_hold = sub['holding_days'].mean()
        print(f"\n[{label}] 信号数={cnt}  胜率={wr:.1f}%  平均盈亏={avg_ret:+.1f}%  中位数={med_ret:+.1f}%  平均持有={avg_hold:.0f}天")

        for s in ["早期", "中期", "晚期"]:
            ss = sub[sub['stage'] == s]
            if len(ss) > 0:
                print(f"  └ {s}: {len(ss)}次  胜率{ss['is_win'].mean()*100:.0f}%  盈亏{ss['return_pct'].mean():+.1f}%")


# === 量价分组 ===
print_group("量价分析：成交量/20日均量", "vol_label",
            ["放量", "正常", "缩量"], df)

# === 波动率分组 ===
print_group("波动率分析：ATR(20) 252日历史分位", "atr_label",
            ["高波", "正常", "低波"], df)

# === MACD背离分组 ===
print_group("MACD背离分析：价格20日新高但DIF未确认", "macd_divergence",
            [True, False], df)

# === 交叉维度：量价×波动率 ===
print(f"\n{'=' * 70}")
print("交叉维度：量价 × 波动率（信号数 > 20 的子组）")
print(f"{'=' * 70}")
for vl in ["放量", "正常", "缩量"]:
    for al in ["高波", "正常", "低波"]:
        sub = df[(df['vol_label'] == vl) & (df['atr_label'] == al)]
        cnt = len(sub)
        if cnt < 20:
            continue
        print(f"[{vl}+{al}] 信号数={cnt}  胜率={sub['is_win'].mean()*100:.1f}%  盈亏={sub['return_pct'].mean():+.1f}%")

# === 结论 ===
print(f"\n{'=' * 70}")
print("结论汇总")
print(f"{'=' * 70}")

for label, col in [("成交量", "vol_label"), ("波动率", "atr_label")]:
    best = df.groupby(col)['return_pct'].mean().idxmax()
    worst = df.groupby(col)['return_pct'].mean().idxmin()
    best_wr = df[df[col] == best]['is_win'].mean() * 100
    worst_wr = df[df[col] == worst]['is_win'].mean() * 100
    best_ret = df[df[col] == best]['return_pct'].mean()
    worst_ret = df[df[col] == worst]['return_pct'].mean()
    print(f"\n{label}: 最优={best}(胜率{best_wr:.0f}% 盈亏{best_ret:+.1f}%)  最差={worst}(胜率{worst_wr:.0f}% 盈亏{worst_ret:+.1f}%)")

div_true = df[df['macd_divergence'] == True]
div_false = df[df['macd_divergence'] == False]
if len(div_true) > 0 and len(div_false) > 0:
    print(f"\nMACD背离: 有背离={len(div_true)}次(胜率{div_true['is_win'].mean()*100:.0f}% 盈亏{div_true['return_pct'].mean():+.1f}%)  "
          f"无背离={len(div_false)}次(胜率{div_false['is_win'].mean()*100:.0f}% 盈亏{div_false['return_pct'].mean():+.1f}%)")

# === 导出 ===
os.makedirs(RESULT_DIR, exist_ok=True)
ts = datetime.now().strftime('%Y%m%d_%H%M')
csv_path = os.path.join(RESULT_DIR, f"trend_aux_{PRESET}_{ts}.csv")
df.to_csv(csv_path, index=False, encoding='utf-8-sig')
print(f"\n详细数据: {csv_path}")
