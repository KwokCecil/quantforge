import os
from datetime import datetime, timedelta

from quantforge.monitors._signal_stats import build_signal_tags, lookup_best, _VOLA_REV

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CHECKPOINTS = [
    (10, 15, False),
    (11,  0, False),
    (13, 30, False),
    (14, 40, True),
]


def _skip_if_passed(h, m):
    checkpoint = datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)
    return datetime.now() > checkpoint + timedelta(minutes=2)


def _classify_block_reason(reason: str) -> str:
    """从reason字符串中识别被拦截的原因类别"""
    if "RSI" in reason and ("禁止买入" in reason or "过热" in reason):
        return "RSI过滤"
    if "均线穿越" in reason:
        return "均线过滤"
    if "MACD顶背离" in reason:
        return "MACD背离"
    if "放量过滤" in reason:
        return "放量过滤"
    if "正常波过滤" in reason or ("ATR分位" in reason and "禁止买入" in reason):
        return "波动率过滤"
    if "ATR扩张" in reason:
        return "ATR扩张"
    if "ADX" in reason and "禁止买入" in reason:
        return "ADX趋势"
    if "严格买入" in reason:
        return "严格买入"
    if "ROC均线方向" in reason or "MAROC" in reason and "未上升" in reason:
        return "MAROC方向"
    if "TS#" in reason:
        return "TS动量"
    if "CrashProtection" in reason:
        return "崩盘防护"
    if "宏观" in reason:
        return "宏观阻断"
    return "其他过滤"


def _is_blocked(d) -> bool:
    """判断hold决策是否是被拦截的（而非单纯ROC不足在观望区间）。
    策略_evaluate中，被拦截的reason包含具体拦截原因；纯观望为'ROC=X.XX 在观望区间'。"""
    if d.direction != "hold":
        return False
    if "在观望区间" in d.reason:
        return False
    return True


def _label(code, code_names):
    name = code_names.get(code, "") if code_names else ""
    return f"{code} {name}" if name else code


def _roc_line(d):
    roc = d.indicator_values.get('roc')
    maroc = d.indicator_values.get('maroc')
    if roc is not None and maroc is not None:
        return f"  ROC={roc:.1f}  MAROC={maroc:.1f}"
    if roc is not None:
        return f"  ROC={roc:.1f}"
    if maroc is not None:
        return f"  MAROC={maroc:.1f}"
    return ""


def _signal_line(d):
    extra = getattr(d, 'extra', {}) or {}
    if not extra:
        return ""
    tags = build_signal_tags(extra)
    best_label, best_stat = lookup_best(extra)

    if not tags and not best_stat:
        return ""

    if best_stat:
        compact_stat = best_stat.replace('胜率', '胜').replace(' 盈亏', '')
        if tags:
            compact_tags = tags.replace('] [', '][')
            label_parts = best_label.split('+') if best_label else []
            label_dup = best_label and all(
                p in compact_tags or _VOLA_REV.get(p, p) in compact_tags
                for p in label_parts
            )
            if best_label and not label_dup:
                return f"  {compact_tags}[{best_label}]{compact_stat}"
            return f"  {compact_tags}{compact_stat}"
        return f"  [{best_label}]{compact_stat}"
    return f"  {tags}" if tags else ""


def _pnl_line(code, positions, response):
    if not positions or not response:
        return ""
    pos = positions.get(code)
    if not pos:
        return ""
    shares = pos.get('shares', 0)
    avg_cost = pos.get('avg_cost', 0)
    if shares <= 0 or avg_cost <= 0:
        return ""
    bar = response.bar_data.get(code)
    if bar is None or bar.empty:
        return ""
    price = float(bar.iloc[-1]['close'])
    if price <= 0:
        return ""
    pnl = (price - avg_cost) * shares
    pnl_pct = (price / avg_cost - 1) * 100
    return f"  持仓{shares}股 成本{avg_cost:.3f} 现价{price:.3f} 盈亏{pnl:+.0f}元 {pnl_pct:+.1f}%"


