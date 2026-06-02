import pandas as pd
from collections import OrderedDict
from loguru import logger

from quantforge.core.strategy import Strategy
from quantforge.core.resolver import Resolver


class StyleRotator:
    """根据基准指数趋势和动量自动切换攻防预设。

    AGGRESSIVE: 进攻模式 → 使用成长/科技聚焦的标的池
    DEFENSIVE:  防守模式 → 使用全天候均衡标的池

    切换条件（AND逻辑）：
    1. 基准 close > MA(trend_period)     — 趋势确认
    2. 基准 ROC(momentum_period) > 0    — 动量确认
    """

    def __init__(self, config):
        self._benchmark = config.sr_benchmark
        self._trend_period = config.sr_trend_period
        self._momentum_period = config.sr_momentum_period
        self._aggressive = config.sr_aggressive_preset
        self._defensive = config.sr_defensive_preset
        self._cooldown = config.sr_cooldown_days
        self._state = self._defensive
        self._days_since_switch = 999

    @property
    def current_preset(self) -> str:
        return self._state

    @property
    def is_aggressive(self) -> bool:
        return self._state == self._aggressive

    def evaluate(self, bar_data: pd.DataFrame) -> str:
        if self._days_since_switch < self._cooldown:
            self._days_since_switch += 1
            return self._state

        if bar_data is None or len(bar_data) < max(self._trend_period, self._momentum_period):
            logger.debug(f"风格轮动: 数据不足 (共{len(bar_data) if bar_data is not None else 0}行)，保持 {self._state}")
            return self._state

        last = bar_data.iloc[-1]
        close = last['close']

        trend_ok = close > bar_data['close'].rolling(self._trend_period).mean().iloc[-1]

        roc = (close / bar_data['close'].iloc[-self._momentum_period - 1] - 1)
        momentum_ok = roc > 0

        new_state = self._aggressive if (trend_ok and momentum_ok) else self._defensive

        if new_state != self._state:
            logger.info(
                f"风格轮动: {self._state} -> {new_state} "
                f"(收盘={close:.2f} 趋势={'确认' if trend_ok else '未确认'} 动量={'确认' if momentum_ok else '未确认'} ROC={roc:.1%})"
            )
            self._state = new_state
            self._days_since_switch = 0

        return self._state

    def reset(self):
        self._state = self._defensive
        self._days_since_switch = 999


class RotationScheduler:
    """封装风格轮动的调度逻辑。

    每日根据 StyleRotator 评估基准走势，返回当前应使用的策略、决议器和标的列表。
    与 run_backtest() 配合使用，消除 _run_rotation_backtest 中的重复回测循环。

    用法:
        scheduler = RotationScheduler(rotator, strategy_agg, resolver_agg, codes_agg,
                                       strategy_def, resolver_def, codes_def,
                                       benchmark_df, trading_dates, preset_agg, preset_def)
        run_backtest(..., rotation_scheduler=scheduler, preloaded_bar_data=aligned)
    """
    def __init__(self,
                 rotator: StyleRotator,
                 strategy_agg: Strategy,
                 resolver_agg: Resolver,
                 codes_agg: list[str],
                 strategy_def: Strategy,
                 resolver_def: Resolver,
                 codes_def: list[str],
                 benchmark_df,
                 trading_dates: list[str],
                 preset_agg: str,
                 preset_def: str):
        self._rotator = rotator
        self._strategy_agg = strategy_agg
        self._resolver_agg = resolver_agg
        self._codes_agg = codes_agg
        self._strategy_def = strategy_def
        self._resolver_def = resolver_def
        self._codes_def = codes_def
        self._preset_agg = preset_agg
        self._preset_def = preset_def

        self._schedule = OrderedDict()
        for date in trading_dates:
            mask = benchmark_df['date'] <= date
            bench_slice = benchmark_df[mask].reset_index(drop=True)
            self._schedule[date] = rotator.evaluate(bench_slice)

    def get(self, date: str) -> tuple[Strategy, Resolver, list[str]]:
        """返回给定日期的 (strategy, resolver, codes)。"""
        preset = self._schedule.get(date, self._preset_agg)
        if preset == self._preset_agg:
            return self._strategy_agg, self._resolver_agg, self._codes_agg
        else:
            return self._strategy_def, self._resolver_def, self._codes_def

    @property
    def schedule(self) -> OrderedDict:
        return self._schedule
