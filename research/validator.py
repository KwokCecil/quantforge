"""策略验证器：参数扫描 + Walk-Forward 样本外验证"""
import itertools
import os
import traceback
from datetime import datetime, timedelta

import pandas as pd
from loguru import logger

from quantforge.core.data_feed import CachedDataFeed, DataRequest
from quantforge.core.executor import BacktestExecutor
from quantforge.core.resolver import RankingResolver
from quantforge.core.backtest_core import run_backtest
from quantforge.core.backtest_support import BacktestAnalyzer


def _add_months(dt: datetime, months: int) -> datetime:
    """日期加月份（处理月末边界）"""
    m = dt.month + months
    year = dt.year + (m - 1) // 12
    month = (m - 1) % 12 + 1
    day = min(dt.day, 28)
    return datetime(year, month, day)


class Validator:
    """策略验证器。提供参数网格搜索和 Walk-Forward 样本外验证。

    参数扫描：
        sweeper = Validator()
        df = sweeper.parameter_sweep(
            strategy_class=ROCStrategy, config_class=ROCConfig,
            param_ranges={'buy_roc_edge': [15, 18, 20, 22, 25], 'roc_n': [15, 20, 22, 25]},
            data_feed=data_feed, codes=codes, start='2020-01-01', end='2025-12-31',
        )

    Walk-Forward：
        df = sweeper.walk_forward(
            strategy_class=ROCStrategy, config_class=ROCConfig,
            data_feed=data_feed, codes=codes, start='2020-01-01', end='2025-12-31',
            train_years=3, test_years=1, param_ranges=param_ranges,
        )
    """

    def __init__(self, objective: str = 'sharpe_ratio'):
        """Args:
            objective: 参数选择的目标指标，可选 'sharpe_ratio', 'total_return',
                       'calmar_ratio' (total_return/max_drawdown), 'excess_return'
        """
        self.objective = objective

    def parameter_sweep(self, strategy_class, config_class,
                        param_ranges: dict, data_feed, codes: list[str],
                        start: str, end: str,
                        skip_cache_update: bool = False,
                        save_dir: str = None) -> pd.DataFrame:
        """参数网格搜索。笛卡尔积展开所有参数组合，每组跑一次回测。

        Args:
            skip_cache_update: 跳过数据缓存刷新（Walk-Forward 中提前刷新过全量数据时使用）
        """
        # 构建参数组合的产品空间
        param_names = list(param_ranges.keys())
        param_values = list(param_ranges.values())
        combinations = list(itertools.product(*param_values))
        total = len(combinations)

        logger.info(f"参数扫描开始: {total} 组参数，{len(codes)} 个标的，区间 {start}~{end}")

        # 提前刷新数据缓存（所有组合共享同一份缓存数据）
        if not skip_cache_update and isinstance(data_feed, CachedDataFeed):
            data_feed.update_cache(codes=codes, data_type='daily_k', start=start, end=end)

        results = []
        for idx, combo in enumerate(combinations):
            param_dict = dict(zip(param_names, combo))
            logger.info(f"[{idx + 1}/{total}] 参数: {param_dict}")

            try:
                analysis = self._run_single_backtest(
                    strategy_class, config_class, param_dict,
                    data_feed, codes, start, end,
                )
                if analysis:
                    row = {**param_dict}
                    row.update(self._extract_metrics(analysis))
                    results.append(row)
            except Exception as e:
                logger.error(f"参数组合 {param_dict} 回测失败: {e}", exc_info=True)

        if not results:
            logger.error("所有参数组合回测均失败")
            return pd.DataFrame()

        df = pd.DataFrame(results)
        df = df.sort_values(self.objective, ascending=False).reset_index(drop=True)
        logger.info(f"参数扫描完成: {len(df)} 组有效结果")

        # 自动生成参数热力图（仅当有 2 个参数维度且指定了保存目录时）
        if save_dir and len(param_names) == 2 and not df.empty:
            os.makedirs(save_dir, exist_ok=True)
            try:
                from quantforge.core.backtest_support import BacktestComparator
                BacktestComparator.plot_param_heatmap(
                    sweep_df=df,
                    x_param=param_names[0], y_param=param_names[1],
                    z_metric=self.objective,
                    output_path=os.path.join(save_dir, 'param_heatmap.png'),
                )
            except Exception as e:
                logger.warning(f"参数热力图生成失败: {e}")

        return df

    def walk_forward(self, strategy_class, config_class,
                     data_feed, codes: list[str],
                     start: str, end: str,
                     train_years: int = 3, test_years: int = 1,
                     param_ranges: dict = None,
                     save_dir: str = None) -> pd.DataFrame:
        """Walk-Forward 样本外验证。滚动窗口：训练期找最优参数 → 测试期验证。

        train_years=3, test_years=1 时，窗口如：
          2020-2022训练 → 2023测试 → 2021-2023训练 → 2024测试 → ...

        Returns:
            DataFrame，每行一个窗口，含训练最优参数 + OOS 指标
        """
        # 预加载全量数据缓存，并用全量范围请求验证每个code都有缓存数据
        if isinstance(data_feed, CachedDataFeed):
            data_feed.update_cache(codes=codes, data_type='daily_k', start=start, end=end)
            data_feed.get_data(DataRequest(codes=codes, data_type='daily_k', start=start, end=end))

        # 生成滚动窗口日期范围
        start_dt = datetime.strptime(start, '%Y-%m-%d')
        end_dt = datetime.strptime(end, '%Y-%m-%d')
        windows = self._generate_windows(start_dt, end_dt, train_years, test_years)

        if not windows:
            logger.error("数据区间不足以生成 Walk-Forward 窗口")
            return pd.DataFrame()

        logger.info(f"Walk-Forward 验证: {len(windows)} 个窗口")

        wf_results = []
        for wi, (train_start, train_end, test_start, test_end) in enumerate(windows):
            ts = train_start.strftime('%Y-%m-%d')
            te = train_end.strftime('%Y-%m-%d')
            ss = test_start.strftime('%Y-%m-%d')
            se = test_end.strftime('%Y-%m-%d')

            logger.info(f"[窗口 {wi + 1}/{len(windows)}] 训练: {ts}~{te}, 测试: {ss}~{se}")

            # 训练期参数扫描（跳过缓存刷新，已在walk_forward中预加载全量数据）
            if param_ranges:
                sweep_df = self.parameter_sweep(
                    strategy_class, config_class, param_ranges,
                    data_feed, codes, ts, te, skip_cache_update=True,
                )
                if sweep_df.empty:
                    logger.warning(f"窗口 {wi + 1} 参数扫描失败，跳过")
                    continue
                best_row = sweep_df.iloc[0]
                best_params = {k: best_row[k] for k in param_ranges.keys()}
                train_metrics = {k: best_row[k] for k in sweep_df.columns if k not in param_ranges}
            else:
                best_params = {}
                train_metrics = {}

            # 测试期样本外回测
            try:
                oos_analysis = self._run_single_backtest(
                    strategy_class, config_class, best_params,
                    data_feed, codes, ss, se,
                )
                oos_metrics = self._extract_metrics(oos_analysis) if oos_analysis else {}
            except Exception as e:
                logger.error(f"窗口 {wi + 1} OOS回测失败: {e}")
                logger.error(traceback.format_exc())
                oos_metrics = {}

            window_record = {
                'window': wi + 1,
                'train_start': ts, 'train_end': te,
                'test_start': ss, 'test_end': se,
                **{f'param_{k}': v for k, v in best_params.items()},
                **{f'train_{k}': v for k, v in train_metrics.items()},
                **{f'oos_{k}': v for k, v in oos_metrics.items()},
            }
            wf_results.append(window_record)

        if not wf_results:
            return pd.DataFrame()

        wf_df = pd.DataFrame(wf_results)

        # 汇总 OOS 表现
        oos_return_col = 'oos_total_return' if 'oos_total_return' in wf_df.columns else None
        oos_sharpe_col = 'oos_sharpe_ratio' if 'oos_sharpe_ratio' in wf_df.columns else None

        summary = {
            'oos_avg_return': wf_df[oos_return_col].mean() if oos_return_col else 0,
            'oos_positive_ratio': (wf_df[oos_return_col] > 0).mean() if oos_return_col else 0,
            'oos_avg_sharpe': wf_df[oos_sharpe_col].mean() if oos_sharpe_col else 0,
        }
        logger.info(f"Walk-Forward 汇总: OOS平均收益={summary['oos_avg_return']:.2%}, "
                     f"正收益占比={summary['oos_positive_ratio']:.0%}, "
                     f"OOS平均Sharpe={summary['oos_avg_sharpe']:.2f}")

        # 自动生成 Walk-Forward 图表
        if save_dir:
            os.makedirs(save_dir, exist_ok=True)
            try:
                from quantforge.core.backtest_support import BacktestComparator
                BacktestComparator.plot_walk_forward(
                    wf_df=wf_df,
                    output_path=os.path.join(save_dir, 'walk_forward.png'),
                )
            except Exception as e:
                logger.warning(f"Walk-Forward 图表生成失败: {e}")

        return wf_df

    @staticmethod
    def _sanitize_params(param_dict: dict) -> dict:
        """将 numpy scalar 转为 Python 原生类型，整数值 float 转 int（pandas 3.x shift() 不接受 float periods）"""
        result = {}
        for k, v in param_dict.items():
            if hasattr(v, 'item'):  # numpy scalar → Python native
                v = v.item()
            if isinstance(v, float) and v == int(v):  # 15.0 → 15
                v = int(v)
            result[k] = v
        return result

    def _run_single_backtest(self, strategy_class, config_class,
                              param_dict: dict, data_feed, codes,
                              start: str, end: str) -> dict | None:
        """单次回测：组装所需组件 → run_backtest() → BacktestAnalyzer.analyze()"""
        param_dict = self._sanitize_params(param_dict)
        config = config_class(start_date=start, end_date=end, **param_dict)
        strategy = strategy_class(config)

        # 确保缓存中有全量数据（避免 sub-range 查询触发 source fetch 覆盖缓存）
        if isinstance(data_feed, CachedDataFeed):
            data_feed.get_data(DataRequest(codes=codes, data_type=config.data_type,
                                           start='1990-01-01', end='2099-12-31'))

        weight_method = 'equal' if config.BUY_AVERAGE else 'signal_weight'
        resolver = RankingResolver(
            top_k=config.top_k,
            weight_method=weight_method,
            high_watermark_stop_edge=config.high_watermark_stop_edge if config.HIGH_WATERMARK_STOP else float('inf'),
            cut_loss_edge=config.cut_loss_edge if config.CUT_LOSS else float('inf'),
            top_k_sell=config.TOP_K_SELL,
        )
        executor = BacktestExecutor(
            initial_capital=config.initial_capital,
            rebalance=config.REBALANCE,
            stop_small_trade=config.STOP_SMALL_TRADE,
            skip_small_trade_limit=config.skip_small_trade_limit,
        )

        request = DataRequest(codes=codes, data_type=config.data_type, start=start, end=end)

        results = run_backtest(
            strategy=strategy, resolver=resolver, executor=executor,
            data_feed=data_feed, codes=codes,
            start=start, end=end,
            benchmark_code=config.benchmark_code,
        )
        if not results:
            return None

        benchmark_series = results.get('benchmark_series')
        analyzer = BacktestAnalyzer()
        analysis = analyzer.analyze(
            executor, benchmark_series=benchmark_series,
            benchmark_name=config.code_names.get(config.benchmark_code, config.benchmark_code),
            code_names=config.code_names,
            strategy_config=config,
            save_dir=None,  # 参数扫描不保存中间结果
        )
        return analysis

    @staticmethod
    def _extract_metrics(analysis: dict) -> dict:
        """从 BacktestAnalyzer 的分析结果中提取关键绩效指标"""
        return {
            'total_return': analysis.get('total_return', 0),
            'annual_return': analysis.get('annual_return', 0),
            'max_drawdown': analysis.get('max_drawdown', 0),
            'sharpe_ratio': analysis.get('sharpe_ratio', 0),
            'sortino_ratio': analysis.get('sortino_ratio', 0),
            'calmar_ratio': analysis.get('annual_return', 0) / max(analysis.get('max_drawdown', 0.0001), 0.0001),
            'win_rate': analysis.get('win_rate', 0),
            'trade_count': analysis.get('trade_count', 0),
            'excess_return': analysis.get('excess_return', 0),
        }

    @staticmethod
    def _generate_windows(start_dt: datetime, end_dt: datetime,
                           train_years: int, test_years: int) -> list:
        """生成 Walk-Forward 滚动窗口列表。

        Returns:
            [(train_start, train_end, test_start, test_end), ...]
        """
        windows = []
        current = start_dt

        while True:
            train_start = current
            train_end = _add_months(train_start, train_years * 12) - timedelta(days=1)
            test_start = _add_months(train_start, train_years * 12)
            test_end = _add_months(test_start, test_years * 12) - timedelta(days=1)

            if test_end > end_dt:
                break

            windows.append((train_start, train_end, test_start, test_end))
            current = _add_months(current, test_years * 12)

        return windows
