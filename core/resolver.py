from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import numpy as np
from loguru import logger

from quantforge.core.data_feed import DataResponse
from quantforge.core.decision import Decision, DecisionType


@dataclass
class TargetPosition:
    """目标仓位——Resolver产出的可执行仓位方案。Executor根据此对象执行交易。"""
    code: str
    target_weight: float
    reason: str


class StopLossChecker:
    """止损检查器。高水位止损优先，成本止损次之。触发止损后跳过后续检查。"""
    def __init__(self, high_watermark_stop_edge: float = 0.15, cut_loss_edge: float = 0.08):
        self.high_watermark_stop_edge = high_watermark_stop_edge
        self.cut_loss_edge = cut_loss_edge

    def check(self, positions: dict[str, Any], data: DataResponse) -> list[TargetPosition]:
        targets: list[TargetPosition] = []

        for code, pos in positions.items():
            if code not in data.bar_data:
                continue
            df = data.bar_data[code]
            if df.empty:
                continue

            current_price = float(df.iloc[-1]['close'])

            if pos.get('high_watermark') and pos['high_watermark'] > 0:
                drawdown = (pos['high_watermark'] - current_price) / pos['high_watermark']
                if drawdown >= self.high_watermark_stop_edge:
                    targets.append(TargetPosition(
                        code=code,
                        target_weight=0.0,
                        reason=f"高水位止损: {pos['high_watermark']:.2f} -> {current_price:.2f} 回落{drawdown:.1%}",
                    ))
                    continue

            if pos.get('avg_cost') and pos['avg_cost'] > 0:
                if current_price < pos['avg_cost'] * (1 - self.cut_loss_edge):
                    loss = 1 - current_price / pos['avg_cost']
                    targets.append(TargetPosition(
                        code=code,
                        target_weight=0.0,
                        reason=f"成本止损: 亏损{loss:.1%}",
                    ))

        return targets


class Resolver(ABC):
    """组合层抽象接口。将Decision转化为TargetPosition，策略与仓位管理解耦。

    核心设计：策略不管仓位怎么分配，Resolver不管策略怎么决策。
    同一策略可搭配不同Resolver（等权/Kelly/自定义），产生不同仓位方案。
    """
    @abstractmethod
    def resolve(self,
                decisions: list[Decision],
                current_positions: dict[str, Any],
                available_capital: float,
                data: DataResponse | None = None) -> list[TargetPosition]:
        pass


