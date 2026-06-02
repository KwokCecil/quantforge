"""
LOF极度异常溢价监控 — 研究脚本
不依赖历史数据，实时检查LOF折溢价率，对极度异常情况给出交易建议。

数据方案: 由于东方财富源受阻，使用 akshare fund_etf_spot_ths (同花顺ETF/LOF快照)
+ SinaFinanceFeed 拉取LOF收盘价组合计算溢价。

注意: fund_etf_spot_ths 包含部分LOF（LOF在THS中归类为ETF-like）。
对于纯LOF，使用新浪 API (SinaFinanceFeed 支持 LOF 代码如 sz161128)。

# @layer: research
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
import akshare as ak
from loguru import logger

from quantforge.data_sources.sina_feed import _detect_exchange
import requests

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), '..', 'results', 'lof_premium_monitor')
os.makedirs(OUTPUT_DIR, exist_ok=True)

# LOF溢价套利阈值
PREMIUM_THRESHOLD = 3.0    # 溢价>3% 视为机会
EXTREME_THRESHOLD = 5.0    # 溢价>5% 极度异常

# 常用LOF代码列表（流动性好的活跃LOF，来源：手动维护 + 基金类型筛选）
COMMON_LOFS = {
    # 原油/商品LOF（QDII，易溢价）
    '162411': '华宝油气LOF', '160216': '国泰商品LOF',
    '161129': '原油LOF易方达', '501018': '南方原油LOF',
    # 海外指数LOF（QDII，易溢价）
    '164824': '印度基金LOF', '160125': '中国互联LOF',
    '161831': '恒生医疗LOF', '160717': '恒生LOF',
    '160518': '博时弘泰LOF',
    # 热门A股LOF
    '161024': '军工LOF', '161725': '行业轮动LOF',
    '161028': '新能源车LOF', '161121': '银行LOF',
    '160643': '空天军工LOF', '161015': '钢铁LOF',
    '161019': '国债LOF',
    # 债券LOF（流动性好的）
    '161716': '招商双债LOF', '161820': '银华纯债LOF',
}
# 同时从THS数据中补充债券型LOF（THS包含部分LOF的债券型ETF）
LOF_FUND_TYPES = ['债券型', '其他']  # THS中可能包含的LOF类型



def fetch_lof_candidates_with_nav():
    """组合策略：手动词典 + THS债券型/其他型基金快照。返回 [(code, name, nav, 申购状态, 类型), ...]"""
    try:
        df_all = ak.fund_etf_spot_ths()
    except Exception as e:
        logger.error(f"fetch ths spot: {e}")
        return []

    # 从THS中筛选债券型/其他型（可能是LOF）
    ths_extra = df_all[df_all['基金类型'].isin(LOF_FUND_TYPES)]

    candidates = {}
    # 手动LOF字典
    for code, name in COMMON_LOFS.items():
        row = df_all[df_all['基金代码'] == code]
        if not row.empty:
            r = row.iloc[0]
            nav = r.get('最新-单位净值', None)
            if pd.isna(nav):
                nav = r.get('前一日-单位净值', None)
            candidates[code] = (name, nav, r.get('申购状态', '?'), r.get('基金类型', '?'))

    # 补充THS额外候选
    for _, r in ths_extra.iterrows():
        code = r['基金代码']
        if code in candidates:
            continue
        nav = r.get('最新-单位净值', None)
        if pd.isna(nav):
            nav = r.get('前一日-单位净值', None)
        if nav is not None and not pd.isna(nav):
            candidates[code] = (r['基金名称'], nav,
                               r.get('申购状态', '?'), r.get('基金类型', '?'))

    return list(candidates.items())



def fetch_lof_prices_batch_sina(codes):
    """批量从新浪获取LOF实时价格（新浪支持一次查询多个）。"""
    try:
        full_codes = []
        for code in codes:
            prefix = _detect_exchange(code)
            full_codes.append(f"{prefix}{code}")
        url = f"https://hq.sinajs.cn/list={','.join(full_codes)}"
        headers = {"Referer": "https://finance.sina.com.cn/"}
        resp = requests.get(url, headers=headers, timeout=15)
        resp.encoding = 'gbk'
        text = resp.text
        # 解析每行
        prices = {}
        for line in text.strip().split('\n'):
            if '=' not in line or '"' not in line:
                continue
            # 提取代码部分: var hq_str_sh501018="..."
            var_part = line.split('=')[0]
            code_part = var_part.replace('var hq_str_', '')
            fields = line.split('"')[1].split(',')
            if len(fields) > 3 and fields[3]:
                try:
                    prices[code_part] = float(fields[3])
                except ValueError:
                    pass
        return prices
    except Exception as e:
        logger.warning(f"batch fetch err: {e}")
        return {}


def compute_premium(price, nav):
    """计算折溢价率 = (price - nav) / nav × 100%"""
    if price is None or nav is None or np.isnan(price) or np.isnan(nav) or nav == 0:
        return None
    return round((price / nav - 1) * 100, 2)


def run():
    logger.info("=== LOF极度异常溢价监控 ===")

    # 1. 获取LOF候选（手动词典 + THS补充）
    candidates = fetch_lof_candidates_with_nav()
    if not candidates:
        logger.error("无法获取LOF数据")
        return

    logger.info(f"筛选 {len(candidates)} 只LOF候选")

    # 2. 批量获取新浪实时价格
    codes = [c[0] for c in candidates]
    prices = fetch_lof_prices_batch_sina(codes)
    logger.info(f"新浪返回 {len(prices)} 个价格")

    # 3. 计算折溢价率
    results = []
    for code, (name, nav, purchase_status, fund_type) in candidates:

        prefix = _detect_exchange(code)
        sina_key = f"{prefix}{code}"
        price = prices.get(sina_key)

        premium = compute_premium(price, nav)
        if premium is None:
            continue

        # 只关注溢价（LOF折价无法做场内申购套利）
        if premium > PREMIUM_THRESHOLD:
            level = 'EXTREME' if premium > EXTREME_THRESHOLD else 'OPPORTUNITY'
            advice = '建议场内申购→T+2卖出' if purchase_status == '开放' else '申购受限，仅观察'
            results.append({
                '代码': code,
                '名称': name,
                '场内价': price,
                '单位净值': nav,
                '溢价率%': premium,
                '级别': level,
                '申购状态': purchase_status,
                '类型': fund_type,
                '建议': advice,
            })

    # 3. 输出
    if results:
        result_df = pd.DataFrame(results).sort_values('溢价率%', ascending=False)
        output_csv = os.path.join(OUTPUT_DIR, 'lof_extreme_premium.csv')
        result_df.to_csv(output_csv, index=False, encoding='utf-8-sig')
        logger.info(f"\n=== LOF极度溢价列表 ({len(results)}只) ===\n{result_df.to_string(index=False)}")
        logger.info(f"\n结果: {output_csv}")

        # 异常汇总
        extreme = result_df[result_df['溢价率%'] > EXTREME_THRESHOLD]
        if not extreme.empty:
            logger.warning(f"\n⚠️ {len(extreme)} 只LOF溢价>5%极度异常！")
    else:
        logger.info(f"当前无LOF触发 {PREMIUM_THRESHOLD}% 溢价阈值")


if __name__ == '__main__':
    run()