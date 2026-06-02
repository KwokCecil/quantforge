"""股债利差 50/50 沪深300+创业板 策略回测。

策略：冲锋信号→买510300(50%)+159915(50%)，撤退→清仓。
中间若任一标的占比<33%(不到另一标的一半)→再平衡回50/50。
"""
import os
import sys
import json
import pandas as pd
import numpy as np
from datetime import datetime
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quantforge.indicators.guzhai_licha import GuzhaiLichaCalculator

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_RESULTS_DIR = os.path.join(os.path.dirname(_SCRIPT_DIR), 'results')

# 交易成本
COMMISSION_RATE = 0.00025   # ETF 佣金万2.5
MIN_COMMISSION = 0.0        # 免5
SLIPPAGE = 0.001            # 滑点 0.1%


def load_prices(codes: list[str], start: str = "2018-01-01") -> dict[str, pd.Series]:
    """加载 ETF 日线收盘价"""
    from quantforge.data_sources.sina_feed import SinaFinanceFeed
    from quantforge.core.data_feed import DataRequest

    feed = SinaFinanceFeed()
    req = DataRequest(codes=codes, data_type="daily_k",
                      start=start, end=datetime.today().strftime("%Y-%m-%d"))
    resp = feed.get_data(req)

    result = {}
    for code in codes:
        df = resp.bar_data.get(code, pd.DataFrame())
        if df.empty:
            logger.warning(f"{code}: 无数据")
            continue
        result[code] = df.set_index('date')['close']
        logger.info(f"{code}: {len(result[code])} 天, {result[code].index.min()} ~ {result[code].index.max()}")
    return result


