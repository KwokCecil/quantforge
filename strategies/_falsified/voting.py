"""
特性墓地 — ROCStrategy 已证伪的策略增强代码，保留供历史参考。

这些代码原本是 ROCStrategy 的方法，经回测验证全周期无正向贡献，
于 2026-05-30 从 roc_momentum.py 移出至此。不再导入、不运行、不维护。

如需恢复某特性，回调 roc_momentum.py 并添加对应的 config flag。
"""

# ============================================================
# 1. 多指标投票增强 (Voting Subsystem)
# ============================================================
# 原位置：roc_momentum.py L366-L796
# 证伪依据：5.08§一, T012/T025
# 证伪原因：ROC/RSI/MACD 三指标高度共线，全周期无正向贡献
# 包含方法：
#   _produce_voting_decisions          — 入口：多指标投票路由分发
#   _compute_vote_signals              — 子：各指标离散化投票
#   _compute_net_vote                  — 子：等权/加权求和
#   _check_strict_consensus            — 子：全票通过/有反对判断
#   _produce_filter_or_direction_decisions — 子：filter/direction集成
#   _apply_filter_integration          — 子：filter模式（vote>阈值才通过）
#   _apply_direction_integration       — 子：direction模式（vote控制买卖方向）
#   _produce_strict_decisions          — 子：strict模式全票通过
#   _produce_continuous_decisions      — 子：continuous模式Z-score融合
#   _produce_boost_decisions           — 子：boost模式权重+投票加分

# ============================================================
# 原代码（不可独立运行，依赖 ROCStrategy._evaluate 等共享方法）
# 包装为伪类以通过语法检查，仅为存档参考
# ============================================================

