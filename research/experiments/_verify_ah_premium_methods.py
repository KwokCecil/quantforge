"""
AH溢价 分位建模方法对比
三种方法预测中概互联/恒生ETF前向收益的能力对比：
  A) 原始滚动分位 — 2年窗口溢价分位
  B) 绝对水平 — 全样本绝对溢价三等分
  C) 去趋势分位 — 溢价偏离2年滚动中位数的残差分位

# @layer: research
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from loguru import logger

from quantforge.data_sources.sina_feed import _fetch_sina_kline_raw, _parse_sina_response

COMPOSITE_PATH = os.path.join(os.path.dirname(__file__), '..', 'results', 'ah_premium_research', 'ah_composite_index.csv')

# 测试的 ETF
ETF_TARGETS = {
    '159605': '中概互联',
    '159920': '恒生ETF',
}

FW_DAYS = [10, 20, 60]


def load_composite():
    df = pd.read_csv(COMPOSITE_PATH, index_col=0, parse_dates=True)
    df = df[~df.index.duplicated(keep='last')]
    return df


def load_etf(code):
    try:
        raw = _fetch_sina_kline_raw(code)
        df = _parse_sina_response(raw)
        df['date'] = pd.to_datetime(df['date'])
        df = df.drop_duplicates(subset=['date'], keep='last')
        df = df.set_index('date').sort_index()
        return df[['close']].rename(columns={'close': f'{code}_close'})
    except Exception as e:
        logger.warning(f"ETF {code}: {e}")
        return pd.DataFrame()


def compute_methods(df):
    """对溢价序列计算三种分位方法"""
    premium = df['composite_premium'].dropna()
    result = pd.DataFrame(index=premium.index)

    # === 方法A: 原始滚动分位 (2yr) ===
    result['A_rolling_pct'] = premium.rolling(504, min_periods=126).apply(
        lambda x: (x.iloc[-1] > x).mean(), raw=False
    )

    # === 方法B: 绝对水平三等分 (全样本固定阈值) ===
    lo = premium.quantile(0.33)
    hi = premium.quantile(0.67)
    result['B_absolute_tercile'] = np.where(premium < lo, 0, np.where(premium < hi, 1, 2))
    # 归一化到 0-1 方便比较
    result['B_absolute_pct'] = result['B_absolute_tercile'] / 2.0

    # === 方法C: 去趋势分位 ===
    # 溢价偏离 504日滚动中位数的残差
    rolling_median = premium.rolling(504, min_periods=126).median()
    residual = premium - rolling_median
    result['C_detrended'] = residual
    # 残差的滚动分位
    result['C_residual_pct'] = residual.rolling(504, min_periods=126).apply(
        lambda x: (x.iloc[-1] > x).mean(), raw=False
    )

    # 保留原始溢价
    result['premium'] = premium
    return result


def test_signal(methods_df, etf_df, etf_name):
    """测试某种分位方法对ETF前向收益的预测能力"""
    merged = methods_df.join(etf_df, how='inner')
    close_col = [c for c in merged.columns if c.endswith('_close')][0]

    # 前向收益
    for d in FW_DAYS:
        merged[f'fw_{d}d'] = merged[close_col].pct_change(d).shift(-d)

    results = []
    for method_name, pct_col in [
        ('A_滚动2yr分位', 'A_rolling_pct'),
        ('B_绝对水平分位', 'B_absolute_pct'),
        ('C_去趋势残余分位', 'C_residual_pct'),
    ]:
        valid = merged[pct_col].notna()
        series = merged.loc[valid, pct_col]

        # 低组 (< 0.33 分位) vs 高组 (> 0.67 分位)
        lo_thresh = series.quantile(0.33)
        hi_thresh = series.quantile(0.67)

        for d in FW_DAYS:
            fw_col = f'fw_{d}d'
            lo_mask = (merged[pct_col] < lo_thresh) & valid
            hi_mask = (merged[pct_col] > hi_thresh) & valid

            lo_ret = merged.loc[lo_mask, fw_col].dropna().mean() * 100
            hi_ret = merged.loc[hi_mask, fw_col].dropna().mean() * 100
            lo_pos = (merged.loc[lo_mask, fw_col].dropna() > 0).mean()
            hi_pos = (merged.loc[hi_mask, fw_col].dropna() > 0).mean()

            spread = hi_ret - lo_ret
            results.append({
                '方法': method_name,
                '周期': f'{d}日',
                '低组收益': lo_ret,
                '高组收益': hi_ret,
                '高低差': spread,
                '低组胜率': lo_pos,
                '高组胜率': hi_pos,
                'N低': int(lo_mask.sum()),
                'N高': int(hi_mask.sum()),
            })

    return pd.DataFrame(results)


def run():
    logger.info("=" * 60)
    logger.info("AH溢价 分位建模方法对比")
    logger.info("=" * 60)

    # 加载数据
    composite = load_composite()
    logger.info(f"综合溢价: {len(composite)} 日")

    # 计算三种方法
    methods = compute_methods(composite)

    # 当前值对比
    last = methods.iloc[-1]
    logger.info(f"\n当前溢价: {last['premium']:.1f}%")
    logger.info(f"  方法A(滚动分位): {last['A_rolling_pct']:.1%}")
    logger.info(f"  方法B(绝对三分位): {last['B_absolute_tercile']:.0f}/2 (={last['B_absolute_pct']:.1%})")
    logger.info(f"  方法C(去趋势分位): {last['C_residual_pct']:.1%} (残差={last['C_detrended']:.1f}%)")

    # 对每个 ETF 测试信号
    for code, name in ETF_TARGETS.items():
        logger.info(f"\n{'─' * 40}")
        logger.info(f"【{name} ({code})】信号预测力对比")
        logger.info(f"{'─' * 40}")

        etf = load_etf(code)
        if etf.empty:
            continue

        result = test_signal(methods, etf, name)

        # 按周期和方法分组打印
        for d in FW_DAYS:
            logger.info(f"\n  ▶ {d}日前向收益:")
            subset = result[result['周期'] == f'{d}日']
            for _, row in subset.iterrows():
                star = " ⭐" if abs(row['高低差']) >= 1.0 else ""
                logger.info(
                    f"    {row['方法']}: "
                    f"低组 {row['低组收益']:+.2f}% ({row['低组胜率']:.0%}) → "
                    f"高组 {row['高组收益']:+.2f}% ({row['高组胜率']:.0%}) "
                    f"| 高低差 {row['高低差']:+.2f}pp{star}"
                )

    # 方法总结
    logger.info(f"\n{'=' * 60}")
    logger.info("方法评估总结:")
    logger.info("  方法A(滚动分位): 被结构性下行趋势污染，近期一直在低位")
    logger.info("  方法B(绝对水平): 全样本固定阈值，不受趋势影响")
    logger.info("  方法C(去趋势分位): 剥离趋势后衡量偏离程度，最干净")
    logger.info("  判断标准: 高低差越大 → 信号区分力越强")

    # 保存结果
    out_dir = os.path.join(os.path.dirname(__file__), '..', 'results', 'ah_premium_research')
    methods.to_csv(os.path.join(out_dir, 'ah_methods_comparison.csv'), encoding='utf-8-sig')
    logger.info(f"\n结果保存: {out_dir}/ah_methods_comparison.csv")


if __name__ == '__main__':
    run()