"""
特性墓地 — RSI 单因子替代模块。

原位置：roc_momentum.py L798-L862, L1057-L1090
证伪依据：5.08§一, T025
证伪原因：以 RSI(14) 替代 ROC(22) 作为排名和信号因子，全周期无正向贡献
包含方法：_produce_rsifactor_decisions, _evaluate_rsi
"""

# ============================================================
# 原代码（不可独立运行，依赖 ROCStrategy 基础设施）
# 包装为伪类以通过语法检查，仅为存档参考
# ============================================================

class _DeadCode:
    # ============================================================
    # RSI 单因子替代
    # ============================================================

    def _produce_rsifactor_decisions(self, data: DataResponse,
                                      positions: dict[str, Any]) -> list[Decision]:
        """RSI单因子模式：用 RSI(14) 替代 ROC(22) 作为排名和信号因子。
        ⚠️ 已证伪(5.08§一, T025)：全周期无正向贡献。"""
        cfg = self._config
        decisions = []
        rsi_values = {}
        indicator_data = {}

        for code, df in data.bar_data.items():
            if df.empty or len(df) < cfg.EMPTY_DAY:
                continue

            df = self._rsi_indicator.compute(df, n=cfg.rsi_period)
            df = self._ma_indicator.compute(df, periods=[cfg.ma_period])

            if self._vol_indicator:
                df = self._vol_indicator.compute(df, n=cfg.ma_period)
                if 'volatility' in df.columns:
                    data.bar_data[code] = df

            latest = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else latest

            try:
                rsi_val = float(latest.get('rsi', np.nan))
                close_val = float(latest.get('close', 0) or 0)
                ma_val = float(latest.get(f'ma{cfg.ma_period}', 0) or 0)
                prev_rsi = float(prev.get('rsi', 0) or 0)
            except (ValueError, TypeError):
                continue

            if np.isnan(rsi_val):
                continue

            rsi_values[code] = rsi_val
            indicator_data[code] = {
                'rsi': rsi_val, 'prev_rsi': prev_rsi,
                'close': close_val, 'ma': ma_val,
            }

        sorted_codes = sorted(rsi_values.items(), key=lambda x: x[1], reverse=True)

        for priority, (code, rsi_val) in enumerate(sorted_codes):
            ind = indicator_data.get(code, {})
            direction, weight, reason = self._evaluate_rsi(code, rsi_val, ind, positions)

            decisions.append(Decision(
                decision_type=DecisionType.ROTATION,
                timestamp=datetime.now(),
                reason=reason,
                target_code=code,
                direction=direction,
                weight=weight,
                priority=priority,
                confidence=min(rsi_val / 100.0, 1.0),
                strategy_name=self.name,
                indicator_values={'rsi': rsi_val},
            ))

        return decisions

    def _evaluate_rsi(self, code: str, rsi_val: float, ind: dict,
                       positions: dict[str, Any]) -> tuple[str, float, str]:
        """RSI模式的买卖评估。RSI>=买入阈值→enter，RSI<=卖出阈值→exit。

        注意：rsi_enhance_enabled 开启时会在 RSI 过高时禁止买入，与 RSI 买入阈值
        (rsi_factor_buy) 形成双重过滤——只有 RSI 在 [rsi_factor_buy, rsi_enhance_below)
        区间才允许买入。
        """
        cfg = self._config

        if code in positions and rsi_val <= cfg.rsi_factor_sell:
            return 'exit', 0.0, f"RSI={rsi_val:.1f} <= {cfg.rsi_factor_sell}"

        if rsi_val >= cfg.rsi_factor_buy:
            buy_ok = True
            reasons = [f"RSI={rsi_val:.1f}>={cfg.rsi_factor_buy}"]

            if cfg.MA_PRICE_CROSS:
                if ind.get('close', 0) <= ind.get('ma', 0):
                    buy_ok = False
                    reasons.append("均线穿越:价格<均线")

            if buy_ok and self._config.rsi_enhance_enabled:
                if rsi_val >= self._config.rsi_enhance_below:
                    buy_ok = False
                    reasons.append(f"RSI={rsi_val:.1f}>={self._config.rsi_enhance_below} 禁止买入")

            if buy_ok:
                weight = rsi_val
                return 'enter', weight, '; '.join(reasons)
            else:
                return 'hold', 0.0, '; '.join(reasons)

        return 'hold', 0.0, f"RSI={rsi_val:.1f} 在观望区间"