# @layer: integration
"""T006 统一验证管道测试"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from loguru import logger

from quantforge.core.data_feed import CachedDataFeed
from quantforge.data_sources.sina_feed import SinaFinanceFeed
from quantforge.research.validation_pipeline import ValidationPipeline
from quantforge.strategies._configs.roc_config import ROCConfig
from quantforge.strategies.roc_momentum import ROCStrategy

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)
logger.remove()
logger.add(sys.stderr, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}")


def test_data_quality_step():
    """仅测试数据质量步骤。"""
    logger.info("=== 测试 data_quality 步骤 ===")
    feed = CachedDataFeed(source=SinaFinanceFeed(), cache_dir=os.path.join(BASE_DIR, "data", "sina"))
    pipeline = ValidationPipeline(
        strategy_class=ROCStrategy, config_class=ROCConfig,
        data_feed=feed,
        codes=["510300", "159915", "513050", "588000"],
        start="2024-01-01", end="2024-12-31",
    )

    report = pipeline.run(steps=["data_quality"])
    step = report.get("steps", {}).get("data_quality", {})

    assert "summary" in step, "缺少 summary"
    assert "avg_coverage" in step["summary"], "缺少 avg_coverage"
    logger.success(f"数据覆盖率: {step['summary']['avg_coverage']}%")
    logger.success("data_quality 测试通过")


def test_lightweight_pipeline():
    """运行轻量管道（data_quality + factor_ic），验证流程是否正常。"""
    logger.info("=== 测试轻量管道 (data_quality + factor_ic) ===")
    feed = CachedDataFeed(source=SinaFinanceFeed(), cache_dir=os.path.join(BASE_DIR, "data", "sina"))
    pipeline = ValidationPipeline(
        strategy_class=ROCStrategy, config_class=ROCConfig,
        data_feed=feed,
        codes=["510300", "159915", "513050"],
        start="2024-01-01", end="2024-12-31",
    )

    report = pipeline.run(steps=["data_quality", "factor_ic"])
    verdict = report.get("verdict", {})

    logger.info(f"判定: {verdict.get('verdict')}")
    logger.info(f"通过步骤: {verdict.get('passed_steps')}")
    logger.info(f"失败步骤: {verdict.get('failed_steps')}")
    logger.info(f"警告: {verdict.get('warnings')}")

    assert verdict.get("verdict") in ("PASS", "WEAK_PASS", "FAIL"), f"无效判定: {verdict.get('verdict')}"
    logger.success("轻量管道测试通过")


if __name__ == "__main__":
    test_data_quality_step()
    test_lightweight_pipeline()
    logger.success("\nT006 统一验证管道测试完成")
