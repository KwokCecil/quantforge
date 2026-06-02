"""股债利差择时策略回测验证脚本。

用 510300 (沪深300ETF) 测试股债利差择时信号的预测能力。
策略：冲锋信号买入/持有，撤退信号卖出，中性信号维持现状。
"""
import os
import sys
import json
import pandas as pd
import numpy as np
from datetime import datetime
from loguru import logger

# 确保 quantforge 可导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from quantforge.indicators.guzhai_licha import GuzhaiLichaCalculator, GuzhaiLichaSignal
from quantforge.data_sources.sina_feed import SinaFinanceFeed

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_RESULTS_DIR = os.path.join(os.path.dirname(_SCRIPT_DIR), 'results')


def load_510300_prices(start: str = "2018-01-01") -> pd.Series:
    """加载510300日线数据"""
    feed = SinaFinanceFeed()
    from quantforge.core.data_feed import DataRequest, DataResponse

    req = DataRequest(codes=["510300"], data_type="daily_k",
                      start=start, end=datetime.today().strftime("%Y-%m-%d"))
    resp = feed.get_data(req)
    df = resp.bar_data.get("510300", pd.DataFrame())
    if df.empty:
        raise ValueError("无法获取510300数据")
    prices = df.set_index('date')['close']
    return prices


def run_simulation(signals: list[GuzhaiLichaSignal], prices: pd.Series) -> dict:
    """模拟股债利差择时策略。

    规则（与 guzhai_licha.py 一致）：
    - 冲锋信号 → 买入/持有（满仓）
    - 撤退信号 → 卖出（空仓）
    - 中性信号 → 维持现状
    - 初始资金 40000
    - 不考虑手续费
    """
    capital = 40000.0
    shares = 0.0
    position = False

    trades = []
    daily_values = []
    price_idx = {str(d)[:10]: p for d, p in prices.items()}

    for signal in signals:
        date_str = signal.date.strftime("%Y-%m-%d")
        if date_str not in price_idx:
            continue
        price = price_idx[date_str]

        if signal.signal_charge and not position:
            # 买入
            shares = capital / price
            position = True
            trades.append({
                'date': date_str, 'action': 'buy', 'price': price,
                'reason': f'双倍分位={signal.double_ttm_pct:.1%} 单倍分位={signal.single_static_pct:.1%}'
            })
        elif signal.signal_retreat and position:
            # 卖出
            capital = shares * price
            shares = 0.0
            position = False
            trades.append({
                'date': date_str, 'action': 'sell', 'price': price,
                'reason': f'双倍分位={signal.double_ttm_pct:.1%} 单倍分位={signal.single_static_pct:.1%}'
            })

        # 记录日净值
        value = shares * price if position else capital
        daily_values.append({'date': date_str, 'value': value, 'position': position})

    # 最终清算
    if position and daily_values:
        last_price = list(price_idx.values())[-1]
        capital = shares * last_price

    total_return = (capital - 40000) / 40000

    # 计算指标
    df_values = pd.DataFrame(daily_values)
    if len(df_values) > 0:
        df_values['daily_return'] = df_values['value'].pct_change()
        sharpe = np.sqrt(252) * df_values['daily_return'].mean() / df_values['daily_return'].std() \
            if df_values['daily_return'].std() > 0 else 0

        # 最大回撤
        cummax = df_values['value'].cummax()
        drawdown = (df_values['value'] - cummax) / cummax
        max_dd = drawdown.min()

        # 胜率（按交易）
        if len(trades) >= 2:
            trade_pairs = []
            buy_price = None
            for t in trades:
                if t['action'] == 'buy':
                    buy_price = t['price']
                elif t['action'] == 'sell' and buy_price is not None:
                    trade_pairs.append((buy_price, t['price']))
                    buy_price = None
            wins = sum(1 for b, s in trade_pairs if s > b)
            win_rate = wins / len(trade_pairs) if trade_pairs else 0
        else:
            win_rate = 0
    else:
        sharpe = 0
        max_dd = 0
        win_rate = 0

    return {
        'total_return': total_return,
        'sharpe': sharpe,
        'max_drawdown': max_dd,
        'trade_count': len(trades),
        'win_rate': win_rate,
        'final_capital': capital,
        'trades': trades[-20:] if len(trades) > 20 else trades,
        'position_days': sum(1 for v in daily_values if v['position']),
        'total_days': len(daily_values),
    }