def _stop_loss_line(code, pos, price, bar, high_watermark_stop_edge, cut_loss_edge):
    hwm = pos.get('high_watermark', 0)
    avg_cost = pos.get('avg_cost', 0)

    if bar is not None and not bar.empty:
        entry_date = pos.get('entry_date', '2018-01-01')
        if 'date' in bar.columns:
            mask = bar[bar['date'] > entry_date]
        else:
            mask = bar
        highs = mask['high'].tail(22)
        if len(highs) > 0:
            hwm = max(hwm, float(highs.max()))

    if hwm and hwm > 0:
        drawdown = (hwm - price) / hwm
        if drawdown >= high_watermark_stop_edge:
            return f"卖出 {code}", f"  高水位止损: 回落{drawdown:.1%}"

    if avg_cost and avg_cost > 0:
        if price < avg_cost * (1 - cut_loss_edge):
            loss = 1 - price / avg_cost
            return f"卖出 {code}", f"  成本止损: 亏损{loss:.1%}"

    return None, None


def build_decision_report(decisions, name, hour, minute, code_names,
                          held_codes=None, positions=None, response=None):
    """构建决策报告文本，返回字符串。每次checkpoint调用，用于写入日志文件。"""
    lines = [f"━━━ {name} 决策报告 ({hour:02d}:{minute:02d}) ━━━"]

    held_codes = held_codes or set()

    enter_list = sorted(
        [d for d in decisions if d.direction == 'enter'],
        key=lambda d: d.priority,
    )
    exit_list = [d for d in decisions if d.direction == 'exit']
    hold_list = [d for d in decisions if d.direction == 'hold']
    blocked_list = [d for d in hold_list if _is_blocked(d)]
    watching_list = [d for d in hold_list if not _is_blocked(d)]

    def _section(title, items, show_pnl=True, show_signal=False):
        lines.append("")
        lines.append(f"  ── {title} ({len(items)}) ──")
        for d in items:
            label = _label(d.target_code, code_names)
            roc_val = d.indicator_values.get('roc')
            roc_part = f"ROC={roc_val:.1f}" if roc_val is not None else ""
            lines.append(f"  {label:<20s} {roc_part:<10s} pri={d.priority:<3d} | {d.reason}")

            if show_signal:
                sig_line = _signal_line(d)
                if sig_line:
                    lines.append(sig_line)

            if show_pnl and d.target_code in held_codes:
                pnl = _pnl_line(d.target_code, positions, response)
                if pnl:
                    lines.append(f"  {'':20s} {'':10s} {'':6s} {pnl}")

    _section("买入信号", enter_list, show_pnl=True, show_signal=True)
    _section("买入被拦截", blocked_list, show_pnl=False, show_signal=True)
    _section("在观望区间", watching_list, show_pnl=False)
    _section("卖出信号", exit_list, show_pnl=True)

    # 持仓观望：已在持仓中，无卖出信号触发
    held_without_exit = []
    exit_codes = {d.target_code for d in exit_list}
    for d in hold_list:
        if d.target_code in held_codes and d.target_code not in exit_codes:
            held_without_exit.append(d)
    if held_without_exit:
        lines.append("")
        lines.append(f"  ── 持仓观望 ({len(held_without_exit)}) ──")
        for d in held_without_exit:
            label = _label(d.target_code, code_names)
            roc_val = d.indicator_values.get('roc')
            roc_part = f"ROC={roc_val:.1f}" if roc_val is not None else ""
            lines.append(f"  {label:<20s} {roc_part:<10s}          | {d.reason}")
            pnl = _pnl_line(d.target_code, positions, response)
            if pnl:
                lines.append(f"  {'':20s} {'':10s} {'':6s} {pnl}")

    # 汇总
    block_counts: dict[str, int] = {}
    for d in blocked_list:
        cat = _classify_block_reason(d.reason)
        block_counts[cat] = block_counts.get(cat, 0) + 1
    block_detail = ", ".join(f"{k}{v}" for k, v in sorted(block_counts.items())) if block_counts else "无"

    lines.append("")
    lines.append(f"  ── 汇总 ──")
    lines.append(f"  共{len(decisions)}标的 | enter={len(enter_list)} exit={len(exit_list)} hold={len(hold_list)} | 拦截明细: {block_detail}")

    return "\n".join(lines)


