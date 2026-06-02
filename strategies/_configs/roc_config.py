from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from quantforge.core.config import StrategyConfig
from quantforge.config.universes.code_names import (
    ETF_NAME_MAP,
    INDEX_NAME_MAP,
    BENCHMARK_CODE,
)


@dataclass
class ROCConfig(StrategyConfig):
    """ROC动量轮动策略参数配置。

    参数重要性分级（基于TOP_K_SELL=False模式回测，2026-04-30更新）：
    - ★★★ 核心参数：对Sharpe影响>0.1，需谨慎调整
    - ★★ 辅助参数：对Sharpe影响<0.1，可适度调整
    - ★ 冗余参数：当前策略下无影响，保留以备扩展

    条件开关分级：
    - 🔴 必须关闭：开启后策略崩溃
    - 🟢 建议开启：有正向效果
    - ⚪ 无效果：开关与否结果相同
    """

    strategy_name: str = "roc_momentum"
    codes: Optional[list[str]] = field(default=None)
    code_names: Optional[dict[str, str]] = field(default=None)
    benchmark_code: str = BENCHMARK_CODE
    start_date: str = "2020-01-01"
    end_date: Optional[str] = field(default=None)
    data_type: str = "daily_k"

    # ============================================================
    # ★★★ 核心参数（对策略表现影响最大）
    # ============================================================

    buy_roc_edge: float = 20.0  # ★★★ 买入ROC阈值(%)。最敏感参数！20.0远优于15(回撤30% vs 20%)和25(几乎无信号)
    top_k: int = 5  # ★ 持仓数量上限。TOP_K_SELL=False下无影响（极少同时有>K个买入信号）
    roc_n: int = 22  # ★★★ ROC回看周期。20-22为黄金区间，22回撤最低

    # ============================================================
    # ★★ 辅助参数（有轻微影响或交互效应）
    # ============================================================

    ma_period: int = 22  # ★★ 价格均线周期。与MA_PRICE_CROSS配合使用
    sell_ma_roc_edge: float = 0.0  # ★★ MAROC卖出阈值(%)。>0时启用，8.0在buy=15时有轻微提升；buy=20时无效果
    roc_m: int = 8  # ★ ROC均线(MAROC)周期。仅在sell_ma_roc_edge>0或ROC_MA_DIRECTION=True时有效

    # ============================================================
    # ★ 冗余参数（当前策略下无影响，保留以备扩展）
    # ============================================================

    sell_roc_edge: float = 3.0  # ★ 卖出ROC阈值(%)。T029全周期扫描结论：1~9均为最优区间，当前3.0无需修改(5.08§五)

    # ============================================================
    # 条件开关
    # ============================================================

    MA_PRICE_CROSS: bool = False  # 🟢 均线穿越：价格在均线之上才可买入。关闭反而提升0.04 Sharpe
    HIGH_WATERMARK_STOP: bool = True  # 🟢 高点回落止损。TOP_K_SELL=False下核心卖出机制
    high_watermark_stop_edge: float = 0.10  # 高点回落止损线(10%)。0.10最优(Sharpe 1.60, DD 18.5%)，0.12次之，0.08过紧

    CUT_LOSS: bool = True  # ⚪ 成本止损。开关无效果（止损极少触发），保持默认
    cut_loss_edge: float = 0.08  # ⚪ 成本止损线。0.03~0.20区间无差异(5.08§一)，极少触发

    STOP_SMALL_TRADE: bool = True  # ⚪ 过滤小额交易。开关无效果，保持默认
    skip_small_trade_limit: float = 2000.0  # 小额交易过滤阈值(元)

    STRICT_BUY: bool = False  # 🔴 严格买入：ROC刚突破阈值才买入。开启后Sharpe从1.10暴跌至0.26！
    ROC_MA_DIRECTION: bool = False  # 🔴 ROC均线方向：MAROC上升才可买入。开启后策略完全不产生交易信号！

    BUY_AVERAGE: bool = False  # ⚪ True=等额分配，False=按ROC权重分配。差异<0.01
    REBALANCE: bool = False  # ⚪ 调仓减仓。开关无效果
    buy_max_ratio: float = 1.0  # 买入上限比例（已废弃，无代码路径使用）

    CROWDED_SELL: bool = False  # ⚪ 买入拥挤时部分卖出过大持仓。开关无效果（已废弃，待移除）
    crowded_position_ratio: float = 0.5  # 拥挤持仓比例（已废弃，待移除）
    TOP_K_SELL: bool = False  # 🟢 不在TOP_K就卖出。True=跌出排名立即卖出；False=仅信号/止损/挤压时卖出
    ROC_CROSS_MAROC_SELL: bool = False  # 🔴 ROC<MAROC时卖出。开启后Sharpe从1.60暴跌至0.99！过度交易

    # ============================================================
    # 资金配置
    # ============================================================

    initial_capital: float = 40000.0

    # ============================================================
    # 波动率倒数加权（T005重验证：全周期+56pp收益、+0.15 Sharpe、盈亏比1.06→1.55）
    # ============================================================

    inverse_vol_weight: bool = False         # 是否启用波动率倒数加权

    # ============================================================
    # 共享指标参数
    # ============================================================

    rsi_period: int = 14                             # RSI计算周期(Wilder(1978)原始默认参数)
    macd_fast: int = 12                              # MACD快线周期(行业标准MACD(12,26,9))
    macd_slow: int = 26                              # MACD慢线周期
    macd_signal: int = 9                             # MACD信号线周期

    # ============================================================
    # 风格轮动自动切换（⚠️ 未启用，设计完成但未经过全周期回测验证）
    # ============================================================

    style_rotation_enabled: bool = False        # 启用自动风格切换（防守/进攻双预设动态轮动）
    sr_benchmark: str = "510300"                # 基准指数代码
    sr_trend_period: int = 60                   # 趋势判断周期（MA）
    sr_momentum_period: int = 22                # 动量确认周期（ROC）
    sr_aggressive_preset: str = "tech_growth"    # 进攻池预设名
    sr_defensive_preset: str = "all_weather"     # 防守池预设名
    sr_cooldown_days: int = 5                   # 切换冷却天数（避免频繁摇摆）

    # ============================================================
    # RSI 增强买入过滤（T028: RSI<60时买入信号胜率80.4%、盈亏+6.4%）
    # ============================================================

    rsi_enhance_enabled: bool = False           # 启用RSI买入过滤：RSI高于阈值时禁止买入
    rsi_enhance_below: float = 60.0             # RSI过滤阈值：只有RSI低于此值时允许买入

    # ============================================================
    # 股债利差仓位调节（⚠️ 已证伪：5.08§四/5.12§六，CDR融合全周期劣于纯ROC，保留参数以备独立择时）
    # ============================================================

    guzhai_licha_enabled: bool = False              # 启用股债利差仓位调节：ratio≤15%满仓，ratio≥92%清仓，中间线性
    guzhai_licha_mode: str = "linear"               # 'linear'=线性分段 | 'retreat_only'=仅撤退时清仓

    # ============================================================
    # MACD 顶背离过滤（T028: 顶背离胜率44% vs 无背离65%，降幅20pp）
    # ============================================================

    macd_divergence_filter_enabled: bool = False    # 启用MACD顶背离过滤：价格新高但DIF未确认时禁止买入
    macd_divergence_lookback: int = 20              # 背离检测回看窗口（日）

    # ============================================================
    # 量价过滤（T028: 放量组胜率61.5% vs 缩量68.5%，放量=追涨盘涌入后市空间有限）
    # ============================================================

    volume_filter_enabled: bool = False             # 启用放量过滤：成交量/均量超过阈值时禁止买入
    volume_filter_spike_ratio: float = 1.5          # 放量倍数阈值（当日vol / 前20日均vol）

    # ============================================================
    # 波动率环境过滤（T028: 正常波胜率48.5% vs 高波66.1%，平淡市ROC策略失效）
    # ============================================================

    atr_filter_enabled: bool = False                # 启用波动率过滤：ATR处于中位(25-75分位)时禁止买入

    # ============================================================
    # ATR波动率扩张（T025: ATR(20)>1.3×ATR(200) → 禁新买，+2.8pp与ADX合体）
    # ============================================================

    atr_expansion_filter_enabled: bool = False      # ATR(20)>1.3×ATR(200)时禁止买入（极端波动不追）

    # ============================================================
    # ADX趋势质量（T025: ADX(14)<20 → 震荡市不参与，+2.8pp与ATR合体）
    # ============================================================

    adx_trend_filter_enabled: bool = False           # ADX(14)<20时禁止买入（震荡市/无趋势不参与）

    # ============================================================
    # 信号统计分类（T040: 实盘信号分类增强。开启后在Decision中注入分类标签和历史统计参考）
    # ============================================================

    signal_stats_enabled: bool = False               # 启用信号分类：Decision.indicator_values/extra 中注入 T028 分类标签

    # ============================================================
    # T032: 辅助信号卖出端（⚠️ 全部证伪：5.08§一，四个卖出信号全不改善基线）
    # ============================================================

    volume_sell_enabled: bool = False               # 放量卖出：持仓中 vol_ratio>=阈值 → exit
    volume_sell_spike_ratio: float = 1.5            # 放量阈值
    atr_expansion_sell_enabled: bool = False        # ATR扩张卖出：持仓中ATR(20)>1.5×ATR(200) → exit
    macd_divergence_sell_enabled: bool = False      # MACD顶背离卖出：持仓中价新高DIF未确认 → exit
    rsi_sell_enabled: bool = False                  # [添头] RSI>80止盈：持仓中RSI>80 → 止盈退出

    def validate(self):
        errors = []
        if self.top_k < 1:
            errors.append(f"top_k 必须 >= 1，当前值: {self.top_k}")
        if self.roc_n < 1:
            errors.append(f"roc_n 必须 >= 1，当前值: {self.roc_n}")
        if self.roc_m < 1:
            errors.append(f"roc_m 必须 >= 1，当前值: {self.roc_m}")
        if self.ma_period < 1:
            errors.append(f"ma_period 必须 >= 1，当前值: {self.ma_period}")
        if self.buy_roc_edge < 0:
            errors.append(f"buy_roc_edge 必须 >= 0，当前值: {self.buy_roc_edge}")
        if self.sell_roc_edge < 0:
            errors.append(f"sell_roc_edge 必须 >= 0，当前值: {self.sell_roc_edge}")
        if self.sell_ma_roc_edge < 0:
            errors.append(f"sell_ma_roc_edge 必须 >= 0，当前值: {self.sell_ma_roc_edge}")
        if not 0 <= self.high_watermark_stop_edge <= 1:
            errors.append(f"high_watermark_stop_edge 必须在 [0, 1] 之间，当前值: {self.high_watermark_stop_edge}")
        if not 0 <= self.cut_loss_edge <= 1:
            errors.append(f"cut_loss_edge 必须在 [0, 1] 之间，当前值: {self.cut_loss_edge}")
        if self.initial_capital <= 0:
            errors.append(f"initial_capital 必须 > 0，当前值: {self.initial_capital}")
        if self.macd_fast >= self.macd_slow:
            errors.append(f"macd_fast({self.macd_fast}) 必须 < macd_slow({self.macd_slow})")
        if self.style_rotation_enabled and self.sr_aggressive_preset == self.sr_defensive_preset:
            errors.append(f"风格轮动启用时 sr_aggressive_preset 和 sr_defensive_preset 不能相同")
        if errors:
            raise ValueError("ROCConfig 参数校验失败:\n  " + "\n  ".join(errors))
        return True

    @property
    def EMPTY_DAY(self) -> int:
        return max(self.roc_n + self.roc_m, self.ma_period, self.macd_slow)

    def __post_init__(self):
        # 默认标的池 = 33只科技/成长方向ETF（T025全周期回测最终确认）
        TECH_GROWTH_CODES = [
            "515880", "159245", "159839", "512690", "159851", "515170",
            "159915", "510300", "588000", "159531", "501021",
            "513050", "159813", "159770", "159819", "516520",
            "159993", "501089", "159996", "513060", "159899",
            "516780", "516020",
            "159922", "512100", "513970", "515950",
            "159824", "561910", "159840", "515790", "516160", "159731",
        ]
        if self.codes is None:
            self.codes = list(TECH_GROWTH_CODES)
        if self.code_names is None:
            self.code_names = {**ETF_NAME_MAP, **INDEX_NAME_MAP}
        if self.end_date is None:
            self.end_date = date.today().strftime("%Y-%m-%d")
