from abc import ABC, abstractmethod

import pandas as pd


class Indicator(ABC):
    """指标层基类。纯函数设计：输入DataFrame，输出附加指标列的DataFrame。

    设计思路：指标计算与策略逻辑分离，同一指标可被多个策略复用。
    compute不修改输入DataFrame（内部copy），保证无副作用。
    """
    @abstractmethod
    def compute(self, data: pd.DataFrame, **kwargs) -> pd.DataFrame:
        pass
