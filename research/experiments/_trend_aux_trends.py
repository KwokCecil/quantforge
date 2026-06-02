# @layer: e2e
"""T028 走势图：按信号日期累积各维度收益，看时间序列表现"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import glob
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

RESULT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results", "T028_aux_signals")
csv_files = sorted(glob.glob(os.path.join(RESULT_DIR, "trend_aux_*.csv")), reverse=True)
df = pd.read_csv(csv_files[0])
df['date'] = pd.to_datetime(df['date'])
df = df.sort_values('date').reset_index(drop=True)
print(f"数据: {csv_files[0]} ({len(df)} 行)")

# ============ 累积收益曲线 ============
fig, axes = plt.subplots(2, 2, figsize=(18, 12))
fig.suptitle('T028 辅助信号分析：各维度累积收益走势', fontsize=16, fontweight='bold')

def plot_cumulative(ax, df, col, labels, colors, title, ylabel='累积收益 (%)'):
    for label, color in zip(labels, colors):
        if isinstance(label, (list, tuple)):
            mask = True
            for i, l in enumerate(label):
                mask = mask & (df[col] == l)
            label_str = ' × '.join(label)
        else:
            mask = df[col] == label
            label_str = str(label)
        sub = df[mask].copy()
        if len(sub) == 0:
            continue
        cum = sub['return_pct'].cumsum()
        ax.plot(sub['date'], cum, color=color, linewidth=1.2, alpha=0.85, label=f'{label_str} (n={len(sub)})')
    ax.axhline(y=0, color='gray', linestyle='--', alpha=0.3)
    ax.set_title(title, fontsize=13)
    ax.set_ylabel(ylabel)
    ax.legend(fontsize=8, loc='upper left')
    ax.grid(alpha=0.2)

# 1. MACD背离走势
colors_macd = ['#F44336', '#4CAF50']
plot_cumulative(axes[0, 0], df, 'macd_divergence',
                [True, False], colors_macd,
                'MACD背离 vs 无背离：累积收益', '累积收益 (%)')

# 2. 量价走势
colors_vol = ['#FF9800', '#2196F3', '#4CAF50']
plot_cumulative(axes[0, 1], df, 'vol_label',
                ['放量', '正常', '缩量'], colors_vol,
                '量价：累积收益', '累积收益 (%)')

# 3. 波动率走势
colors_atr = ['#FF5722', '#607D8B', '#2196F3']
plot_cumulative(axes[1, 0], df, 'atr_label',
                ['高波', '正常', '低波'], colors_atr,
                '波动率：累积收益', '累积收益 (%)')

# 4. 三过滤叠加走势
colors_all = ['#9E9E9E', '#FF5722', '#FF9800', '#2196F3', '#4CAF50']
ax = axes[1, 1]

# 基准线
baseline_mask = pd.Series(True, index=df.index)
baseline_cum = df['return_pct'].cumsum()
ax.plot(df['date'], baseline_cum, color='#9E9E9E', linewidth=1.5, alpha=0.7, label=f'全部 (n={len(df)})')

# 反转过滤后的
for label_text, mask_col, mask_val, color in [
    ('去除背离', 'macd_divergence', False, '#FF5722'),
    ('去除放量', 'vol_label', '放量', '#FF9800'),
    ('去除正常波', 'atr_label', '正常', '#2196F3'),
]:
    if mask_col == 'macd_divergence':
        sub = df[~df['macd_divergence']]
    else:
        sub = df[df[mask_col] != mask_val]
    cum = sub['return_pct'].cumsum()
    ax.plot(sub['date'], cum, color=color, linewidth=1.2, alpha=0.8, label=f'{label_text} (n={len(sub)})')

# 三重过滤
all_good = df[(df['macd_divergence'] == False) & (df['vol_label'] != '放量') & (df['atr_label'] != '正常')]
all_good_cum = all_good['return_pct'].cumsum()
ax.plot(all_good['date'], all_good_cum, color='#4CAF50', linewidth=1.8, alpha=0.9, label=f'三重过滤后 (n={len(all_good)})')

ax.axhline(y=0, color='gray', linestyle='--', alpha=0.3)
ax.set_title('过滤效果：累积收益走势', fontsize=13)
ax.legend(fontsize=8, loc='upper left')
ax.grid(alpha=0.2)

plt.tight_layout(rect=[0, 0, 1, 0.95])
out1 = os.path.join(RESULT_DIR, 'trend_aux_cumulative_trends.png')
plt.savefig(out1, dpi=150, bbox_inches='tight')
plt.close()
print(f"走势图: {out1}")

# ============ 滚动胜率走势（180日窗口）============
fig, axes = plt.subplots(1, 3, figsize=(18, 5))
fig.suptitle('T028 滚动胜率走势（180日窗口）', fontsize=14, fontweight='bold')

window = 180

def plot_rolling_wr(ax, df, col, groups, colors, title):
    all_dates = sorted(df['date'].unique())
    date_series = pd.date_range(all_dates[0], all_dates[-1], freq='D')

    for group, color in zip(groups, colors):
        if isinstance(group, (list, tuple)):
            sub = df.copy()
            for i, g in enumerate(group):
                sub = sub[sub[col] == g]
            label_str = ' × '.join(group)
        else:
            sub = df[df[col] == group]
            label_str = str(group)

        wr_series = []
        for d in all_dates:
            win_start = d - pd.Timedelta(days=window)
            win_data = sub[(sub['date'] >= win_start) & (sub['date'] <= d)]
            if len(win_data) >= 10:
                wr_series.append((d, win_data['is_win'].mean() * 100))
            else:
                wr_series.append((d, np.nan))

        dates, wr = zip(*wr_series)
        ax.plot(dates, wr, color=color, linewidth=1.2, alpha=0.85, label=label_str)

    ax.set_ylim(0, 100)
    ax.axhline(y=50, color='gray', linestyle='--', alpha=0.3)
    ax.set_title(title, fontsize=12)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.2)

plot_rolling_wr(axes[0], df, 'macd_divergence',
                [True, False], ['#F44336', '#4CAF50'],
                'MACD背离 vs 无背离')

plot_rolling_wr(axes[1], df, 'vol_label',
                ['放量', '正常', '缩量'], ['#FF9800', '#2196F3', '#4CAF50'],
                '量价')

plot_rolling_wr(axes[2], df, 'atr_label',
                ['高波', '正常', '低波'], ['#FF5722', '#607D8B', '#2196F3'],
                '波动率')

plt.tight_layout(rect=[0, 0, 1, 0.93])
out2 = os.path.join(RESULT_DIR, 'trend_aux_rolling_wr.png')
plt.savefig(out2, dpi=150, bbox_inches='tight')
plt.close()
print(f"滚动胜率: {out2}")
