import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from scipy import stats
from loguru import logger

from quantforge.core.data_feed import CachedDataFeed, DataRequest
from quantforge.data_sources.sina_feed import SinaFinanceFeed
from quantforge.strategies._configs.roc_config import ROCConfig
from quantforge.research.factor_lab import FactorLab


ROC_PERIODS = [5, 10, 15, 22, 44, 66, 120]
FORWARD_PERIODS = [5, 10, 22, 44, 66]
QUANTILE_BINS = [50, 60, 70, 75, 80, 85, 90, 95]


def _calc_roc(close: pd.Series, period: int) -> pd.Series:
    """ROC因子：当前价格 / N日前价格 - 1"""
    return close / close.shift(period) - 1


def _calc_forward_return(close: pd.Series, period: int) -> pd.Series:
    """未来持有期收益：N日后价格 / 当前价格 - 1"""
    return close.shift(-period) / close - 1


def run_ic_analysis():
    """ROC因子的IC分析主流程：IC矩阵扫描 → 分位数分析 → 衰减分析 → 分层回测"""
    config = ROCConfig(start_date="2020-01-01")

    # ==== 数据加载 ====
    data_feed = CachedDataFeed(
        source=SinaFinanceFeed(),
        cache_dir=os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'sina')
    )
    request = DataRequest(
        codes=config.codes,
        data_type=config.data_type,
        start=config.start_date,
        end=config.end_date,
    )
    response = data_feed.get_data(request)

    all_data = {}
    for code in config.codes:
        if code in response.bar_data and not response.bar_data[code].empty:
            df = response.bar_data[code].copy()
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date').sort_index()
            df = df[~df.index.duplicated(keep='last')]
            df['close'] = pd.to_numeric(df['close'], errors='coerce')
            df = df.dropna(subset=['close'])
            all_data[code] = df

    logger.info(f"加载 {len(all_data)}/{len(config.codes)} 只标的数据")

    # ==== 1. IC矩阵扫描：ROC回看期 × 未来持有期 ====

    logger.info("=" * 60)
    logger.info("IC分析：不同ROC回看期 vs 不同未来持有期")
    logger.info("=" * 60)

    ic_matrix, icir_matrix = FactorLab.ic_matrix_scan(
        factor_func=_calc_roc,
        close_data=all_data,
        factor_params=ROC_PERIODS,
        forward_periods=FORWARD_PERIODS,
    )

    logger.info("\nIC均值矩阵 (ROC回看期 × 未来持有期):")
    logger.info(f"{'':>6}" + "".join(f"{'F'+str(p):>8}" for p in FORWARD_PERIODS))
    for roc_p in ROC_PERIODS:
        row = f"R{roc_p:>4}"
        for fwd_p in FORWARD_PERIODS:
            val = ic_matrix.loc[roc_p, fwd_p]
            row += f"{val:>8.4f}"
        logger.info(row)

    logger.info("\nICIR矩阵 (IC均值/IC标准差):")
    logger.info(f"{'':>6}" + "".join(f"{'F'+str(p):>8}" for p in FORWARD_PERIODS))
    for roc_p in ROC_PERIODS:
        row = f"R{roc_p:>4}"
        for fwd_p in FORWARD_PERIODS:
            val = icir_matrix.loc[roc_p, fwd_p]
            row += f"{val:>8.4f}"
        logger.info(row)

    best_roc = icir_matrix.max(axis=1).idxmax()
    best_fwd = icir_matrix.loc[best_roc].idxmax()
    best_icir = icir_matrix.loc[best_roc, best_fwd]
    best_ic = ic_matrix.loc[best_roc, best_fwd]
    logger.info(f"\n最优参数: ROC回看期={best_roc}, 未来持有期={best_fwd}")
    logger.info(f"  IC均值={best_ic:.4f}, ICIR={best_icir:.4f}")
    logger.info(f"  当前参数: ROC回看期=22, IC均值={ic_matrix.loc[22, 22]:.4f}, ICIR={icir_matrix.loc[22, 22]:.4f}")

    # ==== 2. ROC分位数分析：为买卖阈值提供统计依据 ====

    logger.info("\n" + "=" * 60)
    logger.info("ROC分位数分析：为买卖阈值提供依据")
    logger.info("=" * 60)

    for roc_p in [best_roc, 22]:
        all_roc = []
        for code, df in all_data.items():
            if len(df) < roc_p + 50:
                continue
            roc = _calc_roc(df['close'], roc_p).dropna()
            all_roc.extend(roc.values.tolist())

        all_roc = np.array(all_roc)
        logger.info(f"\nROC(回看期={roc_p}) 分布统计:")
        logger.info(f"  样本数: {len(all_roc)}")
        logger.info(f"  均值: {np.mean(all_roc):.4f}")
        logger.info(f"  标准差: {np.std(all_roc):.4f}")
        logger.info(f"  中位数: {np.median(all_roc):.4f}")

        quantiles = [10, 25, 50, 75, 80, 85, 90, 95]
        logger.info(f"\n  分位数:")
        for q in quantiles:
            val = np.percentile(all_roc, q)
            logger.info(f"    P{q}: {val:.4f} ({val*100:.1f}%)")

        logger.info(f"\n  当前阈值在分布中的位置:")
        buy_pct = stats.percentileofscore(all_roc, 0.15)
        sell_pct = stats.percentileofscore(all_roc, 0.03)
        logger.info(f"    买入阈值15% → P{buy_pct:.1f} (只买最强{100-buy_pct:.1f}%的标的)")
        logger.info(f"    卖出阈值3% → P{sell_pct:.1f} (卖掉最弱{sell_pct:.1f}%的标的)")

        logger.info(f"\n  建议阈值（基于分位数）:")
        for q in QUANTILE_BINS:
            threshold = np.percentile(all_roc, q)
            logger.info(f"    买入阈值 P{q} = {threshold*100:.1f}%")

    # ==== 3. IC衰减分析：ROC预测力如何随时间流逝 ====

    logger.info("\n" + "=" * 60)
    logger.info("滚动IC衰减分析：ROC预测力随持有期的变化")
    logger.info("=" * 60)

    for roc_p in [best_roc, 22]:
        decay_data = []
        for fwd_p in range(1, 67):
            factor_vals = {}
            fwd_rets = {}
            for code, df in all_data.items():
                if len(df) < roc_p + fwd_p + 50:
                    continue
                close = df['close'].astype(float)
                fv = _calc_roc(close, roc_p)
                fr = _calc_forward_return(close, fwd_p)
                factor_vals[code] = fv
                fwd_rets[code] = fr

            result = FactorLab.compute_ic(factor_vals, fwd_rets)
            decay_data.append((fwd_p, result['ic_mean']))

        if decay_data:
            peak_period = max(decay_data, key=lambda x: abs(x[1]))
            zero_cross = None
            for i in range(1, len(decay_data)):
                if decay_data[i - 1][1] > 0 and decay_data[i][1] <= 0:
                    zero_cross = decay_data[i][0]
                    break
            logger.info(f"\nROC(回看期={roc_p}):")
            logger.info(f"  IC峰值: 持有期={peak_period[0]}天, IC={peak_period[1]:.4f}")
            if zero_cross:
                logger.info(f"  IC归零: 持有期={zero_cross}天 (动量效应消失)")
            else:
                logger.info(f"  IC在67天内未归零 (动量效应持续)")

    # ==== 4. 分层回测：因子值分组后的实际收益 ====

    logger.info("\n" + "=" * 60)
    logger.info("分层回测分析：ROC因子值的分组收益")
    logger.info("=" * 60)

    for roc_p in [best_roc, 22]:
        factor_vals = {}
        fwd_rets = {}
        for code, df in all_data.items():
            if len(df) < roc_p + 30:
                continue
            close = df['close'].astype(float)
            fv = _calc_roc(close, roc_p)
            fr = _calc_forward_return(close, roc_p)
            factor_vals[code] = fv
            fwd_rets[code] = fr

        lb_result = FactorLab.layered_backtest(factor_vals, fwd_rets, n_groups=5)
        logger.info(f"\nROC(回看期={roc_p}) 分层回测 (五组):")
        logger.info(f"  各组收益: {lb_result['group_returns']}")
        logger.info(f"  是否单调: {lb_result['is_monotonic']}")
        logger.info(f"  多空spread: {lb_result['spread']:.4f}")

    # ==== 5. 可视化：IC热力图 + 分布直方图 ====

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    ax1 = axes[0, 0]
    ic_vals = ic_matrix.values.astype(float)
    abs_max = max(abs(ic_vals.min()), abs(ic_vals.max())) if ic_vals.size > 0 else 1
    im1 = ax1.imshow(ic_vals, cmap='RdYlGn', aspect='auto', vmin=-abs_max, vmax=abs_max)
    ax1.set_xticks(range(len(FORWARD_PERIODS)))
    ax1.set_xticklabels([str(p) for p in FORWARD_PERIODS])
    ax1.set_yticks(range(len(ROC_PERIODS)))
    ax1.set_yticklabels([str(p) for p in ROC_PERIODS])
    ax1.set_xlabel('Forward Period')
    ax1.set_ylabel('ROC Lookback Period')
    ax1.set_title('IC Mean Matrix')
    for i in range(len(ROC_PERIODS)):
        for j in range(len(FORWARD_PERIODS)):
            ax1.text(j, i, f'{ic_matrix.iloc[i, j]:.3f}', ha='center', va='center', fontsize=8)
    plt.colorbar(im1, ax=ax1)

    ax2 = axes[0, 1]
    icir_vals = icir_matrix.values.astype(float)
    abs_max2 = max(abs(icir_vals.min()), abs(icir_vals.max())) if icir_vals.size > 0 else 1
    im2 = ax2.imshow(icir_vals, cmap='RdYlGn', aspect='auto', vmin=-abs_max2, vmax=abs_max2)
    ax2.set_xticks(range(len(FORWARD_PERIODS)))
    ax2.set_xticklabels([str(p) for p in FORWARD_PERIODS])
    ax2.set_yticks(range(len(ROC_PERIODS)))
    ax2.set_yticklabels([str(p) for p in ROC_PERIODS])
    ax2.set_xlabel('Forward Period')
    ax2.set_ylabel('ROC Lookback Period')
    ax2.set_title('ICIR Matrix')
    for i in range(len(ROC_PERIODS)):
        for j in range(len(FORWARD_PERIODS)):
            ax2.text(j, i, f'{icir_matrix.iloc[i, j]:.3f}', ha='center', va='center', fontsize=8)
    plt.colorbar(im2, ax=ax2)

    ax3 = axes[1, 0]
    for roc_p in [5, 22, 44, 66]:
        if roc_p in ic_matrix.index:
            ax3.plot(FORWARD_PERIODS, ic_matrix.loc[roc_p].values.astype(float), marker='o', label=f'ROC={roc_p}')
    ax3.axhline(y=0, color='black', linestyle='--', linewidth=0.5)
    ax3.set_xlabel('Forward Period (days)')
    ax3.set_ylabel('IC Mean')
    ax3.set_title('IC vs Forward Period')
    ax3.legend()
    ax3.grid(True, alpha=0.3)

    ax4 = axes[1, 1]
    for roc_p in [best_roc, 22]:
        all_roc_vals = []
        for code, df in all_data.items():
            if len(df) < roc_p + 50:
                continue
            roc = _calc_roc(df['close'], roc_p).dropna()
            all_roc_vals.extend(roc.values.tolist())
        ax4.hist(all_roc_vals, bins=100, alpha=0.6, label=f'ROC={roc_p}', density=True)
        if roc_p == 22:
            ax4.axvline(x=0.15, color='red', linestyle='--', label='Buy=15%')
            ax4.axvline(x=0.03, color='green', linestyle='--', label='Sell=3%')
    ax4.set_xlabel('ROC Value')
    ax4.set_ylabel('Density')
    ax4.set_title('ROC Distribution')
    ax4.legend()
    ax4.grid(True, alpha=0.3)

    plt.tight_layout()

    results_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'results')
    os.makedirs(results_dir, exist_ok=True)
    fig_path = os.path.join(results_dir, 'ic_analysis.png')
    plt.savefig(fig_path, dpi=150)
    logger.info(f"\nIC分析图表已保存: {fig_path}")

    ic_matrix.to_csv(os.path.join(results_dir, 'ic_matrix.csv'))
    icir_matrix.to_csv(os.path.join(results_dir, 'icir_matrix.csv'))
    logger.info(f"IC矩阵已保存: {os.path.join(results_dir, 'ic_matrix.csv')}")
    logger.info(f"ICIR矩阵已保存: {os.path.join(results_dir, 'icir_matrix.csv')}")


if __name__ == "__main__":
    run_ic_analysis()