def run_simulation(signals, prices: dict[str, pd.Series],
                   initial_capital: float = 40000.0) -> dict:
    """模拟 50/50 双ETF 择时 + 再平衡 策略。

    Args:
        signals: GuzhaiLichaCalculator.compute() 返回的信号列表
        prices: {code: Series(date→close)}

    Returns:
        dict 包含回测统计
    """
    CODES = list(prices.keys())
    if len(CODES) < 2:
        raise ValueError("至少需要2个标的")

    # 构建日期→价格查找表
    pc_map = {}
    for code in CODES:
        pc_map[code] = {str(d)[:10]: p for d, p in prices[code].items()}

    # 找到所有信号日都有的交易日期
    all_dates = set(pc_map[CODES[0]].keys())
    for code in CODES[1:]:
        all_dates &= set(pc_map[code].keys())
    all_dates = sorted(all_dates)

    capital = initial_capital
    # positions: {code: shares}
    positions = {code: 0.0 for code in CODES}
    in_market = False

    trades = []          # [{date, action('buy'/'sell'/'rebalance'), code, price, shares, cost, reason}]
    daily_values = []    # [{date, total_value, cash, position_value_for_each}]

    signal_idx = {s.date.strftime("%Y-%m-%d"): s for s in signals}
    total_commission = 0.0
    total_slippage = 0.0

    for date_str in all_dates:
        signal = signal_idx.get(date_str)

        # === 交易逻辑 ===
        if signal is not None:
            if signal.signal_retreat and in_market:
                # 撤退：清仓
                for code in CODES:
                    if positions[code] > 0:
                        price = _sell_price(pc_map[code][date_str])
                        value = positions[code] * price
                        comm = max(value * COMMISSION_RATE, MIN_COMMISSION)
                        capital += value - comm
                        trades.append({
                            'date': date_str, 'action': 'sell', 'code': code,
                            'price': price, 'shares': positions[code], 'value': value,
                            'commission': comm,
                            'reason': f'撤退信号 双倍分位={signal.double_ttm_pct:.1%}'
                        })
                        total_commission += comm
                        positions[code] = 0.0
                in_market = False

            elif signal.signal_charge and not in_market:
                # 冲锋：买入
                half_capital = capital / 2
                for code in CODES:
                    price = _buy_price(pc_map[code][date_str])
                    shares = int(half_capital / price / 100) * 100
                    cost = shares * price
                    comm = max(cost * COMMISSION_RATE, MIN_COMMISSION)
                    capital -= (cost + comm)
                    positions[code] = shares
                    trades.append({
                        'date': date_str, 'action': 'buy', 'code': code,
                        'price': price, 'shares': shares, 'cost': cost,
                        'commission': comm,
                        'reason': f'冲锋信号 双倍分位={signal.double_ttm_pct:.1%}'
                    })
                    total_commission += comm
                in_market = True

            elif in_market:
                # 中性 + 已持仓：再平衡检查
                reb_trades = _check_rebalance(date_str, positions, pc_map, capital, CODES)
                for rt in reb_trades:
                    capital = rt['new_cash']
                    trades.append(rt)
                    if rt.get('commission'):
                        total_commission += rt['commission']
        else:
            # 无信号日：再平衡检查
            if in_market:
                reb_trades = _check_rebalance(date_str, positions, pc_map, capital, CODES)
                for rt in reb_trades:
                    capital = rt['new_cash']
                    trades.append(rt)
                    if rt.get('commission'):
                        total_commission += rt['commission']

        # === 记录日净值（现金 + 持仓市值）===
        position_value = 0
        for code in CODES:
            p = pc_map[code].get(date_str)
            if p and positions[code] > 0:
                position_value += positions[code] * p
        total_value = capital + position_value
        daily_values.append({'date': date_str, 'value': total_value, 'in_market': in_market})

    # === 期末清算 ===
    final_value = capital
    for code in CODES:
        if positions[code] > 0:
            last_price = list(pc_map[code].values())[-1]
            final_value += positions[code] * last_price

    total_return = (final_value - initial_capital) / initial_capital

    # === 计算指标 ===
    df_daily = pd.DataFrame(daily_values)
    if len(df_daily) > 1:
        df_daily['return'] = df_daily['value'].pct_change()
        sharpe = np.sqrt(252) * df_daily['return'].mean() / df_daily['return'].std() \
            if df_daily['return'].std() > 0 else 0

        cummax = df_daily['value'].cummax()
        dd = (df_daily['value'] - cummax) / cummax
        max_dd = dd.min()

        in_market_returns = df_daily[df_daily['in_market']]['return'].dropna()
        in_market_sharpe = np.sqrt(252) * in_market_returns.mean() / in_market_returns.std() \
            if len(in_market_returns) > 1 and in_market_returns.std() > 0 else 0
    else:
        sharpe = 0.0
        max_dd = 0.0
        in_market_sharpe = 0.0

    # 按交易对计算胜率
    buy_trades = [t for t in trades if t['action'] == 'buy']
    sell_trades = [(t for t in trades if t['action'] == 'sell' or t['action'] == 'sell_last')]

    # 胜率：跟踪每次冲锋的买入总成本 → 撤退的卖出总收入
    trade_rounds = []
    current_round = None
    for t in trades:
        if t['action'] == 'buy':
            if current_round is not None:
                trade_rounds.append(current_round)
            current_round = {'date': t['date'], 'buy_cost': t['cost'], 'sell_value': 0}
        elif t['action'] in ('sell', 'sell_last'):
            if current_round:
                current_round['sell_value'] += t.get('value', 0)
    if current_round:
        trade_rounds.append(current_round)

    if trade_rounds:
        wins = sum(1 for r in trade_rounds if r['sell_value'] > r['buy_cost'] or not r['sell_value'])
        # 最后一轮可能未卖出，不算
        closed_rounds = [r for r in trade_rounds if r['sell_value'] > 0]
        if closed_rounds:
            closed_wins = sum(1 for r in closed_rounds if r['sell_value'] > r['buy_cost'])
            win_rate = closed_wins / len(closed_rounds)
        else:
            win_rate = 0
    else:
        win_rate = 0

    return {
        'total_return': total_return,
        'sharpe': sharpe,
        'in_market_sharpe': in_market_sharpe,
        'max_drawdown': max_dd,
        'trade_count': len(trades),
        'win_rate': win_rate,
        'trade_rounds': len(trade_rounds),
        'final_value': final_value,
        'total_commission': total_commission,
        'total_slippage': total_slippage,
        'trades': trades,
        'position_days': sum(1 for v in daily_values if v['in_market']),
        'total_days': len(daily_values),
    }


