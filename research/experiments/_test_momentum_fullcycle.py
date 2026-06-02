# @layer: e2e
"""T025 动量策略全周期回测重评估

两步工作流：
  python _test_momentum_fullcycle.py --action load                       # 一次性全量拉取
  python _test_momentum_fullcycle.py --action backtest --mode baseline   # 从缓存读
  python _test_momentum_fullcycle.py --action backtest --mode all        # 全部模式

设计原则:
- 数据与回测分离：一次全量拉取，后续回测只读缓存，数据完全一致
- 分步校验：baseline 跑完验证合理性后再进行其他模式
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np
import pandas as pd
from loguru import logger

from quantforge.core.data_feed import CachedDataFeed, DataRequest, DataResponse
from quantforge.core.executor import BacktestExecutor
from quantforge.core.resolver import RankingResolver
from quantforge.data_sources.sina_feed import SinaFinanceFeed
from quantforge.core.backtest_core import align_dataframes
from quantforge.core.backtest_support import BacktestAnalyzer
from quantforge.strategies._configs.roc_config import ROCConfig
from quantforge.strategies.roc_momentum import ROCStrategy
from quantforge.tools.time_utils import get_trading_dates

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, "results"), exist_ok=True)
logger.remove()
logger.add(sys.stderr, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}")

CACHE_DIR = os.path.join(BASE_DIR, "data", "sina")

# AC< -0.02 的均值回归ETF，动量策略天然不该碰
AC_REVERSAL_CODES = {'512800', '512890', '513100', '513500', '515220', '516970', '517180'}

# tech_growth.json 的33只科技/成长ETF池
TECH33_CODES = [
    "515880", "159245", "159839", "512690", "159851", "515170",
    "159915", "510300", "588000", "159531", "501021",
    "513050", "159813", "159770", "159819", "516520",
    "159993", "501089", "159996", "513060", "159899",
    "516780", "516020",
    "159922", "512100", "513970", "515950",
    "159824", "561910", "159840", "515790", "516160", "159731",
]

MODE_PRESETS = {
    "baseline": {
        "label": "Baseline ROC(22)纯动量",
        "weight_method": "signal_weight",
        "overrides": {
            "inverse_vol_weight": False,
            "ts_momentum_enabled": False,
            "crash_protection_enabled": False,
            "residual_momentum_enabled": False,
            "voting_enabled": False,
            "primary_factor": "ROC",
        },
    },
    "invvol": {
        "label": "InvVol波动率倒数加权",
        "weight_method": "inverse_vol",
        "overrides": {
            "inverse_vol_weight": True,
            "ts_momentum_enabled": False,
            "crash_protection_enabled": False,
        },
    },
    "ts_filter": {
        "label": "TS过滤 (60d, min_ret=5%)",
        "weight_method": "signal_weight",
        "overrides": {
            "ts_momentum_enabled": True,
            "ts_momentum_period": 60,
            "ts_momentum_min_return": 0.05,
            "inverse_vol_weight": False,
            "crash_protection_enabled": False,
        },
    },
    "crash": {
        "label": "动量崩盘防护",
        "weight_method": "signal_weight",
        "overrides": {
            "crash_protection_enabled": True,
            "ts_momentum_enabled": False,
            "inverse_vol_weight": False,
        },
    },
    "ts_invvol": {
        "label": "TS过滤+InvVol (最优组合)",
        "weight_method": "inverse_vol",
        "overrides": {
            "ts_momentum_enabled": True,
            "ts_momentum_period": 60,
            "ts_momentum_min_return": 0.05,
            "inverse_vol_weight": True,
            "crash_protection_enabled": False,
        },
    },
    "rsi": {
        "label": "RSI(14)替代ROC",
        "weight_method": "signal_weight",
        "overrides": {
            "primary_factor": "RSI",
            "roc_n": 14,
            "inverse_vol_weight": False,
            "ts_momentum_enabled": False,
            "crash_protection_enabled": False,
        },
    },
    "residual": {
        "label": "残差动量排名",
        "weight_method": "signal_weight",
        "overrides": {
            "residual_momentum_enabled": True,
            "inverse_vol_weight": False,
            "ts_momentum_enabled": False,
            "crash_protection_enabled": False,
        },
    },
    "voting": {
        "label": "多指标投票增强",
        "weight_method": "signal_weight",
        "overrides": {
            "voting_enabled": True,
            "inverse_vol_weight": False,
            "ts_momentum_enabled": False,
            "crash_protection_enabled": False,
        },
    },
    "market_bl": {
        "label": "大盘过滤+Baseline",
        "weight_method": "signal_weight",
        "market_filter": True,
        "overrides": {
            "inverse_vol_weight": False,
            "ts_momentum_enabled": False,
            "crash_protection_enabled": False,
            "residual_momentum_enabled": False,
            "voting_enabled": False,
            "primary_factor": "ROC",
        },
    },
    "market_invvol": {
        "label": "大盘过滤+InvVol",
        "weight_method": "inverse_vol",
        "market_filter": True,
        "overrides": {
            "inverse_vol_weight": True,
            "ts_momentum_enabled": False,
            "crash_protection_enabled": False,
        },
    },
    "market_ts": {
        "label": "大盘过滤+TS过滤",
        "weight_method": "signal_weight",
        "market_filter": True,
        "overrides": {
            "ts_momentum_enabled": True,
            "ts_momentum_period": 60,
            "ts_momentum_min_return": 0.05,
            "inverse_vol_weight": False,
            "crash_protection_enabled": False,
        },
    },
    "market_crash": {
        "label": "大盘过滤+崩盘防护",
        "weight_method": "signal_weight",
        "market_filter": True,
        "overrides": {
            "crash_protection_enabled": True,
            "ts_momentum_enabled": False,
            "inverse_vol_weight": False,
        },
    },
    "ac_market": {
        "label": "AC筛选+大盘过滤+Baseline",
        "weight_method": "signal_weight",
        "market_filter": True,
        "ac_filter": True,
        "overrides": {
            "inverse_vol_weight": False,
            "ts_momentum_enabled": False,
            "crash_protection_enabled": False,
            "residual_momentum_enabled": False,
            "voting_enabled": False,
            "primary_factor": "ROC",
        },
    },
    "ac_market_invvol": {
        "label": "AC筛选+大盘过滤+InvVol",
        "weight_method": "inverse_vol",
        "market_filter": True,
        "ac_filter": True,
        "overrides": {
            "inverse_vol_weight": True,
            "ts_momentum_enabled": False,
            "crash_protection_enabled": False,
        },
    },
    "ac_trend": {
        "label": "AC筛选+趋势死叉过滤",
        "weight_method": "signal_weight",
        "trend_filter": True,
        "ac_filter": True,
        "overrides": {
            "inverse_vol_weight": False,
            "ts_momentum_enabled": False,
            "crash_protection_enabled": False,
            "residual_momentum_enabled": False,
            "voting_enabled": False,
            "primary_factor": "ROC",
        },
    },
    "ac_trend_invvol": {
        "label": "AC筛选+趋势死叉+InvVol",
        "weight_method": "inverse_vol",
        "trend_filter": True,
        "ac_filter": True,
        "overrides": {
            "inverse_vol_weight": True,
            "ts_momentum_enabled": False,
            "crash_protection_enabled": False,
        },
    },
    "tech33_trend_invvol": {
        "label": "33科技成长池+趋势死叉+InvVol",
        "weight_method": "inverse_vol",
        "trend_filter": True,
        "ac_filter": True,
        "codes_override": TECH33_CODES,
        "overrides": {
            "inverse_vol_weight": True,
            "ts_momentum_enabled": False,
            "crash_protection_enabled": False,
        },
    },
    "tech33_fast_invvol": {
        "label": "33科技成长池+快叉+InvVol",
        "weight_method": "inverse_vol",
        "fast_cross": True,
        "ac_filter": True,
        "codes_override": TECH33_CODES,
        "overrides": {
            "inverse_vol_weight": True,
            "ts_momentum_enabled": False,
            "crash_protection_enabled": False,
        },
    },
    "tech33_atr_trend_invvol": {
        "label": "33池+死叉+ATR扩张+InvVol",
        "weight_method": "inverse_vol",
        "trend_filter": True,
        "atr_filter": True,
        "ac_filter": True,
        "codes_override": TECH33_CODES,
        "overrides": {
            "inverse_vol_weight": True,
            "ts_momentum_enabled": False,
            "crash_protection_enabled": False,
        },
    },
    "tech33_r_trend_invvol": {
        "label": "33池+死叉+ATR+恢复确认+InvVol",
        "weight_method": "inverse_vol",
        "trend_filter": True,
        "atr_filter": True,
        "recovery_filter": True,
        "ac_filter": True,
        "codes_override": TECH33_CODES,
        "overrides": {
            "inverse_vol_weight": True,
            "ts_momentum_enabled": False,
            "crash_protection_enabled": False,
        },
    },
    "tech33_adx_trend_invvol": {
        "label": "33池+死叉+ATR+ADX+InvVol",
        "weight_method": "inverse_vol",
        "trend_filter": True,
        "atr_filter": True,
        "adx_filter": True,
        "ac_filter": True,
        "codes_override": TECH33_CODES,
        "overrides": {
            "inverse_vol_weight": True,
            "ts_momentum_enabled": False,
            "crash_protection_enabled": False,
        },
    },
    "tech33_atr_adx_invvol": {
        "label": "33池+ATR+ADX+InvVol(无死叉)",
        "weight_method": "inverse_vol",
        "atr_filter": True,
        "adx_filter": True,
        "ac_filter": True,
        "codes_override": TECH33_CODES,
        "overrides": {
            "inverse_vol_weight": True,
            "ts_momentum_enabled": False,
            "crash_protection_enabled": False,
        },
    },
    "max_attack_atr_adx": {
        "label": "max_attack + ATR扩张 + ADX趋势",
        "top_k": 5,
        "weight_method": "signal_weight",
        "atr_filter": True,
        "adx_filter": True,
        "ac_filter": True,
        "codes_override": TECH33_CODES,
        "no_stop_loss": True,
        "overrides": {
            "inverse_vol_weight": False,
            "ts_momentum_enabled": False,
            "crash_protection_enabled": False,
            "rsi_enhance_enabled": False,
            "macd_divergence_filter_enabled": False,
            "volume_filter_enabled": False,
            "atr_filter_enabled": False,
            "atr_expansion_filter_enabled": True,
            "adx_trend_filter_enabled": True,
            "top_k": 5,
        },
    },
    "sharp_defense_atr_adx": {
        "label": "sharp_defense + ATR扩张 + ADX趋势",
        "top_k": 5,
        "weight_method": "inverse_vol",
        "atr_filter": True,
        "adx_filter": True,
        "ac_filter": True,
        "codes_override": TECH33_CODES,
        "overrides": {
            "inverse_vol_weight": True,
            "ts_momentum_enabled": False,
            "crash_protection_enabled": False,
            "rsi_enhance_enabled": True,
            "rsi_enhance_below": 60.0,
            "macd_divergence_filter_enabled": True,
            "macd_divergence_lookback": 20,
            "volume_filter_enabled": True,
            "volume_filter_spike_ratio": 1.5,
            "atr_filter_enabled": True,
            "atr_expansion_filter_enabled": True,
            "adx_trend_filter_enabled": True,
            "top_k": 5,
        },
    },
    "full53_atr_adx_invvol": {
        "label": "53全池+ATR+ADX+InvVol(无死叉)",
        "weight_method": "inverse_vol",
        "atr_filter": True,
        "adx_filter": True,
        "ac_filter": True,
        "overrides": {
            "inverse_vol_weight": True,
            "ts_momentum_enabled": False,
            "crash_protection_enabled": False,
        },
    },
}

ALL_MODES = list(MODE_PRESETS.keys())


def _parse_args():
    import argparse
    parser = argparse.ArgumentParser(description="T025 全周期动量策略重评估")
    parser.add_argument("--action", type=str, default="backtest",
                        choices=["load", "backtest"],
                        help="load=一次性全量拉取缓存; backtest=从缓存读取回测")
    parser.add_argument("--mode", type=str, default="baseline",
                        choices=ALL_MODES + ["all"],
                        help="回测模式")
    parser.add_argument("--start", type=str, default="2018-01-01")
    parser.add_argument("--end", type=str, default="2026-04-30")
    parser.add_argument("--top-k", type=int, default=3, dest="top_k")
    parser.add_argument("--benchmark", type=str, default="399006",
                        help="基准指数代码: 399006(创业板) / 000300(沪深300)")
    return parser.parse_args()


def _get_all_codes(codes_override=None):
    if codes_override:
        all_codes = sorted(set(codes_override))
    else:
        tmp_cfg = ROCConfig()
        all_codes = sorted(set(tmp_cfg.codes))
    if "510300" in all_codes:
        all_codes.remove("510300")
        all_codes.insert(0, "510300")
    return all_codes


def _action_load(args):
    """一次性全量拉取所有标的数据，保存到 sina 缓存。"""
    stock_feed = CachedDataFeed(source=SinaFinanceFeed(), cache_dir=CACHE_DIR)
    all_codes = _get_all_codes()

    logger.info(f"全量拉取 {len(all_codes)} 个标的 ({args.start} ~ {args.end})")
    stock_feed.update_cache(codes=all_codes, data_type="daily_k", start=args.start, end=args.end)

    logger.success(f"数据已缓存至 {CACHE_DIR}")
    logger.info("后续回测请使用: python _test_momentum_fullcycle.py --action backtest --mode all")


def _load_data_from_cache(args):
    """从缓存直接读取数据，不触发任何网络请求。"""
    all_codes = _get_all_codes()
    stock_feed = CachedDataFeed(source=SinaFinanceFeed(), cache_dir=CACHE_DIR)

    logger.info(f"从缓存加载 {len(all_codes)} 个标的 ({args.start} ~ {args.end})")
    response = stock_feed.get_data(DataRequest(
        codes=all_codes, data_type="daily_k",
        start=args.start, end=args.end,
    ))

    available_codes = []
    for code in all_codes:
        if code in response.bar_data and not response.bar_data[code].empty:
            available_codes.append(code)
        else:
            logger.warning(f"缓存无数据: {code}")

    if not available_codes:
        logger.error("没有任何标的可回测，请先执行 --action load")
        return None

    logger.info(f"可用标的: {len(available_codes)}/{len(all_codes)}")

    aligned = align_dataframes([response.bar_data[code].copy() for code in available_codes])
    aligned_bar_data = {code: aligned[i] for i, code in enumerate(available_codes)}

    # 构建IPO日期映射——ffill在未上市日期制造了虚假数据，回测时按日期跳过
    from quantforge.tools.json_tool import read_batch_params
    batch_params = read_batch_params(CACHE_DIR) or {}
    ranges = batch_params.get('fund_actual_date_ranges', {})
    ipo_map = {}
    for code in available_codes:
        r = ranges.get(code, {})
        ipo_map[code] = r.get('min_date', args.start)

    benchmark_raw = aligned_bar_data["510300"].copy()
    trading_dates = get_trading_dates(args.start, args.end)
    all_dates = set(benchmark_raw['date'].tolist())
    trading_dates = [d for d in trading_dates if d in all_dates]
    logger.info(f"交易日: {len(trading_dates)}")

    return aligned_bar_data, trading_dates, available_codes, ipo_map


def _is_market_bearish(aligned_bar_data, date, codes, ipo_map, threshold=0.5, ma_period=200):
    """若超过 threshold 比例的ETF收盘价低于MA200，判定为熊市。只统计已上市的ETF。"""
    bear = 0
    total = 0
    for code in codes:
        if code not in aligned_bar_data:
            continue
        if code in ipo_map and date < ipo_map[code]:
            continue
        df = aligned_bar_data[code]
        mask = df['date'] <= date
        rows = df[mask]
        if len(rows) < ma_period:
            continue
        total += 1
        if rows['close'].iloc[-1] < rows['close'].tail(ma_period).mean():
            bear += 1
    if total < 10:
        return False
    return bear / total > threshold


def _is_death_cross(benchmark_df, date):
    """510300 的 MA50 < MA200 → 趋势走弱，禁止新买入。"""
    mask = benchmark_df['date'] <= date
    rows = benchmark_df[mask]
    if len(rows) < 200:
        return False
    close = pd.to_numeric(rows['close'], errors='coerce')
    ma50 = close.tail(50).mean()
    ma200 = close.tail(200).mean()
    return ma50 < ma200


def _is_fast_cross(benchmark_df, date):
    """510300 的 MA20 < MA50 → 快速趋势转弱，比死叉更灵敏。"""
    mask = benchmark_df['date'] <= date
    rows = benchmark_df[mask]
    if len(rows) < 50:
        return False
    close = pd.to_numeric(rows['close'], errors='coerce')
    ma20 = close.tail(20).mean()
    ma50 = close.tail(50).mean()
    return ma20 < ma50


def _is_atr_expansion(benchmark_df, date, ratio=1.3):
    """ATR(20) > ATR(200) × ratio → 波动率异常扩张，暂停新建仓位。"""
    mask = benchmark_df['date'] <= date
    rows = benchmark_df[mask]
    if len(rows) < 201:
        return False
    close = pd.to_numeric(rows['close'], errors='coerce')
    high = pd.to_numeric(rows['high'], errors='coerce') if 'high' in rows.columns else close
    low = pd.to_numeric(rows['low'], errors='coerce') if 'low' in rows.columns else close
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr20 = tr.tail(20).mean()
    atr200 = tr.tail(200).mean()
    if atr200 <= 0:
        return False
    return atr20 > atr200 * ratio


def _days_since_golden_cross(benchmark_df, date):
    """金叉(MA50>MA200)发生以来经过的天数。未发生金叉返回-1。"""
    mask = benchmark_df['date'] <= date
    rows = benchmark_df[mask]
    if len(rows) < 201:
        return -1
    close = pd.to_numeric(rows['close'], errors='coerce')
    ma50 = close.rolling(50).mean()
    ma200 = close.rolling(200).mean()
    dc = ma50 < ma200
    # 找最近一次从True→False的转折点
    if not dc.iloc[-1]:  # 当前不在死叉中
        for i in range(len(dc) - 1, 0, -1):
            if dc.iloc[i] and not dc.iloc[i-1]:
                continue  # 还在找转折点
            if dc.iloc[i-1] and not dc.iloc[i]:
                days = len(dc) - 1 - i
                return days if days >= 0 else 0
        return 999  # 从未死叉过，视为长期金叉
    return -1


def _is_recovery_pending(benchmark_df, date, wait_days=5):
    """金叉后尚未满 wait_days 天，或期间价格跌破MA20，需等待确认。"""
    days = _days_since_golden_cross(benchmark_df, date)
    if days < 0:
        return True  # 仍在死叉中
    if days >= wait_days:
        return False  # 恢复确认完成
    # 检查恢复期间是否都在MA20之上
    mask = benchmark_df['date'] <= date
    rows = benchmark_df[mask].tail(days)
    close = pd.to_numeric(rows['close'], errors='coerce')
    ma20 = close.rolling(20, min_periods=1).mean()
    if (close < ma20).any():
        return True  # 恢复中跌破了MA20，重置
    return True  # 尚未满 wait_days 天


def _is_adx_weak(benchmark_df, date, threshold=20):
    """ADX(14) < threshold → 市场无趋势/震荡，禁止新建仓位。"""
    mask = benchmark_df['date'] <= date
    rows = benchmark_df[mask]
    if len(rows) < 30:
        return False
    high = pd.to_numeric(rows['high'], errors='coerce')
    low = pd.to_numeric(rows['low'], errors='coerce')
    close = pd.to_numeric(rows['close'], errors='coerce')

    tr_arr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr14 = tr_arr.tail(14).mean()
    if atr14 <= 0:
        return False

    up = high.diff()
    dn = -low.diff()
    plus_dm = up.where((up > dn) & (up > 0), 0.0)
    minus_dm = dn.where((dn > up) & (dn > 0), 0.0)
    atr14_full = tr_arr.rolling(14, min_periods=1).mean()
    plus_di = 100 * (plus_dm.rolling(14, min_periods=1).mean() / atr14_full)
    minus_di = 100 * (minus_dm.rolling(14, min_periods=1).mean() / atr14_full)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.rolling(14, min_periods=1).mean()
    return adx.iloc[-1] < threshold


def _run_backtest(strategy, resolver, aligned_bar_data, trading_dates,
                  codes, initial_capital, label, market_filter=False,
                  trend_filter=False, fast_cross=False, atr_filter=False,
                  recovery_filter=False, adx_filter=False, ipo_map=None):
    executor = BacktestExecutor(initial_capital=initial_capital)
    filter_days = 0

    for i, date in enumerate(trading_dates):
        if ipo_map:
            valid_codes = [c for c in codes if c in ipo_map and date >= ipo_map[c]]
        else:
            valid_codes = codes

        date_bar_data = {}
        for code in valid_codes:
            if code in aligned_bar_data:
                mask = aligned_bar_data[code]['date'] <= date
                date_bar_data[code] = aligned_bar_data[code][mask].reset_index(drop=True)

        date_response = DataResponse(bar_data=date_bar_data)

        blocked = False
        if market_filter:
            blocked = blocked or _is_market_bearish(aligned_bar_data, date, valid_codes, ipo_map or {})
        if trend_filter and "510300" in aligned_bar_data:
            blocked = blocked or _is_death_cross(aligned_bar_data["510300"], date)
        if fast_cross and "510300" in aligned_bar_data:
            blocked = blocked or _is_fast_cross(aligned_bar_data["510300"], date)
        if atr_filter and "510300" in aligned_bar_data:
            blocked = blocked or _is_atr_expansion(aligned_bar_data["510300"], date)
        if recovery_filter and "510300" in aligned_bar_data:
            blocked = blocked or _is_recovery_pending(aligned_bar_data["510300"], date)
        if adx_filter and "510300" in aligned_bar_data:
            blocked = blocked or _is_adx_weak(aligned_bar_data["510300"], date)

        try:
            if blocked:
                filter_days += 1
                positions = executor.get_positions()
                if positions:
                    stop_targets = resolver._check_stop_loss(positions, date_response)
                    executor.execute(stop_targets, date_response)
                else:
                    executor.execute([], date_response)
            else:
                decisions = strategy.produce_decisions(date_response, executor.get_positions())
                targets = resolver.resolve(decisions, executor.get_positions(),
                                            executor.available_capital(), date_response)
                executor.execute(targets, date_response)
        except Exception:
            continue

        if (i + 1) % 100 == 0:
            logger.info(f"[{label}] {i+1}/{len(trading_dates)} ({date})")

    if market_filter or trend_filter or fast_cross or atr_filter or recovery_filter or adx_filter:
        logger.info(f"[{label}] 过滤日: {filter_days}/{len(trading_dates)} ({filter_days/len(trading_dates)*100:.1f}%)")
    return executor


def _compute_metrics(executor, label, aligned_bar_data, args=None):
    try:
        analyzer = BacktestAnalyzer()
        config = ROCConfig()

        # 基准：index code → ETF proxy code
        _BENCHMARK_PROXY = {
            '399006': '159915',
            '000300': '510300',
            '000905': '510500',
            '000016': '510050',
        }
        _BENCHMARK_NAMES = {
            '399006': '创业板指',
            '000300': '沪深300',
            '000905': '中证500',
            '000016': '上证50',
        }
        benchmark_code = "399006"
        if args is not None and hasattr(args, 'benchmark'):
            benchmark_code = args.benchmark
        bm_code = _BENCHMARK_PROXY.get(benchmark_code, '510300')
        bm_name = _BENCHMARK_NAMES.get(benchmark_code, benchmark_code)

        bm = None
        if bm_code in aligned_bar_data:
            bm_df = aligned_bar_data[bm_code]
            if not bm_df.empty and 'close' in bm_df.columns:
                series = bm_df.set_index('date')['close'].astype(float)
                series.index = pd.to_datetime(series.index)
                series = series.dropna()
                if len(series) > 0:
                    bm = series / series.iloc[0]

        save_path = os.path.join(BASE_DIR, "results", f"T025_{label}")
        metrics = analyzer.analyze(executor,
                                   benchmark_series=bm,
                                   benchmark_name=bm_name,
                                   code_names=config.code_names,
                                   strategy_config=config,
                                   save_dir=save_path)
        return metrics
    except Exception as e:
        logger.warning(f"[{label}] analyze/plot 异常: {e}，回退到简化指标")
        try:
            results = executor.get_results()
            nv = results.get('net_values', {})
            if not nv:
                return {"total_return": 0, "annual_return": 0, "max_drawdown": 0,
                        "sharpe_ratio": 0, "sortino_ratio": 0, "trade_count": 0, "win_rate": 0}
            if isinstance(nv, dict):
                navs = pd.Series(nv).sort_index()
            else:
                navs = pd.Series(nv)
                navs = navs.sort_index()
            navs.index = pd.to_datetime(navs.index)
            navs = pd.to_numeric(navs, errors='coerce').dropna()
            if len(navs) < 2:
                return {"total_return": 0, "annual_return": 0, "max_drawdown": 0,
                        "sharpe_ratio": 0, "sortino_ratio": 0, "trade_count": 0, "win_rate": 0}
            total = navs.iloc[-1] / navs.iloc[0] - 1
            days = (navs.index[-1] - navs.index[0]).days
            ann = (1 + total) ** (365 / max(days, 1)) - 1
            peak = navs.cummax()
            dd = (peak - navs) / peak
            max_dd = dd.max()
            daily = navs.pct_change().dropna()
            sharpe = daily.mean() / daily.std() * np.sqrt(252) if daily.std() > 0 else 0
            return {"total_return": total, "annual_return": ann, "max_drawdown": max_dd,
                    "sharpe_ratio": sharpe, "sortino_ratio": 0,
                    "trade_count": len(results.get('trade_log', [])),
                    "win_rate": 0}
        except Exception as e2:
            logger.warning(f"[{label}] fallback也失败: {e2}")
            return {"total_return": 0, "annual_return": 0, "max_drawdown": 0,
                    "sharpe_ratio": 0, "sortino_ratio": 0, "trade_count": 0, "win_rate": 0}


def _fmt_row(label, metrics):
    ann = metrics.get('annual_return', 0) or 0
    dd = abs(metrics.get('max_drawdown', 0) or 0.01)
    sharpe = metrics.get('sharpe_ratio', 0) or 0
    total = metrics.get('total_return', 0) or 0
    trades = metrics.get('trade_count', 0) or 0
    win = metrics.get('win_rate', 0) or 0
    calmar = ann / dd if dd > 0 else 0
    return f"{label:<22} {ann:>8.1%} {dd:>8.1%} {sharpe:>7.2f} {calmar:>7.2f} {trades:>5d} {win:>7.1%} {total:>8.1%}"


def _make_roc_config(args, overrides: dict) -> ROCConfig:
    cfg = ROCConfig(
        strategy_name="roc_momentum",
        start_date=args.start,
        end_date=args.end,
        top_k=args.top_k,
        HIGH_WATERMARK_STOP=False,
        CUT_LOSS=False,
        TOP_K_SELL=False,
        BUY_AVERAGE=False,
        STRICT_BUY=False,
        ROC_MA_DIRECTION=False,
        ROC_CROSS_MAROC_SELL=False,
        MA_PRICE_CROSS=False,
        CROWDED_SELL=False,
        STOP_SMALL_TRADE=False,
        REBALANCE=False,
        multi_factor=False,
        style_rotation_enabled=False,
    )
    for key, val in overrides.items():
        setattr(cfg, key, val)
    return cfg


def _run_mode(aligned_bar_data, trading_dates, available_codes, args, mode_name, ipo_map=None):
    preset = MODE_PRESETS[mode_name]
    cfg = _make_roc_config(args, preset["overrides"])
    codes = [c for c in available_codes if c != "510300"]

    if preset.get("codes_override"):
        codes = [c for c in codes if c in preset["codes_override"]]
        cfg.codes = [c for c in cfg.codes if c in preset["codes_override"]]

    if preset.get("ac_filter"):
        before = len(codes)
        codes = [c for c in codes if c not in AC_REVERSAL_CODES]
        rejected = [c for c in available_codes if c in AC_REVERSAL_CODES]
        logger.info(f"[{mode_name}] AC筛选: 排除 {len(rejected)} 只均值回归ETF ({', '.join(rejected)})，{before}→{len(codes)}")
        cfg.codes = [c for c in cfg.codes if c not in AC_REVERSAL_CODES]

    strategy = ROCStrategy(cfg)
    tk = preset.get("top_k", args.top_k)
    hwm_edge = float('inf') if preset.get("no_stop_loss") else 0.10
    cl_edge  = float('inf') if preset.get("no_stop_loss") else 0.08
    resolver = RankingResolver(
        top_k=tk,
        weight_method=preset.get("weight_method", "signal_weight"),
        high_watermark_stop_edge=hwm_edge,
        cut_loss_edge=cl_edge,
        top_k_sell=False,
    )

    executor = _run_backtest(strategy, resolver, aligned_bar_data, trading_dates,
                             codes, cfg.initial_capital, mode_name,
                             market_filter=preset.get("market_filter", False),
                             trend_filter=preset.get("trend_filter", False),
                             fast_cross=preset.get("fast_cross", False),
                             atr_filter=preset.get("atr_filter", False),
                             recovery_filter=preset.get("recovery_filter", False),
                             adx_filter=preset.get("adx_filter", False),
                             ipo_map=ipo_map)
    return _compute_metrics(executor, mode_name, aligned_bar_data, args)


def _validate(baseline_metrics):
    ann = baseline_metrics.get('annual_return', 0) or 0
    dd = abs(baseline_metrics.get('max_drawdown', 0) or 0)
    sharpe = baseline_metrics.get('sharpe_ratio', 0) or 0

    checks = []
    if dd > 0.70:    checks.append(f"回撤过高 ({dd:.1%})，可能止损未生效")
    if sharpe < -0.5: checks.append(f"Sharpe过低 ({sharpe:.2f})")

    if checks:
        logger.warning(f"⚠ BASELINE校验发现: {'; '.join(checks)}")
        logger.warning("  请检查策略代码或数据源。")
        return False
    else:
        logger.success(f"✓ Baseline校验通过 (年化={ann:.1%}, DD={dd:.1%}, Sharpe={sharpe:.2f})")
        return True


def main():
    args = _parse_args()

    if args.action == "load":
        _action_load(args)
        return

    data = _load_data_from_cache(args)
    if data is None:
        return
    aligned_bar_data, trading_dates, available_codes, ipo_map = data

    modes_to_run = list(MODE_PRESETS.keys()) if args.mode == "all" else [args.mode]

    all_metrics = {}
    for mode_name in modes_to_run:
        logger.info(f"\n{'='*60}\n  {MODE_PRESETS[mode_name]['label']}\n{'='*60}")
        metrics = _run_mode(aligned_bar_data, trading_dates, available_codes, args, mode_name, ipo_map)

        row = _fmt_row(MODE_PRESETS[mode_name]['label'], metrics)
        logger.info(f"\n  {'指标':<22} {'年化':>8} {'回撤':>8} {'Sharpe':>7} {'Calmar':>7} {'交易':>5} {'胜率':>7} {'总收益':>8}")
        logger.info(f"  {'-'*70}")
        logger.info(f"  {row}")

        all_metrics[mode_name] = metrics

        if mode_name == "baseline":
            _validate(metrics)  # 仅报告，不中断

    if len(all_metrics) > 1:
        logger.info(f"\n{'='*70}")
        logger.info("全周期对比汇总")
        logger.info(f"{'='*70}")
        logger.info(f"  {'模式':<22} {'年化':>8} {'回撤':>8} {'Sharpe':>7} {'Calmar':>7} {'交易':>5} {'胜率':>7} {'总收益':>8}")
        logger.info(f"  {'-'*70}")
        for mn in modes_to_run:
            label = MODE_PRESETS[mn]['label']
            logger.info(f"  {_fmt_row(label, all_metrics[mn])}")
        logger.info(f"{'='*70}")

    logger.success(f"\nT025 {args.mode} 完成")

if __name__ == "__main__":
    main()
