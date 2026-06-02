"""回测核心引擎 —— 直接影响回测结果的逻辑。

包含:
- run_backtest(): 回测主循环（逐日遍历 Strategy→Resolver→Executor）
- align_dataframes(): 多ETF日期对齐
- _build_benchmark_from_bar_data(): 从ETF数据构建基准净值
- _filter_macro_by_date(): 宏观数据防未来泄漏切片
"""

import pandas as pd
from loguru import logger
from typing import Any, Callable, Optional

from quantforge.core.data_feed import DataFeed, DataResponse
from quantforge.core.executor import BacktestExecutor
from quantforge.core.resolver import Resolver
from quantforge.core.strategy import Strategy
from quantforge.tools.time_utils import get_trading_dates


def align_dataframes(dataframes: list[pd.DataFrame]) -> list[pd.DataFrame]:
    """多基金日期对齐。以第一个基金的日期序列为模板，其他基金reindex并ffill。

    已知偏差：ffill会在停牌日填充前值，回测时假设可交易但实际不能。
    后续在数据质量检查模块中需标记停牌日（成交量为0的日期）。
    """
    if not dataframes:
        return dataframes

    tmpl_dates = dataframes[0]['date'].tolist()

    for idx, fund_df in enumerate(dataframes):
        aligned_df = fund_df.set_index('date').reindex(tmpl_dates)
        aligned_df = aligned_df.ffill().reset_index()

        for col in aligned_df.columns:
            if col != 'date' and aligned_df[col].isna().any():
                first_valid = aligned_df[col].first_valid_index()
                if first_valid is not None:
                    aligned_df.loc[:first_valid - 1, col] = aligned_df.loc[first_valid, col]

        dataframes[idx] = aligned_df

    return dataframes


def _build_benchmark_from_bar_data(aligned_bar_data: dict[str, pd.DataFrame],
                                    benchmark_code: str) -> pd.Series | None:
    """从回测已加载的数据中构建基准净值曲线。优先使用标的池中的ETF。"""
    proxy_map = {
        '399006': '159915',
        '000300': '510300',
        '000905': '510500',
        '000016': '510050',
    }

    target_code = proxy_map.get(benchmark_code, benchmark_code)

    if target_code in aligned_bar_data:
        df = aligned_bar_data[target_code]
        if not df.empty and 'close' in df.columns:
            series = df.set_index('date')['close'].astype(float)
            series.index = pd.to_datetime(series.index)
            series = series.dropna()
            if len(series) > 0:
                return series / series.iloc[0]

    return None


def _filter_macro_by_date(macro_data: dict, date: str) -> dict:
    """过滤 macro_data，只保留 date 之前的数据。

    macro_data 的 value 通常是一个 list[dict]，每个 dict 含 'date' 键。
    回测循环中逐日切片，确保"未来信息"不会泄露到当日策略决策中。
    """
    filtered = {}
    for key, value in macro_data.items():
        if isinstance(value, list):
            filtered[key] = [item for item in value if item.get('date', '') <= date]
        else:
            filtered[key] = value
    return filtered


def run_backtest(strategy: Strategy,
                 resolver: Resolver,
                 executor: BacktestExecutor,
                 data_feed: DataFeed,
                 codes: list[str],
                 start: str,
                 end: str,
                 benchmark_code: Optional[str] = None,
                 extra_macro_data: Optional[dict] = None,
                 position_multiplier_fn: Optional[Callable] = None,
                 rotation_scheduler: Any = None,
                 preloaded_bar_data: Optional[dict] = None) -> dict:
    """回测主循环。按交易日遍历：data→decisions→targets→execute。

    支持多 DataRequest：遍历 strategy.get_required_data() 的全部请求，
    合并 bar_data 和 macro_data。macro_data 逐日切片避免未来信息泄露。

    rotation_scheduler: 可选，风格轮动调度器。传入后每日调用 scheduler.get(date)
        获取当前策略/决议器/标的，替代固定参数。
    preloaded_bar_data: 可选，预加载并对齐的 bar_data。传入后跳过数据加载与对齐步骤。
    """
    logger.info(f"回测开始: {strategy.name}, 标的={codes}, 区间={start}~{end}")

    if preloaded_bar_data is not None:
        aligned_bar_data = preloaded_bar_data
        all_macro_data = {}
        if extra_macro_data:
            all_macro_data.update(extra_macro_data)
    else:
        requests = strategy.get_required_data()

        all_bar_data = {}
        all_macro_data = {}
        for req in requests:
            resp = data_feed.get_data(req)
            all_bar_data.update(resp.bar_data)
            all_macro_data.update(resp.macro_data)

        if extra_macro_data:
            all_macro_data.update(extra_macro_data)

        for code in codes:
            if code not in all_bar_data:
                logger.error(f"缺少数据: {code}")
                return {}

        dfs = [all_bar_data[code].copy() for code in codes]
        dfs = align_dataframes(dfs)

        aligned_bar_data = {}
        for i, code in enumerate(codes):
            aligned_bar_data[code] = dfs[i]

    trading_dates = get_trading_dates(start, end)
    if not trading_dates:
        logger.error("无交易日")
        return {}

    all_dates = set(next(iter(aligned_bar_data.values()))['date'].tolist()) if aligned_bar_data else set()
    trading_dates = [d for d in trading_dates if d in all_dates]

    logger.info(f"共 {len(trading_dates)} 个交易日")

    for i, date in enumerate(trading_dates):
        if rotation_scheduler is not None:
            cur_strategy, cur_resolver, cur_codes = rotation_scheduler.get(date)
        else:
            cur_strategy, cur_resolver, cur_codes = strategy, resolver, codes

        date_bar_data = {}
        for code in cur_codes:
            if code in aligned_bar_data:
                df = aligned_bar_data[code]
                mask = df['date'] <= date
                date_bar_data[code] = df[mask].reset_index(drop=True)

        date_macro_data = _filter_macro_by_date(all_macro_data, date)
        date_response = DataResponse(bar_data=date_bar_data, macro_data=date_macro_data)

        decisions = cur_strategy.produce_decisions(date_response, executor.get_positions())
        targets = cur_resolver.resolve(decisions, executor.get_positions(), executor.available_capital(), date_response)

        if position_multiplier_fn is not None:
            mult = position_multiplier_fn(date)
            for t in targets:
                t.target_weight *= mult

        executor.execute(targets, date_response)

        if (i + 1) % 50 == 0:
            logger.info(f"回测进度: {i+1}/{len(trading_dates)} ({date})")

    results = executor.get_results()

    benchmark_series = None
    if benchmark_code:
        logger.info(f"构建基准数据: {benchmark_code}")
        benchmark_series = _build_benchmark_from_bar_data(aligned_bar_data, benchmark_code)
        if benchmark_series is not None:
            results['benchmark_series'] = benchmark_series
            logger.info("基准数据构建成功")
        else:
            logger.warning("基准数据构建失败，跳过基准对比")

    logger.info(f"回测完成: 最终净值={results.get('net_values', [{}])[-1].get('net_value', 'N/A') if results.get('net_values') else 'N/A'}")

    return results