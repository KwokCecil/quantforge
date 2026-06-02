"""
特性墓地 — 多因子增强模块。

原位置：roc_momentum.py L1268-L1367
证伪依据：5.08§一, T005/T025
证伪原因：多周期ROC(5/15/22)排名加权，因子间共线 r>0.83，比单因子(ROC22)低5.26pp
包含方法：_produce_multifactor_decisions
"""

# ============================================================
# 原代码（不可独立运行，依赖 ROCStrategy._evaluate 等共享方法）
# 包装为伪类以通过语法检查，仅为存档参考
# ============================================================

class _DeadCode:
    def _produce_multifactor_decisions(self, data: DataResponse,
                                        positions: dict[str, Any]) -> list[Decision]:
        """多因子模式：多周期ROC排名加权打分。综合得分排序，取最高分标的买入。
        ⚠️ 已证伪(5.08§一, T005/T025)：因子共线r>0.83，比单因子低5.26pp。

        流程：对每个周期独立排名 → 加权总分 → 按总分排序 → _evaluate决策。
        """
        periods = list(self._config.multi_roc_periods)
        weights = list(self._config.multi_factor_weights)

        # 每个周期 → {code: roc_val}
        period_roc = {p: {} for p in periods}
        indicator_data = {}

        for code, df in data.bar_data.items():
            if df.empty or len(df) < self._config.EMPTY_DAY:
                continue

            df = self._ma_indicator.compute(df, periods=[self._config.ma_period])

            if self._config.rsi_enhance_enabled and self._rsi_indicator:
                df = self._rsi_indicator.compute(df, n=self._config.rsi_period)

            if self._vol_indicator:
                df = self._vol_indicator.compute(df, n=self._config.ma_period)
                if 'volatility' in df.columns:
                    data.bar_data[code] = df  # 原地更新，确保 Resolver 能读到 volatility

            for period_idx, indicator in enumerate(self._roc_indicators):
                period = periods[period_idx]
                df = indicator.compute(df, n=period, m=self._config.roc_m)

            latest = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else latest

            ind_info = {
                'close': float(latest.get('close', 0) or 0),
                'ma': float(latest.get(f'ma{self._config.ma_period}', 0) or 0),
            }

            if self._vol_indicator:
                vol = latest.get('volatility')
                ind_info['volatility'] = float(vol) if vol is not None and not np.isnan(float(vol)) else 0.0

            # 收集每个周期的ROC值
            for period in periods:
                roc_val = latest.get('roc')
                if roc_val is not None and not np.isnan(float(roc_val)):
                    period_roc[period][code] = float(roc_val)

            # 使用主周期（权重最大的周期）的值作为 evaluate 输入
            primary_period = periods[weights.index(max(weights))]
            primary_roc = float(latest.get('roc', 0) or 0)
            maroc_val = float(latest.get('maroc', 0) or 0)
            ind_info.update({
                'roc': primary_roc, 'maroc': maroc_val,
                'prev_roc': float(prev.get('roc', 0) or 0),
                'prev_maroc': float(prev.get('maroc', 0) or 0),
            })

            if self._config.rsi_enhance_enabled:
                rsi_val_latest = latest.get('rsi')
                if rsi_val_latest is not None:
                    ind_info['rsi'] = float(rsi_val_latest)

            indicator_data[code] = ind_info

        # 每个周期独立排名，分配分数（rank 1 = 最高 ROC = 100分，递减）
        scores = {}
        for p, roc_dict in period_roc.items():
            sorted_codes = sorted(roc_dict.items(), key=lambda x: x[1], reverse=True)
            for rank, (code, _) in enumerate(sorted_codes):
                if code not in scores:
                    scores[code] = 0.0
                scores[code] += weights[periods.index(p)] * (100.0 - rank)

        # 按总分排序
        sorted_codes = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        decisions = []
        for priority, (code, total_score) in enumerate(sorted_codes):
            ind = indicator_data.get(code, {})
            direction, weight, reason = self._evaluate(code, ind.get('roc', 0), ind, positions)
            reason = f"[多因子 score={total_score:.0f}] {reason}"

            decisions.append(Decision(
                decision_type=DecisionType.ROTATION,
                timestamp=datetime.now(),
                reason=reason,
                target_code=code,
                direction=direction,
                weight=weight,
                priority=priority,
                confidence=min(total_score / 100.0, 1.0),
                strategy_name=self.name,
                indicator_values={'roc': ind.get('roc', 0), 'maroc': ind.get('maroc', 0),
                                   'score': total_score},
            ))

        return decisions