"""
AH溢价深度研究 — 多角度信号验证
测试假设：
1. 低溢价(均值回归下限) → 随后大跌，H股跌更多 → 溢价回升
2. 高溢价(极端值) → 南下资金增加 → A股承压
3. 溢价分位作为A/H股仓位调节信号
4. AH溢价与沪深300/恒生ETF相关性

数据源：新浪K线 (A股) + akshare stock_zh_ah_daily (H股，新浪源)

# @layer: research
"""
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
import akshare as ak
from loguru import logger

# 复用新浪K线拉取
try:
    from quantforge.data_sources.sina_feed import _fetch_sina_kline_raw, _parse_sina_response
except ModuleNotFoundError:
    from data_sources.sina_feed import _fetch_sina_kline_raw, _parse_sina_response

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), '..', 'results', 'ah_premium_research')
os.makedirs(OUTPUT_DIR, exist_ok=True)

# AH股对映射（H股代码 → A股代码），选取流动性好的大市值标的
AH_PAIRS = {
    '02318': '601318',   # 中国平安
    '02628': '601628',   # 中国人寿
    '03968': '600036',   # 招商银行
    '01288': '601288',   # 农业银行
    '01398': '601398',   # 工商银行
    '00939': '601939',   # 建设银行
    '03988': '601988',   # 中国银行
    '00386': '600028',   # 中国石化
    '00857': '600857',   # 中国石油
    '01088': '601088',   # 中国神华
}
PAIR_LABELS = {k: v for k, v in {
    '02318': '中国平安', '02628': '中国人寿', '03968': '招商银行',
    '01288': '农业银行', '01398': '工商银行', '00939': '建设银行',
    '03988': '中国银行', '00386': '中国石化', '00857': '中国石油',
    '01088': '中国神华',
}.items()}

# 回测参数
AH_START = '2018'
AH_END = '2026'
FX_RATE = 0.91  # 1 HKD ≈ 0.91 RMB


def fetch_pair_data(h_code, a_code):
    """拉取AH个股对双端价格，合并计算溢价率。"""
    # H股
    try:
        df_h = ak.stock_zh_ah_daily(symbol=h_code, start_year=AH_START, end_year=AH_END, adjust='')
        df_h = df_h.rename(columns={'日期': 'date', '收盘': 'close_h'})
        df_h['date'] = pd.to_datetime(df_h['date'])
    except Exception as e:
        logger.warning(f"H {h_code}: {e}")
        return pd.DataFrame()

    # A股
    try:
        raw = _fetch_sina_kline_raw(a_code)
        df_a = _parse_sina_response(raw)
        df_a = df_a.rename(columns={'close': 'close_a'})
        df_a['date'] = pd.to_datetime(df_a['date'])
    except Exception as e:
        logger.warning(f"A {a_code}: {e}")
        return pd.DataFrame()

    if df_h.empty or df_a.empty:
        return pd.DataFrame()

    merged = pd.merge(df_a[['date', 'close_a']], df_h[['date', 'close_h']], on='date', how='inner')
    if merged.empty:
        return pd.DataFrame()

    merged = merged.drop_duplicates(subset=['date'], keep='last')
    merged['premium'] = (merged['close_a'] / (merged['close_h'] * FX_RATE) - 1) * 100
    merged = merged.set_index('date').sort_index()
    return merged


def compute_premium_percentile(series, window=504):
    """计算溢价的滚动分位（2年窗口 ≈ 504交易日）。"""
    return series.rolling(window, min_periods=126).apply(
        lambda x: (x.iloc[-1] > x).mean(), raw=False
    )


