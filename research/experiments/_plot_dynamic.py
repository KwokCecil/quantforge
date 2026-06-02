"""股债利差 基线 vs 动态分层 走势图"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd, numpy as np
from quantforge.data_sources.sina_feed import SinaFinanceFeed
from quantforge.core.data_feed import DataRequest
from datetime import datetime

plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei']
plt.rcParams['axes.unicode_minus'] = False

START, CODES = "2018-01-01", ["510300", "159915"]

sig_dir = os.path.join(os.path.dirname(__file__))
sys.path.insert(0, sig_dir)
from _eval_guzhai_licha_signals import load_raw, make_signals, backtest_5050, backtest_dynamic

def bh_5050():
    feed = SinaFinanceFeed()
    req = DataRequest(codes=CODES, data_type="daily_k", start=START,
                      end=datetime.today().strftime("%Y-%m-%d"))
    resp = feed.get_data(req)
    px = {c: resp.bar_data[c].set_index("date")["close"] for c in CODES}
    px_str = {c: {str(k)[:10]: v for k, v in px[c].to_dict().items()} for c in CODES}
    dates = sorted(set(px[CODES[0]].index.tolist()) & set(px[CODES[1]].index.tolist()))
    dates = [d for d in dates if str(d)[:10] >= START]

    s0 = str(dates[0])[:10]
    shares = {}
    for c in CODES:
        p = px_str[c].get(s0, 1)
        shares[c] = int(20000 / p / 100) * 100
    cash = 40000 - sum(shares[c] * px_str[c].get(s0, 1) for c in CODES)
    daily = []
    for d in dates:
        ds = str(d)[:10]
        mv = sum(shares[c] * px_str[c].get(ds, 0) for c in CODES)
        daily.append({"date": pd.Timestamp(ds), "val": cash + mv})
    return pd.DataFrame(daily)

# ====== 回测 ======
df = load_raw()

# 基线
sdf_base = make_signals(df.copy(), "baseline")
res_base = backtest_5050(sdf_base, "baseline")
dv_base = pd.DataFrame(res_base["trades"] + [("dummy",)])  # dummy to avoid empty df issue
# Need day-by-day values. Let me re-run manually to get daily vals.

# Re-run backtests collecting daily values
def run_and_collect(method):
    sdf = make_signals(df.copy(), method)
    trade_records = []
    if method == "dynamic":
        r = backtest_dynamic(sdf, method)
    else:
        r = backtest_5050(sdf, method)
    return r

r_base = run_and_collect("baseline")
r_dyn = run_and_collect("dynamic")

# We need raw daily values. Let me re-implement a single backtest that returns both.
def backtest_with_daily(method):
    sdf = make_signals(df.copy(), method)
    feed = SinaFinanceFeed()
    req = DataRequest(codes=CODES, data_type="daily_k",
                      start=sdf["date"].iloc[0].strftime("%Y-%m-%d"),
                      end=datetime.today().strftime("%Y-%m-%d"))
    resp = feed.get_data(req)
    px = {c: resp.bar_data[c].set_index("date")["close"] for c in CODES}
    px_str = {c: {str(k)[:10]: v for k, v in px[c].to_dict().items()} for c in CODES}
    dates = sorted(set(px[CODES[0]].index.tolist()) & set(px[CODES[1]].index.tolist()))
    dates = [d for d in dates if str(d)[:10] >= START]

    if method == "dynamic":
        # === 动态分层逻辑 (v2: 起点空仓 + CONFIRM_DAYS=5) ===
        CONFIRM_DAYS = 5
        wt_map = {}
        for s, (_, r) in zip(sdf["date"], sdf.iterrows()):
            key = s.strftime("%Y-%m-%d")
            if r["signal_retreat"]:
                wt_map[key] = 0.0
            elif "charge_strong" in r:
                if r["charge_strong"]:
                    wt_map[key] = 1.0
                elif r["signal_charge"]:
                    wt_map[key] = 0.8
                else:
                    wt_map[key] = 0.5
            else:
                wt_map[key] = 1.0 if r["signal_charge"] else 0.5

        cash = 40000.0; shares = {c: 0 for c in CODES}
        target_w = 0.0; was_cleared = True  # 起点视同刚撤退
        confirm_count = 0; candidate_w = 0.0
        daily, trades = [], []

        for d in dates:
            ds = str(d)[:10]
            raw_w = wt_map.get(ds, 0.5)
            if was_cleared and raw_w < 1.0:
                raw_w = 0.0

            # 确认机制: 连续 CONFIRM_DAYS 同一权重才触发调仓
            if abs(raw_w - candidate_w) < 0.01:
                confirm_count += 1
            else:
                candidate_w = raw_w
                confirm_count = 1

            if confirm_count >= CONFIRM_DAYS:
                new_w = candidate_w
                confirm_count = CONFIRM_DAYS
            else:
                new_w = target_w  # 未确认 → 保持

            if new_w == 0.0:
                was_cleared = True
            elif new_w == 1.0:
                was_cleared = False

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
                trades.append((ds, tag, new_w))
                target_w = new_w

            mv = {c: shares[c] * px_str[c].get(ds, 0) for c in CODES}
            daily.append({"date": pd.Timestamp(ds), "val": cash + sum(mv.values()), "w": target_w})

    else:
        # === 基线逻辑 ===
        sig_map = {}
        for s, (_, r) in zip(sdf["date"], sdf.iterrows()):
            key = s.strftime("%Y-%m-%d")
            sig_map[key] = (r["signal_charge"], r["signal_retreat"])

        cash = 40000.0; shares = {c: 0 for c in CODES}
        in_mkt = False; daily, trades = [], []

        for d in dates:
            ds = str(d)[:10]
            ch, re = sig_map.get(ds, (False, False))
            if ch and not in_mkt:
                half = cash / 2
                for c in CODES:
                    p = px_str[c].get(ds)
                    if p and p > 0:
                        shares[c] = int(half / p / 100) * 100
                        cash -= shares[c] * p
                in_mkt = True
                trades.append((ds, "BUY", 1.0))
            elif re and in_mkt:
                for c in CODES:
                    p = px_str[c].get(ds)
                    if p: cash += shares[c] * p
                shares = {c: 0 for c in CODES}
                in_mkt = False
                trades.append((ds, "SELL", 0.0))

            mv = {c: shares[c] * px_str[c].get(ds, 0) for c in CODES}
            daily.append({"date": pd.Timestamp(ds), "val": cash + sum(mv.values()),
                          "w": 1.0 if in_mkt else 0.0})

    return pd.DataFrame(daily), trades

dv_base, tr_base = backtest_with_daily("baseline")
dv_dyn, tr_dyn = backtest_with_daily("dynamic")
dv_bh = bh_5050()

# ====== 画图 ======
fig, axes = plt.subplots(2, 1, figsize=(18, 10), sharex=True,
                          gridspec_kw={'height_ratios': [3, 1.2]})

ax = axes[0]
init = 40000
ax.plot(dv_base["date"], dv_base["val"]/init, color="#2c7fb8", linewidth=2,
        label=f"基线(全仓进出) +{r_base['return']*100:.1f}%", zorder=3)
ax.plot(dv_dyn["date"], dv_dyn["val"]/init, color="#e6550d", linewidth=1.8,
        label=f"动态分层(0/50/80/100%) +{r_dyn['return']*100:.1f}%", zorder=3)
ax.plot(dv_bh["date"], dv_bh["val"]/init, color="gray", linewidth=1, alpha=0.5,
        label="50/50买入持有", zorder=1)

# 标注基线买卖点
for ds, tag, _ in tr_base:
    dt = pd.Timestamp(ds)
    nav_row = dv_base[dv_base["date"] == dt]
    if len(nav_row) == 0: continue
    nav = nav_row["val"].values[0] / init
    if tag == "BUY":
        ax.scatter(dt, nav - 0.06, marker="^", color="green", s=100, zorder=5,
                   edgecolors="white", linewidths=0.8)
        ax.annotate("买入", (dt, nav - 0.10), fontsize=8, ha='center', color="green",
                    fontweight='bold')
    elif tag == "SELL":
        ax.scatter(dt, nav + 0.06, marker="v", color="red", s=100, zorder=5,
                   edgecolors="white", linewidths=0.8)
        ax.annotate("卖出", (dt, nav + 0.10), fontsize=8, ha='center', color="red",
                    fontweight='bold')

# 动态分层关键调仓标注
key_trades = [t for t in tr_dyn if t[1] in ("CLEAR", "FULL")]
for ds, tag, w in key_trades:
    dt = pd.Timestamp(ds)
    nav_row = dv_dyn[dv_dyn["date"] == dt]
    if len(nav_row) == 0: continue
    nav = nav_row["val"].values[0] / init
    if tag == "FULL":
        ax.scatter(dt, nav - 0.04, marker="^", color="#e6550d", s=50, zorder=4, alpha=0.7)
    elif tag == "CLEAR":
        ax.scatter(dt, nav + 0.04, marker="v", color="#e6550d", s=50, zorder=4, alpha=0.7)

ax.axhline(1.0, color="gray", linestyle=":", alpha=0.4)
ax.set_ylabel("净值 (起始=1.0)", fontsize=12)
ax.legend(loc="upper left", fontsize=10)
ax.set_title("股债利差 50/50 择时: 基线 vs 动态分层 (510300+159915)", fontsize=15, fontweight="bold")
ax.grid(True, alpha=0.25)

# 底部: 仓位热力图
ax2 = axes[1]
ax2.fill_between(dv_dyn["date"], 0, dv_dyn["w"], color="#e6550d", alpha=0.5,
                 label="动态分层仓位 (0/0.5/0.8/1.0)")
ax2.step(dv_base["date"], dv_base["w"], where="post", color="#2c7fb8", linewidth=2,
         label="基线仓位 (0或1)")
ax2.set_ylabel("仓位权重", fontsize=12)
ax2.set_ylim(-0.1, 1.15)
ax2.set_yticks([0, 0.5, 0.8, 1.0])
ax2.legend(loc="upper left", fontsize=9)
ax2.grid(True, alpha=0.25)
ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
ax2.xaxis.set_major_locator(mdates.YearLocator())

fig.tight_layout()
out = os.path.join(os.path.dirname(__file__), "..", "guzhai_licha_dynamic_vs_base.png")
fig.savefig(out, dpi=150, bbox_inches="tight")
print(f"已保存: {out}")
plt.close()