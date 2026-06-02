"""股债利差 50/50 沪深300+创业板 实盘监控器。

每日盘后 15:05 检查股债利差信号，发送买卖/再平衡通知。
"""
import json
import os
import sys
from datetime import datetime

_base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.dirname(_base_dir) not in sys.path:
    sys.path.insert(0, os.path.dirname(_base_dir))

from loguru import logger

from quantforge.indicators.guzhai_licha import GuzhaiLichaCalculator, GuzhaiLichaSignal
from quantforge.tools.time_utils import wait_until, is_stock_trading_day

_STATE_FILE = os.path.join(_base_dir, 'position', 'position_guzhai_licha.json')

CODES = ["510300", "159915"]
CODE_NAMES = {"510300": "沪深300ETF", "159915": "创业板ETF"}


def _load_state() -> dict:
    """加载持仓状态"""
    if os.path.exists(_STATE_FILE):
        with open(_STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {
        "in_market": False,
        "positions": {c: {"shares": 0, "cost": 0.0} for c in CODES},
        "last_signal": None,
        "last_update": None,
    }


def _save_state(state: dict):
    """保存持仓状态"""
    state["last_update"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
    with open(_STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def _get_latest_signal() -> GuzhaiLichaSignal:
    """获取最新（昨天或今天）的股债利差信号

    CSV 数据可能有 1-2 天延迟，取最近的信号日。
    信号计算使用扩展窗口分位，与回测一致。
    """
    calc = GuzhaiLichaCalculator()
    # 取最近 2 年数据，约 500 个交易日，远超分位统计所需的 252
    start = (datetime.now().replace(year=datetime.now().year - 2)).strftime('%Y-%m-%d')
    signals = calc.compute(start)
    if not signals:
        raise ValueError("股债利差信号为空")
    return signals[-1]


def _check_rebalance(state: dict) -> list[str]:
    """检查是否需要再平衡"""
    if not state["in_market"]:
        return []

    # 获取今日收盘价
    from quantforge.data_sources.sina_feed import SinaFinanceFeed
    from quantforge.core.data_feed import DataRequest

    feed = SinaFinanceFeed()
    req = DataRequest(codes=CODES, data_type="daily_k",
                      start=(datetime.now().replace(year=datetime.now().year - 1)).strftime('%Y-%m-%d'),
                      end=datetime.now().strftime('%Y-%m-%d'))
    resp = feed.get_data(req)

    values = {}
    for code in CODES:
        df = resp.bar_data.get(code)
        if df is None or df.empty:
            return []
        price = float(df['close'].iloc[-1])
        shares = state["positions"][code]["shares"]
        values[code] = shares * price

    total = sum(values.values())
    if total <= 0:
        return []

    alerts = []
    for code in CODES:
        weight = values[code] / total if total > 0 else 0
        if weight < 0.33 and values[code] > 0:
            other = CODES[1] if code == CODES[0] else CODES[0]
            other_w = values[other] / total
            alerts.append(
                f"【再平衡】{CODE_NAMES[code]}({code}) 占比{weight:.1%} "
                f"vs {CODE_NAMES[other]}({other}) 占比{other_w:.1%}，"
                f"建议调整回50/50"
            )

    return alerts


def guzhai_licha_monitor(notifiers, name="Guzhai_Licha_5050"):
    """主监控函数。被 main_monitor.py 多进程调用。

    Args:
        notifiers: [WeChatNotifier, EmailNotifier] 列表
        name: 策略名
    """
    notifier = notifiers[0] if notifiers else None
    if notifier is None:
        logger.warning(f"{name}: 无通知器，仅打印")
    else:
        logger.info(f"{name}: 通知器就绪")

    if not is_stock_trading_day():
        logger.info(f"{name}: 非交易日，跳过")
        if notifier:
            notifier.notify("监控状态", f"{name}: 非交易日，跳过")
        return

    # 等到 14:42（收盘前，留出决策操作时间）
    now = datetime.now()
    target = now.replace(hour=14, minute=42, second=0, microsecond=0)
    if now < target:
        wait_seconds = (target - now).total_seconds()
        logger.info(f"{name}: 等待 {wait_seconds:.0f}s 到 14:42")
        wait_until(14, 40)

    logger.info(f"{name}: 开始检查信号")

    # === 获取最新信号 ===
    try:
        signal = _get_latest_signal()
    except Exception as e:
        logger.opt(exception=True).error(f"{name}: 获取信号失败: {e}")
        if notifier:
            notifier.notify(f"{name} 错误", f"获取股债利差信号失败: {e}")
        return

    # === 信号摘要 ===
    signal_summary = (
        f"日期: {signal.date.strftime('%Y-%m-%d')}\n"
        f"PE(静态): {signal.pe_static:.1f}  PE(TTM): {signal.pe_ttm:.1f}\n"
        f"10Y国债: {signal.bond_10y:.2f}%\n"
        f"双倍TTM利差: {signal.double_ttm_licha:.1f}%  分位: {signal.double_ttm_pct:.1%}\n"
        f"单倍静态利差: {signal.single_static_licha:.1f}%  分位: {signal.single_static_pct:.1%}\n"
        f"冲锋信号: {'是' if signal.signal_charge else '否'}  |  "
        f"撤退信号: {'是' if signal.signal_retreat else '否'}"
    )
    logger.info(f"\n{signal_summary}")

    # === 加载当前状态 ===
    try:
        state = _load_state()
    except Exception as e:
        logger.opt(exception=True).error(f"{name}: 加载状态文件失败: {e}")
        if notifier:
            notifier.notify(f"{name} 错误", f"加载状态文件失败: {e}")
        return
    logger.info(f"{name}: 当前状态 in_market={state['in_market']} "
                f"510300={state['positions']['510300']['shares']}股 "
                f"159915={state['positions']['159915']['shares']}股")

    # === 交易决策 ===
    messages = []

    # 撤退信号 → 清仓
    if signal.signal_retreat and state["in_market"]:
        pos_510300 = state["positions"]["510300"]["shares"]
        pos_159915 = state["positions"]["159915"]["shares"]
        msg = (
            f"【{name}】‼撤退信号触发！\n"
            f"{signal_summary}\n\n"
            f"建议明日清仓：\n"
            f"  510300 沪深300ETF: {pos_510300}股\n"
            f"  159915 创业板ETF:  {pos_159915}股"
        )
        messages.append(msg)
        # 更新状态
        state["in_market"] = False
        state["positions"] = {c: {"shares": 0, "cost": 0.0} for c in CODES}
        state["last_signal"] = "retreat"

    # 冲锋信号 → 买入
    elif signal.signal_charge and not state["in_market"]:
        msg = (
            f"【{name}】冲锋信号触发！\n"
            f"{signal_summary}\n\n"
            f"建议明日买入：\n"
            f"  510300 沪深300ETF: 50%仓位\n"
            f"  159915 创业板ETF:  50%仓位"
        )
        messages.append(msg)
        state["in_market"] = True
        state["positions"] = {c: {"shares": 0, "cost": 0.0} for c in CODES}
        state["last_signal"] = "charge"

    # 无信号变化 → 再平衡检查
    else:
        status = "冲锋(已持仓)" if signal.signal_charge else \
                 "撤退(空仓中)" if signal.signal_retreat else "中性"
        msg = (
            f"每日状态 [{status}]\n"
            f"{signal_summary}\n\n"
            f"当前状态: {'持仓中' if state['in_market'] else '空仓'}\n"
            f"  510300: {state['positions']['510300']['shares']}股\n"
            f"  159915: {state['positions']['159915']['shares']}股\n\n"
            f"⚠ 请确认上述持仓与实际账户一致。"
        )
        # 再平衡检查（仅持仓时）
        if state["in_market"]:
            try:
                reb_alerts = _check_rebalance(state)
            except Exception as e:
                logger.opt(exception=True).error(f"{name}: 再平衡检查失败: {e}")
                reb_alerts = []
            if reb_alerts:
                msg += "\n\n" + "\n".join(reb_alerts)
        messages.append(msg)
        state["last_signal"] = "charge" if signal.signal_charge else \
                               "retreat" if signal.signal_retreat else "neutral"

    # === 发送通知 ===
    for msg in messages:
        logger.info(msg)
        if notifier:
            notifier.notify(name, msg)

    # === 保存状态 ===
    try:
        _save_state(state)
        logger.info(f"{name}: 状态已保存")
    except Exception as e:
        logger.opt(exception=True).error(f"{name}: 保存状态失败: {e}")
    logger.info(f"{name}: 监控完成")