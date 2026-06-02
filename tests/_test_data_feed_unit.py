# @layer: unit
"""CachedDataFeed 单元测试：增量更新、复权修正核验、缓存过期检测。

覆盖 CachedDataFeed 所有关键边界路径：
  - _compute_fq_correction: 正常修正 / 无需修正 / 重叠不足 / 比率异常 / 分段不一致
  - _apply_correction_ratio: 全 OHLC 列修正 / 缺列不崩溃
  - _incremental_update: 正常合并 / 复权失败追加新日期 / 复权失败无新日期 / 空旧缓存
  - _need_full_update: 无记录 / 起点过晚 / 缓存过期 / 正常跳过
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock

from quantforge.core.data_feed import CachedDataFeed, DataFeed, DataRequest, DataResponse


# ============================================================
# 辅助：构造 OHLC DataFrame
# ============================================================

def _make_ohlc_df(dates, close_prices, open_prices=None, high_prices=None, low_prices=None):
    """用日期列表和收盘价构造标准 OHLCV DataFrame。"""
    n = len(dates)
    if open_prices is None:
        open_prices = close_prices
    if high_prices is None:
        high_prices = [c * 1.01 for c in close_prices]
    if low_prices is None:
        low_prices = [c * 0.99 for c in close_prices]
    return pd.DataFrame({
        "date": dates,
        "open": open_prices,
        "high": high_prices,
        "low": low_prices,
        "close": close_prices,
        "volume": [1000000] * n,
    })


def _date_range(n, start="2026-01-05"):
    """生成 n 个交易日日期字符串。"""
    return pd.date_range(start, periods=n, freq="B").strftime("%Y-%m-%d").tolist()


def _make_cached_feed(source=None, cache_dir=None):
    """构造 CachedDataFeed 实例。source=None 时使用空 Mock。"""
    if source is None:
        source = MagicMock(spec=DataFeed)
        source.get_data.return_value = DataResponse()
    if cache_dir is None:
        cache_dir = tempfile.mkdtemp(prefix="test_cache_")
    return CachedDataFeed(source=source, cache_dir=cache_dir)


# ============================================================
# _compute_fq_correction 测试
# ============================================================

def test_fq_correction_returns_ratio():
    """前复权使历史价格整体压低 5% → 应检测到 0.95 的修正系数。"""
    feed = _make_cached_feed()
    dates = _date_range(30)
    old_close = [100.0] * 30
    new_close = [95.0] * 30
    df_old = _make_ohlc_df(dates, old_close)
    df_new = _make_ohlc_df(dates, new_close)

    result = feed._compute_fq_correction(df_old, df_new, "test_code")

    assert result is not None
    assert abs(result - 0.95) < 0.001, f"期望 0.95，实际 {result}"


def test_fq_correction_no_change():
    """新旧价格完全一致 → 返回 1.0（无需修正）。"""
    feed = _make_cached_feed()
    dates = _date_range(30)
    df = _make_ohlc_df(dates, [100.0] * 30)

    result = feed._compute_fq_correction(df, df, "test_code")

    assert result == 1.0


def test_fq_correction_near_unity():
    """比率在 1.0±0.0002 以内 → 视为无需修正，返回 1.0。"""
    feed = _make_cached_feed()
    dates = _date_range(30)
    old_close = [100.0] * 30
    new_close = [100.0001] * 30  # ratio = 1.000001，在阈值内
    df_old = _make_ohlc_df(dates, old_close)
    df_new = _make_ohlc_df(dates, new_close)

    result = feed._compute_fq_correction(df_old, df_new, "test_code")

    assert result == 1.0


def test_fq_correction_insufficient_overlap():
    """重叠日期 < 15 天 → 跳过修正，返回 1.0。"""
    feed = _make_cached_feed()
    dates = _date_range(10)
    old_close = [100.0] * 10
    new_close = [95.0] * 10
    df_old = _make_ohlc_df(dates, old_close)
    df_new = _make_ohlc_df(dates, new_close)

    result = feed._compute_fq_correction(df_old, df_new, "test_code")

    assert result == 1.0


def test_fq_correction_ratio_too_high():
    """比率 > 1.002（价格"升高"）→ 异常，返回 None。"""
    feed = _make_cached_feed()
    dates = _date_range(30)
    old_close = [100.0] * 30
    new_close = [101.0] * 30  # ratio = 1.01 > 1.002
    df_old = _make_ohlc_df(dates, old_close)
    df_new = _make_ohlc_df(dates, new_close)

    result = feed._compute_fq_correction(df_old, df_new, "test_code")

    assert result is None


def test_fq_correction_ratio_too_low():
    """比率 < 0.85 → 异常偏低，返回 None。"""
    feed = _make_cached_feed()
    dates = _date_range(30)
    old_close = [100.0] * 30
    new_close = [80.0] * 30  # ratio = 0.80 < 0.85
    df_old = _make_ohlc_df(dates, old_close)
    df_new = _make_ohlc_df(dates, new_close)

    result = feed._compute_fq_correction(df_old, df_new, "test_code")

    assert result is None


def test_fq_correction_segment_deviation():
    """三段比率不一致（偏差 > 0.1%）→ 核验不通过，返回 None。

    构造：前 1/3 比率 0.94，中 1/3 比率 0.95，后 1/3 比率 0.93。
    整体中位数 ≈ 0.94，后段偏差 ≈ |0.93-0.94|/0.94 ≈ 1.06% > 0.1%。
    """
    feed = _make_cached_feed()
    n = 30
    dates = _date_range(n)

    old_close = [100.0] * n

    seg1 = int(n / 3)
    seg2 = int(n * 2 / 3)
    new_close = []
    for i in range(n):
        if i < seg1:
            new_close.append(94.0)
        elif i < seg2:
            new_close.append(95.0)
        else:
            new_close.append(93.0)

    df_old = _make_ohlc_df(dates, old_close)
    df_new = _make_ohlc_df(dates, new_close)

    result = feed._compute_fq_correction(df_old, df_new, "test_code")

    assert result is None


def test_fq_correction_handles_zero_or_nan_price():
    """旧价格为 0 或 NaN 的日期被跳过，不影响整体计算。"""
    feed = _make_cached_feed()
    n = 31  # 30 + 1 个异常日期
    dates = _date_range(n)

    old_close = [100.0] * n
    old_close[15] = 0.0     # 会被跳过
    old_close[16] = float('nan')  # 会被跳过

    new_close = [95.0] * n

    df_old = _make_ohlc_df(dates, old_close)
    df_new = _make_ohlc_df(dates, new_close)

    result = feed._compute_fq_correction(df_old, df_new, "test_code")

    # 跳过了 2 个异常日期，剩余 29 个有效重叠日 → 应正常计算
    assert result is not None
    assert abs(result - 0.95) < 0.001


def test_fq_correction_segment_consistent_passes():
    """三段比率一致（偏差 ≤ 0.1%）→ 核验通过。"""
    feed = _make_cached_feed()
    n = 30
    dates = _date_range(n)
    old_close = [100.0] * n

    # 轻微波动但三段中位数接近
    np.random.seed(42)
    noise = np.random.normal(0, 0.03, n)
    new_close = [100.0 * 0.95 + noise[i] for i in range(n)]

    df_old = _make_ohlc_df(dates, old_close)
    df_new = _make_ohlc_df(dates, new_close)

    result = feed._compute_fq_correction(df_old, df_new, "test_code")

    assert result is not None
    assert 0.949 < result < 0.951


# ============================================================
# _apply_correction_ratio 测试
# ============================================================

def test_apply_correction_all_ohlc():
    """所有 OHLC 列均乘以修正系数。"""
    feed = _make_cached_feed()
    dates = _date_range(5)
    df = _make_ohlc_df(dates, [100.0] * 5)

    result = feed._apply_correction_ratio(df, 0.95, "test_code")

    for col in ["open", "high", "low", "close"]:
        assert (result[col] == df[col] * 0.95).all(), f"{col} 列未正确修正"


def test_apply_correction_missing_columns():
    """只有 close 列存在时，仅修正 close，不崩溃。"""
    feed = _make_cached_feed()
    dates = _date_range(5)
    df = pd.DataFrame({"date": dates, "close": [100.0] * 5})

    result = feed._apply_correction_ratio(df, 0.90, "test_code")

    assert (result["close"] == 90.0).all()
    assert "open" not in result.columns


# ============================================================
# _incremental_update 测试
# ============================================================

def test_incremental_normal_merge():
    """正常增量更新：修正成功 → 旧数据修正 + 新旧合并 + 去重。"""
    cache_dir = tempfile.mkdtemp(prefix="test_cache_")

    old_dates = _date_range(10, "2026-01-05")
    overlap_dates = _date_range(15, "2026-01-12")  # 后 8 天与新数据重叠
    new_only_dates = _date_range(5, "2026-01-27")

    # 旧缓存：前 10 天
    df_old_cache = _make_ohlc_df(old_dates, [100.0] * 10)
    # 新数据：15 天（含重叠 + 纯新增），价格压低 5%
    all_new_dates = overlap_dates + new_only_dates
    df_new_source = _make_ohlc_df(all_new_dates, [95.0] * 20)

    mock_source = MagicMock(spec=DataFeed)
    mock_source.get_data.return_value = DataResponse(
        bar_data={"test_code": df_new_source},
    )

    with patch("quantforge.core.data_feed.read_fund_data", return_value=df_old_cache.to_dict(orient="records")):
        with patch("quantforge.core.data_feed.read_batch_params", return_value={"fund_actual_date_ranges": {}}):
            feed = CachedDataFeed(source=mock_source, cache_dir=cache_dir)
            result = feed._incremental_update("test_code", "2026-01-01", "2026-02-01", "daily_k")

    assert not result.empty
    assert "date" in result.columns

    # 旧数据（前 10 天）已被修正 ×0.95
    old_part = result[result["date"].isin(old_dates)]
    assert len(old_part) == 10
    for _, row in old_part.iterrows():
        assert abs(row["close"] - 95.0) < 0.01, f"旧日期 {row['date']} 未修正"

    # 新数据保留
    new_part = result[result["date"].isin(new_only_dates)]
    assert len(new_part) == 5

    # 重叠日期以 new 为准（keep='last'）
    overlap_part = result[result["date"].isin(overlap_dates)]
    assert len(overlap_part) == len(set(overlap_dates))
    for _, row in overlap_part.iterrows():
        assert abs(row["close"] - 95.0) < 0.01


def test_incremental_correction_fails_appends_new_dates():
    """复权修正失败但有新日期 → 追加新日期，旧数据不变。"""
    cache_dir = tempfile.mkdtemp(prefix="test_cache_")

    old_dates = _date_range(10, "2026-01-05")
    new_only_dates = _date_range(5, "2026-01-27")

    df_old_cache = _make_ohlc_df(old_dates, [100.0] * 10)
    # 新数据价格异常偏高 → 触发 ratio > 1.002
    df_new_source = _make_ohlc_df(new_only_dates, [105.0] * 5)

    mock_source = MagicMock(spec=DataFeed)
    mock_source.get_data.return_value = DataResponse(
        bar_data={"test_code": df_new_source},
    )

    with patch("quantforge.core.data_feed.read_fund_data", return_value=df_old_cache.to_dict(orient="records")):
        with patch("quantforge.core.data_feed.read_batch_params", return_value={"fund_actual_date_ranges": {}}):
            feed = CachedDataFeed(source=mock_source, cache_dir=cache_dir)
            result = feed._incremental_update("test_code", "2026-01-01", "2026-02-01", "daily_k")

    assert not result.empty

    # 旧数据价格未被修改
    old_part = result[result["date"].isin(old_dates)]
    assert len(old_part) == 10
    for _, row in old_part.iterrows():
        assert abs(row["close"] - 100.0) < 0.01, f"旧日期 {row['date']} 价格不应被修改"

    # 新数据已追加
    new_part = result[result["date"].isin(new_only_dates)]
    assert len(new_part) == 5

    # 总记录 = 10 + 5 = 15
    assert len(result) == 15


def test_incremental_correction_fails_no_new_dates():
    """复权修正失败且无新日期 → 返回空 DataFrame。"""
    cache_dir = tempfile.mkdtemp(prefix="test_cache_")

    old_dates = _date_range(20, "2026-01-05")
    # 新数据全在旧日期范围内，价格异常偏高
    same_dates = old_dates[:15]
    df_old_cache = _make_ohlc_df(old_dates, [100.0] * 20)
    df_new_source = _make_ohlc_df(same_dates, [105.0] * 15)

    mock_source = MagicMock(spec=DataFeed)
    mock_source.get_data.return_value = DataResponse(
        bar_data={"test_code": df_new_source},
    )

    with patch("quantforge.core.data_feed.read_fund_data", return_value=df_old_cache.to_dict(orient="records")):
        with patch("quantforge.core.data_feed.read_batch_params", return_value={"fund_actual_date_ranges": {}}):
            feed = CachedDataFeed(source=mock_source, cache_dir=cache_dir)
            result = feed._incremental_update("test_code", "2026-01-01", "2026-02-01", "daily_k")

    assert result.empty


def test_incremental_empty_old_cache():
    """旧缓存为空 → 直接返回新数据，不走复权修正。"""
    cache_dir = tempfile.mkdtemp(prefix="test_cache_")

    new_dates = _date_range(10, "2026-01-05")
    df_new_source = _make_ohlc_df(new_dates, [100.0] * 10)

    mock_source = MagicMock(spec=DataFeed)
    mock_source.get_data.return_value = DataResponse(
        bar_data={"test_code": df_new_source},
    )

    with patch("quantforge.core.data_feed.read_fund_data", return_value=None):
        with patch("quantforge.core.data_feed.read_batch_params", return_value={"fund_actual_date_ranges": {}}):
            feed = CachedDataFeed(source=mock_source, cache_dir=cache_dir)
            result = feed._incremental_update("test_code", "2026-01-01", "2026-02-01", "daily_k")

    assert not result.empty
    assert len(result) == 10


def test_incremental_source_returns_empty():
    """数据源返回空 → 直接返回空 DataFrame。"""
    cache_dir = tempfile.mkdtemp(prefix="test_cache_")

    mock_source = MagicMock(spec=DataFeed)
    mock_source.get_data.return_value = DataResponse(
        bar_data={"test_code": pd.DataFrame()},
    )

    with patch("quantforge.core.data_feed.read_fund_data", return_value=None):
        with patch("quantforge.core.data_feed.read_batch_params", return_value={"fund_actual_date_ranges": {}}):
            feed = CachedDataFeed(source=mock_source, cache_dir=cache_dir)
            result = feed._incremental_update("test_code", "2026-01-01", "2026-02-01", "daily_k")

    assert result.empty


# ============================================================
# _need_full_update 测试
# ============================================================

def test_need_full_no_ranges():
    """无 batch_params 记录 → 需要全量更新。"""
    feed = _make_cached_feed()
    assert feed._need_full_update({}, "test_code", "2026-01-01", "2026-12-31") is True


def test_need_full_cached_min_too_late():
    """缓存起点晚于请求起点 > 5 天 → 需要全量更新。"""
    feed = _make_cached_feed()
    batch_params = {
        "fund_actual_date_ranges": {
            "test_code": {"min_date": "2026-01-10", "max_date": "2026-05-01"},
        }
    }
    assert feed._need_full_update(batch_params, "test_code", "2026-01-01", "2026-05-19") is True


def test_need_full_cached_min_ok():
    """缓存起点在请求起点 5 天内 → 无需全量更新（只要不过期）。"""
    feed = _make_cached_feed()
    batch_params = {
        "fund_actual_date_ranges": {
            "test_code": {"min_date": "2026-01-03", "max_date": "2026-05-15"},
        }
    }
    # 缓存未过期（max_date 距 today 在 30 天内）
    assert feed._need_full_update(batch_params, "test_code", "2026-01-01", "2026-05-19") is False


def test_need_full_cache_stale():
    """缓存最新日期落后今天 > 30 天 → 触发全量更新。"""
    feed = _make_cached_feed()
    batch_params = {
        "fund_actual_date_ranges": {
            "test_code": {
                "min_date": "2026-01-01",
                "max_date": "2026-03-01",  # 比 2026-05-19 落后 79 天
            },
        }
    }
    assert feed._need_full_update(batch_params, "test_code", "2026-01-01", "2026-12-31") is True


def test_need_full_stale_but_past_end():
    """缓存过期但请求 end 是历史日期 → 不触发（ref_dt = end，而非 today）。"""
    feed = _make_cached_feed()
    batch_params = {
        "fund_actual_date_ranges": {
            "test_code": {
                "min_date": "2026-01-01",
                "max_date": "2026-02-28",
            },
        }
    }
    # end 是 2026-03-05，cached_max 是 2026-02-28，差距 5 天 < 30
    assert feed._need_full_update(batch_params, "test_code", "2026-01-01", "2026-03-05") is False


def test_need_full_invalid_date_format():
    """日期格式异常 → 不崩溃，正常处理。"""
    feed = _make_cached_feed()
    batch_params = {
        "fund_actual_date_ranges": {
            "test_code": {"min_date": "2026-01-01", "max_date": "not-a-date"},
        }
    }
    # 日期解析失败，过期检查跳过，min_date 检查通过 → False
    assert feed._need_full_update(batch_params, "test_code", "2026-01-01", "2026-05-19") is False


# ============================================================
# _calc_incremental_window 测试
# ============================================================

def test_calc_window_no_cached_max():
    """无缓存时返回默认 120 天。"""
    feed = _make_cached_feed()
    with patch("quantforge.core.data_feed.read_batch_params", return_value={}):
        window = feed._calc_incremental_window("test_code", "2026-05-19")
    assert window == 120


def test_calc_window_small_gap():
    """缓存差距 ≤ 30 天 → 返回 30 天窗口。"""
    feed = _make_cached_feed()
    bp = {"fund_actual_date_ranges": {"test_code": {"max_date": "2026-05-10"}}}
    with patch("quantforge.core.data_feed.read_batch_params", return_value=bp):
        window = feed._calc_incremental_window("test_code", "2026-05-19")
    assert window == 30


def test_calc_window_medium_gap():
    """缓存差距 31~120 天 → 返回 120 天。"""
    feed = _make_cached_feed()
    bp = {"fund_actual_date_ranges": {"test_code": {"max_date": "2026-02-01"}}}
    with patch("quantforge.core.data_feed.read_batch_params", return_value=bp):
        window = feed._calc_incremental_window("test_code", "2026-05-19")
    assert window == 120


def test_calc_window_large_gap():
    """缓存差距 > 120 天 → 返回 gap × 1.5。"""
    feed = _make_cached_feed()
    bp = {"fund_actual_date_ranges": {"test_code": {"max_date": "2025-12-01"}}}
    with patch("quantforge.core.data_feed.read_batch_params", return_value=bp):
        window = feed._calc_incremental_window("test_code", "2026-05-19")
    # gap = (2026-05-19 - 2025-12-01) = 169 days
    # window = int(169 * 1.5) = 253
    assert window == 253


# ============================================================
# update_cache 集成场景测试（mock 全部 IO）
# ============================================================

def test_update_cache_skips_empty_df():
    """_incremental_update 返回空 → update_cache 不写缓存。"""
    cache_dir = tempfile.mkdtemp(prefix="test_cache_")

    mock_source = MagicMock(spec=DataFeed)
    mock_source.get_data.return_value = DataResponse(
        bar_data={"test_code": pd.DataFrame()},
    )

    with patch("quantforge.core.data_feed.read_batch_params", return_value={}):
        with patch("quantforge.core.data_feed.write_fund_data") as mock_write:
            with patch("quantforge.core.data_feed.write_batch_params"):
                feed = CachedDataFeed(source=mock_source, cache_dir=cache_dir)
                feed.update_cache(["test_code"], "daily_k", "2026-01-01", "2026-05-19")

    # update_cache 内部：need_full_update → True（无缓存记录）→ 全量拉取 → df 为空 → 不保存
    mock_write.assert_not_called()


def test_update_cache_full_update_saves():
    """全量更新成功 → 缓存被写入。"""
    cache_dir = tempfile.mkdtemp(prefix="test_cache_")
    dates = _date_range(10)
    df_source = _make_ohlc_df(dates, [100.0] * 10)

    mock_source = MagicMock(spec=DataFeed)
    mock_source.get_data.return_value = DataResponse(
        bar_data={"test_code": df_source},
    )

    with patch("quantforge.core.data_feed.read_batch_params", return_value={}):
        with patch("quantforge.core.data_feed.write_fund_data") as mock_write:
            with patch("quantforge.core.data_feed.write_batch_params") as mock_write_bp:
                feed = CachedDataFeed(source=mock_source, cache_dir=cache_dir)
                feed.update_cache(["test_code"], "daily_k", "2026-01-01", "2026-05-19")

    mock_write.assert_called_once()
    mock_write_bp.assert_called_once()