class RankingResolver(Resolver):
    """轮动策略专用决议器。按priority排序取TOP_K，分配权重，处理止损。

    卖出逻辑（与旧代码对齐）：
    1. 信号卖出：策略exit决策（ROC<卖出阈值等）→ 始终执行
    2. 止损卖出：成本止损/高水位止损 → 始终执行
    3. TOP_K卖出（top_k_sell=True）：持仓不在TOP_K中 → 立即卖出
    4. 挤压卖出（top_k_sell=False）：持仓不在TOP_K中，且需要腾位置给新买入 → 按ROC从低到高挤出
       若无空间压力，非TOP_K持仓继续持有
    """
    def __init__(self, top_k: int = 10, weight_method: str = 'equal',
                 high_watermark_stop_edge: float = 0.15,
                 cut_loss_edge: float = 0.08,
                 top_k_sell: bool = True):
        self.top_k = top_k
        self.weight_method = weight_method
        self.top_k_sell = top_k_sell
        self._stop_loss = StopLossChecker(high_watermark_stop_edge, cut_loss_edge)

    def resolve(self,
                decisions: list[Decision],
                current_positions: dict[str, Any],
                available_capital: float,
                data: DataResponse | None = None) -> list[TargetPosition]:

        rotation_decisions = [d for d in decisions if d.decision_type == DecisionType.ROTATION]
        rotation_decisions.sort(key=lambda d: d.priority)
        decision_by_code = {d.target_code: d for d in rotation_decisions}

        enter_decisions = [d for d in rotation_decisions if d.direction == 'enter']
        exit_decisions = [d for d in rotation_decisions if d.direction == 'exit']
        top_k_decisions = enter_decisions[:self.top_k]

        targets: list[TargetPosition] = []
        top_k_codes = set()
        sell_codes = set()

        # 1. TOP_K买入目标
        if top_k_decisions:
            total_weight = sum(d.weight for d in top_k_decisions)

            for d in top_k_decisions:
                top_k_codes.add(d.target_code)

                if self.weight_method == 'equal':
                    weight = 1.0 / len(top_k_decisions)
                elif self.weight_method == 'signal_weight':
                    weight = d.weight / total_weight if total_weight > 0 else 1.0 / len(top_k_decisions)
                elif self.weight_method == 'kelly':
                    weight = self._kelly_weight(d)
                elif self.weight_method == 'inverse_vol':
                    weight = 1.0 / len(top_k_decisions)  # 初始等权，后续按波动率调整
                else:
                    weight = 1.0 / len(top_k_decisions)

                targets.append(TargetPosition(
                    code=d.target_code,
                    target_weight=weight,
                    reason=d.reason,
                ))

            # inverse_vol 权重重算：权重 ∝ 1/波动率
            if self.weight_method == 'inverse_vol' and data is not None:
                self._apply_inverse_vol(targets, data)

        # 2. 信号卖出：策略exit决策 → 始终执行（即使标的在TOP_K中）
        for d in exit_decisions:
            if d.target_code in current_positions:
                sell_codes.add(d.target_code)
                targets = [t for t in targets if t.code != d.target_code]
                targets.append(TargetPosition(
                    code=d.target_code,
                    target_weight=0.0,
                    reason=d.reason,
                ))

        # 3. 止损卖出 → 始终执行
        if data is not None:
            stop_targets = self._stop_loss.check(current_positions, data)
            for t in stop_targets:
                sell_codes.add(t.code)
                targets = [tt for tt in targets if tt.code != t.code]
                targets.append(t)

        # 4. 不在TOP_K的持仓处理
        non_top_k_held = [code for code in current_positions
                          if code not in top_k_codes and code not in sell_codes]

        if self.top_k_sell:
            # 模式A：跌出TOP_K立即卖出（原逻辑）
            for code in non_top_k_held:
                targets.append(TargetPosition(
                    code=code,
                    target_weight=0.0,
                    reason="不在TOP_K列表中，卖出",
                ))
        else:
            # 模式B：挤压卖出——仅当需要腾位置给新买入时才卖出非TOP_K持仓
            new_buy_count = len([d for d in top_k_decisions if d.target_code not in current_positions])
            available_slots = max(self.top_k - len([c for c in current_positions if c in top_k_codes or c in sell_codes]), 0)
            need_free = new_buy_count - available_slots

            if need_free > 0 and non_top_k_held:
                exit_by_code = {d.target_code: d for d in exit_decisions}
                non_top_k_with_roc = []
                for code in non_top_k_held:
                    d = exit_by_code.get(code)
                    roc_val = d.indicator_values.get('roc', 0.0) if d and d.indicator_values else 0.0
                    non_top_k_with_roc.append((code, roc_val))
                non_top_k_with_roc.sort(key=lambda x: x[1])

                for code, roc_val in non_top_k_with_roc[:need_free]:
                    targets.append(TargetPosition(
                        code=code,
                        target_weight=0.0,
                        reason=f"挤压卖出: ROC={roc_val:.2f}，腾位置给新买入",
                    ))

        return targets

    def _kelly_weight(self, decision: Decision) -> float:
        """Kelly公式: f* = (bp - q) / b，b=盈亏比, p=置信度, q=1-p"""
        p = decision.confidence
        if p <= 0 or p >= 1:
            return 0.0
        b = max(abs(decision.weight), 0.01)
        q = 1 - p
        f = (b * p - q) / b
        return max(min(f, 1.0), 0.0)

    def _apply_inverse_vol(self, targets: list[TargetPosition], data: DataResponse):
        """以波动率倒数加权重新分配 TOP_K 权重。波动率越高权重越低（风险平价思想）。"""
        volatilities = {}
        for t in targets:
            if t.code in data.bar_data:
                df = data.bar_data[t.code]
                if not df.empty and 'volatility' in df.columns:
                    vol = float(df.iloc[-1]['volatility'])
                    if not np.isnan(vol) and vol > 0:
                        volatilities[t.code] = vol

        if not volatilities or len(volatilities) < 2:
            return

        inv_vols = {code: 1.0 / max(vol, 0.001) for code, vol in volatilities.items()}
        total_inv = sum(inv_vols.values())
        if total_inv <= 0:
            return

        vol_weights = {code: inv / total_inv for code, inv in inv_vols.items()}

        for t in targets:
            if t.code in vol_weights:
                t.target_weight = vol_weights[t.code]
                t.reason += f" (波动率加权={t.target_weight:.0%})"


