"""短期反转策略：在均值回归池中买入过去N天跌幅最大的ETF

策略契约：
- produce_decisions() 产出 ROTATION 决策列表
- 恢复 RankingResolver 排序取 TOP_K
- 复用 BacktestExecutor（标准回测执行器）

与 ROC 动量的关键区别：
- 买跌（PR 升序排名）而非买涨（ROC 降序排名）
- 固定持有期退出（N_hold 天到期）
- %b < 0.2 超跌确认入场，%b > 0.8 止盈退出
"""
import pandas as pd
import numpy as np
from loguru import logger

from quantforge.core.config import StrategyConfig
from quantforge.core.data_feed import DataRequest, DataResponse
from quantforge.core.decision import Decision, DecisionType
from quantforge.core.strategy import Strategy


class ReversalStrategy(Strategy):
    """短期反转策略。遵循 Strategy 基类契约。"""

    def __init__(self, config):
        self._config = config
        self._pool_codes = config.pool_codes
        self._pr_period = config.pr_period
        self._top_k = config.top_k
        self._n_hold = config.n_hold
        self._bb_period = config.bb_period
        self._bb_k = config.bb_k
        self._use_b_pct_filter = config.use_b_pct_filter
        self._buy_threshold = config.buy_threshold
        self._sell_threshold = config.sell_threshold
        self._cut_loss_edge = config.cut_loss_edge
        self._use_volume_filter = config.use_volume_filter
        self._vol_threshold = config.vol_threshold

        self._entry_dates: dict[str, pd.Timestamp] = {}
        self._prev_held_codes: set = set()

    def produce_decisions(self, data: DataResponse, positions: dict) -> list:
        decisions = []
        current_date = self._extract_date(data)

        # === 追踪新入场 ===
        current_held = set(positions.keys())
        for code in current_held - self._prev_held_codes:
            if code in self._pool_codes and current_date:
                self._entry_dates[code] = current_date
        self._prev_held_codes = current_held

        # === 持仓退出判断 ===
        for code in list(positions.keys()):
            if code not in self._pool_codes or code not in data.bar_data:
                continue

            df = data.bar_data[code]
            if df.empty:
                continue

            close = df['close'].values.astype(float)
            pos = positions[code]
            cost = pos.get('avg_cost', 0)

            # N_hold 到期退出
            entry_dt = self._entry_dates.get(code)
            if entry_dt and current_date:
                days_held = (current_date - entry_dt).days
                if days_held >= self._n_hold:
                    decisions.append(Decision(
                        DecisionType.ROTATION, current_date,
                        f"持有{self._n_hold}天到期", code, 'exit',
                    ))
                    self._entry_dates.pop(code, None)
                    continue

            # 成本止损（策略层兜底，Resolver 层会再次检查）
            if cost and cost > 0:
                pnl_pct = (close[-1] - cost) / cost
                if pnl_pct < -self._cut_loss_edge:
                    decisions.append(Decision(
                        DecisionType.ROTATION, current_date,
                        f"成本止损 亏损{pnl_pct:.1%}", code, 'exit',
                    ))
                    self._entry_dates.pop(code, None)
                    continue

            # %b 止盈退出
            pr_val = (close[-1] / close[-self._pr_period - 1] - 1) * 100 if len(close) > self._pr_period else 0
            b_pct = self._calc_b_pct(close)

            if b_pct is not None and b_pct > self._sell_threshold:
                decisions.append(Decision(
                    DecisionType.ROTATION, current_date,
                    f"%b={b_pct:.2f} 回升止盈", code, 'exit',
                    indicator_values={'pr': round(pr_val, 2), 'b_pct': round(b_pct, 4)},
                ))
                self._entry_dates.pop(code, None)
                continue

            # 持仓观望
            decisions.append(Decision(
                DecisionType.ROTATION, current_date,
                f"持仓中  PR={pr_val:.1f}  %b={b_pct:.2f}" if b_pct is not None else "持仓中",
                code, 'hold',
                indicator_values={'pr': round(pr_val, 2), 'b_pct': round(b_pct, 4)} if b_pct is not None else {},
            ))

        # === 入场判断 ===
        for code in self._pool_codes:
            if code in positions or code not in data.bar_data:
                continue

            df = data.bar_data[code]
            if df.empty or len(df) < self._pr_period + 2:
                continue

            close = df['close'].values.astype(float)

            pr_val = (close[-1] / close[-self._pr_period - 1] - 1) * 100

            b_pct = self._calc_b_pct(close)
            if b_pct is None:
                continue

            iv = {'pr': round(pr_val, 2), 'b_pct': round(b_pct, 4)}

            # %b 过滤
            if self._use_b_pct_filter and b_pct >= self._buy_threshold:
                decisions.append(Decision(
                    DecisionType.ROTATION, current_date,
                    f"PR={pr_val:.1f}% %b={b_pct:.2f} >= {self._buy_threshold}", code, 'hold',
                    indicator_values=iv,
                ))
                continue

            # 成交量过滤（Phase 2 开启）
            if self._use_volume_filter and 'volume' in df.columns:
                vol = df['volume'].values.astype(float)
                vol_ma = vol[-21:].mean() if len(vol) >= 21 else vol.mean()
                vol_ratio = vol[-1] / vol_ma if vol_ma > 0 else 1.0
                iv['vol_ratio'] = round(vol_ratio, 2)
                if vol_ratio < self._vol_threshold:
                    decisions.append(Decision(
                        DecisionType.ROTATION, current_date,
                        f"PR={pr_val:.1f}% 缩量 vol_ratio={vol_ratio:.1f}", code, 'hold',
                        indicator_values=iv,
                    ))
                    continue

            # 入场: PR 越小（跌越狠）→ priority 越小 → 优先级越高
            decisions.append(Decision(
                DecisionType.ROTATION, current_date,
                f"PR({self._pr_period})={pr_val:.1f}% %b={b_pct:.2f}", code, 'enter',
                priority=round(pr_val * 100),
                indicator_values=iv,
            ))

        return decisions

    def get_required_data(self) -> list[DataRequest]:
        end = self._config.end_date if self._config.end_date else pd.Timestamp.now().strftime('%Y-%m-%d')
        return [DataRequest(
            codes=self._pool_codes,
            data_type='daily_k',
            start=self._config.start_date,
            end=end,
        )]

    @property
    def name(self) -> str:
        return "short_term_reversal"

    @property
    def config(self) -> StrategyConfig:
        return self._config

    def _extract_date(self, data: DataResponse) -> pd.Timestamp | None:
        """从数据中提取当前回测日期。"""
        for df in data.bar_data.values():
            if df is not None and not df.empty and 'date' in df.columns:
                d = df.iloc[-1]['date']
                return pd.Timestamp(d)
        return None

    def _calc_b_pct(self, close: np.ndarray) -> float | None:
        """计算布林带 %b 值。0 = 下轨，1 = 上轨。"""
        if len(close) < self._bb_period:
            return None
        window = close[-self._bb_period:]
        bb_mid = window.mean()
        bb_std = window.std()
        if bb_std == 0:
            return 0.5
        bb_lower = bb_mid - self._bb_k * bb_std
        bb_upper = bb_mid + self._bb_k * bb_std
        if bb_upper == bb_lower:
            return 0.5
        return float((close[-1] - bb_lower) / (bb_upper - bb_lower))
