# @layer: unit
"""AH溢价信号模块 单元测试。

测试覆盖：
- 数据加载（正常/文件缺失/缺列）
- 方法A 滚动分位计算（合成数据 + 真实CSV一致性）
- 方法B 绝对三等分计算（合成数据 + 阈值验证）
- compute() 状态输出完整性
- 边界情况（短数据、NaN处理、阈值不变性）
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
import pytest

from quantforge.indicators.ah_premium_signal import (
    AHPremiumCalculator,
    AHPremiumState,
    AHPremiumThresholds,
    _WINDOW,
    _MIN_PERIODS,
)


# ══════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════

@pytest.fixture
def synthetic_csv():
    """合成溢价CSV: 1000日，溢价逐步上升 0→100%。"""
    dates = pd.date_range('2020-01-01', periods=1000, freq='B')
    premium = np.linspace(0, 100, 1000)
    df = pd.DataFrame({'composite_premium': premium}, index=dates)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8-sig') as f:
        df.to_csv(f.name)
        path = f.name
    yield path
    os.unlink(path)


@pytest.fixture
def real_csv():
    """真实研究产出的综合溢价CSV。"""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'results', 'ah_premium_research', 'ah_composite_index.csv'
    )
    if not os.path.exists(path):
        pytest.skip("真实CSV不存在，跳过需要真实数据的测试")
    return path


@pytest.fixture
def empty_csv():
    """空CSV（只有表头）。"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8-sig') as f:
        f.write('date,composite_premium\n')
        path = f.name
    yield path
    os.unlink(path)


@pytest.fixture
def missing_column_csv():
    """缺少composite_premium列的CSV。"""
    dates = pd.date_range('2020-01-01', periods=100, freq='B')
    df = pd.DataFrame({'some_other_col': np.random.randn(100)}, index=dates)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8-sig') as f:
        df.to_csv(f.name)
        path = f.name
    yield path
    os.unlink(path)


# ══════════════════════════════════════════════════════
# 数据加载测试
# ══════════════════════════════════════════════════════

class TestDataLoading:
    def test_load_synthetic(self, synthetic_csv):
        calc = AHPremiumCalculator(synthetic_csv)
        df = calc.load()
        assert len(df) == 1000
        assert 'composite_premium' in df.columns
        assert not df.index.duplicated().any()

    def test_load_real_csv(self, real_csv):
        calc = AHPremiumCalculator(real_csv)
        df = calc.load()
        assert len(df) > 0
        assert 'composite_premium' in df.columns
        assert df.index.is_monotonic_increasing

    def test_load_file_not_found(self):
        calc = AHPremiumCalculator('/nonexistent/path.csv')
        with pytest.raises(FileNotFoundError):
            calc.load()

    def test_load_missing_column(self, missing_column_csv):
        calc = AHPremiumCalculator(missing_column_csv)
        with pytest.raises(ValueError, match='composite_premium'):
            calc.load()

    def test_load_empty_csv(self, empty_csv):
        calc = AHPremiumCalculator(empty_csv)
        # 空CSV加载后为空的DataFrame，后续compute会产生NaN分位
        df = calc.load()
        assert len(df) == 0
        # 确认空DataFrame的compute_method_a不会崩溃
        pct = calc.compute_method_a(df)
        assert pct.dropna().empty


# ══════════════════════════════════════════════════════
# 方法A: 滚动2yr分位 测试
# ══════════════════════════════════════════════════════

class TestMethodA:
    def test_monotonic_rising_data(self, synthetic_csv):
        """溢价单调上升时，最新值总是窗口内最大值 → 分位接近1.0。
        这说明滚动分位对单调趋势会饱和，与结构性下行趋势导致的饱和问题一致。"""
        calc = AHPremiumCalculator(synthetic_csv)
        df = calc.load()
        pct = calc.compute_method_a(df)
        valid = pct.dropna()

        # 前_MIN_PERIODS行为NaN
        assert pct.iloc[:_MIN_PERIODS - 1].isna().all()
        # 单调上升 → 每个值都是窗口内最大值 → 分位接近1.0
        # 平均值应 > 0.9
        assert valid.mean() > 0.9, f"单调上升时分位应接近1.0, 实际均值{valid.mean():.3f}"

    def test_constant_data(self):
        """恒值溢价 → (x[-1] > x).mean() = 0（没有严格大于的值）→ 分位=0。
        这是滚动分位对无波动数据的正确行为。"""
        dates = pd.date_range('2020-01-01', periods=_WINDOW + 100, freq='B')
        df = pd.DataFrame({'composite_premium': 50.0}, index=dates)
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8-sig')
        df.to_csv(tmp.name)
        tmp.close()

        try:
            calc = AHPremiumCalculator(tmp.name)
            calc.load()
            pct = calc.compute_method_a()
            valid = pct.dropna()
            # 恒值 → 分位=0
            assert valid.iloc[-1] == 0.0, f"恒值分位应为0, 实际{valid.iloc[-1]:.3f}"
        finally:
            os.unlink(tmp.name)

    def test_short_data_below_min_periods(self):
        """数据不足 _MIN_PERIODS → 所有结果NaN"""
        dates = pd.date_range('2020-01-01', periods=50, freq='B')
        df = pd.DataFrame({'composite_premium': np.random.randn(50)}, index=dates)
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8-sig')
        df.to_csv(tmp.name)
        tmp.close()

        try:
            calc = AHPremiumCalculator(tmp.name)
            calc.load()
            pct = calc.compute_method_a()
            assert pct.dropna().empty, "短数据应全NaN"
        finally:
            os.unlink(tmp.name)

    def test_real_consistency(self, real_csv):
        """验证方法A结果与研究脚本一致。"""
        calc = AHPremiumCalculator(real_csv)
        df = calc.load()
        pct = calc.compute_method_a(df)

        # 验证范围: [0, 1]
        valid = pct.dropna()
        assert valid.min() >= 0
        assert valid.max() <= 1

        # 最新值应为低溢价（研究脚本确认过 ~2.8%）
        latest = valid.iloc[-1]
        assert latest < 0.25, f"最新溢价分位应<25%, 实际{latest:.1%}"


