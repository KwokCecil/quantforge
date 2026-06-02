import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from quantforge.research.strategy_health import HealthMonitor, HealthBaseline


def main():
    baseline_path = os.path.join(os.path.dirname(__file__), '..', 'data',
                                 'health_baseline_roc_momentum.json')
    if not os.path.exists(baseline_path):
        print("基线文件不存在，请先运行 create_baseline")
        return

    baseline = HealthBaseline.load(baseline_path)
    monitor = HealthMonitor(baseline)
    print(f"策略: {baseline.strategy_name}  基线日期: {baseline.baseline_date}")
    print(f"基线指标: Sharpe={baseline.sharpe_1y:.4f} DD={baseline.max_drawdown_1y:.4f} "
          f"年化={baseline.annual_return:.4f} ICIR={baseline.icir_1y:.4f} Crowding={baseline.crowding:.4f}")

    run_dir = None
    index_path = os.path.join(os.path.dirname(__file__), '..', 'results', 'index.json')
    if os.path.exists(index_path):
        with open(index_path, encoding='utf-8') as f:
            idx = json.load(f)
        runs = idx.get('runs', [])
        if runs:
            run_dir = os.path.join(os.path.dirname(__file__), '..', 'results',
                                   os.path.basename(runs[-1]['run_dir']))

    if not run_dir:
        print("未找到回测结果，无法运行健康检查")
        return

    nv = pd.read_csv(os.path.join(run_dir, 'net_values.csv'))
    with open(os.path.join(run_dir, 'trades.json'), encoding='utf-8') as f:
        trades = json.load(f)

    result = monitor.check(nv, trades)

    print(f"判定: {result['verdict']}")
    for c in result['comparisons']:
        d = c['deviation_pct']
        sign = '+' if d >= 0 else ''
        print(f"  {c['metric']:20s}: {c['baseline']:.4f} → {c['current']:.4f}"
              f"  ({sign}{d:.0f}%)  [{c['level']}]")


if __name__ == '__main__':
    main()
