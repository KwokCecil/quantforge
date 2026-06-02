"""策略工厂 —— 通过策略名 + 预设名创建策略实例。

用法:
    from quantforge.strategies.factory import create_strategy
    strategy = create_strategy("roc_momentum", "conservative")

配置文件路径: config/strategies/{strategy_name}/{preset}.json
"""
import os

from quantforge.strategies._configs.reversal_config import ReversalConfig
from quantforge.strategies._configs.roc_config import ROCConfig
from quantforge.strategies.reversal import ReversalStrategy
from quantforge.strategies.roc_momentum import ROCStrategy

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_REGISTRY = {
    "roc_momentum": (ROCStrategy, ROCConfig),
    "short_term_reversal": (ReversalStrategy, ReversalConfig),
}


def create_strategy(strategy_name: str, preset: str = "all_weather"):
    """通过策略名和预设创建策略实例。

    Args:
        strategy_name: 注册的策略名，如 'roc_momentum'
        preset: 预设名，对应 config/strategies/{strategy_name}/{preset}.json

    Returns:
        Strategy 实例
    """
    if strategy_name not in _REGISTRY:
        raise ValueError(f"未知策略: {strategy_name}，已注册: {list(_REGISTRY.keys())}")

    strategy_cls, config_cls = _REGISTRY[strategy_name]
    config_path = os.path.join(_BASE_DIR, "config", "strategies", strategy_name, f"{preset}.json")

    if os.path.exists(config_path):
        config = config_cls.from_json(config_path)
    else:
        raise FileNotFoundError(
            f"配置文件不存在: {config_path}。"
            f"可用预设: {_list_presets(strategy_name)}"
        )

    return strategy_cls(config)


def create_config(strategy_name: str, preset: str = "all_weather"):
    """创建策略配置（不创建策略实例）。"""
    if strategy_name not in _REGISTRY:
        raise ValueError(f"未知策略: {strategy_name}")

    _, config_cls = _REGISTRY[strategy_name]
    config_path = os.path.join(_BASE_DIR, "config", "strategies", strategy_name, f"{preset}.json")
    config = config_cls.from_json(config_path)
    config.validate()
    return config


def _list_presets(strategy_name: str) -> list[str]:
    """列出某策略下所有可用的预设。"""
    preset_dir = os.path.join(_BASE_DIR, "config", "strategies", strategy_name)
    if not os.path.isdir(preset_dir):
        return []
    return [
        f.replace(".json", "")
        for f in os.listdir(preset_dir)
        if f.endswith(".json")
    ]
