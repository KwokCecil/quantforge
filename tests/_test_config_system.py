# @layer: unit
"""T007 策略配置系统测试"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from loguru import logger

from quantforge.strategies._configs.roc_config import ROCConfig
from quantforge.strategies.factory import create_strategy, create_config

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)
logger.remove()
logger.add(sys.stderr, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}")


def test_json_roundtrip():
    """测试配置 JSON 序列化→反序列化 往返。"""
    logger.info("=== 测试 JSON 往返 ===")
    config = ROCConfig(strategy_name="test", buy_roc_edge=20.0, roc_n=22)
    json_path = os.path.join(BASE_DIR, "_test_config_roundtrip.json")

    config.to_json(json_path)
    loaded = ROCConfig.from_json(json_path)

    assert loaded.strategy_name == "test", f"strategy_name不匹配: {loaded.strategy_name}"
    assert loaded.buy_roc_edge == 20.0, f"buy_roc_edge不匹配: {loaded.buy_roc_edge}"
    assert loaded.roc_n == 22, f"roc_n不匹配: {loaded.roc_n}"

    os.remove(json_path)
    logger.success("JSON 往返 OK")


def test_factory_default():
    """测试工厂创建策略（默认预设）。"""
    logger.info("=== 测试工厂 default 预设 ===")
    strategy = create_strategy("roc_momentum", "all_weather")
    assert strategy.name == "roc_momentum"
    config = strategy.config
    assert config.buy_roc_edge == 20.0, f"default buy_roc_edge={config.buy_roc_edge}, 期望20.0"
    assert config.inverse_vol_weight is True, f"default inverse_vol_weight应为True"
    logger.success("工厂 default OK")


def test_factory_presets():
    """测试不同预设产生不同配置。"""
    logger.info("=== 测试不同预设差异 ===")
    aw = create_config("roc_momentum", "all_weather")
    tg = create_config("roc_momentum", "tech_growth")

    assert aw.buy_roc_edge == tg.buy_roc_edge, \
        f"预设间 buy_roc_edge 应一致: {aw.buy_roc_edge} != {tg.buy_roc_edge}"
    assert aw.strategy_name == tg.strategy_name, \
        f"strategy_name 应一致"
    assert aw.codes != tg.codes or aw.top_k != tg.top_k, \
        f"all_weather 与 tech_growth 应有差异"

    logger.info(f"  all_weather: 买边={aw.buy_roc_edge}, top_k={aw.top_k}, 标的={len(aw.codes)}")
    logger.info(f"  tech_growth:  买边={tg.buy_roc_edge}, top_k={tg.top_k}, 标的={len(tg.codes)}")

    logger.success("预设差异验证 OK")


if __name__ == "__main__":
    test_json_roundtrip()
    test_factory_default()
    test_factory_presets()
    logger.success("\nT007 策略配置系统测试完成")