# ══════════════════════════════════════════════════════
# 方法B: 绝对水平三分位 测试
# ══════════════════════════════════════════════════════

class TestMethodB:
    def test_thresholds_on_synthetic(self, synthetic_csv):
        """合成数据(0→100线性): 33%分位≈33, 67%分位≈67。"""
        calc = AHPremiumCalculator(synthetic_csv)
        df = calc.load()
        thresh = calc.compute_thresholds(df)

        assert 30 < thresh.lo < 36, f"lo阈值应≈33, 实际{thresh.lo:.1f}"
        assert 64 < thresh.hi < 70, f"hi阈值应≈67, 实际{thresh.hi:.1f}"
        assert thresh.lo < thresh.hi

    def test_tercile_on_synthetic(self, synthetic_csv):
        """合成数据三等分：前33%天=0，中33%=1，后33%=2。"""
        calc = AHPremiumCalculator(synthetic_csv)
        df = calc.load()
        tercile = calc.compute_method_b(df)

        n = len(tercile)
        # 各三分之一
        n0 = (tercile == 0).sum()
        n1 = (tercile == 1).sum()
        n2 = (tercile == 2).sum()

        assert abs(n0 - n / 3) < 5, f"低组样本应≈{n/3:.0f}, 实际{n0}"
        assert abs(n2 - n / 3) < 5, f"高组样本应≈{n/3:.0f}, 实际{n2}"

    def test_thresholds_idempotent(self, synthetic_csv):
        """阈值计算是幂等的。"""
        calc = AHPremiumCalculator(synthetic_csv)
        calc.load()
        t1 = calc.compute_thresholds()
        t2 = calc.compute_thresholds()
        assert t1.lo == t2.lo
        assert t1.hi == t2.hi

    def test_tercile_values(self):
        """tercile取值仅为 {0, 1, 2}。"""
        dates = pd.date_range('2020-01-01', periods=500, freq='B')
        premium = np.concatenate([np.ones(200)*10, np.ones(100)*50, np.ones(200)*90])
        df = pd.DataFrame({'composite_premium': premium}, index=dates)
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8-sig')
        df.to_csv(tmp.name)
        tmp.close()

        try:
            calc = AHPremiumCalculator(tmp.name)
            calc.load()
            tercile = calc.compute_method_b()
            unique = set(tercile.values)
            assert unique.issubset({0, 1, 2}), f"tercile取值应为0/1/2, 实际{unique}"
        finally:
            os.unlink(tmp.name)

    def test_real_thresholds(self, real_csv):
        calc = AHPremiumCalculator(real_csv)
        calc.load()
        thresh = calc.compute_thresholds()
        # 真实阈值范围合理
        assert 0 < thresh.lo < thresh.hi < 200
        # 当前溢价应在低位
        tercile = calc.compute_method_b()
        assert tercile.iloc[-1] == 0, "当前溢价应在绝对低位"


# ══════════════════════════════════════════════════════
# compute() 状态输出测试
# ══════════════════════════════════════════════════════

