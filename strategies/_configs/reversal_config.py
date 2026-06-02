"""短期反转策略配置"""
from dataclasses import dataclass, field

from quantforge.core.config import StrategyConfig


@dataclass
class ReversalConfig(StrategyConfig):
    """短期反转策略参数。

    核心逻辑：在均值回归池中买入过去N天跌幅最大的ETF，布林带超跌确认，
    持有N_hold天后退出。赚"过度反应后的价格修复"。

    与ROC动量的关键区别：
    - 买跌（PR升序）而非买涨（ROC降序）
    - 标的池完全不重叠（AC < 0 vs AC > 0）
    - 固定持有期退出而非信号驱动退出
    """

    strategy_name: str = "short_term_reversal"

    # === 标的池 ===
    pool_codes: list[str] = field(default_factory=lambda: [
        "512800", "512890", "513100", "513500",
        "515220", "516970", "517180"
    ])
    code_names: dict = field(default_factory=dict)
    benchmark_code: str = "399006"
    data_type: str = "daily_k"
    start_date: str = "2018-01-01"
    end_date: str = ""

    # === 核心参数 ===
    pr_period: int = 5          # PR回看周期（N日收益率）
    top_k: int = 3              # 持仓上限
    n_hold: int = 10            # 固定持有天数

    # === 布林带 ===
    bb_period: int = 20
    bb_k: float = 2.0
    use_b_pct_filter: bool = True
    buy_threshold: float = 0.2     # %b < 0.2 入场
    sell_threshold: float = 0.8    # %b > 0.8 止盈

    # === 成交量过滤（Phase 2） ===
    use_volume_filter: bool = False
    vol_threshold: float = 1.5

    # === RSI反向（Phase 2） ===
    use_rsi_reversal: bool = False
    rsi_buy_threshold: float = 30.0
    rsi_sell_threshold: float = 70.0

    # === 止损（与动量策略一致） ===
    cut_loss: bool = True
    cut_loss_edge: float = 0.08
    high_watermark_stop_edge: float = 0.15

    # === 资金 ===
    initial_capital: float = 40000.0
