"""股债利差信号深度分析：信号分布、阈值敏感性、年份分解。
"""
import os
import sys
import json
import pandas as pd
import numpy as np
from datetime import datetime
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from quantforge.indicators.guzhai_licha import GuzhaiLichaCalculator


def main():
    calc = GuzhaiLichaCalculator()
    df = calc.get_signal_df("2018-01-01")

    print("=" * 70)
    print("  股债利差信号深度分析")
    print("=" * 70)

    # === 1. 信号核心统计 ===
    print(f"\n--- 信号分布（2018-2026）---")
    total = len(df)
    charge = df['signal_charge'].sum()
    retreat = df['signal_retreat'].sum()
    neutral = total - charge - retreat
    print(f"总交易日: {total}")
    print(f"冲锋: {charge}天 ({charge/total:.0%})")
    print(f"中性: {neutral}天 ({neutral/total:.0%})")
    print(f"撤退: {retreat}天 ({retreat/total:.0%})")

    # === 2. 分位值分布 ===
    print(f"\n--- 分位值(ratio)分布 ---")
    print(f"双倍: mean={df['double_ttm_pct'].mean():.1%}, "
          f"P10={df['double_ttm_pct'].quantile(0.1):.1%}, "
          f"P50={df['double_ttm_pct'].quantile(0.5):.1%}, "
          f"P90={df['double_ttm_pct'].quantile(0.9):.1%}")
    print(f"单倍: mean={df['single_static_pct'].mean():.1%}, "
          f"P10={df['single_static_pct'].quantile(0.1):.1%}, "
          f"P50={df['single_static_pct'].quantile(0.5):.1%}, "
          f"P90={df['single_static_pct'].quantile(0.9):.1%}")

    # === 3. 冲锋/撤退时间段 ===
    df['date'] = pd.to_datetime(df['date'])

    # 冲锋连续段
    print(f"\n--- 冲锋连续时间段 ---")
    df['charge_phase'] = (df['signal_charge'] != df['signal_charge'].shift()).cumsum()
    charge_phases = df[df['signal_charge']].groupby('charge_phase')['date'].agg(['first', 'last', 'count'])
    for _, row in charge_phases.iterrows():
        tag = ""
        pct_median = df[(df['date'] >= row['first']) & (df['date'] <= row['last'])]['double_ttm_pct'].median()
        if row['count'] >= 20:
            tag = " ⭐" if pct_median < 0.10 else ""
        print(f"  {row['first'].strftime('%Y-%m-%d')} ~ {row['last'].strftime('%Y-%m-%d')} "
              f"({row['count']}天) pct={pct_median:.1%}{tag}")

    # 撤退连续段
    print(f"\n--- 撤退连续时间段 ---")
    df['retreat_phase'] = (df['signal_retreat'] != df['signal_retreat'].shift()).cumsum()
    retreat_phases = df[df['signal_retreat']].groupby('retreat_phase')['date'].agg(['first', 'last', 'count'])
    for _, row in retreat_phases.iterrows():
        print(f"  {row['first'].strftime('%Y-%m-%d')} ~ {row['last'].strftime('%Y-%m-%d')} "
              f"({row['count']}天)")

    # === 4. 阈值敏感性 ===
    print(f"\n--- 阈值敏感性分析（撤退=92%固定，变动冲锋阈值） ---")
    for fwd in [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]:
        charge_count = (df['double_ttm_pct'] <= fwd).sum()
        print(f"  双倍≤{fwd:.0%}: {charge_count}天冲锋 ({charge_count/total:.0%})")

    print(f"\n--- 阈值敏感性分析（冲锋=15%固定，变动撤退阈值） ---")
    for fb in [0.80, 0.85, 0.90, 0.92, 0.95]:
        retreat_count = (df['double_ttm_pct'] >= fb).sum()
        print(f"  双倍≥{fb:.0%}: {retreat_count}天撤退 ({retreat_count/total:.0%})")

    # === 5. 年份分解 ===
    print(f"\n--- 历年信号分布 ---")
    df['year'] = df['date'].dt.year
    for yr in range(2018, 2027):
        yr_df = df[df['year'] == yr]
        if yr_df.empty:
            continue
        c = yr_df['signal_charge'].sum()
        r = yr_df['signal_retreat'].sum()
        n = len(yr_df) - c - r
        pct_med = yr_df['double_ttm_pct'].median()
        print(f"  {yr}: 冲锋{c:>3}天 中性{n:>3}天 撤退{r:>2}天  | 分位中位数={pct_med:.1%}")

    # === 6. 当前状态 ===
    print(f"\n--- 当前状态 ---")
    last = df.iloc[-1]
    print(f"日期: {last['date'].strftime('%Y-%m-%d')}")
    print(f"HS300 PE: 静态={last['pe_static']:.1f}, TTM={last['pe_ttm']:.1f}")
    print(f"10Y国债: {last['bond_10y']:.2f}%")
    print(f"双倍利差: {last['double_ttm_licha']:.1f}%, 分位(ratio): {last['double_ttm_pct']:.1%}")
    print(f"单倍利差: {last['single_static_licha']:.1f}%, 分位(ratio): {last['single_static_pct']:.1%}")
    print(f"冲锋: {last['signal_charge']}, 撤退: {last['signal_retreat']}")


if __name__ == '__main__':
    main()