class MacroOverlayResolver(Resolver):
    """宏观-动量双层融合决议器。

    同时消费 TIMING（宏观估值信号）和 ROTATION（动量排名信号），
    产出统一的 TargetPosition 列表。

    核心机制：
    - CDR = f(ERP_percentile)：分段线性映射 + EMA平滑 + 趋势过滤 + 绝对ERP修正
    - final_weight = roc_pick.weight × CDR
    - 防御仓位 = CASH（权重 = 1.0 - CDR）
    """
    def __init__(self,
                 top_k: int = 3,
                 erp_abs_min: float = -5.0,
                 erp_abs_max: float = 8.0,
                 trend_ma: int = 50,
                 cdr_smooth_alpha: float = 0.3,
                 defensive_code: str = "",
                 min_position_pct: float = 0.05,
                 top_k_sell: bool = False,
                 high_watermark_stop_edge: float = 0.15,
                 cut_loss_edge: float = 0.08):
        self.top_k = top_k
        self.erp_abs_min = erp_abs_min
        self.erp_abs_max = erp_abs_max
        self.trend_ma = trend_ma
        self.cdr_smooth_alpha = cdr_smooth_alpha
        self.defensive_code = defensive_code
        self.min_position_pct = min_position_pct
        self.top_k_sell = top_k_sell
        self._stop_loss = StopLossChecker(high_watermark_stop_edge, cut_loss_edge)
        self._cdr_state = 0.5
        self._cdr_initialized = False

    def resolve(self,
                decisions: list[Decision],
                current_positions: dict[str, Any],
                available_capital: float,
                data: DataResponse | None = None) -> list[TargetPosition]:

        targets: list[TargetPosition] = []

        timing_d = self._extract_timing_decision(decisions)
        rotation_decisions = [d for d in decisions if d.decision_type == DecisionType.ROTATION]

        if not timing_d:
            logger.warning("MacroOverlayResolver: 无TIMING决策，跳过宏观融合")
            return targets

        erp = timing_d.indicator_values.get('erp', 0)
        percentile = timing_d.indicator_values.get('percentile', 50)

        trend_ok = self._check_trend(data, timing_d.target_code) if data is not None else True

        cdr_raw = self._percentile_to_cdr(percentile)
        cdr_raw = self._apply_erp_abs_cap(cdr_raw, erp)
        cdr_raw = self._apply_trend_cap(cdr_raw, trend_ok)
        cdr = self._smooth_cdr(cdr_raw)

        enter_decisions = [d for d in rotation_decisions if d.direction == 'enter']
        enter_decisions.sort(key=lambda d: d.priority)
        top_k_picks = enter_decisions[:self.top_k]

        pick_codes = {p.target_code for p in top_k_picks}
        exit_decisions = [d for d in rotation_decisions if d.direction == 'exit']

        # 1. 处理已有的持仓
        for code in sorted(pick_codes):
            if code in current_positions:
                continue
            roc_d = next((d for d in top_k_picks if d.target_code == code), None)
            if roc_d is None:
                continue
            roc_w = roc_d.weight if roc_d.weight > 0 else (1.0 / max(len(top_k_picks), 1))
            final_w = roc_w * cdr
            if final_w < self.min_position_pct:
                continue
            targets.append(TargetPosition(
                code=code,
                target_weight=final_w,
                reason=f"ERP{percentile:.0f}% CDR={cdr:.0%} | {roc_d.reason}",
            ))

        # 2. 处理需要退出的持仓（exit信号 + 排名下降）
        for code, pos in current_positions.items():
            should_exit = False

            if any(d.target_code == code for d in exit_decisions):
                should_exit = True

            if not should_exit and self.top_k_sell and code not in pick_codes:
                should_exit = True

            if should_exit:
                targets.append(TargetPosition(
                    code=code,
                    target_weight=0.0,
                    reason="优先级下降/退场信号",
                ))

        if not self.top_k_sell:
            # 挤压卖出：仅当新买入需要腾位置时才卖出非pick持仓
            already_in_pick = {code for code in current_positions if code in pick_codes}
            exit_codes_set = {d.target_code for d in exit_decisions}
            occupied_slots = len(already_in_pick - exit_codes_set)
            new_buy_count = len([c for c in pick_codes if c not in current_positions])
            available_slots = max(self.top_k - occupied_slots, 0)
            need_free = new_buy_count - available_slots

            if need_free > 0:
                non_exited_held = [code for code in current_positions
                                   if code not in pick_codes and code not in exit_codes_set]
                if non_exited_held:
                    for code in non_exited_held[:need_free]:
                        targets.append(TargetPosition(
                            code=code,
                            target_weight=0.0,
                            reason="挤压退出: 腾位置给新的TOP_K",
                        ))

        # 3. 调整仍在pick中的现有持仓权重
        for code, pos in current_positions.items():
            if code not in pick_codes:
                continue
            roc_d = next((d for d in top_k_picks if d.target_code == code), None)
            if roc_d is None:
                continue
            roc_w = roc_d.weight if roc_d.weight > 0 else (1.0 / max(len(top_k_picks), 1))
            final_w = roc_w * cdr
            if final_w < self.min_position_pct:
                continue
            targets.append(TargetPosition(
                code=code,
                target_weight=final_w,
                reason=f"ERP{percentile:.0f}% CDR={cdr:.0%} | {roc_d.reason}",
            ))

        # 4. 汇总分配
        allocated = sum(t.target_weight for t in targets if t.target_weight > 0)
        remaining = max(0.0, 1.0 - allocated)
        if remaining > self.min_position_pct:
            if self.defensive_code:
                targets.append(TargetPosition(
                    code=self.defensive_code,
                    target_weight=remaining,
                    reason="防御仓位（债基）",
                ))
            else:
                targets.append(TargetPosition(
                    code="CASH",
                    target_weight=remaining,
                    reason=f"现金/观望 CDR={cdr:.0%}",
                ))

        # 5. 止损检查
        if data is not None:
            stop_targets = self._stop_loss.check(current_positions, data)
            stop_codes = {t.code for t in stop_targets}
            targets = [t for t in targets if t.code not in stop_codes]
            targets.extend(stop_targets)

        logger.debug(f"MacroOverlayResolver: CDR={cdr:.0%} ERP={erp:.1f} "
                      f"P={percentile:.0f}% picks={pick_codes} targets={len(targets)}")

        return targets

    def _extract_timing_decision(self, decisions: list[Decision]) -> Decision | None:
        for d in decisions:
            if d.decision_type == DecisionType.TIMING:
                return d
        return None

    def _percentile_to_cdr(self, percentile: float) -> float:
        mapping = [
            (0,    0.10, 0.00, 0.10),
            (0.10, 0.30, 0.10, 0.30),
            (0.30, 0.50, 0.30, 0.50),
            (0.50, 0.70, 0.50, 0.70),
            (0.70, 0.90, 0.70, 0.90),
            (0.90, 1.00, 0.90, 1.00),
        ]
        frac = percentile / 100.0
        for (lo_pct, hi_pct, lo_cdr, hi_cdr) in mapping:
            if lo_pct <= frac <= hi_pct:
                t = (frac - lo_pct) / (hi_pct - lo_pct)
                return lo_cdr + t * (hi_cdr - lo_cdr)
        return 0.5

    def _apply_erp_abs_cap(self, cdr: float, erp: float) -> float:
        if cdr > 0.5 and erp < self.erp_abs_min:
            return cdr * 0.5
        return cdr

    def _apply_trend_cap(self, cdr: float, trend_ok: bool) -> float:
        if not trend_ok and cdr > 0.5:
            return 0.5
        return cdr

    def _smooth_cdr(self, cdr_raw: float) -> float:
        if not self._cdr_initialized:
            self._cdr_state = cdr_raw
            self._cdr_initialized = True
            return cdr_raw
        cdr_smoothed = (self.cdr_smooth_alpha * cdr_raw +
                        (1.0 - self.cdr_smooth_alpha) * self._cdr_state)
        self._cdr_state = cdr_smoothed
        return cdr_smoothed

    def _check_trend(self, data: DataResponse, code: str) -> bool:
        if not data or code not in data.bar_data:
            return True
        df = data.bar_data[code]
        if df.empty or 'close' not in df.columns or len(df) < self.trend_ma:
            return True
        close = df['close'].astype(float).values
        ma = np.mean(close[-self.trend_ma:])
        return float(close[-1]) > ma


