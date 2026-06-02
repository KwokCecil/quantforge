# @layer: integration
"""验证SinaFinanceFeed 数据源连通性与返回格式"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from quantforge.data_sources.sina_feed import SinaFinanceFeed
from quantforge.core.data_feed import DataRequest

feed = SinaFinanceFeed()
resp = feed.get_data(DataRequest(
    codes=['510300', '510050', '159915', '588000'],
    data_type='daily_k',
    start='2016-01-01',
    end='2026-05-09'
))

REQUIRED_COLS = {'date', 'open', 'high', 'low', 'close'}

assert resp.bar_data is not None and len(resp.bar_data) > 0, "bar_data 不能为空"
for code, df in resp.bar_data.items():
    assert not df.empty, f"{code} 数据为空"
    assert REQUIRED_COLS.issubset(set(df.columns)), f"{code} 缺少必需列: {REQUIRED_COLS - set(df.columns)}"
    assert len(df) > 100, f"{code} 数据量过少: {len(df)} 行"
    print(f'{code}: {len(df)} rows, {df.date.iloc[0]} ~ {df.date.iloc[-1]}')
    print(f'  close: {df.close.iloc[0]:.3f} -> {df.close.iloc[-1]:.3f}')
    print(f'  cols: {list(df.columns)}')

print("\nAll SinaFinanceFeed checks passed!")