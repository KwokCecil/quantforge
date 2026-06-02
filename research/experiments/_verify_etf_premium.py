"""
ETF溢价埋伏研究 — 验证脚本
使用 akshare fund_etf_spot_ths (同花顺源) 获取ETF最新净值，
与 fund_etf_hist_sina (新浪源) 的收盘价计算折溢价率，
识别高溢价常用ETF用于埋伏策略。

核心问题：哪些跨境ETF频繁出现溢价？溢价模式是否有惯性？

# @layer: research
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
import akshare as ak
from loguru import logger

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), '..', 'results', 'etf_premium_research')
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 常见跨境ETF候选（日均成交额大、流动性好）
TARGET_ETFS = {
    '513100': '国泰纳指100(QDII)',
    '159941': '广发纳指100(QDII)',
    '513500': '博时标普500(QDII)',
    '159866': '工银日经225(QDII)',
    '513520': '华安日经225(QDII)',
    '513050': '易方达中概互联',
    '159605': '广发中概互联',
    '159920': '华夏恒生ETF',
    '510900': '易方达H股ETF',
    '513030': '华安德国30(QDII)',
}


def fetch_etf_price_history(code, start='2018'):
    """从新浪源获取ETF历史价格。"""
    try:
        df = ak.fund_etf_hist_sina(symbol=f'sh{code}' if code.startswith('5') else f'sz{code}')
        df.rename(columns={
            'date': 'date', 'open': 'open', 'high': 'high',
            'low': 'low', 'close': 'close', 'volume': 'volume',
        }, inplace=True)
        return df
    except Exception as e:
        logger.warning(f"fetch {code} hist: {e}")
        return pd.DataFrame()


def fetch_etf_spot_with_nav():
    """从同花顺获取所有ETF最新净值快照。"""
    try:
        df = ak.fund_etf_spot_ths()
        return df
    except Exception as e:
        logger.warning(f"fetch spot: {e}")
        return pd.DataFrame()


def run():
    logger.info("=== ETF溢价埋伏研究 ===")

    # 1. 获取全量ETF净值快照（含有前一日单位净值）
    spot = fetch_etf_spot_with_nav()
    if spot.empty:
        logger.error("无法获取ETF净值数据")
        return

    logger.info(f"获取 {len(spot)} 只基金净值快照")

    # 2. 筛选候选ETF
    spot_targets = spot[spot['基金代码'].isin(TARGET_ETFS.keys())].copy()

    # 3. 拉取每个ETF的价格历史，计算折溢价率趋势
    results = []
    for _, row in spot_targets.iterrows():
        code = row['基金代码']
        name = TARGET_ETFS.get(code, row.get('基金名称', '?'))
        nav_today = row.get('最新-单位净值')
        nav_prev = row.get('前一日-单位净值')

        # 获取价格历史
        hist = fetch_etf_price_history(code)
        if hist.empty or len(hist) < 60:
            continue

        latest_close = hist['close'].iloc[-1] if 'close' in hist.columns else None

        # 计算折溢价率: (收盘价 - NAV) / NAV × 100%
        premium_current = None
        if latest_close and nav_today and not np.isnan(nav_today):
            premium_current = (latest_close / nav_today - 1) * 100
        elif latest_close and nav_prev and not np.isnan(nav_prev):
            premium_current = (latest_close / nav_prev - 1) * 100

        # 历史折溢价: 用最新NAV近似（实际应逐日对照，NAV源不完善暂时近似）
        hist_premium_series = None
        hist_plus_days = 0  # 正溢价天数
        hist_extreme_days = 0  # >3% 溢价天数
        if latest_close and nav_prev and not np.isnan(nav_prev):
            hist['premium'] = (hist['close'] / nav_prev - 1) * 100
            hist_premium_series = hist['premium']
            hist_plus_days = int((hist_premium_series > 0).sum())
            hist_extreme_days = int((hist_premium_series > 3).sum())

        # 近期溢价惯性（按周统计正溢价连续天数）
        if hist_premium_series is not None and len(hist_premium_series) >= 5:
            recent_5d_premium = hist_premium_series.iloc[-5:].mean()
        else:
            recent_5d_premium = None

        results.append({
            'code': code,
            'name': name,
            'close': latest_close,
            'nav': nav_today if nav_today and not np.isnan(nav_today) else nav_prev,
            'premium_pct': round(premium_current, 2) if premium_current else None,
            'total_days': len(hist),
            'premium_days': hist_plus_days,
            'extreme_days_3pct': hist_extreme_days,
            'premium_ratio': round(hist_plus_days / len(hist) * 100, 1),
            'recent_5d_premium': round(recent_5d_premium, 2) if recent_5d_premium else None,
            '申购状态': row.get('申购状态', '?'),
            '赎回状态': row.get('赎回状态', '?'),
        })

    # 4. 输出
    result_df = pd.DataFrame(results)
    result_df = result_df.sort_values('premium_pct', ascending=False, na_position='last')

    output_csv = os.path.join(OUTPUT_DIR, 'etf_premium_analysis.csv')
    result_df.to_csv(output_csv, index=False, encoding='utf-8-sig')
    logger.info(f"\n结果保存: {output_csv}")
    logger.info(f"\n=== ETF溢价分析结果 ===\n{result_df.to_string(index=False)}")

    # 5. 关键发现
    if not result_df.empty and 'premium_pct' in result_df.columns:
        positive = result_df[result_df['premium_pct'].notna() & (result_df['premium_pct'] > 0)]
        logger.info(f"\n{len(positive)}/{len(result_df)} 只ETF当前正溢价")


if __name__ == '__main__':
    run()