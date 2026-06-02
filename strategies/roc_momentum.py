from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger

from quantforge.core.data_feed import DataRequest, DataResponse
from quantforge.core.decision import Decision, DecisionType
from quantforge.core.strategy import Strategy
from quantforge.indicators.technical import ROCIndicator, MAIndicator, VolatilityIndicator, RSIIndicator, MACDIndicator, ATRIndicator, ADXIndicator
from quantforge.strategies._configs.roc_config import ROCConfig


# === T040: 信号分类辅助函数 ===

def _classify_stage(roc_val, maroc_val, prev_maroc, buy_roc_edge):
    """T028 趋势阶段分类：早期/中期/晚期"""
    if roc_val < buy_roc_edge * 1.3:
        return "早期"
    if maroc_val > prev_maroc:
        return "中期"
    return "晚期"


def _classify_volume(vol_ratio):
    """T028 量价分类：放量/正常/缩量"""
    if vol_ratio < 0:
        return "数据不足"
    if vol_ratio >= 1.5:
        return "放量"
    if vol_ratio < 0.8:
        return "缩量"
    return "正常"


def _classify_volatility(atr_pct):
    if atr_pct < 0:
        return "数据不足"
    if atr_pct >= 75:
        return "高波"
    if atr_pct < 25:
        return "低波"
    return "正常"


def _classify_rsi(rsi_val):
    """T028 RSI区间分类"""
    if rsi_val is None:
        return "数据不足"
    if rsi_val < 60:
        return "RSI<60"
    if rsi_val < 70:
        return "RSI 60-70"
    if rsi_val < 80:
        return "RSI 70-80"
    return "RSI>=80"


def _check_macd_divergence(df, lookback=20):
    """检测MACD顶背离：收盘价创N日新高但DIF未同步创新高"""
    if len(df) < lookback + 1:
        return None
    close_arr = df['close'].values[-lookback - 1:]
    dif_arr = df['dif'].values[-lookback - 1:] if 'dif' in df.columns else None
    if dif_arr is None:
        return None
    cw = close_arr[~np.isnan(close_arr)]
    dw = dif_arr[~np.isnan(dif_arr)]
    if len(cw) < 2 or len(dw) < 2:
        return None
    price_new_high = close_arr[-1] >= np.nanmax(close_arr[:-1])
    if not price_new_high:
        return False
    return not (dif_arr[-1] >= np.nanmax(dif_arr[:-1]))


def _compute_vol_ratio_static(df):
    """计算量比：当日成交量 / 前20日均量"""
    if 'vol' not in df.columns or len(df) < 21:
        return -1.0
    vol_today = float(df['vol'].iloc[-1])
    if vol_today <= 0:
        return -1.0
    vol_window = df['vol'].iloc[-21:-1].dropna()
    if len(vol_window) < 5:
        return -1.0
    avg = float(vol_window.mean())
    return vol_today / avg if avg > 0 else -1.0


def _compute_atr_pct_static(df):
    """计算ATR在252日历史中的分位"""
    if 'atr' not in df.columns or len(df) < 252:
        return -1.0
    atr_val = df['atr'].iloc[-1]
    if pd.isna(atr_val):
        return -1.0
    atr_win = df['atr'].iloc[-252:].dropna()
    if len(atr_win) < 50:
        return -1.0
    return (atr_win < float(atr_val)).sum() / len(atr_win) * 100


