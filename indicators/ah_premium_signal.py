"""AH溢价信号计算器。

支持两种分位方法：
- 方法A (中概互联): 滚动504日分位 — 适用于历史数据短的标的
- 方法B (恒生ETF): 全样本固定阈值三等分 — 适用于有完整历史数据的标的

数据来源：research/_verify_ah_premium.py 产出的综合溢价CSV。
"""
import os
from dataclasses import dataclass

import numpy as np
import pandas as pd
from loguru import logger

# 综合溢价CSV默认路径（相对于quantforge包根目录）
_DEFAULT_COMPOSITE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'results', 'ah_premium_research', 'ah_composite_index.csv'
)

# 滚动窗口（交易日）
_WINDOW = 504   # 约2年
_MIN_PERIODS = 126  # 约6个月，保证滚动分位有意义


@dataclass
class AHPremiumState:
    """AH溢价当前状态"""
    premium: float              # 当前综合溢价 (%)
    method_a_pct: float         # 方法A: 滚动2yr分位 [0, 1]
    method_b_tercile: int       # 方法B: 绝对三等分 {0=低, 1=中, 2=高}
    method_a_label: str         # "low" / "neutral" / "high"
    method_b_label: str         # "low" / "neutral" / "high"
    data_date: str              # 最新数据日期
    n_total: int                # 总交易日数
    n_valid: int                # 有效分位天数

    # 仓位建议
    zhonggai_position_hint: str   # 中概互联仓位建议
    hengsheng_position_hint: str  # 恒生ETF仓位建议


@dataclass
class AHPremiumThresholds:
    """绝对水平三分位阈值（全样本固定）"""
    lo: float   # 低/中分界
    hi: float   # 中/高分界


