# @layer: research
"""AH溢价与中概互联/恒生科技ETF联动分析"""

import sys
import os
import pandas as pd
import numpy as np
from loguru import logger

from quantforge.data_sources.sina_feed import _fetch_sina_kline_raw, _parse_sina_response

# ---------------------------------------------------------------------------
# 1. 读取AH综合溢价数据
# ---------------------------------------------------------------------------
AH_PATH = os.path.join(os.path.dirname(__file__), "..", "results", "ah_premium_research", "ah_composite_index.csv")
df_ah = pd.read_csv(AH_PATH)
df_ah["date"] = df_ah["date"].astype(str)
df_ah = df_ah.drop_duplicates(subset=["date"], keep="last").sort_values("date").reset_index(drop=True)
logger.info(f"AH溢价数据: {len(df_ah)} 条, {df_ah['date'].iloc[0]} ~ {df_ah['date'].iloc[-1]}")

# ---------------------------------------------------------------------------
# 2. 拉取ETF数据
# ---------------------------------------------------------------------------
ETF_CODES = {
    "159605": "中概互联ETF",
    "513180": "恒生科技ETF",
}

etf_dfs = {}
for code, name in ETF_CODES.items():
    try:
        raw = _fetch_sina_kline_raw(code)
        df = _parse_sina_response(raw)
        df = df.drop_duplicates(subset=["date"], keep="last").sort_values("date").reset_index(drop=True)
        etf_dfs[code] = df
        logger.info(f"{name}({code}): {len(df)} 条, {df['date'].iloc[0]} ~ {df['date'].iloc[-1]}")
    except Exception as e:
        logger.warning(f"{name}({code}) 拉取失败: {e}")

if not etf_dfs:
    logger.error("所有ETF数据拉取失败，退出")
    sys.exit(1)

# ---------------------------------------------------------------------------
# 3. 对齐到共同日期
# ---------------------------------------------------------------------------
merged = df_ah[["date", "composite_premium"]].copy()
for code in etf_dfs:
    etf_close = etf_dfs[code][["date", "close"]].rename(columns={"close": f"{code}_close"})
    merged = merged.merge(etf_close, on="date", how="inner")

merged = merged.sort_values("date").reset_index(drop=True)
logger.info(f"共同日期: {len(merged)} 条, {merged['date'].iloc[0]} ~ {merged['date'].iloc[-1]}")

if len(merged) < 20:
    logger.error(f"共同日期不足 ({len(merged)} 条)，无法分析")
    sys.exit(1)

# 计算各ETF的对数收益率
for code in etf_dfs:
    merged[f"{code}_ret"] = np.log(merged[f"{code}_close"] / merged[f"{code}_close"].shift(1))

# ---------------------------------------------------------------------------
# a) AH综合溢价 vs ETF的滚动相关性（252日窗口）
# ---------------------------------------------------------------------------
logger.info("=" * 70)
logger.info("a) AH综合溢价 vs ETF 滚动相关性 (252日窗口)")
logger.info("=" * 70)