class TimingResolver(Resolver):
    """择时策略专用决议器。direction→target_weight的简单映射。

    映射规则：enter→全仓股票, exit→清仓(或配债基), hold→不产出TargetPosition。
    宏观择时策略（股债利差、AH溢价）在L3中归入TIMING类型。

    T015 扩展：支持 bond_etf 对端配置，exit 时自动配置债基替代空仓。
    支持 tiered_position + rebalance_threshold 连续仓位调仓模式。
    """
    def __init__(self,
                 high_watermark_stop_edge: float = 0.15,
                 cut_loss_edge: float = 0.08,
                 bond_etf: str = "",
                 rebalance_threshold: float = 0.30):
        self._stop_loss = StopLossChecker(high_watermark_stop_edge, cut_loss_edge)
        self.bond_etf = bond_etf
        self.rebalance_threshold = rebalance_threshold

    def resolve(self,
                decisions: list[Decision],
                current_positions: dict[str, Any],
                available_capital: float,
                data: DataResponse | None = None) -> list[TargetPosition]:

        targets: list[TargetPosition] = []

        for d in decisions:
            if d.decision_type != DecisionType.TIMING:
                continue

            if d.direction == 'enter':
                new_weight = d.weight if d.weight > 0 else 1.0

                if self.bond_etf and new_weight < 1.0:
                    cur_w = self._current_stock_weight(current_positions, data, d.target_code)
                    if cur_w is not None and abs(new_weight - cur_w) > self.rebalance_threshold:
                        if new_weight < cur_w:
                            targets.append(TargetPosition(
                                code=d.target_code,
                                target_weight=new_weight,
                                reason=f"{d.reason} → 调仓减至{new_weight:.0%}",
                            ))
                            if self.bond_etf:
                                targets.append(TargetPosition(
                                    code=self.bond_etf,
                                    target_weight=1.0 - new_weight,
                                    reason=f"对端债基配比",
                                ))
                            continue
                        else:
                            if self.bond_etf and self.bond_etf in current_positions:
                                targets.append(TargetPosition(
                                    code=self.bond_etf,
                                    target_weight=0.0,
                                    reason=f"加仓股票，清仓债基",
                                ))

                if self.bond_etf and self.bond_etf in current_positions:
                    targets.append(TargetPosition(
                        code=self.bond_etf,
                        target_weight=0.0,
                        reason=f"重新进入股票模式，清仓债基",
                    ))
                targets.append(TargetPosition(
                    code=d.target_code,
                    target_weight=new_weight,
                    reason=d.reason,
                ))
                if self.bond_etf and new_weight < 1.0:
                    targets.append(TargetPosition(
                        code=self.bond_etf,
                        target_weight=1.0 - new_weight,
                        reason=f"对端债基配比",
                    ))
            elif d.direction == 'exit':
                if self.bond_etf:
                    targets.append(TargetPosition(
                        code=d.target_code,
                        target_weight=0.0,
                        reason=d.reason,
                    ))
                    targets.append(TargetPosition(
                        code=self.bond_etf,
                        target_weight=1.0,
                        reason=f"{d.reason} → 对端债基",
                    ))
                else:
                    targets.append(TargetPosition(
                        code=d.target_code,
                        target_weight=0.0,
                        reason=d.reason,
                    ))

        if data is not None:
            stop_targets = self._stop_loss.check(current_positions, data)
            stop_codes = {t.code for t in stop_targets}
            targets = [t for t in targets if t.code not in stop_codes]
            targets.extend(stop_targets)

        return targets

    def _current_stock_weight(self, positions: dict[str, Any],
                               data: DataResponse | None, stock_code: str) -> float | None:
        """计算当前股票持仓权重（占总资产比例）。"""
        if data is None or stock_code not in positions or stock_code not in data.bar_data:
            return None
        df = data.bar_data[stock_code]
        if df.empty:
            return None
        stock_value = positions[stock_code].get('shares', 0) * float(df.iloc[-1]['close'])
        total_value = stock_value
        for code, pos in positions.items():
            if code in ('free_capital', 'last_update'):
                continue
            if code not in data.bar_data or data.bar_data[code].empty:
                continue
            total_value += pos.get('shares', 0) * float(data.bar_data[code].iloc[-1]['close'])
        if total_value <= 0:
            return None
        return stock_value / total_value


def make_ranking_resolver(config, weight_method: str = 'signal_weight') -> RankingResolver:
    """根据策略配置创建 RankingResolver。"""
    return RankingResolver(
        top_k=config.top_k,
        weight_method=weight_method,
        high_watermark_stop_edge=config.high_watermark_stop_edge if config.HIGH_WATERMARK_STOP else float('inf'),
        cut_loss_edge=config.cut_loss_edge if config.CUT_LOSS else float('inf'),
        top_k_sell=config.TOP_K_SELL,
    )