class AHPremiumCalculator:
    """AH溢价信号计算器。

    用法:
        calc = AHPremiumCalculator()
        state = calc.compute()
        print(state.zhonggai_position_hint)  # "低溢价: 建议≤1/3仓"
    """

    def __init__(self, composite_csv_path: str | None = None):
        self._csv_path = composite_csv_path or _DEFAULT_COMPOSITE_PATH
        self._df: pd.DataFrame | None = None
        self._methods: pd.DataFrame | None = None
        self._thresholds: AHPremiumThresholds | None = None

    # ── 数据加载 ──────────────────────────────────────

    def load(self) -> pd.DataFrame:
        """加载综合溢价CSV，去重排序。"""
        if not os.path.exists(self._csv_path):
            raise FileNotFoundError(f"综合溢价CSV不存在: {self._csv_path}")

        df = pd.read_csv(self._csv_path, index_col=0, parse_dates=True)
        df = df[~df.index.duplicated(keep='last')]
        df = df.sort_index()

        if 'composite_premium' not in df.columns:
            raise ValueError("CSV缺少 composite_premium 列")

        self._df = df
        if len(df) > 0:
            logger.info(f"AH溢价数据加载: {len(df)} 日, {df.index[0].date()} ~ {df.index[-1].date()}")
        else:
            logger.warning("AH溢价数据加载: 0 日（空CSV）")
        return df

    # ── 方法A: 滚动2yr分位 ───────────────────────────

    def compute_method_a(self, df: pd.DataFrame | None = None) -> pd.Series:
        """计算滚动504日溢价分位。"""
        if df is None:
            df = self._ensure_loaded()

        premium = df['composite_premium'].dropna()
        pct = premium.rolling(_WINDOW, min_periods=_MIN_PERIODS).apply(
            lambda x: (x.iloc[-1] > x).mean(), raw=False
        )
        return pct

    # ── 方法B: 绝对水平三分位 ─────────────────────────

    def compute_thresholds(self, df: pd.DataFrame | None = None) -> AHPremiumThresholds:
        """计算全样本固定的三分位阈值。"""
        if df is None:
            df = self._ensure_loaded()

        premium = df['composite_premium'].dropna()
        lo = float(premium.quantile(0.33))
        hi = float(premium.quantile(0.67))
        self._thresholds = AHPremiumThresholds(lo=lo, hi=hi)
        return self._thresholds

    def compute_method_b(self, df: pd.DataFrame | None = None) -> pd.Series:
        """计算绝对水平三等分 (0=低, 1=中, 2=高)。"""
        if df is None:
            df = self._ensure_loaded()

        thresholds = self._thresholds or self.compute_thresholds(df)
        premium = df['composite_premium']
        tercile = np.where(premium < thresholds.lo, 0,
                           np.where(premium < thresholds.hi, 1, 2))
        return pd.Series(tercile, index=premium.index)

    # ── 状态查询 ──────────────────────────────────────

    def compute(self) -> AHPremiumState:
        """计算当前AH溢价状态，含仓位建议。"""
        df = self._ensure_loaded()

        # 方法A
        pct_a = self.compute_method_a(df)
        valid_a = pct_a.dropna()

        # 方法B
        thresh = self.compute_thresholds(df)
        tercile_b = self.compute_method_b(df)

        # 最新值
        latest_premium = float(df['composite_premium'].iloc[-1])
        latest_pct_a = float(pct_a.iloc[-1]) if not pd.isna(pct_a.iloc[-1]) else float('nan')
        latest_tercile_b = int(tercile_b.iloc[-1]) if not pd.isna(tercile_b.iloc[-1]) else -1

        # 标签
        def _pct_label(pct_val: float) -> str:
            if pd.isna(pct_val):
                return "unknown"
            if pct_val < 0.25:
                return "low"
            if pct_val > 0.75:
                return "high"
            return "neutral"

        def _tercile_label(t: int) -> str:
            return {0: "low", 1: "neutral", 2: "high"}.get(t, "unknown")

        # 仓位建议
        zhonggai_hint = self._position_hint_zhonggai(latest_pct_a)
        hengsheng_hint = self._position_hint_hengsheng(latest_tercile_b)

        return AHPremiumState(
            premium=latest_premium,
            method_a_pct=latest_pct_a,
            method_b_tercile=latest_tercile_b,
            method_a_label=_pct_label(latest_pct_a),
            method_b_label=_tercile_label(latest_tercile_b),
            data_date=str(df.index[-1].date()),
            n_total=len(df),
            n_valid=len(valid_a),
            zhonggai_position_hint=zhonggai_hint,
            hengsheng_position_hint=hengsheng_hint,
        )

    # ── 仓位建议 ──────────────────────────────────────

    @staticmethod
    def _position_hint_zhonggai(pct: float) -> str:
        """基于方法A(滚动分位)的中概互联仓位建议。"""
        if pd.isna(pct):
            return "数据不足，无法建议"
        if pct < 0.25:
            return "低溢价(分位{:.0%}): 建议≤1/3仓，短期逆风".format(pct)
        if pct > 0.75:
            return "高溢价(分位{:.0%}): 可正常~偏重仓位".format(pct)
        return "中性(分位{:.0%}): 正常仓位".format(pct)

    @staticmethod
    def _position_hint_hengsheng(tercile: int) -> str:
        """基于方法B(绝对水平)的恒生ETF仓位建议。"""
        hints = {
            0: "低溢价(绝对低位): 建议≤1/3仓，历史60日前向-2.24%",
            1: "中溢价(绝对中位): 正常仓位",
            2: "高溢价(绝对高位): 可正常~偏重，历史60日前向+3.63%",
        }
        return hints.get(tercile, "数据不足，无法建议")

    # ── helpers ───────────────────────────────────────

    def _ensure_loaded(self) -> pd.DataFrame:
        if self._df is None:
            return self.load()
        return self._df

    @property
    def thresholds(self) -> AHPremiumThresholds | None:
        if self._thresholds is None and self._df is not None:
            self.compute_thresholds()
        return self._thresholds