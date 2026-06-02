# @layer: unit
"""回测辅助函数单元测试。

覆盖 backtest_core.py 中的纯函数：
- align_dataframes(): 多ETF日期对齐 + ffill
- _build_benchmark_from_bar_data(): 指数proxy映射 + 基准净值构建
- _filter_macro_by_date(): 宏观数据防未来泄漏切片
"""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from loguru import logger

from quantforge.core.backtest_core import (
    align_dataframes, _build_benchmark_from_bar_data, _filter_macro_by_date,
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)
logger.remove()
logger.add(sys.stderr, level="WARNING")


# ============================================================
# align_dataframes 测试
# ============================================================

def test_align_dataframes_basic():
    """两个DataFrame日期对齐：不同日期序列对齐后长度一致。"""
    df1 = pd.DataFrame({
        'date': ['2023-01-03', '2023-01-04', '2023-01-05', '2023-01-06'],
        'close': [1.0, 1.1, 1.2, 1.3],
        'volume': [100, 200, 300, 400],
    })
    df2 = pd.DataFrame({
        'date': ['2023-01-04', '2023-01-06'],
        'close': [2.0, 2.2],
        'volume': [500, 600],
    })

    result = align_dataframes([df1.copy(), df2.copy()])

    assert len(result) == 2
    assert len(result[0]) == 4
    assert len(result[1]) == 4  # df2 被对齐到 df1 的4个日期

    assert list(result[0]['date']) == ['2023-01-03', '2023-01-04', '2023-01-05', '2023-01-06']
    assert list(result[1]['date']) == ['2023-01-03', '2023-01-04', '2023-01-05', '2023-01-06']

    logger.success("  ✅ 两DataFrame日期对齐正确")


def test_align_dataframes_ffill():
    """ffill填充缺失日期：缺失日用前一有效值填充。"""
    df1 = pd.DataFrame({
        'date': ['2023-01-03', '2023-01-04', '2023-01-05'],
        'close': [1.0, 1.1, 1.2],
    })
    df2 = pd.DataFrame({
        'date': ['2023-01-03', '2023-01-05'],
        'close': [2.0, 2.2],
    })

    result = align_dataframes([df1.copy(), df2.copy()])

    # 2023-01-04 在 df2 中无数据，应被 ffill 为 2.0
    assert result[1]['close'].iloc[1] == 2.0, \
        f"ffill应填2.0，实际={result[1]['close'].iloc[1]}"
    assert result[1]['close'].iloc[2] == 2.2

    logger.success("  ✅ ffill填充缺失日期正确")


def test_align_dataframes_pre_ffill_nan():
    """前置NaN处理：首条有效数据之前的NaN被填充为首个有效值。"""
    df1 = pd.DataFrame({
        'date': ['2023-01-03', '2023-01-04', '2023-01-05', '2023-01-06'],
        'close': [1.0, 1.1, 1.2, 1.3],
    })
    df2 = pd.DataFrame({
        'date': ['2023-01-05', '2023-01-06'],
        'close': [2.0, 2.2],
    })

    result = align_dataframes([df1.copy(), df2.copy()])

    # 2023-01-03和2023-01-04在df2中无前置数据，应填充为首个有效值2.0
    assert result[1]['close'].iloc[0] == 2.0, f"前置NaN应填2.0，实际={result[1]['close'].iloc[0]}"
    assert result[1]['close'].iloc[1] == 2.0
    assert result[1]['close'].iloc[2] == 2.0
    assert result[1]['close'].iloc[3] == 2.2

    logger.success("  ✅ 前置NaN填充为首个有效值")


def test_align_dataframes_single():
    """单个DataFrame直接返回，不做变化。"""
    df = pd.DataFrame({
        'date': ['2023-01-03', '2023-01-04'],
        'close': [1.0, 1.1],
    })

    result = align_dataframes([df.copy()])

    assert len(result) == 1
    assert len(result[0]) == 2
    assert list(result[0]['close']) == [1.0, 1.1]

    logger.success("  ✅ 单个DataFrame直接返回")


def test_align_dataframes_empty():
    """空列表直接返回。"""
    result = align_dataframes([])
    assert result == []
    logger.success("  ✅ 空列表直接返回")


def test_align_dataframes_three():
    """三个DataFrame同时对齐。"""
    df1 = pd.DataFrame({
        'date': ['2023-01-03', '2023-01-04', '2023-01-05'],
        'close': [1.0, 1.1, 1.2],
    })
    df2 = pd.DataFrame({
        'date': ['2023-01-03', '2023-01-05'],
        'close': [2.0, 2.2],
    })
    df3 = pd.DataFrame({
        'date': ['2023-01-04', '2023-01-05'],
        'close': [3.0, 3.2],
    })

    result = align_dataframes([df1.copy(), df2.copy(), df3.copy()])

    assert len(result) == 3
    for r in result:
        assert len(r) == 3, "所有对齐后长度应一致"

    logger.success("  ✅ 三个DataFrame同时对齐")


# ============================================================
# _build_benchmark_from_bar_data 测试
# ============================================================

def test_build_benchmark_proxy_map():
    """指数代码通过proxy_map映射到ETF。"""
    bar_data = {
        '159915': pd.DataFrame({
            'date': ['2023-01-03', '2023-01-04', '2023-01-05'],
            'close': [1.0, 1.1, 1.2],
        }),
    }

    result = _build_benchmark_from_bar_data(bar_data, '399006')

    assert result is not None
    assert abs(result.iloc[0] - 1.0) < 0.0001
    assert abs(result.iloc[-1] - 1.2) < 0.0001

    logger.success("  ✅ 399006→159915 proxy映射正确")


