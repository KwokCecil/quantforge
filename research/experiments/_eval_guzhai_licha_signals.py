"""股债利差信号改进方案对比评估 v2。

基线 = 与生产代码 GuzhaiLichaCalculator.compute() 完全一致：
  - 信号: ratio_double ≤ 15% OR ratio_single ≤ 40% → 冲锋
          ratio_double ≥ 92% OR ratio_single ≥ 92% → 撤退
  - 标的: 510300 + 159915 各 50%, 触发再平衡
  - 扩展窗口分位

改进方案: 保持 50/50 标的不变, 只改信号计算方法。

# @layer: research
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd, numpy as np
from quantforge.data_sources.sina_feed import SinaFinanceFeed
from quantforge.core.data_feed import DataRequest
from datetime import datetime

CSV = os.path.join(os.path.dirname(__file__), "..", "data", "guzhai_licha.csv")
START = "2018-01-01"
WINDOW_YEARS = 5

# 基线阈值 (与 guzhai_licha.py 一致)
FALLBACK_DOUBLE = 0.92
FORWARD_DOUBLE = 0.15
FALLBACK_SINGLE = 0.92
FORWARD_SINGLE = 0.40

# 改进方案阈值
CHARGE_PCT = 0.15
RETREAT_PCT = 0.92
Z_CHARGE = 1.5
Z_RETREAT = -0.5

CODES = ["510300", "159915"]

# ============ 数据加载 ============
def load_raw() -> pd.DataFrame:
    df = pd.read_csv(CSV, parse_dates=["date"])
    df = df[(df["date"] >= START)]
    return df.sort_values("date").reset_index(drop=True)


# ============ 信号计算 ============
def make_signals(df: pd.DataFrame, method: str) -> pd.DataFrame:
    """给 df 添加 signal_charge / signal_retreat"""
    n = len(df)

    if method == "baseline":
        # === 与 GuzhaiLichaCalculator.compute() 完全一致 ===
        signals = []
        for i in range(n):
            hist = df.iloc[:i+1]
            if len(hist) < 252:
                signals.append((False, False))
                continue
            ratio_d = (hist["double_ttm_licha_pct"].values >= hist.iloc[-1]["double_ttm_licha_pct"]).sum() / len(hist)
            ratio_s = (hist["single_static_licha_pct"].values >= hist.iloc[-1]["single_static_licha_pct"]).sum() / len(hist)
            charge = (ratio_d <= FORWARD_DOUBLE or ratio_s <= FORWARD_SINGLE)
            retreat = (ratio_d >= FALLBACK_DOUBLE or ratio_s >= FALLBACK_SINGLE)
            signals.append((charge, retreat))
        df["signal_charge"] = [s[0] for s in signals]
        df["signal_retreat"] = [s[1] for s in signals]

    elif method == "baseline_tiered":
        # 信心分层: 强信号(双倍+单倍)全仓, 弱信号(仅单倍)半仓
        signals = []
        for i in range(n):
            hist = df.iloc[:i+1]
            if len(hist) < 252:
                signals.append((False, False, False))
                continue
            ratio_d = (hist["double_ttm_licha_pct"].values >= hist.iloc[-1]["double_ttm_licha_pct"]).sum() / len(hist)
            ratio_s = (hist["single_static_licha_pct"].values >= hist.iloc[-1]["single_static_licha_pct"]).sum() / len(hist)
            charge = (ratio_d <= FORWARD_DOUBLE or ratio_s <= FORWARD_SINGLE)
            retreat = (ratio_d >= FALLBACK_DOUBLE or ratio_s >= FALLBACK_SINGLE)
            charge_strong = (ratio_d <= FORWARD_DOUBLE and ratio_s <= FORWARD_SINGLE)
            signals.append((charge, retreat, charge_strong))
        df["signal_charge"] = [s[0] for s in signals]
        df["signal_retreat"] = [s[1] for s in signals]
        df["charge_strong"] = [s[2] for s in signals]

    elif method == "rolling":
        # 利差 5年滚动窗口 reverse_ratio (仅双倍)
        rr = []
        for i in range(n):
            cutoff = df["date"].iloc[i] - pd.DateOffset(years=WINDOW_YEARS)
            window = df[(df["date"] >= cutoff) & (df["date"] <= df["date"].iloc[i])]
            if len(window) < 100:
                rr.append(0.5)
            else:
                cur = df["double_ttm_licha_pct"].iloc[i]
                vals = window["double_ttm_licha_pct"].values
                rr.append((vals >= cur).sum() / len(vals))
        df["signal_charge"] = [r <= CHARGE_PCT for r in rr]
        df["signal_retreat"] = [r >= RETREAT_PCT for r in rr]

    elif method == "ep_only":
        # E/P 扩展窗口 reverse_ratio + 国债辅助
        ep_rr = []; bond_rr = []
        for i in range(n):
            hist_ep = 1.0 / df["pe_ttm"].iloc[:i+1].values
            ep = 1.0 / df["pe_ttm"].iloc[i]
            ep_rr.append((hist_ep >= ep).sum() / len(hist_ep))
            hist_b = df["bond_10y"].iloc[:i+1].values
            b = df["bond_10y"].iloc[i]
            bond_rr.append((hist_b >= b).sum() / len(hist_b))
        df["signal_charge"] = [ep_rr[i] <= CHARGE_PCT and bond_rr[i] > 0.2 for i in range(n)]
        df["signal_retreat"] = [ep_rr[i] >= RETREAT_PCT for i in range(n)]

    elif method == "zscore":
        zs = []
        for i in range(n):
            cutoff = df["date"].iloc[i] - pd.DateOffset(years=WINDOW_YEARS)
            window = df[(df["date"] >= cutoff) & (df["date"] <= df["date"].iloc[i])]
            if len(window) < 100:
                zs.append(0.0)
            else:
                m = window["double_ttm_licha_pct"].mean()
                s = window["double_ttm_licha_pct"].std()
                zs.append((df["double_ttm_licha_pct"].iloc[i] - m) / s if s > 0 else 0.0)
        df["signal_charge"] = [z > Z_CHARGE for z in zs]
        df["signal_retreat"] = [z < Z_RETREAT for z in zs]

    elif method == "dynamic":
        # 动态分层: 与 baseline_tiered 相同的信号计算, 但用于动态仓位
        signals = []
        for i in range(n):
            hist = df.iloc[:i+1]
            if len(hist) < 252:
                signals.append((False, False, False))
                continue
            ratio_d = (hist["double_ttm_licha_pct"].values >= hist.iloc[-1]["double_ttm_licha_pct"]).sum() / len(hist)
            ratio_s = (hist["single_static_licha_pct"].values >= hist.iloc[-1]["single_static_licha_pct"]).sum() / len(hist)
            charge = (ratio_d <= FORWARD_DOUBLE or ratio_s <= FORWARD_SINGLE)
            retreat = (ratio_d >= FALLBACK_DOUBLE or ratio_s >= FALLBACK_SINGLE)
            charge_strong = (ratio_d <= FORWARD_DOUBLE and ratio_s <= FORWARD_SINGLE)
            signals.append((charge, retreat, charge_strong))
        df["signal_charge"] = [s[0] for s in signals]
        df["signal_retreat"] = [s[1] for s in signals]
        df["charge_strong"] = [s[2] for s in signals]

    return df


# ============ 回测: 50/50 双标的 + 再平衡 ============
def backtest_5050(sig_df: pd.DataFrame, label: str) -> dict:
    """50/50 策略: 信号择时进出, 偏离33%触发再平衡"""
    feed = SinaFinanceFeed()
    req = DataRequest(codes=CODES, data_type="daily_k",
                      start=sig_df["date"].iloc[0].strftime("%Y-%m-%d"),
                      end=datetime.today().strftime("%Y-%m-%d"))
    resp = feed.get_data(req)
    px = {c: resp.bar_data[c].set_index("date")["close"] for c in CODES}
    px_str = {c: {str(k)[:10]: v for k, v in px[c].to_dict().items()} for c in CODES}

    # 取两个标的重叠日期
    dates = sorted(set(px[CODES[0]].index.tolist()) & set(px[CODES[1]].index.tolist()))
    dates = [d for d in dates if str(d)[:10] >= START]

    sig_map = {}
    has_tier = "charge_strong" in sig_df.columns
    for s, (_, r) in zip(sig_df["date"], sig_df.iterrows()):
        key = s.strftime("%Y-%m-%d")
        # 基线始终全仓, 分层方案才读 charge_strong
        strong = r["charge_strong"] if has_tier else True
        sig_map[key] = (r["signal_charge"], r["signal_retreat"], strong)

    cash = 40000.0
    shares = {c: 0 for c in CODES}
    in_mkt = False
    daily_vals = []
    trades = []

    for d in dates:
        ds = str(d)[:10]
        ch, re, strong = sig_map.get(ds, (False, False, True))

        # 交易信号
        if ch and not in_mkt:
            # 买入: 强信号全仓, 弱信号半仓
            buy_cash = cash if strong else cash * 0.5
            half = buy_cash / 2
            for c in CODES:
                p = px_str[c].get(ds)
                if p and p > 0:
                    shares[c] = int(half / p / 100) * 100
                    cash -= shares[c] * p
            in_mkt = True
            label = "BUY_FULL" if strong else "BUY_HALF"
            trades.append((ds, label))

        elif re and in_mkt:
            # 卖出: 全部清仓
            for c in CODES:
                p = px_str[c].get(ds)
                if p:
                    cash += shares[c] * p
            shares = {c: 0 for c in CODES}
            in_mkt = False
            trades.append((ds, "SELL"))

        elif in_mkt:
            # 再平衡: 任一标的 < 33% 总市值
            mv = {c: shares[c] * px_str[c].get(ds, 0) for c in CODES}
            total = cash + sum(mv.values())
            if total > 0:
                for c in CODES:
                    if total > 0 and mv[c] / total < 0.33:
                        # 再平衡到 50/50
                        target = total * 0.5
                        p = px_str[c].get(ds)
                        if p and p > 0:
                            diff = int((target - mv[c]) / p / 100) * 100
                            shares[c] += diff
                            cash -= diff * p
                        other = CODES[1] if c == CODES[0] else CODES[0]
                        po = px_str[other].get(ds)
                        if po and po > 0:
                            diff2 = int((total * 0.5 - mv[other]) / po / 100) * 100
                            shares[other] += diff2
                            cash -= diff2 * po
                        trades.append((ds, f"REBAL_{c}"))
                        break  # 一次只处理一个方向

        # 日末市值
        mv = {c: shares[c] * px_str[c].get(ds, 0) for c in CODES}
        daily_vals.append({"date": ds, "val": cash + sum(mv.values()), "in_mkt": in_mkt})

    # 最终清算
    final_val = daily_vals[-1]["val"] if daily_vals else 40000

    ret = (final_val - 40000) / 40000
    dv = pd.DataFrame(daily_vals)
    dv["r"] = dv["val"].pct_change()
    sharpe = np.sqrt(252) * dv["r"].mean() / dv["r"].std() if dv["r"].std() > 0 else 0
    max_dd = ((dv["val"] - dv["val"].cummax()) / dv["val"].cummax()).min()

    mk_days = sum(1 for d in daily_vals if d["in_mkt"])

    return {
        "return": ret, "sharpe": sharpe, "max_dd": max_dd,
        "final": final_val, "market_days": mk_days,
        "charge_days": sig_df["signal_charge"].sum(),
        "retreat_days": sig_df["signal_retreat"].sum(),
        "trade_count": len(trades),
        "trades": trades,
    }


# ============ 回测: 动态分层仓位 ============
CONFIRM_DAYS = 5  # 信号确认天数: 新权重需连续N天才触发调仓

def backtest_dynamic(sig_df: pd.DataFrame, label: str) -> dict:
    """动态分层: 撤退→0%, 强→100%, 弱→80%, 中性→50%.
    回测起点视为空仓(刚撤退完), 仅强信号可首次入场.
    信号确认: 权重变化需连续CONFIRM_DAYS才执行, 减少调仓损耗."""

    feed = SinaFinanceFeed()
    req = DataRequest(codes=CODES, data_type="daily_k",
                      start=sig_df["date"].iloc[0].strftime("%Y-%m-%d"),
                      end=datetime.today().strftime("%Y-%m-%d"))
    resp = feed.get_data(req)
    px = {c: resp.bar_data[c].set_index("date")["close"] for c in CODES}
    px_str = {c: {str(k)[:10]: v for k, v in px[c].to_dict().items()} for c in CODES}
    all_dates = sorted(set(px[CODES[0]].index.tolist()) & set(px[CODES[1]].index.tolist()))
    all_dates = [d for d in all_dates if str(d)[:10] >= START]

    # 构建每日目标权重
    has_tier = "charge_strong" in sig_df.columns
    wt_map = {}
    for s, (_, r) in zip(sig_df["date"], sig_df.iterrows()):
        key = s.strftime("%Y-%m-%d")
        if r["signal_retreat"]:
            wt_map[key] = 0.0
        elif has_tier:
            if r["charge_strong"]:
                wt_map[key] = 1.0
            elif r["signal_charge"]:
                wt_map[key] = 0.8
            else:
                wt_map[key] = 0.5
        else:
            wt_map[key] = 1.0 if r["signal_charge"] else 0.5

    # 回测起点视为刚撤退完 → 必须强信号才能入场
    cash = 40000.0
    shares = {c: 0 for c in CODES}
    target_w = 0.0
    was_cleared = True  # <-- 修复: 起点视同刚撤退
    daily_vals = []
    trades = []

    # 确认计数器: 追踪当前候选权重已连续出现的天数
    confirm_count = 0
    candidate_w = 0.0

    for d in all_dates:
        ds = str(d)[:10]
        raw_w = wt_map.get(ds, 0.5)

        # 撤退后只有强信号才能重入
        if was_cleared and raw_w < 1.0:
            raw_w = 0.0

        # === 确认机制: 只有连续 CONFIRM_DAYS 同一权重才触发调仓 ===
        if abs(raw_w - candidate_w) < 0.01:
            confirm_count += 1
        else:
            candidate_w = raw_w
            confirm_count = 1

        # 确认通过 → 更新目标权重
        if confirm_count >= CONFIRM_DAYS:
            new_w = candidate_w
            confirm_count = CONFIRM_DAYS  # 防溢出
        else:
            new_w = target_w  # 未确认 → 保持

        # 更新 cleared 状态
        if new_w == 0.0:
            was_cleared = True
        elif new_w == 1.0:
            was_cleared = False

        # 权重变化 → 调仓
        if abs(new_w - target_w) > 0.01 or (new_w == 0.0 and target_w > 0):
            mv = {c: shares[c] * px_str[c].get(ds, 0) for c in CODES}
            total = cash + sum(mv.values())
            target_mv = total * new_w

            for c in CODES:
                p = px_str[c].get(ds, 0)
                if p > 0:
                    cash += shares[c] * p
                    shares[c] = 0

            if new_w > 0:
                half = target_mv / 2
                for c in CODES:
                    p = px_str[c].get(ds)
                    if p and p > 0:
                        shares[c] = int(half / p / 100) * 100
                        cash -= shares[c] * p

            tag = {0.0: "CLEAR", 0.5: "NEUTRAL", 0.8: "WEAK", 1.0: "FULL"}.get(new_w, "?")
            trades.append((ds, tag))
            target_w = new_w

        mv = {c: shares[c] * px_str[c].get(ds, 0) for c in CODES}
        daily_vals.append({"date": ds, "val": cash + sum(mv.values()), "in_mkt": target_w > 0,
                           "weight": target_w})

    final_val = daily_vals[-1]["val"] if daily_vals else 40000
    ret = (final_val - 40000) / 40000
    dv = pd.DataFrame(daily_vals)
    dv["r"] = dv["val"].pct_change()
    sharpe = np.sqrt(252) * dv["r"].mean() / dv["r"].std() if dv["r"].std() > 0 else 0
    max_dd = ((dv["val"] - dv["val"].cummax()) / dv["val"].cummax()).min()
    mk_days = sum(1 for d in daily_vals if d["in_mkt"])

    return {
        "return": ret, "sharpe": sharpe, "max_dd": max_dd,
        "final": final_val, "market_days": mk_days,
        "charge_days": sig_df["signal_charge"].sum(),
        "retreat_days": sig_df["signal_retreat"].sum(),
        "trade_count": len(trades),
        "trades": trades,
    }


# ============ 主对比 ============
def main():
    df = load_raw()
    print(f"数据: {len(df)} 天, {df.date.min().date()} ~ {df.date.max().date()}")
    print(f"PE范围: {df.pe_ttm.min():.1f}~{df.pe_ttm.max():.1f}, 国债: {df.bond_10y.min():.2f}~{df.bond_10y.max():.2f}%")
    print(f"标的: {CODES[0]}+{CODES[1]} 各50%, 偏离33%再平衡\n")

    methods = ["baseline", "dynamic", "rolling", "ep_only", "zscore"]
    labels = {
        "baseline": "基线(全仓进出)",
        "dynamic":  "动态分层(0/50/80/100%)",
        "rolling":  "方案1 滚动5y分位",
        "ep_only":  "方案2 E/P独立分位",
        "zscore":   "方案3 Z-Score",
    }
    results = {}

    for method in methods:
        sdf = make_signals(df.copy(), method)
        if method == "dynamic":
            r = backtest_dynamic(sdf, method)
        else:
            r = backtest_5050(sdf, method)
        results[method] = r

    # ======== 买入持有基准 (50/50 再平衡) ========
    feed = SinaFinanceFeed()
    req = DataRequest(codes=CODES, data_type="daily_k", start=START, end=datetime.today().strftime("%Y-%m-%d"))
    resp = feed.get_data(req)
    px = {c: resp.bar_data[c].set_index("date")["close"] for c in CODES}
    dates = sorted(set(px[CODES[0]].index.tolist()) & set(px[CODES[1]].index.tolist()))
    dates = [d for d in dates if str(d)[:10] >= START]

    # 按日期字符串访问
    px_str = {c: {str(k)[:10]: v for k, v in px[c].to_dict().items()} for c in CODES}
    shares_bh = {c: 20000 / px_str[c].get(str(dates[0])[:10], 1) for c in CODES}
    for c in CODES:
        shares_bh[c] = int(shares_bh[c] / 100) * 100
    cash_bh = 40000 - sum(shares_bh[c] * px_str[c].get(str(dates[0])[:10], 1) for c in CODES)
    bh_vals = []
    for d in dates:
        ds = str(d)[:10]
        mv = sum(shares_bh[c] * px_str[c].get(ds, 0) for c in CODES)
        bh_vals.append(cash_bh + mv)

    bh_ret = (bh_vals[-1] - 40000) / 40000
    bh_s = pd.Series(bh_vals).pct_change().dropna()
    bh_sharpe = np.sqrt(252) * bh_s.mean() / bh_s.std() if bh_s.std() > 0 else 0
    bh_dd = ((pd.Series(bh_vals) - pd.Series(bh_vals).cummax()) / pd.Series(bh_vals).cummax()).min()

    # ======== 输出 ========
    print("=" * 95)
    print(f"  股债利差信号改进对比 × 50/50 {CODES[0]}+{CODES[1]} ({START[:4]} ~ 今)")
    print("=" * 95)
    header = f"{'指标':<22}"
    for m in methods:
        header += f" {labels[m]:>18}"
    header += f" {'买入持有':>12}"
    print(header)
    print("-" * 95)

    for metric, fmt, name in [
        ("return", ".1%", "总收益率"),
        ("sharpe", ".2f", "Sharpe"),
        ("max_dd", ".1%", "最大回撤"),
        ("final", ".0f", "最终资金"),
    ]:
        line = f"{name:<22}"
        for m in methods:
            v = results[m][metric]
            line += f" {v:{fmt}}".rjust(19)
        if metric == "return":
            line += f" {bh_ret:{fmt}}".rjust(13)
        elif metric == "sharpe":
            line += f" {bh_sharpe:{fmt}}".rjust(13)
        elif metric == "max_dd":
            line += f" {bh_dd:{fmt}}".rjust(13)
        else:
            line += f" {40000*(1+bh_ret):{fmt}}".rjust(13)
        print(line)

    print(f"{'持仓天数':<22}", end="")
    for m in methods:
        print(f" {results[m]['market_days']:>18}", end="")
    print()

    print(f"{'冲锋天数(信号)':<22}", end="")
    for m in methods:
        print(f" {results[m]['charge_days']:>18}", end="")
    print()

    print(f"{'撤退天数(信号)':<22}", end="")
    for m in methods:
        print(f" {results[m]['retreat_days']:>18}", end="")
    print()

    print(f"{'交易次数':<22}", end="")
    for m in methods:
        print(f" {results[m]['trade_count']:>18}", end="")
    print()

    # ======== 交易日志 ========
    print(f"\n--- 基线交易日志 ---")
    for t in results["baseline"]["trades"]:
        print(f"  {t[0]}  {t[1]}")

    print(f"\n--- 动态分层调仓日志 ---")
    for t in results["dynamic"]["trades"]:
        print(f"  {t[0]}  {t[1]}")

    # ======== 2024-09 ========
    print(f"\n--- 2024-09 大行情期间 (信号) ---")
    for method in methods:
        sdf = make_signals(df.copy(), method)
        sep_oct = sdf[(sdf["date"] >= "2024-09-01") & (sdf["date"] <= "2024-10-15")]
        ch = sum(sep_oct["signal_charge"])
        re = sum(sep_oct["signal_retreat"])
        # 检查是否9月前已入场
        pre = sdf[(sdf["date"] < "2024-09-01") & sdf["signal_charge"]]
        last_retreat = sdf[(sdf["date"] < "2024-09-01") & sdf["signal_retreat"]]
        held = len(pre) > 0 and (len(last_retreat) == 0 or pre["date"].max() > last_retreat["date"].max())
        print(f"  {labels[method]:<30} 冲锋{ch}天 撤退{re}天  {'已持仓' if held else '未持仓'}")

    # ======== 当前 ========
    print(f"\n--- 当前最新信号 ({df.date.iloc[-1].date()}) ---")
    for method in methods:
        sdf = make_signals(df.copy(), method)
        r = sdf.iloc[-1]
        st = "冲锋" if r["signal_charge"] else ("撤退" if r["signal_retreat"] else "中性")
        print(f"  {labels[method]:<30} {st}  PE={r['pe_ttm']:.1f}  国债={r['bond_10y']:.2f}%")

    # ======== 语义 ========
    print(f"\n--- 冲锋日语义 ---")
    for method in methods:
        sdf = make_signals(df.copy(), method)
        chd = sdf[sdf["signal_charge"]]
        if len(chd) > 0:
            print(f"  {labels[method]}: PE中位数={chd['pe_ttm'].median():.1f}  国债中位数={chd['bond_10y'].median():.2f}%  利差中位数={chd['double_ttm_licha_pct'].median():.1f}%")

    print("\n" + "=" * 95)


if __name__ == "__main__":
    main()