def analyze_premium_regime(df, name, look_forward_days=[5, 10, 20, 60, 120]):
    """
    核心分析：溢价分位对未来收益的影响
    - 低溢价区间（<25分位）：A股相对便宜，假设随后H股跌更多
    - 高溢价区间（>75分位）：A股相对贵，假设随后A股涨不动
    """
    df = df.copy()
    if len(df) < 504:
        return None

    # 滚动分位
    df['pct'] = compute_premium_percentile(df['premium'])

    # 前向收益
    for d in look_forward_days:
        df[f'fw_a_{d}d'] = df['close_a'].pct_change(d).shift(-d)
        df[f'fw_h_{d}d'] = df['close_h'].pct_change(d).shift(-d)

    results = {'name': name}
    for d in look_forward_days:
        # === 低溢价区间（< 25%分位）===
        low_mask = (df['pct'] < 0.25) & df['pct'].notna()
        if low_mask.sum() > 10:
            a_ret_low = df.loc[low_mask, f'fw_a_{d}d'].dropna().mean()
            h_ret_low = df.loc[low_mask, f'fw_h_{d}d'].dropna().mean()
            a_pos_low = (df.loc[low_mask, f'fw_a_{d}d'].dropna() > 0).mean()
            h_pos_low = (df.loc[low_mask, f'fw_h_{d}d'].dropna() > 0).mean()
        else:
            a_ret_low = h_ret_low = a_pos_low = h_pos_low = float('nan')

        # === 高溢价区间（> 75%分位）===
        high_mask = (df['pct'] > 0.75) & df['pct'].notna()
        if high_mask.sum() > 10:
            a_ret_high = df.loc[high_mask, f'fw_a_{d}d'].dropna().mean()
            h_ret_high = df.loc[high_mask, f'fw_h_{d}d'].dropna().mean()
            a_pos_high = (df.loc[high_mask, f'fw_a_{d}d'].dropna() > 0).mean()
            h_pos_high = (df.loc[high_mask, f'fw_h_{d}d'].dropna() > 0).mean()
        else:
            a_ret_high = h_ret_high = a_pos_high = h_pos_high = float('nan')

        # === 中性区间（25%-75%）对比基准 ===
        neutral_mask = (df['pct'] >= 0.25) & (df['pct'] <= 0.75) & df['pct'].notna()
        if neutral_mask.sum() > 10:
            a_ret_neutral = df.loc[neutral_mask, f'fw_a_{d}d'].dropna().mean()
            h_ret_neutral = df.loc[neutral_mask, f'fw_h_{d}d'].dropna().mean()
        else:
            a_ret_neutral = h_ret_neutral = float('nan')

        results[f'{d}d_a_low'] = a_ret_low
        results[f'{d}d_h_low'] = h_ret_low
        results[f'{d}d_a_high'] = a_ret_high
        results[f'{d}d_h_high'] = h_ret_high
        results[f'{d}d_a_neutral'] = a_ret_neutral
        results[f'{d}d_h_neutral'] = h_ret_neutral
        results[f'{d}d_n_low'] = low_mask.sum()
        results[f'{d}d_n_high'] = high_mask.sum()

    return results


def build_composite(datasets):
    """从多个AH对构建综合溢价指数：取各对溢价的等权平均。"""
    premiums = {}
    for name, df in datasets.items():
        if 'premium' not in df.columns:
            continue
        # 去重：同一天取最后一个值
        s = df['premium']
        s = s[~s.index.duplicated(keep='last')]
        premiums[name] = s
    if not premiums:
        return pd.DataFrame()
    # 使用 concat 避免重复索引问题
    composite = pd.concat(premiums, axis=1)
    composite.columns = list(premiums.keys())
    composite = composite.dropna(how='all')
    composite['composite_premium'] = composite.mean(axis=1)
    composite['composite_pct'] = compute_premium_percentile(composite['composite_premium'])
    composite['spread'] = composite.max(axis=1) - composite.min(axis=1)  # 分歧度
    return composite