def test_build_benchmark_direct_code():
    """代码本身在 bar_data 中时直接使用。"""
    bar_data = {
        '510050': pd.DataFrame({
            'date': ['2023-01-03', '2023-01-04'],
            'close': [2.0, 2.1],
        }),
    }

    result = _build_benchmark_from_bar_data(bar_data, '510050')

    assert result is not None
    assert abs(result.iloc[0] - 1.0) < 0.0001  # 归一化起始
    assert abs(result.iloc[-1] - 1.05) < 0.0001  # 2.1/2.0=1.05

    logger.success("  ✅ 直接代码匹配")


def test_build_benchmark_no_data():
    """无对应数据时返回None。"""
    bar_data = {
        '159915': pd.DataFrame({
            'date': ['2023-01-03'],
            'close': [1.0],
        }),
    }

    result = _build_benchmark_from_bar_data(bar_data, '000300')
    assert result is None, "000300不在proxy_map且不在bar_data中"

    logger.success("  ✅ 无数据返回None")


def test_build_benchmark_empty_df():
    """空DataFrame返回None。"""
    bar_data = {'159915': pd.DataFrame()}
    result = _build_benchmark_from_bar_data(bar_data, '399006')
    assert result is None

    logger.success("  ✅ 空DataFrame返回None")


def test_build_benchmark_no_close_column():
    """无close列的DataFrame返回None。"""
    bar_data = {
        '159915': pd.DataFrame({
            'date': ['2023-01-03'],
            'open': [1.0],
        }),
    }
    result = _build_benchmark_from_bar_data(bar_data, '399006')
    assert result is None

    logger.success("  ✅ 无close列返回None")


# ============================================================
# _filter_macro_by_date 测试
# ============================================================

def test_filter_macro_by_date_basic():
    """宏观数据按日期正确过滤，未来数据被排除。"""
    macro_data = {
        'guzhai_licha': [
            {'date': '2023-01-03', 'value': 0.05},
            {'date': '2023-01-04', 'value': 0.06},
            {'date': '2023-01-05', 'value': 0.07},
        ],
    }

    filtered = _filter_macro_by_date(macro_data, '2023-01-04')

    assert len(filtered['guzhai_licha']) == 2
    assert filtered['guzhai_licha'][0]['value'] == 0.05
    assert filtered['guzhai_licha'][1]['value'] == 0.06

    logger.success("  ✅ 宏观数据按日期过滤正确")


def test_filter_macro_by_date_future_blocked():
    """等于当前日期的数据可访问，大于的被排除。"""
    macro_data = {
        'indicator': [
            {'date': '2023-01-03', 'v': 1},
            {'date': '2023-01-04', 'v': 2},
            {'date': '2023-01-05', 'v': 3},
        ],
    }

    filtered = _filter_macro_by_date(macro_data, '2023-01-04')

    assert len(filtered['indicator']) == 2
    dates = [item['date'] for item in filtered['indicator']]
    assert dates == ['2023-01-03', '2023-01-04']
    assert all(d <= '2023-01-04' for d in dates)

    logger.success("  ✅ 未来数据被正确排除（<=日期，不含未来）")


def test_filter_macro_by_date_non_list_value():
    """非list类型的value直接保留。"""
    macro_data = {
        'config': {'param': 42},
        'series': [
            {'date': '2023-01-03', 'value': 10},
        ],
    }

    filtered = _filter_macro_by_date(macro_data, '2023-01-04')

    assert filtered['config'] == {'param': 42}  # 非list直接保留
    assert len(filtered['series']) == 1

    logger.success("  ✅ 非list值直接保留")


def test_filter_macro_by_date_empty_list():
    """全部是未来数据时返回空列表。"""
    macro_data = {
        'indicator': [
            {'date': '2023-01-05', 'v': 3},
            {'date': '2023-01-06', 'v': 4},
        ],
    }

    filtered = _filter_macro_by_date(macro_data, '2023-01-04')

    assert filtered['indicator'] == []

    logger.success("  ✅ 全未来数据返回空列表")


def test_filter_macro_by_date_missing_date_key():
    """dict缺少date键时用空字符串比较。"""
    macro_data = {
        'indicator': [
            {'value': 1},
            {'date': '2023-01-03', 'value': 2},
        ],
    }

    filtered = _filter_macro_by_date(macro_data, '2023-01-04')

    # item.get('date', '') <= '2023-01-04': '' <= '2023-01-04' → True
    assert len(filtered['indicator']) == 2

    logger.success("  ✅ 缺少date键时不崩溃，空字符串≤目标日期")


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("回测辅助函数单元测试")
    logger.info("=" * 60)
    try:
        test_align_dataframes_basic()
        test_align_dataframes_ffill()
        test_align_dataframes_pre_ffill_nan()
        test_align_dataframes_single()
        test_align_dataframes_empty()
        test_align_dataframes_three()
        test_build_benchmark_proxy_map()
        test_build_benchmark_direct_code()
        test_build_benchmark_no_data()
        test_build_benchmark_empty_df()
        test_build_benchmark_no_close_column()
        test_filter_macro_by_date_basic()
        test_filter_macro_by_date_future_blocked()
        test_filter_macro_by_date_non_list_value()
        test_filter_macro_by_date_empty_list()
        test_filter_macro_by_date_missing_date_key()
        logger.success("\n全部 16 项通过 ✅")
    except AssertionError as e:
        logger.error(f"\n❌ 失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        logger.error(f"\n❌ 异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)