def run_benchmark(prices: pd.Series) -> dict:
    """510300 买入持有基准"""
    initial = 40000.0
    start_price = prices.iloc[0]
    end_price = prices.iloc[-1]
    shares = initial / start_price
    final = shares * end_price
    total_return = (final - initial) / initial

    returns = prices.pct_change().dropna()
    sharpe = np.sqrt(252) * returns.mean() / returns.std() if returns.std() > 0 else 0
    cummax = prices.cummax()
    drawdown = (prices - cummax) / cummax
    max_dd = drawdown.min()

    return {
        'total_return': total_return,
        'sharpe': sharpe,
        'max_drawdown': max_dd,
        'final_capital': final,
    }


def main():
    logger.info("=== 股债利差择时回测验证 ===")

    # 加载信号
    calc = GuzhaiLichaCalculator()
    signals = calc.compute("2018-01-01")

    # 加载价格
    logger.info("加载 510300 价格数据...")
    prices = load_510300_prices("2018-01-01")
    logger.info(f"510300: {len(prices)} trading days, {prices.index.min()} ~ {prices.index.max()}")

    # 运行策略
    logger.info("运行股债利差择时策略...")
    result = run_simulation(signals, prices)

    # 基准
    benchmark = run_benchmark(prices)

    # 输出
    print("\n" + "=" * 60)
    print("  股债利差择时策略 vs 买入持有基准")
    print("  标的: 510300 (沪深300ETF)")
    print("=" * 60)
    print(f"\n{'指标':<20} {'股债利差择时':>15} {'买入持有':>15}")
    print("-" * 55)
    print(f"{'总收益率':<20} {result['total_return']:>14.2%} {benchmark['total_return']:>14.2%}")
    print(f"{'最终资金':<20} {result['final_capital']:>14.0f} {benchmark['final_capital']:>14.0f}")
    print(f"{'Sharpe':<20} {result['sharpe']:>14.2f} {benchmark['sharpe']:>14.2f}")
    print(f"{'最大回撤':<20} {result['max_drawdown']:>14.2%} {benchmark['max_drawdown']:>14.2%}")
    print(f"{'交易次数':<20} {result['trade_count']:>14} {'N/A':>15}")
    print(f"{'胜率':<20} {result['win_rate']:>14.1%} {'N/A':>15}")
    print(f"{'持仓天数':<20} {result['position_days']:>14} {result['total_days']:>15}")

    # 信号统计
    charge_count = sum(1 for s in signals if s.signal_charge)
    retreat_count = sum(1 for s in signals if s.signal_retreat)
    neutral_count = len(signals) - charge_count - retreat_count
    print(f"\n--- 信号分布 ---")
    print(f"冲锋: {charge_count}天  |  中性: {neutral_count}天  |  撤退: {retreat_count}天")

    # 最近交易
    if result['trades']:
        print(f"\n--- 最近交易 ---")
        for t in result['trades'][-10:]:
            print(f"  {t['date']}  {t['action']:>4} @ {t['price']:.3f}  |  {t['reason']}")

    # 保存结果
    run_dir = os.path.join(_RESULTS_DIR, f"guzhai_licha_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, 'result.json'), 'w') as f:
        json.dump({
            'strategy': {k: v for k, v in result.items() if k != 'trades'},
            'benchmark': benchmark,
            'signal_stats': {
                'charge': charge_count, 'neutral': neutral_count, 'retreat': retreat_count,
                'total': len(signals),
            }
        }, f, indent=2, default=str)
    logger.info(f"结果已保存: {run_dir}")


if __name__ == '__main__':
    main()