def run():
    logger.info("=" * 60)
    logger.info("AH溢价深度研究 — 多角度信号验证")
    logger.info("=" * 60)

    # === 阶段0：数据拉取 ===
    datasets = {}
    for h_code, a_code in AH_PAIRS.items():
        name = PAIR_LABELS.get(h_code, h_code)
        logger.info(f"拉取 {name} ({h_code}/{a_code})...")
        df = fetch_pair_data(h_code, a_code)
        if not df.empty:
            datasets[name] = df
            logger.info(f"  {len(df)} 日, 溢价均值={df['premium'].mean():.1f}%, 范围=[{df['premium'].min():.1f}%, {df['premium'].max():.1f}%]")
        else:
            logger.warning(f"  {name} 数据缺失")

    if not datasets:
        logger.error("无数据")
        return

    logger.info(f"\n共 {len(datasets)} 对AH股数据就绪")

    # === 阶段1：均值回归假设 — 低溢价 → 后续大跌(尤其H股)？ ===
    logger.info("\n" + "=" * 40)
    logger.info("【假设1】低溢价(<25分位) → 后续A/H股表现？(用户假设: H股跌更多)")
    logger.info("=" * 40)

    all_results = []
    for name, df in datasets.items():
        res = analyze_premium_regime(df, name, [5, 10, 20, 60, 120])
        if res:
            all_results.append(res)

    result_df = pd.DataFrame(all_results)

    # 打印低溢价区间的A/H对比
    for d in [5, 10, 20, 60, 120]:
        a_vals = result_df[f'{d}d_a_low'].dropna()
        h_vals = result_df[f'{d}d_h_low'].dropna()
        n_vals = result_df[f'{d}d_h_neutral'].dropna()
        n_low = result_df[f'{d}d_n_low'].dropna()

        if len(a_vals) > 0 and len(h_vals) > 0:
            a_mean = a_vals.mean() * 100
            h_mean = h_vals.mean() * 100
            n_mean = n_vals.mean() * 100
            diff = a_mean - h_mean
            logger.info(
                f"  {d:3d}日前向: A股 {a_mean:+.2f}%  vs  H股 {h_mean:+.2f}%  "
                f"(差值{diff:+.2f}%pp, 中性H={n_mean:+.2f}%, 样本={int(n_low.mean())})"
            )

    # === 阶段2：高溢价作为风险预警 ===
    logger.info("\n" + "=" * 40)
    logger.info("【假设2】高溢价(>75分位) → A股贵了 → 后续A股收益降低？")
    logger.info("=" * 40)

    for d in [5, 10, 20, 60, 120]:
        a_vals = result_df[f'{d}d_a_high'].dropna()
        h_vals = result_df[f'{d}d_h_high'].dropna()
        n_vals = result_df[f'{d}d_h_neutral'].dropna()
        if len(a_vals) > 0 and len(h_vals) > 0:
            a_mean = a_vals.mean() * 100
            h_mean = h_vals.mean() * 100
            n_mean = n_vals.mean() * 100
            logger.info(
                f"  {d:3d}日前向: A股 {a_mean:+.2f}%  vs  H股 {h_mean:+.2f}%  "
                f"(中性H={n_mean:+.2f}%)"
            )

    # === 阶段3：综合溢价指数与沪深300/恒生ETF关系 ===
    logger.info("\n" + "=" * 40)
    logger.info("【假设3】综合溢价指数 vs 宽基指数（沪深300/恒生ETF）")
    logger.info("=" * 40)

    composite = build_composite(datasets)
    if composite.empty:
        logger.warning("无法构建综合指数")
    else:
        logger.info(f"综合溢价: 均值={composite['composite_premium'].mean():.1f}%, "
                    f"最新={composite['composite_premium'].iloc[-1]:.1f}%, "
                    f"分位={composite['composite_pct'].iloc[-1]:.1%}")

        # 拉取沪深300和恒生ETF作为参照
        try:
            raw_300 = _fetch_sina_kline_raw('510300')
            df_300 = _parse_sina_response(raw_300)
            df_300 = df_300.rename(columns={'close': 'hs300', 'date': 'date'})
            df_300['date'] = pd.to_datetime(df_300['date'])

            raw_hsi = _fetch_sina_kline_raw('159920')
            df_hsi = _parse_sina_response(raw_hsi)
            df_hsi = df_hsi.rename(columns={'close': 'heng_sheng', 'date': 'date'})
            df_hsi['date'] = pd.to_datetime(df_hsi['date'])
        except Exception as e:
            logger.warning(f"拉取ETF基准: {e}")
            df_300 = df_hsi = pd.DataFrame()

        if not df_300.empty and not df_hsi.empty:
            benchmarks = pd.merge(df_300[['date', 'hs300']], df_hsi[['date', 'heng_sheng']], on='date', how='inner')
            benchmarks = benchmarks.set_index('date')

            # 对齐综合溢价
            merged = composite.join(benchmarks, how='inner')
            logger.info(f"对齐后共 {len(merged)} 个交易日")

            # 计算各分位区间下的ETF后续收益
            for d in [5, 10, 20, 60]:
                merged[f'fw_hs300_{d}d'] = merged['hs300'].pct_change(d).shift(-d)
                merged[f'fw_hengs_{d}d'] = merged['heng_sheng'].pct_change(d).shift(-d)

                # 低溢价(< 25%) vs 高溢价(> 75%)
                for regime, label, mask_fn in [
                    ('low', '低溢价<25%', lambda m: m['composite_pct'] < 0.25),
                    ('high', '高溢价>75%', lambda m: m['composite_pct'] > 0.75),
                ]:
                    mask = mask_fn(merged) & merged['composite_pct'].notna()
                    if mask.sum() < 5:
                        continue
                    hs300_ret = merged.loc[mask, f'fw_hs300_{d}d'].dropna().mean() * 100
                    heng_ret = merged.loc[mask, f'fw_hengs_{d}d'].dropna().mean() * 100
                    hs300_pos = (merged.loc[mask, f'fw_hs300_{d}d'].dropna() > 0).mean()
                    heng_pos = (merged.loc[mask, f'fw_hengs_{d}d'].dropna() > 0).mean()
                    logger.info(
                        f"  {label}, {d:3d}日前向: 沪深300 {hs300_ret:+.2f}% "
                        f"(胜率{hs300_pos:.0%}) | 恒生 {heng_ret:+.2f}% "
                        f"(胜率{heng_pos:.0%}) | N={int(mask.sum())}"
                    )

            # === 阶段4：溢价分位变化作为趋势信号 ===
            logger.info("\n" + "=" * 40)
            logger.info("【假设4】溢价分位变化方向 → 市场方向信号")
            logger.info("=" * 40)

            merged['pct_change_20d'] = merged['composite_pct'].diff(20)
            rising = merged['pct_change_20d'] > 0.05
            falling = merged['pct_change_20d'] < -0.05

            for d in [10, 20, 60]:
                for label, mask, sign in [
                    ('溢价上升(>+5pp/月)', rising, 'rising'),
                    ('溢价下降(>-5pp/月)', falling, 'falling'),
                ]:
                    m = mask & merged['composite_pct'].notna()
                    if m.sum() < 5:
                        continue
                    hs300_ret = merged.loc[m, f'fw_hs300_{d}d'].dropna().mean() * 100
                    heng_ret = merged.loc[m, f'fw_hengs_{d}d'].dropna().mean() * 100
                    logger.info(
                        f"  {label}: {d}日前向 沪深300 {hs300_ret:+.2f}% "
                        f"恒生 {heng_ret:+.2f}% (N={int(m.sum())})"
                    )

    # 保存完整数据
    output_csv = os.path.join(OUTPUT_DIR, 'ah_deep_research.csv')
    result_df.to_csv(output_csv, index=False, encoding='utf-8-sig')
    if not composite.empty:
        composite.to_csv(os.path.join(OUTPUT_DIR, 'ah_composite_index.csv'), encoding='utf-8-sig')

    logger.info(f"\n结果已保存: {OUTPUT_DIR}/")


if __name__ == '__main__':
    run()