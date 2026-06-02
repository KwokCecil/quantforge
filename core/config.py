from dataclasses import dataclass
import json
import os


@dataclass
class StrategyConfig:
    """策略参数容器基类。每个策略定义自己的子类，支持参数扫描和持久化。

    设计思路：参数与策略逻辑分离，使得Validator可以在参数空间上网格搜索，
    而不需要修改策略代码。from_dict过滤未知字段，保证向前兼容。
    """
    strategy_name: str = ""

    def validate(self) -> bool:
        """参数校验。子类覆盖实现具体校验逻辑。默认无校验。"""
        return True

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if not k.startswith('_')}

    @classmethod
    def from_dict(cls, d: dict) -> 'StrategyConfig':
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def to_json(self, filepath: str):
        """保存配置到 JSON 文件"""
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, filepath: str) -> 'StrategyConfig':
        """从 JSON 文件加载配置"""
        with open(filepath, 'r', encoding='utf-8') as f:
            return cls.from_dict(json.load(f))
