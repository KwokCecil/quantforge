from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional
import os

import pandas as pd

from quantforge.tools.json_tool import read_fund_data, write_fund_data, read_batch_params, write_batch_params
from loguru import logger


@dataclass
class DataRequest:
    codes: list[str]
    data_type: str
    start: str
    end: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class DataResponse:
    bar_data: dict[str, pd.DataFrame] = field(default_factory=dict)
    macro_data: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class DataFeed(ABC):
    @abstractmethod
    def get_data(self, request: DataRequest) -> DataResponse:
        ...


class CachedDataFeed(DataFeed):
    def __init__(self, source: DataFeed, cache_dir: str):
        self.source = source
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    def get_data(self, request: DataRequest) -> DataResponse:
        response = DataResponse()
        for code in request.codes:
            cached = read_fund_data(self.cache_dir, code)
            if cached is not None and len(cached) > 0:
                df = pd.DataFrame(cached)
                if 'date' in df.columns:
                    df['date'] = df['date'].astype(str)
                    df = df.sort_values('date').reset_index(drop=True)
                    mask = (df['date'] >= request.start) & (df['date'] <= request.end)
                    response.bar_data[code] = df[mask].reset_index(drop=True)
                else:
                    response.bar_data[code] = df
            else:
                response.bar_data[code] = pd.DataFrame()
        return response

    def update_cache(self, codes: list[str], data_type: str, start: str, end: str, days: int = 5000):
        batch_params = read_batch_params(self.cache_dir) or {}

        for code in codes:
            need_full = self._need_full_update(batch_params, code, start, end)

            if need_full:
                logger.info(f"{code} 全量更新缓存")
                raw = self.source.get_data(DataRequest(
                    codes=[code], data_type=data_type,
                    start=start, end=end, params={'days': days}
                ))
                df = raw.bar_data.get(code, pd.DataFrame())
            else:
                df = self._incremental_update(code, start, end, data_type)

            if not df.empty:
                self._save_cache(code, df, batch_params)

    def _incremental_update(self, code: str, start: str, end: str, data_type: str) -> pd.DataFrame:
        """增量更新：拉取窗口数据 → 计算+核验修正系数 → 校正历史 → 合并。

        核验不通过时：尝试追加仅新日期（旧数据不动），保证数据时效性。
        仅当无新增日期时才跳过更新。用户需根据日志确认是否需手动全量刷新。
        """
        fetch_window = self._calc_incremental_window(code, end)
        raw = self.source.get_data(DataRequest(
            codes=[code], data_type=data_type,
            start=start, end=end, params={'days': fetch_window}
        ))
        df_new = raw.bar_data.get(code, pd.DataFrame())
        if df_new.empty:
            return pd.DataFrame()

        cached = read_fund_data(self.cache_dir, code)
        df_old = pd.DataFrame(cached) if cached else pd.DataFrame()

        if df_old.empty or 'date' not in df_old.columns or 'close' not in df_old.columns:
            return df_new

        df_old['date'] = df_old['date'].astype(str)
        df_new['date'] = df_new['date'].astype(str)

        correction_ratio = self._compute_fq_correction(df_old, df_new, code)
        if correction_ratio is None:
            old_max_date = df_old['date'].max()
            new_only = df_new[df_new['date'] > old_max_date]
            if new_only.empty:
                logger.error(
                    f"{code} 复权修正核验失败且无新增日期 → 跳过本次更新，缓存保持原样。"
                    f"请手工确认是否需要全量刷新（删除缓存文件后重新运行）。"
                )
                return pd.DataFrame()
            df = pd.concat([df_old, new_only], ignore_index=True)
            df = df.sort_values('date').reset_index(drop=True)
            logger.error(
                f"{code} 复权修正核验失败，但已追加 {len(new_only)} 条新增日期（"
                f"{new_only['date'].iloc[0]} ~ {new_only['date'].iloc[-1]}）。"
                f"历史价格未修正，可能存在不连续性。请手工确认是否需要全量刷新。"
            )
            return df

        if correction_ratio != 1.0:
            df_old = self._apply_correction_ratio(df_old, correction_ratio, code)

        df = pd.concat([df_old, df_new], ignore_index=True)
        df = df.drop_duplicates(subset=['date'], keep='last')
        df = df.sort_values('date').reset_index(drop=True)
        return df

    def _save_cache(self, code: str, df: pd.DataFrame, batch_params: dict):
        records = df.to_dict(orient='records')
        write_fund_data(self.cache_dir, code, records)

        if 'date' in df.columns:
            min_date = df['date'].min()
            max_date = df['date'].max()
        else:
            min_date = ''
            max_date = ''

        if 'fund_actual_date_ranges' not in batch_params:
            batch_params['fund_actual_date_ranges'] = {}
        batch_params['fund_actual_date_ranges'][code] = {
            'min_date': str(min_date),
            'max_date': str(max_date),
        }
        write_batch_params(self.cache_dir, batch_params)

        # 数据质量检查钩子（T026）
        try:
            from quantforge.tools.data_quality import check_data_quality
            qr = check_data_quality(df, code)
            if qr['error_count'] > 0:
                logger.warning(f"{code} 数据质量检查发现{ qr['error_count']}项错误: {[c['detail'] for c in qr['checks'] if c['status']=='🔴 错误']}")
            elif qr['warn_count'] > 0:
                logger.debug(f"{code} 质量检查: {qr['warn_count']}项警告 (日期跳变/停牌等)")
        except Exception:
            pass

    def _need_full_update(self, batch_params: dict, code: str, start: str, end: str) -> bool:
        ranges = batch_params.get('fund_actual_date_ranges', {}).get(code, {})
        if not ranges:
            return True

        from datetime import datetime, timedelta

        cached_min = ranges.get('min_date', '')
        cached_max = ranges.get('max_date', '')

        # 先检查 max：缓存数据是否已过时
        max_is_fresh = False
        if cached_max and end:
            try:
                max_dt = datetime.strptime(cached_max, '%Y-%m-%d')
                end_dt = datetime.strptime(end, '%Y-%m-%d')
                today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                ref_dt = min(end_dt, today)
                gap = (ref_dt - max_dt).days
                if gap > 30:
                    logger.warning(
                        f"{code} 缓存严重过时（缓存最新={cached_max}，"
                        f"参考日期={ref_dt.strftime('%Y-%m-%d')}，"
                        f"差距={gap}天），触发全量更新"
                    )
                else:
                    max_is_fresh = True
            except ValueError:
                pass

        if not max_is_fresh:
            return True

        # max 已是最新，再检查 min：如果 start 远早于 cache_min，
        # 说明 ETF 上市日晚于回测起始日，没有更早的数据可拉，不应触发全量更新
        if cached_min > start:
            try:
                min_dt = datetime.strptime(cached_min, '%Y-%m-%d')
                start_dt = datetime.strptime(start, '%Y-%m-%d')
                if (min_dt - start_dt) > timedelta(days=5):
                    return False
            except ValueError:
                pass

        return False

    def _calc_incremental_window(self, code: str, end: str) -> int:
        """根据距上次缓存的天数，动态计算增量拉取的窗口大小。

        三级：
        - gap ≤ 30   → 30天（日常）
        - gap ≤ 120  → 120天（月度～季度）
        - gap > 120  → gap × 1.5（覆盖缓存缺口 + 缓冲重叠区）

        始终返回有效窗口，不复用全量路径——全量仅由价格偏移检测触发。
        """
        ranges = (read_batch_params(self.cache_dir) or {}).get('fund_actual_date_ranges', {}).get(code, {})
        cached_max = ranges.get('max_date', '')
        if not cached_max:
            return 120

        try:
            from datetime import datetime
            max_dt = datetime.strptime(cached_max, '%Y-%m-%d')
            end_dt = datetime.strptime(end, '%Y-%m-%d')
            gap = (end_dt - max_dt).days
        except ValueError:
            return 120

        if gap <= 0:
            return 30
        elif gap <= 30:
            return 30
        elif gap <= 120:
            return 120
        else:
            return int(gap * 1.5)

    def _compute_fq_correction(self, df_old: pd.DataFrame, df_new: pd.DataFrame, code: str) -> float | None:
        """计算前复权修正系数，含分段一致性核验。

        前复权调整是全局等比的：每次分红使"全部历史价格"乘以 (1-分红率)。
        N次分红的总效应 = ∏(1-d_i)，一个乘数足够。

        核验：将重叠区分三段独立计算比率，三段一致才能确认是纯前复权偏移。
        若不一致 → 仅报告详细信息，不自动操作。

        Returns:
            修正系数；None=核验不通过需人工决策；1.0=无需修正。
        """
        old_map = dict(zip(df_old['date'], pd.to_numeric(df_old['close'], errors='coerce')))
        new_indexed = df_new.set_index('date')

        ratios = []
        for d in df_new['date']:
            if d not in old_map:
                continue
            old_c = old_map[d]
            if pd.isna(old_c) or old_c == 0:
                continue
            new_c = pd.to_numeric(new_indexed.loc[d, 'close'], errors='coerce')
            if pd.isna(new_c) or new_c == 0:
                continue
            ratios.append((d, new_c / old_c))

        if len(ratios) < 15:
            logger.debug(f"{code} 重叠日不足 ({len(ratios)}天，需≥15)，跳过复权修正")
            return 1.0

        # 整体中位数
        all_vals = [r for _, r in ratios]
        overall = float(pd.Series(all_vals).median())

        # 合理性检查：前复权偏移应 ≤1（历史价格被压低），>1 意味着价格"升高"，异常
        if overall > 1.002:
            logger.warning(f"{code} 复权系数 >1 ({overall:.6f})，疑似非复权偏移，需人工确认")
            return None
        if overall < 0.85:
            logger.warning(f"{code} 复权系数异常偏低 ({overall:.6f})，需人工确认")
            return None
        if abs(overall - 1.0) < 0.0002:
            return 1.0

        # 分段核验：切三等分，各自取中位数
        n = len(ratios)
        seg_size = n // 3
        seg_results = []
        for k in range(3):
            start_i = k * seg_size
            end_i = (k + 1) * seg_size if k < 2 else n
            seg_vals = [r for _, r in ratios[start_i:end_i]]
            seg_med = float(pd.Series(seg_vals).median())
            seg_results.append(seg_med)

        max_dev = max(abs(r - overall) / overall for r in seg_results)
        if max_dev > 0.001:
            logger.warning(
                f"{code} 分段核验失败: 整体={overall:.6f} "
                f"前段={seg_results[0]:.6f} 中段={seg_results[1]:.6f} 后段={seg_results[2]:.6f} "
                f"最大偏差={max_dev:.4%} > 0.1%"
            )
            return None

        logger.info(
            f"{code} 前复权修正系数={overall:.6f} ({n}天重叠, "
            f"分段核验通过  ({seg_results[0]:.6f} {seg_results[1]:.6f} {seg_results[2]:.6f})"
        )
        return overall

    def _apply_correction_ratio(self, df: pd.DataFrame, ratio: float, code: str) -> pd.DataFrame:
        """将修正系数等比应用到全部历史 OHLC 价格列。"""
        for col in ['open', 'high', 'low', 'close']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce') * ratio
        logger.debug(f"{code} 历史价格已整体修正 ×{ratio:.6f}")
        return df


_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def create_cached_feed(source_cls, cache_dir: Optional[str] = None) -> CachedDataFeed:
    """创建 CachedDataFeed 实例。Backtest 和 Monitor 共用。"""
    if cache_dir is None:
        cache_dir = os.path.join(_BASE_DIR, 'data', 'sina')
    return CachedDataFeed(source=source_cls(), cache_dir=cache_dir)
