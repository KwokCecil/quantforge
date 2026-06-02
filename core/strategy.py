from abc import ABC, abstractmethod
from typing import Any

from quantforge.core.config import StrategyConfig
from quantforge.core.data_feed import DataRequest, DataResponse
from quantforge.core.decision import Decision


class Strategy(ABC):
    """策略层抽象接口。核心原则：策略只产出Decision，不执行交易。

    - produce_decisions(): 接收数据+当前持仓，产出决策列表
    - get_required_data(): 声明策略需要什么数据，调度层据此提前准备
    - name: 策略标识，日志追溯和多策略并行时使用
    - config: 参数容器，支持参数扫描和持久化
    """
    @abstractmethod
    def produce_decisions(self, data: DataResponse, positions: dict[str, Any]) -> list[Decision]:
        pass

    @abstractmethod
    def get_required_data(self) -> list[DataRequest]:
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def config(self) -> StrategyConfig:
        pass
