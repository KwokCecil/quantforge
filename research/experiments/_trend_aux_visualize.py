# @layer: e2e
"""T028 辅助信号可视化：从 trend_aux_*.csv 读取数据，生成对比图表"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import glob
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

RESULT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results", "T028_aux_signals")
csv_files = sorted(glob.glob(os.path.join(RESULT_DIR, "trend_aux_*.csv")), reverse=True)
if not csv_files:
    print("未找到 trend_aux_*.csv，请先运行 research/_trend_aux_signals.py")
    sys.exit(1)

csv_path = csv_files[0]
df = pd.read_csv(csv_path)
print(f"加载: {csv_path} ({len(df)} 行)")

# ============ 图1: 三信号胜率+盈亏对比 ============
fig, axes = plt.subplots(2, 3, figsize=(16, 10))
fig.suptitle('T028 辅助信号分析：买入信号三维度对比', fontsize=16, fontweight='bold', y=0.98)

# --- MACD 背离 ---
labels = ['有背离', '无背离']
wr = [df[df['macd_divergence']==True]['is_win'].mean()*100,
      df[df['macd_divergence']==False]['is_win'].mean()*100]
ret = [df[df['macd_divergence']==True]['return_pct'].mean(),
       df[df['macd_divergence']==False]['return_pct'].mean()]
cnt = [len(df[df['macd_divergence']==True]), len(df[df['macd_divergence']==False])]

colors = ['#F44336', '#4CAF50']
ax = axes[0, 0]
bars = ax.bar(labels, wr, color=colors, edgecolor='white', linewidth=0.5)
for b, v, c in zip(bars, wr, cnt):
    ax.text(b.get_x() + b.get_width()/2, b.get_height() + 1, f'{v:.1f}%\n(n={c})',
            ha='center', va='bottom', fontsize=10, fontweight='bold')
ax.set_title('MACD背离：胜率', fontsize=13)
ax.set_ylabel('胜率 (%)')
ax.set_ylim(0, 85)
ax.grid(axis='y', alpha=0.3)

ax = axes[1, 0]
bars = ax.bar(labels, ret, color=colors, edgecolor='white', linewidth=0.5)
for b, v in zip(bars, ret):
    ax.text(b.get_x() + b.get_width()/2, b.get_height() + 0.2, f'{v:+.1f}%',
            ha='center', va='bottom', fontsize=11, fontweight='bold')
ax.set_title('MACD背离：平均盈亏', fontsize=13)
ax.set_ylabel('平均盈亏 (%)')
ax.axhline(y=0, color='gray', linestyle='--', alpha=0.5)
ax.grid(axis='y', alpha=0.3)

# --- 量价 ---
vol_order = ['放量', '正常', '缩量']
wr2 = [df[df['vol_label']==l]['is_win'].mean()*100 for l in vol_order]
ret2 = [df[df['vol_label']==l]['return_pct'].mean() for l in vol_order]
cnt2 = [len(df[df['vol_label']==l]) for l in vol_order]
colors2 = ['#FF9800', '#2196F3', '#4CAF50']

ax = axes[0, 1]
bars = ax.bar(vol_order, wr2, color=colors2, edgecolor='white', linewidth=0.5)
for b, v, c in zip(bars, wr2, cnt2):
    ax.text(b.get_x() + b.get_width()/2, b.get_height() + 1, f'{v:.1f}%\n(n={c})',
            ha='center', va='bottom', fontsize=10, fontweight='bold')
ax.set_title('量价（成交量/20日均量）：胜率', fontsize=13)
ax.set_ylim(0, 85)
ax.grid(axis='y', alpha=0.3)

ax = axes[1, 1]
bars = ax.bar(vol_order, ret2, color=colors2, edgecolor='white', linewidth=0.5)
for b, v in zip(bars, ret2):
    ax.text(b.get_x() + b.get_width()/2, b.get_height() + 0.15, f'{v:+.1f}%',
            ha='center', va='bottom', fontsize=11, fontweight='bold')
ax.set_title('量价：平均盈亏', fontsize=13)
ax.axhline(y=0, color='gray', linestyle='--', alpha=0.5)
ax.grid(axis='y', alpha=0.3)

# --- 波动率 ---
atr_order = ['高波', '正常', '低波']
wr3 = [df[df['atr_label']==l]['is_win'].mean()*100 for l in atr_order]
ret3 = [df[df['atr_label']==l]['return_pct'].mean() for l in atr_order]
cnt3 = [len(df[df['atr_label']==l]) for l in atr_order]
colors3 = ['#FF5722', '#607D8B', '#2196F3']

ax = axes[0, 2]
bars = ax.bar(atr_order, wr3, color=colors3, edgecolor='white', linewidth=0.5)
for b, v, c in zip(bars, wr3, cnt3):
    ax.text(b.get_x() + b.get_width()/2, b.get_height() + 1, f'{v:.1f}%\n(n={c})',
            ha='center', va='bottom', fontsize=10, fontweight='bold')
ax.set_title('ATR(20) 252日分位：胜率', fontsize=13)
ax.set_ylim(0, 100)
ax.grid(axis='y', alpha=0.3)

ax = axes[1, 2]
bars = ax.bar(atr_order, ret3, color=colors3, edgecolor='white', linewidth=0.5)
for b, v in zip(bars, ret3):
    ax.text(b.get_x() + b.get_width()/2, b.get_height() + 0.25, f'{v:+.1f}%',
            ha='center', va='bottom', fontsize=11, fontweight='bold')
ax.set_title('波动率：平均盈亏', fontsize=13)
ax.axhline(y=0, color='gray', linestyle='--', alpha=0.5)
ax.grid(axis='y', alpha=0.3)

plt.tight_layout(rect=[0, 0, 1, 0.94])
out1 = os.path.join(RESULT_DIR, 'trend_aux_signal_comparison.png')
plt.savefig(out1, dpi=150, bbox_inches='tight')
plt.close()
print(f"图1: {out1}")

# ============ 图2: 交叉维度热力图 ============
fig, axes = plt.subplots(1, 2, figsize=(14, 6))
fig.suptitle('T028 交叉维度：量价 × 波动率', fontsize=14, fontweight='bold', y=0.98)

heatmap_wr = np.zeros((3, 3))
heatmap_ret = np.zeros((3, 3))
heatmap_cnt = np.zeros((3, 3))

for vi, vl in enumerate(vol_order):
    for ai, al in enumerate(atr_order):
        sub = df[(df['vol_label'] == vl) & (df['atr_label'] == al)]
        if len(sub) > 0:
            heatmap_wr[vi, ai] = sub['is_win'].mean() * 100
            heatmap_ret[vi, ai] = sub['return_pct'].mean()
            heatmap_cnt[vi, ai] = len(sub)

annot_wr = [[f"{heatmap_wr[i,j]:.0f}%\n(n={int(heatmap_cnt[i,j])})" for j in range(3)] for i in range(3)]
annot_ret = [[f"{heatmap_ret[i,j]:+.1f}%\n(n={int(heatmap_cnt[i,j])})" for j in range(3)] for i in range(3)]

im1 = axes[0].imshow(heatmap_wr, cmap='RdYlGn', vmin=40, vmax=100, aspect='auto')
for i in range(3):
    for j in range(3):
        text = axes[0].text(j, i, annot_wr[i][j], ha='center', va='center', fontsize=9, fontweight='bold',
                           color='white' if heatmap_wr[i,j] < 55 else 'black')
axes[0].set_xticks(range(3))
axes[0].set_xticklabels(atr_order)
axes[0].set_yticks(range(3))
axes[0].set_yticklabels(vol_order)
axes[0].set_xlabel('波动率环境')
axes[0].set_ylabel('量价')
axes[0].set_title('胜率 (%)')
plt.colorbar(im1, ax=axes[0], shrink=0.8)

im2 = axes[1].imshow(heatmap_ret, cmap='RdYlGn', aspect='auto')
for i in range(3):
    for j in range(3):
        text = axes[1].text(j, i, annot_ret[i][j], ha='center', va='center', fontsize=9, fontweight='bold',
                           color='white' if abs(heatmap_ret[i,j]) < 3 else 'black')
axes[1].set_xticks(range(3))
axes[1].set_xticklabels(atr_order)
axes[1].set_yticks(range(3))
axes[1].set_yticklabels(vol_order)
axes[1].set_xlabel('波动率环境')
axes[1].set_ylabel('量价')
axes[1].set_title('平均盈亏 (%)')
plt.colorbar(im2, ax=axes[1], shrink=0.8)

plt.tight_layout(rect=[0, 0, 1, 0.94])
out2 = os.path.join(RESULT_DIR, 'trend_aux_heatmap.png')
plt.savefig(out2, dpi=150, bbox_inches='tight')
plt.close()
print(f"图2: {out2}")

# ============ 图3: 三信号叠加过滤效果 ============
fig, axes = plt.subplots(1, 3, figsize=(16, 5))
fig.suptitle('T028 过滤效果模拟：三个信号叠加后剩余买入信号', fontsize=14, fontweight='bold', y=0.98)

total = len(df)
# MACD背离过滤: 去除 macd_divergence=True
after_macd = len(df[df['macd_divergence'] == False])
# 放量过滤: 去除 vol_label == '放量'
after_vol = len(df[df['vol_label'] != '放量'])
# 正常波过滤: 去除 atr_label == '正常'
after_atr = len(df[df['atr_label'] != '正常'])
# 三过滤叠加
all_good = df[(df['macd_divergence'] == False) & (df['vol_label'] != '放量') & (df['atr_label'] != '正常')]

items = ['全部信号', 'MACD背离过滤后', '放量过滤后', '正常波过滤后', '三重过滤后']
values = [total, after_macd, after_vol, after_atr, len(all_good)]
wr_vals = [
    df['is_win'].mean() * 100,
    df[df['macd_divergence']==False]['is_win'].mean() * 100,
    df[df['vol_label']!='放量']['is_win'].mean() * 100,
    df[df['atr_label']!='正常']['is_win'].mean() * 100,
    all_good['is_win'].mean() * 100,
]
ret_vals = [
    df['return_pct'].mean(),
    df[df['macd_divergence']==False]['return_pct'].mean(),
    df[df['vol_label']!='放量']['return_pct'].mean(),
    df[df['atr_label']!='正常']['return_pct'].mean(),
    all_good['return_pct'].mean(),
]

colors5 = ['#9E9E9E', '#607D8B', '#FF9800', '#2196F3', '#4CAF50']

ax = axes[0]
bars = ax.barh(items, values, color=colors5, edgecolor='white')
ax.set_title('信号数量变化', fontsize=13)
ax.set_xlabel('买入信号数')
for b, v in zip(bars, values):
    ax.text(b.get_width() + 5, b.get_y() + b.get_height()/2, str(v), va='center', fontweight='bold')

ax = axes[1]
bars = ax.barh(items, wr_vals, color=colors5, edgecolor='white')
ax.set_title('胜率变化', fontsize=13)
ax.axvline(x=df['is_win'].mean()*100, color='gray', linestyle='--', alpha=0.5, label='基线')
for b, v in zip(bars, wr_vals):
    ax.text(b.get_width() + 0.5, b.get_y() + b.get_height()/2, f'{v:.1f}%', va='center', fontsize=10, fontweight='bold')
ax.legend(fontsize=8)

ax = axes[2]
bars = ax.barh(items, ret_vals, color=colors5, edgecolor='white')
ax.set_title('平均盈亏变化', fontsize=13)
ax.axvline(x=df['return_pct'].mean(), color='gray', linestyle='--', alpha=0.5, label='基线')
for b, v in zip(bars, ret_vals):
    ax.text(b.get_width() + 0.08, b.get_y() + b.get_height()/2, f'{v:+.1f}%', va='center', fontsize=10, fontweight='bold')
ax.legend(fontsize=8)

plt.tight_layout(rect=[0, 0, 1, 0.94])
out3 = os.path.join(RESULT_DIR, 'trend_aux_filter_cascade.png')
plt.savefig(out3, dpi=150, bbox_inches='tight')
plt.close()
print(f"图3: {out3}")

print(f"\n全部图表已生成至 {RESULT_DIR}/")
