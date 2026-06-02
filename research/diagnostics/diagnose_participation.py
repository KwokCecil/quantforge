"""诊断脚本：分析策略不参与特定时期的原因（证据驱动）。"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from collections import defaultdict

from quantforge.core.data_feed import CachedDataFeed, DataRequest
from quantforge.data_sources.sina_feed import SinaFinanceFeed
from quantforge.strategies.factory import create_config
from quantforge.indicators.technical import ROCIndicator, MAIndicator, RSIIndicator, ATRIndicator, ADXIndicator

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

config = create_config("roc_momentum", "tech_growth")
start = config.start_date
end = config.end_date
codes = list(config.codes)

print(f"分析预设: tech_growth")
print(f"标的数量: {len(codes)}")
print(f"回测区间: {start} ~ {end}")
print(f"买入ROC阈值: {config.buy_roc_edge}%")
print(f"RSI增强过滤: 开启, RSI>= {config.rsi_enhance_below} 禁止买入")
print(f"ATR扩张过滤: 开启, ATR(20)>1.3*ATR(200) 禁止买入")
print(f"ADX趋势过滤: 开启, ADX<20 禁止买入")
print(f"高点回落止损: 开启, {config.high_watermark_stop_edge*100:.0f}%")

print("\n=== 加载数据 ===")
data_feed = CachedDataFeed(source=SinaFinanceFeed(), cache_dir=os.path.join(_BASE_DIR, 'data', 'sina'))

all_codes = list(codes)

data_feed.update_cache(codes=all_codes, data_type=config.data_type, start=start, end=end)
response = data_feed.get_data(DataRequest(codes=all_codes, data_type=config.data_type, start=start, end=end))

roc_indicator = ROCIndicator(n=config.roc_n, m=config.roc_m)
ma_indicator = MAIndicator(periods=[config.ma_period])
rsi_indicator = RSIIndicator(n=config.rsi_period)
atr_indicator = ATRIndicator(n=20)
atr200_indicator = ATRIndicator(n=200)
adx_indicator = ADXIndicator(n=14)


def compute_atr_expansion(df, ratio=1.3):
    if len(df) < 200:
        return None
    atr20 = df['atr'].iloc[-1]
    atr200_win = df['atr'].iloc[-201:-1].dropna()
    if len(atr200_win) < 50 or pd.isna(atr20):
        return None
    atr200_mean = float(atr200_win.mean())
    return atr20 > ratio * atr200_mean if atr200_mean > 0 else None


def compute_adx(df):
    if 'adx' not in df.columns:
        return None
    val = df['adx'].iloc[-1]
    return float(val) if not pd.isna(val) else None


def is_macro_atr_expansion(proxy_df, ratio=1.3):
    if len(proxy_df) < 200:
        return False
    atr14 = ATRIndicator(n=14).compute(proxy_df.copy())['atr'].iloc[-1]
    atr200 = ATRIndicator(n=200).compute(proxy_df.copy())['atr'].iloc[-1]
    if pd.isna(atr14) or pd.isna(atr200) or atr200 <= 0:
        return False
    return atr14 > ratio * atr200


def is_macro_adx_weak(proxy_df, threshold=20):
    if len(proxy_df) < 30:
        return None
    high = pd.to_numeric(proxy_df['high'], errors='coerce')
    low = pd.to_numeric(proxy_df['low'], errors='coerce')
    close = pd.to_numeric(proxy_df['close'], errors='coerce')
    tr = pd.concat([high - low, (high - close.shift()).abs(),
                    (low - close.shift()).abs()], axis=1).max(axis=1)
    up = high.diff()
    dn = -low.diff()
    plus_dm = up.where((up > dn) & (up > 0), 0.0)
    minus_dm = dn.where((dn > up) & (dn > 0), 0.0)
    atr14 = tr.rolling(14, min_periods=1).mean()
    plus_di = 100 * (plus_dm.rolling(14, min_periods=1).mean() / atr14)
    minus_di = 100 * (minus_dm.rolling(14, min_periods=1).mean() / atr14)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.rolling(14, min_periods=1).mean()
    return float(adx.iloc[-1])


proxy_df_raw = None
proxy_code = None
if proxy_code and proxy_code in response.bar_data:
    proxy_df_raw = response.bar_data[proxy_code].copy()

trading_dates = None
for code in codes:
    df = response.bar_data.get(code, pd.DataFrame())
    if not df.empty:
        candidate = pd.to_datetime(df['date']).tolist()
        if trading_dates is None or (len(candidate) > 0 and candidate[0] == min(candidate)):
            trading_dates = sorted(candidate)

print(f"交易天数: {len(trading_dates)}")
print(f"日期范围: {min(trading_dates).strftime('%Y-%m-%d')} ~ {max(trading_dates).strftime('%Y-%m-%d')}")

RECORD = {
    "date": [],
    "n_roc_pass": [],
    "n_rsi_block": [],
    "n_atr_block": [],
    "n_adx_block": [],
    "n_buy": [],
    "macro_block": [],
    "macro_atr": [],
    "macro_adx": [],
    "best_roc": [],
    "n_rsi_avail": [],
}

print("\n=== 逐日诊断（每月一次采样） ===")
last_month = None
sample_snapshots = []

for ti, date_str in enumerate(trading_dates):
    dt = pd.Timestamp(date_str)
    month_key = dt.strftime("%Y-%m")

    n_roc_pass = 0
    n_rsi_block = 0
    n_atr_block = 0
    n_adx_block = 0
    n_buy = 0
    n_rsi_avail = 0
    best_roc = -999

    macro_block = False
    macro_atr_val = False
    macro_adx_val = None

    if proxy_df_raw is not None:
        proxy_mask = pd.to_datetime(proxy_df_raw['date']) <= date_str
        proxy_slice = proxy_df_raw[proxy_mask].reset_index(drop=True)
        if not proxy_slice.empty:
            macro_atr_val = is_macro_atr_expansion(proxy_slice)
            adx_v = is_macro_adx_weak(proxy_slice)
            macro_adx_val = adx_v
            if macro_atr_val and adx_v is not None and adx_v < 20:
                macro_block = True

    for code in codes:
        df = response.bar_data.get(code, pd.DataFrame())
        if df.empty:
            continue
        date_mask = pd.to_datetime(df['date']) <= date_str
        df_slice = df[date_mask].reset_index(drop=True)
        if len(df_slice) < config.EMPTY_DAY:
            continue

        df_slice = roc_indicator.compute(df_slice, n=config.roc_n, m=config.roc_m)
        df_slice = ma_indicator.compute(df_slice, periods=[config.ma_period])
        df_slice = rsi_indicator.compute(df_slice, n=config.rsi_period)

        latest = df_slice.iloc[-1]
        roc_val = latest.get('roc')
        rsi_val = latest.get('rsi')

        try:
            roc_val = float(roc_val) if roc_val is not None else None
            rsi_val = float(rsi_val) if rsi_val is not None else None
        except (ValueError, TypeError):
            continue

        if roc_val is None or np.isnan(roc_val):
            continue

        if roc_val > best_roc:
            best_roc = roc_val

        if roc_val >= config.buy_roc_edge:
            n_roc_pass += 1
            buy_ok = True

            if rsi_val is not None and not np.isnan(rsi_val):
                n_rsi_avail += 1
                if rsi_val >= config.rsi_enhance_below:
                    n_rsi_block += 1
                    buy_ok = False
                else:
                    n_buy += 1
            else:
                n_buy += 1

            if buy_ok:
                df_extra = df_slice.copy()
                df_extra = atr_indicator.compute(df_extra, n=20)
                df_extra = atr200_indicator.compute(df_extra, n=200)
                atr_exp = compute_atr_expansion(df_extra)
                if atr_exp is True:
                    n_atr_block += 1
                    n_buy -= 1

                df_extra2 = df_slice.copy()
                df_extra2 = adx_indicator.compute(df_extra2, n=14)
                adx_v2 = compute_adx(df_extra2)
                if adx_v2 is not None and adx_v2 < 20:
                    n_adx_block += 1
                    n_buy -= 1

    RECORD["date"].append(date_str)
    RECORD["n_roc_pass"].append(n_roc_pass)
    RECORD["n_rsi_block"].append(n_rsi_block)
    RECORD["n_atr_block"].append(n_atr_block)
    RECORD["n_adx_block"].append(n_adx_block)
    RECORD["n_buy"].append(max(0, n_buy))
    RECORD["macro_block"].append(macro_block)
    RECORD["macro_atr"].append(macro_atr_val)
    RECORD["macro_adx"].append(macro_adx_val if macro_adx_val is not None else -1)
    RECORD["best_roc"].append(best_roc)
    RECORD["n_rsi_avail"].append(n_rsi_avail)

    if month_key != last_month:
        indicator = "[熔断]" if macro_block else "[正常]"
        sample_snapshots.append(
            f"  {date_str} {indicator} ROC达标={n_roc_pass} RSI挡={n_rsi_block} "
            f"ATR挡={n_atr_block} ADX挡={n_adx_block} 最终买入={n_buy} "
            f"最佳ROC={best_roc:.1f} 宏ATR={macro_atr_val} 宏ADX={macro_adx_val}"
        )
        last_month = month_key

    if (ti + 1) % 200 == 0:
        print(f"  进度: {ti+1}/{len(trading_dates)}")

print("\n=== 月度采样 ===")
for s in sample_snapshots:
    print(s)

df_record = pd.DataFrame(RECORD)

print("\n\n===== 第一部分：2022之前(2018~2021)为什么不参与？ =====")
early = df_record[df_record['date'] <= '2021-12-31']
total_days = len(early)
macro_block_days = early[early['macro_block'] == True]
macro_block_pct = len(macro_block_days) / total_days * 100 if total_days else 0
no_signal_days = early[early['n_roc_pass'] == 0]
rsi_block_only = early[(early['n_roc_pass'] > 0) & (early['n_rsi_block'] > 0) & (early['macro_block'] == False) & (early['n_buy'] == 0)]

print(f"期间交易日: {total_days} 天")
print(f"没有任何标ROC≥{config.buy_roc_edge}%的天数: {len(no_signal_days)} ({len(no_signal_days)/total_days*100:.1f}%)")
print(f"宏观熔断天数: {len(macro_block_days)} ({macro_block_pct:.1f}%)")
print(f"ROC达标但最终买入=0的天数: {len(rsi_block_only)} ({len(rsi_block_only)/total_days*100:.1f}%)")

if len(no_signal_days) > 0:
    non_zero_days = early[early['n_roc_pass'] > 0]
    print(f"\nROC有达标信号的天数（共 {len(non_zero_days)} 天）中:")
    if len(non_zero_days) > 0:
        stats_early = non_zero_days[['n_roc_pass', 'n_rsi_block', 'n_atr_block', 'n_adx_block', 'n_buy', 'best_roc']].describe()
        print(stats_early)

print("\n--- 2018~2021期间 各个过滤器的阻挡分析 ---")
buy_good = early[early['n_buy'] > 0]
print(f"最终有买入信号的天数: {len(buy_good)} / {total_days} ({len(buy_good)/total_days*100:.1f}%)")
if len(buy_good) > 0:
    print("  有买入信号的日期示例（每季度一次）:")
    last_q = None
    for _, row in buy_good.iterrows():
        q = row['date'][:7]
        if q != last_q:
            print(f"    {row['date']}: ROC达标={int(row['n_roc_pass'])} 最终买入={int(row['n_buy'])}")
            last_q = q

print("\n--- ROC达标但被RSI过滤的详细分析 ---")
roc_pass_rsi_block = early[(early['n_roc_pass'] > 0) & (early['n_rsi_block'] > 0)]
print(f"ROC达标→RSI过滤的天数: {len(roc_pass_rsi_block)}")
if len(roc_pass_rsi_block) > 0:
    print(f"  平均ROC达标数: {roc_pass_rsi_block['n_roc_pass'].mean():.1f}")
    print(f"  平均RSI过滤数: {roc_pass_rsi_block['n_rsi_block'].mean():.1f}")
    print(f"  示例日期:")
    for _, row in roc_pass_rsi_block.head(10).iterrows():
        print(f"    {row['date']}: ROC达标={int(row['n_roc_pass'])} RSI过滤={int(row['n_rsi_block'])}")

print("\n--- 2018~2021最佳ROC分布 ---")
best_roc_early = early[early['best_roc'] > -900]['best_roc']
print(f"  最佳ROC分位数: 10%={best_roc_early.quantile(0.1):.1f} 25%={best_roc_early.quantile(0.25):.1f} "
      f"50%={best_roc_early.median():.1f} 75%={best_roc_early.quantile(0.75):.1f} "
      f"90%={best_roc_early.quantile(0.9):.1f}")

print(f"\n  交叉池中任意标的 ROC≥{config.buy_roc_edge}% 的概率: "
      f"{(early['best_roc'] >= config.buy_roc_edge).sum() / total_days * 100:.1f}%")

print("\n\n===== 第二部分：2024-09为什么不参与？ =====")
sept = df_record[(df_record['date'] >= '2024-09-01') & (df_record['date'] <= '2024-09-30')]
print(f"\n2024年9月交易日: {len(sept)} 天")
for _, row in sept.iterrows():
    indicator = "[🔴熔断]" if row['macro_block'] else "[🟢正常]"
    print(f"  {row['date']} {indicator} ROC达标={int(row['n_roc_pass'])} RSI挡={int(row['n_rsi_block'])} "
          f"ATR挡={int(row['n_atr_block'])} ADX挡={int(row['n_adx_block'])} 最终买入={int(row['n_buy'])} "
          f"最佳ROC={row['best_roc']:.1f} 宏ATR={row['macro_atr']} 宏ADX={row['macro_adx']}")

print("\n--- 2024-09: 每天每标的详细阻断分析 ---")
for _, row in sept.iterrows():
    date_str = row['date']
    reasons = []
    if row['macro_block']:
        reasons.append(f"宏观熔断(ATR扩张+ADX={row['macro_adx']:.1f}<20)")
    if row['n_roc_pass'] == 0:
        reasons.append(f"0只标的ROC≥{config.buy_roc_edge}%(最佳={row['best_roc']:.1f})")
    if row['n_rsi_block'] > 0:
        reasons.append(f"{int(row['n_rsi_block'])}只被RSI≥{config.rsi_enhance_below}过滤")
    if row['n_atr_block'] > 0:
        reasons.append(f"{int(row['n_atr_block'])}只被ATR扩张过滤")
    if row['n_adx_block'] > 0:
        reasons.append(f"{int(row['n_adx_block'])}只被ADX<20过滤")
    print(f"  {date_str}: {' | '.join(reasons) if reasons else '无阻挡 → 应有买入'}")

print("\n--- 宏观阻断代理(510300)在2024-09的状态 ---")
if proxy_df_raw is not None:
    proxy_sept = proxy_df_raw.copy()
    proxy_sept['date'] = pd.to_datetime(proxy_sept['date'])
    proxy_sept = proxy_sept[(proxy_sept['date'] >= '2024-09-01') & (proxy_sept['date'] <= '2024-09-30')]
    for _, row in proxy_sept.iterrows():
        d = row['date'].strftime('%Y-%m-%d')
        mask = pd.to_datetime(proxy_df_raw['date']) <= row['date']
        sl = proxy_df_raw[mask].reset_index(drop=True)
        atr_exp = is_macro_atr_expansion(sl)
        adx_v = is_macro_adx_weak(sl)
        print(f"  {d}: close={row['close']} macro_ATR_exp={atr_exp} macro_ADX={adx_v}")

print("\n--- 2024-09: ROC(22)各标的实际值 ---")
for _, row in sept.iterrows():
    date_str = row['date']
    date_dt = pd.Timestamp(date_str)
    if row['best_roc'] > 0:
        roc_list = []
        for code in codes:
            df = response.bar_data.get(code, pd.DataFrame())
            if df.empty:
                continue
            date_mask = pd.to_datetime(df['date']) <= date_str
            df_slice = df[date_mask].reset_index(drop=True)
            if len(df_slice) < config.EMPTY_DAY:
                continue
            df_slice = roc_indicator.compute(df_slice, n=config.roc_n, m=config.roc_m)
            roc_val = float(df_slice.iloc[-1].get('roc', np.nan) or np.nan)
            if not np.isnan(roc_val):
                roc_list.append((code, roc_val))
        roc_list.sort(key=lambda x: x[1], reverse=True)
        if roc_list:
            top5 = [f"{c}={v:.1f}%" for c, v in roc_list[:5]]
            print(f"  {date_str}: TOP5 ROC → {', '.join(top5)}")

print("\n\n===== 第三部分：什么时候才真正有买入信号？ =====")
all_buy = df_record[df_record['n_buy'] > 0]
if len(all_buy) > 0:
    buy_dates = all_buy['date'].tolist()
    print(f"有买入信号的总天数: {len(buy_dates)} / {len(df_record)}")

    print("\n按年份统计有买入信号的天数:")
    df_record['year'] = pd.to_datetime(df_record['date']).dt.year
    yearly = df_record.groupby('year').agg(
        total_days=('date', 'count'),
        buy_days=('n_buy', lambda x: (x > 0).sum()),
        macro_block_days=('macro_block', 'sum'),
        no_roc_days=('n_roc_pass', lambda x: (x == 0).sum()),
    )
    yearly['buy_pct'] = yearly['buy_days'] / yearly['total_days'] * 100
    yearly['macro_block_pct'] = yearly['macro_block_days'] / yearly['total_days'] * 100
    yearly['no_roc_pct'] = yearly['no_roc_days'] / yearly['total_days'] * 100
    for yr, row in yearly.iterrows():
        print(f"  {yr}: 总{int(row['total_days'])}天 | 买入{int(row['buy_days'])}天({row['buy_pct']:.1f}%) | "
              f"熔断{int(row['macro_block_days'])}天({row['macro_block_pct']:.1f}%) | "
              f"0信号{int(row['no_roc_days'])}天({row['no_roc_pct']:.1f}%)")
else:
    print("没有任何买入信号！所有日期都被过滤了。")

print("\n===== 诊断完成 =====")