class _DeadCode:
    # ============================================================
    # 多指标投票增强
    # ============================================================

    def _produce_voting_decisions(self, data: DataResponse,
                                   positions: dict[str, Any]) -> list[Decision]:
        """多指标投票决策。ROC + RSI + MACD 三指标投票/共识，产出 ROTATION 决策。

        ⚠️ 已证伪(5.08§一, T012/T025)：ROC/RSI/MACD共线，全周期无正向贡献。

        流程：计算三指标 → 离散化为+1/0/-1 → 投票 → 按集成方式产出决策。
        """
        cfg = self._config
        roc_values = {}
        vote_results = {}
        indicator_data = {}

        for code, df in data.bar_data.items():
            if df.empty or len(df) < cfg.EMPTY_DAY:
                continue

            df = self._roc_indicator.compute(df, n=cfg.roc_n, m=cfg.roc_m)
            df = self._ma_indicator.compute(df, periods=[cfg.ma_period])
            df = self._rsi_indicator.compute(df, n=cfg.rsi_period)
            df = self._macd_indicator.compute(
                df, fast=cfg.macd_fast, slow=cfg.macd_slow, signal=cfg.macd_signal
            )

            if self._vol_indicator:
                df = self._vol_indicator.compute(df, n=cfg.ma_period)
                if 'volatility' in df.columns:
                    data.bar_data[code] = df

            latest = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else latest

            try:
                roc_val = float(latest.get('roc', np.nan))
                maroc_val = float(latest.get('maroc', np.nan))
                rsi_val = float(latest.get('rsi', np.nan))
                dif_val = float(latest.get('dif', np.nan))
                dea_val = float(latest.get('dea', np.nan))
                macd_bar_val = float(latest.get('macd_bar', np.nan))
                close_val = float(latest.get('close', 0) or 0)
                ma_val = float(latest.get(f'ma{cfg.ma_period}', 0) or 0)
                prev_roc = float(prev.get('roc', 0) or 0)
                prev_maroc = float(prev.get('maroc', 0) or 0)
            except (ValueError, TypeError):
                continue

            if np.isnan(roc_val) or np.isnan(maroc_val):
                continue

            roc_values[code] = roc_val

            vote_signals = self._compute_vote_signals(
                roc_val, rsi_val, dif_val, dea_val, macd_bar_val
            )
            net_vote = self._compute_net_vote(vote_signals)
            vote_results[code] = net_vote

            indicator_data[code] = {
                'roc': roc_val, 'maroc': maroc_val,
                'close': close_val, 'ma': ma_val,
                'prev_roc': prev_roc, 'prev_maroc': prev_maroc,
                'rsi': rsi_val, 'dif': dif_val, 'dea': dea_val,
                'macd_bar': macd_bar_val, 'net_vote': net_vote,
            }

        if cfg.voting_method == 'strict':
            return self._produce_strict_decisions(
                roc_values, vote_results, indicator_data, positions
            )
        if cfg.voting_method == 'continuous':
            return self._produce_continuous_decisions(
                roc_values, vote_results, indicator_data, positions
            )

        integration = cfg.voting_integration
        if integration == 'boost':
            return self._produce_boost_decisions(
                roc_values, vote_results, indicator_data, positions
            )
        return self._produce_filter_or_direction_decisions(
            roc_values, vote_results, indicator_data, positions, integration
        )

    def _compute_vote_signals(self, roc_val: float, rsi_val: float,
                                dif_val: float, dea_val: float,
                                macd_bar_val: float) -> dict[str, int]:
        """计算各指标的投票信号（+1看多/0中性/-1看空）。NaN指标视为中性。"""
        cfg = self._config
        signals = {}

        if not np.isnan(roc_val):
            if roc_val >= cfg.buy_roc_edge:
                signals['ROC'] = 1
            elif roc_val <= cfg.sell_roc_edge:
                signals['ROC'] = -1
            else:
                signals['ROC'] = 0
        else:
            signals['ROC'] = 0

        if not np.isnan(rsi_val):
            if rsi_val >= cfg.rsi_bull_threshold:
                signals['RSI'] = 1
            elif rsi_val <= cfg.rsi_bear_threshold:
                signals['RSI'] = -1
            else:
                signals['RSI'] = 0
        else:
            signals['RSI'] = 0

        if not np.isnan(dif_val) and not np.isnan(dea_val) and not np.isnan(macd_bar_val):
            if dif_val > dea_val and macd_bar_val > 0:
                signals['MACD'] = 1
            elif dif_val < dea_val and macd_bar_val < 0:
                signals['MACD'] = -1
            else:
                signals['MACD'] = 0
        else:
            signals['MACD'] = 0

        return signals

    def _compute_net_vote(self, vote_signals: dict[str, int]) -> float:
        """根据投票方法计算净票数。majority → 等权求和，weighted → 加权求和。"""
        cfg = self._config
        method = cfg.voting_method

        if method == 'weighted' and cfg.indicator_weights:
            total = 0.0
            for indicator in cfg.voting_indicators:
                w = cfg.indicator_weights.get(indicator, 1.0)
                total += w * vote_signals.get(indicator, 0)
            return total
        return float(sum(vote_signals.values()))

    def _check_strict_consensus(self, vote_signals: dict[str, int]) -> tuple[bool, bool]:
        """strict模式：检查是否全票通过（all_bull=True则买入）或有人看空（any_bear=True则卖出）。"""
        cfg = self._config
        all_bull = all(vote_signals.get(ind, 0) == 1 for ind in cfg.voting_indicators)
        any_bear = any(vote_signals.get(ind, 0) == -1 for ind in cfg.voting_indicators)
        return all_bull, any_bear

    def _produce_filter_or_direction_decisions(self, roc_values: dict, vote_results: dict,
                                                 indicator_data: dict,
                                                 positions: dict[str, Any],
                                                 integration: str) -> list[Decision]:
        """filter/direction 集成：ROC排名 + 投票影响方向。"""
        cfg = self._config
        sorted_codes = sorted(roc_values.items(), key=lambda x: x[1], reverse=True)

        crash_active = False
        crash_reason = ""
        if cfg.crash_protection_enabled and cfg.benchmark_code in data.bar_data:
            bm_df = data.bar_data[cfg.benchmark_code]
            crash_active, crash_reason = self._check_crash_protection(bm_df)

        decisions = []

        for priority, (code, roc_val) in enumerate(sorted_codes):
            ind = indicator_data.get(code, {})
            net_vote = vote_results.get(code, 0.0)
            direction, weight, reason = self._evaluate(code, roc_val, ind, positions)

            if integration == 'filter':
                direction = self._apply_filter_integration(direction, net_vote, code, positions)
            else:
                direction = self._apply_direction_integration(direction, net_vote, code, positions)

            if direction != 'exit' and direction != 'enter':
                weight = 0.0

            vote_detail = f"vote={net_vote:.0f}"
            if net_vote >= cfg.voting_threshold_buy:
                vote_detail += "↑"
            elif net_vote <= cfg.voting_threshold_sell:
                vote_detail += "↓"

            decisions.append(Decision(
                decision_type=DecisionType.ROTATION,
                timestamp=datetime.now(),
                reason=f"[{integration} {vote_detail}] {reason}",
                target_code=code,
                direction=direction,
                weight=weight,
                priority=priority,
                confidence=min(abs(roc_val) / 50.0, 1.0),
                strategy_name=self.name,
                indicator_values={
                    'roc': roc_val, 'maroc': ind.get('maroc', 0.0),
                    'rsi': ind.get('rsi', np.nan), 'dif': ind.get('dif', np.nan),
                    'net_vote': net_vote,
                },
            ))

        return decisions

    def _apply_filter_integration(self, direction: str, net_vote: float,
                                    code: str, positions: dict[str, Any]) -> str:
        """filter集成：买入需ROC条件+投票同时通过，卖出逻辑不变。"""
        cfg = self._config
        if direction == 'enter' and net_vote < cfg.voting_threshold_buy:
            return 'hold'
        return direction

    def _apply_direction_integration(self, direction: str, net_vote: float,
                                       code: str, positions: dict[str, Any]) -> str:
        """direction集成：投票控制买卖方向，不通过时可能强制exit。"""
        cfg = self._config
        if code in positions and net_vote <= cfg.voting_threshold_sell:
            return 'exit'
        if direction == 'enter' and net_vote < cfg.voting_threshold_buy:
            return 'hold'
        return direction

    def _produce_strict_decisions(self, roc_values: dict, vote_results: dict,
                                    indicator_data: dict,
                                    positions: dict[str, Any]) -> list[Decision]:
        """strict模式：全票通过才买入，任一反对就卖出。不依赖ROC排名。"""
        cfg = self._config
        decisions = []

        for code, roc_val in sorted(roc_values.items(), key=lambda x: x[1], reverse=True):
            ind = indicator_data.get(code, {})
            vote_signals = self._compute_vote_signals(
                roc_val,
                ind.get('rsi', np.nan),
                ind.get('dif', np.nan),
                ind.get('dea', np.nan),
                ind.get('macd_bar', np.nan),
            )
            all_bull, any_bear = self._check_strict_consensus(vote_signals)

            if code in positions and any_bear:
                direction = 'exit'
                weight = 0.0
                reason = "strict: 有指标看空 → 强制退出"
            elif all_bull:
                direction, weight, reason = self._evaluate(code, roc_val, ind, positions)
                reason = f"strict: 全票通过 → {reason}"
            else:
                direction = 'hold'
                weight = 0.0
                reason = f"strict: 非全票通过({sum(1 for v in vote_signals.values() if v==1)}/3) → 观望"

            if direction != 'exit' and direction != 'enter':
                weight = 0.0

            bullish_count = sum(1 for v in vote_signals.values() if v == 1)

            decisions.append(Decision(
                decision_type=DecisionType.ROTATION,
                timestamp=datetime.now(),
                reason=reason,
                target_code=code,
                direction=direction,
                weight=weight,
                priority=0,
                confidence=bullish_count / len(cfg.voting_indicators),
                strategy_name=self.name,
                indicator_values={
                    'roc': roc_val, 'maroc': ind.get('maroc', 0.0),
                    'rsi': ind.get('rsi', np.nan), 'dif': ind.get('dif', np.nan),
                    'bullish_count': bullish_count,
                },
            ))

        return decisions

    def _produce_continuous_decisions(self, roc_values: dict, vote_results: dict,
                                        indicator_data: dict,
                                        positions: dict[str, Any]) -> list[Decision]:
        """continuous模式：各指标原始值 Z-score(截面归一化) 后加权融合。"""
        cfg = self._config

        codes_list = list(indicator_data.keys())
        all_roc = np.array([indicator_data[c].get('roc', np.nan) for c in codes_list])
        all_rsi = np.array([indicator_data[c].get('rsi', np.nan) for c in codes_list])
        all_macd_bar = np.array([indicator_data[c].get('macd_bar', np.nan) for c in codes_list])

        def safe_zscore(values: np.ndarray) -> np.ndarray:
            finite = values[np.isfinite(values)]
            if len(finite) < 2:
                return np.zeros_like(values)
            mean = np.mean(finite)
            std = np.std(finite)
            if std == 0:
                return np.zeros_like(values)
            return np.clip((values - mean) / std, -3.0, 3.0)

        roc_zs = {code: z for code, z in zip(codes_list, safe_zscore(all_roc))}
        rsi_zs = {code: z for code, z in zip(codes_list, safe_zscore(all_rsi))}
        macd_zs = {code: z for code, z in zip(codes_list, safe_zscore(all_macd_bar))}

        iw = cfg.indicator_weights if cfg.indicator_weights else {'ROC': 1.0, 'RSI': 1.0, 'MACD': 1.0}

        fused = {}
        for code in codes_list:
            z_roc = roc_zs.get(code, 0.0) if np.isfinite(roc_zs.get(code, 0.0)) else 0.0
            z_rsi = rsi_zs.get(code, 0.0) if np.isfinite(rsi_zs.get(code, 0.0)) else 0.0
            z_macd = macd_zs.get(code, 0.0) if np.isfinite(macd_zs.get(code, 0.0)) else 0.0
            fused[code] = (
                iw.get('ROC', 1.0) * z_roc +
                iw.get('RSI', 1.0) * z_rsi +
                iw.get('MACD', 1.0) * z_macd
            )

        sorted_codes = sorted(fused.items(), key=lambda x: x[1], reverse=True)
        decisions = []

        BUY_Z = 2.0   # ±2σ: 统计显著区域，Z-score >2进入买入区、<-2进入卖出区
        SELL_Z = -2.0  # 注：continuous voting模式已证伪（5.08, T012/T025），此段仅保留以备未来验证

        for priority, (code, fusion_score) in enumerate(sorted_codes):
            ind = indicator_data.get(code, {})
            roc_val = ind.get('roc', 0.0)

            if code in positions and fusion_score <= SELL_Z:
                direction = 'exit'
                weight = 0.0
                reason = f"continuous fusion={fusion_score:.2f} <= {SELL_Z} → 强制退出"
            elif fusion_score >= BUY_Z:
                direction, weight, reason = self._evaluate(code, roc_val, ind, positions)
                reason = f"continuous fusion={fusion_score:.2f} → {reason}"
            else:
                direction = 'hold'
                weight = 0.0
                reason = f"continuous fusion={fusion_score:.2f} 在观望区间"

            if direction != 'exit' and direction != 'enter':
                weight = 0.0

            decisions.append(Decision(
                decision_type=DecisionType.ROTATION,
                timestamp=datetime.now(),
                reason=reason,
                target_code=code,
                direction=direction,
                weight=weight,
                priority=priority,
                confidence=min(abs(fusion_score) / 6.0, 1.0),
                strategy_name=self.name,
                indicator_values={
                    'roc': roc_val, 'maroc': ind.get('maroc', 0.0),
                    'rsi': ind.get('rsi', np.nan), 'dif': ind.get('dif', np.nan),
                    'fusion_score': fusion_score,
                },
            ))

        return decisions

    def _produce_boost_decisions(self, roc_values: dict, vote_results: dict,
                                   indicator_data: dict,
                                   positions: dict[str, Any]) -> list[Decision]:
        """boost集成：ROC权重 + 投票加分后重新排序。"""
        cfg = self._config

        adjusted = {}
        for code, roc_val in roc_values.items():
            net_vote = vote_results.get(code, 0.0)
            adj_score = roc_val + net_vote * 10.0
            adjusted[code] = (adj_score, net_vote)

        sorted_codes = sorted(adjusted.items(), key=lambda x: x[1][0], reverse=True)
        decisions = []

        for priority, (code, (adj_score, net_vote)) in enumerate(sorted_codes):
            ind = indicator_data.get(code, {})
            roc_val = roc_values.get(code, 0.0)
            direction, weight, reason = self._evaluate(code, roc_val, ind, positions)

            if direction != 'exit' and direction != 'enter':
                weight = 0.0

            if net_vote < 0:
                reason = f"boost vote={net_vote:.0f}↓ → {reason}"
            elif net_vote > 0:
                reason = f"boost vote={net_vote:.0f}↑ → {reason}"

            decisions.append(Decision(
                decision_type=DecisionType.ROTATION,
                timestamp=datetime.now(),
                reason=reason,
                target_code=code,
                direction=direction,
                weight=weight,
                priority=priority,
                confidence=min(adj_score / 50.0, 1.0),
                strategy_name=self.name,
                indicator_values={
                    'roc': roc_val, 'maroc': ind.get('maroc', 0.0),
                    'rsi': ind.get('rsi', np.nan), 'dif': ind.get('dif', np.nan),
                    'net_vote': net_vote, 'adj_score': adj_score,
                },
            ))

        return decisions