"""股债利差信号计算器。
基于 HS300 PE + 10Y国债收益率，计算两种利差分位值并输出择时信号。
"""
import os
import pandas as pd
import numpy as np
from dataclasses import dataclass
from loguru import logger

_CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'data', 'guzhai_licha.csv')

# 与 guzhai_licha.py 一致的阈值
_FALLBACK_DOUBLE_TTM = 0.92   # 双倍利差 ≥92%分位 → 撤退
_FORWARD_DOUBLE_TTM = 0.15    # 双倍利差 ≤15%分位 → 冲锋
_FALLBACK_SINGLE_STATIC = 0.92
_FORWARD_SINGLE_STATIC = 0.40


@dataclass
class GuzhaiLichaSignal:
    """单日股债利差信号"""
    date: pd.Timestamp
    pe_static: float
    pe_ttm: float
    bond_10y: float
    double_ttm_licha: float     # 滚动双倍利差(%)
    double_ttm_pct: float       # 滚动双倍 reverse_ratio（≥当前值比例。高=贵，低=便宜）
    single_static_licha: float  # 静态单倍利差(%)
    single_static_pct: float    # 静态单倍 reverse_ratio（≥当前值比例。高=贵，低=便宜）
    # 综合信号
    signal_charge: bool = False  # 冲锋信号（双倍≤15% 或 单倍≤40%）
    signal_retreat: bool = False  # 撤退信号（双倍≥92% 或 单倍≥92%）


class GuzhaiLichaCalculator:
    """股债利差信号计算器。

    加载历史CSV，按扩展窗口计算每日分位值，输出择时信号。
    扩展窗口 = 使用截至当前日期的所有历史数据计算分位，无前视偏差。
    至少需要 252 个交易日的历史数据才开始输出信号。
    """

    def __init__(self, cache_file: str = _CACHE_FILE):
        self._df = pd.read_csv(cache_file, parse_dates=['date'])
        self._df = self._df.sort_values('date').reset_index(drop=True)
        self._min_history = 252  # 至少一年数据
        logger.info(f"股债利差数据加载: {len(self._df)} rows, "
                    f"{self._df['date'].min().date()} ~ {self._df['date'].max().date()}")

    def compute(self, start_date: str = "2018-01-01") -> list[GuzhaiLichaSignal]:
        """计算每日股债利差信号（扩展窗口分位）。

        从 start_date 开始，对每个交易日：
        - 取截至当日的历史数据
        - 计算双倍/单倍利差的扩展窗口分位
        - 判定冲锋/撤退信号
        """
        df = self._df[self._df['date'] >= start_date].copy()
        signals = []

        for i in range(len(df)):
            current_date = df.iloc[i]['date']
            # 扩展窗口：截至当前日期的所有历史数据
            hist = df.iloc[:i+1]
            if len(hist) < self._min_history:
                continue

            # 双倍利差分位（reverse_ratio: >=当前值的比例）
            # ratio=0.92 → 92%历史值>=当前 → 当前极低(贵) → 撤退
            # ratio=0.08 → 8%历史值>=当前 → 当前极高(便宜) → 冲锋
            double_ttm_vals = hist['double_ttm_licha_pct'].values
            double_ttm_current = hist.iloc[-1]['double_ttm_licha_pct']
            ratio_double = (double_ttm_vals >= double_ttm_current).sum() / len(double_ttm_vals)

            # 单倍利差分位
            single_vals = hist['single_static_licha_pct'].values
            single_current = hist.iloc[-1]['single_static_licha_pct']
            ratio_single = (single_vals >= single_current).sum() / len(single_vals)

            # 信号判定（与 guzhai_licha.py 完全一致）
            charge = (ratio_double <= _FORWARD_DOUBLE_TTM or
                      ratio_single <= _FORWARD_SINGLE_STATIC)
            retreat = (ratio_double >= _FALLBACK_DOUBLE_TTM or
                       ratio_single >= _FALLBACK_SINGLE_STATIC)

            signals.append(GuzhaiLichaSignal(
                date=current_date,
                pe_static=float(hist.iloc[-1]['pe_static']),
                pe_ttm=float(hist.iloc[-1]['pe_ttm']),
                bond_10y=float(hist.iloc[-1]['bond_10y']),
                double_ttm_licha=float(double_ttm_current),
                double_ttm_pct=float(ratio_double),
                single_static_licha=float(single_current),
                single_static_pct=float(ratio_single),
                signal_charge=charge,
                signal_retreat=retreat,
            ))

        logger.info(f"信号计算完成: {len(signals)} 条 "
                    f"({start_date} ~ {df['date'].max().date()})")
        return signals

    def get_signal_df(self, start_date: str = "2018-01-01") -> pd.DataFrame:
        """返回信号 DataFrame，便于分析"""
        signals = self.compute(start_date)
        rows = [{
            'date': s.date,
            'pe_static': s.pe_static,
            'pe_ttm': s.pe_ttm,
            'bond_10y': s.bond_10y,
            'double_ttm_licha': s.double_ttm_licha,
            'double_ttm_pct': s.double_ttm_pct,
            'single_static_licha': s.single_static_licha,
            'single_static_pct': s.single_static_pct,
            'signal_charge': s.signal_charge,
            'signal_retreat': s.signal_retreat,
        } for s in signals]
        return pd.DataFrame(rows)

    def get_multiplier_map(self, start_date: str = "2018-01-01",
                           mode: str = "linear") -> dict:
        """返回 date_str → 仓位乘数的映射字典。

        mode='linear': 线性分段。ratio≤15%→1.0，15~92%线性，≥92%→0.0
        mode='retreat_only': 只在撤退区清仓，其余正常。ratio≥92%→0.0，其余→1.0
        """
        signals = self.compute(start_date)
        multiplier = {}

        charge_bound = _FORWARD_DOUBLE_TTM   # 0.15
        retreat_bound = _FALLBACK_DOUBLE_TTM  # 0.92

        for s in signals:
            ratio = s.double_ttm_pct
            if mode == 'retreat_only':
                mult = 0.0 if ratio >= retreat_bound else 1.0
            else:
                if ratio <= charge_bound:
                    mult = 1.0
                elif ratio >= retreat_bound:
                    mult = 0.0
                else:
                    mult = 1.0 - (ratio - charge_bound) / (retreat_bound - charge_bound)
                    mult = max(0.0, min(1.0, mult))

            multiplier[s.date.strftime('%Y-%m-%d')] = mult

        logger.info(f"仓位乘数映射[{mode}]: {len(multiplier)} 天 "
                    f"(范围 {min(multiplier.values()):.0%} ~ {max(multiplier.values()):.0%})")
        return multiplier