"""
特性墓地 — 时序动量过滤 + 崩盘防护模块。

原位置：roc_momentum.py
  - ts_momentum inline:  L287-L296, L1189-L1192
  - crash_protection:    L319-L327, L1194-L1197, L584-L617
证伪依据：
  - ts_momentum: 5.08§一, T025（熊市帮倒忙，2022年底部拒买）
  - crash_protection: T025（未经实证，参数来自T021论文参考，未针对A股优化）
包含方法：_check_crash_protection
"""

# ============================================================
# 原代码（不可独立运行，依赖 ROCStrategy 基础设施）
# 包装为伪类以通过语法检查，仅为存档参考
# ============================================================

class _DeadCode:
    # ---- ts_momentum ----
    # 在 _produce_singlefactor_decisions 中（数据收集阶段）：
    # ⚠️ 已证伪(5.08§一, T025)：熊市帮倒忙，2022年底部拒买
    # if self._config.ts_momentum_enabled:
    #     p = self._config.ts_momentum_period
    #     ref_idx = max(0, len(df) - 1 - p)
    #     if len(df) > p:
    #         ref_close = float(df.iloc[ref_idx].get('close', 0) or 0)
    #         ts_ret = (close_val / ref_close - 1) if ref_close > 0 else np.nan
    #     else:
    #         ts_ret = np.nan
    #     indicator_data[code]['ts_return'] = ts_ret

    # 在 _evaluate 中（决策阶段）：
    # if self._config.ts_momentum_enabled:
    #     ts_ret = ind.get('ts_return', np.nan)
    #     if not np.isnan(ts_ret) and ts_ret < self._config.ts_momentum_min_return:
    #         return 'hold', 0.0, f"TS#{...}={ts_ret:.1%}<{...}"

    # ---- crash_protection ----
    # 在 _produce_singlefactor_decisions 中（数据收集阶段）：
    # crash_active = False
    # crash_reason = ""
    # if self._config.crash_protection_enabled and self._config.benchmark_code in data.bar_data:
    #     crash_active, crash_reason = self._check_crash_protection(
    #         data.bar_data[self._config.benchmark_code]
    #     )
    # for code in indicator_data:
    #     indicator_data[code]['crash_active'] = crash_active
    #     indicator_data[code]['crash_reason'] = crash_reason

    # 在 _evaluate 中（决策阶段）：
    # if self._config.crash_protection_enabled:
    #     crash_active = ind.get('crash_active', False)
    #     if crash_active:
    #         return 'hold', 0.0, ind.get('crash_reason', 'CrashProtection')

    def _check_crash_protection(self, bm_df: pd.DataFrame) -> tuple[bool, str]:
        """动量崩盘防护。检查 benchmark 是否处于高波动+大跌状态。
        ⚠️ 未经实证(5.08§一, T025)：未在任何递进测试中体现改善。参数(60天窗口/1.5倍阈值/-15%回撤)来自T021论文参考，未针对A股优化。
        返回 (crash_active, reason)"""
        cfg = self._config
        if bm_df.empty or len(bm_df) < max(cfg.cp_drawdown_window, 252):
            return False, ""

        bm = bm_df.copy()
        bm['date'] = pd.to_datetime(bm['date'])
        bm = bm.set_index('date').sort_index()
        bm['close'] = pd.to_numeric(bm['close'], errors='coerce')
        bm = bm.dropna(subset=['close'])

        if len(bm) < 60:
            return False, ""

        returns = bm['close'].pct_change().dropna()
        cur_vol = returns.tail(cfg.cp_vol_window).std() * np.sqrt(252)
        hist_vol_median = returns.rolling(252).std().median() * np.sqrt(252)

        dd_window = bm['close'].tail(cfg.cp_drawdown_window)
        cur_dd = dd_window.iloc[-1] / dd_window.max() - 1.0

        vol_spike = cur_vol > hist_vol_median * cfg.cp_vol_spike_threshold if hist_vol_median > 0 else False
        market_dd = cur_dd < cfg.cp_market_dd_threshold

        if vol_spike and market_dd:
            return True, (
                f"CrashProtection: vol={cur_vol:.1%}>{hist_vol_median:.1%}*{cfg.cp_vol_spike_threshold} "
                f"dd={cur_dd:.1%}<{cfg.cp_market_dd_threshold}"
            )

        return False, ""