def _check_rebalance(date_str: str, positions: dict, pc_map: dict,
                     capital: float, codes: list[str]) -> list[dict]:
    """检查是否需要再平衡（任一标的 < 33% 总权重 → 调回 50/50）"""
    trades = []

    # 计算当前市值
    values = {}
    for code in codes:
        p = pc_map[code].get(date_str)
        if p is None:
            return trades  # 价格缺失，跳过
        values[code] = positions[code] * p

    total_market = sum(values.values()) + capital
    if total_market <= 0:
        return trades

    for code in codes:
        weight = values[code] / total_market
        if weight < 0.33 and values[code] > 0:
            # 该标的占比过低，需要再平衡
            # 目标：各占 50%
            target_value = total_market * 0.5

            for c in codes:
                current_val = values[c]
                diff = target_value - current_val

                if abs(diff) < 100:  # 差异小于100元忽略
                    continue

                price = pc_map[c][date_str]
                if diff > 0:
                    # 需要买入
                    buy_price = _buy_price(price)
                    avail = min(diff, capital)
                    shares = int(avail / buy_price / 100) * 100
                    if shares == 0:
                        continue
                    cost = shares * buy_price
                    comm = max(cost * COMMISSION_RATE, MIN_COMMISSION)
                    capital -= (cost + comm)
                    trades.append({
                        'date': date_str, 'action': 'rebalance_buy', 'code': c,
                        'price': buy_price, 'shares': shares, 'cost': cost,
                        'commission': comm,
                        'weight_before': f'{current_val / total_market:.1%}',
                        'weight_target': '50%',
                        'new_cash': capital,
                    })
                else:
                    # 需要卖出
                    sell_price = _sell_price(price)
                    sell_shares = int(abs(diff) / sell_price / 100) * 100
                    sell_shares = min(sell_shares, positions[c])
                    if sell_shares == 0:
                        continue
                    value = sell_shares * sell_price
                    comm = max(value * COMMISSION_RATE, MIN_COMMISSION)
                    capital += (value - comm)
                    trades.append({
                        'date': date_str, 'action': 'rebalance_sell', 'code': c,
                        'price': sell_price, 'shares': sell_shares, 'value': value,
                        'commission': comm,
                        'weight_before': f'{current_val / total_market:.1%}',
                        'weight_target': '50%',
                        'new_cash': capital,
                    })

            break  # 一轮只做一次再平衡

    return trades


def _buy_price(p: float) -> float:
    return p * (1 + SLIPPAGE)


def _sell_price(p: float) -> float:
    return p * (1 - SLIPPAGE)


def run_benchmarks(prices: dict[str, pd.Series], initial: float = 40000.0) -> dict:
    """分别买入持有沪深300和创业板"""
    results = {}
    for code, p in prices.items():
        if p.empty:
            continue
        start_p, end_p = p.iloc[0], p.iloc[-1]
        shares = initial / start_p
        final = shares * end_p
        ret = (final - initial) / initial
        rets = p.pct_change().dropna()
        sharpe = np.sqrt(252) * rets.mean() / rets.std() if rets.std() > 0 else 0
        dd = (p - p.cummax()) / p.cummax()
        results[code] = {
            'total_return': ret, 'sharpe': sharpe, 'max_drawdown': dd.min(),
            'final_value': final,
        }
    return results