window = min(252, len(merged) // 2)
if window < 20:
    logger.warning(f"数据不足以计算滚动相关（窗口={window}）")
else:
    for code in etf_dfs:
        col_name = f"{code}_close"
        roll_corr = merged["composite_premium"].rolling(window).corr(merged[col_name])
        merged[f"{code}_roll_corr"] = roll_corr
        latest_corr = roll_corr.dropna().iloc[-1] if not roll_corr.dropna().empty else np.nan
        mean_corr = roll_corr.dropna().mean()
        min_corr = roll_corr.dropna().min()
        max_corr = roll_corr.dropna().max()
        logger.info(f"  {ETF_CODES[code]}({code}):")
        logger.info(f"    最新滚动相关: {latest_corr:.4f}")
        logger.info(f"    均值: {mean_corr:.4f}, 最小: {min_corr:.4f}, 最大: {max_corr:.4f}")

# ---------------------------------------------------------------------------
# b) 按溢价分位分析ETF前向收益
# ---------------------------------------------------------------------------
logger.info("")
logger.info("=" * 70)
logger.info("b) 按溢价分位分析ETF前向收益 (5/10/20/60日)")
logger.info("=" * 70)

# 动态计算分位阈值
premium = merged["composite_premium"].dropna()
q_low = premium.quantile(0.25)
q_high = premium.quantile(0.75)
logger.info(f"  溢价分位阈值: <25% = {q_low:.2f}, 25-75% = {q_low:.2f}~{q_high:.2f}, >75% = {q_high:.2f}")

horizons = [5, 10, 20, 60]

def assign_bucket(p):
    if p < q_low:
        return "低溢价(<25%)"
    elif p > q_high:
        return "高溢价(>75%)"
    else:
        return "中等溢价(25-75%)"

merged["bucket"] = merged["composite_premium"].apply(assign_bucket)

for code in etf_dfs:
    logger.info(f"  --- {ETF_CODES[code]}({code}) ---")
    close_col = f"{code}_close"
    results_rows = []
    for bucket_name in ["低溢价(<25%)", "中等溢价(25-75%)", "高溢价(>75%)"]:
        bucket_mask = merged["bucket"] == bucket_name
        bucket_count = bucket_mask.sum()
        row = {"分位组": bucket_name, "样本数": bucket_count}
        for h in horizons:
            fwd_ret = merged[close_col].shift(-h) / merged[close_col] - 1
            mean_ret = fwd_ret[bucket_mask].mean()
            hit_rate = (fwd_ret[bucket_mask] > 0).mean()
            row[f"{h}日均收益"] = f"{mean_ret*100:.2f}%"
            row[f"{h}日胜率"] = f"{hit_rate*100:.1f}%"
        results_rows.append(row)
    results_df = pd.DataFrame(results_rows)
    logger.info("\n" + results_df.to_string(index=False))

# ---------------------------------------------------------------------------
# c) 溢价年度均值和中位数变化趋势
# ---------------------------------------------------------------------------
logger.info("")
logger.info("=" * 70)
logger.info("c) 溢价年度均值和中位数变化趋势")
logger.info("=" * 70)

merged["year"] = pd.to_datetime(merged["date"]).dt.year
annual_stats = merged.groupby("year").agg(
    均值=("composite_premium", "mean"),
    中位数=("composite_premium", "median"),
    样本数=("composite_premium", "count"),
).round(2)

logger.info("\n" + annual_stats.to_string())

# 趋势判断
if len(annual_stats) >= 2:
    first_mean = annual_stats["均值"].iloc[0]
    last_mean = annual_stats["均值"].iloc[-1]
    first_med = annual_stats["中位数"].iloc[0]
    last_med = annual_stats["中位数"].iloc[-1]
    logger.info(f"  均值变化: {first_mean:.2f} → {last_mean:.2f} ({'↓下行' if last_mean < first_mean else '↑上行'})")
    logger.info(f"  中位数变化: {first_med:.2f} → {last_med:.2f} ({'↓下行' if last_med < first_med else '↑上行'})")

# ---------------------------------------------------------------------------
# d) 溢价年度标准差变化
# ---------------------------------------------------------------------------
logger.info("")
logger.info("=" * 70)
logger.info("d) 溢价年度标准差变化")
logger.info("=" * 70)

annual_std = merged.groupby("year").agg(
    标准差=("composite_premium", "std"),
    最大值=("composite_premium", "max"),
    最小值=("composite_premium", "min"),
).round(2)

logger.info("\n" + annual_std.to_string())

if len(annual_std) >= 2:
    first_std = annual_std["标准差"].iloc[0]
    last_std = annual_std["标准差"].iloc[-1]
    logger.info(f"  标准差变化: {first_std:.2f} → {last_std:.2f} ({'↓缩小' if last_std < first_std else '↑扩大'})")

# ---------------------------------------------------------------------------
# e) 溢价分位的年度分布变化
# ---------------------------------------------------------------------------
logger.info("")
logger.info("=" * 70)
logger.info("e) 溢价分位的年度分布变化")
logger.info("=" * 70)

# 用全样本分位阈值
annual_bucket = merged.groupby("year")["bucket"].value_counts().unstack(fill_value=0)
annual_bucket_pct = annual_bucket.div(annual_bucket.sum(axis=1), axis=0) * 100
annual_bucket_pct = annual_bucket_pct.round(1)

# 确保所有bucket列存在
for b in ["低溢价(<25%)", "中等溢价(25-75%)", "高溢价(>75%)"]:
    if b not in annual_bucket_pct.columns:
        annual_bucket_pct[b] = 0.0

annual_bucket_pct = annual_bucket_pct[["低溢价(<25%)", "中等溢价(25-75%)", "高溢价(>75%)"]]
logger.info("\n各年度溢价分位分布 (%):\n")
logger.info(annual_bucket_pct.to_string())

# 汇总: 各分位组的计数
logger.info("\n各年度溢价分位分布 (天数):")
annual_bucket_count = annual_bucket[["低溢价(<25%)", "中等溢价(25-75%)", "高溢价(>75%)"]]
logger.info("\n" + annual_bucket_count.to_string())

logger.info("")
logger.info("=" * 70)
logger.info("分析完成")
logger.info("=" * 70)