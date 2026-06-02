import numpy as np
import pandas as pd
from scipy import stats
from loguru import logger


class FactorLab:
    """因子分析实验室：计算IC/ICIR、分层回测、IC矩阵扫描"""

    @staticmethod
    def compute_ic(factor_values: dict[str, pd.Series],
                   forward_returns: dict[str, pd.Series],
                   method: str = 'spearman') -> dict:
        """计算因子信息系数（IC）和ICIR。

        IC（Information Coefficient）= 因子值与未来收益的相关系数
        ICIR = IC均值 / IC标准差，衡量IC的稳定性

        Args:
            factor_values: {标的代码: 因子值序列}，所有序列必须时间对齐
            forward_returns: {标的代码: 未来收益序列}，索引与factor_values对应
            method: 'spearman' 或 'pearson'，默认spearman（更稳健，不受异常值影响）

        Returns:
            ic_mean, ic_std, icir, ic_positive_ratio（正值IC占比）, ic_series（每个标的的IC值）
        """
        ic_list = []
        for code in factor_values:
            if code not in forward_returns:
                continue
            fv = factor_values[code]
            fr = forward_returns[code]
            valid = fv.notna() & fr.notna()
            # 最少30个有效样本才有统计意义
            if valid.sum() < 30:
                continue
            if method == 'spearman':
                ic_val, _ = stats.spearmanr(fv[valid], fr[valid])
            elif method == 'pearson':
                ic_val, _ = stats.pearsonr(fv[valid], fr[valid])
            else:
                raise ValueError(f"不支持的IC方法: {method}")
            if not np.isnan(ic_val):
                ic_list.append(ic_val)

        if not ic_list:
            return {
                'ic_mean': 0.0, 'ic_std': 0.0, 'icir': 0.0,
                'ic_positive_ratio': 0.0, 'ic_series': pd.Series(dtype=float),
            }

        ic_series = pd.Series(ic_list)
        ic_mean = float(ic_series.mean())
        ic_std = float(ic_series.std(ddof=1)) if len(ic_series) > 1 else 1.0
        icir = ic_mean / ic_std if ic_std > 0 else 0.0
        ic_positive_ratio = float((ic_series > 0).mean())

        return {
            'ic_mean': ic_mean,
            'ic_std': ic_std,
            'icir': icir,
            'ic_positive_ratio': ic_positive_ratio,
            'ic_series': ic_series,
        }

    @staticmethod
    def layered_backtest(factor_values: dict[str, pd.Series],
                         forward_returns: dict[str, pd.Series],
                         n_groups: int = 5) -> dict:
        """分层回测：按因子值分组，验证各组收益是否单调递增/递减。

        将所有标的的(因子值, 未来收益)对跨时间池化，
        按因子值排序后等分N组，对比最高组与最低组的收益差（spread）。

        Args:
            n_groups: 分组数，默认5组。组数太多会稀释每组样本量
        """
        # 跨标的、跨时间池化所有(因子值, 收益)对
        all_pairs = []
        for code in factor_values:
            if code not in forward_returns:
                continue
            fv = factor_values[code]
            fr = forward_returns[code]
            valid = fv.notna() & fr.notna()
            if valid.sum() < 10:
                continue
            for factor_val, ret_val in zip(fv[valid].values, fr[valid].values):
                all_pairs.append((float(factor_val), float(ret_val)))

        if len(all_pairs) < n_groups * 10:
            logger.warning(f"样本量不足: {len(all_pairs)} < {n_groups * 10}，分层回测结果不可靠")
            return {'group_returns': {}, 'is_monotonic': False, 'spread': 0.0}

        # 按因子值升序排列，等分N组，计算每组平均收益
        all_pairs.sort(key=lambda x: x[0])
        group_size = len(all_pairs) // n_groups
        group_returns = {}
        for g in range(n_groups):
            start = g * group_size
            end = start + group_size if g < n_groups - 1 else len(all_pairs)
            group_ret = np.mean([p[1] for p in all_pairs[start:end]])
            group_returns[g + 1] = float(group_ret)

        rets = list(group_returns.values())

        # 检查单调性：从第1组到第N组是否严格单向变化
        is_monotonic = True
        direction = 1 if rets[-1] >= rets[0] else -1
        for i in range(len(rets) - 1):
            if (rets[i + 1] - rets[i]) * direction < 0:
                is_monotonic = False
                break

        spread = rets[-1] - rets[0]
        return {
            'group_returns': group_returns,
            'is_monotonic': is_monotonic,
            'spread': spread,
        }

    @staticmethod
    def ic_matrix_scan(factor_func,
                       close_data: dict[str, pd.DataFrame],
                       factor_params: list,
                       forward_periods: list[int]) -> tuple[pd.DataFrame, pd.DataFrame]:
        """二维IC矩阵扫描：对不同因子参数 × 未来持有期做网格化IC计算。

        Args:
            factor_func: 因子计算函数，签名为 (close_series, param) -> factor_series
            close_data: {标的代码: DataFrame(含'close'列)}
            factor_params: 因子参数的候选值列表（如不同ROC回看期）
            forward_periods: 未来持有期的候选值列表（如5、10、22天）

        Returns:
            (ic_matrix, icir_matrix)，行列分别为factor_params × forward_periods
        """
        ic_matrix = pd.DataFrame(index=factor_params, columns=forward_periods, dtype=float)
        icir_matrix = pd.DataFrame(index=factor_params, columns=forward_periods, dtype=float)

        for param in factor_params:
            for fwd_p in forward_periods:
                factor_vals = {}
                fwd_rets = {}
                for code, df in close_data.items():
                    if len(df) < param + fwd_p + 30:
                        continue
                    close = df['close'].astype(float)
                    fv = factor_func(close, param)
                    fr = close.shift(-fwd_p) / close - 1
                    factor_vals[code] = fv
                    fwd_rets[code] = fr

                result = FactorLab.compute_ic(factor_vals, fwd_rets)
                ic_matrix.loc[param, fwd_p] = result['ic_mean']
                icir_matrix.loc[param, fwd_p] = result['icir']

        return ic_matrix, icir_matrix
