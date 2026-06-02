"""回测结果对比工具 —— 比较两次回测的差异"""
import argparse
import json
import os
import sys
from typing import Any


def load_run_report(run_dir: str) -> tuple[dict, dict]:
    """加载一次回测的运行数据。

    Returns: (report_json, git_info) 或 ({}, {})
    """
    report = {}
    git_info = {}

    report_path = os.path.join(run_dir, 'report.json')
    if os.path.exists(report_path):
        with open(report_path, 'r', encoding='utf-8') as f:
            report = json.load(f)

    git_path = os.path.join(run_dir, 'git_info.json')
    if os.path.exists(git_path):
        with open(git_path, 'r', encoding='utf-8') as f:
            git_info = json.load(f)

    return report, git_info


COMPARE_FIELDS = {
    'total_return': '总收益率',
    'annual_return': '年化收益率',
    'excess_return': '超额收益',
    'sharpe_ratio': 'Sharpe比率',
    'sortino_ratio': 'Sortino比率',
    'max_drawdown': '最大回撤',
    'trade_count': '交易次数',
    'win_rate': '胜率',
    'profit_factor': '盈亏比',
}


def compare_runs(run_a: str, run_b: str) -> dict:
    """对比两次回测的差异，返回结构化对比结果。"""
    report_a, git_a = load_run_report(run_a)
    report_b, git_b = load_run_report(run_b)

    if not report_a and not report_b:
        return {'error': '两个 run 的 report.json 均不存在'}
    if not report_a:
        return {'error': f'{run_a} 缺少 report.json'}
    if not report_b:
        return {'error': f'{run_b} 缺少 report.json'}

    diffs = {}
    for field, label in COMPARE_FIELDS.items():
        val_a = report_a.get(field)
        val_b = report_b.get(field)
        if val_a is not None and val_b is not None:
            diff = val_b - val_a
            diffs[label] = {
                'a': val_a, 'b': val_b,
                'diff': diff,
                'direction': 'up' if diff > 0 else ('down' if diff < 0 else 'unchanged'),
            }

    return {
        'run_a': {
            'id': os.path.basename(run_a),
            'sha': git_a.get('sha', '?'),
            'branch': git_a.get('branch', '?'),
        },
        'run_b': {
            'id': os.path.basename(run_b),
            'sha': git_b.get('sha', '?'),
            'branch': git_b.get('branch', '?'),
        },
        'diffs': diffs,
    }


def format_comparison(comparison: dict) -> str:
    """将对比结果格式化为可读文本。"""
    if 'error' in comparison:
        return f"错误: {comparison['error']}"

    lines = []
    a = comparison['run_a']
    b = comparison['run_b']

    lines.append(f"基准 (A): {a['id']} @ {a['sha']}({a['branch']})")
    lines.append(f"对比 (B): {b['id']} @ {b['sha']}({b['branch']})")
    lines.append("")
    lines.append(f"{'指标':<14} {'A':>10} {'B':>10} {'变化':>12}")
    lines.append("-" * 48)

    diffs = comparison['diffs']
    for label, d in sorted(diffs.items()):
        arrow = ' ↑' if d['direction'] == 'up' else (' ↓' if d['direction'] == 'down' else '  ')
        a_str = f"{d['a']:.2%}" if isinstance(d['a'], float) and abs(d['a']) < 10 else f"{d['a']}"
        b_str = f"{d['b']:.2%}" if isinstance(d['b'], float) and abs(d['b']) < 10 else f"{d['b']}"
        diff_str = f"{d['diff']:+.4f}{arrow}"
        lines.append(f"{label:<14} {a_str:>10} {b_str:>10} {diff_str:>12}")

    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description="对比两次回测结果")
    parser.add_argument("run_a", help="基准 Run ID")
    parser.add_argument("run_b", help="对比 Run ID")
    parser.add_argument("--results-dir", default="results", help="回测结果目录")
    args = parser.parse_args()

    results_dir = args.results_dir

    # 支持完整路径或 Run ID
    def resolve(path):
        if os.path.isdir(path):
            return path
        full = os.path.join(results_dir, path)
        if os.path.isdir(full):
            return full
        return path

    run_a = resolve(args.run_a)
    run_b = resolve(args.run_b)

    comparison = compare_runs(run_a, run_b)
    print(format_comparison(comparison))

    return 0 if 'error' not in comparison else 1


if __name__ == "__main__":
    sys.exit(main())
