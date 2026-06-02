"""T028 历史统计查询模块。为实盘信号提供分类维度的历史胜率/盈亏参考。

数据来源: T028 研究 (2020-01-01 ~ 2026-05-18, 33标的, ~1450个买入信号)
可靠性验证: _verify_t028_reliable.py (pass)
"""

# === 单维度统计 (T028) ===
# 格式: {维度: {分类: (信号数, 胜率%, 平均盈亏%)}}

STAGE_STATS = {
    "早期": (745, 57.2, 3.3),
    "中期": (558, 67.6, 4.9),
    "晚期": (141, 79.4, 4.6),
}

VOLUME_STATS = {
    "放量": (387, 61.5, 4.6),
    "正常": (713, 62.3, 3.7),
    "缩量": (349, 68.5, 4.4),
}

VOLATILITY_STATS = {
    "高波": (1155, 66.1, 3.9),
    "正常": (260, 48.5, 3.1),
    "低波": (34, 94.1, 16.1),
}

RSI_STATS = {
    "RSI<60": (143, 80.4, 6.4),
    "RSI 60-70": (607, 64.7, 3.1),
    "RSI 70-80": (428, 58.4, 3.7),
    "RSI>=80": (266, 59.0, 5.6),
}

MACD_STATS = {
    "有背离": (77, 44.2, 2.7),
    "无背离": (1372, 64.7, 4.2),
}

# === 交叉维度精选 (T028) ===
CROSS_STATS = {
    ("缩量", "低波"): (28, 92.9, 12.3),
    ("缩量", "高波"): (324, 71.0, 4.3),
    ("放量", "高波"): (318, 64.2, 5.0),
    ("放量", "正常"): (66, 47.0, 1.1),
    ("缩量", "中期"): None,
}


def _format_stat(count, wr, avg_ret):
    return f"胜率{wr:.0f}% 盈亏{avg_ret:+.1f}%"


_VOLA_DISPLAY = {"高波": "高波动率", "低波": "低波动率"}
_VOLA_REV = {"高波动率": "高波", "低波动率": "低波"}


def lookup_single(dimension: str, category: str) -> str:
    """单维度查询，返回人类可读的统计字符串，如 "胜率68% 盈亏+4.4%" """
    maps = {
        "stage": STAGE_STATS,
        "volume": VOLUME_STATS,
        "volatility": VOLATILITY_STATS,
        "rsi": RSI_STATS,
        "macd": MACD_STATS,
    }
    stat_map = maps.get(dimension, {})
    entry = stat_map.get(category)
    if entry:
        return _format_stat(*entry)
    return "数据不足"


def lookup_best(d: dict) -> tuple[str, str]:
    """从 Decision.extra 中找出历史参考价值最高的那条统计。

    优先级: 如果存在交叉维度匹配 → 交叉; 否则选最低样本量≥30的单维度中胜率最高的。
    返回 (标签, 统计字符串)
    """
    stage = d.get('stage_label', '')
    vol_l = d.get('volume_label', '')
    vola_l = d.get('volatility_label', '')
    macd_l = d.get('macd_div_label', '')
    rsi_l = d.get('rsi_label', '')

    crosses = []

    cross_key = (vol_l, vola_l)
    if cross_key in CROSS_STATS:
        entry = CROSS_STATS[cross_key]
        if entry:
            vola_display = _VOLA_DISPLAY.get(vola_l, vola_l)
            crosses.append((f"{vol_l}+{vola_display}", _format_stat(*entry)))

    single_candidates = []
    for dim, label, stats in [
        ("阶段", stage, STAGE_STATS),
        ("量价", vol_l, VOLUME_STATS),
        ("波动率", vola_l, VOLATILITY_STATS),
        ("RSI", rsi_l, RSI_STATS),
        ("背离", macd_l, MACD_STATS),
    ]:
        entry = stats.get(label)
        if entry and entry[0] >= 30:
            single_candidates.append((dim, label, *entry))

    if not single_candidates and not crosses:
        return ("", "")

    if crosses:
        return crosses[0]

    best = max(single_candidates, key=lambda x: x[3])
    display_label = _VOLA_DISPLAY.get(best[1], best[1])
    return (display_label, _format_stat(best[2], best[3], best[4]))


def build_signal_tags(extra: dict) -> str:
    """构建信号标签字符串，如 '[中期] [缩量] [高波]' """
    tags = []
    if extra.get('stage_label'):
        tags.append(extra['stage_label'])
    if extra.get('volume_label'):
        tags.append(extra['volume_label'])
    if extra.get('volatility_label', '') in ('低波', '高波'):
        tags.append(extra['volatility_label'])
    if extra.get('macd_div_label') == '有背离':
        tags.append('背离')
    return ' '.join(f'[{t}]' for t in tags) if tags else ''