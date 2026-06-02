"""验证管道命令行入口。用法：python main_validate.py --strategy roc_momentum [--steps data_quality,factor_ic]"""
import argparse
import json
import os
import sys

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT_DIR = os.path.dirname(_BASE_DIR)
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

from loguru import logger

from quantforge.core.data_feed import CachedDataFeed
from quantforge.data_sources.autostock_feed import AutoStockFeed
from quantforge.research.validation_pipeline import ValidationPipeline
from quantforge.strategies._configs.roc_config import ROCConfig
from quantforge.strategies.roc_momentum import ROCStrategy

STRATEGY_REGISTRY = {
    "roc_momentum": (ROCStrategy, ROCConfig),
}


def main():
    parser = argparse.ArgumentParser(description="QuantForge 策略验证管道")
    parser.add_argument("--strategy", type=str, default="roc_momentum",
                        choices=list(STRATEGY_REGISTRY.keys()),
                        help="策略名称")
    parser.add_argument("--steps", type=str, default=None,
                        help="执行步骤，逗号分隔。默认全部。如: data_quality,factor_ic")
    parser.add_argument("--start", type=str, default="2023-08-30", help="回测起始日期")
    parser.add_argument("--end", type=str, default="2025-06-30", help="回测结束日期")
    parser.add_argument("--train-years", type=int, default=3, help="Walk-Forward训练年数")
    parser.add_argument("--test-years", type=int, default=1, help="Walk-Forward测试年数")
    parser.add_argument("--output", type=str, default=None, help="输出JSON文件路径")
    args = parser.parse_args()

    strategy_cls, config_cls = STRATEGY_REGISTRY[args.strategy]

    data_feed = CachedDataFeed(
        source=AutoStockFeed(),
        cache_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"),
    )

    pipeline = ValidationPipeline(
        strategy_class=strategy_cls,
        config_class=config_cls,
        data_feed=data_feed,
        codes=None,
        start=args.start,
        end=args.end,
    )

    steps = args.steps.split(",") if args.steps else None

    report = pipeline.run(
        steps=steps,
        train_years=args.train_years,
        test_years=args.test_years,
    )

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2, default=str)
        logger.info(f"报告已保存: {args.output}")

    verdict = report.get("verdict", {}).get("verdict", "UNKNOWN")
    logger.info(f"最终判定: {verdict}")

    return 0 if verdict in ("PASS", "WEAK_PASS") else 1


if __name__ == "__main__":
    sys.exit(main())
