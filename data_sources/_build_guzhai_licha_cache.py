"""股债利差历史数据构建脚本。
一次性运行：下载 HS300 PE + 10Y国债收益率 → 计算利差 → 缓存到 data/guzhai_licha.csv。
"""
import os
import pandas as pd
import akshare as ak
from loguru import logger

_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
_CACHE_FILE = os.path.join(_CACHE_DIR, 'guzhai_licha.csv')


def build_cache() -> str:
    """构建股债利差历史缓存。返回缓存文件路径。"""
    os.makedirs(_CACHE_DIR, exist_ok=True)

    # === 下载沪深300 PE（静态 + TTM） ===
    logger.info("下载沪深300历史PE数据...")
    pe_raw = ak.stock_index_pe_lg(symbol="沪深300")
    pe = pe_raw[['日期', '静态市盈率', '滚动市盈率']].copy()
    pe.columns = ['date', 'pe_static', 'pe_ttm']
    pe['date'] = pd.to_datetime(pe['date'])
    pe = pe.dropna(subset=['pe_static', 'pe_ttm'])
    logger.info(f"PE: {len(pe)} rows, {pe['date'].min()} ~ {pe['date'].max()}")

    # === 下载10年期国债收益率 ===
    logger.info("下载10年期国债收益率历史数据...")
    bond_raw = ak.bond_zh_us_rate()
    bond = bond_raw[['日期', '中国国债收益率10年']].copy()
    bond.columns = ['date', 'bond_10y']
    bond['date'] = pd.to_datetime(bond['date'])
    bond = bond.dropna(subset=['bond_10y'])
    logger.info(f"国债: {len(bond)} rows, {bond['date'].min()} ~ {bond['date'].max()}")

    # === 合并 ===
    merged = pd.merge(pe, bond, on='date', how='inner')
    merged = merged.sort_values('date').reset_index(drop=True)

    # === 计算股债利差 ===
    # 滚动双倍：1/TTM_PE - 2 × 国债收益率
    merged['double_ttm_licha'] = (1 / merged['pe_ttm']) - (2 * merged['bond_10y'] / 100)
    # 静态单倍：1/static_PE - 国债收益率
    merged['single_static_licha'] = (1 / merged['pe_static']) - (merged['bond_10y'] / 100)

    # 转换为百分比形式（与 guzhai_licha.py 一致）
    merged['double_ttm_licha_pct'] = merged['double_ttm_licha'] * 100
    merged['single_static_licha_pct'] = merged['single_static_licha'] * 100

    # === 保存 ===
    merged.to_csv(_CACHE_FILE, index=False, encoding='utf-8')
    logger.info(f"缓存已保存: {_CACHE_FILE} ({len(merged)} rows)")
    logger.info(f"利差范围: 双倍={merged['double_ttm_licha_pct'].min():.1f}%~{merged['double_ttm_licha_pct'].max():.1f}%")
    logger.info(f"利差范围: 单倍={merged['single_static_licha_pct'].min():.1f}%~{merged['single_static_licha_pct'].max():.1f}%")

    return _CACHE_FILE


if __name__ == '__main__':
    build_cache()