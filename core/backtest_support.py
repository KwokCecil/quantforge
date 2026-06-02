"""回测支撑工具 —— 后处理与可视化，不影响回测结果。

包含:
- BacktestAnalyzer: 绩效指标计算 + 报告生成 + 单策略图表
- BacktestComparator: 多策略对比可视化
"""

import json
import os
from datetime import datetime
from typing import Any, Optional

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
from loguru import logger

from quantforge.core.executor import BacktestExecutor

plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

DEFAULT_COLORS = [
    '#2196F3', '#4CAF50', '#FF9800', '#9C27B0', '#F44336',
    '#00BCD4', '#795548', '#607D8B', '#E91E63', '#3F51B5',
]

_FLAG_LABELS = {
    'STRICT_BUY': ('严格买入', '❌'),
    'MA_PRICE_CROSS': ('均线穿越', '✅'),
    'ROC_MA_DIRECTION': ('ROC均线方向', '❌'),
    'CUT_LOSS': ('成本止损', '✅'),
    'HIGH_WATERMARK_STOP': ('高点回落止损', '❌'),
    'BUY_AVERAGE': ('等额分配', '⚠️'),
    'REBALANCE': ('调仓减仓', '⚠️'),
    'CROWDED_SELL': ('拥挤卖出', '❌'),
    'STOP_SMALL_TRADE': ('小额交易过滤', '✅'),
}


