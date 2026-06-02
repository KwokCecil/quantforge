"""股债利差信号可视化。
生成 4 张图：利差走势+分位 / 信号分布 / 510300择时 / 策略净值对比
"""
import os
import sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
import numpy as np
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from quantforge.indicators.guzhai_licha import GuzhaiLichaCalculator
from quantforge.data_sources.sina_feed import SinaFinanceFeed
from quantforge.core.data_feed import DataRequest

_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'results', 'guzhai_licha_viz')
os.makedirs(_OUTPUT_DIR, exist_ok=True)

plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False


def load_data():
    """加载信号 + 价格"""
    calc = GuzhaiLichaCalculator()
    df = calc.get_signal_df("2018-01-01")
    df['date'] = pd.to_datetime(df['date'])

    feed = SinaFinanceFeed()
    req = DataRequest(codes=["510300"], data_type="daily_k",
                      start="2018-01-01", end=datetime.today().strftime("%Y-%m-%d"))
    resp = feed.get_data(req)
    prices = resp.bar_data["510300"].set_index('date')['close']
    prices.index = pd.to_datetime(prices.index)

    return df, prices


def compute_equity(df, prices):
    """计算择时策略净值"""
    capital = 40000.0
    shares = 0.0
    position = False
    equity = []
    price_map = {str(d)[:10]: p for d, p in prices.items()}
    dates = sorted(price_map.keys())

    last_signal = None
    for date_str in dates:
        price = price_map[date_str]
        # 找最近的信号
        sig_row = df[df['date'] <= date_str]
        if sig_row.empty:
            equity.append({'date': date_str, 'strategy': capital, 'bh': capital})
            continue

        s = sig_row.iloc[-1]
        if s['signal_charge'] and not position:
            shares = capital / price
            position = True
        elif s['signal_retreat'] and position:
            capital = shares * price
            shares = 0.0
            position = False

        value = shares * price if position else capital
        equity.append({'date': date_str, 'strategy': value, 'bh': None})

    df_eq = pd.DataFrame(equity)
    # Buy & hold
    start_price = prices.iloc[0]
    bh_shares = 40000 / start_price
    bh_values = [bh_shares * p for p in prices]
    df_eq['bh'] = bh_values
    df_eq['date'] = pd.to_datetime(df_eq['date'])
    return df_eq


def get_trade_points(df, prices):
    """获取买卖点"""
    price_map = {str(d)[:10]: p for d, p in prices.items()}
    buys = []
    sells = []
    position = False
    for _, s in df.iterrows():
        ds = s['date'].strftime('%Y-%m-%d')
        if ds not in price_map:
            continue
        price = price_map[ds]
        if s['signal_charge'] and not position:
            buys.append({'date': s['date'], 'price': price})
            position = True
        elif s['signal_retreat'] and position:
            sells.append({'date': s['date'], 'price': price})
            position = False
    return buys, sells


