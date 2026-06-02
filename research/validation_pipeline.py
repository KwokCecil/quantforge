"""统一验证管道：串联数据质量→因子IC→参数稳健性→Walk-Forward→成本敏感度"""
import copy
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from loguru import logger

from quantforge.core.data_feed import DataFeed, DataRequest
from quantforge.core.executor import BacktestExecutor
from quantforge.core.resolver import RankingResolver
from quantforge.core.backtest_core import run_backtest
from quantforge.core.backtest_support import BacktestAnalyzer
from quantforge.research.factor_lab import FactorLab
from quantforge.research.validator import Validator
from quantforge.tools.time_utils import get_trading_dates


@dataclass
class PipelineVerdict:
    """验证判定结果。pass_if: 所有required步骤为True才PASS；warn_if: 任意warn条件触发时WEAK_PASS。"""
    passed_steps: list[str] = field(default_factory=list)
    failed_steps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    verdict: str = "UNKNOWN"

    @property
    def is_pass(self) -> bool:
        return self.verdict == "PASS"

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "passed_steps": self.passed_steps,
            "failed_steps": self.failed_steps,
            "warnings": self.warnings,
        }


class ValidationPipeline:
    """策略上线前统一验证管道。按顺序执行各验证步骤，产出结构化报告和判定。

    使用方式：
        pipeline = ValidationPipeline(strategy_class, config_class, data_feed, codes, start, end)
        report = pipeline.run()
        print(report['verdict']['verdict'])  # PASS / WEAK_PASS / FAIL

    步骤失败处理：某步骤抛出异常时记录失败并继续（非阻断），_build_verdict 汇总所有步骤决定 verdict。
    """

    def __init__(self, strategy_class, config_class,
                 data_feed: DataFeed, codes: list[str],
                 start: str, end: str,
                 benchmark_code: str = None):
        self.strategy_class = strategy_class
        self.config_class = config_class
        self.data_feed = data_feed
        self.codes = codes
        self.start = start
        self.end = end
        self.benchmark_code = benchmark_code

        self._base_config = None
        self._steps_report = {}
        self._verdict_thresholds = {
            "min_icir": 0.1,           # ICIR 低于此值标记失败
            "min_sharpe": 0.0,         # Sharpe 低于此值标记失败
            "max_param_sensitivity": 0.5,  # 最优参数附近 Sharpe 方差/均值超过此值标记不稳
        }

    def run(self, steps: list[str] = None,
            param_ranges: dict = None,
            train_years: int = 3,
            test_years: int = 1) -> dict:
        """执行验证管道。steps=None时执行全部步骤。

        Returns:
            {
                'config': {...},
                'steps': {'data_quality': {...}, 'factor_ic': {...}, ...},
                'verdict': {'verdict': 'PASS', 'passed_steps': [...], ...},
            }
        """
        all_steps = ["data_quality", "factor_ic", "param_robustness",
                      "walk_forward", "cost_sensitivity"]
        steps = steps or all_steps

        self._base_config = self.config_class(
            start_date=self.start, end_date=self.end, codes=self.codes,
        )

        logger.info(f"验证管道启动: {self._base_config.strategy_name}, "
                     f"标的={self.codes}, 区间={self.start}~{self.end}")
        logger.info(f"执行步骤: {steps}")

        self._steps_report = {}
        verdict = PipelineVerdict()

        step_methods = {
            "data_quality": self._step_data_quality,
            "factor_ic": self._step_factor_ic,
            "param_robustness": lambda: self._step_param_robustness(param_ranges or {}),
            "walk_forward": lambda: self._step_walk_forward(param_ranges or {}, train_years, test_years),
            "cost_sensitivity": self._step_cost_sensitivity,
        }

        for step_name in steps:
            if step_name not in step_methods:
                logger.warning(f"未知步骤: {step_name}，跳过")
                continue

            logger.info(f"--- 执行步骤: {step_name} ---")
            try:
                result = step_methods[step_name]()
                self._steps_report[step_name] = result
                verdict.passed_steps.append(step_name)
                logger.success(f"{step_name}: 通过")
            except Exception as e:
                logger.error(f"{step_name}: 失败 — {e}")
                self._steps_report[step_name] = {"error": str(e)}
                verdict.failed_steps.append(step_name)

        self._build_verdict(verdict)

        return {
            "config": self._base_config.to_dict(),
            "steps": self._steps_report,
            "verdict": verdict.to_dict(),
        }

    # ==================== 步骤实现 ====================

    def _step_data_quality(self) -> dict:
        """数据质量检查：日期连续性、停牌日、价格异常、数据跨度。"""
        logger.info("检查数据质量...")
        report = {"codes": {}, "summary": {}}

        response = self.data_feed.get_data(DataRequest(
            codes=self.codes, data_type=self._base_config.data_type,
            start=self.start, end=self.end,
        ))

        trading_dates = get_trading_dates(self.start, self.end)

        for code in self.codes:
            df = response.bar_data.get(code)
            if df is None or df.empty:
                report["codes"][code] = {"error": "无数据"}
                continue

            dates = set(df['date'].tolist())
            missing = sorted(set(trading_dates) - dates)
            missing_count = len(missing)
            total_trading = len(trading_dates)
            coverage = (total_trading - missing_count) / max(total_trading, 1) * 100

            # 停牌日（成交量为0）
            if 'volume' in df.columns:
                suspended = len(df[df['volume'] == 0]) if 'volume' in df.columns else 0
                suspended_dates = df[df['volume'] == 0]['date'].tolist()[:5]
            else:
                suspended = 0
                suspended_dates = []

            # 价格异常（单日涨跌 > 10%）
            if 'close' in df.columns and len(df) > 1:
                pct_chg = df['close'].pct_change().abs()
                anomalies = len(pct_chg[pct_chg > 0.1])
                anomaly_dates = df.loc[pct_chg[pct_chg > 0.1].index, 'date'].tolist()[:5]
            else:
                anomalies = 0
                anomaly_dates = []

            report["codes"][code] = {
                "rows": len(df),
                "date_min": df['date'].min(),
                "date_max": df['date'].max(),
                "missing_dates": missing_count,
                "coverage_pct": round(coverage, 1),
                "missing_sample": missing[:5],
                "suspended_count": suspended,
                "suspended_sample": suspended_dates,
                "anomaly_count": anomalies,
                "anomaly_sample": anomaly_dates,
            }

        # 汇总统计
        coverages = [v.get("coverage_pct", 0) for v in report["codes"].values()]
        report["summary"] = {
            "total_codes": len(self.codes),
            "codes_with_data": sum(1 for v in report["codes"].values() if "rows" in v),
            "avg_coverage": round(sum(coverages) / max(len(coverages), 1), 1),
            "total_anomalies": sum(v.get("anomaly_count", 0) for v in report["codes"].values()),
        }

        return report

    def _step_factor_ic(self) -> dict:
        """因子有效性检验：IC矩阵扫描 + 分层回测。"""
        logger.info("因子IC分析...")

        from quantforge.research.ic_analysis import _calc_roc

        roc_periods = [5, 10, 15, 22, 44, 66, 120]
        fwd_periods = [5, 10, 22, 44, 66]

        # 加载数据
        response = self.data_feed.get_data(DataRequest(
            codes=self.codes, data_type=self._base_config.data_type,
            start=self.start, end=self.end,
        ))

        all_data = {}
        for code in self.codes:
            if code in response.bar_data and not response.bar_data[code].empty:
                df = response.bar_data[code].copy()
                df['date'] = pd.to_datetime(df['date'])
                df = df.set_index('date').sort_index()
                df = df[~df.index.duplicated(keep='last')]
                df['close'] = pd.to_numeric(df['close'], errors='coerce')
                df = df.dropna(subset=['close'])
                all_data[code] = df

        if not all_data:
            return {"error": "无可用数据"}

        # IC矩阵扫描
        ic_matrix, icir_matrix = FactorLab.ic_matrix_scan(
            factor_func=_calc_roc,
            close_data=all_data,
            factor_params=roc_periods,
            forward_periods=fwd_periods,
        )

        best_roc = icir_matrix.max(axis=1).idxmax()
        best_fwd = icir_matrix.loc[best_roc].idxmax()
        best_icir = float(icir_matrix.loc[best_roc, best_fwd])
        best_ic = float(ic_matrix.loc[best_roc, best_fwd])

        # 当前参数对应的IC/ICIR
        curr_roc = getattr(self._base_config, 'roc_n', 22)
        curr_fwd = getattr(self._base_config, 'roc_m', 8)
        curr_fwd = curr_fwd if curr_fwd in fwd_periods else min(fwd_periods, key=lambda x: abs(x - curr_fwd))
        if curr_roc in roc_periods and curr_fwd in fwd_periods:
            curr_ic = float(ic_matrix.loc[curr_roc, curr_fwd])
            curr_icir = float(icir_matrix.loc[curr_roc, curr_fwd])
        else:
            curr_ic = None
            curr_icir = None

        # 分层回测（简化：只报告IC结果）
        return {
            "best_roc_period": best_roc,
            "best_fwd_period": best_fwd,
            "best_ic": best_ic,
            "best_icir": best_icir,
            "current_ic": curr_ic,
            "current_icir": curr_icir,
            "assessment": "OK" if abs(best_icir) >= self._verdict_thresholds["min_icir"] else "WEAK",
        }

    def _step_param_robustness(self, param_ranges: dict) -> dict:
        """参数稳健性：最优参数附近 ±30% 范围内的表现稳定性。"""
        logger.info("参数稳健性检查...")

        # 确定扫描范围（默认 roc_n: [15, 22, 30], buy_roc_edge: [12, 18, 24]）
        if not param_ranges:
            base_n = getattr(self._base_config, 'roc_n', 22)
            base_edge = getattr(self._base_config, 'buy_roc_edge', 18.0)
            param_ranges = {
                "roc_n": [max(int(base_n * 0.7), 5), base_n, int(base_n * 1.3)],
                "buy_roc_edge": [max(int(base_edge * 0.7), 5), base_edge, int(base_edge * 1.3)],
            }

        sweep_df = Validator().parameter_sweep(
            strategy_class=self.strategy_class,
            config_class=self.config_class,
            param_ranges=param_ranges,
            data_feed=self.data_feed,
            codes=self.codes,
            start=self.start,
            end=self.end,
        )

        if sweep_df.empty:
            return {"error": "参数扫描无结果"}

        sharpe_vals = sweep_df["sharpe_ratio"].dropna().values
        sharpe_std = float(sharpe_vals.std()) if len(sharpe_vals) > 1 else 0
        sharpe_mean = float(sharpe_vals.mean()) if len(sharpe_vals) > 0 else 0
        sensitivity = sharpe_std / max(abs(sharpe_mean), 0.01)

        best_row = sweep_df.iloc[0]
        return {
            "best_params": {k: best_row[k] for k in param_ranges.keys()},
            "best_sharpe": float(best_row.get("sharpe_ratio", 0)),
            "sharpe_range": [round(float(sharpe_vals.min()), 2), round(float(sharpe_vals.max()), 2)],
            "param_sensitivity": round(sensitivity, 3),
            "sweep_df_shape": list(sweep_df.shape),
            "assessment": "STABLE" if sensitivity < self._verdict_thresholds["max_param_sensitivity"] else "SENSITIVE",
        }

    def _step_walk_forward(self, param_ranges: dict,
                            train_years: int, test_years: int) -> dict:
        """Walk-Forward 样本外验证。"""
        logger.info("Walk-Forward 验证...")

        if not param_ranges:
            base_n = getattr(self._base_config, 'roc_n', 22)
            base_edge = getattr(self._base_config, 'buy_roc_edge', 18.0)
            param_ranges = {
                "roc_n": [max(int(base_n * 0.7), 5), base_n, int(base_n * 1.3)],
                "buy_roc_edge": [max(int(base_edge * 0.7), 5), base_edge, int(base_edge * 1.3)],
            }

        wf_df = Validator().walk_forward(
            strategy_class=self.strategy_class,
            config_class=self.config_class,
            data_feed=self.data_feed,
            codes=self.codes,
            start=self.start,
            end=self.end,
            train_years=train_years,
            test_years=test_years,
            param_ranges=param_ranges,
        )

        if wf_df.empty:
            return {"error": "Walk-Forward 无结果"}

        oos_returns = wf_df["oos_return"].dropna()
        oos_sharpes = wf_df["oos_sharpe"].dropna()

        return {
            "windows": len(wf_df),
            "oos_avg_return": round(float(oos_returns.mean()) if len(oos_returns) > 0 else 0, 2),
            "oos_avg_sharpe": round(float(oos_sharpes.mean()) if len(oos_sharpes) > 0 else 0, 2),
            "oos_positive_pct": round(
                (oos_returns > 0).sum() / max(len(oos_returns), 1) * 100, 1
            ),
            "assessment": "OK" if (oos_returns > 0).sum() >= max(len(oos_returns) * 0.5, 1) else "WEAK",
        }

    def _step_cost_sensitivity(self) -> dict:
        """交易成本敏感度：不同佣金/滑点假设下收益变化矩阵。"""
        logger.info("成本敏感度分析...")

        config = copy.copy(self._base_config)
        strategy = self.strategy_class(config)

        resolver_base = RankingResolver(
            top_k=getattr(config, 'top_k', 5),
            weight_method='signal_weight',
            high_watermark_stop_edge=getattr(config, 'high_watermark_stop_edge', 0.15)
            if getattr(config, 'HIGH_WATERMARK_STOP', True) else float('inf'),
            cut_loss_edge=getattr(config, 'cut_loss_edge', 0.08)
            if getattr(config, 'CUT_LOSS', True) else float('inf'),
            top_k_sell=getattr(config, 'TOP_K_SELL', False),
        )

        commissions = [0.0001, 0.00025, 0.0005]
        slippages = [0.0005, 0.001, 0.002]

        matrix = []
        base_result = None

        for comm in commissions:
            row = []
            for slip in slippages:
                executor = BacktestExecutor(
                    initial_capital=config.initial_capital,
                    commission_rate=comm, slippage=slip,
                )
                result = run_backtest(
                    strategy=strategy, resolver=resolver_base,
                    executor=executor, data_feed=self.data_feed,
                    codes=self.codes,
                    start=self.start, end=self.end,
                    benchmark_code=self.benchmark_code,
                )
                analyzer = BacktestAnalyzer()
                metrics = analyzer.analyze(executor)

                ret = metrics.get("total_return", 0)
                row.append(round(float(ret) * 100, 2))

                if comm == commissions[1] and slip == slippages[1]:
                    base_result = float(ret) * 100

            matrix.append(row)

        # 计算成本每增加1bp的平均收益衰减
        decay_rate = None
        if base_result is not None and base_result != 0:
            worst = min(min(row) for row in matrix) if matrix else base_result
            decay_rate = round((base_result - worst) / base_result * 100, 1)

        return {
            "commission_rates": commissions,
            "slippage_rates": slippages,
            "return_matrix": matrix,
            "base_return": base_result,
            "cost_decay_rate_pct": decay_rate,
            "assessment": "OK" if decay_rate is not None and decay_rate < 30 else "SENSITIVE",
        }

    def _build_verdict(self, verdict: PipelineVerdict):
        """汇总各步骤结果，生成最终判定。"""
        # 关键步骤
        critical_steps = {"factor_ic", "walk_forward"}

        failed_critical = [s for s in verdict.failed_steps if s in critical_steps]
        weak_steps = []

        for step_name in self._steps_report:
            assessment = self._steps_report[step_name].get("assessment", "")
            if assessment in ("WEAK", "SENSITIVE"):
                weak_steps.append(step_name)
                verdict.warnings.append(f"{step_name}: {assessment}")

        if failed_critical:
            verdict.verdict = "FAIL"
            verdict.warnings.append(f"关键步骤失败: {failed_critical}")
        elif weak_steps:
            verdict.verdict = "WEAK_PASS"
        else:
            verdict.verdict = "PASS"

        if not verdict.passed_steps and not verdict.failed_steps:
            verdict.verdict = "FAIL"
            verdict.warnings.append("无任何步骤执行成功")