class BacktestAnalyzer:
    """回测结果分析器。从BacktestExecutor的执行结果中计算各项绩效指标，并绘制净值/回撤曲线。"""
    def analyze(self, executor: BacktestExecutor, benchmark_series: pd.Series = None,
                benchmark_name: str = "基准", code_names: Optional[dict[str, str]] = None,
                strategy_config=None, save_dir: Optional[str] = None) -> dict:
        results = executor.get_results()
        net_values = results.get('net_values', [])
        trade_log = results.get('trade_log', [])

        if not net_values:
            return {}

        nv_df = pd.DataFrame(net_values)

        total_return = (nv_df['total_value'].iloc[-1] / executor.initial_capital) - 1

        trading_days = len(nv_df)
        years = trading_days / 252
        annual_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0

        nv_df['peak'] = nv_df['total_value'].cummax()
        nv_df['drawdown'] = (nv_df['peak'] - nv_df['total_value']) / nv_df['peak']
        max_drawdown = nv_df['drawdown'].max()

        dd_peak_idx = nv_df['drawdown'].idxmax()
        if dd_peak_idx > 0:
            peak_date = nv_df.loc[:dd_peak_idx, 'total_value'].idxmax()
            trough_date = dd_peak_idx
            max_drawdown_duration = trough_date - peak_date
        else:
            max_drawdown_duration = 0

        daily_returns = nv_df['net_value'].pct_change().dropna()
        sharpe_ratio = 0.0
        sortino_ratio = 0.0
        if len(daily_returns) > 1 and daily_returns.std() > 0:
            sharpe_ratio = (daily_returns.mean() / daily_returns.std()) * np.sqrt(252)
            downside = daily_returns[daily_returns < 0]
            downside_std = downside.std() if len(downside) > 0 else daily_returns.std()
            if downside_std > 0:
                sortino_ratio = (daily_returns.mean() / downside_std) * np.sqrt(252)

        sells = [t for t in trade_log if t['action'] == 'sell']
        buy_queues: dict[str, list[dict]] = {}
        for t in trade_log:
            if t['action'] == 'buy':
                buy_queues.setdefault(t['code'], []).append({
                    'actual_price': t['actual_price'],
                    'commission': t['commission'],
                    'remaining': t['shares'],
                })

        trade_profits = []
        for sell in sells:
            code = sell['code']
            remaining_sell = sell['shares']
            total_profit = 0.0
            queue = buy_queues.get(code, [])
            while remaining_sell > 0 and queue:
                head = queue[0]
                match_shares = min(remaining_sell, head['remaining'])
                buy_commission_share = head['commission'] * (match_shares / head['remaining']) if head['remaining'] > 0 else 0
                profit = (sell['actual_price'] - head['actual_price']) * match_shares - buy_commission_share
                total_profit += profit
                head['remaining'] -= match_shares
                head['commission'] -= buy_commission_share
                remaining_sell -= match_shares
                if head['remaining'] <= 0:
                    queue.pop(0)
            sell_commission_share = sell['commission'] * (1 - remaining_sell / sell['shares']) if sell['shares'] > 0 else sell['commission']
            total_profit -= sell_commission_share
            trade_profits.append(total_profit)

        wins = [p for p in trade_profits if p > 0]
        losses = [p for p in trade_profits if p <= 0]

        trade_count = len(sells)
        win_rate = len(wins) / len(trade_profits) if trade_profits else 0

        avg_win = np.mean(wins) if wins else 0
        avg_lose = abs(np.mean(losses)) if losses else 0

        profit_factor = avg_win / avg_lose if avg_lose > 0 else float('inf')

        b = avg_win / avg_lose if avg_lose > 0 else 0
        p = win_rate
        q = 1 - p
        kelly_position = (b * p - q) / b if b > 0 else 0

        excess_return = 0.0
        benchmark_return = 0.0
        benchmark_annual = 0.0
        if benchmark_series is not None and not benchmark_series.empty:
            benchmark_return = benchmark_series.iloc[-1] / benchmark_series.iloc[0] - 1
            benchmark_years = len(benchmark_series) / 252
            benchmark_annual = (1 + benchmark_return) ** (1 / benchmark_years) - 1 if benchmark_years > 0 else 0
            excess_return = total_return - benchmark_return

        analysis = {
            'total_return': total_return,
            'annual_return': annual_return,
            'max_drawdown': max_drawdown,
            'max_drawdown_duration': int(max_drawdown_duration),
            'sharpe_ratio': sharpe_ratio,
            'sortino_ratio': sortino_ratio,
            'trade_count': trade_count,
            'win_rate': win_rate,
            'avg_win': avg_win,
            'avg_lose': avg_lose,
            'profit_factor': profit_factor,
            'kelly_position': kelly_position,
            'total_commission': results.get('total_commission', 0),
            'total_slippage': results.get('total_slippage', 0),
            'benchmark_return': benchmark_return,
            'benchmark_annual': benchmark_annual,
            'excess_return': excess_return,
        }

        logger.info("=" * 60)
        logger.info("回测分析报告")
        logger.info("=" * 60)
        logger.info(f"总收益率:     {total_return:.2%}")
        logger.info(f"年化收益率:   {annual_return:.2%}")
        logger.info(f"最大回撤:     {max_drawdown:.2%}")
        logger.info(f"Sharpe比率:   {sharpe_ratio:.2f}")
        logger.info(f"Sortino比率:  {sortino_ratio:.2f}")
        logger.info(f"交易次数:     {trade_count}")
        logger.info(f"胜率:         {win_rate:.2%}")
        logger.info(f"盈亏比:       {profit_factor:.2f}")
        logger.info(f"Kelly仓位:    {kelly_position:.2%}")
        logger.info(f"总佣金:       {results.get('total_commission', 0):.2f}")
        logger.info(f"总滑点:       {results.get('total_slippage', 0):.2f}")
        if benchmark_series is not None:
            logger.info(f"基准收益:     {benchmark_return:.2%}")
            logger.info(f"基准年化:     {benchmark_annual:.2%}")
            logger.info(f"超额收益:     {excess_return:.2%}")
        logger.info("=" * 60)

        if save_dir:
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            run_dir = os.path.join(save_dir, f"run_{ts}")
            os.makedirs(run_dir, exist_ok=True)

            from quantforge.research.versioning import save_git_info, update_run_index
            repo_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            git_info = save_git_info(run_dir, repo_path)

            self._save_json(analysis, trade_log, nv_df, code_names, benchmark_name, strategy_config, run_dir)
            self._save_report_md(analysis, trade_log, nv_df, code_names, benchmark_name, strategy_config, run_dir, git_info)
            self._plot(nv_df, trade_log, analysis, code_names, benchmark_series, benchmark_name, run_dir)

            run_info = {
                'run_id': os.path.basename(run_dir),
                'sha': git_info['sha'],
                'branch': git_info['branch'],
                'dirty': git_info['dirty'],
                'timestamp': git_info['timestamp'],
                'strategy': strategy_config.strategy_name if strategy_config else 'unknown',
                'total_return': round(float(analysis.get('total_return', 0)), 4),
                'sharpe_ratio': round(float(analysis.get('sharpe_ratio', 0)), 2),
                'max_drawdown': round(float(analysis.get('max_drawdown', 0)), 4),
                'trade_count': int(analysis.get('trade_count', 0)),
            }
            update_run_index(run_dir, run_info, save_dir)

        return analysis

    def _save_json(self, analysis, trade_log, nv_df, code_names, benchmark_name, strategy_config, run_dir):
        report: dict[str, Any] = {}
        for k, v in analysis.items():
            if isinstance(v, (np.integer,)):
                report[k] = int(v)
            elif isinstance(v, (np.floating,)):
                report[k] = float(v)
            else:
                report[k] = v
        report['benchmark_name'] = benchmark_name
        if strategy_config is not None:
            config_dict = strategy_config.to_dict()
            config_dict_serializable = {}
            for k, v in config_dict.items():
                if isinstance(v, (list, dict, str, int, float, bool)) or v is None:
                    config_dict_serializable[k] = v
                else:
                    config_dict_serializable[k] = str(v)
            report['strategy_config'] = config_dict_serializable

        with open(os.path.join(run_dir, 'report.json'), 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        named_trades = []
        for t in trade_log:
            entry = dict(t)
            if code_names and entry.get('code'):
                entry['name'] = code_names.get(entry['code'], entry['code'])
            named_trades.append(entry)
        with open(os.path.join(run_dir, 'trades.json'), 'w', encoding='utf-8') as f:
            json.dump(named_trades, f, ensure_ascii=False, indent=2)

        nv_df.to_csv(os.path.join(run_dir, 'net_values.csv'), index=False, encoding='utf-8')

    def _save_report_md(self, analysis, trade_log, nv_df, code_names, benchmark_name, strategy_config, run_dir, git_info: Optional[dict] = None):
        """生成中文可读的 Markdown 报告。"""
        lines = []
        lines.append("# 回测分析报告")
        lines.append("")

        if git_info:
            lines.append(f"**代码版本**：`{git_info['sha']}` ({git_info['branch']})")
            if git_info.get('dirty'):
                lines.append("")
                lines.append("> ⚠️ **警告**：当前工作目录有未提交的修改！回测结果可能无法精确复现。")
        lines.append(f"运行时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")

        lines.append("## 绩效指标")
        lines.append("")
        lines.append("| 指标 | 策略 | 基准 |")
        lines.append("|------|------|------|")
        lines.append(f"| 总收益率 | **{analysis['total_return']:.2%}** | {analysis['benchmark_return']:.2%} |")
        lines.append(f"| 年化收益率 | **{analysis['annual_return']:.2%}** | {analysis['benchmark_annual']:.2%} |")
        lines.append(f"| 超额收益 | **{analysis['excess_return']:.2%}** | — |")
        lines.append(f"| 最大回撤 | {analysis['max_drawdown']:.2%} | — |")
        lines.append(f"| Sharpe比率 | {analysis['sharpe_ratio']:.2f} | — |")
        lines.append(f"| Sortino比率 | {analysis['sortino_ratio']:.2f} | — |")
        lines.append(f"| 交易次数 | {analysis['trade_count']} | — |")
        lines.append(f"| 胜率 | {analysis['win_rate']:.2%} | — |")
        lines.append(f"| 盈亏比 | {analysis['profit_factor']:.2f} | — |")
        lines.append(f"| Kelly仓位 | {analysis['kelly_position']:.2%} | — |")
        lines.append(f"| 总佣金 | {analysis['total_commission']:.2f} | — |")
        lines.append(f"| 总滑点 | {analysis['total_slippage']:.2f} | — |")
        lines.append("")

        if strategy_config is not None:
            config_dict = strategy_config.to_dict()

            lines.append("## 策略参数")
            lines.append("")
            lines.append("| 参数 | 值 |")
            lines.append("|------|-----|")
            param_labels = {
                'strategy_name': '策略名称', 'start_date': '开始日期', 'end_date': '结束日期',
                'roc_n': 'ROC周期', 'roc_m': 'ROC均线周期', 'ma_period': '均线周期',
                'buy_roc_edge': '买入ROC阈值', 'sell_roc_edge': '卖出ROC阈值',
                'sell_ma_roc_edge': '卖出MAROC阈值', 'top_k': 'TOP_K',
                'initial_capital': '初始资金', 'buy_max_ratio': '买入上限比例',
                'cut_loss_edge': '止损线', 'high_watermark_stop_edge': '高点回落线',
                'crowded_position_ratio': '拥挤持仓比例', 'skip_small_trade_limit': '小额交易线',
                'benchmark_code': '基准代码', 'data_type': '数据类型',
            }
            for k, v in config_dict.items():
                if k in ('codes', 'code_names'):
                    continue
                label = param_labels.get(k, k)
                if isinstance(v, float):
                    lines.append(f"| {label} | {v:.4f} |")
                else:
                    lines.append(f"| {label} | {v} |")
            lines.append("")

            lines.append("## 条件开关")
            lines.append("")
            lines.append("| 开关 | 状态 | 有效性 | 说明 |")
            lines.append("|------|------|--------|------|")
            flag_docs = {
                'STRICT_BUY': 'ROC刚突破阈值才买入',
                'MA_PRICE_CROSS': '价格在均线之上才可买入',
                'ROC_MA_DIRECTION': 'ROC均线上升才可买入',
                'CUT_LOSS': '成本止损',
                'HIGH_WATERMARK_STOP': '高点回落止损',
                'BUY_AVERAGE': '等额分配（否则按ROC权重）',
                'REBALANCE': '调仓减仓（持仓超目标权重时部分卖出）',
                'CROWDED_SELL': '买入拥挤时部分卖出过大持仓',
                'STOP_SMALL_TRADE': '过滤小额交易',
            }
            for flag_name, desc in flag_docs.items():
                val = config_dict.get(flag_name, False)
                status = "✅ 开启" if val else "⬜ 关闭"
                label_info = _FLAG_LABELS.get(flag_name, ('', ''))
                validity = label_info[1] if label_info else ''
                lines.append(f"| {desc} | {status} | {validity} | {flag_name}={val} |")
            lines.append("")

            codes = config_dict.get('codes', [])
            cn = config_dict.get('code_names', {})
            if codes:
                lines.append("## 标的池")
                lines.append("")
                for c in codes:
                    name = cn.get(c, c) if isinstance(cn, dict) else c
                    lines.append(f"- {c} ({name})")
                lines.append("")

        sells = [t for t in trade_log if t['action'] == 'sell']
        buys = [t for t in trade_log if t['action'] == 'buy']
        if sells or buys:
            buy_queues: dict[str, list[dict]] = {}
            for t in trade_log:
                if t['action'] == 'buy':
                    buy_queues.setdefault(t['code'], []).append({
                        'actual_price': t['actual_price'],
                        'commission': t['commission'],
                        'remaining': t['shares'],
                    })

            cash_lookup = {}
            if not nv_df.empty and 'date' in nv_df.columns and 'cash' in nv_df.columns:
                for _, row in nv_df.iterrows():
                    d = str(row['date'])[:10]
                    cash_lookup[d] = row['cash']

            lines.append("## 交易记录")
            lines.append("")

            trade_profits = []
            code_stats: dict[str, list] = {}
            current_date = None
            for t in trade_log:
                trade_date = t.get('date', '')[:10] if t.get('date') else ''
                if trade_date != current_date:
                    if current_date and current_date in cash_lookup:
                        lines.append(f"  💰 现金 ¥{cash_lookup[current_date]:,.0f}")
                        lines.append("")
                    current_date = trade_date
                    lines.append(f"### {current_date}")
                    lines.append("")

                action = "买入" if t['action'] == 'buy' else "卖出"
                name = code_names.get(t['code'], t['code']) if code_names else t['code']
                total_value = t['shares'] * t['actual_price']
                price_str = f"@{t['actual_price']:.3f}"

                profit_str = ""
                if t['action'] == 'sell':
                    code = t['code']
                    remaining_sell = t['shares']
                    total_profit = 0.0
                    total_cost = 0.0
                    queue = buy_queues.get(code, [])
                    while remaining_sell > 0 and queue:
                        head = queue[0]
                        match_shares = min(remaining_sell, head['remaining'])
                        buy_commission_share = head['commission'] * (match_shares / head['remaining']) if head['remaining'] > 0 else 0
                        profit = (t['actual_price'] - head['actual_price']) * match_shares - buy_commission_share
                        cost = head['actual_price'] * match_shares + buy_commission_share
                        total_profit += profit
                        total_cost += cost
                        head['remaining'] -= match_shares
                        head['commission'] -= buy_commission_share
                        remaining_sell -= match_shares
                        if head['remaining'] <= 0:
                            queue.pop(0)
                    sell_commission_share = t['commission'] * (1 - remaining_sell / t['shares']) if t['shares'] > 0 else t['commission']
                    total_profit -= sell_commission_share
                    total_cost += sell_commission_share

                    if total_cost > 0 or total_profit != 0:
                        rate = total_profit / total_cost if total_cost > 0 else 0
                        profit_str = f" 收益{total_profit:+,.0f}({rate:+.1%})"
                        trade_profits.append(total_profit)

                    code_stats.setdefault(code, []).append(total_profit)

                lines.append(f"- {action} {name} ¥{total_value:,.0f} {price_str}{profit_str} | {t['reason']}")

            if current_date and current_date in cash_lookup:
                lines.append(f"  💰 现金 ¥{cash_lookup[current_date]:,.0f}")
                lines.append("")
            lines.append("")

            if code_stats:
                sorted_codes = sorted(code_stats.items(), key=lambda x: sum(x[1]), reverse=True)
                lines.append("## 交易统计")
                lines.append("")
                lines.append("| 标的 | 笔数 | 胜率 | 赔率 | 凯利 | 总盈亏 |")
                lines.append("|------|------|------|------|------|--------|")
                all_profits: list[float] = []
                for code, profits in sorted_codes:
                    name = code_names.get(code, code) if code_names else code
                    wins = [p for p in profits if p > 0]
                    losses = [p for p in profits if p <= 0]
                    win_rate = len(wins) / len(profits) if profits else 0
                    avg_win = np.mean(wins) if wins else 0
                    avg_lose = abs(np.mean(losses)) if losses else 0
                    profit_factor = avg_win / avg_lose if avg_lose > 0 else float('inf')
                    b = avg_win / avg_lose if avg_lose > 0 else 0
                    p = win_rate
                    q = 1 - p
                    kelly = (b * p - q) / b if b > 0 else 0
                    total_pnl = sum(profits)
                    all_profits.extend(profits)
                    pf_str = f"{profit_factor:.2f}" if profit_factor != float('inf') else "∞"
                    kelly_str = f"{kelly:.2%}" if kelly > 0 else "0.00%"
                    lines.append(f"| {name} | {len(profits)} | {win_rate:.0%} | {pf_str} | {kelly_str} | {total_pnl:+,.0f} |")

                if all_profits:
                    all_wins = [p for p in all_profits if p > 0]
                    all_losses = [p for p in all_profits if p <= 0]
                    all_win_rate = len(all_wins) / len(all_profits) if all_profits else 0
                    all_avg_win = np.mean(all_wins) if all_wins else 0
                    all_avg_lose = abs(np.mean(all_losses)) if all_losses else 0
                    all_pf = all_avg_win / all_avg_lose if all_avg_lose > 0 else float('inf')
                    all_b = all_avg_win / all_avg_lose if all_avg_lose > 0 else 0
                    all_p = all_win_rate
                    all_q = 1 - all_p
                    all_kelly = (all_b * all_p - all_q) / all_b if all_b > 0 else 0
                    all_pnl = sum(all_profits)
                    pf_str = f"{all_pf:.2f}" if all_pf != float('inf') else "∞"
                    kelly_str = f"{all_kelly:.2%}" if all_kelly > 0 else "0.00%"
                    lines.append(f"| **合计** | **{len(all_profits)}** | **{all_win_rate:.0%}** | **{pf_str}** | **{kelly_str}** | **{all_pnl:+,.0f}** |")
                lines.append("")

        filepath = os.path.join(run_dir, '回测报告.md')
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        logger.info(f"回测报告已保存: {filepath}")

    def _plot(self, nv_df, trade_log, analysis, code_names, benchmark_series, benchmark_name, run_dir):
        nv_indexed = nv_df.copy()
        nv_indexed['date'] = pd.to_datetime(nv_indexed['date'])
        nv_indexed = nv_indexed.set_index('date').sort_index()

        if 'drawdown' not in nv_indexed.columns:
            nv_indexed['peak'] = nv_indexed['net_value'].cummax()
            nv_indexed['drawdown'] = (nv_indexed['peak'] - nv_indexed['net_value']) / nv_indexed['peak']

        filepath = os.path.join(run_dir, '净值曲线.png')
        BacktestComparator.plot_single_strategy(
            nv_df=nv_indexed,
            analysis=analysis,
            benchmark_series=benchmark_series,
            benchmark_name=benchmark_name,
            output_path=filepath,
        )


class BacktestComparator:
    """回测结果对比可视化器。

    从标准化的 run 目录加载数据 → 生成标准化对比图表。
    所有方法均为静态方法，无状态，纯函数。
    """

    # ===================== 数据加载 =====================

    @staticmethod
    def load_run(run_dir: str) -> dict | None:
        """加载单个 run 目录。

        Returns:
            {'net_values': DataFrame(index=datetime), 'analysis': dict,
             'benchmark_name': str, 'strategy_name': str} 或 None
        """
        nv_path = os.path.join(run_dir, 'net_values.csv')
        report_path = os.path.join(run_dir, 'report.json')

        if not os.path.exists(nv_path):
            logger.error(f"net_values.csv 不存在: {nv_path}")
            return None

        nv_df = pd.read_csv(nv_path, encoding='utf-8')
        if 'date' not in nv_df.columns:
            logger.error(f"net_values.csv 缺少 date 列: {nv_path}")
            return None

        nv_df['date'] = pd.to_datetime(nv_df['date'])
        nv_df = nv_df.set_index('date').sort_index()

        if 'net_value' not in nv_df.columns and 'total_value' in nv_df.columns:
            nv_df['net_value'] = nv_df['total_value'] / nv_df['total_value'].iloc[0]

        nv_df['peak'] = nv_df['net_value'].cummax()
        nv_df['drawdown'] = (nv_df['peak'] - nv_df['net_value']) / nv_df['peak']

        analysis = {}
        benchmark_name = '基准'
        strategy_name = '策略'
        if os.path.exists(report_path):
            with open(report_path, 'r', encoding='utf-8') as f:
                report = json.load(f)
            analysis = {k: v for k, v in report.items()
                       if k not in ('benchmark_name', 'strategy_config')}
            benchmark_name = report.get('benchmark_name', benchmark_name)
            sc = report.get('strategy_config', {})
            strategy_name = sc.get('strategy_name', strategy_name) if isinstance(sc, dict) else strategy_name

        return {
            'net_values': nv_df,
            'analysis': analysis,
            'benchmark_name': benchmark_name,
            'strategy_name': strategy_name,
        }

    @staticmethod
    def load_runs(run_dirs: list[str], labels: Optional[list[str]] = None) -> dict:
        """批量加载多个 run 目录。

        Returns:
            {labels[i]: {net_values, analysis, ...}}，加载失败的 entry 为 None
        """
        if labels is None:
            labels = [os.path.basename(d) for d in run_dirs]

        result = {}
        for label, run_dir in zip(labels, run_dirs):
            data = BacktestComparator.load_run(run_dir)
            if data:
                result[label] = data
            else:
                logger.warning(f"跳过无法加载的 run: {run_dir}")
        return result

    # ===================== 单策略标准四面板 =====================

    @staticmethod
    def plot_single_strategy(nv_df: pd.DataFrame, analysis: dict,
                              benchmark_series: pd.Series = None,
                              benchmark_name: str = '基准',
                              title: str = '', output_path: str = ''):
        """单策略标准四面板图：净值 / 回撤 / 滚动Sharpe / 月度收益热力图。

        Args:
            nv_df: 含 net_value + drawdown 列，index 为 datetime
            analysis: BacktestAnalyzer 产出的 analysis dict
            benchmark_series: 基准净值 Series，index 为 datetime
        """
        fig = plt.figure(figsize=(18, 14))
        gs = fig.add_gridspec(4, 1, height_ratios=[2.5, 1.5, 1.5, 2], hspace=0.4)

        dates = nv_df.index
        common_xlim = (dates[0], dates[-1])

        monthly_returns = BacktestComparator._compute_monthly_returns(nv_df)

        # === Panel 1: 净值曲线 ===
        ax1 = fig.add_subplot(gs[0])
        ax1.plot(dates, nv_df['net_value'], color='#2196F3', linewidth=1.8, label='策略净值', zorder=3)

        if benchmark_series is not None and not benchmark_series.empty:
            bm_aligned = benchmark_series.reindex(dates, method='ffill')
            ax1.plot(dates, bm_aligned, color='#FF9800', linewidth=1.0, alpha=0.8,
                    linestyle='--', label=benchmark_name, zorder=2)
            nv_s = nv_df['net_value']
            ax1.fill_between(dates, nv_s.values, bm_aligned.values,
                            where=nv_s.values >= bm_aligned.values,
                            color='green', alpha=0.06, label='_nolegend_')
            ax1.fill_between(dates, nv_s.values, bm_aligned.values,
                            where=nv_s.values < bm_aligned.values,
                            color='red', alpha=0.06, label='_nolegend_')

        ax1.axhline(y=1.0, color='gray', linestyle='--', linewidth=0.5, alpha=0.5)
        ax1.set_xlim(common_xlim)
        ax1.set_ylabel('净值', fontsize=12)

        total_ret = analysis.get('total_return', 0)
        annual_ret = analysis.get('annual_return', 0)
        max_dd = analysis.get('max_drawdown', 0)
        sharpe = analysis.get('sharpe_ratio', 0)
        excess = analysis.get('excess_return', 0)

        title_str = (f"总收益 {total_ret:.1%} | 年化 {annual_ret:.1%}"
                     f" | 最大回撤 {max_dd:.1%} | Sharpe {sharpe:.2f}")
        if excess:
            title_str += f" | 超额 {excess:.1%}"
        ax1.set_title(title or title_str, fontsize=12, fontweight='bold')
        ax1.legend(loc='upper left', fontsize=9, framealpha=0.9)
        ax1.grid(True, alpha=0.25)
        ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        ax1.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45, ha='right')

        # === Panel 2: 回撤 ===
        ax2 = fig.add_subplot(gs[1])
        ax2.fill_between(dates, 0, -nv_df['drawdown'] * 100, color='#F44336', alpha=0.45, label=f'策略回撤 (max={max_dd:.1%})')
        ax2.plot(dates, -nv_df['drawdown'] * 100, color='#F44336', linewidth=0.6, alpha=0.8)

        if benchmark_series is not None and not benchmark_series.empty:
            bm_aligned = benchmark_series.reindex(dates, method='ffill')
            bm_peak = bm_aligned.cummax()
            bm_dd = (bm_peak - bm_aligned) / bm_peak
            ax2.fill_between(dates, 0, -bm_dd * 100, color='#FF9800', alpha=0.2, label=f'{benchmark_name}回撤')
            ax2.plot(dates, -bm_dd * 100, color='#FF9800', linewidth=0.5, alpha=0.6)

        ax2.set_xlim(common_xlim)
        ax2.set_ylabel('回撤 (%)', fontsize=12)
        ax2.set_title('回撤曲线', fontsize=12, fontweight='bold')
        ax2.legend(loc='lower left', fontsize=8, framealpha=0.9)
        ax2.grid(True, alpha=0.25)
        ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        ax2.yaxis.set_major_formatter(mticker.FormatStrFormatter('%.0f%%'))
        plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha='right')

        # === Panel 3: 滚动 Sharpe ===
        ax3 = fig.add_subplot(gs[2])
        daily_ret = nv_df['net_value'].pct_change().dropna()

        def _rolling_sharpe(x):
            if len(x) < 2 or x.std() == 0:
                return 0
            return x.mean() / x.std() * np.sqrt(252)

        roll_sharpe = daily_ret.rolling(126).apply(_rolling_sharpe).dropna()
        roll_dates = dates[-len(roll_sharpe):]
        ax3.plot(roll_dates, roll_sharpe, color='#2196F3', linewidth=1.2, label='滚动Sharpe (126日)')

        ax3.set_xlim(common_xlim)
        ax3.axhline(y=0, color='gray', linestyle='--', linewidth=0.5, alpha=0.5)
        ax3.set_ylabel('Sharpe', fontsize=12)
        ax3.set_title('滚动 Sharpe 比率 (126日窗口)', fontsize=12, fontweight='bold')
        ax3.legend(loc='upper left', fontsize=9)
        ax3.grid(True, alpha=0.25)
        ax3.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        ax3.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        plt.setp(ax3.xaxis.get_majorticklabels(), rotation=45, ha='right')

        # === Panel 4: 月度收益热力图 ===
        ax4 = fig.add_subplot(gs[3])
        if monthly_returns is not None and not monthly_returns.empty:
            im = ax4.imshow(monthly_returns.values, cmap='RdYlGn', aspect='auto', vmin=-0.15, vmax=0.15)
            ax4.set_xticks(range(len(monthly_returns.columns)))
            ax4.set_xticklabels(monthly_returns.columns, fontsize=8)
            ax4.set_yticks(range(len(monthly_returns.index)))
            ax4.set_yticklabels(monthly_returns.index, fontsize=8)
            plt.colorbar(im, ax=ax4, label='收益率', shrink=0.85)

            for i in range(len(monthly_returns.index)):
                for j in range(len(monthly_returns.columns)):
                    val = monthly_returns.values[i, j]
                    if not np.isnan(val):
                        color = 'white' if abs(val) > 0.07 else 'black'
                        ax4.text(j, i, f'{val:.1%}', ha='center', va='center', fontsize=6, color=color)
        ax4.set_title('月度收益热力图', fontsize=12, fontweight='bold')

        fig.suptitle(title or '策略回测分析', fontsize=14, fontweight='bold', y=0.995)
        if output_path:
            fig.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='white')
            plt.close(fig)
            logger.info(f"图表已保存: {output_path}")
        else:
            plt.close(fig)

    # ===================== 多策略对比四面板 =====================

    @staticmethod
    def plot_multi_nv_comparison(runs: dict, title: str = '', output_path: str = '',
                                  figsize=(18, 16)):
        """标准四面板多策略对比图。

        Args:
            runs: {标签: {net_values: DataFrame, analysis: dict, ...}}
                  每个 entry 必须包含 net_values(DataFrame, index=datetime, 含 net_value/drawdown)
                  和 analysis(dict, 含 total_return/annual_return/sharpe_ratio/max_drawdown)
        """
        valid_runs = {k: v for k, v in runs.items() if v is not None}
        if not valid_runs:
            logger.error("没有有效的回测数据")
            return

        fig = plt.figure(figsize=figsize)
        gs = fig.add_gridspec(4, 1, height_ratios=[2.5, 1.5, 1.5, 2], hspace=0.4)

        labels = list(valid_runs.keys())
        color_map = {label: DEFAULT_COLORS[i % len(DEFAULT_COLORS)] for i, label in enumerate(labels)}

        first_nv = valid_runs[labels[0]]['net_values']
        common_xlim = (first_nv.index[0], first_nv.index[-1])

        # === Panel 1: 净值对比 ===
        ax1 = fig.add_subplot(gs[0])
        for label, data in valid_runs.items():
            nv = data['net_values']
            color = color_map[label]
            ax1.plot(nv.index, nv['net_value'], color=color, linewidth=1.6, alpha=0.9, label=label)

        ax1.axhline(y=1.0, color='gray', linestyle='--', linewidth=0.5, alpha=0.5)
        ax1.set_xlim(common_xlim)
        ax1.set_ylabel('净值', fontsize=12)
        ax1.set_title(title or '多策略净值对比', fontsize=13, fontweight='bold')
        ax1.legend(loc='upper left', fontsize=9, framealpha=0.9, ncol=2)
        ax1.grid(True, alpha=0.25)
        ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        ax1.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45, ha='right')

        # === Panel 2: 回撤对比 ===
        ax2 = fig.add_subplot(gs[1])
        for label, data in valid_runs.items():
            nv = data['net_values']
            color = color_map[label]
            max_dd = data['analysis'].get('max_drawdown', nv['drawdown'].max())
            ax2.fill_between(nv.index, 0, -nv['drawdown'] * 100, color=color, alpha=0.25,
                            label=f'{label} (max={max_dd:.1%})')
            ax2.plot(nv.index, -nv['drawdown'] * 100, color=color, linewidth=0.6, alpha=0.7)

        ax2.set_xlim(common_xlim)
        ax2.set_ylabel('回撤 (%)', fontsize=12)
        ax2.set_title('回撤对比', fontsize=12, fontweight='bold')
        ax2.legend(loc='lower left', fontsize=8, framealpha=0.9, ncol=2)
        ax2.grid(True, alpha=0.25)
        ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        ax2.yaxis.set_major_formatter(mticker.FormatStrFormatter('%.0f%%'))
        plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha='right')

        # === Panel 3: 滚动 Sharpe ===
        ax3 = fig.add_subplot(gs[2])

        def _rolling_sharpe(x):
            if len(x) < 2 or x.std() == 0:
                return 0
            return x.mean() / x.std() * np.sqrt(252)

        for label, data in valid_runs.items():
            nv = data['net_values']
            daily_ret = nv['net_value'].pct_change().dropna()
            roll_sharpe = daily_ret.rolling(126).apply(_rolling_sharpe).dropna()
            roll_dates = nv.index[-len(roll_sharpe):]
            color = color_map[label]
            ax3.plot(roll_dates, roll_sharpe, color=color, linewidth=1.0, alpha=0.85, label=label)

        ax3.set_xlim(common_xlim)
        ax3.axhline(y=0, color='gray', linestyle='--', linewidth=0.5, alpha=0.5)
        ax3.set_ylabel('Sharpe', fontsize=12)
        ax3.set_title('滚动 Sharpe 比率 (126日窗口)', fontsize=12, fontweight='bold')
        ax3.legend(loc='upper left', fontsize=8, ncol=2)
        ax3.grid(True, alpha=0.25)
        ax3.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        ax3.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        plt.setp(ax3.xaxis.get_majorticklabels(), rotation=45, ha='right')

        # === Panel 4: 指标柱状图 ===
        ax4 = fig.add_subplot(gs[3])

        metric_groups = [
            ('总收益(%)', lambda a: a.get('total_return', 0) * 100),
            ('年化收益(%)', lambda a: a.get('annual_return', 0) * 100),
            ('Sharpe', lambda a: a.get('sharpe_ratio', 0)),
            ('最大回撤(%)', lambda a: a.get('max_drawdown', 0) * 100),
        ]

        x = np.arange(len(labels))
        n_groups = len(metric_groups)
        width = 0.8 / n_groups
        bar_colors = ['#2196F3', '#4CAF50', '#FF9800', '#F44336']

        for gi, (m_name, m_fn) in enumerate(metric_groups):
            values = [m_fn(valid_runs[l]['analysis']) for l in labels]
            offset = (gi - (n_groups - 1) / 2) * width
            bars = ax4.bar(x + offset, values, width, color=bar_colors[gi], alpha=0.85,
                          label=m_name, edgecolor='white', linewidth=0.5)
            for bar in bars:
                h = bar.get_height()
                fmt = f'{h:.2f}' if 'Sharpe' in m_name else f'{h:.1f}'
                ax4.text(bar.get_x() + bar.get_width() / 2., h + max(values) * 0.01,
                        fmt, ha='center', va='bottom', fontsize=7, fontweight='bold')

        ax4.set_xticks(x)
        ax4.set_xticklabels(labels, fontsize=10)
        ax4.set_title('核心指标对比', fontsize=12, fontweight='bold')
        ax4.legend(loc='upper right', fontsize=9, ncol=2)
        ax4.grid(True, alpha=0.2, axis='y')
        ax4.axhline(y=0, color='gray', linewidth=0.5)

        fig.suptitle(title or '多策略回测对比', fontsize=14, fontweight='bold', y=0.995)
        if output_path:
            fig.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='white')
            plt.close(fig)
            logger.info(f"图表已保存: {output_path}")
        else:
            plt.close(fig)

    # ===================== A vs B 改进对比 =====================

    @staticmethod
    def plot_ab_comparison(run_a: dict, run_b: dict,
                            title_a: str = '改进前', title_b: str = '改进后',
                            title: str = '', output_path: str = ''):
        """A vs B 改进对比图：三线净值 / 回撤 / 指标变化 / 超额收益累计。

        Args:
            run_a: 改进前（load_run 返回格式）
            run_b: 改进后（load_run 返回格式）
        """
        if run_a is None or run_b is None:
            logger.error("run_a 或 run_b 数据为空")
            return

        nv_a = run_a['net_values']
        nv_b = run_b['net_values']
        analysis_a = run_a['analysis']
        analysis_b = run_b['analysis']

        fig = plt.figure(figsize=(18, 14))
        gs = fig.add_gridspec(4, 1, height_ratios=[2.5, 1.5, 1.5, 2], hspace=0.4)

        ta0, ta1 = nv_a.index[0], nv_a.index[-1]
        tb0, tb1 = nv_b.index[0], nv_b.index[-1]
        common_xlim = (min(ta0, tb0), max(ta1, tb1))

        # === Panel 1: 三线净值 ===
        ax1 = fig.add_subplot(gs[0])
        ax1.plot(nv_a.index, nv_a['net_value'], color='#FF9800', linewidth=1.5, alpha=0.85,
                label=title_a, linestyle='--')
        ax1.plot(nv_b.index, nv_b['net_value'], color='#4CAF50', linewidth=2.0, alpha=0.9,
                label=title_b)
        ax1.axhline(y=1.0, color='gray', linestyle='--', linewidth=0.5, alpha=0.5)
        ax1.set_xlim(common_xlim)

        ret_a = analysis_a.get('total_return', 0)
        ret_b = analysis_b.get('total_return', 0)
        delta = ret_b - ret_a
        ax1.set_ylabel('净值', fontsize=12)
        ax1.set_title(f'{title_a} vs {title_b}: 收益差 {delta:+.2%}', fontsize=12, fontweight='bold')
        ax1.legend(loc='upper left', fontsize=10, framealpha=0.9)
        ax1.grid(True, alpha=0.25)
        ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        ax1.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45, ha='right')

        # === Panel 2: 回撤对比 ===
        ax2 = fig.add_subplot(gs[1])
        dd_a = analysis_a.get('max_drawdown', nv_a['drawdown'].max())
        dd_b = analysis_b.get('max_drawdown', nv_b['drawdown'].max())
        ax2.fill_between(nv_a.index, 0, -nv_a['drawdown'] * 100, color='#FF9800', alpha=0.3,
                        label=f'{title_a} (max={dd_a:.1%})')
        ax2.fill_between(nv_b.index, 0, -nv_b['drawdown'] * 100, color='#4CAF50', alpha=0.3,
                        label=f'{title_b} (max={dd_b:.1%})')
        ax2.plot(nv_a.index, -nv_a['drawdown'] * 100, color='#FF9800', linewidth=0.6, alpha=0.7)
        ax2.plot(nv_b.index, -nv_b['drawdown'] * 100, color='#4CAF50', linewidth=0.8, alpha=0.8)

        ax2.set_xlim(common_xlim)
        ax2.set_ylabel('回撤 (%)', fontsize=12)
        ax2.set_title(f'回撤对比 (Δ={dd_b-dd_a:+.2%})', fontsize=12, fontweight='bold')
        ax2.legend(loc='lower left', fontsize=9, framealpha=0.9)
        ax2.grid(True, alpha=0.25)
        ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        ax2.yaxis.set_major_formatter(mticker.FormatStrFormatter('%.0f%%'))
        plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha='right')

        # === Panel 3: 指标变化对比表 ===
        ax3 = fig.add_subplot(gs[2])
        ax3.axis('off')

        metrics = [
            ('总收益', 'total_return', '{:.2%}'),
            ('年化收益', 'annual_return', '{:.2%}'),
            ('最大回撤', 'max_drawdown', '{:.2%}'),
            ('Sharpe', 'sharpe_ratio', '{:.2f}'),
            ('Sortino', 'sortino_ratio', '{:.2f}'),
            ('胜率', 'win_rate', '{:.2%}'),
            ('交易次数', 'trade_count', '{:.0f}'),
            ('超额收益', 'excess_return', '{:.2%}'),
        ]

        table_data = [['指标', title_a, title_b, '变化', '评估']]
        for m_name, m_key, m_fmt in metrics:
            va = analysis_a.get(m_key, 0)
            vb = analysis_b.get(m_key, 0)
            delta = vb - va
            if m_key == 'max_drawdown':
                emoji = '✅' if delta <= 0 else '⚠️'
            elif m_key == 'trade_count':
                emoji = '—'
            else:
                emoji = '✅' if delta >= 0 else '⚠️'
            delta_str = f'{delta:+.2%}' if m_fmt.endswith('%') else (
                f'{delta:+.2f}' if m_fmt.endswith('f}') else f'{delta:+.0f}')
            table_data.append([m_name, m_fmt.format(va), m_fmt.format(vb), delta_str, emoji])

        table = ax3.table(cellText=table_data, cellLoc='center', loc='center')
        table.auto_set_font_size(False)
        table.set_fontsize(10)
        table.scale(1, 1.6)

        for j in range(len(table_data[0])):
            table[0, j].set_facecolor('#37474F')
            table[0, j].set_text_props(color='white', fontweight='bold')

        for i in range(1, len(table_data)):
            delta_str = table_data[i][3]
            if '✅' in table_data[i][4]:
                table[i, :].set_facecolor('#E8F5E9')  # type: ignore[index]
            elif '⚠️' in table_data[i][4]:
                table[i, :].set_facecolor('#FFF3E0')  # type: ignore[index]

        ax3.set_title('指标变化对比', fontsize=12, fontweight='bold')

        # === Panel 4: 超额收益累计曲线 ===
        ax4 = fig.add_subplot(gs[3])

        daily_ret_a = nv_a['net_value'].pct_change().dropna()
        daily_ret_b = nv_b['net_value'].pct_change().dropna()

        common_dates = daily_ret_a.index.intersection(daily_ret_b.index)
        if len(common_dates) > 1:
            diff_ret = daily_ret_b.reindex(common_dates) - daily_ret_a.reindex(common_dates)
            cum_excess = (1 + diff_ret).cumprod() - 1
            color = '#4CAF50' if cum_excess.iloc[-1] >= 0 else '#F44336'
            ax4.fill_between(common_dates, 0, cum_excess * 100, color=color, alpha=0.3)
            ax4.plot(common_dates, cum_excess * 100, color=color, linewidth=1.2)
            ax4.axhline(y=0, color='gray', linestyle='--', linewidth=0.5, alpha=0.5)
            ax4.set_ylabel('累计超额 (%)', fontsize=12)
            ax4.set_title(f'超额收益累计 ({title_b} - {title_a})', fontsize=12, fontweight='bold')
            ax4.set_xlim(common_xlim)

        ax4.grid(True, alpha=0.25)
        ax4.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        ax4.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        plt.setp(ax4.xaxis.get_majorticklabels(), rotation=45, ha='right')

        fig.suptitle(title or f'{title_a} vs {title_b}', fontsize=14, fontweight='bold', y=0.995)
        if output_path:
            fig.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='white')
            plt.close(fig)
            logger.info(f"图表已保存: {output_path}")
        else:
            plt.close(fig)

    # ===================== 参数热力图 =====================

    @staticmethod
    def plot_param_heatmap(sweep_df: pd.DataFrame, x_param: str, y_param: str,
                            z_metric: str = 'sharpe_ratio', title: str = '',
                            output_path: str = ''):
        """二维参数热力图。

        Args:
            sweep_df: Validator.parameter_sweep() 的产出 DataFrame
            x_param/y_param: 两个参数维度的列名
            z_metric: 颜色映射的指标列名
        """
        if sweep_df.empty or x_param not in sweep_df.columns or y_param not in sweep_df.columns:
            logger.error(f"参数扫描 DataFrame 缺少 {x_param} 或 {y_param}")
            return

        pivot = sweep_df.pivot_table(index=y_param, columns=x_param, values=z_metric, aggfunc='first')

        fig, ax = plt.subplots(figsize=(10, 8))
        im = ax.imshow(pivot.values, cmap='RdYlGn', aspect='auto', origin='lower')

        ax.set_xticks(range(len(pivot.columns)))
        ax.set_xticklabels([f'{v:.1f}' if isinstance(v, float) else str(v) for v in pivot.columns], fontsize=9)
        ax.set_yticks(range(len(pivot.index)))
        ax.set_yticklabels([f'{v:.1f}' if isinstance(v, float) else str(v) for v in pivot.index], fontsize=9)
        ax.set_xlabel(x_param, fontsize=12)
        ax.set_ylabel(y_param, fontsize=12)

        for i in range(len(pivot.index)):
            for j in range(len(pivot.columns)):
                val = pivot.values[i, j]
                if not np.isnan(val):
                    color = 'white' if abs(val) > (pivot.values.max() - pivot.values.min()) * 0.5 else 'black'
                    ax.text(j, i, f'{val:.2f}', ha='center', va='center', fontsize=8, color=color, fontweight='bold')

        plt.colorbar(im, ax=ax, label=z_metric, shrink=0.85)
        ax.set_title(title or f'参数热力图: {x_param} × {y_param} → {z_metric}', fontsize=13, fontweight='bold')

        if output_path:
            fig.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='white')
            plt.close(fig)
            logger.info(f"图表已保存: {output_path}")
        else:
            plt.close(fig)

    # ===================== Walk-Forward 专用 =====================

    @staticmethod
    def plot_walk_forward(wf_df: pd.DataFrame, title: str = '', output_path: str = ''):
        """Walk-Forward 样本内外收益瀑布图 + 参数漂移时序。

        Args:
            wf_df: Validator.walk_forward() 的产出 DataFrame
        """
        if wf_df.empty:
            logger.error("Walk-Forward DataFrame 为空")
            return

        oos_col = None
        oos_sharpe_col = None
        for col in wf_df.columns:
            if col.startswith('oos_') and 'return' in col:
                oos_col = col
            if col.startswith('oos_') and 'sharpe' in col:
                oos_sharpe_col = col

        param_cols = [c for c in wf_df.columns if c.startswith('param_')]

        n_params = len(param_cols)
        n_rows = 2 if n_params == 0 else 3
        fig = plt.figure(figsize=(16, 4 * n_rows))
        gs = fig.add_gridspec(n_rows, 1, hspace=0.4)

        # === Panel 1: OOS 收益瀑布图 ===
        ax1 = fig.add_subplot(gs[0])
        windows = wf_df['window'].astype(int).tolist()

        if oos_col:
            oos_vals = wf_df[oos_col].values * 100
            colors = ['#4CAF50' if v >= 0 else '#F44336' for v in oos_vals]
            bars = ax1.bar(range(len(windows)), oos_vals, color=colors, alpha=0.85, edgecolor='white')

            for bar, val in zip(bars, oos_vals):
                y_pos = val + 0.3 if val >= 0 else val - 0.8
                ax1.text(bar.get_x() + bar.get_width() / 2., y_pos,
                        f'{val:+.1f}%', ha='center', va='bottom' if val >= 0 else 'top',
                        fontsize=9, fontweight='bold')

            ax1.axhline(y=0, color='gray', linewidth=0.5)
            avg_val = oos_vals.mean()
            ax1.axhline(y=avg_val, color='#2196F3', linestyle='--', linewidth=1.0, alpha=0.7,
                       label=f'OOS平均={avg_val:+.1f}%')

        ax1.set_xticks(range(len(windows)))
        ax1.set_xticklabels([f'W{w}' for w in windows], fontsize=9)
        ax1.set_ylabel('OOS 收益 (%)', fontsize=12)
        ax1.set_title('Walk-Forward 样本外收益 (各窗口)', fontsize=12, fontweight='bold')
        ax1.legend(loc='upper right', fontsize=9)
        ax1.grid(True, alpha=0.25, axis='y')

        # === Panel 2: OOS Sharpe 柱状图 ===
        ax2 = fig.add_subplot(gs[1])

        if oos_sharpe_col:
            sharpe_vals = wf_df[oos_sharpe_col].values
            colors = ['#4CAF50' if v >= 0 else '#F44336' for v in sharpe_vals]
            bars = ax2.bar(range(len(windows)), sharpe_vals, color=colors, alpha=0.85, edgecolor='white')

            for bar, val in zip(bars, sharpe_vals):
                y_pos = val + 0.1 if val >= 0 else val - 0.3
                ax2.text(bar.get_x() + bar.get_width() / 2., y_pos,
                        f'{val:.2f}', ha='center', va='bottom' if val >= 0 else 'top',
                        fontsize=9, fontweight='bold')

            ax2.axhline(y=0, color='gray', linewidth=0.5)
            avg_sharpe = sharpe_vals.mean()
            ax2.axhline(y=avg_sharpe, color='#2196F3', linestyle='--', linewidth=1.0, alpha=0.7,
                       label=f'OOS平均={avg_sharpe:+.2f}')
        else:
            train_return_col = None
            for col in wf_df.columns:
                if col.startswith('train_') and 'return' in col:
                    train_return_col = col
                    break
            if train_return_col and oos_col:
                train_vals = wf_df[train_return_col].values * 100
                oos_vals_2 = wf_df[oos_col].values * 100
                x = np.arange(len(windows))
                width = 0.35
                ax2.bar(x - width / 2, train_vals, width, color='#2196F3', alpha=0.7, label='训练期收益(%)')
                ax2.bar(x + width / 2, oos_vals_2, width, color='#FF9800', alpha=0.7, label='OOS收益(%)')
                ax2.set_ylabel('收益 (%)', fontsize=12)

        ax2.set_xticks(range(len(windows)))
        ax2.set_xticklabels([f'W{w}' for w in windows], fontsize=9)
        ax2.set_title('OOS Sharpe', fontsize=12, fontweight='bold')
        ax2.legend(loc='upper right', fontsize=9)
        ax2.grid(True, alpha=0.25, axis='y')

        # === Panel 3: 参数漂移时序（如果有参数扫描） ===
        if n_params > 0:
            ax3 = fig.add_subplot(gs[2])
            for pc in param_cols:
                param_name = pc.replace('param_', '')
                ax3.plot(windows, wf_df[pc], marker='o', linewidth=1.5, label=param_name)
            ax3.set_xticks(windows)
            ax3.set_xticklabels([f'W{w}' for w in windows], fontsize=9)
            ax3.set_ylabel('最优参数', fontsize=12)
            ax3.set_title('参数漂移 (各窗口训练期最优)', fontsize=12, fontweight='bold')
            ax3.legend(loc='best', fontsize=9)
            ax3.grid(True, alpha=0.25)

        fig.suptitle(title or 'Walk-Forward 样本外验证', fontsize=14, fontweight='bold', y=0.995)
        if output_path:
            fig.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='white')
            plt.close(fig)
            logger.info(f"图表已保存: {output_path}")
        else:
            plt.close(fig)

    # ===================== 内部工具 =====================

    @staticmethod
    def _compute_monthly_returns(nv_df: pd.DataFrame) -> pd.DataFrame | None:
        """从日频净值计算月度收益矩阵 (month × year)"""
        if nv_df.empty or 'net_value' not in nv_df.columns:
            return None

        monthly = nv_df['net_value'].resample('ME').last().pct_change().dropna()
        if len(monthly) < 2:
            return None

        monthly_ret = monthly.to_frame('return')
        monthly_ret['year'] = monthly_ret.index.year
        monthly_ret['month'] = monthly_ret.index.month

        pivot = monthly_ret.pivot_table(index='year', columns='month', values='return', aggfunc='first')
        month_names = {1: '1月', 2: '2月', 3: '3月', 4: '4月', 5: '5月', 6: '6月',
                       7: '7月', 8: '8月', 9: '9月', 10: '10月', 11: '11月', 12: '12月'}
        pivot.columns = [month_names.get(c, str(c)) for c in pivot.columns]
        return pivot