class TestComputeState:
    def test_complete_state_on_synthetic(self, synthetic_csv):
        calc = AHPremiumCalculator(synthetic_csv)
        state = calc.compute()

        assert isinstance(state, AHPremiumState)
        assert state.premium > 0
        assert 0 <= state.method_a_pct <= 1
        assert state.method_b_tercile in (0, 1, 2)
        assert state.method_a_label in ('low', 'neutral', 'high')
        assert state.method_b_label in ('low', 'neutral', 'high')
        assert len(state.zhonggai_position_hint) > 5  # 非空提示
        assert len(state.hengsheng_position_hint) > 5
        assert state.n_total == 1000

    def test_real_state_complete(self, real_csv):
        calc = AHPremiumCalculator(real_csv)
        state = calc.compute()

        assert state.premium > 0
        assert state.data_date  # 非空字符串
        assert state.n_total > 0
        assert state.n_valid > 0
        assert state.n_valid <= state.n_total

    def test_label_high(self, synthetic_csv):
        """合成数据末尾溢价最高 → 高标签"""
        calc = AHPremiumCalculator(synthetic_csv)
        state = calc.compute()
        assert state.method_a_label == 'high', f"合成末尾应为high, 实际{state.method_a_label}"
        assert state.method_b_tercile == 2

    def test_low_premium_gives_low_label(self):
        """构造一个近期一直在低位的溢价序列。"""
        dates = pd.date_range('2020-01-01', periods=_WINDOW + 100, freq='B')
        # 前半段高溢价(100)，后半段一路降到10
        premium = np.concatenate([
            np.ones(_WINDOW // 2) * 100,
            np.linspace(100, 10, len(dates) - _WINDOW // 2)
        ])
        df = pd.DataFrame({'composite_premium': premium}, index=dates)
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8-sig')
        df.to_csv(tmp.name)
        tmp.close()

        try:
            calc = AHPremiumCalculator(tmp.name)
            state = calc.compute()
            assert state.method_a_label == 'low', f"持续下降末尾应为low, 实际{state.method_a_label}"
        finally:
            os.unlink(tmp.name)


# ══════════════════════════════════════════════════════
# 边界与容错测试
# ══════════════════════════════════════════════════════

class TestEdgeCases:
    def test_all_nan_premium(self):
        """全NaN溢价列。"""
        dates = pd.date_range('2020-01-01', periods=100, freq='B')
        df = pd.DataFrame({'composite_premium': [np.nan] * 100}, index=dates)
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8-sig')
        df.to_csv(tmp.name)
        tmp.close()

        try:
            calc = AHPremiumCalculator(tmp.name)
            calc.load()
            pct = calc.compute_method_a()
            assert pct.dropna().empty, "全NaN应返回空Series"
        finally:
            os.unlink(tmp.name)

    def test_duplicate_indices_handled(self):
        """重复日期应被去重，不影响计算。"""
        dates = pd.date_range('2020-01-01', periods=500, freq='B')
        # 构造重复日期
        df1 = pd.DataFrame({'composite_premium': np.random.randn(500)}, index=dates)
        df2 = pd.DataFrame({'composite_premium': np.random.randn(500)}, index=dates)
        df_dup = pd.concat([df1, df2]).sort_index()

        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8-sig')
        df_dup.to_csv(tmp.name)
        tmp.close()

        try:
            calc = AHPremiumCalculator(tmp.name)
            df = calc.load()
            assert not df.index.duplicated().any(), "加载后不应有重复日期"
        finally:
            os.unlink(tmp.name)

    def test_default_path_does_not_crash_on_import(self):
        """模块导入时不应因默认路径问题崩溃。"""
        from quantforge.indicators import ah_premium_signal
        assert hasattr(ah_premium_signal, 'AHPremiumCalculator')


# ══════════════════════════════════════════════════════
# 与真实数据一致性校验
# ══════════════════════════════════════════════════════

class TestRealDataConsistency:
    """验证计算结果与研究脚本 _verify_ah_premium.py 一致。"""

    def test_method_a_latest_matches_research(self, real_csv):
        """方法A最新分位应与研究脚本输出一致(~2.8%)。"""
        calc = AHPremiumCalculator(real_csv)
        state = calc.compute()
        # 允差±5pp 因为研究脚本用了不同数据结构
        assert 0.0 < state.method_a_pct < 0.10, \
            f"方法A最新分位应在0~10%, 实际{state.method_a_pct:.1%}"

    def test_method_b_latest_tercile_zero(self, real_csv):
        """方法B当前应处于绝对低位(0) — 溢价已降至全样本最低区间。"""
        calc = AHPremiumCalculator(real_csv)
        state = calc.compute()
        assert state.method_b_tercile == 0, \
            f"方法B当前应在低位, 实际tercile={state.method_b_tercile}"

    def test_premium_in_expected_range(self, real_csv):
        """当前溢价应在15-25%之间。"""
        calc = AHPremiumCalculator(real_csv)
        state = calc.compute()
        assert 10 < state.premium < 30, \
            f"当前溢价应在10-30%, 实际{state.premium:.1f}%"

    def test_n_total_reasonable(self, real_csv):
        """总交易日数应>1500。"""
        calc = AHPremiumCalculator(real_csv)
        state = calc.compute()
        assert state.n_total > 1500, f"总数据应>1500日, 实际{state.n_total}"