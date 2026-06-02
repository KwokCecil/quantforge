import json
import os
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from loguru import logger


@dataclass
class HealthBaseline:
    strategy_name: str
    baseline_date: str
    sharpe_1y: float
    max_drawdown_1y: float
    turnover_3m: float
    excess_3m: float
    annual_return: float
    icir_1y: float = 0.0       # 滚动1年ICIR，需要因子IC数据，缺数据时为0
    crowding: float = 0.0      # 持仓标的收益率相关性均值，缺数据时为0

    def save(self, path: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self.__dict__, f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: str):
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        return cls(**data)


@dataclass
class HealthComparison:
    metric: str
    baseline: float
    current: float
    deviation_pct: float
    warning: bool
    level: str = "OK"

    def describe(self) -> str:
        direction = "↑" if self.current > self.baseline else "↓"
        return f"{self.metric}: {self.baseline:.4f}→{self.current:.4f} ({direction}{abs(self.deviation_pct):.0f}%)"


class HealthMonitor:
    def __init__(self, baseline: HealthBaseline):
        self.baseline = baseline

    def compute_current(self, nv_df: pd.DataFrame, trade_log: list,
                        benchmark_series: pd.Series = None,
                        ic_series: list = None,
                        holdings_history: list = None) -> dict:
        dates = pd.to_datetime(nv_df['date'])
        net_values = nv_df['net_value'].values

        annual_return = self._annual_return(dates, net_values)
        sharpe_1y = self._rolling_sharpe_1y(dates, net_values)
        max_drawdown_1y = self._max_drawdown_1y(nv_df)
        turnover_3m = self._turnover_3m(dates, trade_log)
        excess_3m = self._excess_3m(dates, net_values, benchmark_series)
        icir_1y = self._rolling_icir_1y(dates, ic_series)
        crowding = self._crowding(holdings_history)

        return {
            'sharpe_1y': sharpe_1y,
            'max_drawdown_1y': max_drawdown_1y,
            'turnover_3m': turnover_3m,
            'excess_3m': excess_3m,
            'annual_return': annual_return,
            'icir_1y': icir_1y,
            'crowding': crowding,
        }

    def compare(self, current: dict) -> list[HealthComparison]:
        baseline = self.baseline
        comparisons = []

        comps_def = [
            ('sharpe_1y', baseline.sharpe_1y, -0.3, 'sharpe_1y'),
            ('max_drawdown_1y', baseline.max_drawdown_1y, 1.0, 'max_drawdown_1y'),
            ('turnover_3m', baseline.turnover_3m, 0.5, 'turnover_3m'),
            ('excess_3m', baseline.excess_3m, -2.0, 'excess_3m'),
            ('annual_return', baseline.annual_return, -0.3, 'annual_return'),
            ('icir_1y', baseline.icir_1y, -0.3, 'icir_1y'),
            ('crowding', baseline.crowding, 0.3, 'crowding'),
        ]

        for metric, base_val, warn_threshold, key in comps_def:
            cur_val = current[key]
            if base_val == 0:
                deviation = 0.0
            else:
                deviation = (cur_val - base_val) / abs(base_val)

            is_warning = False
            level = "OK"
            if key in ('max_drawdown_1y', 'turnover_3m'):
                if deviation > warn_threshold:
                    is_warning = True
                    level = "WARNING" if deviation > warn_threshold * 2 else "WATCH"
            else:
                if deviation < warn_threshold:
                    is_warning = True
                    level = "WARNING" if deviation < warn_threshold * 2 else "WATCH"

            comparisons.append(HealthComparison(
                metric=metric,
                baseline=base_val,
                current=cur_val,
                deviation_pct=deviation * 100,
                warning=is_warning,
                level=level,
            ))

        return comparisons

    def verdict(self, comparisons: list[HealthComparison]) -> str:
        levels = [c.level for c in comparisons]
        warning_count = levels.count('WARNING')
        watch_count = levels.count('WATCH')
        if warning_count >= 3:
            return 'CRITICAL'
        if warning_count >= 1:
            return 'WARNING'
        if watch_count >= 2:
            return 'WATCH'
        if watch_count >= 1:
            return 'WATCH'
        return 'HEALTHY'

    def check(self, nv_df: pd.DataFrame, trade_log: list,
              benchmark_series: pd.Series = None) -> dict:
        current = self.compute_current(nv_df, trade_log, benchmark_series)
        comparisons = self.compare(current)
        verdict = self.verdict(comparisons)
        return {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'verdict': verdict,
            'metrics': current,
            'comparisons': [c.__dict__ for c in comparisons],
        }

    # === 指标计算 ===

    @staticmethod
    def _annual_return(dates, net_values) -> float:
        if len(dates) < 2:
            return 0.0
        years = (dates.iloc[-1] - dates.iloc[0]).days / 365.25
        if years <= 0:
            return 0.0
        return (net_values[-1] / net_values[0]) ** (1 / years) - 1

    @staticmethod
    def _rolling_sharpe_1y(dates, net_values) -> float:
        window = min(252, len(net_values) - 1)
        if window < 20:
            return 0.0
        daily_ret = np.diff(net_values[-window - 1:]) / net_values[-window - 1:-1]
        if daily_ret.std() == 0:
            return 0.0
        return daily_ret.mean() / daily_ret.std() * np.sqrt(252)

    @staticmethod
    def _max_drawdown_1y(nv_df: pd.DataFrame) -> float:
        if 'drawdown' not in nv_df.columns:
            peak = nv_df['net_value'].cummax()
            dd = (peak - nv_df['net_value']) / peak
        else:
            dd = nv_df['drawdown']
        window = min(252, len(dd))
        return float(dd.iloc[-window:].max())

    @staticmethod
    def _turnover_3m(dates, trade_log: list) -> float:
        if not trade_log:
            return 0.0
        last_date = dates.iloc[-1]
        cutoff = last_date - pd.Timedelta(days=90)
        recent_trades = [t for t in trade_log
                        if pd.Timestamp(t['date']) >= cutoff]
        trading_days = (last_date - cutoff).days * 5 / 7
        if trading_days <= 0:
            return 0.0
        return len(recent_trades) / trading_days

    @staticmethod
    def _excess_3m(dates, net_values, benchmark_series: pd.Series = None) -> float:
        if benchmark_series is None or benchmark_series.empty:
            return 0.0
        window = min(63, len(net_values))
        if window < 5:
            return 0.0
        strategy_ret = net_values[-1] / net_values[-window] - 1
        bm_first = benchmark_series.iloc[-window] if len(benchmark_series) > window else benchmark_series.iloc[0]
        bm_last = benchmark_series.iloc[-1]
        if bm_first <= 0:
            return 0.0
        bench_ret = bm_last / bm_first - 1
        return strategy_ret - bench_ret

    @staticmethod
    def _rolling_icir_1y(dates, ic_series: list = None) -> float:
        """滚动1年ICIR = IC均值/IC标准差。
        ic_series: [{'date': str, 'ic': float}, ...]，缺数据返回0。
        """
        if not ic_series:
            return 0.0
        last_date = dates.iloc[-1]
        cutoff = last_date - pd.Timedelta(days=252)
        recent_ic = [x['ic'] for x in ic_series
                     if pd.Timestamp(x['date']) >= cutoff
                     and x['ic'] is not None and not np.isnan(x['ic'])]
        if len(recent_ic) < 20:
            return 0.0
        ic_mean = np.mean(recent_ic)
        ic_std = np.std(recent_ic)
        if ic_std == 0:
            return 0.0
        return ic_mean / ic_std

    @staticmethod
    def _crowding(holdings_history: list = None) -> float:
        """因子拥挤度 = 最近持仓标的间日收益率相关性均值。
        holdings_history: [{'date': str, 'codes': [str, ...]}, ...]
        缺数据返回0。
        """
        if not holdings_history or len(holdings_history) < 5:
            return 0.0
        # 取最近3个月的持仓记录
        recent = holdings_history[-63:]
        # 收集每期持仓的代码集合，取高频出现的code
        all_codes = set()
        for h in recent:
            all_codes.update(h.get('codes', []))
        if len(all_codes) < 3:
            return 0.0
        return 0.0  # 简化：实际需要日收益率面板数据，当前数据管道不直接支持
        # TODO: 接入日收益率面板后实现真实拥挤度计算

    # === 基线创建（从回测结果） ===

    @classmethod
    def create_baseline(cls, strategy_name: str, run_dir: str,
                        output_path: str = None) -> HealthBaseline:
        nv_path = os.path.join(run_dir, 'net_values.csv')
        trades_path = os.path.join(run_dir, 'trades.json')
        report_path = os.path.join(run_dir, 'report.json')

        nv_df = pd.read_csv(nv_path)
        nv_df['date'] = pd.to_datetime(nv_df['date'])
        dates = nv_df['date']

        net_values = nv_df['net_value'].values

        with open(trades_path, encoding='utf-8') as f:
            trade_log = json.load(f)

        benchmark_series = None
        if os.path.exists(report_path):
            with open(report_path, encoding='utf-8') as f:
                report = json.load(f)
            bm = report.get('analysis', {}).get('benchmark', {})
            if bm.get('benchmark_nav'):
                benchmark_series = _build_benchmark_from_report(report, dates)
            elif bm.get('benchmark_return'):
                benchmark_series = _build_benchmark_from_return(bm['benchmark_return'], nv_df)

        baseline = cls._compute_baseline(strategy_name, dates, net_values, trade_log, benchmark_series)

        if output_path:
            baseline.save(output_path)
            logger.info(f"基线已保存: {output_path}")

        return baseline

    @classmethod
    def _compute_baseline(cls, name, dates, net_values, trade_log, benchmark_series):
        nv_df = pd.DataFrame({'date': dates, 'net_value': net_values,
                              'drawdown': pd.Series(0.0, index=range(len(dates)))})
        peak = pd.Series(net_values).cummax()
        nv_df['drawdown'] = (peak - nv_df['net_value']) / peak

        return HealthBaseline(
            strategy_name=name,
            baseline_date=datetime.now().strftime('%Y-%m-%d'),
            sharpe_1y=cls._rolling_sharpe_1y(dates, net_values),
            max_drawdown_1y=cls._max_drawdown_1y(nv_df),
            turnover_3m=cls._turnover_3m(dates, trade_log),
            excess_3m=cls._excess_3m(dates, net_values, benchmark_series),
            annual_return=cls._annual_return(dates, net_values),
            icir_1y=0.0,
            crowding=0.0,
        )


def _build_benchmark_from_report(report: dict, dates: pd.DatetimeIndex) -> pd.Series:
    bm_nav = report['analysis']['benchmark']['benchmark_nav']
    if isinstance(bm_nav, list):
        bm_series = pd.Series(dtype=float)
        for item in bm_nav:
            bm_series[item['date']] = item['value']
        bm_series.index = pd.to_datetime(bm_series.index)
        return bm_series
    return pd.Series()


def _build_benchmark_from_return(benchmark_return: float, nv_df: pd.DataFrame) -> pd.Series:
    """从总收益率反推基准净值序列（近似）"""
    years = (pd.to_datetime(nv_df['date'].iloc[-1]) -
             pd.to_datetime(nv_df['date'].iloc[0])).days / 365.25
    if years <= 0:
        return pd.Series()
    n = len(nv_df)
    daily_ret = (1 + benchmark_return) ** (1 / (n - 1)) - 1
    bm_values = (1 + daily_ret) ** np.arange(n)
    return pd.Series(bm_values, index=pd.to_datetime(nv_df['date']))
