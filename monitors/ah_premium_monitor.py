"""AH溢价每日开盘报告监控器。

每个交易日开盘前运行，报告AH溢价分位及中概互联/恒生ETF策略建议。

数据流程：
1. 拉取最新AH股价（新浪/akshare）
2. 更新综合溢价CSV
3. 计算信号（方法A: 滚动分位 → 中概互联; 方法B: 绝对水平 → 恒生ETF）
4. 输出格式化报告
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PARENT_DIR = os.path.dirname(_BASE_DIR)
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

import numpy as np
import pandas as pd
import akshare as ak
from loguru import logger

from quantforge.data_sources.sina_feed import _fetch_sina_kline_raw, _parse_sina_response
from quantforge.indicators.ah_premium_signal import AHPremiumCalculator, AHPremiumState
from quantforge.tools.time_utils import is_stock_trading_day

# ═══════════════════════════════════════════════════════
# 配置常量
# ═══════════════════════════════════════════════════════

AH_PAIRS = {
    '02318': '601318',   # 中国平安
    '02628': '601628',   # 中国人寿
    '03968': '600036',   # 招商银行
    '01288': '601288',   # 农业银行
    '01398': '601398',   # 工商银行
    '00939': '601939',   # 建设银行
    '03988': '601988',   # 中国银行
    '00386': '600028',   # 中国石化
    '00857': '600857',   # 中国石油
    '01088': '601088',   # 中国神华
}

PAIR_NAMES = {
    '02318': '中国平安', '02628': '中国人寿', '03968': '招商银行',
    '01288': '农业银行', '01398': '工商银行', '00939': '建设银行',
    '03988': '中国银行', '00386': '中国石化', '00857': '中国石油',
    '01088': '中国神华',
}

FX_RATE = 0.91          # 1 HKD ≈ 0.91 RMB
AH_START = '2018'       # 首次全量拉取起点
_CSV_PATH = os.path.join(_BASE_DIR, 'results', 'ah_premium_research', 'ah_composite_index.csv')
_STATE_PATH = os.path.join(_BASE_DIR, 'position', 'position_ah_premium.json')

# 目标ETF（用于参考，非交易）
BENCHMARK_CODES = {
    '510300': '沪深300',      # A股宽基
    '159920': '恒生ETF',      # H股宽基
}
TARGET_ETFS = {
    '159605': '中概互联',     # 方法A标的
}


# ═══════════════════════════════════════════════════════
# 数据更新
# ═══════════════════════════════════════════════════════

def _fetch_latest_ah_data() -> dict[str, float]:
    """拉取所有AH对的最新日收盘溢价率。

    Returns:
        {name: premium} — 各AH对的最新溢价率
    """
    premiums = {}
    for h_code, a_code in AH_PAIRS.items():
        name = PAIR_NAMES.get(h_code, h_code)
        try:
            # H股收盘价（用近3年范围，取最新日）
            df_h = ak.stock_zh_ah_daily(symbol=h_code, start_year='2024', end_year='2026', adjust='')
            if df_h is None or df_h.empty:
                continue
            # 列名兼容
            cols = list(df_h.columns)
            date_col = next((c for c in cols if c in ('日期', 'date')), cols[0])
            close_col = next((c for c in cols if c in ('收盘', 'close')), None)
            if close_col is None:
                continue
            df_h = df_h.rename(columns={date_col: 'date', close_col: 'close_h'})
            df_h['date'] = pd.to_datetime(df_h['date'])

            # A股收盘价
            raw = _fetch_sina_kline_raw(a_code)
            df_a = _parse_sina_response(raw)
            if df_a.empty:
                continue
            df_a = df_a.rename(columns={'close': 'close_a'})
            df_a['date'] = pd.to_datetime(df_a['date'])

            # 合并，取最新公共日期
            merged = pd.merge(
                df_a[['date', 'close_a']],
                df_h[['date', 'close_h']],
                on='date', how='inner'
            )
            if merged.empty:
                continue

            latest = merged.iloc[-1]
            premium = (latest['close_a'] / (latest['close_h'] * FX_RATE) - 1) * 100
            premiums[name] = float(premium)
        except Exception as e:
            logger.warning(f"拉取 {name} 失败: {e}")
    return premiums


def _update_composite_csv(premiums: dict[str, float]) -> str | None:
    """将最新溢价数据追加到综合CSV。

    取各对溢价的 median 作为综合溢价。
    返回新数据日期（ISO格式），若数据无更新则返回 None。
    """
    if not premiums:
        logger.warning("无AH溢价数据，跳过CSV更新")
        return None

    composite_val = float(np.median(list(premiums.values())))

    # 确定日期（用今天，报告的是昨日收盘数据）
    today = datetime.now().date()

    # 读取已有CSV
    if os.path.exists(_CSV_PATH):
        existing = pd.read_csv(_CSV_PATH, index_col=0, parse_dates=True)
        # 检查今日是否已存在
        existing_dates = {d.date() for d in existing.index}
        if today in existing_dates:
            logger.info(f"{today} 数据已存在CSV中，跳过追加")
            return today.isoformat()
    else:
        existing = pd.DataFrame()

    # 追加新行
    new_row = pd.DataFrame(
        {'composite_premium': [composite_val]},
        index=[pd.Timestamp(today)]
    )
    if not existing.empty:
        combined = pd.concat([existing, new_row])
    else:
        combined = new_row

    combined = combined[~combined.index.duplicated(keep='last')]
    combined = combined.sort_index()

    os.makedirs(os.path.dirname(_CSV_PATH), exist_ok=True)
    combined.to_csv(_CSV_PATH, encoding='utf-8-sig')
    logger.info(f"综合溢价更新: {today} → {composite_val:.1f}% (来自 {len(premiums)} 对, median)")
    return today.isoformat()


# ═══════════════════════════════════════════════════════
# 报告生成
# ═══════════════════════════════════════════════════════

def _format_report(state: AHPremiumState, premiums: dict[str, float]) -> str:
    """格式化开盘报告文本（适配手机：每行≤16个中文字符宽度）。"""
    W = 16
    lines = []
    sep = "━" * W

    # ── 标题 ──
    date_short = state.data_date[-5:] if state.data_date else "??-??"
    lines.append(sep)
    lines.append(f"AH溢价 {date_short}")
    lines.append(f"综合溢价 {state.premium:.1f}%")
    lines.append(sep)

    # ── 各AH对明细（两两一行）──
    if premiums:
        lines.append("【各对溢价】")
        sorted_pairs = sorted(premiums.items(), key=lambda x: x[1], reverse=True)
        for i in range(0, len(sorted_pairs), 2):
            pair = sorted_pairs[i:i + 2]
            parts = []
            for name, val in pair:
                parts.append(f"{name} {val:.0f}%")
            lines.append("  ".join(parts))

    # ── 方法A: 中概互联 ──
    pct_a = state.method_a_pct
    label_a = state.method_a_label

    lines.append(sep)
    lines.append("【中概互联 159605】")
    lines.append("方法A 滚动2年分位")

    if pd.isna(pct_a):
        lines.append("  分位: 数据不足")
    else:
        if pct_a < 0.10:
            tag = "🔴极度低位"
        elif pct_a < 0.25:
            tag = "🟠低位"
        elif pct_a > 0.90:
            tag = "🟢极度高位"
        elif pct_a > 0.75:
            tag = "🟡高位"
        else:
            tag = "⚪中性"

        short_hint = _short_hint_a(pct_a)
        lines.append(f"  分位 {pct_a:.1%}  {tag}")
        lines.append(f"  📌 {short_hint}")

    # ── 方法B: 恒生ETF ──
    tc = state.method_b_tercile

    lines.append(sep)
    lines.append("【恒生ETF 159920】")
    lines.append("方法B 全样本绝对水平")

    if tc == -1:
        lines.append("  数据不足")
    else:
        tcmap = {0: "🔴绝对低位", 1: "⚪中位", 2: "🟢绝对高位"}
        lines.append(f"  {tcmap.get(tc, '未知')}")

        if tc == 0:
            lines.append("  60日前向: -2.24%")
        elif tc == 2:
            lines.append("  60日前向: +3.63%")

        short_hint_b = _short_hint_b(tc)
        lines.append(f"  📌 {short_hint_b}")

    # ── 策略总结 ──
    zhonggai_short = "≤1/3仓" if label_a == "low" else "可加仓" if label_a == "high" else "正常"
    hengsheng_short = "≤1/3仓" if tc == 0 else "可加仓" if tc == 2 else "正常"

    lines.append(sep)
    lines.append("【策略汇总】")
    lines.append(f"  中概: {zhonggai_short}")
    lines.append(f"  恒生: {hengsheng_short}")

    if label_a == "low" and tc == 0:
        lines.append("  ⚠ 双低→港股承压")
        lines.append("     全线≤1/3仓")
    elif label_a == "high" and tc == 2:
        lines.append("  ✅ 双高→港股顺风")

    # ── 免责 ──
    lines.append("─" * W)
    lines.append("历史统计 仅供参考")

    return "\n".join(lines)


def _short_hint_a(pct: float) -> str:
    """方法A仓位建议（≤16字宽手机适配版）。"""
    if pd.isna(pct):
        return "数据不足"
    if pct < 0.25:
        return "建议减仓"
    if pct > 0.75:
        return "可加仓"
    return "正常"


def _short_hint_b(tercile: int) -> str:
    """方法B仓位建议（≤16字宽手机适配版）。"""
    hints = {
        0: "建议减仓",
        1: "正常",
        2: "可加仓",
    }
    return hints.get(tercile, "数据不足")


# ═══════════════════════════════════════════════════════
# 状态持久化
# ═══════════════════════════════════════════════════════

def _load_state() -> dict:
    if os.path.exists(_STATE_PATH):
        with open(_STATE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {
        "last_report_date": None,
        "last_premium": None,
        "last_method_a_pct": None,
        "last_method_b_tercile": None,
        "reports_count": 0,
    }


def _save_state(state: dict):
    state["last_updated"] = datetime.now().isoformat()
    os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
    with open(_STATE_PATH, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


# ═══════════════════════════════════════════════════════
# 主监控入口
# ═══════════════════════════════════════════════════════

def ah_premium_monitor(notifiers, name="AH_Premium"):
    """AH溢价每日开盘报告。

    Args:
        notifiers: [WeChatNotifier, EmailNotifier] 列表
        name: 策略名
    """
    notifier = notifiers[0] if notifiers else None
    if notifier is None:
        logger.warning(f"{name}: 无通知器，仅本地输出")
    else:
        logger.info(f"{name}: 通知器就绪")

    if not is_stock_trading_day():
        logger.info(f"{name}: 非交易日，跳过")
        if notifier:
            notifier.notify("监控状态", f"{name}: 非交易日，跳过")
        return

    logger.info(f"{name}: 开始拉取数据")

    # ── Step 1: 拉取最新溢价 ──
    try:
        premiums = _fetch_latest_ah_data()
    except Exception as e:
        logger.opt(exception=True).error(f"{name}: 拉取数据失败: {e}")
        if notifier:
            notifier.notify(f"{name} 错误", f"拉取AH溢价数据失败: {e}")
        return

    # ── Step 2: 更新CSV ──
    try:
        report_date = _update_composite_csv(premiums)
    except Exception as e:
        logger.opt(exception=True).error(f"{name}: 更新CSV失败: {e}")
        if notifier:
            notifier.notify(f"{name} 错误", f"更新CSV失败: {e}")
        return

    if report_date is None:
        logger.warning(f"{name}: 无新数据")
        if notifier:
            notifier.notify(f"{name}", "AH溢价: 今日无新数据可更新")
        return

    # ── Step 3: 计算信号 ──
    try:
        calc = AHPremiumCalculator(_CSV_PATH)
        state = calc.compute()
    except Exception as e:
        logger.opt(exception=True).error(f"{name}: 信号计算失败: {e}")
        if notifier:
            notifier.notify(f"{name} 错误", f"信号计算失败: {e}")
        return

    # ── Step 4: 生成报告 ──
    report = _format_report(state, premiums)
    logger.info(f"\n{report}")

    # ── Step 5: 发送通知 ──
    if notifier:
        try:
            notifier.notify(name, report)
            logger.info(f"{name}: 报告已发送")
        except Exception as e:
            logger.opt(exception=True).error(f"{name}: 发送通知失败: {e}")

    # ── Step 6: 保存状态 ──
    persisted = _load_state()
    persisted["last_report_date"] = report_date
    persisted["last_premium"] = state.premium
    persisted["last_method_a_pct"] = state.method_a_pct
    persisted["last_method_b_tercile"] = state.method_b_tercile
    persisted["reports_count"] += 1
    _save_state(persisted)

    logger.info(f"{name}: 监控完成")


# ═══════════════════════════════════════════════════════
# 独立运行入口
# ═══════════════════════════════════════════════════════

if __name__ == "__main__":
    # 独立运行时（无通知器，仅本地输出）
    from loguru import logger as _logger
    _logger.remove()
    _logger.add(sys.stdout, level='INFO',
                format='<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}')

    ah_premium_monitor([], name="AH_Premium")