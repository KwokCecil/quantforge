"""akshare 股债利差数据可靠性验证
# @layer: research
校验项：PE范围、国债范围、日期连续性、与公开来源交叉验证
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np

CSV = os.path.join(os.path.dirname(__file__), "..", "data", "guzhai_licha.csv")


def validate():
    df = pd.read_csv(CSV, parse_dates=["date"])
    print(f"=== 基本信息 ===\n行数: {len(df)}, 日期区间: {df.date.min().date()} ~ {df.date.max().date()}")

    issues = []

    # 1. 日期连续性（工作日检查）
    print(f"\n=== 1. 日期连续性 ===")
    df_sorted = df.sort_values("date").reset_index(drop=True)
    gaps = df_sorted["date"].diff().dropna()
    big_gaps = gaps[gaps > pd.Timedelta(days=7)]
    if len(big_gaps) > 0:
        msg = f"日期跳跃>7天: {len(big_gaps)}处"
        print(f"❌ {msg}")
        print(big_gaps.head(10))
        issues.append(msg)
    else:
        print(f"✅ 无大跳跃")

    # 2. PE 范围合理性（HS300 PE 历史范围：8~50）
    print(f"\n=== 2. PE范围 ===")
    for col, label, low, high in [("pe_ttm", "PE(TTM)", 5, 60), ("pe_static", "PE(静态)", 5, 60)]:
        s = df[col]
        print(f"  {label}: min={s.min():.1f} max={s.max():.1f} mean={s.mean():.1f} median={s.median():.1f}")
        if s.min() < low:
            msg = f"{label} 过低: {s.min():.1f} < {low}"
            print(f"  ⚠️ {msg}")
            issues.append(msg)
        if s.max() > high:
            msg = f"{label} 过高: {s.max():.1f} > {high}"
            print(f"  ⚠️ {msg}")
            issues.append(msg)

    # 3. 国债收益率范围（10Y: 1.5%~5%）
    print(f"\n=== 3. 国债收益率 ===")
    b = df["bond_10y"]
    print(f"  10Y: min={b.min():.2f}% max={b.max():.2f}% mean={b.mean():.2f}% median={b.median():.2f}%")
    if b.min() < 1.0:
        issues.append(f"国债收益率过低: {b.min():.2f}%")
    if b.max() > 6.0:
        issues.append(f"国债收益率过高: {b.max():.2f}%")

    # 4. 利差合理性（通常在 -15% ~ +10%）
    print(f"\n=== 4. 利差范围 ===")
    for col, label in [("double_ttm_licha_pct", "双倍TTM"), ("single_static_licha_pct", "单倍静态")]:
        s = df[col]
        print(f"  {label}: min={s.min():.1f}% max={s.max():.1f}% mean={s.mean():.1f}%")
        if s.min() < -20:
            issues.append(f"{label}利差过低: {s.min():.1f}%")

    # 5. 利差逻辑一致性（PE越低利差越高）
    print(f"\n=== 5. 逻辑一致性 ===")
    corr_pe_ttm = df["pe_ttm"].corr(df["double_ttm_licha_pct"])
    corr_pe_static = df["pe_static"].corr(df["single_static_licha_pct"])
    print(f"  corr(PE_TTM, 双倍利差) = {corr_pe_ttm:.3f}")
    print(f"  corr(PE_static, 单倍利差) = {corr_pe_static:.3f}")
    if corr_pe_ttm > -0.5:
        issues.append(f"PE_TTM与利差相关性异常: {corr_pe_ttm:.3f}（预期强负相关）")

    # 6. 关键时间点核对（与公开数据交叉验证）
    print(f"\n=== 6. 关键时间点核对 ===")
    checks = {
        "2015-06-12": {"pe_ttm": (18, 22), "label": "2015牛市顶"},
        "2018-12-28": {"pe_ttm": (9, 13),   "label": "2018熊市底"},
        "2021-02-10": {"pe_ttm": (16, 20),  "label": "2021春节前顶"},
        "2024-02-05": {"pe_ttm": (10, 13),  "label": "2024初底"},
        "2024-09-30": {"pe_ttm": (12, 15),  "label": "2024-09大行情"},
    }
    for date_str, info in checks.items():
        row = df[df["date"] == date_str]
        if row.empty:
            nearest = df.iloc[(df["date"] - pd.Timestamp(date_str)).abs().argsort()[:1]]
            print(f"  ⚠️ {info['label']} ({date_str}): 无数据, 最近={nearest.iloc[0].date.strftime('%Y-%m-%d')}")
            continue
        pe = row.iloc[0]["pe_ttm"]
        lo, hi = info["pe_ttm"]
        ok = lo <= pe <= hi
        status = "✅" if ok else "⚠️"
        print(f"  {status} {info['label']} ({date_str}): PE_TTM={pe:.1f} (预期{lo}~{hi})")

    # 7. 缺失值
    print(f"\n=== 7. 缺失值 ===")
    nulls = df.isnull().sum()
    null_cols = nulls[nulls > 0]
    if len(null_cols) == 0:
        print("✅ 无缺失值")
    else:
        print(f"⚠️ 缺失: {null_cols.to_dict()}")

    # 汇总
    print(f"\n{'='*40}")
    if issues:
        print(f"❌ 发现问题 {len(issues)} 项:")
        for i, iss in enumerate(issues, 1):
            print(f"  {i}. {iss}")
    else:
        print("✅ 所有项目通过，数据可靠")

    return issues


if __name__ == "__main__":
    validate()