def main():
    logger.info("=== 股债利差 50/50 沪深300+创业板 策略回测 ===")

    # 信号
    calc = GuzhaiLichaCalculator()
    signals = calc.compute("2018-01-01")
    charge_days = sum(1 for s in signals if s.signal_charge)
    retreat_days = sum(1 for s in signals if s.signal_retreat)
    logger.info(f"信号: 冲锋={charge_days}天 中性={len(signals)-charge_days-retreat_days}天 撤退={retreat_days}天")

    # 价格
    prices = load_prices(["510300", "159915"], "2018-01-01")

    # 策略回测
    result = run_simulation(signals, prices)

    # 基准
    benchmarks = run_benchmarks(prices)

    # === 输出 ===
    print("\n" + "=" * 65)
    print("  股债利差 50/50 沪深300+创业板 择时策略")
    print("  策略: 冲锋→各买50%, 撤退→清仓, 偏移>33%→再平衡")
    print("=" * 65)

    print(f"\n{'指标':<22} {'50/50择时':>15} {'510300持有':>15} {'159915持有':>15}")
    print("-" * 68)
    print(f"{'总收益率':<22} {result['total_return']:>14.2%} {benchmarks['510300']['total_return']:>14.2%} {benchmarks['159915']['total_return']:>14.2%}")
    print(f"{'最终资金':<22} {result['final_value']:>14.0f} {benchmarks['510300']['final_value']:>14.0f} {benchmarks['159915']['final_value']:>14.0f}")
    print(f"{'Sharpe':<22} {result['sharpe']:>14.2f} {benchmarks['510300']['sharpe']:>14.2f} {benchmarks['159915']['sharpe']:>14.2f}")
    print(f"{'最大回撤':<22} {result['max_drawdown']:>14.2%} {benchmarks['510300']['max_drawdown']:>14.2%} {benchmarks['159915']['max_drawdown']:>14.2%}")
    print(f"{'交易次数':<22} {result['trade_count']:>14} {'N/A':>15} {'N/A':>15}")
    print(f"{'胜率':<22} {result['win_rate']:>14.1%} {'N/A':>15} {'N/A':>15}")
    print(f"{'持仓天数':<22} {result['position_days']:>14} {'N/A':>15} {'N/A':>15}")
    print(f"{'交易轮次':<22} {result['trade_rounds']:>14} {'N/A':>15} {'N/A':>15}")
    print(f"{'佣金合计':<22} {result['total_commission']:>14.2f} {'N/A':>15} {'N/A':>15}")

    # 交易明细
    print(f"\n--- 交易明细 ({len(result['trades'])} 笔) ---")
    for t in result['trades']:
        action_map = {
            'buy': '买', 'sell': '卖', 'rebalance_buy': '再平衡买',
            'rebalance_sell': '再平衡卖', 'sell_last': '清仓卖'
        }
        act = action_map.get(t['action'], t['action'])
        print(f"  {t['date']}  {act:<6} {t['code']} "
              f"@{t['price']:.3f} x {t.get('shares', 0):>5}股  "
              f"|  {t['reason']}")

    # 再平衡信息
    reb_trades = [t for t in result['trades'] if 'rebalance' in t['action']]
    if reb_trades:
        print(f"\n--- 再平衡明细 ({len(reb_trades)} 笔) ---")
        for t in reb_trades:
            print(f"  {t['date']}  {t['action']:<14} {t['code']} "
                  f"前占比={t['weight_before']} → 目标=50% "
                  f"@{t['price']:.3f} x {t['shares']}股")

    if not result['trades']:
        print("  (无交易)")

    # 保存结果
    run_dir = os.path.join(_RESULTS_DIR, f"guzhai_licha_5050_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, 'result.json'), 'w') as f:
        json.dump({
            'strategy': {
                'total_return': result['total_return'],
                'sharpe': result['sharpe'],
                'max_drawdown': result['max_drawdown'],
                'trade_count': result['trade_count'],
                'win_rate': result['win_rate'],
                'trade_rounds': result['trade_rounds'],
                'total_commission': result['total_commission'],
                'position_days': result['position_days'],
                'total_days': result['total_days'],
            },
            'benchmarks': benchmarks,
        }, f, indent=2, default=str)
    logger.info(f"结果已保存: {run_dir}")


if __name__ == '__main__':
    main()