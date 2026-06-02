from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class DecisionType(Enum):
    """决策类型——策略→Resolver之间的核心契约标识"""
    ROTATION = "rotation"  # 多标的轮动：产出排序优先级和权重建议
    TIMING = "timing"      # 单标的择时：产出入场/出场/观望决策


@dataclass
class Decision:
    """策略产出的决策对象。策略不执行交易，只产出Decision，由Resolver转化为TargetPosition。

    核心设计：Decision ≠ 交易指令。它携带语义信息（方向、权重、优先级、置信度），
    Resolver根据这些信息决定最终仓位。同一策略可搭配不同Resolver产生不同仓位方案。
    """
    decision_type: DecisionType
    timestamp: datetime
    reason: str

    target_code: str = ""       # 决策标的
    direction: str = ""         # 'enter' | 'exit' | 'hold'
    weight: float = 0.0         # 目标权重(0.0~1.0)，轮动策略使用
    priority: int = 0           # 排序优先级，轮动策略使用（越小越优先）
    confidence: float = 1.0     # 置信度，Kelly权重计算使用

    extra: dict[str, Any] = field(default_factory=dict)               # 扩展字段，未来新DecisionType可复用
    strategy_name: str = ""                                           # 策略标识，多策略并行时追溯来源
    indicator_values: dict[str, float] = field(default_factory=dict)  # 指标快照，用于日志和验证
