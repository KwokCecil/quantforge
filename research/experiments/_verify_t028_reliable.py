# @layer: e2e
"""T028 可靠性验证：通过 main_backtest.run_core_backtest 重跑全周期，
按 T028 的分类维度对真实交易做分组统计，与原始研究结论对比。

原始 T028 使用自己实现的回测逻辑（非 main_backtest），信号识别仅用
ROC >= buy_roc_edge，未经过策略 _evaluate 的多层过滤。
本脚本使用统一的 run_core_backtest 入口，从真实 trade_log 中取交易，
验证分类统计结论是否一致。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import copy
from collections import defaultdict

import numpy as np
import pandas as pd
from loguru import logger

from quantforge.core.data_feed import CachedDataFeed, DataRequest
from quantforge.data_sources.sina_feed import SinaFinanceFeed
from quantforge.indicators.technical import ROCIndicator, RSIIndicator, MACDIndicator, ATRIndicator
from quantforge.main_backtest import run_core_backtest
from quantforge.strategies.factory import create_config
from quantforge.strategies._configs.roc_config import ROCConfig

# 日志静默
logger.remove()
logger.add(sys.stdout, level='WARNING')

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULT_DIR = os.path.join(_BASE_DIR, "results", "T028_verify")

# === T028 阈值常量 ===
VOL_SPIKE = 1.5
VOL_SHRINK = 0.8
ATR_HIGH_PCT = 75
ATR_LOW_PCT = 25


def classify_stage(roc_val, maroc_val, prev_maroc, early_edge):
    if roc_val < early_edge:
        return "早期"
    if maroc_val > prev_maroc:
        return "中期"
    return "晚期"


def classify_volume(vol_ratio):
    if vol_ratio < 0:
        return "数据不足"
    if vol_ratio >= VOL_SPIKE:
        return "放量"
    if vol_ratio < VOL_SHRINK:
        return "缩量"
    return "正常"


def classify_volatility(atr_pct):
    if atr_pct < 0:
        return "数据不足"
    if atr_pct >= ATR_HIGH_PCT:
        return "高波"
    if atr_pct < ATR_LOW_PCT:
        return "低波"
    return "正常"


def classify_rsi(rsi_val):
    if rsi_val is None or np.isnan(rsi_val):
        return "数据不足"
    if rsi_val < 60:
        return "RSI<60"
    if rsi_val < 70:
        return "RSI 60-70"
    if rsi_val < 80:
        return "RSI 70-80"
    return "RSI>=80"


def check_macd_divergence(df, lookback=20):
    if 'close' not in df.columns or 'dif' not in df.columns or len(df) < lookback + 1:
        return None
    close_vals = df['close'].values[-lookback - 1:]
    dif_vals = df['dif'].values[-lookback - 1:]
    cw = close_vals[~np.isnan(close_vals)]
    dw = dif_vals[~np.isnan(dif_vals)]
    if len(cw) < 2 or len(dw) < 2:
        return None
    price_new_high = close_vals[-1] >= np.nanmax(close_vals[:-1])
    if not price_new_high:
        return False
    dif_new_high = dif_vals[-1] >= np.nanmax(dif_vals[:-1])
    return not dif_new_high


def compute_vol_ratio(df):
    if 'vol' not in df.columns or len(df) < 21:
        return -1.0
    vol_today = float(df['vol'].iloc[-1])
    if vol_today <= 0:
        return -1.0
    vol_window = df['vol'].iloc[-21:-1].dropna()
    if len(vol_window) < 5:
        return -1.0
    avg = float(vol_window.mean())
    return vol_today / avg if avg > 0 else -1.0


def compute_atr_pct(df):
    if 'atr' not in df.columns or len(df) < 252:
        return -1.0
    atr_val = df['atr'].iloc[-1]
    if pd.isna(atr_val):
        return -1.0
    atr_win = df['atr'].iloc[-252:].dropna()
    if len(atr_win) < 50:
        return -1.0
    return (atr_win < float(atr_val)).sum() / len(atr_win) * 100


def precompute_indicators(codes, start, end):
    """预计算所有标的的全区间指标，返回 {code: DataFrame}"""
    feed = CachedDataFeed(
        source=SinaFinanceFeed(),
        cache_dir=os.path.join(_BASE_DIR, "data", "sina"),
    )
    feed.update_cache(codes=codes, data_type="daily_k", start=start, end=end)
    response = feed.get_data(DataRequest(codes=codes, data_type="daily_k", start=start, end=end))

    roc_ind = ROCIndicator(n=22, m=15)
    rsi_ind = RSIIndicator(n=14)
    macd_ind = MACDIndicator(fast=12, slow=26, signal=9)
    atr_ind = ATRIndicator(n=20)

    code_dfs = {}
    for code in codes:
        df = response.bar_data.get(code)
        if df is None or df.empty:
            continue
        df = df.copy()
        df = roc_ind.compute(df, n=22, m=15)
        df = rsi_ind.compute(df, n=14)
        df = macd_ind.compute(df, fast=12, slow=26, signal=9)
        df = atr_ind.compute(df, n=20)
        code_dfs[code] = df

    return code_dfs


def pair_trades(trade_log):
    """将线性 trade_log 按 code 配对 buy→sell，返回 (buy, sell, pnl_pct) 列表"""
    code_queues = defaultdict(list)

    for t in trade_log:
        if t['action'] == 'buy':
            code_queues[t['code']].append(t)
        elif t['action'] == 'sell':
            code = t['code']
            if not code_queues[code]:
                continue
            buy = code_queues[code].pop(0)
            buy_price = buy['actual_price']
            sell_price = t['actual_price']
            if buy_price <= 0:
                continue
            pnl_pct = (sell_price / buy_price - 1) * 100
            yield buy, t, pnl_pct


def main():
    print("=" * 70)
    print("T028 可靠性验证：通过 main_backtest.run_core_backtest 重跑验证")
    print("=" * 70)

    # === 配置：tech_growth，关闭所有过滤开关 ===
    base_config = create_config("roc_momentum", "tech_growth")
    cfg = ROCConfig(**{
        **base_config.to_dict(),
        'buy_roc_edge': 15.0,   # 匹配 T028 研究时的参数
        'rsi_enhance_enabled': False,
        'macd_divergence_filter_enabled': False,
        'volume_filter_enabled': False,
        'atr_filter_enabled': False,
        'atr_expansion_filter_enabled': False,
        'adx_trend_filter_enabled': False,
    })

    # 使用 T028 相同区间
    cfg.start_date = '2020-01-01'
    cfg.end_date = '2026-05-18'
    codes = list(cfg.codes)

    print(f"配置: tech_growth (全部过滤 OFF)")
    print(f"区间: {cfg.start_date} ~ {cfg.end_date}")
    print(f"标的: {len(codes)} 个")
    early_edge = cfg.buy_roc_edge * 1.3
    print(f"买入ROC阈值: {cfg.buy_roc_edge}  早期上限: {early_edge}")
    print()

    # === 步骤1：预计算指标 ===
    print("预计算指标...")
    code_dfs = precompute_indicators(codes, cfg.start_date, cfg.end_date)
    print(f"  完成: {len(code_dfs)} 个标的")

    # === 步骤2：通过 run_core_backtest 跑回测 ===
    print("运行回测 (main_backtest.run_core_backtest)...")
    from quantforge.core.data_feed import CachedDataFeed as CF
    from quantforge.data_sources.sina_feed import SinaFinanceFeed as SF
    feed = CF(source=SF(), cache_dir=os.path.join(_BASE_DIR, "data", "sina"))
    feed.update_cache(codes=codes, data_type=cfg.data_type, start=cfg.start_date, end=cfg.end_date)

    result = run_core_backtest(cfg, skip_cache_refresh=True)
    if not result:
        print("回测失败！")
        return

    trade_log = result['trade_log']
    total_return = result['total_return']
    sharpe = result['sharpe']
    max_dd = result['max_drawdown']
    trade_count = result['trade_count']

    print(f"  总收益: {total_return:+.2%}  夏普: {sharpe:.2f}  最大回撤: {max_dd:.2%}  交易: {trade_count}")
    print(f"  trade_log 记录: {len(trade_log)} 条")
    print()

    # === 步骤3：提取交易对并分类 ===
    print("提取交易对并分类...")
    pairs = list(pair_trades(trade_log))
    print(f"  有效交易对: {len(pairs)} 组")

    records = []
    for buy, sell, pnl_pct in pairs:
        code = buy['code']
        entry_date = buy['date']
        df = code_dfs.get(code)
        if df is None:
            continue

        mask = df['date'] <= entry_date
        df_slice = df[mask].reset_index(drop=True)
        if len(df_slice) < 2:
            continue

        latest = df_slice.iloc[-1]
        prev = df_slice.iloc[-2] if len(df_slice) > 1 else latest

        roc_val = float(latest.get('roc', np.nan))
        maroc_val = float(latest.get('maroc', np.nan))
        prev_maroc = float(prev.get('maroc', np.nan))
        rsi_val = float(latest.get('rsi', np.nan)) if latest.get('rsi') is not None else np.nan
        close_val = float(latest.get('close', 0) or 0)

        if np.isnan(roc_val) or np.isnan(maroc_val):
            continue

        stage = classify_stage(roc_val, maroc_val, prev_maroc, early_edge)

        vol_ratio = compute_vol_ratio(df_slice)
        volume_label = classify_volume(vol_ratio)

        atr_pct = compute_atr_pct(df_slice)
        volatility_label = classify_volatility(atr_pct)

        macd_div = check_macd_divergence(df_slice)
        macd_label = "有背离" if macd_div else ("无背离" if macd_div is False else "数据不足")

        rsi_label = classify_rsi(rsi_val)

        roc_maroc_diff = roc_val - maroc_val

        records.append({
            'code': code, 'date': entry_date,
            'roc': roc_val, 'maroc': maroc_val, 'prev_maroc': prev_maroc,
            'rsi': rsi_val, 'close': close_val,
            'vol_ratio': vol_ratio, 'atr_pct': atr_pct,
            'macd_divergence': macd_div,
            'roc_maroc_diff': roc_maroc_diff,
            'stage': stage,
            'volume_label': volume_label,
            'volatility_label': volatility_label,
            'rsi_label': rsi_label,
            'macd_label': macd_label,
            'pnl_pct': pnl_pct,
            'is_win': pnl_pct > 0,
        })

    df_records = pd.DataFrame(records)
    print(f"  成功分类: {len(df_records)} 条")
    print()

    # === 步骤4：分组统计 ===
    def group_stats(df, col, label):
        groups = df.groupby(col)
        print(f"\n{'='*60}")
        print(f"  {label} 分组统计 (run_core_backtest 真实交易)")
        print(f"{'='*60}")
        print(f"  {'分类':<12s} {'交易数':>6s} {'胜率':>8s} {'平均盈亏':>10s} {'中位数盈亏':>10s}")
        print(f"  {'-'*50}")

        t028_ref = {
            'stage': {
                '早期': (745, 57.2, 3.3, 1.3),
                '中期': (558, 67.6, 4.9, 3.9),
                '晚期': (141, 79.4, 4.6, 4.0),
            },
            'volume_label': {
                '放量': (387, 61.5, 4.6),
                '正常': (713, 62.3, 3.7),
                '缩量': (349, 68.5, 4.4),
            },
            'volatility_label': {
                '高波动率': (1155, 66.1, 3.9),
                '正常': (260, 48.5, 3.1),
                '低波动率': (34, 94.1, 16.1),
            },
            'rsi_label': {
                'RSI<60': (143, 80.4, 6.4),
                'RSI 60-70': (607, 64.7, 3.1),
                'RSI 70-80': (428, 58.4, 3.7),
                'RSI>=80': (266, 59.0, 5.6),
            },
            'macd_label': {
                '有背离': (77, 44.2, 2.7),
                '无背离': (1372, 64.7, 4.2),
            },
        }

        results = []
        for name, grp in groups:
            count = len(grp)
            wr = grp['is_win'].mean() * 100 if count > 0 else 0
            avg_pnl = grp['pnl_pct'].mean() if count > 0 else 0
            med_pnl = grp['pnl_pct'].median() if count > 0 else 0

            ref = t028_ref.get(col, {}).get(name)
            if ref:
                ref_count, ref_wr, ref_avg = ref[0], ref[1], ref[2]
                wr_delta = wr - ref_wr
                avg_delta = avg_pnl - ref_avg
                note = f"T028参考: 胜率{ref_wr}% 盈亏+{ref_avg}%  (N={ref_count})"
                print(f"  {name:<12s} {count:>6d} {wr:>7.1f}% {avg_pnl:>+9.2f}% {med_pnl:>+9.2f}%  | {note}")
            else:
                print(f"  {name:<12s} {count:>6d} {wr:>7.1f}% {avg_pnl:>+9.2f}% {med_pnl:>+9.2f}%")

            results.append({
                'dimension': col, 'category': name,
                'count': count, 'win_rate': round(wr, 1), 'avg_return': round(avg_pnl, 2),
                'median_return': round(med_pnl, 2),
            })

    group_stats(df_records, 'stage', '阶段')
    group_stats(df_records, 'volume_label', '量价')
    group_stats(df_records, 'volatility_label', '波动率')
    group_stats(df_records, 'rsi_label', 'RSI区间')
    group_stats(df_records, 'macd_label', 'MACD背离')

    # === 步骤5：交叉维度 ===
    print(f"\n{'='*60}")
    print(f"  交叉维度: 阶段 × 量价")
    print(f"{'='*60}")
    cross = df_records.groupby(['stage', 'volume_label']).agg(
        count=('pnl_pct', 'count'),
        win_rate=('is_win', 'mean'),
        avg_pnl=('pnl_pct', 'mean'),
    ).reset_index()
    cross['win_rate'] = cross['win_rate'] * 100
    for _, row in cross.iterrows():
        if row['count'] >= 3:
            print(f"  {row['stage']:<4s} × {row['volume_label']:<4s}  "
                  f"N={int(row['count']):>3d}  胜率{row['win_rate']:.1f}%  盈亏{row['avg_pnl']:+.2f}%")

    # === 步骤6：总结 ===
    print(f"\n{'='*60}")
    print(f"  验证总结")
    print(f"{'='*60}")
    print(f"  回测引擎: main_backtest.run_core_backtest (统一入口)")
    print(f"  交易对: {len(pairs)} 组 (原始T028: ~1444 个买入信号)")
    print(f"  差异原因: run_core_backtest 经过策略 _evaluate 真实过滤逻辑")
    print(f"           (STRICT_BUY, MA_PRICE_CROSS, ROC_MA_DIRECTION 等)")
    print(f"           而原始T028仅用 ROC >= buy_roc_edge 一条规则")
    print(f"  预期: 分类趋势方向应一致，绝对值可有偏差")
    print()

    # 保存 CSV
    os.makedirs(RESULT_DIR, exist_ok=True)
    csv_path = os.path.join(RESULT_DIR, "t028_verify_trades.csv")
    df_records.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f"详细记录已保存: {csv_path}")


if __name__ == '__main__':
    main()