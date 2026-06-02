"""RSI增强过滤探索：多维度评估开/关及各阈值的效果（不唯Sharpe/收益论）。

评估维度：
  1. 信号量：每天产生的买入信号总数（参与度）
  2. 胜率：买入后N日收益>0的比例
  3. 盈亏比：正向收益均值 / 负向收益均值的绝对值
  4. 收益分布：P10/P25/P50/P75/P90 分位数
  5. 极端收益：最大正收益、最大负收益
  6. 热点行情捕捉：2024-09等关键时期的信号覆盖
  7. RSI实际分布：买入时RSI值的分位数，阈值对分布的截断效应
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from collections import defaultdict

from quantforge.core.data_feed import CachedDataFeed, DataRequest
from quantforge.data_sources.sina_feed import SinaFinanceFeed
from quantforge.strategies.factory import create_config
from quantforge.indicators.technical import ROCIndicator, MAIndicator, RSIIndicator

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

config = create_config("roc_momentum", "tech_growth")
codes = list(config.codes)

print("=" * 70)
print("RSI增强过滤 — 多维度探索")
print("=" * 70)
print(f"策略: tech_growth")
print(f"ROC阈值: buy>= {config.buy_roc_edge}%")
print(f"标的数: {len(codes)} (不含代理)")
print(f"分析区间: {config.start_date} ~ {config.end_date}")

print("\n--- 加载数据 ---")
data_feed = CachedDataFeed(source=SinaFinanceFeed(), cache_dir=os.path.join(_BASE_DIR, 'data', 'sina'))
all_codes = list(codes)
if proxy_code and proxy_code not in all_codes:
    all_codes.append(proxy_code)
data_feed.update_cache(codes=all_codes, data_type=config.data_type, start=config.start_date, end=config.end_date)
response = data_feed.get_data(DataRequest(codes=all_codes, data_type=config.data_type, start=config.start_date, end=config.end_date))

roc_indicator = ROCIndicator(n=config.roc_n, m=config.roc_m)
ma_indicator = MAIndicator(periods=[config.ma_period])
rsi_indicator = RSIIndicator(n=config.rsi_period)

HORIZONS = [1, 3, 5, 10, 20]
RSI_THRESHOLDS = [None, 50, 55, 60, 65, 70, 75]

trading_dates = None
for code in codes:
    df = response.bar_data.get(code, pd.DataFrame())
    if not df.empty:
        dates = sorted(pd.to_datetime(df['date']).tolist())
        if trading_dates is None or (len(dates) > 0):
            trading_dates = sorted(set(trading_dates or []) | set(dates))
trading_dates = sorted(trading_dates)

print(f"交易天数: {len(trading_dates)}")
print(f"日期范围: {trading_dates[0].strftime('%Y-%m-%d')} ~ {trading_dates[-1].strftime('%Y-%m-%d')}")

EMPTY_DAY = max(config.roc_n + config.roc_m, config.ma_period, config.rsi_period)

def compute_forward_returns(code, entry_date_str, horizon):
    df = response.bar_data.get(code, pd.DataFrame())
    if df.empty:
        return np.nan
    df_dates = pd.to_datetime(df['date'])
    entry_idx = (df_dates == entry_date_str)
    if not entry_idx.any():
        return np.nan
    idx = entry_idx.idxmax() if hasattr(entry_idx, 'idxmax') else entry_idx.argmax()
    target_idx = idx + horizon
    if target_idx >= len(df):
        return np.nan
    entry_close = float(df.iloc[idx]['close'])
    exit_close = float(df.iloc[target_idx]['close'])
    if entry_close <= 0:
        return np.nan
    return exit_close / entry_close - 1


def simulate_signals_for_threshold(rsi_max, rsi_enabled):
    results = []
    for ti, date_str in enumerate(trading_dates):
        dt = pd.Timestamp(date_str)
        for code in codes:
            if code == proxy_code:
                continue
            df = response.bar_data.get(code, pd.DataFrame())
            if df.empty:
                continue
            date_mask = pd.to_datetime(df['date']) <= date_str
            df_slice = df[date_mask].reset_index(drop=True)
            if len(df_slice) < EMPTY_DAY:
                continue

            df_slice = roc_indicator.compute(df_slice, n=config.roc_n, m=config.roc_m)
            df_slice = ma_indicator.compute(df_slice, periods=[config.ma_period])
            df_slice = rsi_indicator.compute(df_slice, n=config.rsi_period)

            latest = df_slice.iloc[-1]
            try:
                roc_val = float(latest.get('roc', np.nan))
                rsi_val_raw = float(latest.get('rsi', np.nan))
            except (ValueError, TypeError):
                continue
            if np.isnan(roc_val) or roc_val < config.buy_roc_edge:
                continue
            if rsi_enabled:
                if np.isnan(rsi_val_raw) or rsi_val_raw >= rsi_max:
                    continue

            fwd_rets = {}
            for h in HORIZONS:
                fwd_rets[f'fwd{h}'] = compute_forward_returns(code, date_str, h)

            results.append({
                'date': date_str,
                'code': code,
                'roc': roc_val,
                'rsi': rsi_val_raw if not np.isnan(rsi_val_raw) else np.nan,
                **fwd_rets,
            })

    return pd.DataFrame(results)


print("\n" + "=" * 70)
print("第一维度：各RSI阈值下的信号量 & 前向收益分布")
print("=" * 70)

all_results = {}
for rsi_limit in RSI_THRESHOLDS:
    enabled = rsi_limit is not None
    label = f"RSI<{rsi_limit}" if enabled else "RSI关闭"
    print(f"\n  计算: {label}...", end=" ", flush=True)
    df = simulate_signals_for_threshold(rsi_limit, enabled)
    all_results[label] = df
    print(f"信号={len(df)}条")

print("\n\n" + "=" * 70)
print("信号量对比（按年份）")
print("=" * 70)
print(f"{'RSI配置':<14}", end="")
years_set = set()
for label, df in all_results.items():
    if not df.empty:
        df['year'] = pd.to_datetime(df['date']).dt.year
        years_set.update(df['year'].unique())
years_sorted = sorted(years_set)
for y in years_sorted:
    print(f" {y:>6}", end="")
print(f" {'总计':>8}")
print("-" * (14 + 9 * len(years_sorted) + 8))

for label, df in all_results.items():
    print(f"{label:<14}", end="")
    total = 0
    for y in years_sorted:
        cnt = len(df[df['year'] == y]) if 'year' in df.columns and not df.empty else 0
        print(f" {cnt:>6}", end="")
        total += cnt
    print(f" {total:>8}")

print("\n\n" + "=" * 70)
print("前向收益分布（全历史，买入后N日）")
print("=" * 70)

for label, df in all_results.items():
    if df.empty:
        print(f"\n{label}: 无信号")
        continue
    print(f"\n{'─' * 50}")
    print(f"{label}  (信号总数: {len(df)})")
    print(f"{'─' * 50}")
    for h in HORIZONS:
        col = f'fwd{h}'
        valid = df[col].dropna()
        if len(valid) < 5:
            print(f"  持有{h:>2}日: 有效样本={len(valid)} 不足")
            continue
        win_rate = (valid > 0).mean() * 100
        pos_rets = valid[valid > 0]
        neg_rets = valid[valid < 0]
        p_l_ratio = pos_rets.mean() / abs(neg_rets.mean()) if len(neg_rets) > 0 and neg_rets.mean() != 0 else float('inf')
        pcts = valid.quantile([0.1, 0.25, 0.5, 0.75, 0.9])
        print(f"  持有{h:>2}日: n={len(valid):>5}  胜率={win_rate:5.1f}%  盈亏比={p_l_ratio:.2f}  "
              f"P10={pcts[0.1]:>6.1%}  P25={pcts[0.25]:>6.1%}  P50={pcts[0.5]:>6.1%}  "
              f"P75={pcts[0.75]:>6.1%}  P90={pcts[0.9]:>6.1%}  "
              f"Max+={valid.max():>6.1%}  Max-={valid.min():>6.1%}")


print("\n\n" + "=" * 70)
print("第二维度：RSI阈值与胜率/盈亏比的关系（持有5日）")
print("=" * 70)
print(f"{'RSI配置':<14} {'信号量':>7} {'胜率':>7} {'盈亏比':>7} {'中位收益':>9} {'P75收益':>8} {'最大正':>8} {'最大负':>8}")
print("-" * 75)
for label, df in all_results.items():
    if df.empty:
        print(f"{label:<14} {'0':>7}")
        continue
    valid = df['fwd5'].dropna()
    if len(valid) < 5:
        print(f"{label:<14} {len(df):>7}  {'样本不足':>20}")
        continue
    wr = (valid > 0).mean() * 100
    pos = valid[valid > 0]
    neg = valid[valid < 0]
    pl = pos.mean() / abs(neg.mean()) if len(neg) > 0 and neg.mean() != 0 else 999
    print(f"{label:<14} {len(df):>7} {wr:>6.1f}% {pl:>6.2f} "
          f"{valid.median():>8.1%} {valid.quantile(0.75):>7.1%} "
          f"{valid.max():>7.1%} {valid.min():>7.1%}")


print("\n\n" + "=" * 70)
print("第三维度：2024年09月行情捕捉能力")
print("=" * 70)
print(f"{'RSI配置':<14} {'9月信号':>9} {'前10天':>7} {'后9天':>7} {'总买入码':>9} {'fwd5中位':>9} {'fwd5胜率':>8}")
print("-" * 70)
for label, df in all_results.items():
    if df.empty:
        continue
    sept = df[(df['date'] >= '2024-09-01') & (df['date'] <= '2024-09-30')]
    sept_early = sept[sept['date'] <= '2024-09-13']
    sept_late = sept[sept['date'] > '2024-09-13']
    codes_set = sept['code'].unique() if not sept.empty else []
    fwd5 = sept['fwd5'].dropna() if not sept.empty else pd.Series(dtype=float)
    wr = (fwd5 > 0).mean() * 100 if len(fwd5) > 0 else 0
    print(f"{label:<14} {len(sept):>9} {len(sept_early):>7} {len(sept_late):>7} "
          f"{len(codes_set):>9} {fwd5.median():>8.1%} {wr:>7.1f}%" if len(fwd5) > 0 else
          f"{label:<14} {len(sept):>9} {len(sept_early):>7} {len(sept_late):>7} {'-':>9}")


print("\n\n" + "=" * 70)
print("第四维度：ROC达标时刻的RSI自然分布（不设RSI过滤时）")
print("=" * 70)
no_rsi = all_results.get("RSI关闭", pd.DataFrame())
if not no_rsi.empty:
    rsi_vals = no_rsi['rsi'].dropna()
    if len(rsi_vals) > 0:
        print(f"ROC≥{config.buy_roc_edge}%时RSI的实际分布 (n={len(rsi_vals)})")
        for p in [1, 5, 10, 25, 50, 75, 90, 95, 99]:
            print(f"  P{p:>2}: {rsi_vals.quantile(p/100):.1f}")
        print(f"\n  RSI<50的比例: {(rsi_vals < 50).mean()*100:.1f}%")
        print(f"  RSI<55的比例: {(rsi_vals < 55).mean()*100:.1f}%")
        print(f"  RSI<60的比例: {(rsi_vals < 60).mean()*100:.1f}%")
        print(f"  RSI<65的比例: {(rsi_vals < 65).mean()*100:.1f}%")
        print(f"  RSI<70的比例: {(rsi_vals < 70).mean()*100:.1f}%")
        print(f"  RSI<75的比例: {(rsi_vals < 75).mean()*100:.1f}%")

        print("\n  按RSI区间分组的信号质量（持有5日）:")
        bins = [(0, 40), (40, 50), (50, 55), (55, 60), (60, 65), (65, 70), (70, 80), (80, 100)]
        print(f"  {'RSI区间':<12} {'信号量':>7} {'胜率':>7} {'盈亏比':>6} {'中位收益':>9} {'P90':>8} {'最大正':>8} {'最大负':>8}")
        for lo, hi in bins:
            subset = no_rsi[(no_rsi['rsi'] >= lo) & (no_rsi['rsi'] < hi)]
            fwd5 = subset['fwd5'].dropna()
            if len(fwd5) < 3:
                print(f"  {lo}~{hi:<4}      {len(subset):>7}  {'样本不足':>20}")
                continue
            wr = (fwd5 > 0).mean() * 100
            pos = fwd5[fwd5 > 0]
            neg = fwd5[fwd5 < 0]
            pl = pos.mean() / abs(neg.mean()) if len(neg) > 0 and neg.mean() != 0 else 999
            print(f"  {lo}~{hi:<4}      {len(subset):>7} {wr:>6.1f}% {pl:>5.2f} "
                  f"{fwd5.median():>8.1%} {fwd5.quantile(0.9):>7.1%} "
                  f"{fwd5.max():>7.1%} {fwd5.min():>7.1%}")


print("\n\n" + "=" * 70)
print("第五维度：ROC达标信号的年度RSI均值（市场环境变化）")
print("=" * 70)
if not no_rsi.empty:
    no_rsi['year'] = pd.to_datetime(no_rsi['date']).dt.year
    for y in sorted(no_rsi['year'].dropna().unique()):
        y_data = no_rsi[no_rsi['year'] == y]
        if len(y_data) < 3:
            continue
        rsi_y = y_data['rsi'].dropna()
        fwd5_y = y_data['fwd5'].dropna()
        wr = (fwd5_y > 0).mean() * 100 if len(fwd5_y) > 0 else 0
        print(f"  {y}: 信号={len(y_data)}  RSI均值={rsi_y.mean():.1f}  RSI中位={rsi_y.median():.1f}  "
              f"RSI<60占比={(rsi_y<60).mean()*100:.1f}%  RSI<65占比={(rsi_y<65).mean()*100:.1f}%  "
              f"fwd5胜率={wr:.1f}%")


print("\n\n" + "=" * 70)
print("综合建议矩阵 (持有5日)")
print("=" * 70)
print(f"{'RSI配置':<14} {'参与率':>7} {'胜率':>7} {'盈亏比':>7} {'中位':>7} {'P90':>7} {'Max+':>7} {'Max-':>7} {'9月捕捉':>9}")
print("-" * 80)
for label, df in all_results.items():
    if df.empty:
        continue
    fwd5 = df['fwd5'].dropna()
    sept_cnt = len(df[(df['date'] >= '2024-09-01') & (df['date'] <= '2024-09-30')])
    wr = (fwd5 > 0).mean() * 100
    pos = fwd5[fwd5 > 0]
    neg = fwd5[fwd5 < 0]
    pl = pos.mean() / abs(neg.mean()) if len(neg) > 0 and neg.mean() != 0 else 999
    part_rate = len(df) / max(1, len(trading_dates))
    print(f"{label:<14} {part_rate:>6.1%} {wr:>6.1f}% {pl:>6.2f} "
          f"{fwd5.median():>6.1%} {fwd5.quantile(0.9):>6.1%} "
          f"{fwd5.max():>6.1%} {fwd5.min():>6.1%} {sept_cnt:>9}")

print("\n===== 探索完成 =====")
