"""
特性墓地 — 残差动量模块。

原位置：roc_momentum.py L864-L994
证伪依据：5.08§一, T025
证伪原因：ETF层残差信息量不足（剥离市场beta后的特质收益），全周期无效
包含方法：_produce_residual_decisions, _compute_residual_momentum
"""

# ============================================================
# 原代码（不可独立运行，依赖 ROCStrategy._evaluate 等共享方法）
# 包装为伪类以通过语法检查，仅为存档参考
# ============================================================

class _DeadCode:
    def _produce_residual_decisions(self, data: DataResponse,
                                      positions: dict[str, Any]) -> list[Decision]:
        """残差动量模式：用剥离市场beta后的特质收益替代ROC排名。信号仍用ROC确认。
        ⚠️ 已证伪(5.08§一, T025)：ETF层残差信息量不足，全周期无效。"""
        cfg = self._config
        decisions = []
        res_values = {}
        indicator_data = {}
        bm_code = cfg.benchmark_code

        bm_df = data.bar_data.get(bm_code, pd.DataFrame())

        for code, df in data.bar_data.items():
            if code == bm_code or df.empty or len(df) < max(cfg.residual_window, cfg.EMPTY_DAY):
                continue

            df = self._roc_indicator.compute(df, n=cfg.roc_n, m=cfg.roc_m)
            df = self._ma_indicator.compute(df, periods=[cfg.ma_period])

            if self._vol_indicator:
                df = self._vol_indicator.compute(df, n=cfg.ma_period)
                if 'volatility' in df.columns:
                    data.bar_data[code] = df

            res_mom = self._compute_residual_momentum(df, bm_df)
            if np.isnan(res_mom):
                continue

            latest = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else latest
            try:
                roc_val = float(latest.get('roc', np.nan))
                maroc_val = float(latest.get('maroc', np.nan))
                close_val = float(latest.get('close', 0) or 0)
                ma_val = float(latest.get(f'ma{cfg.ma_period}', 0) or 0)
                prev_roc = float(prev.get('roc', 0) or 0)
                prev_maroc = float(prev.get('maroc', 0) or 0)
            except (ValueError, TypeError):
                continue

            if np.isnan(roc_val):
                continue

            res_values[code] = res_mom
            indicator_data[code] = {
                'roc': roc_val, 'maroc': maroc_val,
                'close': close_val, 'ma': ma_val, 'prev_roc': prev_roc, 'prev_maroc': prev_maroc,
                'residual_momentum': res_mom, 'crash_active': False, 'crash_reason': '',
            }

            if cfg.ts_momentum_enabled:
                p = cfg.ts_momentum_period
                ref_idx = max(0, len(df) - 1 - p)
                if len(df) > p:
                    ref_close = float(df.iloc[ref_idx].get('close', 0) or 0)
                    ts_ret = (close_val / ref_close - 1) if ref_close > 0 else np.nan
                else:
                    ts_ret = np.nan
                indicator_data[code]['ts_return'] = ts_ret

        sorted_codes = sorted(res_values.items(), key=lambda x: x[1], reverse=True)

        for priority, (code, res_mom) in enumerate(sorted_codes):
            ind = indicator_data.get(code, {})
            roc_val = ind.get('roc', 0.0)
            direction, weight, reason = self._evaluate(code, roc_val, ind, positions)

            decisions.append(Decision(
                decision_type=DecisionType.ROTATION,
                timestamp=datetime.now(),
                reason=f"[ResMOM={res_mom:.3f}] {reason}",
                target_code=code,
                direction=direction,
                weight=weight,
                priority=priority,
                confidence=min(abs(res_mom * 100) / 50.0, 1.0),
                strategy_name=self.name,
                indicator_values={'residual_momentum': res_mom, 'roc': roc_val},
            ))

        return decisions

    def _compute_residual_momentum(self, stock_df: pd.DataFrame,
                                     bm_df: pd.DataFrame) -> float:
        """OLS回归：ret_stock = α + β × ret_benchmark + ε。
        残差动量 = 最近 N 日的累积残差收益。"""
        cfg = self._config

        stock = stock_df.copy()
        stock['date'] = pd.to_datetime(stock['date'])
        stock = stock.set_index('date').sort_index()
        stock['ret'] = stock['close'].pct_change()

        bm = bm_df.copy()
        if bm.empty:
            return np.nan
        bm['date'] = pd.to_datetime(bm['date'])
        bm = bm.set_index('date').sort_index()
        bm['ret'] = bm['close'].pct_change()

        common = stock.index.intersection(bm.index)
        if len(common) < 60:
            return np.nan

        s_ret = stock.loc[common, 'ret'].dropna().values.astype(float)
        b_ret = bm.loc[common, 'ret'].dropna().values.astype(float)
        min_len = min(len(s_ret), len(b_ret))
        s_ret = s_ret[-min_len:]
        b_ret = b_ret[-min_len:]

        w = min(cfg.residual_window, len(s_ret))
        if w < 30:
            return np.nan

        y_w = s_ret[-w:]
        X_w = b_ret[-w:]

        X_mat = np.column_stack([np.ones(len(X_w)), X_w])
        try:
            coeffs, _, _, _ = np.linalg.lstsq(X_mat, y_w, rcond=None)
            alpha, beta = coeffs[0], coeffs[1]
        except np.linalg.LinAlgError:
            return np.nan

        residuals = s_ret - (alpha + beta * b_ret)

        rp = min(cfg.residual_rank_period, len(residuals))
        res_window = residuals[-rp:]
        cum_res = np.prod(1 + res_window) - 1

        return cum_res