class ROCStrategy(Strategy):
    """ROC动量轮动策略。产出ROTATION类型Decision，由RankingResolver处理仓位分配。

    条件开关系统：所有开关在 ROCConfig 中定义，修改 True/False 即可启用/禁用。
    - 买入条件：STRICT_BUY / MA_PRICE_CROSS / ROC_MA_DIRECTION
    - 卖出条件：sell_roc_edge / sell_ma_roc_edge / ROC_MA_DIRECTION
    - 止损条件：CUT_LOSS / HIGH_WATERMARK_STOP（由Resolver处理）
    - 仓位管理：BUY_AVERAGE / CROWDED_SELL / STOP_SMALL_TRADE
    """
    def __init__(self, config: ROCConfig):
        self._config = config
        self._ma_indicator = MAIndicator(periods=[config.ma_period])

        self._roc_indicator = ROCIndicator(n=config.roc_n, m=config.roc_m)

        if config.inverse_vol_weight:
            self._vol_indicator = VolatilityIndicator(n=config.ma_period)
        else:
            self._vol_indicator = None

        if config.atr_filter_enabled:
            self._atr_indicator = ATRIndicator(n=20)  # ATR(20): 行业标准短期波动率(T025全周期确认ATR+ADX组合+2.8pp)
        elif config.signal_stats_enabled:
            self._atr_indicator = ATRIndicator(n=20)
        else:
            self._atr_indicator = None

        if config.atr_expansion_filter_enabled or config.atr_expansion_sell_enabled:
            self._atr200_indicator = ATRIndicator(n=200)  # ATR(200): ~1年交易日，作为长期波动率基线(T025)
        else:
            self._atr200_indicator = None

        if config.adx_trend_filter_enabled:
            self._adx_indicator = ADXIndicator(n=14)  # ADX(14): Wilder(1978)原始默认参数，<20=震荡市(T025)
        else:
            self._adx_indicator = None

        need_rsi = config.rsi_enhance_enabled or config.rsi_sell_enabled or config.signal_stats_enabled
        need_macd = config.macd_divergence_filter_enabled or config.macd_divergence_sell_enabled or config.signal_stats_enabled
        self._rsi_indicator = RSIIndicator(n=config.rsi_period) if need_rsi else None
        self._macd_indicator = MACDIndicator(
            fast=config.macd_fast, slow=config.macd_slow, signal=config.macd_signal
        ) if need_macd else None

        # T040: 拦截历史缓存，记录每个标的最近一次被拦截的原因和日期
        self._blocked_history: dict[str, tuple[str, str]] = {}

    @property
    def name(self) -> str:
        return self._config.strategy_name

    @property
    def config(self) -> ROCConfig:
        return self._config

    def get_required_data(self) -> list[DataRequest]:
        codes = list(self._config.codes)
        return [
            DataRequest(
                codes=codes,
                data_type=self._config.data_type,
                start=self._config.start_date,
                end=self._config.end_date,
            )
        ]

    def produce_decisions(self, data: DataResponse, positions: dict[str, Any]) -> list[Decision]:
        return self._produce_singlefactor_decisions(data, positions)

    def _produce_singlefactor_decisions(self, data: DataResponse, positions: dict[str, Any]) -> list[Decision]:
        decisions = []
        roc_values = {}
        indicator_data = {}

        for code, df in data.bar_data.items():
            if df.empty or len(df) < self._config.EMPTY_DAY:
                continue

            df = self._roc_indicator.compute(df, n=self._config.roc_n, m=self._config.roc_m)
            df = self._ma_indicator.compute(df, periods=[self._config.ma_period])

            if (self._config.rsi_enhance_enabled or self._config.rsi_sell_enabled) and self._rsi_indicator:
                df = self._rsi_indicator.compute(df, n=self._config.rsi_period)

            if (self._config.macd_divergence_filter_enabled or self._config.macd_divergence_sell_enabled) and self._macd_indicator:
                df = self._macd_indicator.compute(df,
                    fast=self._config.macd_fast, slow=self._config.macd_slow, signal=self._config.macd_signal)

            if (self._config.atr_filter_enabled or self._config.atr_expansion_sell_enabled) and self._atr_indicator:
                df = self._atr_indicator.compute(df, n=20)

            if (self._config.atr_expansion_filter_enabled or self._config.atr_expansion_sell_enabled) and self._atr200_indicator:
                df = self._atr200_indicator.compute(df, n=200)

            if self._config.adx_trend_filter_enabled and self._adx_indicator:
                df = self._adx_indicator.compute(df, n=14)

            # 波动率加权模式下，把 volatility 写回 bar_data 供 Resolver 使用
            if self._vol_indicator:
                df = self._vol_indicator.compute(df, n=self._config.ma_period)
                if 'volatility' in df.columns:
                    data.bar_data[code] = df  # 原地更新，确保 Resolver 能读到 volatility

            latest = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else latest

            roc_val = latest.get('roc')
            maroc_val = latest.get('maroc')
            ma_val = latest.get(f'ma{self._config.ma_period}')
            close_val = latest.get('close')
            prev_roc = prev.get('roc')
            prev_maroc = prev.get('maroc')

            if roc_val is None or maroc_val is None:
                continue

            try:
                roc_val = float(roc_val)
                maroc_val = float(maroc_val)
                close_val = float(close_val) if close_val is not None else 0.0
                ma_val = float(ma_val) if ma_val is not None else 0.0
                prev_roc = float(prev_roc) if prev_roc is not None else 0.0
                prev_maroc = float(prev_maroc) if prev_maroc is not None else 0.0
            except (ValueError, TypeError):
                continue

            if np.isnan(roc_val) or np.isnan(maroc_val):
                continue

            roc_values[code] = roc_val
            indicator_data[code] = {
                'roc': roc_val, 'maroc': maroc_val,
                'close': close_val, 'ma': ma_val, 'prev_roc': prev_roc, 'prev_maroc': prev_maroc,
            }

            if self._config.rsi_enhance_enabled or self._config.rsi_sell_enabled:
                rsi_val_latest = latest.get('rsi')
                if rsi_val_latest is not None:
                    indicator_data[code]['rsi'] = float(rsi_val_latest)

            if self._config.macd_divergence_filter_enabled or self._config.macd_divergence_sell_enabled:
                indicator_data[code]['macd_divergence'] = self._check_divergence(df)

            if self._config.volume_filter_enabled or self._config.volume_sell_enabled:
                vol_ratio = self._compute_vol_ratio(df)
                indicator_data[code]['vol_ratio'] = vol_ratio

            if self._config.atr_filter_enabled:
                atr_pct = self._compute_atr_pct(df)
                indicator_data[code]['atr_pct'] = atr_pct

            if self._config.atr_expansion_filter_enabled:
                expansion = self._check_atr_expansion(df)
                indicator_data[code]['atr_expansion'] = expansion  # type: ignore[assignment]

            if self._config.adx_trend_filter_enabled:
                adx_val = self._get_adx(df)
                indicator_data[code]['adx'] = adx_val  # type: ignore[assignment]

            # === T040: 信号统计分类数据 ===
            if self._config.signal_stats_enabled:
                self._compute_signal_stats(code, df, latest, prev, indicator_data)

            # === T032: 持仓中标的的持有期卖出信号 ===
            hold_sell = (
                self._config.volume_sell_enabled or
                self._config.atr_expansion_sell_enabled or
                self._config.macd_divergence_sell_enabled or
                self._config.rsi_sell_enabled
            )
            if code in positions and hold_sell:
                if self._config.volume_sell_enabled:
                    indicator_data[code]['hold_vol_ratio'] = indicator_data[code].get('vol_ratio', -1.0)
                if self._config.atr_expansion_sell_enabled:
                    indicator_data[code]['hold_atr_expansion'] = self._check_atr_expansion(df, ratio=1.5)  # type: ignore[assignment]
                if self._config.macd_divergence_sell_enabled:
                    indicator_data[code]['hold_macd_divergence'] = self._check_divergence(df)
                if self._config.rsi_sell_enabled:
                    indicator_data[code]['hold_rsi'] = indicator_data[code].get('rsi')  # type: ignore[assignment]

        sorted_codes = sorted(roc_values.items(), key=lambda x: x[1], reverse=True)

        for priority, (code, roc_val) in enumerate(sorted_codes):
            ind = indicator_data.get(code, {})
            direction, weight, reason = self._evaluate(code, roc_val, ind, positions)

            # T040: 更新拦截历史
            if self._config.signal_stats_enabled:
                if direction == 'hold' and '在观望区间' not in reason:
                    self._blocked_history[code] = (datetime.now().strftime('%Y-%m-%d'), reason)
                elif direction == 'enter' and code in self._blocked_history:
                    del self._blocked_history[code]

            indicator_vals = {'roc': roc_val, 'maroc': ind.get('maroc', 0.0)}
            if self._config.signal_stats_enabled:
                for k in ('rsi', 'vol_ratio', 'atr_pct', 'macd_divergence'):
                    if k in ind:
                        indicator_vals[k] = ind[k]

            extra = self._build_signal_extra(code, roc_val, ind, direction)

            decisions.append(Decision(
                decision_type=DecisionType.ROTATION,
                timestamp=datetime.now(),
                reason=reason,
                target_code=code,
                direction=direction,
                weight=weight,
                priority=priority,
                confidence=min(abs(roc_val) / 50.0, 1.0),
                strategy_name=self.name,
                indicator_values=indicator_vals,
                extra=extra,
            ))

        return decisions

    def _compute_vol_ratio(self, df: pd.DataFrame) -> float:
        """计算当日成交量 / 前20日均量。数据不足返回 -1。"""
        if 'vol' not in df.columns or len(df) < 21:
            return -1.0
        vol_today = float(df['vol'].iloc[-1])
        if vol_today <= 0:
            return -1.0
        vol_window = df['vol'].iloc[-21:-1].dropna()
        if len(vol_window) < 5:
            return -1.0
        avg = float(vol_window.mean())
        return vol_today / avg if avg > 0 else -1.0

    def _compute_atr_pct(self, df: pd.DataFrame) -> float:
        """计算 ATR(20) 在最近252天的历史分位。数据不足返回 -1。"""
        if 'atr' not in df.columns or len(df) < 252:
            return -1.0
        atr_val = df['atr'].iloc[-1]
        if pd.isna(atr_val):
            return -1.0
        atr_win = df['atr'].iloc[-252:].dropna()
        if len(atr_win) < 50:
            return -1.0
        return (atr_win < float(atr_val)).sum() / len(atr_win) * 100

    def _check_atr_expansion(self, df: pd.DataFrame, ratio: float = 1.3) -> bool | None:
        """ATR波动率扩张：ATR(20) > ratio × ATR(200)均值。数据不足返回None。"""
        if 'atr' not in df.columns or len(df) < 200:
            return None
        atr20 = df['atr'].iloc[-1]
        atr200_win = df['atr'].iloc[-201:-1].dropna()
        if len(atr200_win) < 50 or pd.isna(atr20):
            return None
        atr200_mean = float(atr200_win.mean())
        return atr20 > ratio * atr200_mean if atr200_mean > 0 else None

    def _get_adx(self, df: pd.DataFrame) -> float | None:
        """获取当日ADX值。数据不足返回None。"""
        if 'adx' not in df.columns:
            return None
        val = df['adx'].iloc[-1]
        return float(val) if not pd.isna(val) else None

    def _check_divergence(self, df: pd.DataFrame) -> bool:
        """检测MACD顶背离。收盘价创lookback日新高但DIF未同步创新高。"""
        cfg = self._config
        lb = cfg.macd_divergence_lookback
        if 'close' not in df.columns or 'dif' not in df.columns or len(df) < lb + 1:
            return False
        close_vals = df['close'].values[-lb - 1:]
        dif_vals = df['dif'].values[-lb - 1:]
        cw = close_vals[~np.isnan(close_vals)]
        dw = dif_vals[~np.isnan(dif_vals)]
        if len(cw) < 2 or len(dw) < 2:
            return False
        price_new_high = close_vals[-1] >= np.nanmax(close_vals[:-1])
        if not price_new_high:
            return False
        dif_new_high = dif_vals[-1] >= np.nanmax(dif_vals[:-1])
        return not dif_new_high

    def _evaluate(self, code: str, roc_val: float, ind: dict,
                  positions: dict[str, Any]) -> tuple[str, float, str]:
        """评估单个标的的决策方向。条件开关控制买入/卖出的触发条件。"""

        # ---- 卖出判断 ----
        if code in positions:
            if roc_val < self._config.sell_roc_edge:
                return 'exit', 0.0, f"ROC={roc_val:.2f} < 卖出阈值{self._config.sell_roc_edge}"

            maroc_val = ind.get('maroc')
            if self._config.sell_ma_roc_edge > 0 and maroc_val is not None and maroc_val < self._config.sell_ma_roc_edge:
                return 'exit', 0.0, f"MAROC={maroc_val:.2f} < 均线卖出阈值{self._config.sell_ma_roc_edge}"

            if self._config.ROC_MA_DIRECTION and ind.get('maroc', 0) <= ind.get('prev_maroc', ind.get('maroc', 0)):
                return 'exit', 0.0, f"MAROC方向向下"

            if self._config.ROC_CROSS_MAROC_SELL:
                if maroc_val is not None and roc_val < maroc_val:
                    return 'exit', 0.0, f"ROC={roc_val:.2f} < MAROC={maroc_val:.2f}"

            # === T032: 辅助信号卖出 ===
            if self._config.volume_sell_enabled:
                vr = ind.get('hold_vol_ratio', -1.0)
                if vr >= self._config.volume_sell_spike_ratio:
                    return 'exit', 0.0, f"放量卖出: vol_ratio={vr:.1f}"

            if self._config.atr_expansion_sell_enabled:
                if ind.get('hold_atr_expansion') is True:
                    return 'exit', 0.0, "ATR扩张卖出: ATR(20)>1.5×ATR(200)"

            if self._config.macd_divergence_sell_enabled:
                if ind.get('hold_macd_divergence') is True:
                    return 'exit', 0.0, "MACD顶背离卖出: 价新高DIF未确认"

            if self._config.rsi_sell_enabled:
                rsi_v = ind.get('hold_rsi')
                if rsi_v is not None and rsi_v > 80:
                    return 'exit', 0.0, f"RSI>80止盈: RSI={rsi_v:.1f}"

        # ---- 买入判断 ----
        if roc_val >= self._config.buy_roc_edge:
            buy_ok = True
            reasons = [f"ROC={roc_val:.2f}>={self._config.buy_roc_edge}"]

            if self._config.STRICT_BUY:
                if ind.get('prev_roc', 0) >= self._config.buy_roc_edge:
                    buy_ok = False
                    reasons.append("严格买入:前一日ROC已超阈值")
                else:
                    reasons.append("严格买入:ROC刚突破")

            if self._config.MA_PRICE_CROSS:
                if ind.get('close', 0) <= ind.get('ma', 0):
                    buy_ok = False
                    reasons.append("均线穿越:价格<均线")

            if self._config.ROC_MA_DIRECTION:
                if ind.get('maroc', 0) <= ind.get('prev_maroc', ind.get('maroc', 0)):
                    buy_ok = False
                    reasons.append("ROC均线方向:MAROC未上升")

            if buy_ok and self._config.rsi_enhance_enabled:
                rsi_val = ind.get('rsi')
                if rsi_val is not None and rsi_val >= self._config.rsi_enhance_below:
                    buy_ok = False
                    reasons.append(f"RSI={rsi_val:.1f}>={self._config.rsi_enhance_below} 禁止买入")

            if buy_ok and self._config.macd_divergence_filter_enabled:
                if ind.get('macd_divergence', False):
                    buy_ok = False
                    reasons.append("MACD顶背离:价格新高但DIF未确认 禁止买入")

            if buy_ok and self._config.volume_filter_enabled:
                vol_ratio = ind.get('vol_ratio', -1.0)
                if vol_ratio >= 0 and vol_ratio >= self._config.volume_filter_spike_ratio:
                    buy_ok = False
                    reasons.append(f"放量过滤: vol_ratio={vol_ratio:.1f}>={self._config.volume_filter_spike_ratio} 禁止买入")

            if buy_ok and self._config.atr_filter_enabled:
                atr_pct = ind.get('atr_pct', -1.0)
                if atr_pct >= 0 and 25 <= atr_pct < 75:
                    buy_ok = False
                    reasons.append(f"正常波过滤: ATR分位={atr_pct:.0f}% (25~75分位) 禁止买入")

            if buy_ok and self._config.atr_expansion_filter_enabled:
                expansion = ind.get('atr_expansion')
                if expansion is True:
                    buy_ok = False
                    reasons.append("ATR扩张过滤: ATR(20)>1.3×ATR(200) 极端波动禁止买入")

            if buy_ok and self._config.adx_trend_filter_enabled:
                adx_val = ind.get('adx')
                if adx_val is not None and adx_val < 20:
                    buy_ok = False
                    reasons.append(f"ADX趋势过滤: ADX={adx_val:.1f}<20 震荡市禁止买入")

            if buy_ok:
                weight = roc_val
                return 'enter', weight, '; '.join(reasons)
            else:
                return 'hold', 0.0, '; '.join(reasons)

        return 'hold', 0.0, f"ROC={roc_val:.2f} 在观望区间"

    # === T040: 信号统计 ===

    def _compute_signal_stats(self, code, df, latest, prev, indicator_data):
        """计算 T028 分类所需的指标（RSI/量价/波动率/MACD背离/阶段）"""
        if self._rsi_indicator:
            rsi_val = latest.get('rsi')
            if rsi_val is not None:
                indicator_data[code]['rsi'] = float(rsi_val)
            else:
                df_r = self._rsi_indicator.compute(df, n=self._config.rsi_period)
                rsi_val = df_r.iloc[-1].get('rsi')
                if rsi_val is not None:
                    indicator_data[code]['rsi'] = float(rsi_val)

        if 'vol' in df.columns:
            indicator_data[code]['vol_ratio'] = _compute_vol_ratio_static(df)

        if self._atr_indicator:
            df_a = self._atr_indicator.compute(df, n=20)
            indicator_data[code]['atr_pct'] = _compute_atr_pct_static(df_a)

        if self._macd_indicator:
            df_m = self._macd_indicator.compute(df,
                fast=self._config.macd_fast, slow=self._config.macd_slow, signal=self._config.macd_signal)
            indicator_data[code]['macd_divergence'] = _check_macd_divergence(df_m,
                lookback=self._config.macd_divergence_lookback)

    def _build_signal_extra(self, code, roc_val, ind, direction):
        """构建 Decision.extra 中的 T040 分类标签"""
        if not self._config.signal_stats_enabled:
            return {}

        maroc_val = ind.get('maroc')
        prev_maroc = ind.get('prev_maroc', maroc_val)
        rsi_val = ind.get('rsi')
        vol_ratio = ind.get('vol_ratio', -1.0)
        atr_pct = ind.get('atr_pct', -1.0)
        macd_div = ind.get('macd_divergence')
        buy_edge = self._config.buy_roc_edge

        stage = _classify_stage(roc_val, maroc_val, prev_maroc, buy_edge) if maroc_val is not None else "数据不足"
        volume_label = _classify_volume(vol_ratio)
        volatility_label = _classify_volatility(atr_pct)
        rsi_label = _classify_rsi(rsi_val)
        macd_label = "有背离" if macd_div is True else ("无背离" if macd_div is False else "数据不足")

        extra = {
            'stage_label': stage,
            'volume_label': volume_label,
            'volatility_label': volatility_label,
            'rsi_label': rsi_label,
            'macd_div_label': macd_label,
        }

        if direction == 'hold':
            prior = self._blocked_history.get(code)
            if prior:
                extra['prior_block_date'] = prior[0]
                extra['prior_block_reason'] = prior[1]

        return extra

    
