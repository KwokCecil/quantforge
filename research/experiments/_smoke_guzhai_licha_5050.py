"""烟雾测试：检查当前股债利差信号"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quantforge.indicators.guzhai_licha import GuzhaiLichaCalculator

calc = GuzhaiLichaCalculator()
signals = calc.compute('2025-01-01')
latest = signals[-1]

print('=== 最新信号 ===')
print(f'日期: {latest.date.strftime("%Y-%m-%d")}')
print(f'PE静态: {latest.pe_static:.1f}   PE_TTM: {latest.pe_ttm:.1f}')
print(f'国债10Y: {latest.bond_10y:.2f}%')
print(f'双倍利差: {latest.double_ttm_licha:.1f}%  分位: {latest.double_ttm_pct:.1%}')
print(f'单倍利差: {latest.single_static_licha:.1f}%  分位: {latest.single_static_pct:.1%}')
print(f'冲锋: {latest.signal_charge}  撤退: {latest.signal_retreat}')
print()
print('当前建议: ', end='')
if latest.signal_charge:
    print('买入 510300(50%) + 159915(50%)')
elif latest.signal_retreat:
    print('清仓！')
else:
    print('维持现状（中性区）')