def main():
    df, prices = load_data()
    df_eq = compute_equity(df, prices)
    buys, sells = get_trade_points(df, prices)

    fig, axes = plt.subplots(2, 2, figsize=(20, 12))
    ax1, ax2, ax3, ax4 = axes.flatten()

    # ========== 图1: 股债利差走势 + 分位 ==========
    ax1_twin = ax1.twinx()
    ax1.fill_between(df['date'], 0, 1,
                     where=df['signal_charge'], alpha=0.12, color='green', label='_nolegend_')
    ax1.fill_between(df['date'], 0, 1,
                     where=df['signal_retreat'], alpha=0.12, color='red', label='_nolegend_')

    l1, = ax1.plot(df['date'], df['double_ttm_licha'], color='#2196F3', linewidth=1.2,
                   label='滚动双倍利差(%)')
    l2, = ax1.plot(df['date'], df['single_static_licha'], color='#FF9800', linewidth=0.8,
                   alpha=0.7, label='静态单倍利差(%)')
    ax1.axhline(y=0, color='gray', linewidth=0.5, linestyle='--')

    l3, = ax1_twin.plot(df['date'], df['double_ttm_pct'] * 100, color='#4CAF50', linewidth=1.0,
                         alpha=0.5, label='双倍分位(ratio×100)')
    l3b, = ax1_twin.plot(df['date'], df['single_static_pct'] * 100, color='#FF5722', linewidth=0.7,
                          alpha=0.3, label='单倍分位(ratio×100)')

    # 阈值线
    ax1_twin.axhline(y=92, color='red', linewidth=1, linestyle='--', alpha=0.6)
    ax1_twin.axhline(y=15, color='green', linewidth=1, linestyle='--', alpha=0.6)
    ax1_twin.axhline(y=40, color='orange', linewidth=0.8, linestyle='--', alpha=0.4)

    ax1.set_title('股债利差与分位值 (HS300 PE vs 10Y国债)', fontsize=13, fontweight='bold')
    ax1.set_ylabel('利差 (%)', fontsize=10)
    ax1_twin.set_ylabel('分位 (%)', fontsize=10)
    lines = [l1, l2, l3, l3b]
    labels = [l.get_label() for l in lines]
    ax1.legend(lines, labels, loc='upper left', fontsize=8, ncol=2)
    ax1.grid(True, alpha=0.3)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
    ax1.xaxis.set_major_locator(mdates.YearLocator())

    # 标记冲锋/撤退关键区域
    for _, s in df[df['signal_retreat']].iterrows():
        ax1.axvline(x=s['date'], color='red', alpha=0.08, linewidth=3)
    for _, s in df[df['signal_charge']].iterrows() if df['signal_charge'].sum() < 100 \
            else df[df['signal_charge']].iloc[::10].iterrows():
        pass  # 太多冲锋信号，不画线

    # ========== 图2: 信号日历热力 + PE/国债 ==========
    ax2_twin = ax2.twinx()
    ax2_twin.fill_between(df['date'], 0, 1,
                          where=df['signal_charge'], alpha=0.15, color='green', label='_nolegend_')
    ax2_twin.fill_between(df['date'], 0, 1,
                          where=df['signal_retreat'], alpha=0.15, color='red', label='_nolegend_')

    l4, = ax2.plot(df['date'], df['pe_ttm'], color='#673AB7', linewidth=1.3, label='HS300 TTM PE')
    l5, = ax2.plot(df['date'], df['pe_static'], color='#9C27B0', linewidth=0.8,
                   alpha=0.6, label='HS300 静态PE')
    ax2.set_ylabel('PE', fontsize=10)

    l6, = ax2_twin.plot(df['date'], df['bond_10y'], color='#E91E63', linewidth=1.2, label='10Y国债(%)')
    ax2_twin.set_ylabel('国债收益率 (%)', fontsize=10, color='#E91E63')
    ax2_twin.tick_params(axis='y', labelcolor='#E91E63')

    ax2.set_title('沪深300 PE 与 10年期国债收益率', fontsize=13, fontweight='bold')
    lines2 = [l4, l5, l6]
    labels2 = [l.get_label() for l in lines2]
    ax2.legend(lines2, labels2, loc='upper left', fontsize=8)
    ax2.grid(True, alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
    ax2.xaxis.set_major_locator(mdates.YearLocator())

    # ========== 图3: 510300 择时效果 ==========
    ax3.plot(prices.index, prices.values, color='#607D8B', linewidth=1.0, alpha=0.7,
             label='510300 价格')

    # 买卖点
    if buys:
        bx = [b['date'] for b in buys]
        by = [b['price'] for b in buys]
        ax3.scatter(bx, by, marker='^', s=100, color='green', edgecolors='white',
                    linewidths=1, zorder=5, label=f'买入({len(buys)}次)')
    if sells:
        sx = [s['date'] for s in sells]
        sy = [s['price'] for s in sells]
        ax3.scatter(sx, sy, marker='v', s=100, color='red', edgecolors='white',
                    linewidths=1, zorder=5, label=f'卖出({len(sells)}次)')

    # 背景着色
    for _, s in df[df['signal_charge']].iterrows():
        ax3.axvline(x=s['date'], color='green', alpha=0.03, linewidth=4)
    for _, s in df[df['signal_retreat']].iterrows():
        ax3.axvline(x=s['date'], color='red', alpha=0.08, linewidth=4)

    # 标注涨跌区间
    for b, s_pt in zip(buys, sells) if len(buys) == len(sells) else []:
        ret = (s_pt['price'] - b['price']) / b['price'] * 100
        mid_date = b['date'] + (s_pt['date'] - b['date']) / 2
        mid_price = (b['price'] + s_pt['price']) / 2
        ax3.annotate(f'{ret:+.0f}%', xy=(mid_date, mid_price),
                     fontsize=10, fontweight='bold',
                     color='green' if ret > 0 else 'red',
                     ha='center', va='center',
                     bbox=dict(boxstyle='round,pad=0.3', facecolor='white', alpha=0.8))

    ax3.set_title('510300 股债利差择时买卖点', fontsize=13, fontweight='bold')
    ax3.set_ylabel('价格 (元)', fontsize=10)
    ax3.legend(loc='upper left', fontsize=8)
    ax3.grid(True, alpha=0.3)
    ax3.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
    ax3.xaxis.set_major_locator(mdates.YearLocator())

    # ========== 图4: 策略净值 vs 买入持有 ==========
    ax4.fill_between(df_eq['date'], df_eq['strategy'], df_eq['bh'],
                     where=df_eq['strategy'] >= df_eq['bh'], alpha=0.1, color='green')
    ax4.fill_between(df_eq['date'], df_eq['strategy'], df_eq['bh'],
                     where=df_eq['strategy'] < df_eq['bh'], alpha=0.1, color='red')

    ax4.plot(df_eq['date'], df_eq['strategy'], color='#2196F3', linewidth=2.0,
             label='股债利差择时')
    ax4.plot(df_eq['date'], df_eq['bh'], color='#9E9E9E', linewidth=1.5,
             linestyle='--', label='买入持有')

    # 超额收益标注
    strat_ret = (df_eq['strategy'].iloc[-1] - 40000) / 40000 * 100
    bh_ret = (df_eq['bh'].iloc[-1] - 40000) / 40000 * 100
    ax4.annotate(f'择时: +{strat_ret:.1f}%', xy=(df_eq['date'].iloc[-1], df_eq['strategy'].iloc[-1]),
                 fontsize=11, fontweight='bold', color='#2196F3',
                 xytext=(40, 20), textcoords='offset points')
    ax4.annotate(f'持有: +{bh_ret:.1f}%', xy=(df_eq['date'].iloc[-1], df_eq['bh'].iloc[-1]),
                 fontsize=11, fontweight='bold', color='#9E9E9E',
                 xytext=(40, -20), textcoords='offset points')

    ax4.set_title('策略净值对比 (初始资金 ¥40,000)', fontsize=13, fontweight='bold')
    ax4.set_ylabel('净值 (元)', fontsize=10)
    ax4.legend(loc='upper left', fontsize=9)
    ax4.grid(True, alpha=0.3)
    ax4.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
    ax4.xaxis.set_major_locator(mdates.YearLocator())

    # 保存
    plt.tight_layout()
    out_path = os.path.join(_OUTPUT_DIR, 'guzhai_licha_overview.png')
    plt.savefig(out_path, dpi=150, bbox_inches='tight', facecolor='white')
    plt.close()
    print(f'图表已保存: {out_path}')

    # 打印关键摘要
    print(f'\n=== 关键数据摘要 ===')
    print(f'区间: 2018-01-01 ~ 2026-05-21')
    print(f'择时交易: {len(buys)}买 {len(sells)}卖')
    for i, (b, s_pt) in enumerate(zip(buys, sells) if len(sells) > 0 else zip(buys, [])):
        ret = (s_pt['price'] - b['price']) / b['price'] * 100 if s_pt else float('nan')
        print(f'  交易{i+1}: {b["date"].strftime("%Y-%m-%d")}买@{b["price"]:.3f} → '
              f'{s_pt["date"].strftime("%Y-%m-%d") if s_pt else "持有中"}卖@{s_pt["price"]:.3f} '
              f'({"+" if ret>0 else ""}{ret:.1f}%)')
    if len(buys) > len(sells):
        b = buys[-1]
        curr_price = prices.iloc[-1]
        ret = (curr_price - b['price']) / b['price'] * 100
        print(f'  持仓中: {b["date"].strftime("%Y-%m-%d")}买@{b["price"]:.3f} → '
              f'当前@{curr_price:.3f} ({"+" if ret>0 else ""}{ret:.1f}%)')


if __name__ == '__main__':
    main()