def _build_always_report_summary(decisions, name, hour, minute, code_names):
    """无交易信号时的always_report简报：列出被拦截标的及原因"""
    blocked = [d for d in decisions if _is_blocked(d)]
    watching = [d for d in decisions if d.direction == 'hold' and not _is_blocked(d)]

    lines = [f"{name} {hour:02d}:{minute:02d}"]
    lines.append("无买入/卖出信号")

    if blocked:
        lines.append(f"ROC达标被拦截: {len(blocked)}个标的")
        for d in blocked:
            label = _label(d.target_code, code_names)
            cat = _classify_block_reason(d.reason)
            tags = build_signal_tags(getattr(d, 'extra', {}) or {})
            tag_str = f" {tags}" if tags else ""
            lines.append(f"  {label}  {cat}{tag_str}")
    else:
        lines.append(f"ROC达标: 0 | 全部{len(watching)}个标的在观望区间")

    if watching and blocked:
        lines.append(f"其余{len(watching)}个标的在观望区间")

    return "\n".join(lines)


def report_roc_signals(notifier, decisions, name, hour, minute, code_names,
                       held_codes=None, positions=None, response=None,
                       high_watermark_stop_edge=float('inf'),
                       cut_loss_edge=float('inf')):
    held_codes = held_codes or set()
    exit_decisions = [d for d in decisions if d.direction == 'exit']
    enter_decisions = sorted(
        [d for d in decisions if d.direction == 'enter'],
        key=lambda d: d.priority,
    )[:5]

    exit_codes = {d.target_code for d in exit_decisions}
    stop_warnings = []

    if response is not None:
        for code in held_codes:
            if code in exit_codes:
                continue
            pos = (positions or {}).get(code)
            if not pos:
                continue
            bar = response.bar_data.get(code)
            if bar is None or bar.empty:
                continue
            price = float(bar.iloc[-1]['close'])
            if price <= 0:
                continue
            label, reason = _stop_loss_line(code, pos, price, bar,
                                            high_watermark_stop_edge, cut_loss_edge)
            if label:
                pnl = _pnl_line(code, positions, response)
                lines_for_entry = [f"{label} ({_label(code, code_names)})", reason]
                if pnl:
                    lines_for_entry.append(pnl)
                stop_warnings.append("\n".join(lines_for_entry))

    if not notifier:
        return

    has_signals = bool(exit_decisions or enter_decisions or stop_warnings)

    if has_signals:
        lines = [f"━━ {name} {hour:02d}:{minute:02d} ━━"]
    else:
        notifier.notify("信号简报", _build_always_report_summary(
            decisions, name, hour, minute, code_names))
        return

    if exit_decisions:
        lines.append("── 卖出信号 ──")
        for d in exit_decisions:
            label = _label(d.target_code, code_names)
            lines.append(f"卖出 {label}")
            if d.reason:
                lines.append(f"  {d.reason}")
            roc_str = _roc_line(d)
            if roc_str:
                lines.append(roc_str)
            pnl = _pnl_line(d.target_code, positions, response)
            if pnl:
                lines.append(pnl)

    if stop_warnings:
        if exit_decisions:
            lines.append("")
        lines.append("── 止损预警 ──")
        lines.extend(stop_warnings)

    if enter_decisions:
        lines.append("── 买入信号 TOP5 ──")
        for d in enter_decisions:
            label = _label(d.target_code, code_names)
            hold_mark = " [持仓]" if d.target_code in held_codes else ""
            lines.append(f"买入 {label}{hold_mark}")
            if d.reason:
                lines.append(f"  {d.reason}")
            roc_str = _roc_line(d)
            if roc_str:
                lines.append(roc_str)
            sig_line = _signal_line(d)
            if sig_line:
                lines.append(sig_line)
            prior = (d.extra or {}).get('prior_block_reason')
            if prior:
                prior_date = (d.extra or {}).get('prior_block_date', '?')
                lines.append(f"  上次拦截: {prior_date} {prior}")
            if d.target_code in held_codes:
                pnl = _pnl_line(d.target_code, positions, response)
                if pnl:
                    lines.append(pnl)

    notifier.notify("信号汇报", "\n".join(lines))


def report_bond_signals(notifier, decisions, name, hour, minute, code_names):
    if notifier and decisions:
        d = decisions[0]
        label = _label(d.target_code, code_names)
        direction_map = {"enter": "全仓买入", "exit": "清仓卖出", "hold": "观望"}
        direction_text = direction_map.get(d.direction, d.direction)
        erp = d.indicator_values.get('erp', 'N/A')
        pct = d.indicator_values.get('percentile', 'N/A')
        lines = [
            f"━━ {name} {hour:02d}:{minute:02d} ━━",
            f"{label}",
            f"  {direction_text}",
            f"  ERP={erp}  分位{pct}%",
            d.reason,
        ]
        notifier.notify("择时信号", "\n".join(lines))
