# @layer: integration
"""T002 Validator 分步验证脚本"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from loguru import logger

from quantforge.core.data_feed import CachedDataFeed
from quantforge.data_sources.sina_feed import SinaFinanceFeed
from quantforge.strategies._configs.roc_config import ROCConfig
from quantforge.strategies.roc_momentum import ROCStrategy
from quantforge.research.validator import Validator
from quantforge.tools.log_format import format_no_exception, format_exception_chain_str

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

os.makedirs(os.path.join(_BASE_DIR, 'logs'), exist_ok=True)
logger.remove()
logger.add(sys.stderr, level='INFO',
           format='<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}',
           enqueue=True)


def main():
    config = ROCConfig(start_date="2020-01-01")
    data_feed = CachedDataFeed(source=SinaFinanceFeed(), cache_dir=os.path.join(_BASE_DIR, 'data', 'sina'))

    validator = Validator(objective='sharpe_ratio')

    sweep_result = validator.parameter_sweep(
        strategy_class=ROCStrategy, config_class=ROCConfig,
        param_ranges={'buy_roc_edge': [18, 20], 'roc_n': [15, 22]},
        data_feed=data_feed, codes=config.codes,
        start='2020-01-01', end='2025-12-31',
    )

    logger.info(f"\n=== 参数扫描结果 ({len(sweep_result)} 组) ===")
    display = [c for c in ['buy_roc_edge', 'roc_n', 'total_return', 'sharpe_ratio', 'max_drawdown', 'calmar_ratio'] if c in sweep_result.columns]
    for _, row in sweep_result[display].iterrows():
        logger.info(f"  buy_roc_edge={row['buy_roc_edge']:.0f} roc_n={row['roc_n']:.0f}: return={row['total_return']:.2%} sharpe={row['sharpe_ratio']:.2f} dd={row['max_drawdown']:.2%}")

    logger.info("\n=== Walk-Forward 验证 ===")
    wf_result = validator.walk_forward(
        strategy_class=ROCStrategy, config_class=ROCConfig,
        data_feed=data_feed, codes=config.codes,
        start='2020-01-01', end='2025-12-31',
        train_years=3, test_years=1,
        param_ranges={'buy_roc_edge': [18, 20], 'roc_n': [15, 22]},
    )

    if not wf_result.empty:
        for _, row in wf_result.iterrows():
            oos_cols = [c for c in wf_result.columns if c.startswith('oos_')]
            logger.info(f"  窗口{row['window']}: {row['train_start']}~{row['train_end']}→{row['test_start']}~{row['test_end']}")
            for c in oos_cols:
                v = row[c]
                logger.info(f"    {c}: {v:.4f}" if isinstance(v, float) else f"    {c}: {v}")
    else:
        logger.warning("WF 无有效结果")


if __name__ == "__main__":
    main()
